import logging
import re
import time

import openai

from app.clients.exceptions import (
    InvalidRequestError, InvalidToolCallError, ProviderConnectionError, ProviderKeyError,
    ProviderOverloadedError, ProviderRateLimitedError, ProviderUnavailableError, QuotaExhaustedError,
)
from app.core.config import get_settings
from app.core.pricing import price_usage
from app.models.deployment import Deployment
from app.models.schemas import GatewayRequest, GatewayResponse

log = logging.getLogger(__name__)
settings = get_settings()

GATEWAY_ONLY_TOOL_CALL_FIELDS = {"thought_signature"}
INVALID_TOOL_CALL_CODE = "tool_use_failed"
OVERLOADED_STATUSES = {503, 529}
DAILY_LIMIT_PATTERN = re.compile(r"per[ -]day|\b[RT]PD\b|daily", re.IGNORECASE)
DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)(ms|h|m|s)")
UNIT_SECONDS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}
EPOCH_MILLISECONDS_THRESHOLD = 1e11
EPOCH_SECONDS_THRESHOLD = 1e9
DEFAULT_HEADER_SUFFIX = "default"

_clients: dict[str, openai.AsyncOpenAI] = {}


def _client_for(deployment: Deployment) -> openai.AsyncOpenAI:
    client = _clients.get(deployment.id)
    if client is None:
        client = openai.AsyncOpenAI(
            api_key=deployment.api_key.get_secret_value(),
            base_url=deployment.base_url,
            max_retries=0,
            timeout=settings.PROVIDER_TIMEOUT_SECONDS,
        )
        _clients[deployment.id] = client
    return client


async def close_clients() -> None:
    for client in _clients.values():
        await client.close()
    _clients.clear()


def _clean_messages(messages: list[dict]) -> list[dict]:
    cleaned = []
    for m in messages:
        calls = m.get("tool_calls")
        if calls:
            m = {**m, "tool_calls": [
                {k: v for k, v in c.items() if k not in GATEWAY_ONLY_TOOL_CALL_FIELDS}
                if isinstance(c, dict) else c
                for c in calls
            ]}
        cleaned.append(m)
    return cleaned


def _request_kwargs(request: GatewayRequest, deployment: Deployment) -> dict:
    kwargs = {"model": deployment.model, "messages": _clean_messages(request.messages)}
    if request.tools is not None:
        kwargs["tools"] = request.tools
    if request.tool_choice is not None:
        kwargs["tool_choice"] = request.tool_choice
    return kwargs


def _as_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _seconds_until_reset(reset: float) -> float:
    if reset > EPOCH_MILLISECONDS_THRESHOLD:
        reset /= 1000
    if reset > EPOCH_SECONDS_THRESHOLD:
        return max(0.0, reset - time.time())
    return max(0.0, reset)


def _duration_seconds(value) -> float | None:
    if value is None:
        return None
    number = _as_float(value)
    if number is not None:
        return _seconds_until_reset(number)
    parts = DURATION_PART.findall(str(value))
    if not parts:
        return None
    return sum(float(amount) * UNIT_SECONDS[unit] for amount, unit in parts)


def _retry_after_seconds(headers) -> float | None:
    if headers is None:
        return None
    retry_after = _as_float(headers.get("retry-after"))
    if retry_after is not None:
        return max(0.0, retry_after)
    return _duration_seconds(headers.get("x-ratelimit-reset"))


def _header_name(kind: str, suffix: str) -> str:
    return f"x-ratelimit-{kind}" if suffix == DEFAULT_HEADER_SUFFIX else f"x-ratelimit-{kind}-{suffix}"


def rate_limit_info(headers, deployment: Deployment) -> dict[str, tuple[float, float]] | None:
    if headers is None or not deployment.rate_limit_headers:
        return None
    info = {}
    for suffix, meter in deployment.rate_limit_headers.items():
        remaining = _as_float(headers.get(_header_name("remaining", suffix)))
        reset_in = _duration_seconds(headers.get(_header_name("reset", suffix)))
        if remaining is not None and reset_in is not None:
            info[meter] = (remaining, reset_in)
    return info or None


def _error_code(body) -> str | None:
    if not isinstance(body, dict):
        return None
    error = body.get("error", body)
    return error.get("code") if isinstance(error, dict) else None


def _usage(response) -> tuple[int, int, int]:
    u = getattr(response, "usage", None)
    details = getattr(u, "prompt_tokens_details", None)
    return (int(getattr(u, "prompt_tokens", 0) or 0),
            int(getattr(u, "completion_tokens", 0) or 0),
            int(getattr(details, "cached_tokens", 0) or 0))


