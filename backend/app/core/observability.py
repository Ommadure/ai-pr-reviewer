"""Error reporting with Sentry: optional (SENTRY_DSN), and scrubbed before anything leaves.

Reviewed code is customers' private source, and requests carry credentials, so the
rule is: Sentry gets the error, the stack, and our context tags. It never gets
request bodies (webhook payloads are code), cookies, auth or signature headers,
query strings (the OAuth callback carries `code` and `state`), or stack-frame local
variables (they can hold tokens and diffs).
"""

import os
from typing import Any, Literal

import sentry_sdk
from sentry_sdk.types import Event, Hint
from structlog.contextvars import get_contextvars

from app.core.config import Settings
from app.core.logging import CONTEXT_KEYS

Component = Literal["api", "worker"]
DROP_HEADERS = frozenset({"authorization", "cookie", "x-hub-signature", "x-hub-signature-256"})


def init_sentry(settings: Settings, *, component: Component) -> bool:
    if not settings.sentry_dsn:
        return False
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.app_env,
        # Render sets RENDER_GIT_COMMIT, so each error names the deploy it came from.
        release=os.environ.get("RENDER_GIT_COMMIT") or None,
        server_name=component,
        send_default_pii=False,
        include_local_variables=False,
        max_request_body_size="never",
        traces_sample_rate=settings.sentry_traces_sample_rate,
        before_send=scrub_event,
    )
    sentry_sdk.set_tag("component", component)
    return True


def scrub_event(event: Event, hint: Hint) -> Event | None:
    request: dict[str, Any] | None = event.get("request")
    if request:
        for key in ("data", "cookies", "query_string", "env"):
            request.pop(key, None)
        headers = request.get("headers")
        if isinstance(headers, dict):
            request["headers"] = {k: v for k, v in headers.items() if k.lower() not in DROP_HEADERS}
    # Tag the event with the unit of work it happened in (same fields as the logs).
    context = get_contextvars()
    tags: dict[str, Any] = event.setdefault("tags", {})  # type: ignore[assignment]
    for key in CONTEXT_KEYS:
        if key in context:
            tags.setdefault(key, str(context[key]))
    return event
