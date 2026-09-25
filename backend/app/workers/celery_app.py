"""Celery application: `celery -A app.workers.celery_app worker|beat`.

The reliability settings below are the reason a review survives a worker crash;
see spec section 12 and docs/adr/0002.
"""

from celery import Celery

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.workers.schedules import BEAT_SCHEDULE

settings = get_settings()
configure_logging(settings.app_env)

celery_app = Celery(
    "reviewpilot",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    # Acknowledge a message only after the task finishes, so if the worker dies
    # mid-review the broker redelivers it instead of silently losing it.
    task_acks_late=True,
    # ...and if the worker process is killed (OOM, deploy), requeue the task.
    task_reject_on_worker_lost=True,
    # Take one task at a time: reviews are long, and prefetching would let one
    # worker hoard tasks that an idle worker could be running.
    worker_prefetch_multiplier=1,
    task_soft_time_limit=240,
    task_time_limit=300,
    task_default_queue="default",
    # Celery captures print()/stdout into its own logger at WARNING by default, which
    # made every structlog info line look like a warning.
    worker_redirect_stdouts_level="INFO",
    task_routes={
        "app.workers.tasks.review_*": {"queue": "reviews"},
        "app.workers.tasks.poll_feedback": {"queue": "feedback"},
    },
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    result_expires=60 * 60 * 24,
    timezone="UTC",
    enable_utc=True,
    worker_send_task_events=True,  # lets Flower show live task state
    task_send_sent_event=True,
    broker_connection_retry_on_startup=True,
    beat_schedule=BEAT_SCHEDULE,
)
