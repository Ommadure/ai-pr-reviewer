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
    "poll-feedback": {
        "task": "app.workers.tasks.poll_feedback",
        "schedule": 30 * 60,
    },
}


# Render's free web service sleeps after 15 idle minutes and takes ~1 minute to wake,
# longer than GitHub waits for a webhook (10 s). The always-on worker pings it first.
KEEP_WARM_SECONDS = 10 * 60


def beat_schedule(keep_warm_url: str = "") -> dict[str, dict[str, Any]]:
    schedule = dict(BEAT_SCHEDULE)
    if keep_warm_url:
        schedule["keep-api-warm"] = {
            "task": "app.workers.tasks.keep_api_warm",
            "schedule": KEEP_WARM_SECONDS,
            "args": (keep_warm_url,),
        }
    return schedule
