from collections import Counter

from app.router import circuit_breaker, cooldowns, health, limiter, selection
from app.router.admission import get_admission
from app.router.selection import Need
from app.state.store import get_store

STATUS_NEED = Need(tokens=0, needs_tools=False, required_group=None, tier=None, priority=True)


def _verdict(view: selection.DeploymentView) -> str:
    blocker = view.blocker
    if blocker == selection.BLOCK_BAD_KEY:
        return "bad_key"
    if blocker == selection.BLOCK_BREAKER or circuit_breaker.state_of(view.breaker) != circuit_breaker.CLOSED:
        return "breaker_open"
    if blocker == selection.BLOCK_QUOTA:
        return "exhausted"
    if view.cooldown_wait > 0:
        return "cooling"
    if any(view.meters.readings[n].remaining < 1 for n in limiter.REQUEST_METERS if n in view.meters.readings):
        return "exhausted"
    return "ready"


def _ready_in_s(view: selection.DeploymentView) -> float | None:
    if view.rate_wait is None:
        return None
    return round(max(view.rate_wait, view.breaker_wait), 1)


def deployment_status(view: selection.DeploymentView) -> dict:
    d = view.deployment
    return {
        "id": d.id,
        "provider": d.provider,
        "model": d.model,
        "tier": d.tier,
        "verdict": _verdict(view),
        "ready_in_s": _ready_in_s(view),
        "score": round(view.score(None), 3),
        "meters": limiter.snapshot(view.meters),
        "cooldown": cooldowns.snapshot(view.cooldown),
        "breaker": {"group": circuit_breaker.group_label(d.model_group), **circuit_breaker.snapshot(view.breaker)},
        "health": health.snapshot(view.health),
    }


async def gateway_status(deployments) -> dict:
    views = await selection.build_view(deployments, STATUS_NEED)
    entries = [deployment_status(v) for v in views]
    waits = [e["ready_in_s"] for e in entries if e["ready_in_s"] is not None]
    verdicts = Counter(e["verdict"] for e in entries)
    return {
        "state_store": get_store().backend,
        "admission": get_admission().snapshot(),
        "deployments_total": len(entries),
        "deployments_ready": verdicts.get("ready", 0),
        "by_verdict": dict(verdicts),
        "soonest_ready_in_s": min(waits, default=None),
        "deployments": entries,
    }
