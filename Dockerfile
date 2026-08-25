# Wardrobe Fashion Assistant — single-image deployment.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    WARDROBE_DATA_DIR=/data

WORKDIR /app

# System deps for Pillow (WebP support) kept minimal.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libwebp7 \
    && rm -rf /var/lib/apt/lists/*

# Set ENABLE_VISION=true at build time to bake in the optional Fashion-CLIP
# auto-tagging dependencies (torch CPU + transformers). Default off keeps the
# image small.
ARG ENABLE_VISION=false

COPY backend/requirements.txt ./requirements.txt
COPY backend/requirements-vision.txt ./requirements-vision.txt
RUN pip install --no-cache-dir -r requirements.txt \
    && if [ "$ENABLE_VISION" = "true" ]; then \
         pip install --no-cache-dir -r requirements-vision.txt; \
       fi

COPY backend ./backend
COPY frontend ./frontend

# Persistent data lives here (SQLite db + images). Mount a volume over it.
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health').status==200 else 1)"

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
