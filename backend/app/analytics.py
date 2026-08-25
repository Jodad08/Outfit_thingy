"""Wardrobe analytics: cost-per-wear, rotation coverage, capsule gaps."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Item


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def summary(db: Session, capsule_targets: dict | None = None) -> dict:
    items = list(db.scalars(select(Item)).all())
    owned = [i for i in items if i.status == "owned"]
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=90)

    by_category: dict[str, int] = {}
    for i in owned:
        by_category[i.category] = by_category.get(i.category, 0) + 1

    total_spend = sum(i.purchase_price or 0 for i in owned)
    total_wears = sum(i.wear_count for i in owned)

    worn_90 = sum(1 for i in owned if _aware(i.last_worn) and _aware(i.last_worn) >= cutoff)
    pct_worn_90 = round(100 * worn_90 / len(owned), 1) if owned else 0.0

    # Cost per wear per item (only where a price is recorded).
    cpw = [
        {
            "id": i.id,
            "name": i.name,
            "cost_per_wear": round(i.purchase_price / i.wear_count, 2)
            if i.purchase_price and i.wear_count
            else None,
            "wear_count": i.wear_count,
            "purchase_price": i.purchase_price,
        }
        for i in owned
        if i.purchase_price
    ]
    worst_cpw = sorted(
        [c for c in cpw if c["cost_per_wear"] is not None],
        key=lambda c: c["cost_per_wear"],
        reverse=True,
    )[:5]

    never_worn = sorted(
        ({"id": i.id, "name": i.name, "category": i.category} for i in owned if i.wear_count == 0),
        key=lambda x: x["name"],
    )

    most_worn = sorted(owned, key=lambda i: i.wear_count, reverse=True)[:5]

    # Capsule gaps: category counts below target.
    gaps = []
    for cat, target in (capsule_targets or {}).items():
        have = by_category.get(cat, 0)
        if have < target:
            gaps.append({"category": cat, "have": have, "target": target, "gap": target - have})

    least_used_category = min(by_category, key=by_category.get) if by_category else None

    return {
        "item_count": len(owned),
        "total_spend": round(total_spend, 2),
        "total_wears": total_wears,
        "avg_wears_per_item": round(total_wears / len(owned), 1) if owned else 0.0,
        "pct_worn_last_90_days": pct_worn_90,
        "by_category": by_category,
        "least_used_category": least_used_category,
        "capsule_gaps": gaps,
        "never_worn": never_worn,
        "highest_cost_per_wear": worst_cpw,
        "most_worn": [
            {"id": i.id, "name": i.name, "wear_count": i.wear_count} for i in most_worn
        ],
    }
