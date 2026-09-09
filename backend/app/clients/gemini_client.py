import json
import time
from google import genai
from google.genai import types
from app.core.config import get_settings
from app.models.schemas import GatewayRequest, GatewayResponse

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
                parts = [
                    types.Part.from_function_call(
                        name=call["function"]["name"],
                        args=_parse_args(call["function"]["arguments"])
                    )
                    for call in msg["tool_calls"]
                ]
                contents.append(types.Content(role="model", parts=parts))
            else:
                contents.append(types.Content(
                    role="model",
                    parts=[types.Part.from_text(text=msg["content"])]
                ))

        elif role == "tool":
            contents.append(types.Content(
                role="tool",
                parts=[types.Part.from_function_response(
                    name=msg.get("name", "unknown_function"),
                    response={"result": msg["content"]}
                )]
            ))

    return contents, system_instruction


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


def convert_tool_choice(tool_choice: str | None):
    """OpenAI: 'auto' / 'none' / 'required'. Gemini: AUTO / NONE / ANY."""
    if tool_choice is None:
        return None
    mode = {"auto": "AUTO", "none": "NONE", "required": "ANY"}.get(tool_choice, "AUTO")
    return types.ToolConfig(function_calling_config=types.FunctionCallingConfig(mode=mode))


def normalize_tool_calls(function_calls):
    """Convert Gemini's FunctionCall objects into the same OpenAI-shaped
    dicts groq_client.py should also be producing — so GatewayResponse.tool_calls
    has ONE consistent shape no matter which provider answered."""
    if not function_calls:
        return None

    return [
        {
            "id": f"call_{i}",
            "type": "function",
            "function": {
                "name": call.name,
                "arguments": json.dumps(call.args)
            }
        }
        for i, call in enumerate(function_calls)
    ]


async def complete(request: GatewayRequest) -> GatewayResponse:
    try:
        start_time = time.time()

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

        response = client.models.generate_content(
            model=settings.GEMINI_MODEL,
            contents=contents,
            config=config
        )

        latency_ms = (time.time() - start_time) * 1000

        cost = calculate_cost(
            response.usage_metadata.prompt_token_count,
            response.usage_metadata.candidates_token_count
        )

        has_calls = bool(response.function_calls)
        answer = None if has_calls else response.text
        tool_calls = normalize_tool_calls(response.function_calls) if has_calls else None
        finish_reason = response.candidates[0].finish_reason if response.candidates else None

        return GatewayResponse(
            content=answer,
            tool_calls=tool_calls,
            finish_reason=str(finish_reason) if finish_reason else None,
            model_used=settings.GEMINI_MODEL,
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