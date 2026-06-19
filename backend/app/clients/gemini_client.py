import time
import google.generativeai as genai
from app.core.config import get_settings
from app.models.schemas import GatewayResponse, ComplexityLevel

settings = get_settings()

genai.configure(api_key=settings.GEMINI_API_KEY)
client = genai.GenerativeModel(settings.GEMINI_MODEL)


def calculate_cost(total_tokens: int) -> float:
    return (total_tokens / 1_000_000) * settings.GEMINI_COST_PER_MILLION_TOKENS


async def complete(query: str) -> GatewayResponse:
    try:
        start_time = time.time()

        response = client.generate_content(query)

        latency_ms = (time.time() - start_time) * 1000

        total_tokens = response.usage_metadata.total_token_count
        cost = calculate_cost(total_tokens)
        answer = response.text

        return GatewayResponse(
            response=answer,
            model_used=settings.GEMINI_MODEL,
            complexity=ComplexityLevel.COMPLEX,
            cost_usd=cost,
            latency_ms=latency_ms,
            cache_hit=False
        )

    except Exception as e:
        if "quota" in str(e).lower():
            raise Exception("Gemini quota exceeded")

        if "connection" in str(e).lower():
            raise Exception("Gemini connection failed")

        raise Exception(f"Gemini error: {str(e)}")