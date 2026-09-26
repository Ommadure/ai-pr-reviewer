"""POST /api/v1/webhooks/github: the only door GitHub knocks on.

Deliberately does almost nothing (ADR 0002): verify, dedupe, record, route,
enqueue, return. GitHub gives us 10 seconds; this takes milliseconds.
"""

import json
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import bind_context
from app.db.session import get_session
from app.github.signatures import is_valid_signature
from app.repositories import deliveries
from app.services.commands import CommandJob
from app.services.dispatch import ReviewDispatcher, get_review_dispatcher
from app.services.webhook_router import route_event

router = APIRouter(tags=["webhooks"])
log = structlog.get_logger()

MAX_BODY_BYTES = 25 * 1024 * 1024  # GitHub caps payloads at 25 MB


@router.post("/webhooks/github")
async def github_webhook(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    dispatcher: Annotated[ReviewDispatcher, Depends(get_review_dispatcher)],
) -> JSONResponse:
    body = await _read_body(request)
    delivery_id = request.headers.get("x-github-delivery")
    event = request.headers.get("x-github-event")
    bind_context(delivery_id=delivery_id, github_event=event)

    # 1. Authenticity first: nothing below runs for an unsigned request.
    secret = settings.github_webhook_secret.get_secret_value()
    if not is_valid_signature(secret, body, request.headers.get("x-hub-signature-256")):
        log.warning("webhook.invalid_signature")  # never log the body
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid signature")
    if not delivery_id or not event:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missing GitHub delivery headers")
    payload = _parse_json(body)
    action = payload.get("action") if isinstance(payload.get("action"), str) else None
    installation_id = (payload.get("installation") or {}).get("id")
    bind_context(
        action=action,
        github_installation_id=installation_id,
        repo=(payload.get("repository") or {}).get("full_name"),
        pr=(payload.get("pull_request") or payload.get("issue") or {}).get("number"),
    )

    # 2. Dedupe: GitHub may deliver the same event more than once.
    claimed = await deliveries.try_claim(
        session,
        delivery_id=delivery_id,
        event=event,
        action=action,
        installation_id=installation_id,
    )
    if not claimed:
        await session.rollback()
        log.info("webhook.duplicate")
        return JSONResponse({"status": "duplicate"}, status_code=status.HTTP_200_OK)

    # 3. Route + record, in one transaction: either all of it is saved or none.
    try:
        outcome = await route_event(session, event, payload, bot_login=settings.bot_login)
        await deliveries.set_status(
            session, delivery_id, outcome.status, ignore_reason=outcome.ignore_reason
        )
        await session.commit()
    except Exception as exc:
        await session.rollback()
        await deliveries.record_failure(
            session,
            delivery_id=delivery_id,
            event=event,
            action=action,
            installation_id=installation_id,
            error=f"{type(exc).__name__}: {exc}"[:1000],
        )
        await session.commit()
        log.exception("webhook.processing_failed")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Processing failed") from exc

    # 4. Enqueue only after commit, so the worker can read the rows we just wrote.
    try:
        for job in outcome.jobs:
            # Publishing to Redis is blocking I/O; keep it off the event loop.
            if isinstance(job, CommandJob):
                await run_in_threadpool(dispatcher.enqueue_command, job)
            else:
                await run_in_threadpool(dispatcher.enqueue_review, job)
    except Exception as exc:
        await deliveries.set_status(session, delivery_id, "failed", error="enqueue_failed")
        await session.commit()
        log.exception("webhook.enqueue_failed")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Queue unavailable") from exc

    log.info("webhook.handled", status=outcome.status, ignore_reason=outcome.ignore_reason)
    status_code = status.HTTP_200_OK if event == "ping" else status.HTTP_202_ACCEPTED
    return JSONResponse({"status": outcome.status}, status_code=status_code)


async def _read_body(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Payload too large")
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Payload too large")
    return body


def _parse_json(body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Body is not JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Body is not a JSON object")
    return payload
