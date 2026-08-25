# 👕 Wardrobe Fashion Assistant

A self-hosted personal fashion assistant. Photograph each garment in your
wardrobe, and the app turns your closet into a searchable, structured database
and generates complete outfits from it — filtered by weather, occasion, and
laundry status, then ranked by color harmony, comfort, your style preferences,
and wardrobe rotation.

It runs entirely on your own hardware (Docker or a Proxmox/Ubuntu VM). No cloud
services, no accounts, no external API keys. Your photos and data stay local.

---

## What it does

- **Photograph garments** — snap a photo (phone camera or file), and the app
  downscales it, strips metadata, converts to WebP, and auto-detects the
  dominant color.
- **Structured wardrobe** — every item is a record: category, colors, material,
  seasons, occasions, formality, comfort, warmth, rain-safety, price, laundry
  status, and wear count.
- **Outfit generation** using a layered engine (see below): hard rules filter
  out anything unavailable/inappropriate, then a weighted score ranks the rest.
- **Explainable suggestions** — every outfit shows its score breakdown and a
  plain-language rationale. Nothing is a black box.
- **Wear tracking** — mark items or whole outfits as worn; the app keeps wear
  counts and last-worn dates for rotation and analytics.
- **Insights** — cost-per-wear, "worn in the last 90 days", never-worn items,
  most-worn items, category breakdown, and capsule-wardrobe gap detection.
- **Laundry status** — items in the wash are automatically excluded from
  recommendations until you mark them clean.

## How the recommendation engine works

The design follows a layered approach so recommendations stay reliable:

**Layer 1 — Structured data.** Each garment is stored as a full record in SQLite.

**Layer 2 — Deterministic filtering (hard constraints).** Before anything is
ranked, items are removed if they are: not owned, in the laundry/repair,
out of season, wrong occasion, rain-unsafe shoes while it's raining, or clearly
wrong-weight for the temperature.

**Layer 3 — Recommendation scoring (soft preferences).** Surviving outfits are
scored with a weighted formula:

```
Outfit Score = Occasion match   × 0.25
             + Weather match     × 0.20
             + Color harmony     × 0.15
             + Comfort           × 0.15
             + Personal preference × 0.15
             + Rotation benefit  × 0.10
```

The weights are configurable (via `PUT /api/preferences`). Color harmony uses
HSV hue relationships (neutrals pair with anything; analogous and complementary
combos score highest). Rotation benefit favors under-used and not-recently-worn
items.

**Layer 4 — Explanation.** Each outfit is returned with its per-term breakdown
and a human-readable rationale. (This is done deterministically; you can later
swap in an LLM here without touching layers 1–3.)

---

## Quick start (Docker)

```bash
docker compose up --build -d
```

Open **http://localhost:8000**. Data (SQLite database + images) is persisted in
the `wardrobe-data` Docker volume, so it survives container rebuilds.

To stop: `docker compose down` (add `-v` to also delete the data volume).

### Plain Docker (no compose)

```bash
docker build -t wardrobe-assistant .
docker run -d --name wardrobe -p 8000:8000 -v wardrobe-data:/data wardrobe-assistant
```

## Quick start (Proxmox / Ubuntu VM, no Docker)

On an Ubuntu VM:

```bash
sudo apt update && sudo apt install -y python3 python3-venv git
git clone <your-repo-url> wardrobe && cd wardrobe
./scripts/dev.sh        # dev server with auto-reload on :8000
```

For a persistent production service, use the provided systemd unit — see the
install steps in [`deploy/wardrobe.service`](deploy/wardrobe.service). It runs
uvicorn under a dedicated `wardrobe` user with data in `/var/lib/wardrobe`.

> **Tip:** to reach it from other devices on your LAN, open port 8000 on the VM
> firewall, or put it behind a reverse proxy (nginx/Caddy) with HTTPS.

---

## Optional: AI auto-tagging (self-hosted, CPU)

You can have the app **suggest** a garment's category, subcategory, pattern,
formality, and seasons straight from the photo — so adding an item is mostly
confirming pre-filled fields instead of typing them.

- Uses **Fashion-CLIP**, a zero-shot vision model, running **locally on CPU**
  (no GPU, no cloud, no API keys). Your photos never leave the machine.
- It only ever **suggests** — every field stays editable, and the model never
  writes to the database directly.
- It's fully optional: the base app has zero ML dependencies. Enabling it adds
  `torch` (CPU) + `transformers`, and the model weights (~600 MB) download once
  on first use and are cached on the data volume.

**Enable with Docker:**

```bash
ENABLE_VISION=true WARDROBE_ENABLE_AUTOTAG=1 docker compose up --build -d
```

(`ENABLE_VISION` bakes the deps into the image at build time;
`WARDROBE_ENABLE_AUTOTAG=1` turns tagging on at runtime.)

**Enable without Docker:**

```bash
pip install -r backend/requirements.txt -r backend/requirements-vision.txt
export WARDROBE_ENABLE_AUTOTAG=1
uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
```

If the model can't load for any reason, the app logs a warning and silently
falls back to manual entry — intake never breaks.

## Optional: Pinterest inspiration

Import boards from your Pinterest account as an **inspiration gallery**, then
**match outfits from your own closet** to the colors of any inspiration pin.

- Uses the **official Pinterest API (v5)** with OAuth — your account, your
  boards. Pins and tokens are stored **locally**; nothing leaves your machine
  except the API calls to fetch them.
- Each imported pin gets a color palette; the recommender can rank real outfits
  from your wardrobe by how well they echo a pin's palette.
- You can also **add inspiration photos manually** (works with no API setup).

