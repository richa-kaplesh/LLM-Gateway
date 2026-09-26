from app.clients import groq_client, gemini_client
from app.cache.cache import cache
from app.models.schemas import GatewayRequest, GatewayResponse
from app.router.bucket import get_provider_for_conversation, conversation_provider_map
from app.clients.exceptions import InvalidRequestError
import asyncio
from app.router.circuit_breaker import breakers

CLIENTS = {"groq": groq_client, "gemini": gemini_client}


async def _call_with_retry(client_module, request, attempts=2, delay=2.0):
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
    use_cache = request.tools is None and not request.is_tool_related
    if use_cache:
        cached = cache.get(request)
        if cached:
            return cached

    provider = get_provider_for_conversation(request.conversation_id)
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
            conversation_provider_map[request.conversation_id] = fallback_provider
        except InvalidRequestError:
            raise
        except Exception as fallback_error:
            raise Exception(f"Both providers failed. Primary: {primary_error}. Fallback: {fallback_error}")

    if use_cache:
        cache.set(request, response)
    return response


def _other_provider(provider: str) -> str:
    return "gemini" if provider == "groq" else "groq"