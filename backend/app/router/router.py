from app.clients import groq_client, gemini_client
from app.cache.cache import get_cache_for
from app.models.schemas import GatewayRequest, GatewayResponse
from app.router.bucket import select_provider, tpm_buckets
from app.clients.exceptions import InvalidRequestError, TooLongError
import asyncio
from app.router.circuit_breaker import breakers
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

    provider = select_provider(estimated_tokens)
    if provider is None:
        raise Exception("Both providers are rate-limited right now. Try again shortly.")

    async def _try(p: str):
        if not breakers[p].allow_request():
            raise Exception(f"{p} circuit is open, skipping")
        try:
            resp = await _call_with_retry(CLIENTS[p], request)
            breakers[p].record_success()
            return resp
        except InvalidRequestError:
            breakers[p].record_inconclusive()
            raise
        except Exception:
            breakers[p].record_failure()
            raise

    try:
        response = await _try(provider)
    except InvalidRequestError:
        raise
    except Exception as primary_error:
        fallback_provider = _other_provider(provider)
        try:
            response = await _try(fallback_provider)
        except InvalidRequestError:
            raise
        except Exception as fallback_error:
            raise Exception(f"Both providers failed. Primary: {primary_error}. Fallback: {fallback_error}")

    if cache:
        cache.set(request, response)
    return response


def _other_provider(provider: str) -> str:
    return "gemini" if provider == "groq" else "groq"