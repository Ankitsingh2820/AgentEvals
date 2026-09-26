"""Runs an evaluation: the agent version on every dataset case, then every evaluator on
every run. Designed to run outside the request (FastAPI background task today, a worker
queue later), so it opens its own session from a session factory."""

import logging
import time
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.costs.calculator import MissingPriceError, llm_cost
from app.costs.pricing import find_model_pricing, to_prices
from app.engine.executor import execute_run
from app.evaluation.base import EvalInput, ToolCallView, Verdict
from app.evaluation.evaluators import evaluate
from app.evaluation.summary import build_summary
from app.leases import LeaseHeld, claim, release, renew, seconds_left
from app.models import (
    AgentVersion,
    Dataset,
    DatasetCase,
    Evaluation,
    EvaluationResult,
    Evaluator,
    Run,
)

logger = logging.getLogger(__name__)


def eval_input(run: Run, case) -> EvalInput:
    return EvalInput(
        case_input=case.input,
        expected_output=case.expected_output,
        labels=case.labels or {},
        output=run.final_output,
        run_status=run.status,
        run_error=run.error,
        tool_calls=[
            ToolCallView(
                s.tool_call.tool_name,
                s.tool_call.arguments,
                s.tool_call.result,
                s.tool_call.success,
            )
            for s in run.steps
            if s.tool_call is not None
        ],
    )


def _judge_cost(db: Session, verdict: Verdict):
    j = verdict.judge
    if j is None or (j.input_tokens + j.output_tokens) == 0:
        return None
    pricing = find_model_pricing(db, j.provider, j.model, datetime.now(UTC).date())
    if pricing is None:
        return None
    try:
        return llm_cost(
            to_prices(pricing),
            j.input_tokens,
            j.output_tokens,
            j.cache_creation_input_tokens,
            j.cache_read_input_tokens,
        ).total
    except MissingPriceError:
        return None


def _result(
    db: Session,
    evaluation: Evaluation,
    run: Run,
    evaluator: Evaluator,
    verdict: Verdict,
    latency_ms: float,
) -> EvaluationResult:
    j = verdict.judge
    return EvaluationResult(
        evaluation_id=evaluation.id,
        run_id=run.id,
        dataset_case_id=run.dataset_case_id,
        evaluator_id=evaluator.id,
        status=verdict.status,
        score=verdict.score,
        passed=verdict.passed,
        reason=verdict.reason,
        details={**verdict.details, **({"judge_raw_response": j.raw_response} if j else {})},
        latency_ms=latency_ms,
        judge_provider=j.provider if j else None,
        judge_model=j.model if j else None,
        judge_prompt_version=j.prompt_version if j else None,
        judge_prompt_sha256=j.prompt_sha256 if j else None,
        judge_input_tokens=j.input_tokens if j else 0,
        judge_output_tokens=j.output_tokens if j else 0,
        judge_estimated_cost=_judge_cost(db, verdict),
    )


def ordered_evaluators(db: Session, ids: list[str]) -> list[Evaluator]:
    found = {e.id: e for e in db.scalars(select(Evaluator).where(Evaluator.id.in_(ids)))}
    return [found[i] for i in ids]


def evaluate_case(
    db: Session,
    evaluation: Evaluation,
    version: AgentVersion,
    case: DatasetCase,
    evaluators: list[Evaluator],
    repetition: int = 0,
) -> tuple[Run, list[EvaluationResult]]:
    """Run the agent once on one case, score it with every evaluator, and commit."""
    run = execute_run(
        db,
        version,
        case.input,
        experiment_id=evaluation.experiment_id,
        evaluation_id=evaluation.id,
        dataset_case_id=case.id,
        repetition=repetition,
    )
    x = eval_input(run, case)
    results = []
    for evaluator in evaluators:
        t0 = time.perf_counter()
        verdict = evaluate(evaluator, x)
        latency = (time.perf_counter() - t0) * 1000
        result = _result(db, evaluation, run, evaluator, verdict, latency)
        db.add(result)
        results.append(result)
    db.commit()  # persist per case, so progress survives a later failure
    return run, results


TERMINAL = ("completed", "failed")


