"""Baseline metrics: every number is computed from the rows the gateway really logged
(request_logs, breaker_events). Nothing is estimated except `est_saved_usd`, which is
labelled as an estimate in the UI. A metric with no data is None ("not recorded"), never 0.

Conventions
  * errors are rows with status = 'error'; every other rate is over SUCCESSFUL requests
  * latency / cost statistics exclude errors (they never reached a provider with a result)
  * provider latency/cost exclude cache hits (a hit never called the provider)
  * cache-hit rows with latency 0 predate accurate accounting; they are counted separately
    (legacy.hit_rows_with_zero_latency) and excluded from hit latency / cost statistics
"""
import json
import logging
from collections import Counter, defaultdict
from datetime import datetime

from app.core.db import get_pool
from app.core.registry import get_deployments

log = logging.getLogger(__name__)


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _r(v, nd=2):
    return None if v is None else round(float(v), nd)


def _pct(num, den):
    return None if not den else round(num / den * 100, 2)


_LAT = """count(*) AS n, avg(latency_ms) AS avg,
          percentile_cont(0.5)  WITHIN GROUP (ORDER BY latency_ms) AS p50,
          percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95,
          percentile_cont(0.99) WITHIN GROUP (ORDER BY latency_ms) AS p99"""


def _lat(row) -> dict:
    return {"n": row["n"], "avg": _r(row["avg"]), "p50": _r(row["p50"]),
            "p95": _r(row["p95"]), "p99": _r(row["p99"])}


ATTEMPT_SCHEMA = (
    "ALTER TABLE request_logs ADD COLUMN IF NOT EXISTS request_id TEXT",
    "ALTER TABLE request_logs ADD COLUMN IF NOT EXISTS deployment_id TEXT",
    "ALTER TABLE request_logs ADD COLUMN IF NOT EXISTS attempts SMALLINT",
    "ALTER TABLE request_logs ADD COLUMN IF NOT EXISTS capacity_existed BOOLEAN",
    "CREATE INDEX IF NOT EXISTS request_logs_request_id_idx ON request_logs (request_id)",
    "CREATE INDEX IF NOT EXISTS request_logs_deployment_time_idx ON request_logs (deployment_id, timestamp)",
    """CREATE TABLE IF NOT EXISTS attempt_logs (
           id BIGSERIAL PRIMARY KEY,
           timestamp TIMESTAMPTZ NOT NULL DEFAULT now(),
           request_id TEXT NOT NULL,
           attempt_no SMALLINT NOT NULL,
           deployment_id TEXT NOT NULL,
           provider TEXT NOT NULL,
           model TEXT NOT NULL,
           outcome TEXT NOT NULL,
           error_type TEXT,
           latency_ms DOUBLE PRECISION,
           retry_after_s DOUBLE PRECISION)""",
    "CREATE INDEX IF NOT EXISTS attempt_logs_timestamp_idx ON attempt_logs (timestamp)",
    "CREATE INDEX IF NOT EXISTS attempt_logs_deployment_time_idx ON attempt_logs (deployment_id, timestamp)",
    "CREATE INDEX IF NOT EXISTS attempt_logs_request_id_idx ON attempt_logs (request_id)",
)


async def ensure_tables() -> None:
    """Create the snapshots table, and add the token columns to request_logs (idempotent).
    Only after the columns exist does the tracker start writing to them."""
    from app.tracker.tracker import tracker
    pool = get_pool()
    try:
        for col in ("input_tokens", "output_tokens", "thought_tokens", "cached_tokens"):
            await pool.execute(f"ALTER TABLE request_logs ADD COLUMN IF NOT EXISTS {col} INTEGER")
        tracker.token_columns = True
    except Exception as ex:
        log.warning("could not add token columns to request_logs; token usage will not be "
                    "recorded until this succeeds: %s: %s", type(ex).__name__, ex)
    try:
        for statement in ATTEMPT_SCHEMA:
            await pool.execute(statement)
        tracker.attempt_columns = True
    except Exception as ex:
        log.warning("could not add deployment/attempt logging schema; per-deployment history "
                    "will not be recorded until this succeeds: %s: %s", type(ex).__name__, ex)
    await pool.execute(
        """CREATE TABLE IF NOT EXISTS metric_snapshots (
               id BIGSERIAL PRIMARY KEY, label TEXT NOT NULL,
               taken_at TIMESTAMPTZ NOT NULL DEFAULT now(),
               window_since TIMESTAMPTZ, window_until TIMESTAMPTZ, metrics JSONB NOT NULL)"""
    )


