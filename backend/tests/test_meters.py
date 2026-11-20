import asyncio

import fakeredis
import pytest

from app.state.memory_store import MemoryStore
from app.state.meter_logic import BUCKET, DAY, Demand
from app.state.redis_store import RedisStore


def demands(tokens):
    return [Demand("rpm", BUCKET, 30, 1), Demand("tpm", BUCKET, 8000, tokens),
            Demand("rpd", DAY, 1000, 1), Demand("tpd", DAY, 20000, tokens)]


@pytest.fixture(params=["memory", "redis"])
def store(request):
    if request.param == "memory":
        return MemoryStore()
    return RedisStore(fakeredis.FakeAsyncRedis())


def run(coro):
    return asyncio.run(coro)


def test_reserve_until_tpm_runs_out(store):
    async def scenario():
        results = [await store.meter("gw:m:a", demands(3000), 1.0, reserve=True) for _ in range(3)]
        return results
    first, second, third = run(scenario())
    assert first.ok and second.ok
    assert not third.ok
    assert third.readings["tpm"].wait_s > 0
    assert third.readings["tpm"].used == pytest.approx(6000, abs=50)


def test_release_gives_capacity_back(store):
    async def scenario():
        held = await store.meter("gw:m:b", demands(7000), 1.0, reserve=True)
        blocked = await store.meter("gw:m:b", demands(7000), 1.0, reserve=False)
        await store.meter_release("gw:m:b", held.slot, demands(7000))
        after = await store.meter("gw:m:b", demands(7000), 1.0, reserve=False)
        return blocked, after
    blocked, after = run(scenario())
    assert not blocked.ok
    assert after.ok


def test_daily_reserve_fraction_blocks_normal_requests(store):
    async def scenario():
        for _ in range(6):
            await store.meter("gw:m:c", [Demand("rpd", DAY, 10, 1)], 1.0, reserve=True)
        normal = await store.meter("gw:m:c", [Demand("rpd", DAY, 10, 1)], 0.6, reserve=False)
        priority = await store.meter("gw:m:c", [Demand("rpd", DAY, 10, 1)], 1.0, reserve=False)
        return normal, priority
    normal, priority = run(scenario())
    assert not normal.ok and normal.wait_s is not None and normal.wait_s > 0
    assert priority.ok


def test_header_override_caps_remaining(store):
    async def scenario():
        await store.meter_override("gw:m:d", "tpm", 500, 30)
        return await store.meter("gw:m:d", demands(1000), 1.0, reserve=False)
    result = run(scenario())
    assert not result.ok
    assert result.readings["tpm"].remaining == pytest.approx(500)
    assert 25 < result.readings["tpm"].wait_s <= 30


def test_drain_empties_minute_bucket(store):
    async def scenario():
        await store.meter_drain("gw:m:e", ["tpm"])
        return await store.meter("gw:m:e", demands(1000), 1.0, reserve=False)
    result = run(scenario())
    assert not result.ok
    assert result.readings["tpm"].wait_s == pytest.approx(7.5, abs=0.5)


def test_too_big_request_never_fits(store):
    result = run(store.meter("gw:m:f", demands(9000), 1.0, reserve=False))
    assert result.readings["tpm"].wait_s is None
    assert result.wait_s is None


def test_json_cas_update(store):
    async def scenario():
        def bump(value):
            value = value or {"n": 0}
            value["n"] += 1
            return value, value["n"]
        await asyncio.gather(*(store.update_json("gw:j:x", bump) for _ in range(20)))
        return (await store.get_json_many(["gw:j:x"]))[0]
    assert run(scenario())["n"] == 20
