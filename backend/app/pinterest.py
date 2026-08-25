"""Pinterest inspiration integration (official v5 API).

Optional. Enabled only when PINTEREST_CLIENT_ID/SECRET are configured. Pulls
boards and pins from *your own* Pinterest account via the OAuth authorization-
code flow, downloads pin images locally, and extracts a color palette from each
so the recommender can match wardrobe outfits to an inspiration pin.

All tokens and images stay on this machine (single-user, self-hosted).

OAuth flow:
  1. GET /api/pinterest/connect  -> redirects you to Pinterest to authorize.
  2. Pinterest redirects back to /api/pinterest/callback?code=...&state=...
  3. We exchange the code for access + refresh tokens and store them.
Docs: https://developers.pinterest.com/docs/api/v5/
"""
from __future__ import annotations

import base64
import logging
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session

from . import color_utils, config, models

log = logging.getLogger("wardrobe.pinterest")

SCOPES = "boards:read,pins:read"
# Preferred pin image sizes, largest first.
_IMAGE_SIZE_PREFERENCE = ["1200x", "600x", "400x300", "originals", "150x150"]
_MAX_PINS_PER_IMPORT = 250


class PinterestError(RuntimeError):
    """Raised for configuration/auth/API problems; surfaced as HTTP 4xx."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# --- OAuth -------------------------------------------------------------------
def build_authorize_url(db: Session) -> str:
    if not config.pinterest_configured():
        raise PinterestError("Pinterest is not configured (set PINTEREST_CLIENT_ID/SECRET).")
    auth = _get_auth(db)
    state = secrets.token_urlsafe(24)
    auth.pending_state = state
    db.commit()
    params = {
        "client_id": config.PINTEREST_CLIENT_ID,
        "redirect_uri": config.PINTEREST_REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
    }
    return f"{config.PINTEREST_OAUTH_HOST}/oauth/?{urlencode(params)}"


def _basic_auth_header() -> str:
    raw = f"{config.PINTEREST_CLIENT_ID}:{config.PINTEREST_CLIENT_SECRET}".encode()
    return "Basic " + base64.b64encode(raw).decode()


def exchange_code(db: Session, code: str, state: str | None) -> None:
    auth = _get_auth(db)
    if not auth.pending_state or state != auth.pending_state:
        raise PinterestError("OAuth state mismatch — please restart the connection.")
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": config.PINTEREST_REDIRECT_URI,
    }
    token = _token_request(data)
    _store_token(db, auth, token)
    auth.pending_state = None
    db.commit()


def _refresh(db: Session, auth: models.PinterestAuth) -> None:
    if not auth.refresh_token:
        raise PinterestError("Not connected to Pinterest.")
    token = _token_request(
        {"grant_type": "refresh_token", "refresh_token": auth.refresh_token}
    )
    _store_token(db, auth, token)
    db.commit()


def _token_request(data: dict) -> dict:
    url = f"{config.PINTEREST_API_BASE}/oauth/token"
    headers = {
        "Authorization": _basic_auth_header(),
        "Content-Type": "application/x-www-form-urlencoded",
    }
    try:
        resp = httpx.post(url, data=data, headers=headers, timeout=30)
    except httpx.HTTPError as exc:
        raise PinterestError(f"Could not reach Pinterest: {exc}") from exc
    if resp.status_code != 200:
        raise PinterestError(f"Pinterest token error {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def _store_token(db: Session, auth: models.PinterestAuth, token: dict) -> None:
    auth.access_token = token.get("access_token")
    # Pinterest may or may not rotate the refresh token; keep the old one if absent.
    if token.get("refresh_token"):
        auth.refresh_token = token["refresh_token"]
    expires_in = int(token.get("expires_in", 0) or 0)
    auth.expires_at = _now() + timedelta(seconds=expires_in) if expires_in else None
    auth.scopes = token.get("scope", SCOPES)


def _valid_access_token(db: Session) -> str:
    auth = _get_auth(db)
    if not auth.access_token:
        raise PinterestError("Not connected to Pinterest. Connect an account first.")
    exp = _aware(auth.expires_at)
    if exp and exp <= _now() + timedelta(seconds=60):
        log.info("Refreshing Pinterest access token…")
        _refresh(db, auth)
    return auth.access_token


def _get_auth(db: Session) -> models.PinterestAuth:
    auth = db.get(models.PinterestAuth, 1)
    if auth is None:
        auth = models.PinterestAuth(id=1)
        db.add(auth)
        db.commit()
    return auth


def is_connected(db: Session) -> bool:
    auth = db.get(models.PinterestAuth, 1)
    return bool(auth and auth.access_token)


def disconnect(db: Session) -> None:
    auth = _get_auth(db)
    auth.access_token = None
    auth.refresh_token = None
    auth.expires_at = None
    auth.pending_state = None
    db.commit()


# --- API calls ---------------------------------------------------------------
def _api_get(db: Session, path: str, params: dict | None = None) -> dict:
    token = _valid_access_token(db)
    url = f"{config.PINTEREST_API_BASE}{path}"
    try:
        resp = httpx.get(
            url,
            params=params or {},
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
    except httpx.HTTPError as exc:
        raise PinterestError(f"Could not reach Pinterest: {exc}") from exc
    if resp.status_code == 401:
        raise PinterestError("Pinterest authorization expired — please reconnect.")
    if resp.status_code != 200:
        raise PinterestError(f"Pinterest API error {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def list_boards(db: Session) -> list[dict]:
    boards: list[dict] = []
    bookmark: str | None = None
    while True:
        params = {"page_size": 100}
        if bookmark:
            params["bookmark"] = bookmark
        data = _api_get(db, "/boards", params)
        for b in data.get("items", []):
            boards.append(
                {
                    "id": b.get("id"),
                    "name": b.get("name"),
                    "pin_count": b.get("pin_count"),
                    "privacy": b.get("privacy"),
                }
            )
        bookmark = data.get("bookmark")
        if not bookmark:
            break
    return boards


def _best_image_url(pin: dict) -> str | None:
    media = pin.get("media") or {}
    images = media.get("images") or {}
    for size in _IMAGE_SIZE_PREFERENCE:
        if size in images and images[size].get("url"):
            return images[size]["url"]
    # Fall back to any available size.
    for value in images.values():
        if value.get("url"):
            return value["url"]
    return None


def import_board(db: Session, board_id: str) -> dict:
    """Fetch a board's pins, download images, extract palettes, store them."""
    board_name = None
    try:
        board_name = _api_get(db, f"/boards/{board_id}").get("name")
    except PinterestError:
        pass  # board name is best-effort

    imported, skipped = 0, 0
    bookmark: str | None = None
    seen = 0
    while seen < _MAX_PINS_PER_IMPORT:
        params = {"page_size": 100}
        if bookmark:
            params["bookmark"] = bookmark
        data = _api_get(db, f"/boards/{board_id}/pins", params)
        items = data.get("items", [])
        if not items:
            break
        for pin in items:
            seen += 1
            pin_id = pin.get("id")
            # Skip pins already imported.
            existing = (
                db.query(models.Inspiration)
                .filter(models.Inspiration.external_id == pin_id)
                .first()
            )
            if existing:
                skipped += 1
                continue
            url = _best_image_url(pin)
            if not url:
                skipped += 1
                continue
            try:
                img_resp = httpx.get(url, timeout=30, follow_redirects=True)
                img_resp.raise_for_status()
                image_rel, thumb_rel, primary, palette = color_utils.save_inspiration_image(
                    img_resp.content
                )
            except (httpx.HTTPError, OSError) as exc:
                log.warning("Skipping pin %s (image fetch/decode failed): %s", pin_id, exc)
                skipped += 1
                continue
            db.add(
                models.Inspiration(
                    source="pinterest",
                    external_id=pin_id,
                    board_id=board_id,
                    board_name=board_name,
                    title=pin.get("title") or None,
                    description=pin.get("description") or None,
                    link=pin.get("link") or None,
                    image=image_rel,
                    thumbnail=thumb_rel,
                    primary_color_hex=primary,
                    palette=palette,
                )
            )
            imported += 1
        db.commit()
        bookmark = data.get("bookmark")
        if not bookmark:
            break

    return {"board_id": board_id, "board_name": board_name, "imported": imported, "skipped": skipped}
