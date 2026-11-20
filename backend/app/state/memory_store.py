import copy
import time
from typing import Any, Callable

from app.state import meter_logic
from app.state.meter_logic import Demand, MeterResult


class MemoryStore:
    backend = "memory"

    def __init__(self, clock: Callable[[], float] = time.time):
        self._clock = clock
        self._hashes: dict[str, dict] = {}
        self._json: dict[str, tuple[dict, float | None]] = {}

    def _hash(self, key: str) -> dict:
        return self._hashes.setdefault(key, {})

    async def meter(self, key: str, demands: list[Demand], day_fraction: float,
                    reserve: bool) -> MeterResult:
        return meter_logic.evaluate(self._hash(key), demands, day_fraction, self._clock(), reserve)

    async def meter_many(self, items: list[tuple[str, list[Demand], float]]) -> list[MeterResult]:
        return [await self.meter(key, demands, fraction, reserve=False) for key, demands, fraction in items]

    async def meter_release(self, key: str, slot: int, demands: list[Demand]) -> None:
        meter_logic.release(self._hash(key), slot, demands, self._clock())

    async def meter_override(self, key: str, name: str, remaining: float, reset_in: float) -> None:
        meter_logic.override(self._hash(key), name, remaining, reset_in, self._clock())

    async def meter_drain(self, key: str, names: list[str]) -> None:
        meter_logic.drain(self._hash(key), names, self._clock())

    def _get(self, key: str) -> dict | None:
        entry = self._json.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if expires_at is not None and self._clock() >= expires_at:
            del self._json[key]
            return None
        return copy.deepcopy(value)

    async def get_json_many(self, keys: list[str]) -> list[dict | None]:
        return [self._get(k) for k in keys]

    async def set_json(self, key: str, value: dict, ttl_s: float | None = None) -> None:
        expires_at = self._clock() + ttl_s if ttl_s else None
        self._json[key] = (copy.deepcopy(value), expires_at)

    async def update_json(self, key: str, fn: Callable[[dict | None], tuple[dict | None, Any]],
                          ttl_s: float | None = None) -> Any:
        new_value, result = fn(self._get(key))
        if new_value is not None:
            await self.set_json(key, new_value, ttl_s)
        return result

    async def ping(self) -> bool:
        return True

    async def reset(self) -> None:
        self._hashes.clear()
        self._json.clear()

    async def close(self) -> None:
        return None
