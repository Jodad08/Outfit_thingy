"""Outfit recommendation engine.

Implements the layered approach from the reference design:

  Layer 2 (deterministic filtering): hard constraints remove items that are
    unavailable, out of season, wrong for the weather/occasion, or rain-unsafe.

  Layer 3 (recommendation scoring): remaining candidate outfits are scored with
    a weighted formula:

      Outfit Score = OccasionMatch  * 0.25
                   + WeatherMatch   * 0.20
                   + ColorHarmony   * 0.15
                   + Comfort        * 0.15
                   + PersonalPref   * 0.15
                   + RotationBenefit* 0.10

A plain-language rationale is attached to each outfit (a deterministic stand-in
for the optional AI-explanation layer), so nothing about the recommendation is
a black box.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass
from datetime import datetime, timezone

from . import color_utils, config
from .models import Item, Preferences

DEFAULT_WEIGHTS = {
    "occasion": 0.25,
    "weather": 0.20,
    "color": 0.15,
    "comfort": 0.15,
    "preference": 0.15,
    "rotation": 0.10,
}

# Which categories make up a complete outfit. Two silhouettes are supported.
_TOP_BOTTOM = ["top", "bottom", "shoes"]
_DRESS = ["dress", "shoes"]

# How strongly an inspiration palette pulls the ranking when one is supplied.
INSPO_WEIGHT = 0.4


@dataclass
class Candidate:
    items: list[Item]
    score: float
    breakdown: dict[str, float]
    rationale: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def passes_hard_constraints(
    item: Item,
    *,
    season: str | None,
    occasion: str | None,
    raining: bool,
    temperature_c: float | None,
) -> bool:
    """Layer 2: deterministic filtering. Returns True if the item is eligible."""
    if item.status != "owned":
        return False
    if item.laundry_status != "available":
        return False
    if season and item.seasons and season not in item.seasons:
        return False
    if occasion and item.occasions and occasion not in item.occasions:
        return False
    if raining and item.category == "shoes" and not item.rain_safe:
        return False
    # Extreme-temperature guard: exclude clearly wrong-weight garments.
    if temperature_c is not None:
        if temperature_c <= 5 and item.warmth <= 2:
            return False
        if temperature_c >= 28 and item.warmth >= 9:
            return False
    return True


def _weather_match(item: Item, temperature_c: float | None) -> float:
    """How appropriate an item's warmth is for the target temperature."""
    if temperature_c is None:
        return 0.7
    # Map temperature to an ideal warmth level (1-10). Colder -> warmer clothes.
    ideal = max(1.0, min(10.0, 10.0 - (temperature_c - 2) * (9.0 / 33.0)))
    return max(0.0, 1.0 - abs(item.warmth - ideal) / 9.0)


def _rotation_benefit(item: Item, max_wear: int) -> float:
    """Favor under-used items and those not worn recently (wardrobe rotation)."""
    freshness = 1.0 - (item.wear_count / max_wear if max_wear else 0.0)
    recency = 1.0
    last = _aware(item.last_worn)
    if last:
        days = (_now() - last).days
        recency = min(1.0, days / 30.0)  # worn today -> 0, a month ago -> 1
    return 0.5 * freshness + 0.5 * recency


def _preference_score(item: Item, prefs: Preferences) -> float:
    score = 0.5
    if prefs.preferred_colors:
        name = (
            color_utils.nearest_color_name(item.primary_color_hex)
            if item.primary_color_hex
            else None
        )
        item_colors = {c.lower() for c in item.colors}
        if name:
            item_colors.add(name)
        if item_colors & {c.lower() for c in prefs.preferred_colors}:
            score += 0.35
    # Reward closeness to the preferred formality level.
    score += 0.15 * (1.0 - abs(item.formality_score - prefs.formality_preference) / 9.0)
    return min(1.0, score)


def _score_outfit(
    items: list[Item],
    *,
    occasion: str | None,
    temperature_c: float | None,
    prefs: Preferences,
    max_wear: int,
    weights: dict[str, float],
    target_palette: list[str] | None = None,
) -> tuple[float, dict[str, float]]:
    # Occasion match: fraction of items explicitly tagged for the occasion.
    if occasion:
        tagged = [1.0 if occasion in i.occasions else 0.0 for i in items]
        occasion_match = sum(tagged) / len(items)
    else:
        # No occasion given: reward internal formality consistency instead.
        formalities = [i.formality_score for i in items]
        spread = max(formalities) - min(formalities)
        occasion_match = max(0.0, 1.0 - spread / 9.0)

    weather = sum(_weather_match(i, temperature_c) for i in items) / len(items)
    color = color_utils.harmony_score([i.primary_color_hex for i in items])
    comfort = sum(i.comfort_score for i in items) / (10.0 * len(items))
    preference = sum(_preference_score(i, prefs) for i in items) / len(items)
    rotation = sum(_rotation_benefit(i, max_wear) for i in items) / len(items)

    breakdown = {
        "occasion": round(occasion_match, 3),
        "weather": round(weather, 3),
        "color": round(color, 3),
        "comfort": round(comfort, 3),
        "preference": round(preference, 3),
        "rotation": round(rotation, 3),
    }
    total = sum(weights[k] * breakdown[k] for k in weights)

    # Blend in inspiration-palette matching when a target palette is given.
    if target_palette:
        echo = color_utils.palette_match([i.primary_color_hex for i in items], target_palette)
        breakdown["inspiration"] = round(echo, 3)
        total = total * (1 - INSPO_WEIGHT) + echo * INSPO_WEIGHT

    return round(total, 4), breakdown


