import math
from dataclasses import dataclass

SLOT_SECONDS = 600
DAY_SLOTS = 144
SECONDS_PER_MINUTE = 60
BUCKET = "bucket"
DAY = "day"


@dataclass(frozen=True)
class Demand:
    name: str
    kind: str
    limit: float
    amount: float


@dataclass(frozen=True)
class Reading:
    limit: float
    used: float
    remaining: float
    wait_s: float | None


@dataclass(frozen=True)
class MeterResult:
    ok: bool
    slot: int
    readings: dict[str, Reading]

    @property
    def wait_s(self) -> float | None:
        waits = [r.wait_s for r in self.readings.values()]
        if any(w is None for w in waits):
            return None
        return max(waits, default=0.0)


def _bucket(h: dict, d: Demand, now: float) -> tuple[float, float | None]:
    rate = d.limit / SECONDS_PER_MINUTE
    tokens = h.get(f"b:{d.name}:tokens", d.limit)
    elapsed = max(0.0, now - h.get(f"b:{d.name}:ts", now))
    tokens = min(d.limit, tokens + elapsed * rate)
    h[f"b:{d.name}:tokens"] = tokens
    h[f"b:{d.name}:ts"] = now
    wait = None if d.amount > d.limit else max(0.0, (d.amount - tokens) / rate)
    return d.limit - tokens, wait


def _day(h: dict, d: Demand, now: float, current: int, day_fraction: float) -> tuple[float, float | None]:
    prefix = f"d:{d.name}:"
    live = []
    for field in [k for k in h if k.startswith(prefix)]:
        slot = int(field[len(prefix):])
        if slot <= current - DAY_SLOTS:
            del h[field]
        else:
            live.append((slot, h[field]))
    live.sort()
    used = sum(v for _, v in live)
    effective = d.limit * day_fraction
    if d.amount > effective:
        return used, None
    excess = used + d.amount - effective
    if excess <= 0:
        return used, 0.0
    freed = 0.0
    for slot, amount in live:
        freed += amount
        if freed >= excess:
            return used, max(0.0, (slot + DAY_SLOTS) * SLOT_SECONDS - now)
    return used, None


def _override(h: dict, d: Demand, now: float, remaining: float, wait: float | None):
    rem_key, reset_key = f"o:{d.name}:rem", f"o:{d.name}:reset"
    if rem_key not in h or reset_key not in h:
        return remaining, wait
    if now >= h[reset_key]:
        del h[rem_key], h[reset_key]
        return remaining, wait
    remaining = min(remaining, h[rem_key])
    if h[rem_key] < d.amount and wait is not None:
        wait = max(wait, h[reset_key] - now)
    return remaining, wait


def evaluate(h: dict, demands: list[Demand], day_fraction: float, now: float, reserve: bool) -> MeterResult:
    current = math.floor(now / SLOT_SECONDS)
    readings = {}
    for d in demands:
        if d.kind == BUCKET:
            used, wait = _bucket(h, d, now)
        else:
            used, wait = _day(h, d, now, current, day_fraction)
        remaining, wait = _override(h, d, now, d.limit - used, wait)
        readings[d.name] = Reading(d.limit, used, max(0.0, remaining), wait)
    ok = all(r.wait_s == 0 for r in readings.values())
    if reserve and ok:
        for d in demands:
            if d.kind == BUCKET:
                h[f"b:{d.name}:tokens"] -= d.amount
            else:
                field = f"d:{d.name}:{current}"
                h[field] = h.get(field, 0.0) + d.amount
            if f"o:{d.name}:rem" in h:
                h[f"o:{d.name}:rem"] -= d.amount
    return MeterResult(ok, current, readings)


def release(h: dict, slot: int, demands: list[Demand], now: float) -> None:
    for d in demands:
        if d.kind == BUCKET:
            _bucket(h, d, now)
            h[f"b:{d.name}:tokens"] = min(d.limit, h[f"b:{d.name}:tokens"] + d.amount)
        else:
            field = f"d:{d.name}:{slot}"
            if field in h:
                h[field] -= d.amount
                if h[field] <= 0:
                    del h[field]
        reset = h.get(f"o:{d.name}:reset")
        if reset is not None and now < reset:
            h[f"o:{d.name}:rem"] += d.amount


def override(h: dict, name: str, remaining: float, reset_in: float, now: float) -> None:
    h[f"o:{name}:rem"] = remaining
    h[f"o:{name}:reset"] = now + reset_in


def drain(h: dict, names: list[str], now: float) -> None:
    for name in names:
        h[f"b:{name}:tokens"] = 0.0
        h[f"b:{name}:ts"] = now
