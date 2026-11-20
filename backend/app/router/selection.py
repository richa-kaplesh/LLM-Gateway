import random
import time
from dataclasses import dataclass, field

from app.core.config import get_settings
from app.models.deployment import Deployment
from app.router import circuit_breaker, cooldowns, health, limiter
from app.router.circuit_breaker import ModelGroup
from app.state.meter_logic import MeterResult
from app.state.store import get_store

STICKY_BONUS = 0.15
PENALTY_WEIGHT = 0.1
LATENCY_WEIGHT_PER_SECOND = 0.02
MAX_LATENCY_PENALTY = 0.3

BLOCK_RATE, BLOCK_QUOTA, BLOCK_BREAKER, BLOCK_BAD_KEY = "rate_limited", "quota_exhausted", "breaker_open", "bad_key"


@dataclass(frozen=True)
class Need:
    tokens: int
    needs_tools: bool
    required_group: ModelGroup | None
    tier: str | None = None
    priority: bool = False

    @property
    def day_fraction(self) -> float:
        if self.priority:
            return 1.0
        return 1.0 - get_settings().DAILY_RESERVE_PCT / 100.0


def can_serve(deployment: Deployment, need: Need) -> bool:
    if need.required_group is not None:
        if deployment.model_group != need.required_group:
            return False
    elif need.tier is not None and deployment.tier != need.tier:
        return False
    if need.needs_tools and not deployment.capabilities.tools:
        return False
    return limiter.can_ever_fit(deployment.limits, need.tokens, need.day_fraction)


@dataclass
class DeploymentView:
    deployment: Deployment
    meters: MeterResult
    cooldown: dict | None
    breaker: dict | None
    health: dict | None
    now: float = field(default_factory=time.time)

    @property
    def id(self) -> str:
        return self.deployment.id

    @property
    def cooldown_wait(self) -> float:
        return cooldowns.remaining(self.cooldown, self.now)

    @property
    def breaker_wait(self) -> float:
        return circuit_breaker.retry_after(self.breaker, self.now)

    @property
    def is_bad_key(self) -> bool:
        return cooldowns.is_bad_key(self.cooldown, self.now)

    @property
    def rate_wait(self) -> float | None:
        meter_wait = self.meters.wait_s
        if meter_wait is None:
            return None
        return max(meter_wait, self.cooldown_wait)

    @property
    def available(self) -> bool:
        return self.breaker_wait == 0 and self.rate_wait == 0

    @property
    def blocker(self) -> str | None:
        if self.is_bad_key:
            return BLOCK_BAD_KEY
        if self.breaker_wait > 0:
            return BLOCK_BREAKER
        if cooldowns.reason(self.cooldown, self.now) == cooldowns.QUOTA_EXHAUSTED or (
                not self.meters.ok and limiter.blocked_by_day(self.meters)
                and not limiter.blocked_by_minute(self.meters)):
            return BLOCK_QUOTA
        if self.rate_wait != 0:
            return BLOCK_RATE
        return None

    def score(self, preferred: ModelGroup | None) -> float:
        value = limiter.headroom(self.meters)
        if preferred is not None and self.deployment.model_group == preferred:
            value += STICKY_BONUS
        value -= PENALTY_WEIGHT * health.penalty(self.health, self.now)
        lat = health.latency_ms(self.health)
        if lat is not None:
            value -= min(MAX_LATENCY_PENALTY, LATENCY_WEIGHT_PER_SECOND * lat / 1000)
        return value


async def build_view(deployments, need: Need) -> list[DeploymentView]:
    eligible = [d for d in deployments if can_serve(d, need)]
    if not eligible:
        return []
    meters = await limiter.peek_many(eligible, need.tokens, need.day_fraction)
    groups = sorted({d.model_group for d in eligible})
    keys = ([cooldowns.cooldown_key(d.id) for d in eligible]
            + [health.health_key(d.id) for d in eligible]
            + [circuit_breaker.breaker_key(g) for g in groups])
    values = await get_store().get_json_many(keys)
    n = len(eligible)
    cools, healths, breakers = values[:n], values[n:2 * n], dict(zip(groups, values[2 * n:]))
    now = time.time()
    return [DeploymentView(d, m, c, breakers.get(d.model_group), h, now)
            for d, m, c, h in zip(eligible, meters, cools, healths)]


def available(views: list[DeploymentView], exclude: set[str]) -> list[DeploymentView]:
    return [v for v in views if v.id not in exclude and v.available]


def soonest_rate_wait(views: list[DeploymentView], exclude: set[str]) -> float | None:
    waits = [v.rate_wait for v in views
             if v.id not in exclude and not v.is_bad_key and v.breaker_wait == 0]
    return min((w for w in waits if w is not None), default=None)


def soonest_breaker_wait(views: list[DeploymentView], exclude: set[str]) -> float | None:
    waits = [v.breaker_wait for v in views if v.id not in exclude and not v.is_bad_key]
    return min((w for w in waits if w > 0), default=None)


def pick(candidates: list[DeploymentView], preferred: ModelGroup | None) -> DeploymentView:
    if len(candidates) == 1:
        return candidates[0]
    first, second = random.sample(candidates, 2)
    a, b = first.score(preferred), second.score(preferred)
    if a == b:
        return random.choice([first, second])
    return first if a > b else second
