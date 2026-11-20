import time
from collections import deque
from dataclasses import dataclass, field

from app.models.deployment import Deployment, Limits

SECONDS_PER_MINUTE = 60
SECONDS_PER_DAY = 86_400


class TokenBucket:
    def __init__(self, capacity: float, refill_per_second: float):
        self.capacity = capacity
        self.refill_per_second = refill_per_second
        self.tokens = capacity
        self._last_refill = time.monotonic()

    def _refill(self) -> None:
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self._last_refill) * self.refill_per_second)
        self._last_refill = now

    def can_consume(self, amount: float) -> bool:
        self._refill()
        return self.tokens >= amount

    def seconds_until(self, amount: float) -> float | None:
        self._refill()
        if amount > self.capacity:
            return None
        return max(0.0, (amount - self.tokens) / self.refill_per_second)

    def consume(self, amount: float):
        self.tokens -= amount
        return amount

    def refund(self, receipt) -> None:
        self._refill()
        self.tokens = min(self.capacity, self.tokens + receipt)

    def used(self) -> float:
        self._refill()
        return self.capacity - self.tokens

    def snapshot(self) -> dict:
        used = self.used()
        return {"limit": self.capacity, "used": round(used, 1),
                "full_in_s": round(used / self.refill_per_second, 1)}


class RollingWindow:
    def __init__(self, limit: int, window_seconds: float):
        self.limit = limit
        self.window_seconds = window_seconds
        self._entries: deque[list[float]] = deque()
        self._total = 0.0

    def _expire(self) -> None:
        cutoff = time.monotonic() - self.window_seconds
        while self._entries and self._entries[0][0] <= cutoff:
            self._total -= self._entries.popleft()[1]

    def can_consume(self, amount: float) -> bool:
        self._expire()
        return self._total + amount <= self.limit

    def seconds_until(self, amount: float) -> float | None:
        self._expire()
        if amount > self.limit:
            return None
        excess = self._total + amount - self.limit
        if excess <= 0:
            return 0.0
        freed = 0.0
        now = time.monotonic()
        for started_at, entry_amount in self._entries:
            freed += entry_amount
            if freed >= excess:
                return max(0.0, started_at + self.window_seconds - now)
        return None

    def consume(self, amount: float):
        entry = [time.monotonic(), amount]
        self._entries.append(entry)
        self._total += amount
        return entry

    def refund(self, receipt) -> None:
        try:
            self._entries.remove(receipt)
        except ValueError:
            return
        self._total -= receipt[1]

    def used(self) -> float:
        self._expire()
        return self._total

    def snapshot(self) -> dict:
        used = self.used()
        next_free = None
        if self._entries:
            next_free = round(max(0.0, self._entries[0][0] + self.window_seconds - time.monotonic()), 1)
        return {"limit": self.limit, "used": round(used, 1), "next_free_in_s": next_free}


@dataclass
class Reservation:
    receipts: list = field(default_factory=list)


class DeploymentLimiter:
    def __init__(self, limits: Limits):
        self._request_meters = []
        self._token_meters = []
        if limits.rpm:
            self._request_meters.append(("rpm", TokenBucket(limits.rpm, limits.rpm / SECONDS_PER_MINUTE)))
        if limits.rpd:
            self._request_meters.append(("rpd", RollingWindow(limits.rpd, SECONDS_PER_DAY)))
        if limits.tpm:
            self._token_meters.append(("tpm", TokenBucket(limits.tpm, limits.tpm / SECONDS_PER_MINUTE)))
        if limits.tpd:
            self._token_meters.append(("tpd", RollingWindow(limits.tpd, SECONDS_PER_DAY)))

    def _demands(self, tokens: int):
        return ([(name, meter, 1) for name, meter in self._request_meters]
                + [(name, meter, tokens) for name, meter in self._token_meters])

    def can_ever_fit(self, tokens: int) -> bool:
        return all(meter.seconds_until(amount) is not None for _, meter, amount in self._demands(tokens))

    def fits(self, tokens: int) -> bool:
        return all(meter.can_consume(amount) for _, meter, amount in self._demands(tokens))

    def seconds_until_fits(self, tokens: int) -> float | None:
        waits = [meter.seconds_until(amount) for _, meter, amount in self._demands(tokens)]
        if None in waits:
            return None
        return max(waits, default=0.0)

    def reserve(self, tokens: int) -> Reservation | None:
        if not self.fits(tokens):
            return None
        return Reservation([(meter, meter.consume(amount)) for _, meter, amount in self._demands(tokens)])

    def release(self, reservation: Reservation) -> None:
        for meter, receipt in reservation.receipts:
            meter.refund(receipt)
        reservation.receipts.clear()

    def snapshot(self) -> dict[str, dict]:
        meters = {}
        for name, meter, _ in self._demands(0):
            state = meter.snapshot()
            state["remaining"] = round(max(0.0, state["limit"] - state["used"]), 1)
            state["utilisation_pct"] = round(100 * state["used"] / state["limit"], 1)
            meters[name] = state
        return meters


_limiters: dict[str, DeploymentLimiter] = {}


def limiter_for(deployment: Deployment) -> DeploymentLimiter:
    limiter = _limiters.get(deployment.id)
    if limiter is None:
        limiter = DeploymentLimiter(deployment.limits)
        _limiters[deployment.id] = limiter
    return limiter


def reset_limiters() -> None:
    _limiters.clear()
