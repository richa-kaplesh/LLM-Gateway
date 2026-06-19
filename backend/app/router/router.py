from app.clients import groq_client, gemini_client
from app.cache import cache
from app.models.schemas import GatewayResponse, ComplexityLevel
from app.core.config import get_settings

settings = get_settings()


def classify_complexity(query: str) -> ComplexityLevel:
    word_count = len(query.strip().split())

    if word_count <= settings.SIMPLE_QUERY_WORD_LIMIT:
        return ComplexityLevel.SIMPLE
    return ComplexityLevel.COMPLEX


async def route(query: str, user_id: str) -> GatewayResponse:
    cached = cache.get(query)
    if cached:
        return cached

    complexity = classify_complexity(query)

    try:
        if complexity == ComplexityLevel.SIMPLE:
            response = await groq_client.complete(query)
        else:
            response = await gemini_client.complete(query)

    except Exception as groq_or_gemini_error:
        try:
            if complexity == ComplexityLevel.SIMPLE:
                response = await gemini_client.complete(query)
            else:
                response = await groq_client.complete(query)
        except Exception as fallback_error:
            raise Exception(f"Both providers failed: {fallback_error}")

    cache.set(query, response)

    return response