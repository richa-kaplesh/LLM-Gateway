import logging
import time

log = logging.getLogger(__name__)

DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS = 60.0
MIN_COOLDOWN_SECONDS = 1.0
MAX_COOLDOWN_SECONDS = 600.0
BAD_KEY_COOLDOWN_SECONDS = 900.0

_cooldown_until: dict[str, float] = {}
_bad_keys: set[str] = set()


def remaining(deployment_id: str) -> float:
    return max(0.0, _cooldown_until.get(deployment_id, 0.0) - time.monotonic())


def is_bad_key(deployment_id: str) -> bool:
    return deployment_id in _bad_keys


def _cool_down(deployment_id: str, seconds: float) -> None:
    _cooldown_until[deployment_id] = time.monotonic() + seconds


def record_rate_limited(deployment_id: str, retry_after: float | None) -> None:
    seconds = DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS if retry_after is None else retry_after
    seconds = min(max(seconds, MIN_COOLDOWN_SECONDS), MAX_COOLDOWN_SECONDS)
    _cool_down(deployment_id, seconds)
    log.warning("deployment %s rate-limited: cooldown %.0f s (retry_after=%s)",
                deployment_id, seconds, retry_after)


def record_bad_key(deployment_id: str) -> None:
    _bad_keys.add(deployment_id)
    _cool_down(deployment_id, BAD_KEY_COOLDOWN_SECONDS)
    log.error("deployment %s: API key rejected; benched for %.0f s. Fix its key env var.",
              deployment_id, BAD_KEY_COOLDOWN_SECONDS)


def record_success(deployment_id: str) -> None:
    _bad_keys.discard(deployment_id)


def snapshot(deployment_id: str) -> dict:
    return {"cooldown_remaining_s": round(remaining(deployment_id), 1),
            "bad_key": is_bad_key(deployment_id)}


def reset_cooldowns() -> None:
    _cooldown_until.clear()
    _bad_keys.clear()
