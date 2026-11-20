import logging
import random
import time

from app.state.store import KEY_PREFIX, get_store

log = logging.getLogger(__name__)

BASE_RATE_LIMIT_COOLDOWN_SECONDS = 15.0
MIN_COOLDOWN_SECONDS = 1.0
MAX_COOLDOWN_SECONDS = 600.0
DEFAULT_QUOTA_COOLDOWN_SECONDS = 3600.0
MAX_QUOTA_COOLDOWN_SECONDS = 86_400.0
BAD_KEY_COOLDOWN_SECONDS = 900.0
STREAK_MEMORY_SECONDS = 600.0

RATE_LIMITED = "rate_limited"
QUOTA_EXHAUSTED = "quota_exhausted"
BAD_KEY = "bad_key"


def cooldown_key(deployment_id: str) -> str:
    return f"{KEY_PREFIX}cool:{deployment_id}"


def remaining(state: dict | None, now: float | None = None) -> float:
    if not state:
        return 0.0
    now = time.time() if now is None else now
    return max(0.0, state.get("until", 0.0) - now)


def is_bad_key(state: dict | None, now: float | None = None) -> bool:
    return bool(state and state.get("reason") == BAD_KEY and remaining(state, now) > 0)


def reason(state: dict | None, now: float | None = None) -> str | None:
    return state.get("reason") if state and remaining(state, now) > 0 else None


async def _set(deployment_id: str, why: str, seconds: float, streak: int) -> None:
    state = {"until": time.time() + seconds, "reason": why, "streak": streak}
    await get_store().set_json(cooldown_key(deployment_id), state, seconds + STREAK_MEMORY_SECONDS)


async def record_rate_limited(deployment_id: str, retry_after: float | None, state: dict | None) -> float:
    streak = (state or {}).get("streak", 0) + 1
    if retry_after is None:
        seconds = BASE_RATE_LIMIT_COOLDOWN_SECONDS * 2 ** (streak - 1) * random.uniform(0.8, 1.2)
    else:
        seconds = retry_after
    seconds = min(max(seconds, MIN_COOLDOWN_SECONDS), MAX_COOLDOWN_SECONDS)
    await _set(deployment_id, RATE_LIMITED, seconds, streak)
    log.warning("deployment %s rate-limited: cooldown %.0f s (retry_after=%s, streak=%d)",
                deployment_id, seconds, retry_after, streak)
    return seconds


async def record_quota_exhausted(deployment_id: str, retry_after: float | None) -> float:
    seconds = DEFAULT_QUOTA_COOLDOWN_SECONDS if retry_after is None else retry_after
    seconds = min(max(seconds, MIN_COOLDOWN_SECONDS), MAX_QUOTA_COOLDOWN_SECONDS)
    await _set(deployment_id, QUOTA_EXHAUSTED, seconds, 0)
    log.warning("deployment %s daily quota exhausted: benched %.0f s", deployment_id, seconds)
    return seconds


async def record_bad_key(deployment_id: str) -> None:
    await _set(deployment_id, BAD_KEY, BAD_KEY_COOLDOWN_SECONDS, 0)
    log.error("deployment %s: API key rejected; benched for %.0f s. Fix its key env var.",
              deployment_id, BAD_KEY_COOLDOWN_SECONDS)


async def record_success(deployment_id: str, state: dict | None) -> None:
    if state and state.get("streak", 0) > 0 and remaining(state) == 0:
        await _set(deployment_id, RATE_LIMITED, 0.0, 0)


def snapshot(state: dict | None) -> dict:
    return {"cooldown_remaining_s": round(remaining(state), 1),
            "reason": reason(state),
            "streak": (state or {}).get("streak", 0),
            "bad_key": is_bad_key(state)}