async def _breaker_time_column(pool) -> str | None:
    cols = {r["column_name"] for r in await pool.fetch(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'breaker_events'")}
    for c in ("timestamp", "created_at", "occurred_at", "time"):
        if c in cols:
            return c
    return None


LOCAL_REFUSALS = ("local_capacity", "breaker_open")
MIN_POOL_CALLS = 20


async def _deployment_metrics(pool, W: str, args: tuple) -> dict | None:
    try:
        attempt_rows = await pool.fetch(
            f"""SELECT deployment_id, min(provider) AS provider, min(model) AS model,
                       count(*) AS attempts,
                       count(*) FILTER (WHERE outcome NOT IN ('local_capacity', 'breaker_open')) AS calls,
                       count(*) FILTER (WHERE outcome = 'success') AS successes,
                       max(timestamp) AS last_attempt_at,
                       percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms)
                           FILTER (WHERE outcome = 'success') AS p50,
                       percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)
                           FILTER (WHERE outcome = 'success') AS p95
                FROM attempt_logs WHERE {W} GROUP BY deployment_id""", *args)
        outcome_rows = await pool.fetch(
            f"""SELECT deployment_id, outcome, count(*) AS count
                FROM attempt_logs WHERE {W} GROUP BY 1, 2""", *args)
        token_rows = await pool.fetch(
            f"""SELECT deployment_id, sum(input_tokens) AS input, sum(output_tokens) AS output,
                       avg(input_tokens) AS avg_input, avg(output_tokens) AS avg_output
                FROM request_logs
                WHERE {W} AND status = 'success' AND NOT cache_hit AND deployment_id IS NOT NULL
                GROUP BY 1""", *args)
        routing = await pool.fetchrow(
            f"""SELECT count(*) FILTER (WHERE status = 'error' AND capacity_existed IS NOT NULL) AS gave_up,
                       count(*) FILTER (WHERE status = 'error' AND capacity_existed) AS wasted,
                       count(*) FILTER (WHERE attempts > 1) AS multi_attempt,
                       count(*) FILTER (WHERE attempts IS NOT NULL) AS with_attempts,
                       avg(attempts) AS avg_attempts
                FROM request_logs WHERE {W}""", *args)
    except Exception as ex:
        log.warning("deployment metrics unavailable: %s: %s", type(ex).__name__, ex)
        return None

    seen = {r["deployment_id"]: r for r in attempt_rows}
    outcomes: dict[str, dict[str, int]] = defaultdict(dict)
    for r in outcome_rows:
        outcomes[r["deployment_id"]][r["outcome"]] = r["count"]
    tokens = {r["deployment_id"]: r for r in token_rows}
    registry = {d.id: d for d in get_deployments()}
    ids = list(registry) + sorted(i for i in seen if i not in registry)

    def group_of(dep_id: str) -> tuple[str, str]:
        if dep_id in registry:
            return registry[dep_id].model_group
        return seen[dep_id]["provider"], seen[dep_id]["model"]

    pool_attempts: Counter = Counter()
    pool_calls: Counter = Counter()
    for dep_id in ids:
        row = seen.get(dep_id)
        pool_attempts[group_of(dep_id)] += row["attempts"] if row else 0
        pool_calls[group_of(dep_id)] += row["calls"] if row else 0

    deployments = []
    for dep_id in ids:
        dep, row, tok = registry.get(dep_id), seen.get(dep_id), tokens.get(dep_id)
        group = group_of(dep_id)
        attempts = row["attempts"] if row else 0
        calls = row["calls"] if row else 0
        if dep is None:
            state = "not_live"
        elif attempts == 0 and pool_attempts[group] > 0:
            state = "idle"
        elif attempts == 0:
            state = "no_traffic"
        else:
            state = "active"
        errors = {k: v for k, v in outcomes.get(dep_id, {}).items() if k != "success"}
        deployments.append({
            "deployment_id": dep_id, "provider": group[0], "model": group[1], "state": state,
            "attempts": attempts, "provider_calls": calls,
            "successes": row["successes"] if row else 0,
            "local_refusals": sum(errors.get(k, 0) for k in LOCAL_REFUSALS),
            "share_of_pool_pct": _pct(calls, pool_calls[group]),
            "errors": errors,
            "error_rate_pct": _pct(calls - (row["successes"] if row else 0), calls),
            "provider_429s": errors.get("rate_limited", 0),
            "latency_ms": {"p50": _r(row["p50"]) if row else None, "p95": _r(row["p95"]) if row else None},
            "tokens": {
                "total_input": int(tok["input"]) if tok and tok["input"] is not None else None,
                "total_output": int(tok["output"]) if tok and tok["output"] is not None else None,
                "avg_input": _r(tok["avg_input"], 0) if tok else None,
                "avg_output": _r(tok["avg_output"], 0) if tok else None,
            },
            "last_attempt_at": row["last_attempt_at"].isoformat() if row else None,
        })

    pools = []
    for group in dict.fromkeys(group_of(i) for i in ids):
        members = [d for d in deployments if (d["provider"], d["model"]) == group]
        enabled = [d for d in members if d["state"] in ("active", "idle", "no_traffic")]
        shares = [d["share_of_pool_pct"] or 0.0 for d in enabled]
        balanced = len(enabled) >= 2 and pool_calls[group] >= MIN_POOL_CALLS
        pools.append({
            "provider": group[0], "model": group[1],
            "deployments": [d["deployment_id"] for d in members],
            "enabled": len(enabled), "provider_calls": pool_calls[group],
            "fair_share_pct": _r(100 / len(enabled)) if enabled else None,
            "imbalance_pct": _r(max(shares) - min(shares)) if balanced else None,
            "idle": [d["deployment_id"] for d in members if d["state"] == "idle"],
        })

    return {
        "by_deployment": deployments,
        "pools": pools,
        "routing": {
            "gave_up": routing["gave_up"],
            "rejected_while_capacity_existed": routing["wasted"],
            "rejected_while_capacity_existed_pct": _pct(routing["wasted"], routing["gave_up"]),
            "provider_429s": sum(d["provider_429s"] for d in deployments),
            "multi_attempt_requests": routing["multi_attempt"],
            "multi_attempt_pct": _pct(routing["multi_attempt"], routing["with_attempts"]),
            "avg_attempts": _r(routing["avg_attempts"]),
        },
    }


async def get_metrics_summary(since: datetime | None, until: datetime | None) -> dict:
    pool = get_pool()
    # window filter, shared by every query below: $1 = since, $2 = until (both may be NULL)
    W = "($1::timestamptz IS NULL OR timestamp >= $1) AND ($2::timestamptz IS NULL OR timestamp < $2)"
    ok = f"{W} AND status = 'success'"
    args = (since, until)

    t = await pool.fetchrow(
        f"""SELECT count(*) AS total, min(timestamp) AS first_ts, max(timestamp) AS last_ts,
                   count(*) FILTER (WHERE status = 'error') AS errors,
                   count(*) FILTER (WHERE status = 'success') AS successes,
                   count(*) FILTER (WHERE status = 'success' AND was_fallback) AS fallbacks,
                   count(*) FILTER (WHERE status = 'success' AND cache_hit) AS hits,
                   count(*) FILTER (WHERE status = 'success' AND cache_hit
                                    AND coalesce(latency_ms, 0) = 0) AS legacy_hits,
                   coalesce(sum(cost_usd) FILTER (WHERE status = 'success'), 0) AS total_cost
            FROM request_logs WHERE {W}""", *args)
    total, successes, hits = t["total"], t["successes"], t["hits"]

    lat_all = await pool.fetchrow(f"SELECT {_LAT} FROM request_logs WHERE {ok} AND latency_ms IS NOT NULL", *args)
    lat_hit = await pool.fetchrow(
        f"SELECT {_LAT} FROM request_logs WHERE {ok} AND cache_hit AND latency_ms > 0", *args)
    lat_miss = await pool.fetchrow(
        f"SELECT {_LAT} FROM request_logs WHERE {ok} AND NOT cache_hit AND latency_ms IS NOT NULL", *args)

    errs = await pool.fetch(
        f"""SELECT coalesce(error_type, 'unknown') AS error_type, count(*) AS count
            FROM request_logs WHERE {W} AND status = 'error'
            GROUP BY 1 ORDER BY 2 DESC""", *args)
    scopes = await pool.fetch(
        f"""SELECT coalesce(cache_scope, 'none') AS scope, count(*) AS requests,
                   count(*) FILTER (WHERE cache_hit) AS hits
            FROM request_logs WHERE {ok} GROUP BY 1 ORDER BY 2 DESC""", *args)
    provs = await pool.fetch(
        f"""SELECT provider_used AS provider, count(*) AS requests,
                   count(*) FILTER (WHERE was_fallback) AS fallbacks,
                   avg(latency_ms) FILTER (WHERE NOT cache_hit) AS avg_lat,
                   percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)
                       FILTER (WHERE NOT cache_hit) AS p95_lat,
                   avg(cost_usd) FILTER (WHERE NOT cache_hit) AS avg_cost
            FROM request_logs WHERE {ok} AND provider_used IS NOT NULL
            GROUP BY 1 ORDER BY 2 DESC""", *args)

    c = await pool.fetchrow(
        f"""SELECT avg(cost_usd) FILTER (WHERE NOT cache_hit) AS avg_miss,
                   avg(cost_usd) FILTER (WHERE cache_hit AND latency_ms > 0) AS avg_hit
            FROM request_logs WHERE {ok}""", *args)
    avg_miss, avg_hit = c["avg_miss"], c["avg_hit"]
    real_hits = hits - t["legacy_hits"]
    est_saved = None
    if avg_miss is not None and real_hits > 0:
        est_saved = max(0.0, real_hits * (float(avg_miss) - float(avg_hit or 0)))

    e = await pool.fetchrow(
        f"""SELECT count(*) AS n, avg(embed_latency_ms) AS avg_lat,
                   percentile_cont(0.95) WITHIN GROUP (ORDER BY embed_latency_ms) AS p95_lat,
                   avg(embed_cost_usd) AS avg_cost,
                   avg(latency_ms) FILTER (WHERE NOT cache_hit) AS miss_total_lat,
                   avg(embed_latency_ms) FILTER (WHERE NOT cache_hit) AS miss_embed_lat
            FROM request_logs WHERE {ok} AND embed_latency_ms IS NOT NULL""", *args)
    share = None
    if e["miss_total_lat"] and e["miss_embed_lat"] is not None:
        share = round(float(e["miss_embed_lat"]) / float(e["miss_total_lat"]) * 100, 2)

    tok = await pool.fetchrow(
        f"""SELECT avg(estimated_tokens) AS avg_t,
                   percentile_cont(0.95) WITHIN GROUP (ORDER BY estimated_tokens) AS p95_t
            FROM request_logs WHERE {W} AND estimated_tokens IS NOT NULL""", *args)
    # Real usage reported by the providers (provider calls only: cache hits have none).
    # Guarded: if the token columns have not been added yet, report "not recorded".
    real = None
    try:
        real = await pool.fetchrow(
            f"""SELECT count(*) AS n, avg(input_tokens) AS avg_in, avg(output_tokens) AS avg_out,
                       avg(thought_tokens) AS avg_thought, sum(input_tokens) AS sum_in,
                       sum(output_tokens) AS sum_out, sum(cached_tokens) AS sum_cached,
                       avg(estimated_tokens::float / nullif(input_tokens + output_tokens, 0))
                           AS est_ratio
                FROM request_logs
                WHERE {ok} AND NOT cache_hit AND input_tokens IS NOT NULL""", *args)
    except Exception as ex:
        log.warning("token usage columns unavailable: %s: %s", type(ex).__name__, ex)

    transitions = []
    tcol = await _breaker_time_column(pool)
    if tcol:
        bw = f"($1::timestamptz IS NULL OR {tcol} >= $1) AND ($2::timestamptz IS NULL OR {tcol} < $2)"
    else:
        bw = "TRUE"     # no usable time column: show all transitions rather than guess
    try:
        rows = await pool.fetch(
            f"""SELECT provider, new_state, count(*) AS count FROM breaker_events
                WHERE {bw} GROUP BY 1, 2 ORDER BY 3 DESC""", *(args if tcol else ()))
        transitions = [{"provider": r["provider"], "new_state": r["new_state"], "count": r["count"]} for r in rows]
    except Exception as ex:                       # table missing: leave the section empty
        log.warning("could not read breaker_events: %s: %s", type(ex).__name__, ex)

    deployments = await _deployment_metrics(pool, W, args)

    return {
        "window": {
            "since": (since or t["first_ts"]).isoformat() if (since or t["first_ts"]) else None,
            "until": (until or t["last_ts"]).isoformat() if (until or t["last_ts"]) else None,
            "request_count": total,
        },
        "totals": {
            "requests": total, "errors": t["errors"], "error_rate_pct": _pct(t["errors"], total),
            "fallback_count": t["fallbacks"], "fallback_rate_pct": _pct(t["fallbacks"], successes),
        },
        "error_types": [{"error_type": r["error_type"], "count": r["count"]} for r in errs],
        "cache": {
            "hits": hits, "hit_rate_pct": _pct(hits, successes),
            "by_scope": [{"scope": r["scope"], "requests": r["requests"], "hits": r["hits"],
                          "hit_rate_pct": _pct(r["hits"], r["requests"])} for r in scopes],
        },
        "latency_ms": {"all": _lat(lat_all), "hit": _lat(lat_hit), "miss": _lat(lat_miss)},
        "by_provider": [{
            "provider": r["provider"], "requests": r["requests"], "fallbacks": r["fallbacks"],
            "avg_latency_ms": _r(r["avg_lat"]), "p95_latency_ms": _r(r["p95_lat"]),
            "avg_cost_usd": _r(r["avg_cost"], 8)} for r in provs],
        "cost": {
            "total_usd": _r(t["total_cost"], 8) if successes else None,
            "avg_miss_usd": _r(avg_miss, 8), "avg_hit_usd": _r(avg_hit, 8),
            "est_saved_usd": _r(est_saved, 8),
        },
        "embed": {
            "rows_with_data": e["n"], "avg_latency_ms": _r(e["avg_lat"]), "p95_latency_ms": _r(e["p95_lat"]),
            "avg_cost_usd": _r(e["avg_cost"], 8), "share_of_miss_latency_pct": share,
        },
        "tokens": {
            "avg_estimated": _r(tok["avg_t"], 0), "p95_estimated": _r(tok["p95_t"], 0),
            "rows_with_usage": real["n"] if real else 0,
            "avg_input": _r(real["avg_in"], 0) if real else None,
            "avg_output": _r(real["avg_out"], 0) if real else None,
            "avg_thought": _r(real["avg_thought"], 0) if real else None,
            "total_input": int(real["sum_in"]) if real and real["sum_in"] is not None else None,
            "total_output": int(real["sum_out"]) if real and real["sum_out"] is not None else None,
            "total_cached": int(real["sum_cached"]) if real and real["sum_cached"] is not None else None,
            # estimated / actual (input + output): 1.0 = perfect, >1 = the estimate is too high
            "estimate_to_actual_ratio": _r(real["est_ratio"], 2) if real else None,
        },
        "legacy": {"hit_rows_with_zero_latency": t["legacy_hits"]},
        "breaker": {"transitions": transitions},
        "deployments": deployments,
    }


def _snap(row) -> dict:
    m = row["metrics"]
    return {
        "id": row["id"], "label": row["label"], "taken_at": row["taken_at"].isoformat(),
        "window_since": row["window_since"].isoformat() if row["window_since"] else None,
        "window_until": row["window_until"].isoformat() if row["window_until"] else None,
        "metrics": json.loads(m) if isinstance(m, str) else m,
    }


async def list_snapshots() -> list[dict]:
    rows = await get_pool().fetch("SELECT * FROM metric_snapshots ORDER BY taken_at ASC, id ASC")
    return [_snap(r) for r in rows]


async def save_snapshot(label: str, since: datetime | None, until: datetime | None) -> dict:
    summary = await get_metrics_summary(since, until)
    row = await get_pool().fetchrow(
        """INSERT INTO metric_snapshots (label, window_since, window_until, metrics)
           VALUES ($1, $2, $3, $4::jsonb) RETURNING *""",
        label, since, until, json.dumps(summary))
    return _snap(row)
