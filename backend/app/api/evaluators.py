"""Evaluators are versioned and immutable, like agents and datasets: posting an existing
name creates its next version, so past scores always point at the rule that produced them."""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.evaluation.configs import LLMJudgeConfig, parse_config
from app.models import Evaluator
from app.providers import available_providers
from app.schemas import EvaluatorIn, EvaluatorOut

router = APIRouter(prefix="/evaluators", tags=["evaluators"])


@router.post("", response_model=EvaluatorOut, status_code=status.HTTP_201_CREATED)
def create_evaluator(body: EvaluatorIn, db: Session = Depends(get_db)) -> Evaluator:
    try:
        cfg = parse_config(body.type, body.config)
    except ValidationError as e:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            e.errors(include_url=False, include_context=False),
        ) from e
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e
    if isinstance(cfg, LLMJudgeConfig) and cfg.provider not in available_providers():
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"unknown judge provider '{cfg.provider}'"
        )

    latest = db.scalar(select(func.max(Evaluator.version)).where(Evaluator.name == body.name))
    evaluator = Evaluator(
        name=body.name,
        version=(latest or 0) + 1,
        type=body.type,
        config=cfg.model_dump(),  # store with defaults filled in, so the version is complete
        description=body.description,
    )
    db.add(evaluator)
    db.commit()
    return evaluator


@router.get("", response_model=list[EvaluatorOut])
def list_evaluators(db: Session = Depends(get_db)) -> list[Evaluator]:
    return list(db.scalars(select(Evaluator).order_by(Evaluator.name, Evaluator.version.desc())))


@router.get("/{evaluator_id}", response_model=EvaluatorOut)
def get_evaluator(evaluator_id: str, db: Session = Depends(get_db)) -> Evaluator:
    evaluator = db.get(Evaluator, evaluator_id)
    if evaluator is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "evaluator not found")
    return evaluator
