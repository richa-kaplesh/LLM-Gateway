from app.clients import groq_client, gemini_client
from app.cache.cache import cache
from app.models.schemas import GatewayRequest, GatewayResponse
from app.router.bucket import get_provider_for_conversation, conversation_provider_map
from app.clients.exceptions import InvalidRequestError

CLIENTS = {"groq": groq_client, "gemini": gemini_client}


def _other_provider(provider: str) -> str:
    return "gemini" if provider == "groq" else "groq"


async def route(request: GatewayRequest) -> GatewayResponse:
    use_cache = request.tools is None

    if use_cache:
        cached = await cache.get(request)
        if cached:
            return cached

    provider = get_provider_for_conversation(request.conversation_id)
    if provider is None:
        raise Exception("Both providers are rate-limited right now. Try again shortly.")

    try:
        response = await CLIENTS[provider].complete(request)
    except InvalidRequestError:
        raise
    except Exception as primary_error:
        fallback_provider = _other_provider(provider)
        try:
            response = await CLIENTS[fallback_provider].complete(request)
            conversation_provider_map[request.conversation_id] = fallback_provider
        except InvalidRequestError:
            raise
        except Exception as fallback_error:
            raise Exception(
                f"Both providers failed. Primary ({provider}): {primary_error}. "
                f"Fallback ({fallback_provider}): {fallback_error}"
            )

    if use_cache:
        await cache.set(request, response)

    return response