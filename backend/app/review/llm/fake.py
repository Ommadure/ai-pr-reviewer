"""Scripted LLM for tests and offline demos. Never touches the network."""

import json
from collections.abc import Callable, Sequence

from app.review.llm.base import BaseProvider, Completion, LLMError, Message, TokenUsage
from app.review.tokens import estimate_tokens

Response = str | Exception
Responder = Callable[[list[Message]], Response]

EMPTY_REVIEW = json.dumps({"comments": [], "file_summaries": []})
EMPTY_SUMMARY = json.dumps(
    {
        "overview": "(fake provider: no real review)",
        "risk_level": "low",
        "key_changes": [],
        "notes": [],
    }
)


class FakeLLMProvider(BaseProvider):
    """Answers from a script (in call order) or from a responder function.

    Script items can be raw text (valid JSON, broken JSON, anything) or an
    exception to raise, so tests can drive every failure path.
    """

    name = "fake"

    def __init__(
        self,
        script: Sequence[Response] = (),
        *,
        responder: Responder | None = None,
        default: Response | None = None,
    ) -> None:
        self._script = list(script)
        self._responder = responder
        self._default = default
        self.calls: list[list[Message]] = []

    async def complete(
        self,
        messages: list[Message],
        *,
        model: str,
        temperature: float,
        max_output_tokens: int | None = None,
    ) -> Completion:
        self.calls.append(messages)
        if self._script:
            response = self._script.pop(0)
        elif self._responder:
            response = self._responder(messages)
        elif self._default is not None:
            response = self._default
        else:
            # Offline demo mode: a valid empty answer for whichever prompt this is.
            response = EMPTY_SUMMARY if "<review_notes>" in messages[-1].content else EMPTY_REVIEW
        if isinstance(response, Exception):
            raise response
        if not isinstance(response, str):
            raise LLMError("fake responder returned a non-string")
        prompt = "\n".join(message.content for message in messages)
        usage = TokenUsage(estimate_tokens(prompt), estimate_tokens(response))
        return Completion(response, usage, latency_ms=1, model=model)
