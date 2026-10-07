from app.clients import groq_client, gemini_client
from app.cache.cache import get_cache_for
from app.models.schemas import GatewayRequest, GatewayResponse
from app.router.bucket import (
    get_provider_for_conversation, pin_conversation, reserve_capacity,
    release_capacity, capacity_retry_after, tpm_buckets,
)
from app.tracker.tracker import tracker
from app.router.circuit_breaker import breakers
from app.core.config import get_settings
from app.clients.exceptions import (
    InvalidRequestError, TooLongError, AllProvidersRateLimitedError,
    AllProvidersUnavailableError, ProviderRateLimitedError, CircuitOpenError,
)
import asyncio
import json
import tiktoken
import logging
import time

log = logging.getLogger(__name__)
settings = get_settings()
_encoder = tiktoken.get_encoding("cl100k_base")

CLIENTS = {"groq": groq_client, "gemini": gemini_client}

DB_TIMEOUT_SECONDS = 2.0   # the database must never be able to stall or fail a request


def estimate_tokens(messages: list[dict]) -> int:
    text = " ".join(m.get("content", "") or "" for m in messages)
    return len(_encoder.encode(text))


def estimate_request_tokens(request: GatewayRequest) -> int:
    """What this request will cost against a provider's TPM: the messages, the
    tools schema (sent on every call), and an allowance for the generated output
    (providers count input + output)."""
    total = estimate_tokens(request.messages)
    if request.tools:
        total += len(_encoder.encode(json.dumps(request.tools)))
    return total + settings.OUTPUT_TOKEN_ALLOWANCE


def _has_tool_history(messages: list[dict]) -> bool:
    return any(m.get("role") == "tool" or m.get("tool_calls") for m in messages)


async def _record_transition(p: str, before: str, after: str) -> None:
    """Persist a breaker transition. Best-effort: a DB problem must not be able
    to leave the breaker half-open or fail the request."""
    try:
        await asyncio.wait_for(tracker.log_breaker_transition(p, before, after), DB_TIMEOUT_SECONDS)
    except Exception as e:
        log.warning("could not persist breaker transition %s %s->%s: %s: %s",
                    p, before, after, type(e).__name__, e)


async def _call_with_retry(client_module, request, attempts=3, delay=2.0):
    name = client_module.__name__.rsplit(".", 1)[-1]    # "groq_client" / "gemini_client"
    last_error = None
    for i in range(attempts):
        t0 = time.perf_counter()
        try:
            return await client_module.complete(request)
        except (InvalidRequestError, ProviderRateLimitedError):
            raise          # retrying the same provider can't help
        except Exception as e:
            last_error = e
            log.warning("%s call failed after %.0f ms (attempt %d/%d): %s",
                        name, (time.perf_counter() - t0) * 1000, i + 1, attempts, e)
            if i < attempts - 1:
                await asyncio.sleep(delay)
    raise last_error


async def _attempt_provider(p: str, request: GatewayRequest, estimated_tokens: int,
                            reserve: bool) -> GatewayResponse:
    """One provider, with breaker + capacity bookkeeping.
    reserve=False when capacity was already spent by provider selection (primary);
    reserve=True for the fallback, which previously bypassed the limiter entirely."""
    if reserve:
        ok, wait = reserve_capacity(p, estimated_tokens)
        if not ok:
            raise ProviderRateLimitedError(f"{p} has no capacity right now", retry_after=wait)

    breaker = breakers[p]
    state_before = breaker.state
    if not breaker.allow_request():
        release_capacity(p, estimated_tokens)         # nothing was sent, give it back
        paused = breaker.pause_remaining()
        if paused > 0:
            log.warning("%s is cooling down after a rate limit (%.0fs left), skipping it", p, paused)
            raise ProviderRateLimitedError(f"{p} is cooling down after a rate limit", retry_after=paused)
        log.warning("circuit %s for %s, skipping it", breaker.state, p)
        raise CircuitOpenError(f"{p} circuit is {breaker.state}, skipping", retry_after=breaker.retry_after())

    t0 = time.perf_counter()
    try:
        if breaker.state != state_before:             # open -> half_open: we are the probe
            await _record_transition(p, state_before, breaker.state)
            state_before = breaker.state
        resp = await _call_with_retry(CLIENTS[p], request)
        breaker.record_success()
        log.info("provider call ok: provider=%s model=%s latency_ms=%.0f total_ms=%.0f cost_usd=%.6f",
                 p, resp.model_used, resp.latency_ms,
                 (time.perf_counter() - t0) * 1000, resp.cost_usd)
        return resp
    except InvalidRequestError as e:
        breaker.record_inconclusive()
        release_capacity(p, estimated_tokens)
        log.info("provider %s rejected the request as invalid: %s", p, e)
        raise
    except ProviderRateLimitedError as e:
        breaker.record_rate_limited(e.retry_after)    # alive but busy: pause, don't count a failure
        release_capacity(p, estimated_tokens)
        log.warning("provider %s rate-limited us (pausing %.0fs): %s", p, breaker.pause_remaining(), e)
        raise
    except asyncio.CancelledError:
        breaker.record_inconclusive()                 # a cancelled probe must not wedge half_open
        release_capacity(p, estimated_tokens)
        raise
    except Exception as e:
        breaker.record_failure()
        log.warning("provider %s failed (breaker=%s, consecutive_failures=%d): %s",
                    p, breaker.state, breaker.consecutive_failures, e)
        raise
    finally:
        if breaker.state != state_before:
            await _record_transition(p, state_before, breaker.state)


