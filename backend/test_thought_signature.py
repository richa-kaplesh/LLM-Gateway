"""Offline regression tests for the thought_signature / bytes-serialization fix.

Run from backend/ :   python test_thought_signature.py
No API keys or network are needed; provider clients are faked.
"""
import asyncio
import base64
import os

os.environ.setdefault("JINA_API_KEY", "test")
os.environ.setdefault("GROQ_API_KEY", "gsk_test_1111")
os.environ.setdefault("GEMINI_API_KEY", "AIza_test_2222")

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from google.genai import types

from app.models.schemas import GatewayRequest, GatewayResponse
from app.clients import gemini_client, groq_client
from app.clients.exceptions import (
    InvalidRequestError, ProviderConnectionError, ProviderRateLimitedError,
    AllProvidersRateLimitedError,
)
from app.router import router as router_mod
from app.router.bucket import pin_conversation

import types as _types


def fake_client(name, fn):
    """The router logs `client_module.__name__`, so a fake provider must look like a module."""
    m = _types.ModuleType(name)
    m.complete = fn
    return m


SIG = b"\x1f\x8b\x08\x00\xff\xfe"          # starts like gzip; invalid UTF-8 at index 1
SIG_B64 = base64.b64encode(SIG).decode()


def _resp(provider="gemini", **kw):
    return GatewayResponse(model_used="m", provider_used=provider, cost_usd=0.0,
                           latency_ms=1.0, cache_hit=False, **kw)


def _tool_call(sig):
    return {"id": "call_0", "type": "function",
            "function": {"name": "get_weather", "arguments": '{"city": "Delhi"}'},
            "thought_signature": sig}


# 1 ─ the original bug: bytes inside tool_calls must not break the HTTP response ───────────
def test_response_with_bytes_serializes():
    app = FastAPI()

    @app.post("/q", response_model=GatewayResponse)
    async def q():
        return _resp(tool_calls=[_tool_call(SIG)])          # raw bytes, as before the fix

    r = TestClient(app).post("/q")
    assert r.status_code == 200, r.text
    assert r.json()["tool_calls"][0]["thought_signature"] == SIG_B64


# 2 ─ gemini adapter: encode on the way out, decode on the way in, lossless ────────────────
def test_signature_round_trip():
    part = types.Part.from_function_call(name="get_weather", args={"city": "Delhi"})
    part.thought_signature = SIG
    out = gemini_client.normalize_tool_calls([part])
    assert out[0]["thought_signature"] == SIG_B64 and isinstance(out[0]["thought_signature"], str)

    history = [
        {"role": "user", "content": "weather in Delhi?"},
        {"role": "assistant", "content": None, "tool_calls": out},
    ]
    contents, _ = gemini_client.convert_messages(history)
    replayed = contents[1].parts[0].thought_signature
    assert isinstance(replayed, bytes) and replayed == SIG      # bytes, NOT the base64 string


def test_missing_signature_is_none():
    part = types.Part.from_function_call(name="f", args={})
    assert gemini_client.normalize_tool_calls([part])[0]["thought_signature"] is None
    contents, _ = gemini_client.convert_messages(
        [{"role": "assistant", "tool_calls": [_tool_call(None)]}])
    assert contents[0].parts[0].thought_signature is None


# 3 ─ bad client input is a 400 (InvalidRequestError), never relabelled as a provider outage ─
def test_bad_input_is_invalid_request_not_provider_down():
    for bad in ({"tool_calls": [_tool_call("%%%not-base64%%%")]},
                {"tool_calls": [{**_tool_call(None),
                                 "function": {"name": "f", "arguments": "{oops"}}]}):
        req = GatewayRequest(conversation_id="c", user_id="u", messages=[
            {"role": "user", "content": "hi"}, {"role": "assistant", **bad}])
        try:
            asyncio.run(gemini_client.complete(req, api_key="AIza_dummy"))
        except InvalidRequestError:
            pass
        else:
            raise AssertionError("expected InvalidRequestError")


