"""Pydantic request/response models."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ItemBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    category: str
    subcategory: str | None = None
    colors: list[str] = []
    primary_color_hex: str | None = None
    material: str | None = None
    seasons: list[str] = []
    occasions: list[str] = []
    fit: str | None = None
    formality_score: int = Field(5, ge=1, le=10)
    comfort_score: int = Field(7, ge=1, le=10)
    warmth: int = Field(5, ge=1, le=10)
    rain_safe: bool = True
    purchase_price: float | None = None
    laundry_status: str = "available"
    status: str = "owned"
    notes: str | None = None


class ItemCreate(ItemBase):
    # Populated from the /api/analyze step after a photo is captured.
    image: str | None = None
    thumbnail: str | None = None


class AnalyzeResult(BaseModel):
    image: str
    thumbnail: str
    primary_color_hex: str | None = None
    color_name: str | None = None


class ItemUpdate(BaseModel):
    """All fields optional for PATCH-style edits."""

    name: str | None = None
    category: str | None = None
    subcategory: str | None = None
    colors: list[str] | None = None
    primary_color_hex: str | None = None
    material: str | None = None
    seasons: list[str] | None = None
    occasions: list[str] | None = None
    fit: str | None = None
    formality_score: int | None = Field(None, ge=1, le=10)
    comfort_score: int | None = Field(None, ge=1, le=10)
    warmth: int | None = Field(None, ge=1, le=10)
    rain_safe: bool | None = None
    purchase_price: float | None = None
    laundry_status: str | None = None
    status: str | None = None
    notes: str | None = None


class ItemOut(ItemBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    image: str | None = None
    thumbnail: str | None = None
    wear_count: int = 0
    last_worn: datetime | None = None
    created_at: datetime
    cost_per_wear: float | None = None


class OutfitItemRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    category: str
    thumbnail: str | None = None
    primary_color_hex: str | None = None


class ScoredOutfit(BaseModel):
    item_ids: list[int]
    items: list[OutfitItemRef]
    score: float
    breakdown: dict[str, float]
    rationale: str


class OutfitOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str | None = None
    item_ids: list[int]
    occasion: str | None = None
    score: float | None = None
    score_breakdown: dict | None = None
    favorite: bool = False
    wear_count: int = 0
    last_worn: datetime | None = None
    created_at: datetime
    items: list[OutfitItemRef] = []


class OutfitSave(BaseModel):
    item_ids: list[int]
    name: str | None = None
    occasion: str | None = None
    score: float | None = None
    score_breakdown: dict | None = None
    favorite: bool = False


class RecommendRequest(BaseModel):
    occasion: str | None = None
    season: str | None = None
    temperature_c: float | None = None  # target outdoor temperature
    raining: bool = False
    require_outerwear: bool | None = None  # None = auto based on temperature
    limit: int = Field(8, ge=1, le=30)
    # Optionally force certain items to be included (e.g. "style around this top").
    include_item_ids: list[int] = []


class PreferencesOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    preferred_colors: list[str] = []
    formality_preference: int = 5
    prioritize_underused: bool = True
    capsule_targets: dict = {}
    weights: dict = {}


class PreferencesUpdate(BaseModel):
    preferred_colors: list[str] | None = None
    formality_preference: int | None = Field(None, ge=1, le=10)
    prioritize_underused: bool | None = None
    capsule_targets: dict | None = None
    weights: dict | None = None
