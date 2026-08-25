"""Runtime configuration, driven by environment variables.

Everything is local-first so the app can run offline in Docker or a
Proxmox VM with no external services.
"""
from __future__ import annotations

import os
from pathlib import Path

# Root directory for all persistent data (mounted as a volume in Docker).
DATA_DIR = Path(os.environ.get("WARDROBE_DATA_DIR", "/data")).resolve()
IMAGE_DIR = DATA_DIR / "images"
THUMB_DIR = DATA_DIR / "thumbnails"
DB_PATH = DATA_DIR / "wardrobe.db"

DATABASE_URL = os.environ.get("WARDROBE_DATABASE_URL", f"sqlite:///{DB_PATH}")

# Max stored image dimension (px). Images are downscaled + converted to WebP
# to keep the wardrobe compact and privacy-friendly (metadata is stripped).
MAX_IMAGE_SIDE = int(os.environ.get("WARDROBE_MAX_IMAGE_SIDE", "1280"))
THUMB_SIDE = int(os.environ.get("WARDROBE_THUMB_SIDE", "400"))

# Cap on how many outfit combinations the recommender will score per request.
MAX_COMBINATIONS = int(os.environ.get("WARDROBE_MAX_COMBOS", "4000"))

# --- Optional AI auto-tagging (Fashion-CLIP, CPU) ----------------------------
# When enabled, uploaded photos are also run through a self-hosted zero-shot
# classifier to *suggest* category / subcategory / pattern / formality / season.
# All suggestions remain editable; the model never writes to the DB directly.
# Requires the extra deps in backend/requirements-vision.txt.
_TRUTHY = {"1", "true", "yes", "on"}
ENABLE_AUTOTAG = os.environ.get("WARDROBE_ENABLE_AUTOTAG", "0").lower() in _TRUTHY
AUTOTAG_MODEL = os.environ.get("WARDROBE_AUTOTAG_MODEL", "patrickjohncyh/fashion-clip")
# Model weights are cached here so they persist across container restarts.
MODEL_DIR = Path(os.environ.get("WARDROBE_MODEL_DIR", str(DATA_DIR / "models"))).resolve()

# --- Optional Pinterest inspiration integration ------------------------------
# Create a developer app at https://developers.pinterest.com/ to get these.
# Only needed for the official-API import; manual inspiration upload always works.
PINTEREST_CLIENT_ID = os.environ.get("PINTEREST_CLIENT_ID", "")
PINTEREST_CLIENT_SECRET = os.environ.get("PINTEREST_CLIENT_SECRET", "")
# Must exactly match a redirect URI registered on the Pinterest app.
PINTEREST_REDIRECT_URI = os.environ.get(
    "PINTEREST_REDIRECT_URI", "http://localhost:8000/api/pinterest/callback"
)
PINTEREST_API_BASE = os.environ.get("PINTEREST_API_BASE", "https://api.pinterest.com/v5")
PINTEREST_OAUTH_HOST = os.environ.get("PINTEREST_OAUTH_HOST", "https://www.pinterest.com")
INSPO_DIR = DATA_DIR / "inspiration"


def pinterest_configured() -> bool:
    return bool(PINTEREST_CLIENT_ID and PINTEREST_CLIENT_SECRET)


def ensure_dirs() -> None:
    """Create the data directories on startup if they do not yet exist."""
    for path in (DATA_DIR, IMAGE_DIR, THUMB_DIR, INSPO_DIR):
        path.mkdir(parents=True, exist_ok=True)
