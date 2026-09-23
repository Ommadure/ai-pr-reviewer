"""Celery tasks. Phase 0 has only `ping`, to prove the api → broker → worker path works."""

from app.workers.celery_app import celery_app


@celery_app.task(name="app.workers.tasks.ping")
def ping() -> str:
    return "pong"