def _combine_failures(p1: str, e1: Exception, p2: str, e2: Exception) -> Exception:
    """Turn two provider failures into ONE clean error with a Retry-After hint:
    429 if both were rate limits, 503 otherwise (never a bare 500)."""
    hints = [e.retry_after for e in (e1, e2)
             if isinstance(e, (ProviderRateLimitedError, CircuitOpenError)) and e.retry_after is not None]
    retry_after = min(hints) if hints else None
    detail = f"{p1}: {e1} | {p2}: {e2}"
    if isinstance(e1, ProviderRateLimitedError) and isinstance(e2, ProviderRateLimitedError):
        return AllProvidersRateLimitedError(f"Both providers are rate-limited right now. {detail}",
                                            retry_after=retry_after)
    return AllProvidersUnavailableError(f"No provider could serve the request. {detail}",
                                        retry_after=retry_after)


async def route(request: GatewayRequest) -> GatewayResponse:
    cache = get_cache_for(request)
    lookup = None
    if cache:
        try:
            lookup = await cache.get(request)
        except Exception as e:
            # Jina down/rate-limited must not take the whole gateway down: skip the cache
            log.warning("cache lookup failed, continuing without cache: %s: %s", type(e).__name__, e)
            cache = None
        else:
            if lookup.hit:
                return lookup.hit

    estimated_tokens = estimate_request_tokens(request)
    max_capacity = max(tpm_buckets["groq"].capacity, tpm_buckets["gemini"].capacity)
    if estimated_tokens > max_capacity:
        raise TooLongError(f"Prompt is too long ({estimated_tokens} tokens) for any configured provider")

    provider = get_provider_for_conversation(
        request.conversation_id, estimated_tokens,
        must_stick=_has_tool_history(request.messages),
    )
    if provider is None:
        raise AllProvidersRateLimitedError(
            "Both providers are rate-limited right now. Try again shortly.",
            retry_after=capacity_retry_after(estimated_tokens),
        )

    was_fallback = False
    try:
        response = await _attempt_provider(provider, request, estimated_tokens, reserve=False)
    except InvalidRequestError:
        raise
    except Exception as primary_error:
        fallback_provider = _other_provider(provider)
        log.warning("fallback: %s failed (%s); trying %s", provider, primary_error, fallback_provider)
        try:
            response = await _attempt_provider(fallback_provider, request, estimated_tokens, reserve=True)
            was_fallback = True
            pin_conversation(request.conversation_id, fallback_provider)
            log.info("fallback succeeded: conversation=%s re-pinned from %s to %s",
                     request.conversation_id, provider, fallback_provider)
        except InvalidRequestError:
            raise
        except Exception as fallback_error:
            raise _combine_failures(provider, primary_error, fallback_provider, fallback_error) from None

    response.was_fallback = was_fallback

    if cache and lookup and lookup.embedding:
        emb = lookup.embedding
        cache.set(request, response, emb)        # store the pure LLM response first
        # a miss really costs the embedding call on top of the LLM call
        response.embed_latency_ms = emb.latency_ms
        response.embed_cost_usd = emb.cost_usd
        response.latency_ms += emb.latency_ms
        response.cost_usd += emb.cost_usd
    return response


def _other_provider(provider: str) -> str:
    return "gemini" if provider == "groq" else "groq"