import json
import re
import time
import asyncio
from google import genai
from google.genai import types
from app.core.config import get_settings
from app.models.schemas import GatewayRequest, GatewayResponse
from google.genai import errors as genai_errors
from app.clients.exceptions import (
    ProviderUnavailableError, ProviderConnectionError, InvalidRequestError,
    ProviderRateLimitedError, ProviderKeyError,
)
import logging
log = logging.getLogger(__name__)
settings = get_settings()

# ── Network / timeout error detector ──────────────────────────────────────
# httpcore.ReadTimeout (and similar) bubble up through the Google SDK as a
# plain Exception — we detect them by name so we don't have to import httpcore.
_NETWORK_ERROR_KEYWORDS = (
    "ReadTimeout", "WriteTimeout", "ConnectTimeout", "PoolTimeout",
    "TimeoutException",   # httpcore base
    "ConnectError", "RemoteProtocolError",
    "ReadError", "WriteError",
)


def _is_network_error(exc: BaseException) -> bool:
    """Return True if exc (or any exception in its cause chain) looks like a
    network-level timeout or connection failure from httpcore / httpx.

    We walk __cause__ and __context__ so exceptions wrapped by the SDK's
    tenacity retry layer are also caught.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        name = type(current).__name__
        module = getattr(type(current), "__module__", "") or ""
        if name in _NETWORK_ERROR_KEYWORDS:
            return True
        # Also catch asyncio.TimeoutError and built-in TimeoutError
        if isinstance(current, (asyncio.TimeoutError, TimeoutError, ConnectionError)):
            return True
        # Any exception from the httpcore / httpx namespace with "Timeout" in name
        if ("httpcore" in module or "httpx" in module) and (
            "Timeout" in name or "Connection" in name or "Read" in name
        ):
            return True
        next_exc = getattr(current, "__cause__", None)
        if next_exc is None:
            next_exc = getattr(current, "__context__", None)
        current = next_exc
    return False


def _make_client(api_key: str) -> genai.Client:
    # timeout is in milliseconds; without one a hung connection hangs the request
    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=int(settings.PROVIDER_TIMEOUT_SECONDS * 1000)),
    )


def _is_bad_key(e: Exception) -> bool:
    """Gemini reports an invalid or expired key as HTTP 400 (reason API_KEY_INVALID),
    not 401, so check the message too."""
    msg = str(e)
    return "API_KEY_INVALID" in msg or "API key not valid" in msg or "API key expired" in msg


def _retry_after_seconds(e: Exception) -> float | None:
    """Gemini 429s carry a RetryInfo block ("retryDelay": "28s") in the error details."""
    try:
        details = getattr(e, "details", None)
        items = details.get("error", {}).get("details", []) if isinstance(details, dict) else []
        for item in items:
            delay = item.get("retryDelay") if isinstance(item, dict) else None
            if delay:
                return float(str(delay).rstrip("s"))
    except (AttributeError, TypeError, ValueError):
        pass
    m = re.search(r"retry in ([\d.]+)\s*s", str(e), re.IGNORECASE)
    return float(m.group(1)) if m else None


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



async def complete(request: GatewayRequest, api_key: str | None = None) -> GatewayResponse:
    """Call Gemini.  Pass ``api_key`` to use a specific key; omit to fall back
    to the single legacy key in settings (backward-compatible behaviour)."""
    key = api_key or settings.GEMINI_API_KEY
    client = _make_client(key)
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
            retry_after = _retry_after_seconds(e)
            log.warning("Gemini 429 (retry_after=%s): %s", retry_after, str(e)[:300])
            raise ProviderRateLimitedError(f"Gemini quota exceeded: {str(e)[:300]}", retry_after=retry_after)
        if code in (401, 403, 404) or _is_bad_key(e):
            # Bad/expired key, no access to this model for this key's project, or a wrong
            # GEMINI_MODEL. Treated per KEY: the router benches this key and tries the next.
            # If every key fails this way the final error says so (and names the cause).
            log.error("Gemini key unusable (HTTP %s) with GEMINI_MODEL=%s: %s",
                      code, settings.GEMINI_MODEL, str(e)[:300])
            raise ProviderKeyError(f"Gemini key unusable (HTTP {code}): {str(e)[:300]}")
        if code == 408:
            # Timeout: the network timed out, not a server problem. The other
            # provider may respond fine — treat as a transient connection error.
            raise ProviderConnectionError(f"Gemini request timed out (HTTP 408): {str(e)[:300]}")
        raise InvalidRequestError(f"Gemini rejected the request: {str(e)}")
    except genai_errors.ServerError as e:
        # 5xx (e.g. 503 "model is experiencing high demand") is a temporary Google-side
        # problem. Treat it like a connection error so the router falls back to Groq.
        raise ProviderConnectionError(f"Gemini server error (HTTP {getattr(e, 'code', '5xx')}): {str(e)[:300]}")
    except Exception as e:
        # httpcore.ReadTimeout and other network/connection errors bubble up
        # here as plain Exceptions (the SDK wraps them without re-typing).
        # Detect by walking the cause chain — if it's a network issue, raise
        # ProviderConnectionError so the router falls back to Groq instead of
        # stopping with a 503.
        if _is_network_error(e):
            log.warning("Gemini network/timeout error (will try Groq fallback): %s: %s",
                        type(e).__name__, e)
            raise ProviderConnectionError(f"Gemini network error: {e}") from e
        log.error("Gemini unexpected error", exc_info=True)
        raise ProviderUnavailableError(f"Gemini error: {str(e)}")