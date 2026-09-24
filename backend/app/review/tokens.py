"""Cheap, provider-independent token estimate used for *budgeting* (ADR 0003).

No single tokenizer matches Gemini, Claude, GPT and open models, and calling a
count-tokens API for every chunk costs a network round trip. Source code packs
roughly 3 to 4 characters per token, so dividing by 3.5 slightly *over*-estimates
for prose. That's the safe direction for a budget. Actual usage for *cost* always
comes from the provider's response, never from this estimate.
"""

import math

CHARS_PER_TOKEN = 3.5


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)
