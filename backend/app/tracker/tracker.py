from app.models.schemas import GatewayResponse, CostSummary
from app.core.db import get_pool
import logging
log = logging.getLogger(__name__)

class CostTracker:
    async def log(self, user_id: str, conversation_id: str, response: GatewayResponse | None,
                   status: str, error_type: str | None = None,
                   cache_scope: str | None = None, estimated_tokens: int | None = None) -> None:
        pool = get_pool()

        # embed_latency_ms defaults to 0.0 when no embedding call happened
        # (no cache scope, or the request errored). Store NULL in that case,
        # so averages are not dragged down by fake zeros.
        embedded = bool(response and response.embed_latency_ms)
        embed_latency = response.embed_latency_ms if embedded else None
        embed_cost = response.embed_cost_usd if embedded else None

        await pool.execute(
            """INSERT INTO request_logs
               (user_id, conversation_id, provider_used, model_used, was_fallback,
                cost_usd, latency_ms, estimated_tokens, cache_hit, cache_scope,
                status, error_type, embed_latency_ms, embed_cost_usd)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)""",
            user_id, conversation_id,
            response.provider_used if response else None,
            response.model_used if response else None,
            response.was_fallback if response else False,
            response.cost_usd if response else 0,
            response.latency_ms if response else None,
            estimated_tokens,
            response.cache_hit if response else False,
            cache_scope,
            status, error_type,
            embed_latency, embed_cost,
        )
        
    async def log_breaker_transition(self, provider: str, old_state: str, new_state: str) -> None:
        log.warning("circuit breaker %s: %s -> %s", provider, old_state, new_state)
        pool = get_pool()
        await pool.execute(
            "INSERT INTO breaker_events (provider, old_state, new_state) VALUES ($1,$2,$3)",
            provider, old_state, new_state,
        )

    async def get_global_stats(self) -> dict:
        pool = get_pool()
        row = await pool.fetchrow(
            """SELECT
                 count(*) AS total_requests,
                 coalesce(sum(cost_usd), 0) AS total_cost,
                 count(*) FILTER (WHERE cache_hit) AS cache_hits,
                 count(*) FILTER (WHERE status = 'error') AS errors,
                 count(*) FILTER (WHERE provider_used = 'groq') AS groq_requests,
                 count(*) FILTER (WHERE provider_used = 'gemini') AS gemini_requests,
                 count(*) FILTER (WHERE was_fallback) AS fallback_count,
                 coalesce(avg(cost_usd) FILTER (WHERE NOT cache_hit), 0) AS avg_llm_cost
               FROM request_logs"""
        )
        if row["total_requests"] == 0:
            return {"message": "no requests yet"}
        total = row["total_requests"]
        return {
            "total_requests": total,
            "total_cost_usd": round(float(row["total_cost"]), 6),
            "cache_hits": row["cache_hits"],
            "cache_hit_rate": round(row["cache_hits"] / total * 100, 2),
            "cost_saved_usd": round(row["cache_hits"] * float(row["avg_llm_cost"]), 6),
            "groq_requests": row["groq_requests"],
            "gemini_requests": row["gemini_requests"],
            "fallback_count": row["fallback_count"],
            "error_count": row["errors"],
            "error_rate_pct": round(row["errors"] / total * 100, 2),
        }

    async def get_user_stats(self, user_id: str) -> CostSummary | None:
        pool = get_pool()
        row = await pool.fetchrow(
            """SELECT
                 count(*) AS total_requests,
                 coalesce(sum(cost_usd), 0) AS total_cost,
                 count(*) FILTER (WHERE cache_hit) AS cache_hits,
                 coalesce(avg(latency_ms), 0) AS avg_latency,
                 coalesce(avg(cost_usd) FILTER (WHERE NOT cache_hit), 0) AS avg_llm_cost
               FROM request_logs WHERE user_id = $1""",
            user_id,
        )
        if row["total_requests"] == 0:
            return None
        total = row["total_requests"]
        return CostSummary(
            user_id=user_id, total_requests=total,
            total_cost_usd=round(float(row["total_cost"]), 6),
            cache_hits=row["cache_hits"],
            cache_hit_rate=round(row["cache_hits"] / total * 100, 2),
            cost_saved_usd=round(row["cache_hits"] * float(row["avg_llm_cost"]), 6),
            avg_latency_ms=round(float(row["avg_latency"]), 2),
        )

    async def get_recent_requests(self, limit: int = 200) -> list[dict]:
        pool = get_pool()
        rows = await pool.fetch(
            """SELECT timestamp, cost_usd, latency_ms, model_used, provider_used,
                      cache_hit, status, error_type
               FROM request_logs ORDER BY timestamp DESC LIMIT $1""",
            limit,
        )
        return [dict(r) for r in rows]


tracker = CostTracker()