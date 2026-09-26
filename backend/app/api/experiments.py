from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db import get_db, get_session_factory
from app.evaluation.summary import run_quality
from app.experiments.criteria import AcceptanceCriteria
from app.experiments.runner import CONFIG_FIELDS, config_snapshot
from app.models import (
    AgentVersion,
    Dataset,
    DatasetCase,
    EvaluationResult,
    Evaluator,
    Experiment,
    Run,
)
from app.ratelimit import rate_limit
from app.schemas import ExperimentCaseOut, ExperimentIn, ExperimentOut
from app.tasks import dispatch

router = APIRouter(prefix="/experiments", tags=["experiments"])


def _get(db: Session, experiment_id: str) -> Experiment:
    exp = db.get(Experiment, experiment_id)
    if exp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "experiment not found")
    return exp


@router.post("", response_model=ExperimentOut, status_code=status.HTTP_201_CREATED)
def create_experiment(body: ExperimentIn, db: Session = Depends(get_db)) -> Experiment:
    """Define an experiment. Nothing runs until POST /experiments/{id}/run.

    Baseline and candidate may be the same version: an A/A experiment measures how much
    results vary between identical configurations (the noise floor)."""
    for vid in (body.baseline_agent_version_id, body.candidate_agent_version_id):
        if db.get(AgentVersion, vid) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"agent version {vid} not found")
    if db.get(Dataset, body.dataset_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "dataset not found")
    if len(set(body.evaluator_ids)) != len(body.evaluator_ids):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "duplicate evaluator ids")
    found = set(db.scalars(select(Evaluator.id).where(Evaluator.id.in_(body.evaluator_ids))))
    if missing := [i for i in body.evaluator_ids if i not in found]:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"evaluators not found: {missing}")
    try:
        criteria = AcceptanceCriteria.model_validate(body.acceptance_criteria)
    except ValidationError as e:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            e.errors(include_url=False, include_context=False),
        ) from e

    if body.isolate is not None and (unknown := set(body.isolate) - set(CONFIG_FIELDS)):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"unknown config fields in isolate: {sorted(unknown)}",
        )

    exp = Experiment(
        name=body.name,
        description=body.description,
        dataset_id=body.dataset_id,
        baseline_agent_version_id=body.baseline_agent_version_id,
        candidate_agent_version_id=body.candidate_agent_version_id,
        evaluator_ids=body.evaluator_ids,
        repetitions=body.repetitions,
        acceptance_criteria=criteria.model_dump(),
        config_snapshot={},
    )
    db.add(exp)
    db.flush()
    exp.config_snapshot = config_snapshot(db, exp)
    if body.isolate is not None:
        if extra := [f for f in exp.config_snapshot["config_diff"] if f not in body.isolate]:
            db.rollback()
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"arms also differ in {extra}; only {body.isolate} may differ",
            )
        exp.config_snapshot["isolate"] = body.isolate
    db.commit()
    return exp


@router.post(
    "/{experiment_id}/run",
    response_model=ExperimentOut,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(rate_limit("execute"))],
)
def run(
    experiment_id: str,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    session_factory: sessionmaker = Depends(get_session_factory),
) -> Experiment:
    """Run once. To repeat an experiment, use /reproduce, which keeps both results."""
    exp = _get(db, experiment_id)
    if exp.status != "created":
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"experiment is {exp.status}; use /reproduce to run it again"
        )
    exp.status = "queued"
    db.commit()
    dispatch("experiment", exp.id, background, session_factory)
    return exp


@router.post(
    "/{experiment_id}/reproduce", response_model=ExperimentOut, status_code=status.HTTP_201_CREATED
)
def reproduce(experiment_id: str, db: Session = Depends(get_db)) -> Experiment:
    """Create a new experiment with the identical, pinned configuration."""
    src = _get(db, experiment_id)
    exp = Experiment(
        name=src.name,
        description=src.description,
        dataset_id=src.dataset_id,
        baseline_agent_version_id=src.baseline_agent_version_id,
        candidate_agent_version_id=src.candidate_agent_version_id,
        evaluator_ids=src.evaluator_ids,
        repetitions=src.repetitions,
        acceptance_criteria=src.acceptance_criteria,
        config_snapshot=src.config_snapshot,
        reproduction_of=src.id,
    )
    db.add(exp)
    db.commit()
    return exp


@router.get("", response_model=list[ExperimentOut])
def list_experiments(db: Session = Depends(get_db)) -> list[Experiment]:
    return list(db.scalars(select(Experiment).order_by(Experiment.created_at.desc())))


@router.get("/{experiment_id}", response_model=ExperimentOut)
def get_experiment(experiment_id: str, db: Session = Depends(get_db)) -> Experiment:
    return _get(db, experiment_id)


def _side(run: Run | None, results: list[EvaluationResult]) -> dict | None:
    if run is None:
        return None
    q = run_quality(results)
    return {
        "run_id": run.id,
        "status": run.status,
        "quality": None if q is None else q[0],
        "passed": None if q is None else q[1],
        "estimated_cost": run.estimated_total_cost,
        "latency_ms": run.total_latency_ms,
        "tool_calls": run.tool_call_count,
    }


@router.get("/{experiment_id}/cases", response_model=list[ExperimentCaseOut])
def compare_cases(experiment_id: str, db: Session = Depends(get_db)):
    """Side-by-side results per case, largest quality regressions first."""
    exp = _get(db, experiment_id)
    arm_of = {exp.baseline_evaluation_id: "baseline", exp.candidate_evaluation_id: "candidate"}
    runs = list(db.scalars(select(Run).where(Run.evaluation_id.in_(list(arm_of)))))
    results: dict[str, list] = {}
    for r in db.scalars(
        select(EvaluationResult).where(EvaluationResult.run_id.in_([run.id for run in runs]))
    ):
        results.setdefault(r.run_id, []).append(r)
    keys = {
        c.id: c.case_key
        for c in db.scalars(select(DatasetCase).where(DatasetCase.dataset_id == exp.dataset_id))
    }

    table: dict[tuple, dict] = {}
    for run in runs:
        row = table.setdefault(
            (run.dataset_case_id, run.repetition),
            {
                "case_key": keys.get(run.dataset_case_id),
                "repetition": run.repetition,
                "baseline": None,
                "candidate": None,
            },
        )
        row[arm_of[run.evaluation_id]] = _side(run, results.get(run.id, []))

    def delta(row):
        b, c = row["baseline"], row["candidate"]
        if b and c and b["quality"] is not None and c["quality"] is not None:
            return c["quality"] - b["quality"]
        return None

    rows = [{**row, "quality_delta": delta(row)} for row in table.values()]
    rows.sort(
        key=lambda r: (
            r["quality_delta"] is None,
            r["quality_delta"] or 0,
            r["case_key"] or "",
            r["repetition"],
        )
    )
    return rows
