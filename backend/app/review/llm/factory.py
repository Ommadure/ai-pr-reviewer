"""Build the configured LLM provider. The only place that knows every provider."""

import httpx

from app.core.config import ReviewSettings
from app.review.llm.base import LLMProvider
from app.review.llm.fake import FakeLLMProvider
from app.review.llm.gemini import GeminiProvider
from app.review.models import ReviewBudget
from app.review.pricing import PriceTable


class LLMConfigurationError(Exception):
    pass


def build_provider(settings: ReviewSettings, http: httpx.AsyncClient) -> LLMProvider:
    match settings.llm_provider:
        case "fake":
            return FakeLLMProvider()
        case "gemini":
            key = settings.gemini_api_key.get_secret_value()
            if not key:
                raise LLMConfigurationError(
                    "GEMINI_API_KEY is not set (create one at https://aistudio.google.com/apikey)"
                )
            return GeminiProvider(key, http)
        case other:
            # Planned: an OpenAI-compatible provider for open models (ADR 0003).
            raise LLMConfigurationError(f"LLM_PROVIDER={other!r} is not implemented yet")


def review_model(settings: ReviewSettings) -> str:
    if settings.llm_provider == "fake":
        return settings.llm_model or "fake-model"
    if not settings.llm_model:
        raise LLMConfigurationError(
            "LLM_MODEL is not set. Pick a current model name from your provider's docs, "
            "e.g. https://ai.google.dev/gemini-api/docs/models for Gemini."
        )
    return settings.llm_model


def review_budget(settings: ReviewSettings) -> ReviewBudget:
    return ReviewBudget(
        max_files=settings.review_max_files,
        max_input_tokens=settings.review_max_input_tokens,
        max_chunk_tokens=settings.review_max_chunk_tokens,
        max_concurrent_llm_calls=settings.review_max_concurrent_llm_calls,
        max_output_tokens=settings.review_max_output_tokens,
        max_cost_usd=settings.review_max_cost_usd,
    )


def price_table(settings: ReviewSettings) -> PriceTable:
    return PriceTable.from_config(settings.llm_pricing)
