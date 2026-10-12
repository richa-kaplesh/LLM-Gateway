"""Per-key RPM/TPM buckets: each API key has its own budget; provider capacity = sum of keys."""
import pytest

from app.models.schemas import GatewayRequest, GatewayResponse
from app.clients import exceptions as ex
from app.clients.key_manager import KeyManager
from app.router import router as R
from app.router import bucket as B
from app.router.circuit_breaker import CircuitBreaker, breakers


class _Tracker:
    async def log_breaker_transition(self, *a, **k):
        pass


class _Stub:
    def __init__(self, name, calls):
        self.__name__ = f"app.clients.{name}_client"
        self.name, self.calls = name, calls

    async def complete(self, request, api_key=None):
        self.calls.append((self.name, api_key))
        return GatewayResponse(content="hi", model_used="m", provider_used=self.name,
                               cost_usd=0, latency_ms=1, cache_hit=False)


@pytest.fixture
def world(monkeypatch):
    calls = []
    B.reset_buckets()
    breakers["groq"], breakers["gemini"] = CircuitBreaker(), CircuitBreaker()
    B.conversation_provider_map.clear()
    monkeypatch.setattr(R, "tracker", _Tracker())
    monkeypatch.setattr(R, "_key_managers", {"groq": KeyManager("groq", ["gA", "gB", "gC"]),
                                              "gemini": KeyManager("gemini", ["mA", "mB"])})
    monkeypatch.setattr(R, "CLIENTS", {"groq": _Stub("groq", calls), "gemini": _Stub("gemini", calls)})
    monkeypatch.setattr(B.settings, "GROQ_RPM", 2)
    monkeypatch.setattr(B.settings, "GROQ_TPM", 10_000)
    monkeypatch.setattr(B.settings, "GEMINI_RPM", 1)
    monkeypatch.setattr(B.settings, "GEMINI_TPM", 10_000)
    return calls


def _req(cid="c"):
    return GatewayRequest(conversation_id=cid, user_id="u", messages=[{"role": "user", "content": "hello there"}])


async def _go(cid, provider="groq"):
    B.conversation_provider_map[cid] = provider
    return await R.route(_req(cid))


def test_each_key_has_its_own_bucket():
    B.reset_buckets()
    a, b = B.buckets_for("groq", "kA"), B.buckets_for("groq", "kB")
    assert a is not b and a is B.buckets_for("groq", "kA")
    a[0].consume(); a[1].consume(500)
    assert b[0].tokens == b[0].capacity and b[1].tokens == b[1].capacity


def test_reserve_spends_only_that_key():
    B.reset_buckets()
    ok, _ = B.reserve_capacity("groq", 100, "kA")
    assert ok
    assert B.buckets_for("groq", "kB")[1].tokens == B.buckets_for("groq", "kB")[1].capacity


def test_failed_tpm_check_does_not_spend_rpm():
    B.reset_buckets()
    rpm, tpm = B.buckets_for("groq", "kA")
    tpm.tokens = 10
    before = rpm.tokens
    ok, wait = B.reserve_capacity("groq", 500, "kA")
    assert not ok and wait > 0 and rpm.tokens == before


def test_release_refunds_that_key():
    B.reset_buckets()
    B.reserve_capacity("groq", 300, "kA")
    B.release_capacity("groq", 300, "kA")
    rpm, tpm = B.buckets_for("groq", "kA")
    assert rpm.tokens == rpm.capacity and tpm.tokens == tpm.capacity


def test_too_big_for_a_key_never_fits():
    B.reset_buckets()
    assert B.key_wait("groq", "kA", B.per_key_limits("groq")[1] + 1) is None


@pytest.mark.asyncio
async def test_capacity_is_summed_across_keys(world):
    """2 RPM per key x 3 keys = 6 requests served by groq before it runs dry."""
    served = [await _go(f"c{i}") for i in range(6)]
    assert all(r.provider_used == "groq" for r in served)
    used = [k for p, k in world if p == "groq"]
    assert sorted(set(used)) == ["gA", "gB", "gC"]
    assert all(used.count(k) == 2 for k in ("gA", "gB", "gC"))   # no key exceeded its own budget


