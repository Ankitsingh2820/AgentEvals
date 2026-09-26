from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.agents import _new_version, get_agent_or_404
from app.api.experiments import create_experiment
from app.db import get_db
from app.engine.options import AgentOptions
from app.models import AgentVersion, OptimizationReport, Run
from app.optimization.analyzer import analyze
from app.schemas import (
    AgentConfig,
    AgentVersionOut,
    ApplyOptimizationIn,
    ApplyOptimizationOut,
    ExperimentIn,
    ExperimentOut,
    OptimizationAnalyzeIn,
    OptimizationReportOut,
)

router = APIRouter(prefix="/optimizations", tags=["optimizations"])


@router.post("/analyze", response_model=OptimizationReportOut, status_code=status.HTTP_201_CREATED)
def analyze_agent(body: OptimizationAnalyzeIn, db: Session = Depends(get_db)):
    """Analyze one agent version's recorded runs (default: latest version, newest
    `limit` finished runs) and store the recommendations with their evidence."""
    agent = get_agent_or_404(db, body.agent_id)
    version = (
        db.get(AgentVersion, body.agent_version_id)
        if body.agent_version_id
        else agent.latest_version
    )
    if version is None or version.agent_id != agent.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agent version not found")
    runs = list(
        db.scalars(
            select(Run)
            .where(Run.agent_version_id == version.id, Run.status != "running")
            .order_by(Run.started_at.desc())
            .limit(body.limit)
        )
    )
    report = OptimizationReport(
        agent_id=agent.id,
        agent_version_id=version.id,
        runs_analyzed=len(runs),
        recommendations=analyze(db, version, runs),
    )
    db.add(report)
    db.commit()
    return report


@router.get("", response_model=list[OptimizationReportOut])
def list_reports(agent_id: str | None = Query(default=None), db: Session = Depends(get_db)):
    query = select(OptimizationReport).order_by(OptimizationReport.created_at.desc())
    if agent_id:
        query = query.where(OptimizationReport.agent_id == agent_id)
    return list(db.scalars(query))


def _get(db: Session, report_id: str) -> OptimizationReport:
    report = db.get(OptimizationReport, report_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "optimization report not found")
    return report


@router.get("/{report_id}", response_model=OptimizationReportOut)
def get_report(report_id: str, db: Session = Depends(get_db)):
    return _get(db, report_id)


@router.post(
    "/{report_id}/apply", response_model=ApplyOptimizationOut, status_code=status.HTTP_201_CREATED
)
def apply(report_id: str, body: ApplyOptimizationIn, db: Session = Depends(get_db)):
    """Create a candidate agent version with one recommendation applied (optionally with
    overrides), and optionally an experiment comparing it against the analyzed version.
    Nothing is promoted: the experiment decides."""
    report = _get(db, report_id)
    rec = next((r for r in report.recommendations if r["type"] == body.type), None)
    if rec is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"report has no '{body.type}' recommendation"
        )
    if not rec["suggested_options"] and not body.options_override:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "this recommendation has no configuration change to apply",
        )

    base = db.get(AgentVersion, report.agent_version_id)
    options = {**(base.options or {}), **rec["suggested_options"], **(body.options_override or {})}
    try:
        AgentOptions.model_validate(options)
    except ValidationError as e:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            e.errors(include_url=False, include_context=False),
        ) from e
    config = AgentConfig(
        provider=base.provider,
        model=base.model,
        system_prompt=base.system_prompt,
        temperature=base.temperature,
        max_tokens=base.max_tokens,
        tools=base.tools,
        rag_config=base.rag_config,
        eval_config=base.eval_config,
        options=options,
        metadata={
            **(base.metadata_ or {}),
            "optimization": {"report_id": report.id, "type": body.type},
        },
    )
    agent = base.agent
    version = _new_version(agent, config, agent.latest_version.version + 1)
    db.commit()

    experiment = None
    if body.experiment is not None:
        experiment = create_experiment(
            ExperimentIn(
                name=body.experiment.name or f"{agent.name}: {rec['title']}",
                description=f"From optimization report {report.id} ({body.type}).",
                dataset_id=body.experiment.dataset_id,
                baseline_agent_version_id=base.id,
                candidate_agent_version_id=version.id,
                evaluator_ids=body.experiment.evaluator_ids,
                repetitions=body.experiment.repetitions,
                acceptance_criteria=body.experiment.acceptance_criteria,
                isolate=["options"],
            ),
            db,
        )
    return ApplyOptimizationOut(
        agent_version=AgentVersionOut.model_validate(version),
        experiment=None if experiment is None else ExperimentOut.model_validate(experiment),
    )
