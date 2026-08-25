# CLAUDE.md — project guide for Claude Code

Self-hosted wardrobe fashion assistant. Photograph garments → structured SQLite
database → generate ranked outfits. Runs fully offline (Docker or an
Ubuntu/Proxmox VM). No cloud services or API keys.

## Architecture (layered, from the reference design)

1. **Structured data** — `backend/app/models.py`: `Item`, `Outfit`, `WearLog`,
   `Preferences` (SQLite via SQLAlchemy).
2. **Deterministic filtering (hard constraints)** —
   `recommender.passes_hard_constraints`: availability, laundry status, season,
   occasion, rain (rain-unsafe shoes), extreme-temperature guard.
3. **Weighted scoring (soft preferences)** — `recommender._score_outfit`:
   `occasion .25 + weather .20 + color .15 + comfort .15 + preference .15 + rotation .10`
   (weights overridable via `Preferences.weights`). Color harmony is HSV-based
   in `color_utils.py`.
4. **Explanation** — each outfit returns a per-term breakdown + rationale.
   Optional AI intake tagging lives in `autotag.py` (see below).

## Layout

```
backend/app/
  main.py         FastAPI routes (API + serves frontend + /media images)
  models.py       ORM models + controlled vocabularies
  schemas.py      Pydantic models
  recommender.py  filtering + scoring
  color_utils.py  image save (WebP, EXIF-stripped) + dominant color + harmony
  analytics.py    cost-per-wear, rotation, capsule gaps
  autotag.py      OPTIONAL Fashion-CLIP zero-shot tagging (lazy, CPU)
  config.py       env-driven config
  database.py     engine/session/init
frontend/         vanilla HTML/CSS/JS SPA, camera capture, no build step
Dockerfile, docker-compose.yml, deploy/wardrobe.service, scripts/dev.sh
```

## Run

- **Docker:** `docker compose up --build -d` → http://localhost:8000
- **Dev (no Docker):** `./scripts/dev.sh` (creates venv, runs uvicorn --reload)
- **API docs:** `/docs` (Swagger). Health: `/api/health`.

## Optional AI auto-tagging

Off by default; base app has **no ML deps**. To enable:
`pip install -r backend/requirements-vision.txt` and set
`WARDROBE_ENABLE_AUTOTAG=1` (Docker: build with `ENABLE_VISION=true`).
`torch` must come from the CPU wheel index (already pinned in
`requirements-vision.txt` as `torch==2.5.1+cpu` via
`--extra-index-url https://download.pytorch.org/whl/cpu`). Weights (~600MB)
download once to `WARDROBE_MODEL_DIR`. Tagging only *suggests* form values; it
never writes to the DB. `autotag.py` splits pure scoring logic (unit-testable,
no ML libs) from the lazy model backend.

## Conventions

- Data (SQLite DB + garment images + model cache) lives under
  `WARDROBE_DATA_DIR` (default `/data`) and is **git-ignored** — never commit
  personal photos or the database.
- `models.CATEGORIES / SEASONS / OCCASIONS / LAUNDRY_STATUSES / ITEM_STATUSES`
  are the single source of truth for vocabularies; the frontend fetches them
  from `/api/meta`.
- Images are stored as WebP with EXIF stripped; served read-only under `/media`.
- Keep the base app dependency-light; anything heavy (ML) stays optional and
  lazily imported so `import backend.app.main` works without it.
