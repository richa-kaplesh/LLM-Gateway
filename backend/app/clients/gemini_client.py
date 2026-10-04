import json
import time
from google import genai
from google.genai import types
from app.core.config import get_settings
from app.models.schemas import GatewayRequest, GatewayResponse
from google.genai import errors as genai_errors
from app.clients.exceptions import ProviderUnavailableError, InvalidRequestError
import logging 
log = logging.getLogger(__name__)
settings = get_settings()

client = genai.Client(api_key=settings.GEMINI_API_KEY)


def calculate_cost(prompt_tokens: int, output_tokens: int) -> float:
    input_cost = (prompt_tokens / 1_000_000) * settings.GEMINI_INPUT_COST_PER_MILLION
    output_cost = (output_tokens / 1_000_000) * settings.GEMINI_OUTPUT_COST_PER_MILLION
    return input_cost + output_cost



def _parse_args(raw):
    """OpenAI-style tool_calls store arguments as a JSON string. Gemini
    wants a plain dict. Handle both in case the caller already parsed it."""
    if isinstance(raw, str):
        return json.loads(raw)
    return raw


def convert_messages(messages: list[dict]):
    """Translate OpenAI-shaped messages into Gemini's Content list.
    Gemini has no 'system' role, so system messages are pulled out
    separately and passed via system_instruction instead."""
    system_instruction = None
    contents = []

    for msg in messages:
        role = msg["role"]

        if role == "system":
            system_instruction = (system_instruction or "") + msg["content"] + "\n"

        elif role == "user":
            contents.append(types.Content(
                role="user",
                parts=[types.Part.from_text(text=msg["content"])]
            ))

        elif role == "assistant":
            if msg.get("tool_calls"):
                parts = []
                for call in msg["tool_calls"]:
                    part = types.Part.from_function_call(
                        name=call["function"]["name"],
                        args=_parse_args(call["function"]["arguments"])
                    )
                    part.thought_signature =call.get("thought_signature")
                    parts.append(part)
                
                contents.append(types.Content(role="model", parts=parts))
            else:
                contents.append(types.Content(
                    role="model",
                    parts=[types.Part.from_text(text=msg["content"])]
                ))

        elif role == "tool":
            contents.append(types.Content(
                role="user",
                parts=[types.Part.from_function_response(
                    name=msg.get("name", "unknown_function"),
                    response={"result": msg["content"]}
                )]
                ))

    return contents, system_instruction


def convert_tool_choice(tool_choice: str | None):
    """OpenAI: 'auto' / 'none' / 'required'. Gemini: AUTO / NONE / ANY."""
    if tool_choice is None:
        return None
    mode = {"auto": "AUTO", "none": "NONE", "required": "ANY"}.get(tool_choice, "AUTO")
    return types.ToolConfig(function_calling_config=types.FunctionCallingConfig(mode=mode))


def normalize_tool_calls(parts):
    """parts = response.candidates[0].content.parts — the full list,
    so we can grab each function call's thought_signature, not just
    the bare call."""
    calls = [p for p in parts if p.function_call]
    if not calls:
        return None

    result = []
    for i, part in enumerate(calls):
        call = part.function_call
        result.append({
            "id": f"call_{i}",
            "type": "function",
            "function": {
                "name": call.name,
                "arguments": json.dumps(call.args)
            },
            "thought_signature": part.thought_signature  
        })
    return result

def convert_tools(tools: list[dict] | None):
    """OpenAI shape: [{"type": "function", "function": {name, description, parameters}}]
    Gemini shape: Tool(function_declarations=[FunctionDeclaration(...)])"""
    if not tools:
        return None

    declarations = [
        types.FunctionDeclaration(
            name=t["function"]["name"],
            description=t["function"].get("description", ""),
            parameters=t["function"].get("parameters", {})
        )
        for t in tools
    ]
    return types.Tool(function_declarations=declarations)



async def complete(request: GatewayRequest) -> GatewayResponse:
    try:
        start_time = time.perf_counter()

        contents, system_instruction = convert_messages(request.messages)
        tool = convert_tools(request.tools)
        tool_config = convert_tool_choice(request.tool_choice)

        config_kwargs = {}
        if system_instruction:
            config_kwargs["system_instruction"] = system_instruction
        if tool:
            config_kwargs["tools"] = [tool]
        if tool_config:
            config_kwargs["tool_config"] = tool_config

        config = types.GenerateContentConfig(**config_kwargs) if config_kwargs else None

        response = await client.aio.models.generate_content(
            model=settings.GEMINI_MODEL,
            contents=contents,
            config=config
        )

        latency_ms = (time.perf_counter() - start_time) * 1000

        cost = calculate_cost(
            response.usage_metadata.prompt_token_count,
            response.usage_metadata.candidates_token_count
        )

        has_calls = bool(response.function_calls)
        answer = None if has_calls else response.text
        tool_calls = normalize_tool_calls(response.candidates[0].content.parts) if has_calls else None
        finish_reason = response.candidates[0].finish_reason if response.candidates else None

        return GatewayResponse(
            content=answer,
            tool_calls=tool_calls,
            finish_reason=str(finish_reason) if finish_reason else None,
            model_used=settings.GEMINI_MODEL,
            provider_used="gemini",
            cost_usd=cost,
            latency_ms=latency_ms,
            cache_hit=False
        )

    except genai_errors.ClientError as e:
        code = getattr(e, "code", None)
        if code == 429:
            raise ProviderUnavailableError("Gemini quota exceeded")
        raise InvalidRequestError(f"Gemini rejected the request: {str(e)}")
    except genai_errors.ServerError as e:
        raise ProviderUnavailableError(f"Gemini server error: {str(e)}")
    except Exception as e:
        log.error("Gemini unexpected error", exc_info=True)
        raise ProviderUnavailableError(f"Gemini error: {str(e)}")