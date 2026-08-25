"""Optional AI auto-tagging via a self-hosted Fashion-CLIP model.

This is the reference design's "Layer 4" applied to intake: a zero-shot vision
model *suggests* structured attributes (category, subcategory, pattern,
formality, seasons) from a garment photo. Every suggestion is editable in the
UI and the model never writes to the database directly — it only pre-fills the
add-item form.

Design notes:
  * Fully optional. The heavy deps (torch + transformers) live in
    requirements-vision.txt and are imported lazily, so the base app runs with
    no ML dependencies at all.
  * CPU-only, no GPU required. Weights are cached on the data volume.
  * Zero-shot: it classifies the image against the label lists the app already
    defines, so there is no training step and no dataset to manage.

The scoring logic (softmax, formality expectation, season thresholding) is kept
as pure functions so it can be unit-tested without downloading a model.
"""
from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass, field

from . import config

log = logging.getLogger("wardrobe.autotag")

# --- Label vocabularies (zero-shot candidates) -------------------------------
CATEGORY_PROMPTS = {
    "top": "a top or shirt",
    "bottom": "a pair of pants, trousers, shorts or a skirt",
    "dress": "a dress",
    "outerwear": "a jacket, coat or outer layer",
    "shoes": "a pair of shoes",
    "bag": "a bag or purse",
    "accessory": "a fashion accessory",
}

SUBCATEGORIES = {
    "top": ["t-shirt", "button-down shirt", "blouse", "sweater", "hoodie", "polo shirt", "tank top"],
    "bottom": ["jeans", "chinos", "dress trousers", "shorts", "skirt", "leggings"],
    "dress": ["casual day dress", "cocktail dress", "maxi dress", "sundress"],
    "outerwear": ["denim jacket", "blazer", "wool coat", "puffer jacket", "cardigan", "leather jacket"],
    "shoes": ["sneakers", "boots", "dress shoes", "sandals", "heels", "loafers"],
    "bag": ["tote bag", "backpack", "handbag", "crossbody bag"],
    "accessory": ["scarf", "hat", "belt", "sunglasses", "necklace"],
}

PATTERNS = ["a solid color", "striped", "plaid", "checked", "floral", "a graphic print", "polka dot"]
_PATTERN_LABELS = ["solid", "striped", "plaid", "checked", "floral", "graphic", "polka dot"]

# Formality levels mapped onto the app's 1-10 scale.
FORMALITY_LEVELS = [
    ("very casual loungewear", 2),
    ("casual everyday wear", 4),
    ("smart casual outfit", 6),
    ("business attire", 8),
    ("formal eveningwear", 10),
]

SEASON_PROMPTS = {
    "spring": "lightweight spring clothing",
    "summer": "light breathable summer clothing",
    "fall": "autumn clothing in warm tones",
    "winter": "heavy warm winter clothing",
}


@dataclass
class Suggestions:
    category: str | None = None
    category_confidence: float | None = None
    subcategory: str | None = None
    pattern: str | None = None
    formality_score: int | None = None
    seasons: list[str] = field(default_factory=list)
    notes: str | None = None

    def as_dict(self) -> dict:
        return {
            "category": self.category,
            "category_confidence": self.category_confidence,
            "subcategory": self.subcategory,
            "pattern": self.pattern,
            "formality_score": self.formality_score,
            "seasons": self.seasons,
            "notes": self.notes,
        }


# --- Pure scoring helpers (unit-testable, no ML libs) -------------------------
def softmax(scores: list[float]) -> list[float]:
    if not scores:
        return []
    m = max(scores)
    exps = [math.exp(s - m) for s in scores]
    total = sum(exps) or 1.0
    return [e / total for e in exps]


def best_label(probs: list[float], labels: list[str]) -> tuple[str, float]:
    idx = max(range(len(probs)), key=lambda i: probs[i])
    return labels[idx], round(probs[idx], 3)


def expected_formality(probs: list[float], levels: list[tuple[str, int]]) -> int:
    """Probability-weighted formality on the 1-10 scale."""
    val = sum(p * lvl for p, (_, lvl) in zip(probs, levels))
    return max(1, min(10, round(val)))


def pick_seasons(probs: list[float], labels: list[str], threshold: float = 0.22) -> list[str]:
    """Return every season clearly above chance, else the single best one."""
    chosen = [labels[i] for i, p in enumerate(probs) if p >= threshold]
    if not chosen:
        chosen = [best_label(probs, labels)[0]]
    return chosen


