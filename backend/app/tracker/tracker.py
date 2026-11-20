from app.models.schemas import GatewayResponse, CostSummary
from app.core.db import get_pool, has_pool
from app.router.attempt_log import AttemptLog
import logging
log = logging.getLogger(__name__)

class CostTracker:
    # Set True by metrics.ensure_tables() once the token columns exist in request_logs. Until
    # then (or if the migration could not run) rows are written WITHOUT them, so logging a
    # request can never fail just because the schema is one step behind the code.
    token_columns = False
    attempt_columns = False

    async def log(self, user_id: str, conversation_id: str, response: GatewayResponse | None,
                   status: str, error_type: str | None = None,
                   cache_scope: str | None = None, estimated_tokens: int | None = None,
                   request_id: str | None = None, attempts: AttemptLog | None = None) -> None:
        # embed_latency_ms defaults to 0.0 when no embedding call happened
        # (no cache scope, or the request errored). Store NULL in that case,
        # so averages are not dragged down by fake zeros.
        if not has_pool():
            return
        embedded = bool(response and response.embed_latency_ms)

        row = {
            "user_id": user_id,
            "conversation_id": conversation_id,
            "provider_used": response.provider_used if response else None,
            "model_used": response.model_used if response else None,
            "was_fallback": response.was_fallback if response else False,
            "cost_usd": response.cost_usd if response else 0,
            "latency_ms": response.latency_ms if response else None,
            "estimated_tokens": estimated_tokens,
            "cache_hit": response.cache_hit if response else False,
            "cache_scope": cache_scope,
            "status": status,
            "error_type": error_type,
            "embed_latency_ms": response.embed_latency_ms if embedded else None,
            "embed_cost_usd": response.embed_cost_usd if embedded else None,
        }
        if self.token_columns:
            row.update({
                "input_tokens": response.input_tokens if response else None,
                "output_tokens": response.output_tokens if response else None,
                "thought_tokens": response.thought_tokens if response else None,
                "cached_tokens": response.cached_tokens if response else None,
            })
        if self.attempt_columns:
            row.update({
                "request_id": request_id,
                "deployment_id": attempts.final_deployment_id if attempts else None,
                "attempts": attempts.count if attempts else None,
                "capacity_existed": attempts.capacity_existed if attempts else None,
            })

        columns = ", ".join(row)
        placeholders = ", ".join(f"${i}" for i in range(1, len(row) + 1))
        attempt_rows = [
            (request_id, a.attempt_no, a.deployment_id, a.provider, a.model,
             a.outcome, a.error_type, a.latency_ms, a.retry_after_s, a.error_detail)
            for a in (attempts.attempts if attempts else [])
        ] if self.attempt_columns and request_id else []

        async with get_pool().acquire() as conn, conn.transaction():
            await conn.execute(f"INSERT INTO request_logs ({columns}) VALUES ({placeholders})",
                               *row.values())
            if attempt_rows:
                await conn.executemany(
                    """INSERT INTO attempt_logs
                       (request_id, attempt_no, deployment_id, provider, model,
                        outcome, error_type, latency_ms, retry_after_s, error_detail)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)""",
                    attempt_rows,
                )

    async def log_breaker_transition(self, provider: str, old_state: str, new_state: str) -> None:
        log.warning("circuit breaker %s: %s -> %s", provider, old_state, new_state)
        if not has_pool():
            return
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
                 count(*) FILTER (WHERE status = 'success') AS successes,
                 count(*) FILTER (WHERE status = 'success' AND cache_hit) AS cache_hits,
                 count(*) FILTER (WHERE status = 'error') AS errors,
                 count(*) FILTER (WHERE status = 'success' AND provider_used = 'groq') AS groq_requests,
                 count(*) FILTER (WHERE status = 'success' AND provider_used = 'gemini') AS gemini_requests,
                 count(*) FILTER (WHERE status = 'success' AND was_fallback) AS fallback_count,
                 coalesce(avg(cost_usd) FILTER (WHERE status = 'success' AND NOT cache_hit), 0) AS avg_llm_cost,
                 coalesce(avg(cost_usd) FILTER (WHERE status = 'success' AND cache_hit
                                                AND latency_ms > 0), 0) AS avg_hit_cost,
                 count(*) FILTER (WHERE status = 'success' AND cache_hit
                                  AND latency_ms > 0) AS real_hits
               FROM request_logs"""
        )
        if row["total_requests"] == 0:
            return {"message": "no requests yet"}
        total = row["total_requests"]
        ok = row["successes"]
        # hit rate is over successful requests (a failed request could never have been a hit);
        # saving per hit = what a miss costs minus what a hit costs (the embedding call)
        per_hit = max(0.0, float(row["avg_llm_cost"]) - float(row["avg_hit_cost"]))
        return {
            "total_requests": total,
            "total_cost_usd": round(float(row["total_cost"]), 6),
            "cache_hits": row["cache_hits"],
            "cache_hit_rate": round(row["cache_hits"] / ok * 100, 2) if ok else 0.0,
            "cost_saved_usd": round(row["real_hits"] * per_hit, 6),
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
                 count(*) FILTER (WHERE status = 'success') AS successes,
                 count(*) FILTER (WHERE status = 'success' AND cache_hit) AS cache_hits,
                 coalesce(avg(latency_ms) FILTER (WHERE status = 'success'), 0) AS avg_latency,
                 coalesce(avg(cost_usd) FILTER (WHERE status = 'success' AND NOT cache_hit), 0) AS avg_llm_cost,
                 coalesce(avg(cost_usd) FILTER (WHERE status = 'success' AND cache_hit
                                                AND latency_ms > 0), 0) AS avg_hit_cost,
                 count(*) FILTER (WHERE status = 'success' AND cache_hit
                                  AND latency_ms > 0) AS real_hits
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
            cache_hit_rate=round(row["cache_hits"] / row["successes"] * 100, 2) if row["successes"] else 0.0,
            cost_saved_usd=round(row["real_hits"] * max(0.0, float(row["avg_llm_cost"]) - float(row["avg_hit_cost"])), 6),
            avg_latency_ms=round(float(row["avg_latency"]), 2),
        )

    async def get_recent_requests(self, limit: int = 200) -> list[dict]:
        pool = get_pool()
        rows = await pool.fetch(
            """SELECT timestamp, cost_usd, latency_ms, model_used, provider_used,
                      cache_hit, status, error_type
               FROM request_logs WHERE status = 'success' AND latency_ms IS NOT NULL
               ORDER BY timestamp DESC LIMIT $1""",
            limit,
        )
        return [dict(r) for r in rows]


tracker = CostTracker()