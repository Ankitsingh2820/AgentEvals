from collections import defaultdict
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.agents import get_agent_or_404
from app.db import get_db
from app.evaluation.summary import run_quality
from app.metrics.stats import percentile, rate, summarize
from app.models import EvaluationResult, Run
from app.schemas import AgentMetricsOut, OverviewKpis, OverviewOut, TrendPoint

router = APIRouter(prefix="/metrics", tags=["metrics"])


@router.get("/agents/{agent_id}", response_model=AgentMetricsOut)
def agent_metrics(
    agent_id: str,
    agent_version_id: str | None = None,
    since: datetime | None = None,
    db: Session = Depends(get_db),
) -> AgentMetricsOut:
    """Aggregate metrics over an agent's finished runs.

    - Latency statistics use succeeded runs only: a run that fails fast would otherwise
      make the agent look faster than it is.
    - Cost statistics use every finished run whose cost is complete. Failed runs are
      still real spend; runs with partial/unpriced cost are counted in `runs_without_cost`.
    """
    get_agent_or_404(db, agent_id)
    query = select(Run).where(Run.agent_id == agent_id, Run.status != "running")
    if agent_version_id:
        query = query.where(Run.agent_version_id == agent_version_id)
    if since:
        query = query.where(Run.started_at >= since)
    runs = list(db.scalars(query))

    succeeded = [r for r in runs if r.status == "succeeded"]
    costed = [r for r in runs if r.cost_status == "complete"]
    tool_calls = sum(r.tool_call_count for r in runs)

    return AgentMetricsOut(
        agent_id=agent_id,
        agent_version_id=agent_version_id,
        runs=len(runs),
        succeeded=len(succeeded),
        failed=len(runs) - len(succeeded),
        success_rate=rate(len(succeeded), len(runs)),
        latency_ms=summarize([r.total_latency_ms for r in succeeded]),
        llm_latency_ms=summarize([r.llm_latency_ms for r in succeeded]),
        tool_latency_ms=summarize([r.tool_latency_ms for r in succeeded]),
        estimated_cost=summarize([r.estimated_total_cost for r in costed]),
        estimated_total_spend=sum((r.estimated_total_cost for r in costed), start=0),
        runs_without_cost=len(runs) - len(costed),
        input_tokens=summarize([r.input_tokens for r in runs]),
        output_tokens=summarize([r.output_tokens for r in runs]),
        llm_calls=summarize([r.llm_call_count for r in runs]),
        tool_calls=summarize([r.tool_call_count for r in runs]),
        tool_failure_rate=rate(sum(r.tool_failure_count for r in runs), tool_calls),
    )


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _kpis(runs: list[Run], quality: dict[str, float]) -> OverviewKpis:
    ok = [r for r in runs if r.status == "succeeded"]
    costed = [float(r.estimated_total_cost) for r in runs if r.cost_status == "complete"]
    lat = [r.total_latency_ms for r in ok]
    q = [quality[r.id] for r in runs if r.id in quality]
    return OverviewKpis(
        runs=len(runs),
        success_rate=rate(len(ok), len(runs)),
        avg_cost=sum(costed) / len(costed) if costed else None,
        runs_with_cost=len(costed),
        avg_latency_ms=sum(lat) / len(lat) if lat else None,
        p50_latency_ms=percentile(lat, 50) if lat else None,
        p95_latency_ms=percentile(lat, 95) if lat else None,
        avg_quality=sum(q) / len(q) if q else None,
        runs_with_quality=len(q),
    )


@router.get("/overview", response_model=OverviewOut)
def overview(
    days: int = Query(30, ge=1, le=365),
    agent_id: str | None = None,
    db: Session = Depends(get_db),
) -> OverviewOut:
    """Dashboard headline numbers for the last `days` days, the same numbers for the
    preceding period (for deltas), and a daily trend.

    Quality is the mean evaluator score of runs that were evaluated; unevaluated runs
    have no quality and are excluded from it (see `runs_with_quality`)."""
    now = datetime.now(UTC)
    start, prev_start = now - timedelta(days=days), now - timedelta(days=2 * days)
    query = select(Run).where(Run.status != "running", Run.started_at >= prev_start)
    if agent_id:
        get_agent_or_404(db, agent_id)
        query = query.where(Run.agent_id == agent_id)
    runs = list(db.scalars(query))

    by_run = defaultdict(list)
    ids = [r.id for r in runs]
    for i in range(0, len(ids), 1000):  # stay under DB parameter limits
        for res in db.scalars(
            select(EvaluationResult).where(EvaluationResult.run_id.in_(ids[i : i + 1000]))
        ):
            by_run[res.run_id].append(res)
    quality = {rid: q[0] for rid, rs in by_run.items() if (q := run_quality(rs))}

    current = [r for r in runs if _as_utc(r.started_at) >= start]
    previous = [r for r in runs if _as_utc(r.started_at) < start]

    by_day = defaultdict(list)
    for r in current:
        by_day[_as_utc(r.started_at).date()].append(r)
    trend = []
    for i in range(days, -1, -1):
        day = (now - timedelta(days=i)).date()
        k = _kpis(by_day.get(day, []), quality)
        trend.append(
            TrendPoint(
                date=day,
                runs=k.runs,
                success_rate=k.success_rate,
                avg_cost=k.avg_cost,
                p50_latency_ms=k.p50_latency_ms,
                p95_latency_ms=k.p95_latency_ms,
                avg_quality=k.avg_quality,
            )
        )
    return OverviewOut(
        days=days,
        agent_id=agent_id,
        start=start,
        end=now,
        kpis=_kpis(current, quality),
        previous=_kpis(previous, quality),
        trend=trend,
    )