**Setup:**

1. Create an app at <https://developers.pinterest.com/> and note the
   **App ID** and **App secret**.
2. In the app's settings, add a **redirect URI** that exactly matches what the
   app uses — by default `http://localhost:8000/api/pinterest/callback`
   (change via `PINTEREST_REDIRECT_URI` if you serve it elsewhere).
3. Provide the credentials as environment variables and start the app:

   ```bash
   PINTEREST_CLIENT_ID=xxxx \
   PINTEREST_CLIENT_SECRET=yyyy \
   docker compose up -d
   ```

4. Open the app → **Inspo** tab → **Connect Pinterest**, approve access, then
   **Load my boards** and **Import** the ones you want. Tap **✨ Match closet**
   on any pin to see outfits from your wardrobe that fit its palette.

> Pinterest gates production API access; your own account works in the app's
> **trial** mode, which is all a personal, self-hosted setup needs.

## Remote access (optional)

This is a single-user, self-hosted app, so keep it on your VM rather than moving
it to a serverless platform. To reach it away from home **without exposing a
port or giving up local hosting**, put a tunnel in front of it:

- **Cloudflare Tunnel** (`cloudflared`) — free TLS, no open ports, optional
  Cloudflare Access login. The app still runs on your VM; Cloudflare is just a
  secure front door.
- **Tailscale** — private access from your own devices, zero public exposure.

## Configuration

All settings are environment variables (all optional):

| Variable | Default | Purpose |
|---|---|---|
| `WARDROBE_DATA_DIR` | `/data` | Where the SQLite DB and images live |
| `WARDROBE_DATABASE_URL` | `sqlite:///<data>/wardrobe.db` | Override DB (e.g. Postgres) |
| `WARDROBE_MAX_IMAGE_SIDE` | `1280` | Max stored image dimension (px) |
| `WARDROBE_THUMB_SIDE` | `400` | Thumbnail dimension (px) |
| `WARDROBE_MAX_COMBOS` | `4000` | Cap on outfit combinations scored per request |
| `WARDROBE_ENABLE_AUTOTAG` | `0` | Turn on AI auto-tagging (needs vision deps installed) |
| `WARDROBE_AUTOTAG_MODEL` | `patrickjohncyh/fashion-clip` | Zero-shot model to use |
| `WARDROBE_MODEL_DIR` | `<data>/models` | Where model weights are cached |
| `PINTEREST_CLIENT_ID` | _(empty)_ | Pinterest developer app ID (enables import) |
| `PINTEREST_CLIENT_SECRET` | _(empty)_ | Pinterest developer app secret |
| `PINTEREST_REDIRECT_URI` | `http://localhost:8000/api/pinterest/callback` | Must match the app's registered redirect URI |

## Tech stack

- **Backend:** FastAPI + SQLAlchemy + SQLite. Image processing with Pillow only
  (no ML dependencies — fully offline).
- **Frontend:** vanilla HTML/CSS/JS, mobile-first, uses the browser camera via a
  standard file input (`capture="environment"`). No build step.
- **Storage:** SQLite database + WebP images on a local volume.

## API

Interactive API docs are served at **`/docs`** (Swagger UI). Key endpoints:

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/analyze` | Upload a photo → stored image + detected color |
| `GET/POST` | `/api/items` | List / create garments |
| `PUT/DELETE` | `/api/items/{id}` | Edit / delete a garment |
| `POST` | `/api/items/{id}/wear` | Log a wear |
| `POST` | `/api/recommend` | Generate ranked outfits |
| `GET/POST` | `/api/outfits` | List / save outfits |
| `POST` | `/api/outfits/{id}/wear` | Wear an outfit (bumps every item) |
| `GET/PUT` | `/api/preferences` | Style prefs, capsule targets, scoring weights |
| `GET` | `/api/analytics` | Wardrobe insights |
| `GET` | `/api/inspiration` | List inspiration images |
| `POST` | `/api/inspiration/upload` | Manually add an inspiration photo |
| `POST` | `/api/recommend` (`inspiration_id`) | Match closet outfits to a pin's palette |
| `GET` | `/api/pinterest/status` | Config/connection status |
| `GET` | `/api/pinterest/connect` | Start OAuth (redirects to Pinterest) |
| `GET` | `/api/pinterest/boards` | List your boards |
| `POST` | `/api/pinterest/boards/{id}/import` | Import a board's pins |

## Data & privacy

- Everything is stored locally under `WARDROBE_DATA_DIR`. Garment photos and the
  database are **git-ignored** and never leave your machine.
- Image EXIF metadata is stripped on upload.
- Back up your wardrobe by copying that one directory (or the Docker volume).

## Project layout

```
backend/app/
  main.py          FastAPI app + routes
  models.py        ORM models (Item, Outfit, WearLog, Preferences)
  schemas.py       Pydantic request/response models
  recommender.py   Layered filtering + weighted outfit scoring
  color_utils.py   Image storage, dominant-color, color harmony
  analytics.py     Cost-per-wear, rotation, capsule gaps
  database.py      Engine/session/init
  config.py        Env-driven configuration
frontend/          Mobile-friendly single-page UI (no build step)
Dockerfile, docker-compose.yml
scripts/dev.sh     Local dev runner
deploy/wardrobe.service   systemd unit for Ubuntu/Proxmox
```

## Roadmap ideas

Layers 1–3 are complete and useful offline today. Natural next additions:
weather auto-fetch from a location, calendar-aware occasion detection, packing-
list/travel mode, purchase-evaluation scoring for new items, and an optional
vision model to auto-fill category/material/pattern from the photo.
