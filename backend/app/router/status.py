from collections import Counter

from app.models.deployment import Deployment
from app.router import cooldowns
from app.router.circuit_breaker import breaker_for, group_label
from app.router.limiter import limiter_for

REQUEST_METERS = ("rpm", "rpd")


def _verdict(cooldown: dict, breaker: dict, meters: dict) -> str:
    if cooldown["bad_key"]:
        return "bad_key"
    if breaker["state"] != "closed" or breaker["retry_in_seconds"] > 0:
        return "breaker_open"
    if cooldown["cooldown_remaining_s"] > 0:
        return "cooling"
    if any(meters[name]["remaining"] < 1 for name in REQUEST_METERS if name in meters):
        return "exhausted"
    return "ready"


def _ready_in_s(deployment: Deployment, cooldown: dict, breaker: dict) -> float | None:
    limiter_wait = limiter_for(deployment).seconds_until_fits(0)
    if limiter_wait is None:
        return None
    wait = max(limiter_wait, cooldown["cooldown_remaining_s"], breaker["retry_in_seconds"])
    return round(wait, 1)


def deployment_status(deployment: Deployment) -> dict:
    meters = limiter_for(deployment).snapshot()
    cooldown = cooldowns.snapshot(deployment.id)
    breaker = breaker_for(deployment.model_group).snapshot()
    return {
        "id": deployment.id,
        "provider": deployment.provider,
        "model": deployment.model,
        "tier": deployment.tier,
        "verdict": _verdict(cooldown, breaker, meters),
        "ready_in_s": _ready_in_s(deployment, cooldown, breaker),
        "meters": meters,
        "cooldown": cooldown,
        "breaker": {"group": group_label(deployment.model_group), **breaker},
    }


def gateway_status(deployments) -> dict:
    entries = [deployment_status(d) for d in deployments]
    waits = [e["ready_in_s"] for e in entries if e["ready_in_s"] is not None]
    verdicts = Counter(e["verdict"] for e in entries)
    return {
        "deployments_total": len(entries),
        "deployments_ready": verdicts.get("ready", 0),
        "by_verdict": dict(verdicts),
        "soonest_ready_in_s": min(waits, default=None),
        "deployments": entries,
    }