# 4 ─ Groq must never receive Gemini's field ───────────────────────────────────────────────
def test_groq_strips_thought_signature():
    msgs = [{"role": "assistant", "tool_calls": [_tool_call(SIG_B64)]}, {"role": "user", "content": "x"}]
    cleaned = groq_client._clean_messages(msgs)
    assert "thought_signature" not in cleaned[0]["tool_calls"][0]
    assert cleaned[0]["tool_calls"][0]["function"]["name"] == "get_weather"
    assert "thought_signature" in msgs[0]["tool_calls"][0]       # input not mutated


# 5 ─ cache: tool traffic is neither served from nor stored in the cache ───────────────────
def test_cache_skipped_for_tool_requests():
    calls = []
    original = router_mod.get_cache_for
    router_mod.get_cache_for = lambda req: calls.append(req) or None
    try:
        async def ok(request, api_key=None):
            return _resp("groq", content="ok")
        router_mod.CLIENTS = {"groq": fake_client("groq_client", ok), "gemini": fake_client("gemini_client", ok)}
        pin_conversation("t-cache", "groq")
        tools = [{"type": "function", "function": {"name": "f", "parameters": {}}}]
        asyncio.run(router_mod.route(GatewayRequest(
            conversation_id="t-cache", user_id="u", cache_scope="global",
            messages=[{"role": "user", "content": "hi"}], tools=tools)))
        assert calls == [], "cache must not be consulted when tools are present"
        asyncio.run(router_mod.route(GatewayRequest(
            conversation_id="t-cache", user_id="u", cache_scope="global",
            messages=[{"role": "user", "content": "hi"}])))
        assert len(calls) == 1, "plain requests still use the cache"
    finally:
        router_mod.get_cache_for = original


def test_cache_set_ignores_tool_call_responses():
    from app.cache.cache import SemanticCache, EmbedResult
    import numpy as np
    c = SemanticCache()
    emb = EmbedResult(vector=np.ones(4, dtype=np.float32), tokens=1, latency_ms=1, cost_usd=0)
    req = GatewayRequest(conversation_id="c", user_id="u", messages=[{"role": "user", "content": "q"}])
    c.set(req, _resp(tool_calls=[_tool_call(SIG_B64)]), emb)
    assert c.responses == []
    c.set(req, _resp(content="plain"), emb)
    assert len(c.responses) == 1


# 6 ─ routing with tool history ─────────────────────────────────────────────────────────────
def _tool_history_request(conv):
    return GatewayRequest(conversation_id=conv, user_id="u", messages=[
        {"role": "user", "content": "weather?"},
        {"role": "assistant", "content": None, "tool_calls": [_tool_call(SIG_B64)]},
        {"role": "tool", "tool_call_id": "call_0", "content": "31C"},
    ])


def test_groq_pinned_tool_history_never_falls_back_to_gemini():
    seen = []

    async def groq(request, api_key=None):
        seen.append("groq"); raise ProviderRateLimitedError("429", retry_after=5)

    async def gemini(request, api_key=None):
        seen.append("gemini"); return _resp("gemini", content="should never happen")

    router_mod.CLIENTS = {"groq": fake_client("groq_client", groq),
                          "gemini": fake_client("gemini_client", gemini)}
    pin_conversation("t-groq", "groq")
    try:
        asyncio.run(router_mod.route(_tool_history_request("t-groq")))
    except AllProvidersRateLimitedError:
        pass
    else:
        raise AssertionError("expected AllProvidersRateLimitedError")
    assert "gemini" not in seen, f"Groq-made tool history leaked to Gemini: {seen}"


def test_gemini_pinned_tool_history_may_fall_back_to_groq():
    async def gemini(request, api_key=None):
        raise ProviderConnectionError("timeout")

    async def groq(request, api_key=None):
        return _resp("groq", content="served by groq")

    router_mod.CLIENTS = {"groq": fake_client("groq_client", groq),
                          "gemini": fake_client("gemini_client", gemini)}
    pin_conversation("t-gem", "gemini")
    out = asyncio.run(router_mod.route(_tool_history_request("t-gem")))
    assert out.provider_used == "groq" and out.was_fallback is True


