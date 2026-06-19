from pydantic import BaseModel
from typing import Optional
from enum import Enum


class ComplexityLevel(str, Enum):
    SIMPLE = "simple"
    COMPLEX = "complex"


class GatewayRequest(BaseModel):
    query: str
    user_id: str
    stream: bool = False


class GatewayResponse(BaseModel):
    response: str
    model_used: str
    complexity: ComplexityLevel
    cost_usd: float
    latency_ms: float
    cache_hit: bool


class CostSummary(BaseModel):
    user_id: str
    total_requests: int
    total_cost_usd: float
    cache_hits: int
    cost_saved_usd: float


class HealthCheck(BaseModel):
    status: str
    groq_available: bool
    gemini_available: bool