def start(db: Session, evaluation: Evaluation) -> None:
    evaluation.status = "running"
    evaluation.started_at = evaluation.started_at or datetime.now(UTC)
    db.commit()


def resume_state(
    db: Session, evaluation: Evaluation, n_evaluators: int
) -> dict[tuple[str, int], tuple[Run, list[EvaluationResult]]]:
    """Work already finished for this evaluation, keyed by (case id, repetition).

    Makes the runners safe to re-deliver: if a worker died mid-evaluation, the task runs
    again and only the missing cases are executed. A run that was cut off (still
    'running', or without all its verdicts) is detached from the evaluation - it did
    happen and cost money, so it stays as a standalone run - and its case runs again.
    """
    done: dict[tuple[str, int], tuple[Run, list[EvaluationResult]]] = {}
    runs = list(db.scalars(select(Run).where(Run.evaluation_id == evaluation.id)))
    if not runs:
        return done
    results: dict[str, list[EvaluationResult]] = {}
    for r in db.scalars(
        select(EvaluationResult).where(EvaluationResult.evaluation_id == evaluation.id)
    ):
        results.setdefault(r.run_id, []).append(r)
    for run in runs:
        rs = results.get(run.id, [])
        key = (run.dataset_case_id, run.repetition)
        if run.status != "running" and len(rs) == n_evaluators and key not in done:
            done[key] = (run, rs)
            continue
        for r in rs:
            db.delete(r)
        if run.status == "running":
            run.status = "failed"
            run.error = "interrupted: worker stopped mid-run"
        run.evaluation_id = None
        run.experiment_id = None
    db.commit()
    return done


def finish(db: Session, evaluation: Evaluation, runs, results, evaluators) -> None:
    evaluation.summary = build_summary(runs, results, evaluators)
    evaluation.status = "completed"
    evaluation.ended_at = datetime.now(UTC)


def fail(db: Session, evaluation_id: str, error: Exception) -> Evaluation:
    db.rollback()
    evaluation = db.get(Evaluation, evaluation_id)
    evaluation.status = "failed"
    evaluation.error = f"{type(error).__name__}: {error}"
    evaluation.ended_at = datetime.now(UTC)
    return evaluation


def run_evaluation(
    session_factory: sessionmaker, evaluation_id: str, owner: str | None = None
) -> None:
    """Run (or resume) an evaluation. Raises LeaseHeld if another execution owns it."""
    owner = owner or uuid.uuid4().hex
    ttl = get_settings().task_lease_seconds
    with session_factory() as db:
        evaluation = db.get(Evaluation, evaluation_id)
        if evaluation is None or evaluation.status in TERMINAL:
            return  # duplicate delivery of a finished task
        if not claim(db, Evaluation, evaluation_id, owner, ttl):
            db.refresh(evaluation)
            raise LeaseHeld(seconds_left(evaluation.lease_expires_at))
        try:
            _run_claimed(db, evaluation, owner, ttl)
        finally:
            release(db, Evaluation, evaluation_id, owner)


def _run_claimed(db: Session, evaluation: Evaluation, owner: str, ttl: int) -> None:
    evaluation_id = evaluation.id
    log = {"evaluation_id": evaluation.id}
    resumed = evaluation.status == "running"
    start(db, evaluation)
    logger.info("evaluation resumed" if resumed else "evaluation started", extra=log)

    try:
        version = db.get(AgentVersion, evaluation.agent_version_id)
        dataset = db.get(Dataset, evaluation.dataset_id)
        evaluators = ordered_evaluators(db, evaluation.evaluator_ids)
        done = resume_state(db, evaluation, len(evaluators))
        runs, results = [], []
        for case in dataset.cases:
            for rep in range(evaluation.repetitions):
                if (case.id, rep) in done:
                    run, rs = done[(case.id, rep)]
                else:
                    run, rs = evaluate_case(db, evaluation, version, case, evaluators, rep)
                    renew(db, Evaluation, evaluation_id, owner, ttl)  # heartbeat
                runs.append(run)
                results += rs
        finish(db, evaluation, runs, results, evaluators)
    except Exception as e:
        logger.exception("evaluation failed", extra=log)
        evaluation = fail(db, evaluation_id, e)
    db.commit()
    logger.info("evaluation finished", extra={**log, "status": evaluation.status})