# 7 ─ main.py: a non-serializable response is caught, tracked as an error, and not a bare 500 ─
def test_handler_guard_reports_serialization_failure():
    from app import main as main_mod
    tracked = []

    async def fake_route(request):
        return _resp(tool_calls=[{"bad": object()}])     # not JSON-encodable, not bytes

    async def fake_track(request, response, status, error_type=None):
        tracked.append((status, error_type))

    main_mod.route, main_mod._track = fake_route, fake_track
    r = TestClient(main_mod.app).post("/query", json={
        "conversation_id": "c", "user_id": "u", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 500 and "could not be serialized" in r.json()["detail"], r.text
    assert tracked == [("error", "ResponseSerializationError")], tracked   # NOT logged as success


# 8 ─ a LOCAL rpm/tpm budget miss must not bench API keys (the 503 cascade seen on Render) ───
def _reset_router_state():
    from app.router.bucket import tpm_buckets, rpm_buckets
    from app.router.circuit_breaker import breakers
    for b in (*tpm_buckets.values(), *rpm_buckets.values()):
        b.tokens = b.capacity
    for br in breakers.values():
        br.state, br.consecutive_failures, br.paused_until = "closed", 0, 0.0
    for km in router_mod._key_managers.values():
        for ks in km._states:
            ks._cooldown_until = 0.0


def test_local_capacity_miss_does_not_bench_keys():
    from app.router.bucket import tpm_buckets
    from app.router.circuit_breaker import breakers
    _reset_router_state()

    async def groq_ok(request, api_key=None):
        return _resp("groq", content="ok")

    async def gemini_never(request, api_key=None):
        raise AssertionError("gemini circuit is open: must not be called")

    router_mod.CLIENTS = {"groq": fake_client("groq_client", groq_ok),
                          "gemini": fake_client("gemini_client", gemini_never)}
    try:
        breakers["gemini"].state, breakers["gemini"].opened_at = "open", breakers["gemini"]._now()
        tpm_buckets["groq"].tokens = 0                                   # local budget spent
        mk = lambda c: GatewayRequest(conversation_id=c, user_id="u",
                                      messages=[{"role": "user", "content": "hi"}])
        try:
            asyncio.run(router_mod.route(mk("cap-1")))
        except AllProvidersRateLimitedError as e:
            assert e.retry_after and e.retry_after > 0, "must give a real Retry-After"
        else:
            raise AssertionError("expected AllProvidersRateLimitedError")
        assert all(ks.available for ks in router_mod._key_managers["groq"]._states), \
            "a local budget miss must not put API keys on cooldown"
        _reset_router_state()                                            # budget refills
        breakers["gemini"].state, breakers["gemini"].opened_at = "open", breakers["gemini"]._now()
        assert asyncio.run(router_mod.route(mk("cap-2"))).provider_used == "groq"
    finally:
        _reset_router_state()


def test_pinned_tool_history_capacity_miss_does_not_bench_keys():
    from app.router.bucket import tpm_buckets
    _reset_router_state()

    async def must_not_run(request, api_key=None):
        raise AssertionError("no call expected")

    router_mod.CLIENTS = {"groq": fake_client("groq_client", must_not_run),
                          "gemini": fake_client("gemini_client", must_not_run)}
    try:
        pin_conversation("cap-pin", "groq")
        tpm_buckets["groq"].tokens = 0
        try:
            asyncio.run(router_mod.route(_tool_history_request("cap-pin")))
        except AllProvidersRateLimitedError:
            pass
        else:
            raise AssertionError("expected AllProvidersRateLimitedError")
        assert all(ks.available for ks in router_mod._key_managers["groq"]._states)
    finally:
        _reset_router_state()


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t(); print(f"PASS  {t.__name__}")
        except Exception as e:
            failed += 1; print(f"FAIL  {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)