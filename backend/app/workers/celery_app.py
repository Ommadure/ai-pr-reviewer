"""Celery application: `celery -A app.workers.celery_app worker|beat`.

The reliability settings below are the reason a review survives a worker crash;
see spec section 12 and docs/adr/0002.
"""

import ssl
from typing import Any

from celery import Celery, Task
from celery.signals import task_postrun, task_prerun

from app.core.config import get_settings
from app.core.logging import configure_logging, new_context
from app.core.observability import init_sentry
from app.workers.schedules import beat_schedule

settings = get_settings()
configure_logging(settings.app_env)
init_sentry(settings, component="worker")


@task_prerun.connect
def _task_context(task_id: str | None = None, task: Task | None = None, **_: Any) -> None:
    # A worker process runs task after task: start each one with a clean log context.
    new_context(task=task.name.rsplit(".", 1)[-1] if task else None, task_id=task_id)


@task_postrun.connect
def _clear_task_context(**_: Any) -> None:
    new_context()


def redis_tls_options(url: str) -> dict[str, Any] | None:
    """TLS settings for a `rediss://` URL (Upstash, Render external Key Value).

    Celery refuses a rediss:// URL without an explicit certificate policy; we
    require the server's certificate to verify, never CERT_NONE.
    """
    return {"ssl_cert_reqs": ssl.CERT_REQUIRED} if url.startswith("rediss://") else None


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
    # Live task events feed Flower; each one is a Redis PUBLISH, so production turns them off.
    worker_send_task_events=settings.celery_task_events,
    task_send_sent_event=settings.celery_task_events,
    # Nothing reads task return values (state lives in Postgres), so don't store them.
    task_ignore_result=True,
    # Idle queue poll: one BRPOP per interval. It returns the moment a task arrives.
    # Connection health checks (PING) scale with it: 25 s by default, 120 s at a 30 s poll.
    broker_transport_options={
        "polling_interval": settings.celery_poll_seconds,
        "health_check_interval": max(25, settings.celery_poll_seconds * 4),
    },
    broker_use_ssl=redis_tls_options(settings.redis_url),
    redis_backend_use_ssl=redis_tls_options(settings.redis_url),
    broker_connection_retry_on_startup=True,
    beat_schedule=beat_schedule(settings.keep_warm_url),
)
