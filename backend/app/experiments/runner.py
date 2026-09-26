"""Runs an experiment: both arms over the same cases, interleaved, then compared."""

import logging
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.evaluation import judge
from app.evaluation.runner import (
    TERMINAL,
    evaluate_case,
    fail,
    finish,
    ordered_evaluators,
    resume_state,
    start,
)
from app.evaluation.summary import run_quality
from app.experiments.compare import Obs, compare
from app.experiments.criteria import AcceptanceCriteria
from app.leases import LeaseHeld, claim, release, renew, seconds_left
from app.models import AgentVersion, Dataset, Evaluation, EvaluationResult, Experiment, Run

logger = logging.getLogger(__name__)


def version_snapshot(v: AgentVersion) -> dict[str, Any]:
    return {
        "agent_id": v.agent_id,
        "agent_version_id": v.id,
        "version": v.version,
        "provider": v.provider,
        "model": v.model,
        "system_prompt": v.system_prompt,
        "temperature": v.temperature,
        "max_tokens": v.max_tokens,
        "tools": v.tools,
        "rag_config": v.rag_config,
        "options": v.options or {},
    }


# Fields of an agent version that define its behaviour (and so can differ between arms).
CONFIG_FIELDS = (
    "provider",
    "model",
    "system_prompt",
    "temperature",
    "max_tokens",
    "tools",
    "rag_config",
    "options",
)


def config_diff(base: dict[str, Any], cand: dict[str, Any]) -> list[str]:
    return [f for f in CONFIG_FIELDS if base.get(f) != cand.get(f)]


def config_snapshot(db: Session, exp: Experiment) -> dict[str, Any]:
    """Freeze everything that determines the experiment's outcome."""
    dataset = db.get(Dataset, exp.dataset_id)
    base = version_snapshot(db.get(AgentVersion, exp.baseline_agent_version_id))
    cand = version_snapshot(db.get(AgentVersion, exp.candidate_agent_version_id))
    return {
        "baseline": base,
        "candidate": cand,
        "config_diff": config_diff(base, cand),
        "dataset": {
            "id": dataset.id,
            "name": dataset.name,
            "version": dataset.version,
            "cases": len(dataset.cases),
        },
        "evaluators": [
            {"id": e.id, "name": e.name, "version": e.version, "type": e.type, "config": e.config}
            for e in ordered_evaluators(db, exp.evaluator_ids)
        ],
        "judge_prompt_version": judge.PROMPT_VERSION,
        "repetitions": exp.repetitions,
        "acceptance_criteria": exp.acceptance_criteria,
    }


def observation(run: Run, results: list[EvaluationResult]) -> Obs:
    q = run_quality(results)
    return Obs(
        succeeded=run.status == "succeeded",
        quality=None if q is None else q[0],
        passed=None if q is None else q[1],
        cost=run.estimated_total_cost if run.cost_status == "complete" else None,
        latency_ms=run.total_latency_ms,
        llm_calls=run.llm_call_count,
        tool_calls=run.tool_call_count,
        tool_failures=run.tool_failure_count,
        retries=run.retry_count,
        tokens=run.input_tokens
        + run.output_tokens
        + run.cache_creation_input_tokens
        + run.cache_read_input_tokens,
    )


def _arm(db: Session, exp: Experiment, arm: str, version_id: str) -> Evaluation:
    ev = Evaluation(
        agent_id=db.get(AgentVersion, version_id).agent_id,
        agent_version_id=version_id,
        dataset_id=exp.dataset_id,
        evaluator_ids=exp.evaluator_ids,
        repetitions=exp.repetitions,
        experiment_id=exp.id,
        arm=arm,
    )
    db.add(ev)
    return ev


