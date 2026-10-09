"""Regression tests that exercise the REAL _attempt_provider() (breaker + capacity +
key rotation together). test_multikey.py mocks _attempt_provider, so it cannot catch
bugs in how those layers interact. Only the provider SDK clients are stubbed here."""
import time
import pytest

from app.models.schemas import GatewayRequest, GatewayResponse
from app.clients import exceptions as ex
from app.clients.key_manager import KeyManager
from app.router import router as R
from app.router import bucket as B
from app.router.circuit_breaker import CircuitBreaker, breakers


class _Tracker:
    async def log_breaker_transition(self, *a): pass


class _Stub:
    def __init__(self, name, per_key, calls):
        self.__name__ = f"app.clients.{name}_client"
        self.name, self.per_key, self.calls = name, per_key, calls

    async def complete(self, request, api_key=None):
        self.calls.append((self.name, api_key))
        behavior = self.per_key.get(api_key, self.per_key["*"])
        if isinstance(behavior, Exception):
            raise behavior
        return GatewayResponse(content="hi", model_used="m", provider_used=self.name,
                               cost_usd=0, latency_ms=1, cache_hit=False)


OK = object()


@pytest.fixture
def world(monkeypatch):
    calls = []

    def build(groq, gemini):
        s = B.settings
        for p, rpm, tpm in (("groq", s.GROQ_RPM, s.GROQ_TPM), ("gemini", s.GEMINI_RPM, s.GEMINI_TPM)):
            B.rpm_buckets[p] = B.TokenBucket(rpm, rpm / 60)
            B.tpm_buckets[p] = B.TokenBucket(tpm, tpm / 60)
        breakers["groq"], breakers["gemini"] = CircuitBreaker(), CircuitBreaker()
        B.conversation_provider_map.clear()
        monkeypatch.setattr(R, "tracker", _Tracker())
        monkeypatch.setattr(R, "_key_managers", {"groq": KeyManager("groq", ["gA", "gB", "gC"]),
                                                  "gemini": KeyManager("gemini", ["mA", "mB"])})
        monkeypatch.setattr(R, "CLIENTS", {"groq": _Stub("groq", groq, calls),
                                           "gemini": _Stub("gemini", gemini, calls)})
        return calls
    return build


def _req():
    return GatewayRequest(conversation_id="c", user_id="u", messages=[{"role": "user", "content": "hello there"}])


async def _go(provider="groq"):
    B.conversation_provider_map["c"] = provider
    return await R.route(_req())


@pytest.mark.asyncio
async def test_open_circuit_falls_over_instead_of_503(world):
    world({"*": OK}, {"*": OK})
    breakers["groq"].state, breakers["groq"].opened_at = "open", time.monotonic()
    resp = await _go("groq")
    assert resp.provider_used == "gemini"


@pytest.mark.asyncio
async def test_both_circuits_open_is_503_not_429(world):
    world({"*": OK}, {"*": OK})
    for p in breakers:
        breakers[p].state, breakers[p].opened_at = "open", time.monotonic()
    with pytest.raises(ex.AllProvidersUnavailableError) as e:
        await _go("groq")
    assert e.value.retry_after is not None


@pytest.mark.asyncio
async def test_second_key_on_same_provider_is_really_tried(world):
    """gA 429, Gemini 429 -> must reach gB. (Was blocked by a provider-wide pause.)"""
    calls = world({"gA": ex.ProviderRateLimitedError("429", 30), "*": OK},
                  {"*": ex.ProviderRateLimitedError("429", 30)})
    resp = await _go("groq")
    assert resp.provider_used == "groq"
    assert ("groq", "gB") in calls
    km = R._key_managers["groq"]
    assert [k["available"] for k in km.status_summary()] == [False, True, True]


@pytest.mark.asyncio
async def test_429_does_not_open_breaker(world):
    world({"*": ex.ProviderRateLimitedError("429", 30)}, {"*": OK})
    for _ in range(5):
        await _go("groq")
    assert breakers["groq"].state == "closed"
    assert breakers["groq"].consecutive_failures == 0


@pytest.mark.asyncio
async def test_hours_long_retry_after_is_capped(world):
    world({"*": ex.ProviderRateLimitedError("daily cap", 7200)}, {"*": OK})
    await _go("groq")
    remaining = max(k["cooldown_remaining_s"] for k in R._key_managers["groq"].status_summary())
    assert 0 < remaining <= KeyManager.MAX_COOLDOWN_SECONDS


@pytest.mark.asyncio
async def test_timeout_is_not_retried_against_same_provider(world):
    calls = world({"*": ex.ProviderConnectionError("timeout")}, {"*": OK})
    resp = await _go("groq")
    assert resp.provider_used == "gemini"
    assert sum(1 for p, _ in calls if p == "groq") == 1


@pytest.mark.asyncio
async def test_unused_reservation_is_refunded_when_keys_on_cooldown(world):
    world({"*": OK}, {"*": OK})
    for ks in R._key_managers["groq"]._states:
        ks.put_on_cooldown(100)
    before = B.tpm_buckets["groq"].tokens
    await _go("groq")                       # served by gemini
    assert B.tpm_buckets["groq"].tokens >= before - 5


@pytest.mark.asyncio
async def test_server_error_still_stops_immediately(world):
    """Existing design decision (see test_multikey::test_3b): 5xx/auth => no fallback."""
    calls = world({"*": ex.ProviderUnavailableError("401 invalid api key")}, {"*": OK})
    with pytest.raises(ex.AllProvidersUnavailableError, match="Provider 'groq' is down"):
        await _go("groq")
    assert all(p == "groq" for p, _ in calls)


# ── a rejected key is skipped, not fatal ───────────────────────────────────

@pytest.mark.asyncio
async def test_bad_key_is_skipped_and_next_key_on_same_provider_is_used(world):
    calls = world({"gA": ex.ProviderKeyError("401 invalid api key"), "*": OK}, {"*": OK})
    resp = await _go("groq")
    assert resp.provider_used == "groq"
    assert ("groq", "gB") in calls
    km = R._key_managers["groq"]
    assert [k["auth_failed"] for k in km.status_summary()] == [True, False, False]
    assert breakers["groq"].state == "closed" and breakers["groq"].consecutive_failures == 0


@pytest.mark.asyncio
async def test_bad_key_is_not_retried_while_benched(world):
    calls = world({"gA": ex.ProviderKeyError("401"), "*": OK}, {"*": OK})
    await _go("groq")
    await _go("groq")
    assert sum(1 for c in calls if c == ("groq", "gA")) == 1


@pytest.mark.asyncio
async def test_all_keys_of_a_provider_rejected_falls_back_to_the_other_provider(world):
    world({"*": ex.ProviderKeyError("401")}, {"*": OK})
    resp = await _go("groq")
    assert resp.provider_used == "gemini"


@pytest.mark.asyncio
async def test_all_keys_everywhere_rejected_gives_clear_503(world):
    world({"*": ex.ProviderKeyError("401 invalid api key")}, {"*": ex.ProviderKeyError("400 API key not valid")})
    with pytest.raises(ex.AllProvidersUnavailableError, match="key\\(s\\) rejected"):
        await _go("groq")


@pytest.mark.asyncio
async def test_bad_key_on_fallback_side_is_skipped_too(world):
    calls = world({"*": ex.ProviderRateLimitedError("429", 30)}, {"mA": ex.ProviderKeyError("bad"), "*": OK})
    resp = await _go("groq")
    assert resp.provider_used == "gemini"
    assert ("gemini", "mB") in calls
