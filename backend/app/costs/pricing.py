"""Pricing registry lookups. The price that applies on a date is the entry with the latest
effective_date on or before that date."""

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.costs.calculator import ModelPrices
from app.models import ModelPricing, ToolPricing


def find_model_pricing(db: Session, provider: str, model: str, on: date) -> ModelPricing | None:
    return db.scalar(
        select(ModelPricing)
        .where(
            ModelPricing.provider == provider,
            ModelPricing.model == model,
            ModelPricing.effective_date <= on,
        )
        .order_by(ModelPricing.effective_date.desc())
        .limit(1)
    )


def find_tool_pricing(db: Session, tool_name: str, on: date) -> ToolPricing | None:
    return db.scalar(
        select(ToolPricing)
        .where(ToolPricing.tool_name == tool_name, ToolPricing.effective_date <= on)
        .order_by(ToolPricing.effective_date.desc())
        .limit(1)
    )


def to_prices(p: ModelPricing) -> ModelPrices:
    return ModelPrices(
        input_per_mtok=p.input_price_per_mtok,
        output_per_mtok=p.output_price_per_mtok,
        cache_write_per_mtok=p.cache_write_price_per_mtok,
        cache_read_per_mtok=p.cache_read_price_per_mtok,
        currency=p.currency,
    )


def snapshot(p: ModelPricing) -> dict[str, Any]:
    """JSON copy of the prices used, stored on each LLM call (decimals as strings)."""

    def s(v):
        return None if v is None else str(v)

    return {
        "pricing_id": p.id,
        "provider": p.provider,
        "model": p.model,
        "input_price_per_mtok": s(p.input_price_per_mtok),
        "output_price_per_mtok": s(p.output_price_per_mtok),
        "cache_write_price_per_mtok": s(p.cache_write_price_per_mtok),
        "cache_read_price_per_mtok": s(p.cache_read_price_per_mtok),
        "currency": p.currency,
        "effective_date": p.effective_date.isoformat(),
    }
