import asyncio
from pathlib import Path

import pytest

from app.clients import adapters
from app.clients.exceptions import (
    AllInvalidToolCallsError, AllProvidersUnavailableError, AllQuotaExhaustedError,
    GatewayOverloadedError, InvalidToolCallError, ProviderConnectionError, ProviderRateLimitedError,
    QuotaExhaustedError,
)
from app.core.registry import load_deployments
from app.models.schemas import GatewayRequest, GatewayResponse
from app.router import attempt_log, circuit_breaker, router
from app.router.admission import Admission
from app.state.memory_store import MemoryStore
from app.state.store import get_store, use_store

YAML = """
deployments:
{entries}
"""

ENTRY = """  - id: {id}
    provider: {provider}
    adapter: openai_compatible
    base_url: http://mock/v1
    key_env: KEY_{n}
    model: {model}
    limits: {{rpm: 100, tpm: 100000, rpd: {rpd}, tpd: null}}
    capabilities: {{tools: true, json: true}}
    tier: {tier}
    price: {{input: 0, output: 0, cached_input: null}}
"""


def make_deployments(tmp_path: Path, specs):
    entries = "".join(ENTRY.format(n=i, **s) for i, s in enumerate(specs))
    path = tmp_path / "deployments.yaml"
    path.write_text(YAML.format(entries=entries), encoding="utf-8")
    env = {f"KEY_{i}": f"k{i}" for i in range(len(specs))}
    return tuple(load_deployments(path, env))


SMART3 = [dict(id=f"a-{i}", provider="p", model="m", tier="smart", rpd=1000) for i in range(3)]


def ok(deployment):
    return GatewayResponse(content="hi", model_used=deployment.model, provider_used=deployment.provider,
                           deployment_id=deployment.id, cost_usd=0, latency_ms=5, cache_hit=False)


class Script:
    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.calls: list[str] = []

    async def __call__(self, request, deployment):
        self.calls.append(deployment.id)
        outcome = self.behaviour(deployment, len(self.calls))
        if isinstance(outcome, Exception):
            raise outcome
        return ok(deployment)


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    use_store(MemoryStore())
    monkeypatch.setattr(router.settings, "MAX_QUEUE_WAIT_SECONDS", 0.0)
    monkeypatch.setattr("app.router.router.get_admission", lambda: Admission(10, 10, 1.0))

    async def no_track(*args, **kwargs):
        return None
    monkeypatch.setattr(router.tracker, "log_breaker_transition", no_track)
    yield


def request(**extra):
    body = {"conversation_id": "c1", "user_id": "u1",
            "messages": [{"role": "user", "content": "hello"}],
            "tools": [{"type": "function", "function": {"name": "f", "parameters": {"type": "object"}}}]}
    body.update(extra)
    return GatewayRequest(**body)


def run(deployments, monkeypatch, behaviour, req=None):
    script = Script(behaviour)
    monkeypatch.setattr(adapters, "complete", script)
    monkeypatch.setattr("app.router.router.get_deployments", lambda: deployments)

    async def go():
        attempt_log.start()
        try:
            return await router.route(req or request()), attempt_log.current()
        except Exception as e:
            return e, attempt_log.current()
    result, log = asyncio.run(go())
    return result, log, script


async def breaker_state(group):
    return (await get_store().get_json_many([circuit_breaker.breaker_key(group)]))[0]


def test_invalid_tool_call_retries_and_does_not_trip_breaker(tmp_path, monkeypatch):
    deps = make_deployments(tmp_path, SMART3)
    result, log, script = run(deps, monkeypatch,
                              lambda d, n: InvalidToolCallError("bad call") if n == 1 else None)
    assert isinstance(result, GatewayResponse)
    assert [a.outcome for a in log.attempts] == ["invalid_tool_call", "success"]
    state = asyncio.run(breaker_state(("p", "m")))
    assert circuit_breaker.state_of(state) == "closed"


def test_repeated_invalid_tool_calls_give_a_502_class_error(tmp_path, monkeypatch):
    deps = make_deployments(tmp_path, SMART3)
    result, log, _ = run(deps, monkeypatch, lambda d, n: InvalidToolCallError("bad call"))
    assert isinstance(result, AllInvalidToolCallsError)
    assert result.status_code == 502
    assert len(log.attempts) == 3


def test_outages_open_the_breaker(tmp_path, monkeypatch):
    deps = make_deployments(tmp_path, SMART3)
    result, log, _ = run(deps, monkeypatch, lambda d, n: ProviderConnectionError("boom"))
    assert isinstance(result, AllProvidersUnavailableError)
    state = asyncio.run(breaker_state(("p", "m")))
    assert state["state"] == "open"
    assert state["open_for"] >= 24


