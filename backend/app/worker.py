"""Celery worker for long-running evaluations and experiments.

    celery -A app.worker worker --loglevel=INFO --concurrency=2

Delivery is at-least-once: tasks are acknowledged only after they finish
(acks_late). If a worker dies, Redis redelivers its task after the visibility timeout.
The runners are written for that:
- a lease (app/leases.py) ensures one execution per evaluation/experiment at a time; a
  duplicate delivery re-queues itself until the lease lapses,
- a finished evaluation/experiment is skipped,
- an interrupted one resumes where it stopped (evaluation.runner.resume_state).
"""

import os
import uuid

from celery import Celery
from celery.signals import worker_process_init, worker_ready

from app.config import get_settings

settings = get_settings()

celery_app = Celery("agenteval", broker=settings.redis_url or "redis://localhost:6379/0")
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,  # long tasks: don't reserve work another worker could do
    task_ignore_result=True,  # results live in PostgreSQL, not the broker
    broker_connection_retry_on_startup=True,
    # Logging: pool processes use our JSON logger (configured in _init_worker). Celery must
    # not hijack the root logger or redirect stdout into its logging proxy, or those lines
    # never reach the container output.
    worker_hijack_root_logger=False,
    worker_redirect_stdouts=False,
    # How long Redis waits before redelivering a task whose worker died. Leases make early
    # redelivery harmless (a duplicate backs off), so this bounds crash recovery time.
    broker_transport_options={"visibility_timeout": settings.task_visibility_timeout_seconds},
)


@worker_process_init.connect
def _init_worker(**_):
    from app import db
    from app.logging_config import configure_logging
    from app.telemetry import setup_telemetry

    # A forked pool process must not reuse the parent's pooled DB connections (the
    # recovery sweeper opens some in the parent); drop inherited ones without closing them.
    db.engine.dispose(close=False)
    configure_logging(settings.log_level)
    setup_telemetry()


@worker_ready.connect
def _start_recovery_sweep(**_):
    """Periodically re-queue work whose worker died (see app/recovery.py)."""
    import threading

    from app import db
    from app.recovery import requeue_abandoned

    def sweep():
        while True:
            try:
                with db.SessionLocal() as session:
                    requeue_abandoned(
                        session, lambda name, args: celery_app.send_task(name, args=args)
                    )
            except Exception:  # never let the sweeper die; try again next interval
                import logging

                logging.getLogger(__name__).exception("recovery sweep failed")
            stop.wait(settings.task_recovery_interval_seconds)

    stop = threading.Event()
    threading.Thread(target=sweep, name="agenteval-recovery", daemon=True).start()


def _run_leased(task, runner, object_id: str) -> None:
    """Run under a lease; if another execution holds it, re-queue for when it lapses."""
    from app import db
    from app.leases import LeaseHeld

    # The lease owner must be unique per *execution*: a redelivered message keeps the
    # original task id, so using that id would let a duplicate share the live lease.
    owner = f"{task.request.hostname}:{os.getpid()}:{uuid.uuid4().hex[:12]}"
    try:
        runner(db.SessionLocal, object_id, owner=owner)
    except LeaseHeld as held:
        raise task.retry(countdown=max(5, round(held.retry_in) + 1), max_retries=None) from held


@celery_app.task(bind=True, name="agenteval.run_evaluation")
def run_evaluation_task(self, evaluation_id: str) -> None:
    from app.evaluation.runner import run_evaluation

    _run_leased(self, run_evaluation, evaluation_id)


@celery_app.task(bind=True, name="agenteval.run_experiment")
def run_experiment_task(self, experiment_id: str) -> None:
    from app.experiments.runner import run_experiment

    _run_leased(self, run_experiment, experiment_id)
