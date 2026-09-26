from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.api.agents import get_agent_or_404
from app.db import get_db, get_session_factory
from app.models import (
    AgentVersion,
    Dataset,
    DatasetCase,
    Evaluation,
    EvaluationResult,
    Evaluator,
)
from app.ratelimit import rate_limit
from app.schemas import EvaluationIn, EvaluationOut, EvaluationResultOut
from app.tasks import dispatch

router = APIRouter(prefix="/evaluations", tags=["evaluations"])


@router.post(
    "",
    response_model=EvaluationOut,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(rate_limit("execute"))],
)
def create_evaluation(
    body: EvaluationIn,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    session_factory: sessionmaker = Depends(get_session_factory),
) -> Evaluation:
    """Start an evaluation. Returns immediately (202); poll GET /evaluations/{id}.

    Runs on the configured task backend (AGENTEVAL_TASK_BACKEND): in-process for
    development, or the Celery worker, which survives restarts and resumes interrupted
    evaluations.
    """
    agent = get_agent_or_404(db, body.agent_id)
    if body.agent_version_id:
        version = db.get(AgentVersion, body.agent_version_id)
        if version is None or version.agent_id != agent.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "agent version not found")
    else:
        version = agent.latest_version
    if db.get(Dataset, body.dataset_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "dataset not found")
    if len(set(body.evaluator_ids)) != len(body.evaluator_ids):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "duplicate evaluator ids")
    found = set(db.scalars(select(Evaluator.id).where(Evaluator.id.in_(body.evaluator_ids))))
    if missing := [i for i in body.evaluator_ids if i not in found]:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"evaluators not found: {missing}")

    evaluation = Evaluation(
        agent_id=agent.id,
        agent_version_id=version.id,
        dataset_id=body.dataset_id,
        evaluator_ids=body.evaluator_ids,
        repetitions=body.repetitions,
    )
    db.add(evaluation)
    db.commit()
    dispatch("evaluation", evaluation.id, background, session_factory)
    return evaluation


@router.get("", response_model=list[EvaluationOut])
def list_evaluations(agent_id: str | None = None, db: Session = Depends(get_db)):
    query = select(Evaluation).order_by(Evaluation.created_at.desc())
    if agent_id:
        query = query.where(Evaluation.agent_id == agent_id)
    return list(db.scalars(query))


def _get(db: Session, evaluation_id: str) -> Evaluation:
    evaluation = db.get(Evaluation, evaluation_id)
    if evaluation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "evaluation not found")
    return evaluation


@router.get("/{evaluation_id}", response_model=EvaluationOut)
def get_evaluation(evaluation_id: str, db: Session = Depends(get_db)) -> Evaluation:
    return _get(db, evaluation_id)


@router.get("/{evaluation_id}/results", response_model=list[EvaluationResultOut])
def get_results(evaluation_id: str, db: Session = Depends(get_db)):
    _get(db, evaluation_id)
    rows = db.execute(
        select(EvaluationResult, DatasetCase.case_key, Evaluator.name)
        .join(DatasetCase, DatasetCase.id == EvaluationResult.dataset_case_id)
        .join(Evaluator, Evaluator.id == EvaluationResult.evaluator_id)
        .where(EvaluationResult.evaluation_id == evaluation_id)
        .order_by(DatasetCase.position, Evaluator.name)
    )
    return [
        EvaluationResultOut.model_validate(
            {
                **{c.key: getattr(r, c.key) for c in EvaluationResult.__table__.columns},
                "case_key": key,
                "evaluator_name": name,
            }
        )
        for r, key, name in rows
    ]
