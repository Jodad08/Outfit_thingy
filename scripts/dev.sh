#!/usr/bin/env bash
# Local development run (no Docker). Serves on http://localhost:8000
set -euo pipefail
cd "$(dirname "$0")/.."

python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q -r backend/requirements.txt

export WARDROBE_DATA_DIR="${WARDROBE_DATA_DIR:-$(pwd)/data}"
mkdir -p "$WARDROBE_DATA_DIR"

echo "Data dir: $WARDROBE_DATA_DIR"
echo "Open http://localhost:8000"
exec uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload
