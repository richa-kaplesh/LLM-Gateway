import time
import groq
from app.core.config import get_settings
from app.core.pricing import price_usage
from app.models.schemas import GatewayRequest, GatewayResponse
from app.clients.exceptions import (
    ProviderUnavailableError, ProviderConnectionError, InvalidRequestError,
    ProviderRateLimitedError, ProviderKeyError,
)
import logging
log = logging.getLogger(__name__)

settings = get_settings()


def _make_client(api_key: str) -> groq.AsyncGroq:
    # max_retries=0: the gateway owns retry/fallback policy. The SDK's built-in
    # retries (default 2, including on 429) would silently multiply every
    # upstream call and burn the very quota we are trying to protect.
    return groq.AsyncGroq(
        api_key=api_key,
        max_retries=0,
        timeout=settings.PROVIDER_TIMEOUT_SECONDS,
    )


def _retry_after_seconds(e: Exception) -> float | None:
    """Groq sends a Retry-After header (seconds) on 429s."""
    try:
        value = e.response.headers.get("retry-after")
        return float(value) if value is not None else None
    except (AttributeError, TypeError, ValueError):
        return None


def calculate_cost(prompt_tokens: int, completion_tokens: int, cached_tokens: int = 0) -> float:
    return price_usage(settings.GROQ_MODEL, prompt_tokens, completion_tokens, cached_tokens)[0]


def _usage(response) -> tuple[int, int, int]:
    """(prompt, completion, cached) tokens as reported by Groq; completion already includes
    any reasoning tokens. Missing fields count as 0 rather than crashing a good answer."""
    u = getattr(response, "usage", None)
    details = getattr(u, "prompt_tokens_details", None)
    return (int(getattr(u, "prompt_tokens", 0) or 0),
            int(getattr(u, "completion_tokens", 0) or 0),
            int(getattr(details, "cached_tokens", 0) or 0))


def normalize_tool_calls(tool_calls):
    if not tool_calls:
        return None

    return [
        {
            "id": call.id,
            "type": "function",
            "function": {
                "name": call.function.name,
                "arguments": call.function.arguments  # Groq already gives this as a JSON string
            }
        }
        for call in tool_calls
    ]


def _clean_messages(messages: list[dict]) -> list[dict]:
    """Drop gateway-only fields before forwarding. `thought_signature` is Gemini's;
    it rides on assistant tool_calls and Groq doesn't know it (unknown field -> 400)."""
    cleaned = []
    for m in messages:
        calls = m.get("tool_calls")
        if calls:
            m = {**m, "tool_calls": [
                {k: v for k, v in c.items() if k != "thought_signature"} if isinstance(c, dict) else c
                for c in calls
            ]}
        cleaned.append(m)
    return cleaned


async def complete(request: GatewayRequest, api_key: str | None = None) -> GatewayResponse:
    """Call Groq.  Pass ``api_key`` to use a specific key; omit to fall back to
    the single legacy key in settings (backward-compatible behaviour)."""
    key = api_key or settings.GROQ_API_KEY
    client = _make_client(key)
    try:
        start_time = time.perf_counter()

        kwargs = {
            "model": settings.GROQ_MODEL,
            "messages": _clean_messages(request.messages),
        }
        if request.tools is not None:
            kwargs["tools"] = request.tools
        if request.tool_choice is not None:
            kwargs["tool_choice"] = request.tool_choice

        response = await client.chat.completions.create(**kwargs)

        latency_ms = (time.perf_counter() - start_time) * 1000
        prompt_t, completion_t, cached_t = _usage(response)
        cost, price_known = price_usage(settings.GROQ_MODEL, prompt_t, completion_t, cached_t)

        message = response.choices[0].message
        answer = message.content
        tool_calls = normalize_tool_calls(message.tool_calls)
        finish_reason = response.choices[0].finish_reason

        return GatewayResponse(
            content=answer,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            model_used=settings.GROQ_MODEL,
            provider_used="groq",
            cost_usd=cost,
            latency_ms=latency_ms,
            cache_hit=False,
            input_tokens=prompt_t, output_tokens=completion_t, cached_tokens=cached_t,
            price_known=price_known,
        )

    except groq.RateLimitError as e:
        # 429 = alive but busy. Keep Groq's own message: it says WHICH limit
        # (requests/min, tokens/min or tokens/day) and when it resets.
        detail = (getattr(e, "message", None) or str(e))[:300]
        retry_after = _retry_after_seconds(e)
        log.warning("Groq 429 (retry_after=%s): %s", retry_after, detail)
        raise ProviderRateLimitedError(f"Groq rate limit hit: {detail}", retry_after=retry_after)
    except groq.APIConnectionError as e:
        # Network-level failure (DNS, TCP, timeout) — the provider may be fine.
        # ProviderConnectionError tells the router to try the other provider.
        raise ProviderConnectionError(f"Groq connection failed: {e}")
    except groq.BadRequestError as e:
        code = (getattr(e, "body", None) or {}).get("error", {}).get("code")
        if code == "tool_use_failed":
            log.warning("Groq produced an invalid tool call (tool_use_failed); router will retry or fall back")

            raise ProviderUnavailableError(f"Groq failed to generate a valid tool call: {str(e)}")
        raise InvalidRequestError(f"Groq rejected the request: {str(e)}")
    except (groq.AuthenticationError, groq.PermissionDeniedError) as e:
        # 401/403: this KEY is bad. The router benches it and tries the next one.
        raise ProviderKeyError(f"Groq rejected the API key (HTTP {getattr(e, 'status_code', '401/403')}): {str(e)[:200]}")
    except groq.InternalServerError as e:
        # 5xx (e.g. 503 "over capacity") is a temporary Groq-side problem: fall back to Gemini.
        raise ProviderConnectionError(f"Groq server error (HTTP {getattr(e, 'status_code', '5xx')}): {str(e)[:300]}")
    except Exception as e:
        log.error("Groq unexpected error", exc_info=True)
        raise ProviderUnavailableError(f"Groq error: {str(e)}")