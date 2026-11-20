import random
from dataclasses import dataclass

from app.models.deployment import Deployment
from app.router import cooldowns
from app.router.circuit_breaker import ModelGroup, breaker_for
from app.router.limiter import limiter_for


@dataclass(frozen=True)
class Need:
    tokens: int
    needs_tools: bool
    required_group: ModelGroup | None


def can_serve(deployment: Deployment, need: Need) -> bool:
    if need.required_group is not None and deployment.model_group != need.required_group:
        return False
    if need.needs_tools and not deployment.capabilities.tools:
        return False
    return limiter_for(deployment).can_ever_fit(need.tokens)


def _rate_limit_wait(deployment: Deployment, need: Need) -> float | None:
    limiter_wait = limiter_for(deployment).seconds_until_fits(need.tokens)
    if limiter_wait is None:
        return None
    return max(cooldowns.remaining(deployment.id), limiter_wait)


def _candidates(deployments, need: Need, exclude: set[str]) -> list[Deployment]:
    return [d for d in deployments if d.id not in exclude and can_serve(d, need)]


def available(deployments, need: Need, exclude: set[str]) -> list[Deployment]:
    return [d for d in _candidates(deployments, need, exclude)
            if breaker_for(d.model_group).retry_after() == 0 and _rate_limit_wait(d, need) == 0]


def soonest_rate_limit_wait(deployments, need: Need, exclude: set[str]) -> float | None:
    waits = [_rate_limit_wait(d, need) for d in _candidates(deployments, need, exclude)
             if not cooldowns.is_bad_key(d.id) and breaker_for(d.model_group).retry_after() == 0]
    return min((w for w in waits if w is not None), default=None)


def soonest_breaker_wait(deployments, need: Need, exclude: set[str]) -> float | None:
    waits = [breaker_for(d.model_group).retry_after() for d in _candidates(deployments, need, exclude)
             if not cooldowns.is_bad_key(d.id)]
    return min((w for w in waits if w > 0), default=None)


def pick(candidates: list[Deployment], preferred_group: ModelGroup | None) -> Deployment:
    preferred = [d for d in candidates if d.model_group == preferred_group]
    return random.choice(preferred or candidates)
