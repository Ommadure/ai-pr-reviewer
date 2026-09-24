import json

import httpx
import pytest
import respx

from app.review.llm.base import (
    LLMError,
    LLMInvalidOutput,
    LLMRateLimited,
    Message,
    parse_json_output,
)
from app.review.llm.fake import FakeLLMProvider
from app.review.llm.gemini import GEMINI_API_URL, GeminiProvider
from app.review.models import FileReviewOutput

MODEL = "gemini-test-flash"
URL = f"{GEMINI_API_URL}/models/{MODEL}:generateContent"
VALID = {"comments": [], "file_summaries": [{"path": "a.py", "summary": "Adds x."}]}


# ---- output parsing (shared by every provider) ----


@pytest.mark.parametrize(
    "text",
    [
        json.dumps(VALID),
        f"```json\n{json.dumps(VALID)}\n```",
        f"Here is the review:\n{json.dumps(VALID)}\nThanks!",
    ],
)
def test_parse_tolerates_wrapping(text: str) -> None:
    assert parse_json_output(text, FileReviewOutput).file_summaries[0].path == "a.py"


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("no json here", "no JSON object"),
        ('{"comments": [}', "malformed JSON"),
        ('{"comments": [{"path": "a.py"}]}', "schema mismatch"),
    ],
)
def test_parse_reports_the_problem(text: str, problem: str) -> None:
    with pytest.raises(ValueError, match=problem):
        parse_json_output(text, FileReviewOutput)


async def test_fake_provider_raises_invalid_output_with_raw_text() -> None:
    fake = FakeLLMProvider(["not json"])
    with pytest.raises(LLMInvalidOutput) as exc_info:
        await fake.generate_structured([], FileReviewOutput, model="m", temperature=0)
    assert exc_info.value.raw_text == "not json"
    assert exc_info.value.usage.output_tokens > 0  # tokens were still spent


# ---- Gemini (REST, mocked) ----


class FakeSleep:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def _gemini_response(text: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "candidates": [{"content": {"role": "model", "parts": [{"text": text}]}}],
            "usageMetadata": {
                "promptTokenCount": 1200,
                "candidatesTokenCount": 80,
                "thoughtsTokenCount": 40,
            },
            "modelVersion": f"{MODEL}-001",
        },
    )


@pytest.fixture
def sleep() -> FakeSleep:
    return FakeSleep()


@pytest.fixture
async def gemini(sleep: FakeSleep) -> GeminiProvider:
    return GeminiProvider("test-key", httpx.AsyncClient(), sleep=sleep)


MESSAGES = [
    Message("system", "You review code."),
    Message("user", "Review this."),
    Message("assistant", "{bad"),
    Message("user", "Fix it."),
]


@respx.mock
async def test_gemini_request_shape_and_usage(gemini: GeminiProvider) -> None:
    route = respx.post(URL).mock(return_value=_gemini_response(json.dumps(VALID)))

    result = await gemini.generate_structured(
        MESSAGES, FileReviewOutput, model=MODEL, temperature=0.1
    )

    request = route.calls[0].request
    assert request.headers["x-goog-api-key"] == "test-key"
    assert "key=" not in str(request.url)  # never in the URL
    body = json.loads(request.content)
    assert body["systemInstruction"] == {"parts": [{"text": "You review code."}]}
    assert [c["role"] for c in body["contents"]] == ["user", "model", "user"]
    assert body["generationConfig"] == {"temperature": 0.1, "responseMimeType": "application/json"}
    assert result.output.file_summaries[0].summary == "Adds x."
    # Thinking tokens are billed as output.
    assert (result.usage.input_tokens, result.usage.output_tokens) == (1200, 120)
    assert result.model == f"{MODEL}-001"


@respx.mock
async def test_gemini_short_rate_limit_waits_the_server_hint(
    gemini: GeminiProvider, sleep: FakeSleep
) -> None:
    rate_limited = httpx.Response(
        429,
        json={
            "error": {
                "code": 429,
                "status": "RESOURCE_EXHAUSTED",
                "details": [
                    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "7s"}
                ],
            }
        },
    )
    respx.post(URL).mock(side_effect=[rate_limited, _gemini_response(json.dumps(VALID))])
    await gemini.generate_structured(MESSAGES, FileReviewOutput, model=MODEL, temperature=0)
    assert sleep.calls == [7.0]


@respx.mock
async def test_gemini_long_rate_limit_raises(gemini: GeminiProvider) -> None:
    respx.post(URL).mock(return_value=httpx.Response(429, headers={"retry-after": "120"}))
    with pytest.raises(LLMRateLimited) as exc_info:
        await gemini.generate_structured(MESSAGES, FileReviewOutput, model=MODEL, temperature=0)
    assert exc_info.value.retry_after == 120


@respx.mock
async def test_gemini_retries_server_errors(gemini: GeminiProvider, sleep: FakeSleep) -> None:
    respx.post(URL).mock(side_effect=[httpx.Response(503), _gemini_response(json.dumps(VALID))])
    await gemini.generate_structured(MESSAGES, FileReviewOutput, model=MODEL, temperature=0)
    assert len(sleep.calls) == 1


@respx.mock
async def test_gemini_client_errors_are_not_retried(gemini: GeminiProvider) -> None:
    route = respx.post(URL).mock(
        return_value=httpx.Response(400, json={"error": {"message": "API key not valid"}})
    )
    with pytest.raises(LLMError, match="API key not valid"):
        await gemini.generate_structured(MESSAGES, FileReviewOutput, model=MODEL, temperature=0)
    assert route.call_count == 1


@respx.mock
async def test_gemini_blocked_prompt(gemini: GeminiProvider) -> None:
    respx.post(URL).mock(
        return_value=httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}})
    )
    with pytest.raises(LLMError, match="blockReason=SAFETY"):
        await gemini.generate_structured(MESSAGES, FileReviewOutput, model=MODEL, temperature=0)


async def test_gemini_rejects_odd_model_names(gemini: GeminiProvider) -> None:
    with pytest.raises(LLMError, match="invalid model name"):
        await gemini.generate_structured(
            MESSAGES, FileReviewOutput, model="../../v1/x", temperature=0
        )
