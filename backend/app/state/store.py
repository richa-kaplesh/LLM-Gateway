import logging
from typing import Any, Callable, Protocol

from app.state.meter_logic import Demand, MeterResult

log = logging.getLogger(__name__)

KEY_PREFIX = "gw:"


class Store(Protocol):
    backend: str

    async def meter(self, key: str, demands: list[Demand], day_fraction: float,
                    reserve: bool) -> MeterResult: ...

    async def meter_many(self, items: list[tuple[str, list[Demand], float]]) -> list[MeterResult]: ...

    async def meter_release(self, key: str, slot: int, demands: list[Demand]) -> None: ...

    async def meter_override(self, key: str, name: str, remaining: float, reset_in: float) -> None: ...

    async def meter_drain(self, key: str, names: list[str]) -> None: ...

    async def get_json_many(self, keys: list[str]) -> list[dict | None]: ...

    async def set_json(self, key: str, value: dict, ttl_s: float | None = None) -> None: ...

    async def update_json(self, key: str, fn: Callable[[dict | None], tuple[dict | None, Any]],
                          ttl_s: float | None = None) -> Any: ...

    async def ping(self) -> bool: ...

    async def reset(self) -> None: ...

    async def close(self) -> None: ...


_store: Store | None = None


def get_store() -> Store:
    global _store
    if _store is None:
        from app.state.memory_store import MemoryStore
        _store = MemoryStore()
    return _store


async def init_store(redis_url: str | None) -> Store:
    global _store
    if redis_url:
        from app.state.redis_store import RedisStore
        store = RedisStore(redis_url)
        await store.ping()
        _store = store
        log.info("state store: redis (shared across instances)")
    else:
        from app.state.memory_store import MemoryStore
        _store = MemoryStore()
        log.warning("state store: in-memory (REDIS_URL not set): state is lost on restart "
                    "and is not shared between instances")
    return _store


def use_store(store: Store) -> None:
    global _store
    _store = store


async def close_store() -> None:
    if _store is not None:
        await _store.close()
