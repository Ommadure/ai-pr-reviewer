# ADR 0003: Gemini as the first LLM provider, behind a provider-agnostic interface

- **Status:** Accepted
- **Date:** 2026-09-24

## Context
- Development and evaluation make many LLM calls. The eval harness alone reviews 30 cases for each prompt or model variant. A **free tier** keeps the project affordable.
- Longer term we want to run an **open-source model** (for example via Ollama or vLLM), so we aren't tied to one vendor and can compare quality against cost.
- The spec's default was Anthropic. Its engine design (a pure engine plus an `LLMProvider` protocol) already allows swapping providers.

## Decision
- The first real provider is **Google Gemini**, via the free tier from Google AI Studio.
- The review engine depends only on an `LLMProvider` protocol: `generate_structured(messages, schema) -> LLMResult[T]`. This returns parsed output, token usage, and latency. No vendor SDK types leak past `app/review/llm/`.
- **Structured output is schema-driven on our side.** Every provider's output is validated with our Pydantic models, and we allow one repair attempt. Vendor features like JSON mode are an optimisation, never a requirement. That matters because smaller open models are less reliable at strict JSON.
- The open-source path will be an **OpenAI-compatible provider** (`LLM_PROVIDER=openai_compatible` plus a base URL). Ollama, vLLM, and most hosted open-model APIs expose that API, so one implementation covers them all.
- A `FakeLLMProvider` returns scripted outputs for tests. Tests never call a real LLM.
- **Token counting:** for budgeting we use a cheap local estimate, because no single tokenizer matches Gemini and open models. For **cost** we record the actual usage each provider reports in its response.

## Consequences
- Development costs nothing within free-tier limits.
- **Free-tier rate limits are low.** `REVIEW_MAX_CONCURRENT_LLM_CALLS` defaults to 2, and 429s go through retry with backoff.
- **Privacy:** Google may use free-tier inputs to improve its products. Point ReviewPilot only at test repositories with non-sensitive code. Secret redaction (Phase 2) runs before any LLM call regardless of provider.
- Eval results must record the provider and model, because precision and recall numbers aren't comparable across models.

## Alternatives considered
- **Anthropic or OpenAI first:** strong structured-output support, but paid from the first call.
- **Open-source model only:** free and private, but needs local GPU or RAM, and weaker JSON reliability would slow early development. It's planned as the second provider instead.
