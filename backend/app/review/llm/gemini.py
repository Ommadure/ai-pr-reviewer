"""Google Gemini via its REST API (generateContent).

Plain httpx instead of the Google SDK: one less dependency, and respx can mock
it in tests like the GitHub client. Reference:
https://ai.google.dev/api/generate-content
"""

import asyncio
import random
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import structlog

from app.review.llm.base import (
    BaseProvider,
    Completion,
    LLMError,
    LLMRateLimited,
    Message,
    TokenUsage,
)

log = structlog.get_logger()
Sleep = Callable[[float], Awaitable[None]]
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta"
MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
DURATION = re.compile(r"^(\d+(?:\.\d+)?)s$")


class GeminiProvider(BaseProvider):
    name = "gemini"

    def __init__(
        self,
        api_key: str,
        http: httpx.AsyncClient,
        *,
        base_url: str = GEMINI_API_URL,
        timeout: float = 120.0,
        max_retries: int = 3,
        max_inline_wait: float = 30.0,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if not api_key:
            raise ValueError("GEMINI_API_KEY is not set")
        self._api_key = api_key
        self._http = http
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries
        self._max_inline_wait = max_inline_wait
        self._sleep = sleep

    async def complete(
        self, messages: list[Message], *, model: str, temperature: float
    ) -> Completion:
        if not MODEL_NAME.fullmatch(model):
            raise LLMError(f"invalid model name {model!r}")
        url = f"{self._base_url}/models/{model}:generateContent"
        body = _request_body(messages, temperature)
        # The key goes in a header, never the URL, so it can't end up in access logs.
        headers = {"x-goog-api-key": self._api_key}

        # Latency covers the whole call, retries and waits included: that's the time
        # the review actually spent here, and what the dashboard should show.
        started = time.monotonic()
        attempt = 0
        while True:
            try:
                response = await self._http.post(
                    url, json=body, headers=headers, timeout=self._timeout
                )
            except httpx.TransportError as exc:
                if attempt >= self._max_retries:
                    raise LLMError(
                        f"Gemini unreachable: {type(exc).__name__}", latency_ms=_since(started)
                    ) from exc
                await self._retry(_backoff(attempt), model, attempt, type(exc).__name__)
                attempt += 1
                continue

            if response.status_code == 429:
                wait = _retry_delay(response) or _backoff(attempt)
                if wait <= self._max_inline_wait and attempt < self._max_retries:
                    await self._retry(wait, model, attempt, "429 rate limited")
                    attempt += 1
                    continue
                raise LLMRateLimited("Gemini rate limit exceeded", retry_after=wait)
            if response.status_code >= 500 and attempt < self._max_retries:
                await self._retry(_backoff(attempt), model, attempt, str(response.status_code))
                attempt += 1
                continue
            if response.is_error:
                raise LLMError(
                    f"Gemini {response.status_code}: {_error_message(response)}",
                    latency_ms=_since(started),
                )
            return _parse_completion(response.json(), model, _since(started))

    async def _retry(self, wait: float, model: str, attempt: int, reason: str) -> None:
        log.warning(
            "llm.retry",
            provider=self.name,
            model=model,
            attempt=attempt + 1,
            reason=reason,
            wait_s=round(wait, 1),
        )
        await self._sleep(wait)


def _since(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _request_body(messages: list[Message], temperature: float) -> dict[str, Any]:
    system = "\n\n".join(m.content for m in messages if m.role == "system")
    contents = [
        {"role": "model" if m.role == "assistant" else "user", "parts": [{"text": m.content}]}
        for m in messages
        if m.role != "system"
    ]
    body: dict[str, Any] = {
        "contents": contents,
        "generationConfig": {"temperature": temperature, "responseMimeType": "application/json"},
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    return body


def _parse_completion(data: dict[str, Any], model: str, latency_ms: int) -> Completion:
    metadata = data.get("usageMetadata") or {}
    usage = TokenUsage(
        input_tokens=int(metadata.get("promptTokenCount", 0)),
        # "Thinking" tokens are billed as output tokens.
        output_tokens=int(metadata.get("candidatesTokenCount", 0))
        + int(metadata.get("thoughtsTokenCount", 0)),
    )
    candidates = data.get("candidates") or []
    if not candidates:
        reason = (data.get("promptFeedback") or {}).get("blockReason", "unknown")
        raise LLMError(f"Gemini returned no answer (blockReason={reason})", usage=usage)
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(part.get("text", "") for part in parts if not part.get("thought"))
    return Completion(text, usage, latency_ms, str(data.get("modelVersion") or model))


def _retry_delay(response: httpx.Response) -> float | None:
    if "retry-after" in response.headers:
        try:
            return float(response.headers["retry-after"])
        except ValueError:
            pass
    try:
        details = response.json().get("error", {}).get("details", [])
    except ValueError:
        return None
    for detail in details:
        if str(detail.get("@type", "")).endswith("RetryInfo"):
            match = DURATION.match(str(detail.get("retryDelay", "")))
            if match:
                return float(match.group(1))
    return None


def _error_message(response: httpx.Response) -> str:
    try:
        return str(response.json().get("error", {}).get("message", response.text))[:300]
    except ValueError:
        return response.text[:300]


def _backoff(attempt: int) -> float:
    # ~2s, 4s, 8s (+ jitter): Gemini's "high demand" 503s usually clear within seconds,
    # but not within the first one.
    return 2.0 * 2.0**attempt + random.uniform(0, 1)  # noqa: S311 (not crypto)
