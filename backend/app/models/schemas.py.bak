import base64
from pydantic import BaseModel, field_serializer
from typing import Any, Optional
from enum import Enum


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
    cache_scope: Optional[str] = None   # "conversation" | "global" | None


class GatewayResponse(BaseModel):
    content: Optional[str] = None
    tool_calls: Optional[list[dict]] = None
    finish_reason: Optional[str] = None
    model_used: str
    provider_used: str
    cost_usd: float
    latency_ms: float
    cache_hit: bool
    was_fallback: bool = False
    embed_latency_ms: float = 0.0   # part of latency_ms spent on the cache's embedding call
    embed_cost_usd: float = 0.0     # part of cost_usd spent on the cache's embedding call

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
    groq_available: bool
    gemini_available: bool