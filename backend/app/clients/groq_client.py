import time
import groq
from app.core.config import get_settings
from app.models.schemas import GatewayRequest, GatewayResponse
from app.clients.exceptions import ProviderUnavailableError, InvalidRequestError

settings = get_settings()

client = groq.Groq(api_key=settings.GROQ_API_KEY)


def calculate_cost(prompt_tokens: int, completion_tokens: int) -> float:
    input_cost = (prompt_tokens / 1_000_000) * settings.GROQ_INPUT_COST_PER_MILLION
    output_cost = (completion_tokens / 1_000_000) * settings.GROQ_OUTPUT_COST_PER_MILLION
    return input_cost + output_cost


def normalize_tool_calls(tool_calls):
    if not tool_calls:
        return None

    return [
        {
            "id": call.id,
            "type": "function",
            "function": {
                "name": call.function.name,
                "arguments": call.function.arguments  # Groq already gives this as a JSON string
            }
        }
        for call in tool_calls
    ]


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
        cost = calculate_cost(
            response.usage.prompt_tokens,
            response.usage.completion_tokens
        )

        message = response.choices[0].message
        answer = message.content
        tool_calls = normalize_tool_calls(message.tool_calls)
        finish_reason = response.choices[0].finish_reason

        return GatewayResponse(
            content=answer,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            model_used=settings.GROQ_MODEL,
            provider_used="groq",
            cost_usd=cost,
            latency_ms=latency_ms,
            cache_hit=False
        )

    except groq.RateLimitError:
        raise ProviderUnavailableError("Groq rate limit hit")
    except groq.APIConnectionError:
        raise ProviderUnavailableError("Groq connection failed")
    except groq.BadRequestError as e:
        code = (getattr(e, "body", None) or {}).get("error", {}).get("code")
        if code == "tool_use_failed":
            # The model itself produced invalid JSON for its tool call arguments
            # (usually unescaped quotes in generated code). This is Groq's model
            # glitching, not a malformed request from us, so it's worth retrying
            # instead of failing immediately — router._call_with_retry will retry
            # this call, and fail over to Gemini if it keeps happening.
            raise ProviderUnavailableError(f"Groq failed to generate a valid tool call: {str(e)}")
        raise InvalidRequestError(f"Groq rejected the request: {str(e)}")
    except Exception as e:
        raise ProviderUnavailableError(f"Groq error: {str(e)}")