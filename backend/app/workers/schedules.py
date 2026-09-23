"""Celery beat schedule. Filled in later: mark_stuck_runs (Phase 3),
poll_feedback (Phase 4), cleanup_webhook_deliveries (Phase 3).

Exactly one beat process may run, or every scheduled job runs N times.
"""

from typing import Any

BEAT_SCHEDULE: dict[str, dict[str, Any]] = {}
