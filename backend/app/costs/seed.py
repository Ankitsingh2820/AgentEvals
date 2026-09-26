"""Load the initial price list into the pricing registry. Idempotent: entries that already
exist (same provider/model/effective_date) are left untouched, never overwritten.

    python -m app.costs.seed
"""

import json
import logging
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ModelPricing, ToolPricing

SEED_FILE = Path(__file__).parent / "seed_pricing.json"
DEFAULT_SOURCE = "seed_pricing.json (list prices as of 2026-06-24)"

logger = logging.getLogger(__name__)


def _dec(v: str | None) -> Decimal | None:
    return None if v is None else Decimal(v)


def seed_pricing(db: Session, path: Path = SEED_FILE) -> int:
    data = json.loads(path.read_text(encoding="utf-8"))
    added = 0
    for m in data["models"]:
        eff = date.fromisoformat(m["effective_date"])
        exists = db.scalar(
            select(ModelPricing.id).where(
                ModelPricing.provider == m["provider"],
                ModelPricing.model == m["model"],
                ModelPricing.effective_date == eff,
            )
        )
        if exists:
            continue
        db.add(
            ModelPricing(
                provider=m["provider"],
                model=m["model"],
                input_price_per_mtok=Decimal(m["input"]),
                output_price_per_mtok=Decimal(m["output"]),
                cache_write_price_per_mtok=_dec(m.get("cache_write")),
                cache_read_price_per_mtok=_dec(m.get("cache_read")),
                currency=m.get("currency", "USD"),
                effective_date=eff,
                source=m.get("source", DEFAULT_SOURCE),
            )
        )
        added += 1
    for t in data.get("tools", []):
        eff = date.fromisoformat(t["effective_date"])
        exists = db.scalar(
            select(ToolPricing.id).where(
                ToolPricing.tool_name == t["tool_name"], ToolPricing.effective_date == eff
            )
        )
        if exists:
            continue
        db.add(
            ToolPricing(
                tool_name=t["tool_name"],
                price_per_call=Decimal(t["price_per_call"]),
                currency=t.get("currency", "USD"),
                effective_date=eff,
                source=t.get("source", DEFAULT_SOURCE),
            )
        )
        added += 1
    db.commit()
    return added


if __name__ == "__main__":
    from app.config import get_settings
    from app.db import SessionLocal
    from app.logging_config import configure_logging

    configure_logging(get_settings().log_level)
    with SessionLocal() as session:
        logger.info("pricing seeded", extra={"entries_added": seed_pricing(session)})
