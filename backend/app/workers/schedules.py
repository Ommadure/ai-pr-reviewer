"""Celery beat schedule. Exactly one beat process may run, or every job runs N times."""

from typing import Any

from celery.schedules import crontab

BEAT_SCHEDULE: dict[str, dict[str, Any]] = {
    "mark-stuck-runs": {
        "task": "app.workers.tasks.mark_stuck_runs",
        "schedule": 10 * 60,  # seconds
    },
    "cleanup-webhook-deliveries": {
        "task": "app.workers.tasks.cleanup_webhook_deliveries",
        "schedule": crontab(hour=3, minute=17),  # daily, off the top of the hour
    },
    # poll_feedback (every 30 min) arrives in Phase 4.
}
