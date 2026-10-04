from app.clients import groq_client, gemini_client
from app.cache.cache import get_cache_for
from app.models.schemas import GatewayRequest, GatewayResponse
from app.router.bucket import get_provider_for_conversation, conversation_provider_map, tpm_buckets
from app.tracker.tracker import tracker
import asyncio
from app.router.circuit_breaker import breakers
from app.clients.exceptions import InvalidRequestError, TooLongError, AllProvidersRateLimitedError
import tiktoken
import logging
import time

log = logging.getLogger(__name__)
_encoder = tiktoken.get_encoding("cl100k_base")

CLIENTS = {"groq": groq_client, "gemini": gemini_client}


def estimate_tokens(messages: list[dict]) -> int:
    text = " ".join(m.get("content", "") or "" for m in messages)
    return len(_encoder.encode(text))


async def _call_with_retry(client_module, request, attempts=3, delay=2.0):
    name = client_module.__name__.rsplit(".", 1)[-1]    # "groq_client" / "gemini_client"
    last_error = None
    for i in range(attempts):
        t0 = time.perf_counter()
        try:
            return await client_module.complete(request)
        except InvalidRequestError:
            raise
        except Exception as e:
            last_error = e
            log.warning("%s call failed after %.0f ms (attempt %d/%d): %s",
                        name, (time.perf_counter() - t0) * 1000, i + 1, attempts, e)
            if i < attempts - 1:
                await asyncio.sleep(delay)
    raise last_error


async def route(request: GatewayRequest) -> GatewayResponse:
    cache = get_cache_for(request)
    lookup = None
    if cache:
        lookup = await cache.get(request)
        if lookup.hit:
            return lookup.hit

    estimated_tokens = estimate_tokens(request.messages)
    max_capacity = max(tpm_buckets["groq"].capacity, tpm_buckets["gemini"].capacity)
    if estimated_tokens > max_capacity:
        raise TooLongError(f"Prompt is too long ({estimated_tokens} tokens) for any configured provider")

    provider = get_provider_for_conversation(request.conversation_id, estimated_tokens)

    if provider is None:
        raise AllProvidersRateLimitedError("Both providers are rate-limited right now. Try again shortly.")

    async def _try(p: str):
        breaker = breakers[p]
        state_before = breaker.state
        allowed = breaker.allow_request()
        if breaker.state != state_before:
            await tracker.log_breaker_transition(p, state_before, breaker.state)
        if not allowed:
            log.warning("circuit open for %s, skipping it", p)
            raise Exception(f"{p} circuit is open, skipping")

        state_before = breaker.state
        t0 = time.perf_counter() 
        try:
            resp = await _call_with_retry(CLIENTS[p], request)
            breaker.record_success()
            log.info("provider call ok: provider=%s model=%s latency_ms=%.0f total_ms=%.0f cost_usd=%.6f",
                     p, resp.model_used, resp.latency_ms,
                     (time.perf_counter() - t0) * 1000, resp.cost_usd) 
            return resp
        except InvalidRequestError as e:
            breaker.record_inconclusive()
            log.info("provider %s rejected the request as invalid: %s", p, e)
            raise
        except Exception as e:
            breaker.record_failure()
            log.warning("provider %s failed (breaker=%s, consecutive_failures=%d): %s",
                        p, breaker.state, breaker.consecutive_failures, e)      
            raise
        finally:
            if breaker.state != state_before:
                await tracker.log_breaker_transition(p, state_before, breaker.state)

    was_fallback = False
    try:
        response = await _try(provider)
    except InvalidRequestError:
        raise
    except Exception as primary_error:
        fallback_provider = _other_provider(provider)
        log.warning("fallback: %s failed (%s); trying %s", provider, primary_error, fallback_provider)
        try:
            response = await _try(fallback_provider)
            was_fallback = True
            conversation_provider_map[request.conversation_id] = fallback_provider   
            log.info("fallback succeeded: conversation=%s re-pinned from %s to %s",
                     request.conversation_id, provider, fallback_provider)
        except InvalidRequestError:
            raise
        except Exception as fallback_error:
            log.error("both providers failed: %s: %s | %s: %s",
                      provider, primary_error, fallback_provider, fallback_error)
            raise Exception(f"Both providers failed. Primary: {primary_error}. Fallback: {fallback_error}")

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