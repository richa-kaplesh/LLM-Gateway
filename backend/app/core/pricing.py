"""One rate card for every model the gateway can call, and the one place cost is computed.

Prices are USD per million tokens and come from config.py (so you change a price in one
place, or with an env var). A model that is not on the card is NOT silently priced at the
wrong rate: cost is 0 and `price_known` is False, which the Baseline tab shows.

Cost = fresh_input x input_price + cached_input x cached_price + output x output_price
  * cached_input are prompt tokens the provider served from its prompt cache
  * output includes thinking tokens: providers bill reasoning as output
"""
import logging
from dataclasses import dataclass

from app.core.config import get_settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Price:
    input: float                      # USD per 1M input tokens
    output: float                     # USD per 1M output tokens
    cached_input: float | None = None  # None = no known discount (billed as normal input)


def rate_card() -> dict[str, Price]:
    s = get_settings()

    def cached(v: float) -> float | None:
        return v if v >= 0 else None

    return {
        s.GROQ_MODEL: Price(s.GROQ_INPUT_COST_PER_MILLION, s.GROQ_OUTPUT_COST_PER_MILLION,
                            cached(s.GROQ_CACHED_INPUT_COST_PER_MILLION)),
        s.GEMINI_MODEL: Price(s.GEMINI_INPUT_COST_PER_MILLION, s.GEMINI_OUTPUT_COST_PER_MILLION,
                              cached(s.GEMINI_CACHED_INPUT_COST_PER_MILLION)),
    }


def price_usage(model: str, input_tokens: int | None, output_tokens: int | None,
                cached_tokens: int | None = None) -> tuple[float, bool]:
    """Returns (cost_usd, price_known). Missing token counts are treated as 0 (the provider
    did not report them), cached tokens are clamped to [0, input_tokens] so a provider bug can
    never produce a negative bill."""
    price = rate_card().get(model)
    if price is None:
        log.warning("no price for model %r in the rate card; cost recorded as 0", model)
        return 0.0, False
    inp = max(0, int(input_tokens or 0))
    out = max(0, int(output_tokens or 0))
    cached = min(max(0, int(cached_tokens or 0)), inp)
    cached_price = price.input if price.cached_input is None else price.cached_input
    cost = ((inp - cached) * price.input + cached * cached_price + out * price.output) / 1_000_000
    return cost, True
