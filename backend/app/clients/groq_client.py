import time
import groq
from app.core.config import get_settings
from app.models.schemas import GatewayResponse, ComplexityLevel

settings = get_settings()

client = groq.Groq(api_key=settings.GROQ_API_KEY)


def calculate_cost(total_tokens: int) -> float:
    return (total_tokens / 1_000_000) * settings.GROQ_COST_PER_MILLION_TOKENS


async def complete(query: str) -> GatewayResponse:
    try:
        start_time = time.time()
        response = client.chat.completions.create(
            model=settings.GROQ_MODEL,
            messages=[
                {"role": "user", "content": query}
            ]
        )
        latency_ms = (time.time() - start_time) * 1000
        total_tokens = response.usage.total_tokens
        cost = calculate_cost(total_tokens)
        answer = response.choices[0].message.content

        # DEBUG - move these before return
        print("USAGE:", response.usage)
        print("TOKENS:", response.usage.total_tokens)
        print("COST:", cost)

        return GatewayResponse(
            response=answer,
            model_used=settings.GROQ_MODEL,
            complexity=ComplexityLevel.SIMPLE,
            cost_usd=cost,
            latency_ms=latency_ms,
            cache_hit=False
        )

    except groq.RateLimitError:
        raise Exception("Groq rate limit hit")
    except groq.APIConnectionError:
        raise Exception("Groq connection failed")
    except Exception as e:
        raise Exception(f"Groq error: {str(e)}")
