import math
import time

from app.state.store import KEY_PREFIX, get_store

PENALTY_HALF_LIFE_SECONDS = 120.0
LATENCY_SMOOTHING = 0.3
STATE_TTL_SECONDS = 86_400.0

PENALTIES = {"rate_limited": 1.0, "quota_exhausted": 1.0, "invalid_tool_call": 0.5,
             "overloaded": 2.0, "down": 2.0}


def health_key(deployment_id: str) -> str:
    return f"{KEY_PREFIX}hl:{deployment_id}"


def penalty(state: dict | None, now: float | None = None) -> float:
    if not state:
        return 0.0
    now = time.time() if now is None else now
    elapsed = max(0.0, now - state.get("ts", now))
    return state.get("pen", 0.0) * math.pow(0.5, elapsed / PENALTY_HALF_LIFE_SECONDS)


def latency_ms(state: dict | None) -> float | None:
    return (state or {}).get("lat")


def _update(state: dict | None, outcome: str, latency: float | None, now: float):
    current = dict(state or {})
    current["pen"] = penalty(current, now) + PENALTIES.get(outcome, 0.0)
    current["ts"] = now
    if outcome == "success" and latency is not None:
        previous = current.get("lat")
        current["lat"] = latency if previous is None else (
            LATENCY_SMOOTHING * latency + (1 - LATENCY_SMOOTHING) * previous)
    return current, None


async def record(deployment_id: str, outcome: str, latency: float | None) -> None:
    if outcome not in PENALTIES and outcome != "success":
        return
    await get_store().update_json(health_key(deployment_id),
                                  lambda s: _update(s, outcome, latency, time.time()), STATE_TTL_SECONDS)


def snapshot(state: dict | None) -> dict:
    lat = latency_ms(state)
    return {"penalty": round(penalty(state), 2), "latency_ewma_ms": round(lat, 1) if lat is not None else None}
