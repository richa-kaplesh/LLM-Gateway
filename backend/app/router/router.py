from app.clients import groq_client, gemini_client
from app.cache.cache import get_cache_for
from app.models.schemas import GatewayRequest, GatewayResponse
from app.router.bucket import get_provider_for_conversation, conversation_provider_map, tpm_buckets
from app.tracker.tracker import tracker
import asyncio
from app.router.circuit_breaker import breakers
from app.clients.exceptions import InvalidRequestError, TooLongError, AllProvidersRateLimitedError
import tiktoken

_encoder = tiktoken.get_encoding("cl100k_base")

CLIENTS = {"groq": groq_client, "gemini": gemini_client}


def estimate_tokens(messages: list[dict]) -> int:
    text = " ".join(m.get("content", "") or "" for m in messages)
    return len(_encoder.encode(text))


async def _call_with_retry(client_module, request, attempts=3, delay=2.0):
    last_error = None
    for i in range(attempts):
        try:
            return await client_module.complete(request)
        except InvalidRequestError:
            raise
        except Exception as e:
            last_error = e
            if i < attempts - 1:
                await asyncio.sleep(delay)
    raise last_error


async def route(request: GatewayRequest) -> GatewayResponse:
    cache = get_cache_for(request)
    if cache:
        cached = cache.get(request)
        if cached:
            return cached

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
            raise Exception(f"{p} circuit is open, skipping")

        state_before = breaker.state
        try:
            resp = await _call_with_retry(CLIENTS[p], request)
            breaker.record_success()
            return resp
        except InvalidRequestError:
            breaker.record_inconclusive()
            raise
        except Exception:
            breaker.record_failure()
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
        try:
            response = await _try(fallback_provider)
            was_fallback = True
            conversation_provider_map[request.conversation_id] = fallback_provider   # re-pin
        except InvalidRequestError:
            raise
        except Exception as fallback_error:
            raise Exception(f"Both providers failed. Primary: {primary_error}. Fallback: {fallback_error}")

    response.was_fallback = was_fallback

    if cache:
        cache.set(request, response)
    return response


def _other_provider(provider: str) -> str:
    return "gemini" if provider == "groq" else "groq"