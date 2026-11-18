"""Token-cost estimates using provider list prices checked on 2026-11-18.

Prices are USD per 1M tokens and are kept in one place so the API response and the
README use the same numbers. They are estimates, not invoice guarantees; provider
pricing and model aliases can change.
"""

from __future__ import annotations

from dataclasses import dataclass

PRICING_CHECKED_ON = "2026-11-18"


@dataclass(frozen=True, slots=True)
class TokenPrice:
    input_per_million: float
    output_per_million: float
    source: str


MODEL_PRICES: dict[str, TokenPrice] = {
    "gemini-3.5-flash-lite": TokenPrice(
        0.30, 2.50, "https://ai.google.dev/gemini-api/docs/pricing"
    ),
    "gemini-3.1-flash-lite": TokenPrice(
        0.25, 1.50, "https://ai.google.dev/gemini-api/docs/pricing"
    ),
    "gpt-6-luna": TokenPrice(
        0.10, 0.50, "https://developers.openai.com/api/docs/pricing"
    ),
    "gpt-5.4-nano": TokenPrice(
        0.20, 1.25, "https://developers.openai.com/api/docs/pricing"
    ),
    "claude-haiku-5-5": TokenPrice(
        0.10, 0.50, "https://platform.claude.com/docs/en/about-claude/pricing"
    ),
    "claude-sonnet-5-5": TokenPrice(
        2.00, 10.00, "https://platform.claude.com/docs/en/about-claude/pricing"
    ),
}


def estimate_cost_usd(
    model: str,
    input_tokens: int | None,
    output_tokens: int | None,
) -> float | None:
    if input_tokens is None or output_tokens is None:
        return None

    price = MODEL_PRICES.get(model)
    if price is None:
        model_lower = model.lower()
        for known_model, known_price in MODEL_PRICES.items():
            if model_lower.startswith(known_model):
                price = known_price
                break
    if price is None:
        return None

    cost = (
        input_tokens * price.input_per_million / 1_000_000
        + output_tokens * price.output_per_million / 1_000_000
    )
    return round(cost, 8)