@pytest.mark.asyncio
async def test_exhausted_key_is_skipped_not_cooled_down(world):
    for _ in range(2):
        B.reserve_capacity("groq", 10, "gA")                       # gA is out of RPM
    resp = await _go("c")
    assert resp.provider_used == "groq"
    assert ("groq", "gA") not in world
    assert R._key_managers["groq"].keys()[0].available            # local limit != provider 429


@pytest.mark.asyncio
async def test_all_groq_keys_dry_falls_back_to_gemini(world):
    for k in ("gA", "gB", "gC"):
        for _ in range(2):
            B.reserve_capacity("groq", 10, k)
    resp = await _go("c")
    assert resp.provider_used == "gemini"


@pytest.mark.asyncio
async def test_everything_dry_is_429_with_retry_after(world):
    for k in ("gA", "gB", "gC"):
        for _ in range(2):
            B.reserve_capacity("groq", 10, k)
    for k in ("mA", "mB"):
        B.reserve_capacity("gemini", 10, k)
    with pytest.raises(ex.AllProvidersRateLimitedError) as e:
        await _go("c")
    assert e.value.retry_after is not None and 0 < e.value.retry_after <= 60


@pytest.mark.asyncio
async def test_tool_history_pinned_conversation_uses_other_groq_keys(world):
    for _ in range(2):
        B.reserve_capacity("groq", 10, "gA")
    B.conversation_provider_map["t"] = "groq"
    req = GatewayRequest(conversation_id="t", user_id="u", messages=[
        {"role": "user", "content": "x"}, {"role": "tool", "content": "r", "tool_call_id": "1"}])
    resp = await R.route(req)
    assert resp.provider_used == "groq" and ("groq", "gA") not in world


def test_weights_follow_key_count(world):
    w = R._weights()
    assert w["groq"] == 2 * 3 and w["gemini"] == 1 * 2


@pytest.mark.asyncio
async def test_one_circuit_open_other_provider_keys_dry_is_429_not_503(world):
    """Production log: 'no provider available: gemini circuit is open'. Groq keys were only
    out of per-key budget (not down), so the right answer is 429 + Retry-After, not 503."""
    breakers["gemini"].state = "open"
    import time as _t
    breakers["gemini"].opened_at = _t.monotonic()
    for k in ("gA", "gB", "gC"):
        for _ in range(2):
            B.reserve_capacity("groq", 10, k)
    with pytest.raises(ex.AllProvidersRateLimitedError) as e:
        await _go("c", provider="gemini")
    assert e.value.retry_after is not None


@pytest.mark.asyncio
async def test_one_circuit_open_other_provider_keys_cooling_is_429(world):
    breakers["gemini"].state = "open"
    import time as _t
    breakers["gemini"].opened_at = _t.monotonic()
    for ks in R._key_managers["groq"].keys():
        ks.put_on_cooldown(30)
    with pytest.raises(ex.AllProvidersRateLimitedError):
        await _go("c", provider="gemini")


@pytest.mark.asyncio
async def test_busy_keys_wait_for_capacity_instead_of_failing(world, monkeypatch):
    """Every key is out of RPM but one refills within ~1 s: the request waits and is answered."""
    monkeypatch.setattr(B.settings, "GROQ_RPM", 60)         # refills 1 request/second/key
    monkeypatch.setattr(B.settings, "GEMINI_RPM", 60)
    monkeypatch.setattr(R.settings, "MAX_QUEUE_WAIT_SECONDS", 5.0)
    B.reset_buckets()
    for p, keys in (("groq", ("gA", "gB", "gC")), ("gemini", ("mA", "mB"))):
        for k in keys:
            B.buckets_for(p, k)[0].tokens = 0.0
    resp = await _go("c")
    assert resp.content == "hi"


@pytest.mark.asyncio
async def test_wait_longer_than_cap_fails_fast_with_429(world, monkeypatch):
    monkeypatch.setattr(R.settings, "MAX_QUEUE_WAIT_SECONDS", 0.2)
    for k in ("gA", "gB", "gC"):
        for _ in range(2):
            B.reserve_capacity("groq", 10, k)
    for k in ("mA", "mB"):
        B.reserve_capacity("gemini", 10, k)
    with pytest.raises(ex.AllProvidersRateLimitedError):
        await _go("c")