def _rationale(items: list[Item], breakdown: dict[str, float], occasion: str | None) -> str:
    parts: list[str] = []
    if occasion:
        parts.append(f"Suited for {occasion}")
    top_terms = sorted(breakdown.items(), key=lambda kv: kv[1], reverse=True)[:2]
    label = {
        "occasion": "occasion fit",
        "weather": "weather fit",
        "color": "color harmony",
        "comfort": "comfort",
        "preference": "your style preferences",
        "rotation": "freshens rotation",
        "inspiration": "matches your inspiration",
    }
    parts.append("strong on " + " and ".join(label[k] for k, _ in top_terms))
    colors = [c for c in (i.primary_color_hex for i in items) if c]
    names = sorted({color_utils.nearest_color_name(c) for c in colors})
    if names:
        parts.append(", ".join(names))
    return "; ".join(parts) + "."


def _pick(items: list[Item], category: str) -> list[Item]:
    return [i for i in items if i.category == category]


def generate(
    all_items: list[Item],
    prefs: Preferences,
    *,
    occasion: str | None = None,
    season: str | None = None,
    temperature_c: float | None = None,
    raining: bool = False,
    require_outerwear: bool | None = None,
    include_item_ids: list[int] | None = None,
    target_palette: list[str] | None = None,
    limit: int = 8,
) -> list[Candidate]:
    include_item_ids = include_item_ids or []
    weights = {**DEFAULT_WEIGHTS, **(prefs.weights or {})}

    eligible = [
        i
        for i in all_items
        if passes_hard_constraints(
            i,
            season=season,
            occasion=occasion,
            raining=raining,
            temperature_c=temperature_c,
        )
    ]
    by_id = {i.id: i for i in all_items}
    forced = [by_id[i] for i in include_item_ids if i in by_id]

    max_wear = max((i.wear_count for i in eligible), default=0) or 1

    # Decide whether an outer layer is required.
    if require_outerwear is None:
        require_outerwear = temperature_c is not None and temperature_c <= 12

    candidates: list[Candidate] = []

    for base in (_TOP_BOTTOM, _DRESS):
        buckets = {cat: _pick(eligible, cat) for cat in base}
        if any(not buckets[cat] for cat in base):
            continue

        # Honor forced items: constrain the relevant category to that item.
        for f in forced:
            if f.category in buckets:
                buckets[f.category] = [f]

        combo_iters = [buckets[cat] for cat in base]
        for combo in _bounded_product(combo_iters):
            outfit = list(combo)

            # Add outerwear when required/available.
            outer_options = _pick(eligible, "outerwear")
            if require_outerwear:
                if not outer_options:
                    continue
                variants = [outfit + [o] for o in outer_options[:6]]
            elif outer_options and (temperature_c is not None and temperature_c <= 18):
                variants = [outfit, outfit + [outer_options[0]]]
            else:
                variants = [outfit]

            for variant in variants:
                if forced and not all(f in variant for f in forced if f.category in base + ["outerwear"]):
                    continue
                score, breakdown = _score_outfit(
                    variant,
                    occasion=occasion,
                    temperature_c=temperature_c,
                    prefs=prefs,
                    max_wear=max_wear,
                    weights=weights,
                    target_palette=target_palette,
                )
                candidates.append(
                    Candidate(
                        items=variant,
                        score=score,
                        breakdown=breakdown,
                        rationale=_rationale(variant, breakdown, occasion),
                    )
                )

    candidates.sort(key=lambda c: c.score, reverse=True)
    return _dedupe(candidates)[:limit]


def _bounded_product(iterables: list[list[Item]]):
    """itertools.product with a global cap so large wardrobes stay responsive."""
    count = 0
    for combo in itertools.product(*iterables):
        yield combo
        count += 1
        if count >= config.MAX_COMBINATIONS:
            return


def _dedupe(candidates: list[Candidate]) -> list[Candidate]:
    seen: set[frozenset[int]] = set()
    out: list[Candidate] = []
    for c in candidates:
        key = frozenset(i.id for i in c.items)
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out
