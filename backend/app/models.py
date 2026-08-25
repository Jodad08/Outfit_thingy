"""ORM models for the wardrobe.

The schema follows the reference design: each garment is a structured record
carrying the attributes needed for deterministic filtering (Layer 2) and
recommendation scoring (Layer 3).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base

# --- Controlled vocabularies -------------------------------------------------
CATEGORIES = ["top", "bottom", "dress", "outerwear", "shoes", "bag", "accessory"]
SEASONS = ["spring", "summer", "fall", "winter"]
OCCASIONS = ["casual", "work", "formal", "sport", "travel", "loungewear", "party"]
LAUNDRY_STATUSES = ["available", "laundry", "dry_cleaner", "repair", "packed"]
ITEM_STATUSES = ["owned", "unavailable", "for_sale", "donate"]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Item(Base):
    __tablename__ = "items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    category: Mapped[str] = mapped_column(String, nullable=False, index=True)
    subcategory: Mapped[str | None] = mapped_column(String, nullable=True)

    # Human-readable color tags plus a machine primary color for harmony scoring.
    colors: Mapped[list] = mapped_column(JSON, default=list)
    primary_color_hex: Mapped[str | None] = mapped_column(String, nullable=True)

    material: Mapped[str | None] = mapped_column(String, nullable=True)
    seasons: Mapped[list] = mapped_column(JSON, default=list)
    occasions: Mapped[list] = mapped_column(JSON, default=list)
    fit: Mapped[str | None] = mapped_column(String, nullable=True)

    # 1-10 subjective/derived scores.
    formality_score: Mapped[int] = mapped_column(Integer, default=5)
    comfort_score: Mapped[int] = mapped_column(Integer, default=7)
    warmth: Mapped[int] = mapped_column(Integer, default=5)  # 1=cold-weather-only ... 10=very warm
    rain_safe: Mapped[bool] = mapped_column(Boolean, default=True)

    wear_count: Mapped[int] = mapped_column(Integer, default=0)
    last_worn: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    purchase_price: Mapped[float | None] = mapped_column(Float, nullable=True)

    laundry_status: Mapped[str] = mapped_column(String, default="available")
    status: Mapped[str] = mapped_column(String, default="owned")

    image: Mapped[str | None] = mapped_column(String, nullable=True)
    thumbnail: Mapped[str | None] = mapped_column(String, nullable=True)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class Outfit(Base):
    __tablename__ = "outfits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    item_ids: Mapped[list] = mapped_column(JSON, default=list)
    occasion: Mapped[str | None] = mapped_column(String, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_breakdown: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    wear_count: Mapped[int] = mapped_column(Integer, default=0)
    last_worn: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class WearLog(Base):
    __tablename__ = "wear_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    outfit_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    worn_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)

    item: Mapped[Item] = relationship("Item")


class Inspiration(Base):
    """An inspiration image (a Pinterest pin, or a manually uploaded look)."""

    __tablename__ = "inspiration"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String, default="manual")  # pinterest | manual
    external_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    board_id: Mapped[str | None] = mapped_column(String, nullable=True)
    board_name: Mapped[str | None] = mapped_column(String, nullable=True)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    link: Mapped[str | None] = mapped_column(String, nullable=True)

    image: Mapped[str | None] = mapped_column(String, nullable=True)
    thumbnail: Mapped[str | None] = mapped_column(String, nullable=True)
    primary_color_hex: Mapped[str | None] = mapped_column(String, nullable=True)
    palette: Mapped[list] = mapped_column(JSON, default=list)  # list of hex strings

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class PinterestAuth(Base):
    """Singleton (id=1) row holding the OAuth tokens for the connected account.

    Stored locally in SQLite. This is a single-user, self-hosted app, so tokens
    live on your own machine and never leave it.
    """

    __tablename__ = "pinterest_auth"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    access_token: Mapped[str | None] = mapped_column(String, nullable=True)
    refresh_token: Mapped[str | None] = mapped_column(String, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    scopes: Mapped[str | None] = mapped_column(String, nullable=True)
    # Transient CSRF state for an in-flight OAuth authorization.
    pending_state: Mapped[str | None] = mapped_column(String, nullable=True)


class Preferences(Base):
    """Singleton (id=1) row holding personal style rules and scoring weights."""

    __tablename__ = "preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    preferred_colors: Mapped[list] = mapped_column(JSON, default=list)
    formality_preference: Mapped[int] = mapped_column(Integer, default=5)  # 1-10
    prioritize_underused: Mapped[bool] = mapped_column(Boolean, default=True)
    # Capsule-wardrobe target counts per category (used by analytics/gap finder).
    capsule_targets: Mapped[dict] = mapped_column(JSON, default=dict)
    # Scoring weights (must roughly sum to 1); overrides the defaults if set.
    weights: Mapped[dict] = mapped_column(JSON, default=dict)
