from pydantic import BaseModel
from typing import Optional
from enum import Enum


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