def build_suggestions(sim_groups: dict[str, list[float]]) -> Suggestions:
    """Turn raw per-group similarity scores into structured suggestions.

    `sim_groups` maps a group name to a list of raw (unnormalized) similarity
    scores aligned with that group's label order. Kept separate from the model
    so it can be tested with synthetic scores.
    """
    cat_labels = list(CATEGORY_PROMPTS.keys())
    cat_probs = softmax(sim_groups["category"])
    category, conf = best_label(cat_probs, cat_labels)

    subs = SUBCATEGORIES.get(category, [])
    subcategory = None
    if subs and f"sub:{category}" in sim_groups:
        subcategory = best_label(softmax(sim_groups[f"sub:{category}"]), subs)[0]

    pattern = best_label(softmax(sim_groups["pattern"]), _PATTERN_LABELS)[0] if "pattern" in sim_groups else None
    formality = (
        expected_formality(softmax(sim_groups["formality"]), FORMALITY_LEVELS)
        if "formality" in sim_groups
        else None
    )
    seasons = (
        pick_seasons(softmax(sim_groups["season"]), list(SEASON_PROMPTS.keys()))
        if "season" in sim_groups
        else []
    )

    note_bits = [b for b in (subcategory, pattern) if b]
    notes = "AI-detected: " + ", ".join(note_bits) if note_bits else None
    return Suggestions(
        category=category,
        category_confidence=conf,
        subcategory=subcategory,
        pattern=pattern,
        formality_score=formality,
        seasons=seasons,
        notes=notes,
    )


# --- Model backend (lazy, optional) ------------------------------------------
class Tagger:
    """Lazily-loaded Fashion-CLIP wrapper. Safe to construct even when the ML
    dependencies are missing — it simply reports itself unavailable."""

    def __init__(self) -> None:
        self._model = None
        self._processor = None
        self._torch = None
        self._lock = threading.Lock()
        self._load_failed = False

    @property
    def enabled(self) -> bool:
        return config.ENABLE_AUTOTAG and not self._load_failed

    def _ensure_loaded(self) -> bool:
        if self._model is not None:
            return True
        if self._load_failed:
            return False
        with self._lock:
            if self._model is not None:
                return True
            try:
                import os

                config.MODEL_DIR.mkdir(parents=True, exist_ok=True)
                os.environ.setdefault("HF_HOME", str(config.MODEL_DIR))
                import torch
                from transformers import CLIPModel, CLIPProcessor

                log.info("Loading Fashion-CLIP model %s (first run downloads weights)…", config.AUTOTAG_MODEL)
                self._torch = torch
                self._model = CLIPModel.from_pretrained(config.AUTOTAG_MODEL)
                self._processor = CLIPProcessor.from_pretrained(config.AUTOTAG_MODEL)
                self._model.eval()
                log.info("Fashion-CLIP ready.")
                return True
            except Exception as exc:  # noqa: BLE001
                log.warning("Auto-tagging disabled — could not load model: %s", exc)
                self._load_failed = True
                return False

    def _image_features(self, pil_image):
        torch = self._torch
        inputs = self._processor(images=pil_image, return_tensors="pt")
        with torch.no_grad():
            feat = self._model.get_image_features(**inputs)
        return feat / feat.norm(dim=-1, keepdim=True)

    def _text_sims(self, img_feat, texts: list[str]) -> list[float]:
        torch = self._torch
        inputs = self._processor(text=texts, return_tensors="pt", padding=True)
        with torch.no_grad():
            tfeat = self._model.get_text_features(**inputs)
        tfeat = tfeat / tfeat.norm(dim=-1, keepdim=True)
        scale = self._model.logit_scale.exp()
        sims = (img_feat @ tfeat.T).squeeze(0) * scale
        return sims.tolist()

    def suggest(self, pil_image) -> Suggestions | None:
        if not self.enabled or not self._ensure_loaded():
            return None
        try:
            img_feat = self._image_features(pil_image.convert("RGB"))

            sim_groups: dict[str, list[float]] = {}
            sim_groups["category"] = self._text_sims(
                img_feat, [f"a photo of {p}" for p in CATEGORY_PROMPTS.values()]
            )
            # Predict the category first, then score only its subcategories.
            cat = best_label(softmax(sim_groups["category"]), list(CATEGORY_PROMPTS.keys()))[0]
            subs = SUBCATEGORIES.get(cat, [])
            if subs:
                sim_groups[f"sub:{cat}"] = self._text_sims(
                    img_feat, [f"a photo of {s}" for s in subs]
                )
            sim_groups["pattern"] = self._text_sims(img_feat, [f"a photo of {p} clothing" for p in PATTERNS])
            sim_groups["formality"] = self._text_sims(
                img_feat, [f"a photo of {phrase}" for phrase, _ in FORMALITY_LEVELS]
            )
            sim_groups["season"] = self._text_sims(
                img_feat, [f"a photo of {p}" for p in SEASON_PROMPTS.values()]
            )
            return build_suggestions(sim_groups)
        except Exception as exc:  # noqa: BLE001
            log.warning("Auto-tag inference failed: %s", exc)
            return None


# Module-level singleton.
tagger = Tagger()
