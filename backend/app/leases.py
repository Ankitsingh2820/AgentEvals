"""Leases: at most one task execution works on an evaluation/experiment at a time.

Celery delivers at least once. After a worker dies, Redis redelivers its unacknowledged
task once the visibility timeout passes, and it can also redeliver a task that is still
running if it outlives that timeout. A lease makes both cases safe:

- claim:  atomic UPDATE ... WHERE lease is free, expired, or already ours
- renew:  after every case (a heartbeat); the lease expires only if the owner stops
- release when the work finishes

A delivery that cannot claim the lease is not lost: the worker re-queues it for when the
lease would expire. If the owner is still alive it will have finished (and the retry is a
no-op); if it died, the retry takes over and resumes.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, update
from sqlalchemy.orm import Session


class LeaseHeld(Exception):
    def __init__(self, retry_in: float):
        super().__init__(f"leased by another worker; retry in {retry_in:.0f}s")
        self.retry_in = retry_in


def _now() -> datetime:
    return datetime.now(UTC)


def claim(db: Session, model, object_id: str, owner: str, ttl_seconds: int) -> bool:
    now = _now()
    result = db.execute(
        update(model)
        .where(
            model.id == object_id,
            or_(
                model.lease_expires_at.is_(None),
                model.lease_expires_at < now,
                model.lease_owner == owner,
            ),
        )
        .values(lease_owner=owner, lease_expires_at=now + timedelta(seconds=ttl_seconds))
        .execution_options(synchronize_session=False)
    )
    db.commit()
    return result.rowcount == 1


def renew(db: Session, model, object_id: str, owner: str, ttl_seconds: int) -> None:
    db.execute(
        update(model)
        .where(model.id == object_id, model.lease_owner == owner)
        .values(lease_expires_at=_now() + timedelta(seconds=ttl_seconds))
        .execution_options(synchronize_session=False)
    )
    db.commit()


def release(db: Session, model, object_id: str, owner: str) -> None:
    db.execute(
        update(model)
        .where(model.id == object_id, model.lease_owner == owner)
        .values(lease_owner=None, lease_expires_at=None)
        .execution_options(synchronize_session=False)
    )
    db.commit()


def seconds_left(expires_at: datetime | None) -> float:
    if expires_at is None:
        return 0.0
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return max((expires_at - _now()).total_seconds(), 0.0)
