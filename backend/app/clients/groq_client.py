import time
import groq
from app.core.config import get_settings
from app.models.schemas import GatewayRequest, GatewayResponse

settings = get_settings()

client = groq.Groq(api_key=settings.GROQ_API_KEY)


def calculate_cost(prompt_tokens: int, completion_tokens: int) -> float:
    input_cost = (prompt_tokens / 1_000_000) * settings.GROQ_INPUT_COST_PER_MILLION
    output_cost = (completion_tokens / 1_000_000) * settings.GROQ_OUTPUT_COST_PER_MILLION
    return input_cost + output_cost

async def complete(request: GatewayRequest) -> GatewayResponse:
    try:
        start_time = time.time()

        kwargs = {
            "model": settings.GROQ_MODEL,
            "messages": request.messages,
        }
        if request.tools is not None:
            kwargs["tools"] = request.tools
        if request.tool_choice is not None:
            kwargs["tool_choice"] = request.tool_choice

        response = client.chat.completions.create(**kwargs)

        latency_ms = (time.time() - start_time) * 1000
        total_tokens = response.usage.total_tokens
        cost = calculate_cost(total_tokens)

        message = response.choices[0].message
        answer = message.content
        tool_calls = message.tool_calls
        finish_reason = response.choices[0].finish_reason

        return GatewayResponse(
            content=answer,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            model_used=settings.GROQ_MODEL,
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