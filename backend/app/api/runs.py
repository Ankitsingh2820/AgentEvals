from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.agents import get_agent_or_404
from app.db import get_db
from app.engine.executor import execute_run
from app.models import AgentVersion, Run
from app.ratelimit import rate_limit
from app.schemas import LatencyBreakdown, RunCreate, RunOut, RunTraceOut

router = APIRouter(prefix="/runs", tags=["runs"])


@router.post(
    "",
    response_model=RunOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("execute"))],
)
def create_run(body: RunCreate, db: Session = Depends(get_db)) -> Run:
    """Execute an agent synchronously and return the run summary.

    A failed agent run still returns 201: the run was created and its failure is recorded
    in `status`/`error`. (Long-running batch execution moves to a worker queue later.)
    """
    agent = get_agent_or_404(db, body.agent_id)
    if body.agent_version_id:
        version = db.get(AgentVersion, body.agent_version_id)
        if version is None or version.agent_id != agent.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "agent version not found")
    else:
        version = agent.latest_version
    return execute_run(db, version, body.input)


@router.get("", response_model=list[RunOut])
def list_runs(
    agent_id: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[Run]:
    query = select(Run).order_by(Run.started_at.desc()).limit(limit)
    if agent_id:
        query = query.where(Run.agent_id == agent_id)
    return list(db.scalars(query))


def _get_run(db: Session, run_id: str) -> Run:
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    return run


@router.get("/{run_id}", response_model=RunOut)
def get_run(run_id: str, db: Session = Depends(get_db)) -> Run:
    return _get_run(db, run_id)


@router.get("/{run_id}/trace", response_model=RunTraceOut)
def get_trace(run_id: str, db: Session = Depends(get_db)) -> RunTraceOut:
    run = _get_run(db, run_id)
    llm_ms, tool_ms, total = run.llm_latency_ms, run.tool_latency_ms, run.total_latency_ms
    return RunTraceOut(
        run=RunOut.model_validate(run),
        agent_version=run.agent_version,
        latency=LatencyBreakdown(
            total_ms=total,
            llm_ms=llm_ms,
            tool_ms=tool_ms,
            other_ms=None if total is None else max(total - llm_ms - tool_ms, 0.0),
        ),
        steps=run.steps,
    )
