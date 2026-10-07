"""LLM Gateway router — provider + key selection, fallback, and rotation.

Rotation rules
──────────────
1. Pick the primary provider (sticky routing / weighted random).
2. Call it with the current key.
3. If rate-limited (429 / ProviderRateLimitedError):
   a. Put that key on cooldown (Retry-After respected when present).
   b. Try the OTHER provider with its first available key.
   c. If the other provider is also rate-limited: go back to the primary and
      try its NEXT available key, then the other provider's next key, and so
      on — alternating until all keys on both providers are exhausted.
   d. If every key on every provider is rate-limited: return
      AllProvidersRateLimitedError with the soonest retry time.
4. If a provider returns a NON-rate-limit failure (5xx, connection error,
   timeout, auth failure): classify_error() returns 'provider_down'.
   Stop immediately — no key rotation, no fallback.  Return a clear error
   that names the provider and includes the underlying cause.

The classify_error() helper in exceptions.py is the single place that encodes
the "rotate vs stop" decision, making it easy to read and test independently.
"""

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
    ProviderDownError, ProviderConnectionError, classify_error,
)
from app.clients.key_manager import KeyManager
import asyncio
import json
import tiktoken
import logging
import time

log = logging.getLogger(__name__)
settings = get_settings()
_encoder = tiktoken.get_encoding("cl100k_base")

CLIENTS = {"groq": groq_client, "gemini": gemini_client}

# ── Per-provider key managers (initialised once at import time) ────────────
_key_managers: dict[str, KeyManager] = {
    "groq":   KeyManager("groq",   settings.groq_keys()),
    "gemini": KeyManager("gemini", settings.gemini_keys()),
}

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


