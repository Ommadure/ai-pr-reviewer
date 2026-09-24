"""The LLM seam: everything the engine knows about language models.

Providers implement one method, `complete()`: messages in, text and token usage
out. Parsing and validating the JSON happens once, here, for every provider
(ADR 0003): vendor JSON modes are an optimisation we never rely on.
"""

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import BaseModel, ValidationError

Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True)
class Message:
    role: Role
    content: str


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class Completion:
    text: str
    usage: TokenUsage
    latency_ms: int
    model: str


@dataclass(frozen=True)
class LLMResult[T: BaseModel]:
    output: T
    usage: TokenUsage
    latency_ms: int
    model: str


class LLMError(Exception):
    """A call failed. `usage` is set when tokens were still spent (and billed)."""

    def __init__(
        self, message: str, *, usage: TokenUsage | None = None, latency_ms: int = 0
    ) -> None:
        super().__init__(message)
        self.usage = usage or TokenUsage()
        self.latency_ms = latency_ms


class LLMRateLimited(LLMError):
    def __init__(self, message: str, *, retry_after: float) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class LLMInvalidOutput(LLMError):
    """The model answered, but not with JSON matching the schema."""

    def __init__(self, problem: str, *, raw_text: str, usage: TokenUsage, latency_ms: int) -> None:
        super().__init__(f"invalid output: {problem}", usage=usage, latency_ms=latency_ms)
        self.problem = problem
        self.raw_text = raw_text


class LLMProvider(Protocol):
    name: str

    async def generate_structured[T: BaseModel](
        self, messages: list[Message], schema: type[T], *, model: str, temperature: float
    ) -> LLMResult[T]: ...


class BaseProvider(ABC):
    """Shared parse-and-validate; subclasses only implement complete()."""

    name: str = "base"

    @abstractmethod
    async def complete(
        self, messages: list[Message], *, model: str, temperature: float
    ) -> Completion: ...

    async def generate_structured[T: BaseModel](
        self, messages: list[Message], schema: type[T], *, model: str, temperature: float
    ) -> LLMResult[T]:
        completion = await self.complete(messages, model=model, temperature=temperature)
        try:
            output = parse_json_output(completion.text, schema)
        except ValueError as exc:
            raise LLMInvalidOutput(
                str(exc),
                raw_text=completion.text,
                usage=completion.usage,
                latency_ms=completion.latency_ms,
            ) from exc
        return LLMResult(output, completion.usage, completion.latency_ms, completion.model)


FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def parse_json_output[T: BaseModel](text: str, schema: type[T]) -> T:
    """Lenient about wrapping (code fences, stray prose), strict about content."""
    candidate = text.strip()
    fenced = FENCE.match(candidate)
    if fenced:
        candidate = fenced.group(1)
    if not candidate.startswith("{"):
        start, end = candidate.find("{"), candidate.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("response contains no JSON object")
        candidate = candidate[start : end + 1]
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed JSON ({exc.msg} at line {exc.lineno})") from exc
    try:
        return schema.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or '<root>'}: {error['msg']}"
            for error in exc.errors()[:10]
        )
        raise ValueError(f"schema mismatch: {problems}") from exc
