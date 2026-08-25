"""Image handling and color reasoning.

Two jobs:
  1. Store an uploaded photo compactly (downscale, strip metadata, WebP) and
     make a thumbnail.
  2. Estimate a garment's dominant color and reason about color harmony for the
     recommender's color-compatibility term.

Only Pillow is used, so this runs fully offline with no ML dependencies.
"""
from __future__ import annotations

import colorsys
import io
import secrets
from pathlib import Path

from PIL import Image

from . import config

_BASIC_COLORS: dict[str, tuple[int, int, int]] = {
    "black": (20, 20, 20),
    "white": (240, 240, 240),
    "gray": (128, 128, 128),
    "navy": (30, 40, 80),
    "blue": (50, 90, 200),
    "teal": (30, 140, 140),
    "green": (60, 150, 70),
    "olive": (110, 120, 60),
    "yellow": (220, 200, 60),
    "orange": (230, 140, 50),
    "red": (200, 50, 50),
    "pink": (230, 130, 170),
    "purple": (130, 70, 170),
    "brown": (120, 80, 50),
    "beige": (200, 180, 150),
    "cream": (235, 225, 200),
}

NEUTRALS = {"black", "white", "gray", "navy", "beige", "cream", "brown"}


def _open_normalized(raw: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(raw))
    # Apply EXIF orientation, then drop all metadata by re-encoding later.
    from PIL import ImageOps

    img = ImageOps.exif_transpose(img)
    return img.convert("RGBA")


def save_image(raw: bytes) -> tuple[str, str, str | None]:
    """Persist an uploaded image.

    Returns (image_rel_path, thumb_rel_path, primary_color_hex).
    """
    img = _open_normalized(raw)

    token = secrets.token_hex(8)
    full_name = f"{token}.webp"
    thumb_name = f"{token}_thumb.webp"

    full = _fit(img, config.MAX_IMAGE_SIDE)
    thumb = _fit(img, config.THUMB_SIDE)

    # Flatten onto white for the stored WebP (keeps files small + predictable).
    _flatten(full).save(config.IMAGE_DIR / full_name, "WEBP", quality=85, method=6)
    _flatten(thumb).save(config.THUMB_DIR / thumb_name, "WEBP", quality=80, method=6)

    primary = dominant_color_hex(img)
    return f"images/{full_name}", f"thumbnails/{thumb_name}", primary


def _fit(img: Image.Image, max_side: int) -> Image.Image:
    w, h = img.size
    scale = min(1.0, max_side / max(w, h))
    if scale >= 1.0:
        return img.copy()
    return img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)


def _flatten(img: Image.Image) -> Image.Image:
    bg = Image.new("RGB", img.size, (255, 255, 255))
    bg.paste(img, mask=img.split()[-1])
    return bg


def dominant_color_hex(img: Image.Image) -> str | None:
    """Estimate the garment's dominant color.

    Heuristic: sample the center region (garments are usually centered),
    ignore transparent and near-white background pixels, and pick the most
    common quantized color.
    """
    small = _fit(img, 200)
    w, h = small.size
    # Center crop to reduce background influence.
    cx0, cy0 = int(w * 0.15), int(h * 0.15)
    cx1, cy1 = int(w * 0.85), int(h * 0.85)
    region = small.crop((cx0, cy0, cx1, cy1)).convert("RGBA")

    counts: dict[tuple[int, int, int], int] = {}
    for r, g, b, a in region.getdata():
        if a < 40:
            continue
        # Skip near-white / near-black background-ish pixels lightly.
        if r > 245 and g > 245 and b > 245:
            continue
        key = (r // 24 * 24, g // 24 * 24, b // 24 * 24)
        counts[key] = counts.get(key, 0) + 1

    if not counts:
        return None
    r, g, b = max(counts, key=counts.get)
    return f"#{r:02x}{g:02x}{b:02x}"


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def nearest_color_name(hex_value: str) -> str:
    r, g, b = hex_to_rgb(hex_value)
    best, best_d = "gray", 1e9
    for name, (cr, cg, cb) in _BASIC_COLORS.items():
        d = (r - cr) ** 2 + (g - cg) ** 2 + (b - cb) ** 2
        if d < best_d:
            best, best_d = name, d
    return best


def _is_neutral(hex_value: str) -> bool:
    r, g, b = hex_to_rgb(hex_value)
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    # Low saturation OR very dark/light reads as a neutral that goes with anything.
    return s < 0.18 or v < 0.15 or (v > 0.9 and s < 0.25)


def _hue(hex_value: str) -> float:
    r, g, b = hex_to_rgb(hex_value)
    h, _, _ = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    return h * 360.0


def pair_harmony(a: str | None, b: str | None) -> float:
    """Score two colors' compatibility in [0, 1]."""
    if not a or not b:
        return 0.6  # unknown -> neutral-ish default
    if _is_neutral(a) or _is_neutral(b):
        return 0.9  # neutrals pair with everything
    ha, hb = _hue(a), _hue(b)
    diff = abs(ha - hb)
    diff = min(diff, 360 - diff)  # circular distance
    if diff < 25:
        return 0.85  # analogous / monochrome
    if 150 <= diff <= 210:
        return 0.8  # complementary
    if 100 <= diff <= 140:
        return 0.7  # triadic-ish
    if diff < 60:
        return 0.6
    return 0.4  # clashing


def harmony_score(hex_colors: list[str | None]) -> float:
    """Average pairwise harmony across an outfit's colors."""
    present = [c for c in hex_colors if c]
    if len(present) < 2:
        return 0.75
    total, n = 0.0, 0
    for i in range(len(present)):
        for j in range(i + 1, len(present)):
            total += pair_harmony(present[i], present[j])
            n += 1
    return total / n if n else 0.75
