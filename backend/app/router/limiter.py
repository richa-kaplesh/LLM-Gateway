from dataclasses import dataclass

from app.models.deployment import Deployment, Limits
from app.state.meter_logic import BUCKET, DAY, SECONDS_PER_MINUTE, Demand, MeterResult
from app.state.store import KEY_PREFIX, get_store

METER_KINDS = {"rpm": BUCKET, "tpm": BUCKET, "rpd": DAY, "tpd": DAY}
REQUEST_METERS = ("rpm", "rpd")
DAY_METERS = ("rpd", "tpd")
MINUTE_METERS = ("rpm", "tpm")


@dataclass
class Reservation:
    deployment_id: str
    slot: int
    demands: list[Demand]
    released: bool = False


def meter_key(deployment_id: str) -> str:
    return f"{KEY_PREFIX}m:{deployment_id}"


def demands_for(limits: Limits, tokens: int) -> list[Demand]:
    demands = []
    for name, kind in METER_KINDS.items():
        limit = getattr(limits, name)
        if limit:
            demands.append(Demand(name, kind, limit, 1 if name in REQUEST_METERS else tokens))
    return demands


def can_ever_fit(limits: Limits, tokens: int, day_fraction: float = 1.0) -> bool:
    for d in demands_for(limits, tokens):
        ceiling = d.limit * day_fraction if d.kind == DAY else d.limit
        if d.amount > ceiling:
            return False
    return True


async def reserve(deployment: Deployment, tokens: int, day_fraction: float) -> tuple[Reservation | None, MeterResult]:
    demands = demands_for(deployment.limits, tokens)
    result = await get_store().meter(meter_key(deployment.id), demands, day_fraction, reserve=True)
    if not result.ok:
        return None, result
    return Reservation(deployment.id, result.slot, demands), result


async def release(reservation: Reservation | None) -> None:
    if reservation is None or reservation.released:
        return
    reservation.released = True
    await get_store().meter_release(meter_key(reservation.deployment_id), reservation.slot, reservation.demands)


async def peek_many(deployments: list[Deployment], tokens: int, day_fraction: float) -> list[MeterResult]:
    items = [(meter_key(d.id), demands_for(d.limits, tokens), day_fraction) for d in deployments]
    return await get_store().meter_many(items)


async def apply_rate_limit(deployment: Deployment, info: dict[str, tuple[float, float]] | None) -> None:
    if not info:
        return
    store = get_store()
    for name, (remaining, reset_in) in info.items():
        if getattr(deployment.limits, name, None) and reset_in > 0:
            await store.meter_override(meter_key(deployment.id), name, remaining, reset_in)


async def drain_minute_meters(deployment: Deployment) -> None:
    names = [n for n in MINUTE_METERS if getattr(deployment.limits, n)]
    await get_store().meter_drain(meter_key(deployment.id), names)


def blocked_by_day(result: MeterResult) -> bool:
    return any(result.readings[n].wait_s != 0 for n in DAY_METERS if n in result.readings)


def blocked_by_minute(result: MeterResult) -> bool:
    return any(result.readings[n].wait_s != 0 for n in MINUTE_METERS if n in result.readings)


def headroom(result: MeterResult) -> float:
    names = [n for n in DAY_METERS if n in result.readings] or list(result.readings)
    fractions = [result.readings[n].remaining / result.readings[n].limit for n in names]
    return min(fractions, default=1.0)


def snapshot(result: MeterResult) -> dict[str, dict]:
    meters = {}
    for name, r in result.readings.items():
        state = {"limit": r.limit, "used": round(r.used, 1), "remaining": round(r.remaining, 1),
                 "utilisation_pct": round(100 * r.used / r.limit, 1) if r.limit else 0.0}
        if METER_KINDS[name] == BUCKET:
            state["full_in_s"] = round(r.used / (r.limit / SECONDS_PER_MINUTE), 1)
        else:
            state["next_free_in_s"] = None if r.used <= 0 else round(r.wait_s, 1) if r.wait_s else None
        meters[name] = state
    return meters