async def _call_with_retry(client_module, request, api_key: str, attempts=3, delay=2.0):
    name = client_module.__name__.rsplit(".", 1)[-1]    # "groq_client" / "gemini_client"
    last_error = None
    for i in range(attempts):
        t0 = time.perf_counter()
        try:
            return await client_module.complete(request, api_key=api_key)
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
                            reserve: bool, api_key: str) -> GatewayResponse:
    """One provider + one key, with breaker + capacity bookkeeping.
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
        resp = await _call_with_retry(CLIENTS[p], request, api_key=api_key)
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


# ── Multi-key rotation ─────────────────────────────────────────────────────

async def _route_with_key_rotation(
    primary: str,
    request: GatewayRequest,
    estimated_tokens: int,
) -> GatewayResponse:
    """Implement the full rotation policy across all keys on both providers.

    Algorithm:
    1. Try primary provider with its next available key (capacity already reserved).
    2. On rate-limit: put that key on cooldown, try OTHER provider (first available key).
    3. If other is also rate-limited: alternate back to primary's next key, then other's
       next key, until all keys on both providers are exhausted.
    4. On provider-down (non-rate-limit error): stop immediately, raise ProviderDownError.
    5. All keys exhausted: raise AllProvidersRateLimitedError with soonest retry.

    Note: _attempt_provider() still manages the circuit-breaker and RPM/TPM buckets.
    The key manager manages per-key cooldowns (from 429 Retry-After).
    """
    fallback = _other_provider(primary)
    km_primary = _key_managers[primary]
    km_fallback = _key_managers[fallback]

    # Track the last rate-limit error from each provider for building the final error
    last_rl_primary: ProviderRateLimitedError | None = None
    last_rl_fallback: ProviderRateLimitedError | None = None

    # Build an interleaved sequence: primary, fallback, primary, fallback, …
    # We keep drawing one key from each provider alternately until both are dry.
    # We track the last key tried on each side so we can skip it in next_available().
    tried_primary_key: str | None = None
    tried_fallback_key: str | None = None

    # First attempt: primary provider (capacity already reserved in route())
    ks = km_primary.next_available()
    if ks is None:
        log.warning("all %s keys are on cooldown at start", primary)
    else:
        tried_primary_key = ks.key
        log.info("routing request: provider=%s key=%s", primary, ks.masked)
        try:
            resp = await _attempt_provider(primary, request, estimated_tokens,
                                           reserve=False, api_key=ks.key)
            km_primary.record_success(ks)
            return resp
        except InvalidRequestError:
            raise
        except ProviderRateLimitedError as e:
            km_primary.record_rate_limited(ks, e.retry_after)
            last_rl_primary = e
            log.warning("rotation: %s key %s rate-limited; trying %s", primary, ks.masked, fallback)
        except ProviderConnectionError as e:
            # Network-level failure (DNS/TCP/timeout).  Don't penalise the key
            # (the network may recover); try the other provider immediately.
            log.warning("rotation: %s connection error; falling back to %s: %s",
                        primary, fallback, e)
            ks_fb = km_fallback.next_available()
            if ks_fb is None:
                raise AllProvidersUnavailableError(
                    f"Groq connection error and all {fallback} keys are on cooldown.",
                    retry_after=km_fallback.soonest_retry_seconds(),
                ) from e
            try:
                resp = await _attempt_provider(fallback, request, estimated_tokens,
                                               reserve=True, api_key=ks_fb.key)
                km_fallback.record_success(ks_fb)
                return resp
            except Exception as fb_err:
                raise AllProvidersUnavailableError(
                    f"{primary} connection error AND {fallback} failed: {fb_err}",
                    retry_after=None,
                ) from fb_err
        except Exception as e:
            # Server-side failure (5xx, bad key, wrong model) — stop immediately
            raise ProviderDownError(primary, e) from e

    # Alternating loop: fallback → primary → fallback → …
    # Each iteration picks one key from whichever side is "next".
    turn = fallback      # who we are about to try
    for _ in range(km_primary.key_count() + km_fallback.key_count()):
        km = km_fallback if turn == fallback else km_primary
        skip = tried_fallback_key if turn == fallback else tried_primary_key

        ks = km.next_available(skip_key=skip)
        if ks is None:
            log.info("no available keys left on %s, switching side", turn)
            turn = _other_provider(turn)
            continue

        if turn == fallback:
            tried_fallback_key = ks.key
        else:
            tried_primary_key = ks.key

        log.info("rotation: trying provider=%s key=%s", turn, ks.masked)
        try:
            resp = await _attempt_provider(turn, request, estimated_tokens,
                                           reserve=(turn == fallback),
                                           api_key=ks.key)
            km.record_success(ks)
            return resp
        except InvalidRequestError:
            raise
        except ProviderRateLimitedError as e:
            km.record_rate_limited(ks, e.retry_after)
            if turn == fallback:
                last_rl_fallback = e
            else:
                last_rl_primary = e
            log.warning("rotation: %s key %s rate-limited; switching to other side", turn, ks.masked)
        except ProviderConnectionError as e:
            # Connection error in the alternating loop — log and keep rotating
            # (other keys/providers may work; don't stop here).
            log.warning("rotation: %s connection error; continuing rotation: %s", turn, e)
        except Exception as e:
            # Server-side failure — stop immediately
            raise ProviderDownError(turn, e) from e

        turn = _other_provider(turn)

    # All keys on all providers exhausted
    soonest = min(
        (t for t in (km_primary.soonest_retry_seconds(), km_fallback.soonest_retry_seconds())
         if t is not None),
        default=None,
    )
    errors = " | ".join(filter(None, [str(last_rl_primary), str(last_rl_fallback)]))
    log.error(
        "all keys exhausted on both providers (groq=%d keys, gemini=%d keys); "
        "soonest retry in %.0f s",
        km_primary.key_count(), km_fallback.key_count(), soonest or 0,
    )
    raise AllProvidersRateLimitedError(
        f"All keys on every provider are rate-limited. {errors}",
        retry_after=soonest,
    )


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
        response = await _route_with_key_rotation(provider, request, estimated_tokens)

        # Detect whether we ended up on the fallback provider so we can re-pin
        if response.provider_used != provider:
            was_fallback = True
            pin_conversation(request.conversation_id, response.provider_used)
            log.info("fallback succeeded: conversation=%s re-pinned from %s to %s",
                     request.conversation_id, provider, response.provider_used)

    except ProviderDownError as e:
        # A provider returned a non-rate-limit failure.  Do NOT rotate or fall back.
        log.error("provider %s is down: %s", e.provider, e.cause)
        raise AllProvidersUnavailableError(
            f"Provider {e.provider!r} is down: {e.cause}",
            retry_after=None,
        )
    except InvalidRequestError:
        raise

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