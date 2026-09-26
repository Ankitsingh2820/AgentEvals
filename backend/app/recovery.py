"""Find and re-queue work abandoned by a dead worker.

The broker also redelivers unacknowledged tasks, but on its own schedule: Redis/kombu
restores them only after the visibility timeout, and checks roughly every 100 s. This
sweep bounds recovery by the lease instead. Anything `running` whose lease has lapsed
(its owner stopped renewing it) is re-queued. Leases make duplicate messages harmless: a
second delivery either backs off or finds the work finished.
"""

import logging
from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Evaluation, Experiment

logger = logging.getLogger(__name__)


def find_abandoned(db: Session) -> list[tuple[str, str]]:
    """(kind, id) of running evaluations/experiments whose lease has lapsed."""
    now = datetime.now(UTC)
    lapsed = [Experiment.lease_expires_at.is_(None), Experiment.lease_expires_at < now]
    experiments = db.scalars(
        select(Experiment.id).where(Experiment.status == "running", or_(*lapsed))
    )
    evaluations = db.scalars(
        select(Evaluation.id).where(
            Evaluation.status == "running",
            # An experiment's arms are driven by the experiment itself, never on their own.
            Evaluation.experiment_id.is_(None),
            or_(Evaluation.lease_expires_at.is_(None), Evaluation.lease_expires_at < now),
        )
    )
    return [("experiment", i) for i in experiments] + [("evaluation", i) for i in evaluations]


def requeue_abandoned(db: Session, send) -> int:
    """Re-queue abandoned work with `send(task_name, [id])`. Returns how many."""
    found = find_abandoned(db)
    for kind, object_id in found:
        logger.warning("re-queueing abandoned work", extra={"kind": kind, "id": object_id})
        send(f"agenteval.run_{kind}", [object_id])
    return len(found)