def _normalize_tool_calls(tool_calls) -> list[dict] | None:
    if not tool_calls:
        return None
    return [
        {"id": call.id, "type": "function",
         "function": {"name": call.function.name, "arguments": call.function.arguments}}
        for call in tool_calls
    ]


def _rate_limited(deployment: Deployment, message: str, retry_after, rate_limit) -> ProviderRateLimitedError:
    if DAILY_LIMIT_PATTERN.search(message):
        return QuotaExhaustedError(f"{deployment.id} daily quota: {message}",
                                   retry_after=retry_after, rate_limit=rate_limit)
    return ProviderRateLimitedError(f"{deployment.id} rate limit: {message}",
                                    retry_after=retry_after, rate_limit=rate_limit)


def _raise_for_body_error(response, deployment: Deployment) -> None:
    extra = getattr(response, "model_extra", None) or {}
    error = extra.get("error")
    if error is None and getattr(response, "choices", None):
        return
    if not isinstance(error, dict):
        raise ProviderUnavailableError(f"{deployment.id}: response had no choices")
    message = str(error.get("message", error))[:300]
    try:
        code = int(error.get("code"))
    except (TypeError, ValueError):
        code = None
    if code == 429:
        raise _rate_limited(deployment, f"(in 200 body) {message}", None, None)
    if code in (401, 403):
        raise ProviderKeyError(f"{deployment.id} rejected the API key (in 200 body): {message}")
    if code == 400:
        raise InvalidRequestError(f"{deployment.id} rejected the request (in 200 body): {message}")
    if code in OVERLOADED_STATUSES:
        raise ProviderOverloadedError(f"{deployment.id} overloaded {code} (in 200 body): {message}")
    raise ProviderConnectionError(f"{deployment.id} upstream error {code} (in 200 body): {message}")


def _to_gateway_response(response, deployment: Deployment, latency_ms: float, rate_limit) -> GatewayResponse:
    prompt_t, completion_t, cached_t = _usage(response)
    choice = response.choices[0]
    return GatewayResponse(
        content=choice.message.content,
        tool_calls=_normalize_tool_calls(choice.message.tool_calls),
        finish_reason=choice.finish_reason,
        model_used=deployment.model,
        provider_used=deployment.provider,
        deployment_id=deployment.id,
        tier=deployment.tier,
        cost_usd=price_usage(deployment.price, prompt_t, completion_t, cached_t),
        latency_ms=latency_ms,
        cache_hit=False,
        input_tokens=prompt_t, output_tokens=completion_t, cached_tokens=cached_t,
        rate_limit=rate_limit,
    )


async def complete(request: GatewayRequest, deployment: Deployment) -> GatewayResponse:
    client = _client_for(deployment)
    start = time.perf_counter()
    try:
        raw = await client.chat.completions.with_raw_response.create(**_request_kwargs(request, deployment))
        response = raw.parse()
    except openai.RateLimitError as e:
        headers = getattr(e.response, "headers", None)
        detail = (getattr(e, "message", None) or str(e))[:300]
        raise _rate_limited(deployment, detail, _retry_after_seconds(headers), rate_limit_info(headers, deployment))
    except openai.APIConnectionError as e:
        raise ProviderConnectionError(f"{deployment.id} connection failed: {type(e).__name__}: {e}")
    except (openai.AuthenticationError, openai.PermissionDeniedError) as e:
        raise ProviderKeyError(f"{deployment.id} rejected the API key (HTTP {e.status_code}): {str(e)[:200]}")
    except openai.BadRequestError as e:
        if _error_code(e.body) == INVALID_TOOL_CALL_CODE:
            raise InvalidToolCallError(f"{deployment.id} generated an invalid tool call: {str(e)[:300]}")
        raise InvalidRequestError(f"{deployment.id} rejected the request: {str(e)[:500]}")
    except openai.APIStatusError as e:
        if e.status_code in OVERLOADED_STATUSES:
            raise ProviderOverloadedError(f"{deployment.id} overloaded (HTTP {e.status_code}): {str(e)[:300]}",
                                          retry_after=_retry_after_seconds(getattr(e.response, "headers", None)))
        if e.status_code >= 500:
            raise ProviderConnectionError(f"{deployment.id} server error (HTTP {e.status_code}): {str(e)[:300]}")
        raise ProviderUnavailableError(f"{deployment.id} HTTP {e.status_code}: {str(e)[:300]}")
    latency_ms = (time.perf_counter() - start) * 1000
    _raise_for_body_error(response, deployment)
    return _to_gateway_response(response, deployment, latency_ms, rate_limit_info(raw.headers, deployment))
