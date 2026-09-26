"""Pricing registry API. Append-only: there is no update or delete. A price change is a new
entry with a later effective date, so past runs stay explainable."""

from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.costs.pricing import find_model_pricing
from app.db import get_db
from app.models import ModelPricing, ToolPricing
from app.schemas import ModelPricingIn, ModelPricingOut, ToolPricingIn, ToolPricingOut

router = APIRouter(prefix="/pricing", tags=["pricing"])


@router.post("/models", response_model=ModelPricingOut, status_code=status.HTTP_201_CREATED)
def add_model_pricing(body: ModelPricingIn, db: Session = Depends(get_db)) -> ModelPricing:
    duplicate = db.scalar(
        select(ModelPricing.id).where(
            ModelPricing.provider == body.provider,
            ModelPricing.model == body.model,
            ModelPricing.effective_date == body.effective_date,
        )
    )
    if duplicate:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "a price for this model and effective date exists; entries are immutable, "
            "add one with a later effective_date instead",
        )
    entry = ModelPricing(**body.model_dump())
    db.add(entry)
    db.commit()
    return entry


@router.get("/models", response_model=list[ModelPricingOut])
def list_model_pricing(
    provider: str | None = None, model: str | None = None, db: Session = Depends(get_db)
) -> list[ModelPricing]:
    query = select(ModelPricing).order_by(
        ModelPricing.provider, ModelPricing.model, ModelPricing.effective_date.desc()
    )
    if provider:
        query = query.where(ModelPricing.provider == provider)
    if model:
        query = query.where(ModelPricing.model == model)
    return list(db.scalars(query))


@router.get("/models/current", response_model=ModelPricingOut)
def current_model_pricing(
    provider: str, model: str, on: date | None = None, db: Session = Depends(get_db)
) -> ModelPricing:
    """The price in effect on `on` (default: today)."""
    entry = find_model_pricing(db, provider, model, on or datetime.now(UTC).date())
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no price for this model on that date")
    return entry


@router.post("/tools", response_model=ToolPricingOut, status_code=status.HTTP_201_CREATED)
def add_tool_pricing(body: ToolPricingIn, db: Session = Depends(get_db)) -> ToolPricing:
    duplicate = db.scalar(
        select(ToolPricing.id).where(
            ToolPricing.tool_name == body.tool_name,
            ToolPricing.effective_date == body.effective_date,
        )
    )
    if duplicate:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "a price for this tool and effective date exists"
        )
    entry = ToolPricing(**body.model_dump())
    db.add(entry)
    db.commit()
    return entry


@router.get("/tools", response_model=list[ToolPricingOut])
def list_tool_pricing(db: Session = Depends(get_db)) -> list[ToolPricing]:
    return list(
        db.scalars(
            select(ToolPricing).order_by(ToolPricing.tool_name, ToolPricing.effective_date.desc())
        )
    )
