import random
import time

from app.state.store import KEY_PREFIX, get_store

ModelGroup = tuple[str, str]

FAILURE_THRESHOLD = 3
BASE_OPEN_SECONDS = 30.0
MAX_OPEN_SECONDS = 600.0
PROBE_TIMEOUT_SECONDS = 30.0
STATE_TTL_SECONDS = 86_400.0

CLOSED, OPEN, HALF_OPEN = "closed", "open", "half_open"
SUCCESS, FAILURE, REACHABLE, INCONCLUSIVE = "success", "failure", "reachable", "inconclusive"


def group_label(group: ModelGroup) -> str:
    provider, model = group
    return f"{provider}:{model}"


def breaker_key(group: ModelGroup) -> str:
    return f"{KEY_PREFIX}brk:{group_label(group)}"


def _fresh() -> dict:
    return {"state": CLOSED, "failures": 0, "trips": 0, "opened_at": None, "open_for": 0.0, "probe_at": None}


def state_of(state: dict | None) -> str:
    return (state or {}).get("state", CLOSED)


def retry_after(state: dict | None, now: float | None = None) -> float:
    if not state:
        return 0.0
    now = time.time() if now is None else now
    if state["state"] == OPEN:
        return max(0.0, state["opened_at"] + state["open_for"] - now)
    if state["state"] == HALF_OPEN and state.get("probe_at") is not None:
        return max(0.0, state["probe_at"] + PROBE_TIMEOUT_SECONDS - now)
    return 0.0


def _open(state: dict, now: float) -> None:
    state["trips"] += 1
    base = min(BASE_OPEN_SECONDS * 2 ** (state["trips"] - 1), MAX_OPEN_SECONDS)
    state.update(state=OPEN, opened_at=now, open_for=base * random.uniform(0.8, 1.2), probe_at=None)


def _allow(state: dict | None, now: float) -> tuple[dict | None, tuple[bool, str, str]]:
    current = dict(state or _fresh())
    before = current["state"]
    if before == CLOSED:
        return None, (True, before, before)
    if before == OPEN:
        if now < current["opened_at"] + current["open_for"]:
            return None, (False, before, before)
        current.update(state=HALF_OPEN, probe_at=now)
        return current, (True, before, HALF_OPEN)
    if current.get("probe_at") is None or now - current["probe_at"] >= PROBE_TIMEOUT_SECONDS:
        current["probe_at"] = now
        return current, (True, before, before)
    return None, (False, before, before)


def _record(state: dict | None, event: str, now: float) -> tuple[dict | None, tuple[str, str]]:
    current = dict(state or _fresh())
    before = current["state"]
    if event == SUCCESS:
        if before == CLOSED and current["failures"] == 0 and current["trips"] == 0:
            return None, (before, before)
        current.update(_fresh())
    elif event == FAILURE:
        current["failures"] += 1
        if before == HALF_OPEN or (before == CLOSED and current["failures"] >= FAILURE_THRESHOLD):
            _open(current, now)
    elif event == REACHABLE:
        if before != HALF_OPEN:
            return None, (before, before)
        current.update(state=CLOSED, failures=0, probe_at=None)
    elif event == INCONCLUSIVE:
        if before != HALF_OPEN:
            return None, (before, before)
        current.update(state=OPEN, opened_at=now, probe_at=None)
    return current, (before, current["state"])


async def allow(group: ModelGroup, cached: dict | None) -> tuple[bool, str, str]:
    if state_of(cached) == CLOSED:
        return True, CLOSED, CLOSED
    return await get_store().update_json(breaker_key(group), lambda s: _allow(s, time.time()), STATE_TTL_SECONDS)


async def record(group: ModelGroup, event: str, cached: dict | None) -> tuple[str, str]:
    if event in (SUCCESS, REACHABLE, INCONCLUSIVE) and state_of(cached) == CLOSED \
            and (cached or {}).get("failures", 0) == 0 and (cached or {}).get("trips", 0) == 0:
        return CLOSED, CLOSED
    return await get_store().update_json(breaker_key(group), lambda s: _record(s, event, time.time()),
                                         STATE_TTL_SECONDS)


def snapshot(state: dict | None) -> dict:
    current = state or _fresh()
    return {"state": current["state"],
            "consecutive_failures": current["failures"],
            "trips": current["trips"],
            "retry_in_seconds": round(retry_after(current), 1)}
