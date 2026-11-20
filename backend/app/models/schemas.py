import base64
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_serializer, field_validator

MESSAGE_ROLES = {"system", "user", "assistant", "tool", "developer"}
MAX_MESSAGES = 500


def _jsonable(obj: Any) -> Any:
    """Recursively make a value JSON-safe. `bytes` (e.g. Gemini's thought_signature)
    cannot be JSON-encoded: Pydantic tries UTF-8 and raises 'invalid utf-8 sequence'.
    Encode it as base64 text instead."""
    if isinstance(obj, (bytes, bytearray)):
        return base64.b64encode(bytes(obj)).decode("ascii")
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    return obj


class GatewayRequest(BaseModel):
    conversation_id: str
    user_id: str
    messages: list[dict]
    tools: Optional[list[dict]] = None
    tool_choice: Optional[str] = None
    stream: bool = False
    is_tool_related: bool = False
    cache_scope: Optional[Literal["conversation", "global"]] = None
    tier: Optional[Literal["smart", "fast"]] = None

    @field_validator("messages")
    @classmethod
    def _valid_messages(cls, messages: list[dict]) -> list[dict]:
        if not messages:
            raise ValueError("messages must not be empty")
        if len(messages) > MAX_MESSAGES:
            raise ValueError(f"at most {MAX_MESSAGES} messages are allowed")
        for i, m in enumerate(messages):
            if m.get("role") not in MESSAGE_ROLES:
                raise ValueError(f"messages[{i}].role must be one of {sorted(MESSAGE_ROLES)}")
            content = m.get("content")
            if content is not None and not isinstance(content, (str, list)):
                raise ValueError(f"messages[{i}].content must be a string, a list of parts, or null")
        return messages


class GatewayResponse(BaseModel):
    content: Optional[str] = None
    tool_calls: Optional[list[dict]] = None
    finish_reason: Optional[str] = None
    model_used: str
    provider_used: str
    deployment_id: Optional[str] = None
    tier: Optional[str] = None
    attempts: int = 1
    cost_usd: float
    latency_ms: float
    cache_hit: bool
    was_fallback: bool = False
    embed_latency_ms: float = 0.0   # part of latency_ms spent on the cache's embedding call
    embed_cost_usd: float = 0.0     # part of cost_usd spent on the cache's embedding call
    # Real token counts as reported by the provider (None on a cache hit: no provider call)
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None    # includes thinking tokens, which providers bill as output
    thought_tokens: Optional[int] = None   # the thinking part of output_tokens (Gemini)
    cached_tokens: Optional[int] = None    # prompt tokens served from the provider's prompt cache
    price_known: bool = True               # False when the model has no entry in the rate card
    rate_limit: Optional[dict] = Field(default=None, exclude=True)

    @field_serializer("tool_calls")
    def _serialize_tool_calls(self, value: Optional[list[dict]]):
        # Safety net: tool_calls is list[dict], so Pydantic accepts ANY value inside it,
        # including bytes, and only fails later at JSON-encoding time (-> HTTP 500).
        return _jsonable(value) if value is not None else None


class CostSummary(BaseModel):
    user_id: str
    total_requests: int
    total_cost_usd: float
    cache_hits: int
    cache_hit_rate: float
    cost_saved_usd: float
    avg_latency_ms: float


class HealthCheck(BaseModel):
    status: str
    deployments: list[str]