def run_experiment(
    session_factory: sessionmaker, experiment_id: str, owner: str | None = None
) -> None:
    """Run (or resume) an experiment. Raises LeaseHeld if another execution owns it."""
    owner = owner or uuid.uuid4().hex
    ttl = get_settings().task_lease_seconds
    with session_factory() as db:
        exp = db.get(Experiment, experiment_id)
        if exp is None or exp.status in TERMINAL:
            return  # duplicate delivery of a finished task
        if not claim(db, Experiment, experiment_id, owner, ttl):
            db.refresh(exp)
            raise LeaseHeld(seconds_left(exp.lease_expires_at))
        try:
            _run_claimed(db, exp, owner, ttl)
        finally:
            release(db, Experiment, experiment_id, owner)


def _run_claimed(db: Session, exp: Experiment, owner: str, ttl: int) -> None:
    experiment_id = exp.id
    log = {"experiment_id": exp.id}
    resumed = exp.baseline_evaluation_id is not None
    exp.status = "running"
    exp.started_at = exp.started_at or datetime.now(UTC)
    if resumed:  # redelivered after a worker died: continue the same two arms
        base_ev = db.get(Evaluation, exp.baseline_evaluation_id)
        cand_ev = db.get(Evaluation, exp.candidate_evaluation_id)
    else:
        base_ev = _arm(db, exp, "baseline", exp.baseline_agent_version_id)
        cand_ev = _arm(db, exp, "candidate", exp.candidate_agent_version_id)
        db.flush()
        exp.baseline_evaluation_id, exp.candidate_evaluation_id = base_ev.id, cand_ev.id
    start(db, base_ev)
    start(db, cand_ev)
    logger.info("experiment resumed" if resumed else "experiment started", extra=log)

    try:
        arms = {
            "baseline": (base_ev, db.get(AgentVersion, exp.baseline_agent_version_id)),
            "candidate": (cand_ev, db.get(AgentVersion, exp.candidate_agent_version_id)),
        }
        evaluators = ordered_evaluators(db, exp.evaluator_ids)
        dataset = db.get(Dataset, exp.dataset_id)
        done = {arm: resume_state(db, ev, len(evaluators)) for arm, (ev, _) in arms.items()}
        collected: dict[str, dict] = {"baseline": {}, "candidate": {}}
        all_runs: dict[str, list] = defaultdict(list)
        all_results: dict[str, list] = defaultdict(list)

        for i, case in enumerate(dataset.cases):
            for rep in range(exp.repetitions):
                # Alternate which arm goes first, so warm caches or provider load
                # drift don't systematically favour one side.
                order = (
                    ["baseline", "candidate"] if (i + rep) % 2 == 0 else ["candidate", "baseline"]
                )
                for arm in order:
                    ev, version = arms[arm]
                    if (case.id, rep) in done[arm]:
                        run, results = done[arm][(case.id, rep)]
                    else:
                        run, results = evaluate_case(db, ev, version, case, evaluators, rep)
                        renew(db, Experiment, experiment_id, owner, ttl)  # heartbeat
                    collected[arm][(case.id, rep)] = observation(run, results)
                    all_runs[arm].append(run)
                    all_results[arm] += results

        for arm, (ev, _) in arms.items():
            finish(db, ev, all_runs[arm], all_results[arm], evaluators)
        keys = collected["baseline"].keys()
        pairs = [(collected["baseline"][k], collected["candidate"][k]) for k in keys]
        criteria = AcceptanceCriteria.model_validate(exp.acceptance_criteria)
        exp.comparison = _json_safe(compare(pairs, criteria))
        exp.verdict = exp.comparison["verdict"]
        exp.status = "completed"
    except Exception as e:
        logger.exception("experiment failed", extra=log)
        fail(db, base_ev.id, e)
        fail(db, cand_ev.id, e)
        exp = db.get(Experiment, experiment_id)
        exp.status = "failed"
        exp.error = f"{type(e).__name__}: {e}"
    exp.ended_at = datetime.now(UTC)
    db.commit()
    logger.info("experiment finished", extra={**log, "status": exp.status, "verdict": exp.verdict})


def _json_safe(value):
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(v) for v in value]
    if isinstance(value, float) and value != value:  # NaN
        return None
    return value
