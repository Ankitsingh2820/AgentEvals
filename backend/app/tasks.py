"""Dispatch long-running work to the configured backend.

inline: a FastAPI background task in the API process. Simple, no extra services, but
        work is lost if the process restarts. For development and tests.
celery: a durable Redis-backed queue processed by `app.worker`. Survives API restarts
        and resumes after worker crashes. For anything shared or long-running.
"""

from typing import Literal

from fastapi import BackgroundTasks
from sqlalchemy.orm import sessionmaker

from app.config import get_settings

Kind = Literal["evaluation", "experiment"]


def dispatch(
    kind: Kind, object_id: str, background: BackgroundTasks, session_factory: sessionmaker
) -> str:
    """Queue the work and return the backend used."""
    if get_settings().task_backend == "celery":
        from app.worker import celery_app

        celery_app.send_task(f"agenteval.run_{kind}", args=[object_id])
        return "celery"

    if kind == "evaluation":
        from app.evaluation.runner import run_evaluation as runner
    else:
        from app.experiments.runner import run_experiment as runner
    background.add_task(runner, session_factory, object_id)
    return "inline"
