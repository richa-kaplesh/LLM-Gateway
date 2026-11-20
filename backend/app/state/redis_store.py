import json
import math
from typing import Any, Callable

import redis.asyncio as redis
from redis.exceptions import WatchError

from app.state import lua_scripts
from app.state.meter_logic import Demand, MeterResult, Reading
from app.state.store import KEY_PREFIX

MAX_CAS_RETRIES = 20


def _demand_args(demands: list[Demand]) -> list:
    args: list = []
    for d in demands:
        args += [d.name, d.kind, repr(float(d.limit)), repr(float(d.amount))]
    return args


def _decode(value) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def _parse(raw: list) -> MeterResult:
    ok, slot, rest = int(raw[0]), int(raw[1]), [_decode(v) for v in raw[2:]]
    readings = {}
    for i in range(0, len(rest), 5):
        name, limit, used, remaining, wait = rest[i:i + 5]
        wait_value = float(wait)
        readings[name] = Reading(float(limit), float(used), float(remaining),
                                 None if wait_value < 0 else wait_value)
    return MeterResult(bool(ok), slot, readings)


class RedisStore:
    backend = "redis"

    def __init__(self, url_or_client):
        if isinstance(url_or_client, str):
            self._redis = redis.from_url(url_or_client, socket_timeout=2.0, socket_connect_timeout=3.0,
                                         health_check_interval=30)
        else:
            self._redis = url_or_client
        self._evaluate = self._redis.register_script(lua_scripts.EVALUATE)
        self._release = self._redis.register_script(lua_scripts.RELEASE)
        self._override = self._redis.register_script(lua_scripts.OVERRIDE)
        self._drain = self._redis.register_script(lua_scripts.DRAIN)

    async def meter(self, key: str, demands: list[Demand], day_fraction: float,
                    reserve: bool) -> MeterResult:
        args = ["reserve" if reserve else "peek", repr(float(day_fraction)), len(demands)] + _demand_args(demands)
        return _parse(await self._evaluate(keys=[key], args=args))

    async def meter_many(self, items: list[tuple[str, list[Demand], float]]) -> list[MeterResult]:
        if not items:
            return []
        async with self._redis.pipeline(transaction=False) as pipe:
            for key, demands, fraction in items:
                args = ["peek", repr(float(fraction)), len(demands)] + _demand_args(demands)
                await self._evaluate(keys=[key], args=args, client=pipe)
            raw = await pipe.execute()
        return [_parse(r) for r in raw]

    async def meter_release(self, key: str, slot: int, demands: list[Demand]) -> None:
        await self._release(keys=[key], args=[slot, len(demands)] + _demand_args(demands))

    async def meter_override(self, key: str, name: str, remaining: float, reset_in: float) -> None:
        await self._override(keys=[key], args=[name, repr(float(remaining)), repr(float(reset_in))])

    async def meter_drain(self, key: str, names: list[str]) -> None:
        if names:
            await self._drain(keys=[key], args=names)

    async def get_json_many(self, keys: list[str]) -> list[dict | None]:
        if not keys:
            return []
        values = await self._redis.mget(keys)
        return [json.loads(v) if v is not None else None for v in values]

    async def set_json(self, key: str, value: dict, ttl_s: float | None = None) -> None:
        px = max(1, math.ceil(ttl_s * 1000)) if ttl_s else None
        await self._redis.set(key, json.dumps(value), px=px)

    async def update_json(self, key: str, fn: Callable[[dict | None], tuple[dict | None, Any]],
                          ttl_s: float | None = None) -> Any:
        px = max(1, math.ceil(ttl_s * 1000)) if ttl_s else None
        async with self._redis.pipeline(transaction=True) as pipe:
            for _ in range(MAX_CAS_RETRIES):
                try:
                    await pipe.watch(key)
                    raw = await pipe.get(key)
                    new_value, result = fn(json.loads(raw) if raw is not None else None)
                    if new_value is None:
                        await pipe.unwatch()
                        return result
                    pipe.multi()
                    pipe.set(key, json.dumps(new_value), px=px)
                    await pipe.execute()
                    return result
                except WatchError:
                    continue
        raise RuntimeError(f"could not update {key}: too much contention")

    async def ping(self) -> bool:
        return bool(await self._redis.ping())

    async def reset(self) -> None:
        async for key in self._redis.scan_iter(match=f"{KEY_PREFIX}*"):
            await self._redis.delete(key)

    async def close(self) -> None:
        await self._redis.aclose()
