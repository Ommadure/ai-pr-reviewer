"""Token usage → US dollars.

Prices change and differ by tier, so none are hard-coded as facts here: they
come from configuration (`LLM_PRICING`, USD per 1M input/output tokens, copied
from the provider's pricing page). An unknown model costs 0 and is flagged, so
a missing price is visible instead of silently wrong.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.review.llm.base import TokenUsage

PER_MILLION = 1_000_000


@dataclass(frozen=True)
class ModelPrice:
    input_per_mtok: float
    output_per_mtok: float


class PriceTable:
    def __init__(self, prices: Mapping[str, ModelPrice] | None = None) -> None:
        self._prices = dict(prices or {})

    @classmethod
    def from_config(cls, raw: Mapping[str, Sequence[float]]) -> "PriceTable":
        """`{"model-name": [input_usd_per_mtok, output_usd_per_mtok]}`."""
        return cls({model: ModelPrice(float(p[0]), float(p[1])) for model, p in raw.items()})

    def price_for(self, model: str) -> ModelPrice | None:
        if model in self._prices:
            return self._prices[model]
        # Providers often report a versioned name ("model-001") for an alias ("model").
        matches = [name for name in self._prices if model.startswith(name)]
        return self._prices[max(matches, key=len)] if matches else None

    def cost_usd(self, model: str, usage: TokenUsage) -> float:
        price = self.price_for(model)
        if price is None:
            return 0.0
        return (
            usage.input_tokens * price.input_per_mtok + usage.output_tokens * price.output_per_mtok
        ) / PER_MILLION

    def is_priced(self, model: str) -> bool:
        return self.price_for(model) is not None


def usd_to_inr(usd: float, rate: float | None) -> float | None:
    return None if rate is None else usd * rate