def test_rate_limits_do_not_use_the_failure_budget(tmp_path, monkeypatch):
    specs = [dict(id=f"a-{i}", provider="p", model="m", tier="smart", rpd=1000) for i in range(5)]
    deps = make_deployments(tmp_path, specs)
    result, log, _ = run(deps, monkeypatch,
                         lambda d, n: ProviderRateLimitedError("429", retry_after=30) if n <= 4 else None)
    assert isinstance(result, GatewayResponse)
    assert [a.outcome for a in log.attempts].count("rate_limited") == 4
    assert result.attempts == 5


def test_all_daily_quotas_gone_is_reported_as_quota_exhausted(tmp_path, monkeypatch):
    deps = make_deployments(tmp_path, SMART3)
    result, log, _ = run(deps, monkeypatch, lambda d, n: QuotaExhaustedError("per day", retry_after=7200))
    assert isinstance(result, AllQuotaExhaustedError)
    assert result.code == "quota_exhausted"
    assert result.retry_after > 3600


def test_tier_selects_only_matching_deployments(tmp_path, monkeypatch):
    specs = SMART3 + [dict(id="f-1", provider="p", model="fast", tier="fast", rpd=1000)]
    deps = make_deployments(tmp_path, specs)
    result, _, script = run(deps, monkeypatch, lambda d, n: None, request(tier="fast"))
    assert result.deployment_id == "f-1"
    result, _, script = run(deps, monkeypatch, lambda d, n: None, request())
    assert result.deployment_id.startswith("a-")


def test_tool_history_stays_on_the_pinned_model(tmp_path, monkeypatch):
    specs = [dict(id="a-1", provider="p", model="m1", tier="smart", rpd=1000),
             dict(id="b-1", provider="q", model="m2", tier="smart", rpd=1000)]
    deps = make_deployments(tmp_path, specs)
    first, _, _ = run(deps, monkeypatch, lambda d, n: None)
    history = [{"role": "user", "content": "x"},
               {"role": "assistant", "content": None,
                "tool_calls": [{"id": "1", "type": "function", "function": {"name": "f", "arguments": "{}"}}]},
               {"role": "tool", "tool_call_id": "1", "content": "42"}]
    for _ in range(5):
        again, _, _ = run(deps, monkeypatch, lambda d, n: None, request(messages=history))
        assert again.model_used == first.model_used


def test_p2c_prefers_the_key_with_more_daily_headroom(tmp_path, monkeypatch):
    specs = [dict(id="big", provider="p", model="m", tier="smart", rpd=1000),
             dict(id="small", provider="p", model="m", tier="smart", rpd=10)]
    deps = make_deployments(tmp_path, specs)
    picks = []
    for i in range(8):
        result, _, _ = run(deps, monkeypatch, lambda d, n: None, request(conversation_id=f"c{i}"))
        picks.append(result.deployment_id)
    assert picks.count("big") >= 6


def test_daily_reserve_is_kept_for_conversations_in_progress(tmp_path, monkeypatch):
    specs = [dict(id="only", provider="p", model="m", tier="smart", rpd=10)]
    deps = make_deployments(tmp_path, specs)
    for i in range(9):
        result, _, _ = run(deps, monkeypatch, lambda d, n: None, request(conversation_id=f"n{i}"))
        assert isinstance(result, GatewayResponse)
    blocked, _, _ = run(deps, monkeypatch, lambda d, n: None, request(conversation_id="late"))
    assert isinstance(blocked, AllQuotaExhaustedError)
    history = [{"role": "user", "content": "x"}, {"role": "tool", "tool_call_id": "1", "content": "1"}]
    allowed, _, _ = run(deps, monkeypatch, lambda d, n: None, request(conversation_id="n0", messages=history))
    assert isinstance(allowed, GatewayResponse)


def test_admission_sheds_when_queue_is_full():
    async def scenario():
        gate = Admission(max_in_flight=1, max_waiting=0, max_wait_seconds=1.0)
        release = asyncio.Event()

        async def hold():
            async with gate.slot():
                await release.wait()
        holder = asyncio.create_task(hold())
        await asyncio.sleep(0)
        with pytest.raises(GatewayOverloadedError):
            async with gate.slot():
                pass
        release.set()
        await holder
        return gate.shed
    assert asyncio.run(scenario()) == 1
