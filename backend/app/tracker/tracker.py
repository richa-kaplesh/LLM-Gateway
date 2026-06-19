from dataclasses import dataclass, field
from datetime import datetime
from app.models.schemas import GatewayResponse


@dataclass
class RequestLog:
    user_id: str
    query: str
    model_used: str
    complexity: str
    cost_usd: float
    latency_ms: float
    cache_hit: bool
    timestamp: datetime = field(default_factory=datetime.utcnow)


class CostTracker:
    def __init__(self):
        self.logs: list[RequestLog] = []

    def log(self, user_id: str, query: str, response: GatewayResponse) -> None:
        log_entry = RequestLog(
            user_id=user_id,
            query=query,
            model_used=response.model_used,
            complexity=response.complexity,
            cost_usd=response.cost_usd,
            latency_ms=response.latency_ms,
            cache_hit=response.cache_hit
        )
        self.logs.append(log_entry)

    def get_user_stats(self, user_id: str) -> dict:
        user_logs = [l for l in self.logs if l.user_id == user_id]

        if not user_logs:
            return {"user_id": user_id, "message": "no requests found"}

        total_requests = len(user_logs)
        total_cost = sum(l.cost_usd for l in user_logs)
        cache_hits = sum(1 for l in user_logs if l.cache_hit)
        avg_latency = sum(l.latency_ms for l in user_logs) / total_requests

        # Cost saved = requests that hit cache * average LLM cost
        avg_llm_cost = (
            sum(l.cost_usd for l in user_logs if not l.cache_hit) /
            max(sum(1 for l in user_logs if not l.cache_hit), 1)
        )
        cost_saved = cache_hits * avg_llm_cost

        return {
            "user_id": user_id,
            "total_requests": total_requests,
            "total_cost_usd": round(total_cost, 6),
            "cache_hits": cache_hits,
            "cache_hit_rate": round(cache_hits / total_requests * 100, 2),
            "cost_saved_usd": round(cost_saved, 6),
            "avg_latency_ms": round(avg_latency, 2)
        }

    def get_global_stats(self) -> dict:
        if not self.logs:
            return {"message": "no requests yet"}

        total_requests = len(self.logs)
        total_cost = sum(l.cost_usd for l in self.logs)
        cache_hits = sum(1 for l in self.logs if l.cache_hit)
        groq_requests = sum(1 for l in self.logs if "llama" in l.model_used)
        gemini_requests = sum(1 for l in self.logs if "gemini" in l.model_used)

        avg_llm_cost = (
            sum(l.cost_usd for l in self.logs if not l.cache_hit) /
            max(sum(1 for l in self.logs if not l.cache_hit), 1)
        )
        cost_saved = cache_hits * avg_llm_cost

        return {
            "total_requests": total_requests,
            "total_cost_usd": round(total_cost, 6),
            "cache_hits": cache_hits,
            "cache_hit_rate": round(cache_hits / total_requests * 100, 2),
            "cost_saved_usd": round(cost_saved, 6),
            "groq_requests": groq_requests,
            "gemini_requests": gemini_requests
        }


# Single instance shared across entire app
tracker = CostTracker()