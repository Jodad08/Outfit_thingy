"""FastAPI application wiring together the wardrobe assistant."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import (
    analytics,
    autotag,
    color_utils,
    config,
    models,
    pinterest,
    recommender,
    schemas,
)
from .database import get_db, init_db

FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Wardrobe Fashion Assistant", version="1.0.0", lifespan=lifespan)


# --- helpers -----------------------------------------------------------------
def _item_to_out(item: models.Item) -> schemas.ItemOut:
    out = schemas.ItemOut.model_validate(item)
    if item.purchase_price and item.wear_count:
        out.cost_per_wear = round(item.purchase_price / item.wear_count, 2)
    return out


def _item_ref(item: models.Item) -> schemas.OutfitItemRef:
    return schemas.OutfitItemRef.model_validate(item)


def _get_item(db: Session, item_id: int) -> models.Item:
    item = db.get(models.Item, item_id)
    if item is None:
        raise HTTPException(404, f"Item {item_id} not found")
    return item


# --- image analysis ----------------------------------------------------------
@app.post("/api/analyze", response_model=schemas.AnalyzeResult)
async def analyze_image(image: UploadFile = File(...)) -> schemas.AnalyzeResult:
    """Store an uploaded garment photo and suggest its dominant color."""
    raw = await image.read()
    if not raw:
        raise HTTPException(400, "Empty upload")
    try:
        image_path, thumb_path, primary = color_utils.save_image(raw)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Could not process image: {exc}") from exc
    name = color_utils.nearest_color_name(primary) if primary else None

    # Optional AI auto-tagging (zero-shot Fashion-CLIP). Suggestions only.
    suggestions = None
    if autotag.tagger.enabled:
        import io

        from PIL import Image

        try:
            pil = Image.open(io.BytesIO(raw))
            result = autotag.tagger.suggest(pil)
            if result is not None:
                suggestions = result.as_dict()
        except Exception:  # noqa: BLE001
            suggestions = None  # never let tagging break intake

    return schemas.AnalyzeResult(
        image=image_path,
        thumbnail=thumb_path,
        primary_color_hex=primary,
        color_name=name,
        suggestions=suggestions,
    )


# --- items -------------------------------------------------------------------
@app.get("/api/items", response_model=list[schemas.ItemOut])
def list_items(
    db: Session = Depends(get_db),
    category: str | None = None,
    season: str | None = None,
    occasion: str | None = None,
    status: str | None = None,
    laundry_status: str | None = None,
    q: str | None = Query(None, description="Search item names"),
) -> list[schemas.ItemOut]:
    stmt = select(models.Item).order_by(models.Item.created_at.desc())
    if category:
        stmt = stmt.where(models.Item.category == category)
    if status:
        stmt = stmt.where(models.Item.status == status)
    if laundry_status:
        stmt = stmt.where(models.Item.laundry_status == laundry_status)
    items = list(db.scalars(stmt).all())
    # JSON-list membership + text filters applied in Python (SQLite-friendly).
    if season:
        items = [i for i in items if season in (i.seasons or [])]
    if occasion:
        items = [i for i in items if occasion in (i.occasions or [])]
    if q:
        ql = q.lower()
        items = [i for i in items if ql in i.name.lower()]
    return [_item_to_out(i) for i in items]


@app.post("/api/items", response_model=schemas.ItemOut, status_code=201)
def create_item(payload: schemas.ItemCreate, db: Session = Depends(get_db)) -> schemas.ItemOut:
    if payload.category not in models.CATEGORIES:
        raise HTTPException(400, f"category must be one of {models.CATEGORIES}")
    data = payload.model_dump()
    # Auto-add the detected color name to the color tags if none provided.
    if not data.get("colors") and data.get("primary_color_hex"):
        data["colors"] = [color_utils.nearest_color_name(data["primary_color_hex"])]
    item = models.Item(**data)
    db.add(item)
    db.commit()
    db.refresh(item)
    return _item_to_out(item)


@app.get("/api/items/{item_id}", response_model=schemas.ItemOut)
def get_item(item_id: int, db: Session = Depends(get_db)) -> schemas.ItemOut:
    return _item_to_out(_get_item(db, item_id))


@app.put("/api/items/{item_id}", response_model=schemas.ItemOut)
def update_item(
    item_id: int, payload: schemas.ItemUpdate, db: Session = Depends(get_db)
) -> schemas.ItemOut:
    item = _get_item(db, item_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(item, key, value)
    db.commit()
    db.refresh(item)
    return _item_to_out(item)


@app.delete("/api/items/{item_id}")
def delete_item(item_id: int, db: Session = Depends(get_db)) -> Response:
    item = _get_item(db, item_id)
    for rel in (item.image, item.thumbnail):
        if rel:
            fp = config.DATA_DIR / rel
            fp.unlink(missing_ok=True)
    db.delete(item)
    db.commit()
    return Response(status_code=204)


@app.post("/api/items/{item_id}/wear", response_model=schemas.ItemOut)
def wear_item(item_id: int, db: Session = Depends(get_db)) -> schemas.ItemOut:
    item = _get_item(db, item_id)
    _log_wear(db, item)
    db.commit()
    db.refresh(item)
    return _item_to_out(item)


def _log_wear(db: Session, item: models.Item, outfit_id: int | None = None) -> None:
    from .models import _utcnow

    item.wear_count += 1
    item.last_worn = _utcnow()
    db.add(models.WearLog(item_id=item.id, outfit_id=outfit_id))


# --- recommendations ---------------------------------------------------------
@app.post("/api/recommend", response_model=list[schemas.ScoredOutfit])
def recommend(req: schemas.RecommendRequest, db: Session = Depends(get_db)):
    items = list(db.scalars(select(models.Item)).all())
    prefs = db.get(models.Preferences, 1)

    target_palette = None
    if req.inspiration_id is not None:
        inspo = db.get(models.Inspiration, req.inspiration_id)
        if inspo is None:
            raise HTTPException(404, f"Inspiration {req.inspiration_id} not found")
        target_palette = list(inspo.palette or [])
        if inspo.primary_color_hex and inspo.primary_color_hex not in target_palette:
            target_palette.append(inspo.primary_color_hex)

    candidates = recommender.generate(
        items,
        prefs,
        occasion=req.occasion,
        season=req.season,
        temperature_c=req.temperature_c,
        raining=req.raining,
        require_outerwear=req.require_outerwear,
        include_item_ids=req.include_item_ids,
        target_palette=target_palette,
        limit=req.limit,
    )
    return [
        schemas.ScoredOutfit(
            item_ids=[i.id for i in c.items],
            items=[_item_ref(i) for i in c.items],
            score=c.score,
            breakdown=c.breakdown,
            rationale=c.rationale,
        )
        for c in candidates
    ]


# --- saved outfits -----------------------------------------------------------
def _outfit_to_out(db: Session, outfit: models.Outfit) -> schemas.OutfitOut:
    out = schemas.OutfitOut.model_validate(outfit)
    refs = []
    for iid in outfit.item_ids:
        it = db.get(models.Item, iid)
        if it:
            refs.append(_item_ref(it))
    out.items = refs
    return out


@app.get("/api/outfits", response_model=list[schemas.OutfitOut])
def list_outfits(db: Session = Depends(get_db)) -> list[schemas.OutfitOut]:
    outfits = db.scalars(
        select(models.Outfit).order_by(models.Outfit.created_at.desc())
    ).all()
    return [_outfit_to_out(db, o) for o in outfits]


@app.post("/api/outfits", response_model=schemas.OutfitOut, status_code=201)
def save_outfit(payload: schemas.OutfitSave, db: Session = Depends(get_db)) -> schemas.OutfitOut:
    missing = [iid for iid in payload.item_ids if db.get(models.Item, iid) is None]
    if missing:
        raise HTTPException(400, f"Unknown item ids: {missing}")
    outfit = models.Outfit(
        name=payload.name,
        item_ids=payload.item_ids,
        occasion=payload.occasion,
        score=payload.score,
        score_breakdown=payload.score_breakdown,
        favorite=payload.favorite,
    )
    db.add(outfit)
    db.commit()
    db.refresh(outfit)
    return _outfit_to_out(db, outfit)


@app.post("/api/outfits/{outfit_id}/favorite", response_model=schemas.OutfitOut)
def toggle_favorite(outfit_id: int, db: Session = Depends(get_db)) -> schemas.OutfitOut:
    outfit = db.get(models.Outfit, outfit_id)
    if outfit is None:
        raise HTTPException(404, "Outfit not found")
    outfit.favorite = not outfit.favorite
    db.commit()
    db.refresh(outfit)
    return _outfit_to_out(db, outfit)


@app.post("/api/outfits/{outfit_id}/wear", response_model=schemas.OutfitOut)
def wear_outfit(outfit_id: int, db: Session = Depends(get_db)) -> schemas.OutfitOut:
    """Mark an outfit as worn today: bumps wear counts for every item in it."""
    outfit = db.get(models.Outfit, outfit_id)
    if outfit is None:
        raise HTTPException(404, "Outfit not found")
    from .models import _utcnow

    for iid in outfit.item_ids:
        item = db.get(models.Item, iid)
        if item:
            _log_wear(db, item, outfit_id=outfit.id)
    outfit.wear_count += 1
    outfit.last_worn = _utcnow()
    db.commit()
    db.refresh(outfit)
    return _outfit_to_out(db, outfit)


@app.delete("/api/outfits/{outfit_id}")
def delete_outfit(outfit_id: int, db: Session = Depends(get_db)) -> Response:
    outfit = db.get(models.Outfit, outfit_id)
    if outfit is None:
        raise HTTPException(404, "Outfit not found")
    db.delete(outfit)
    db.commit()
    return Response(status_code=204)


# --- preferences & analytics -------------------------------------------------
@app.get("/api/preferences", response_model=schemas.PreferencesOut)
def get_preferences(db: Session = Depends(get_db)) -> schemas.PreferencesOut:
    prefs = db.get(models.Preferences, 1)
    return schemas.PreferencesOut.model_validate(prefs)


@app.put("/api/preferences", response_model=schemas.PreferencesOut)
def update_preferences(
    payload: schemas.PreferencesUpdate, db: Session = Depends(get_db)
) -> schemas.PreferencesOut:
    prefs = db.get(models.Preferences, 1)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(prefs, key, value)
    db.commit()
    db.refresh(prefs)
    return schemas.PreferencesOut.model_validate(prefs)


@app.get("/api/analytics")
def get_analytics(db: Session = Depends(get_db)) -> dict:
    prefs = db.get(models.Preferences, 1)
    return analytics.summary(db, capsule_targets=prefs.capsule_targets if prefs else {})


@app.get("/api/meta")
def get_meta() -> dict:
    """Controlled vocabularies for the frontend to build dropdowns."""
    return {
        "categories": models.CATEGORIES,
        "seasons": models.SEASONS,
        "occasions": models.OCCASIONS,
        "laundry_statuses": models.LAUNDRY_STATUSES,
        "item_statuses": models.ITEM_STATUSES,
        "autotag_enabled": autotag.tagger.enabled,
        "pinterest_configured": config.pinterest_configured(),
    }


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


# --- inspiration (Pinterest + manual) ----------------------------------------
@app.get("/api/inspiration", response_model=list[schemas.InspirationOut])
def list_inspiration(db: Session = Depends(get_db)) -> list[schemas.InspirationOut]:
    rows = db.scalars(
        select(models.Inspiration).order_by(models.Inspiration.created_at.desc())
    ).all()
    return [schemas.InspirationOut.model_validate(r) for r in rows]


@app.post("/api/inspiration/upload", response_model=schemas.InspirationOut, status_code=201)
async def upload_inspiration(
    image: UploadFile = File(...),
    title: str | None = None,
    db: Session = Depends(get_db),
) -> schemas.InspirationOut:
    """Manually add an inspiration image (works without Pinterest configured)."""
    raw = await image.read()
    if not raw:
        raise HTTPException(400, "Empty upload")
    try:
        image_rel, thumb_rel, primary, palette = color_utils.save_inspiration_image(raw)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Could not process image: {exc}") from exc
    row = models.Inspiration(
        source="manual",
        title=title,
        image=image_rel,
        thumbnail=thumb_rel,
        primary_color_hex=primary,
        palette=palette,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return schemas.InspirationOut.model_validate(row)


@app.delete("/api/inspiration/{inspo_id}")
def delete_inspiration(inspo_id: int, db: Session = Depends(get_db)) -> Response:
    row = db.get(models.Inspiration, inspo_id)
    if row is None:
        raise HTTPException(404, "Inspiration not found")
    for rel in (row.image, row.thumbnail):
        if rel:
            (config.DATA_DIR / rel).unlink(missing_ok=True)
    db.delete(row)
    db.commit()
    return Response(status_code=204)


@app.get("/api/pinterest/status", response_model=schemas.PinterestStatus)
def pinterest_status(db: Session = Depends(get_db)) -> schemas.PinterestStatus:
    count = db.query(models.Inspiration).count()
    return schemas.PinterestStatus(
        configured=config.pinterest_configured(),
        connected=pinterest.is_connected(db),
        inspiration_count=count,
        redirect_uri=config.PINTEREST_REDIRECT_URI,
    )


@app.get("/api/pinterest/connect")
def pinterest_connect(db: Session = Depends(get_db)) -> RedirectResponse:
    try:
        url = pinterest.build_authorize_url(db)
    except pinterest.PinterestError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url)


@app.get("/api/pinterest/callback", response_class=HTMLResponse)
def pinterest_callback(
    db: Session = Depends(get_db),
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    if error:
        return HTMLResponse(_callback_page(f"Pinterest authorization failed: {error}", ok=False))
    if not code:
        return HTMLResponse(_callback_page("Missing authorization code.", ok=False))
    try:
        pinterest.exchange_code(db, code, state)
    except pinterest.PinterestError as exc:
        return HTMLResponse(_callback_page(str(exc), ok=False))
    return HTMLResponse(_callback_page("Pinterest connected! You can close this tab.", ok=True))


def _callback_page(message: str, ok: bool) -> str:
    color = "#4ade80" if ok else "#ff6b6b"
    return (
        f"<!doctype html><html><head><meta charset='utf-8'><title>Pinterest</title>"
        f"<style>body{{font-family:system-ui;background:#0f1115;color:#e8eaf0;"
        f"display:flex;min-height:100vh;align-items:center;justify-content:center;text-align:center}}"
        f".c{{max-width:360px;padding:24px}}.b{{color:{color};font-size:1.1rem;font-weight:600}}</style></head>"
        f"<body><div class='c'><p class='b'>{message}</p>"
        f"<p><a style='color:#7c9cff' href='/'>Return to Wardrobe</a></p></div>"
        f"<script>try{{setTimeout(()=>{{if({str(ok).lower()})location.href='/'}},1500)}}catch(e){{}}</script>"
        f"</body></html>"
    )


@app.post("/api/pinterest/disconnect")
def pinterest_disconnect(db: Session = Depends(get_db)) -> dict:
    pinterest.disconnect(db)
    return {"connected": False}


@app.get("/api/pinterest/boards", response_model=list[schemas.PinterestBoard])
def pinterest_boards(db: Session = Depends(get_db)) -> list[schemas.PinterestBoard]:
    try:
        boards = pinterest.list_boards(db)
    except pinterest.PinterestError as exc:
        raise HTTPException(400, str(exc)) from exc
    return [schemas.PinterestBoard(**b) for b in boards]


@app.post("/api/pinterest/boards/{board_id}/import")
def pinterest_import(board_id: str, db: Session = Depends(get_db)) -> dict:
    try:
        return pinterest.import_board(db, board_id)
    except pinterest.PinterestError as exc:
        raise HTTPException(400, str(exc)) from exc


# --- static media + frontend -------------------------------------------------
# Uploaded images/thumbnails. Ensure the dir exists before mounting (import-time).
config.ensure_dirs()
app.mount("/media", StaticFiles(directory=str(config.DATA_DIR)), name="media")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(str(FRONTEND_DIR / "index.html"))


# Frontend assets (css/js). Mounted last so /api and /media win.
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
