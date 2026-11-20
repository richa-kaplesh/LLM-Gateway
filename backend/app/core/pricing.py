from app.models.deployment import Price


def price_usage(price: Price, input_tokens: int | None, output_tokens: int | None,
                cached_tokens: int | None = None) -> float:
    inp = max(0, int(input_tokens or 0))
    out = max(0, int(output_tokens or 0))
    cached = min(max(0, int(cached_tokens or 0)), inp)
    cached_price = price.input if price.cached_input is None else price.cached_input
    return ((inp - cached) * price.input + cached * cached_price + out * price.output) / 1_000_000
