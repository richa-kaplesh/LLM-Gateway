"""Tests for multi-key rotation logic.

Covered scenarios
─────────────────
1. rate_limit_then_other_provider
   Primary key A returns 429 → gateway tries other provider → succeeds.

2. both_providers_rate_limited_then_next_key
   Primary key A → 429.  Other provider key A → 429.
   Primary key B → succeeds (second key on the original provider).

3. provider_down_immediate_stop
   Primary provider returns a 5xx / connection error (ProviderUnavailableError).
   Gateway raises ProviderDownError immediately — no retries, no rotation to
   the other provider.

4. all_keys_exhausted
   All keys on both providers return 429.
   Gateway raises AllProvidersRateLimitedError with the soonest retry time.

5. provider_down_on_fallback_stops_immediately
   Primary → 429. Fallback → DOWN error. No further rotation.

How the tests work
──────────────────
- Section A: pure unit tests for classify_error() and KeyManager.
  No network calls, no provider SDKs required.
- Section B: integration tests for _route_with_key_rotation().
  We patch _attempt_provider so no real network calls happen.
  The KeyManager instances are injected fresh per test.
"""

import pytest
from unittest.mock import patch, MagicMock

from app.clients.exceptions import (
    ProviderRateLimitedError,
    ProviderUnavailableError,
    ProviderConnectionError,
    ProviderDownError,
    AllProvidersRateLimitedError,
    AllProvidersUnavailableError,
    CircuitOpenError,
    classify_error,
)
from app.clients.key_manager import KeyManager, _mask_key
import app.router.router as _router_module


# ── Section A: unit tests for classify_error() and KeyManager ─────────────

class TestClassifyError:
    def test_rate_limit_error_is_rate_limit(self):
        assert classify_error(ProviderRateLimitedError("429")) == "rate_limit"

    def test_circuit_open_is_rate_limit(self):
        assert classify_error(CircuitOpenError("open")) == "rate_limit"

    def test_connection_error_is_transient(self):
        assert classify_error(ProviderConnectionError("DNS failure")) == "transient"

    def test_unavailable_is_provider_down(self):
        # ProviderUnavailableError (5xx, bad key) = provider_down
        assert classify_error(ProviderUnavailableError("5xx")) == "provider_down"

    def test_generic_exception_is_provider_down(self):
        assert classify_error(ConnectionError("timeout")) == "provider_down"

    def test_provider_down_error_is_provider_down(self):
        assert classify_error(ProviderDownError("groq", RuntimeError("x"))) == "provider_down"


class TestKeyManager:
    def test_next_available_round_robins(self):
        mgr = KeyManager("groq", ["k1", "k2", "k3"])
        keys = [mgr.next_available().key for _ in range(6)]
        assert keys == ["k1", "k2", "k3", "k1", "k2", "k3"]

    def test_skips_cooldown_key(self):
        mgr = KeyManager("groq", ["k1", "k2"])
        ks = mgr.next_available()                          # k1
        mgr.record_rate_limited(ks, retry_after=3600)      # put k1 on cooldown
        ks2 = mgr.next_available()
        assert ks2 is not None
        assert ks2.key == "k2"

    def test_returns_none_when_all_on_cooldown(self):
        mgr = KeyManager("groq", ["k1", "k2"])
        ks1 = mgr.next_available()
        ks2 = mgr.next_available()
        mgr.record_rate_limited(ks1, retry_after=3600)
        mgr.record_rate_limited(ks2, retry_after=3600)
        assert mgr.next_available() is None

    def test_mask_key(self):
        assert _mask_key("gsk_abc1234XXXX") == "…XXXX"
        assert _mask_key("ab") == "…ab"

    def test_soonest_retry_seconds_is_nonnegative(self):
        mgr = KeyManager("groq", ["k1"])
        ks = mgr.next_available()
        mgr.record_rate_limited(ks, retry_after=10)
        s = mgr.soonest_retry_seconds()
        assert s is not None
        assert 0 <= s <= 10

    def test_skip_key_parameter(self):
        mgr = KeyManager("groq", ["k1", "k2", "k3"])
        ks = mgr.next_available(skip_key="k1")
        assert ks.key == "k2"

    def test_all_on_cooldown_reports_true(self):
        mgr = KeyManager("groq", ["k1"])
        ks = mgr.next_available()
        mgr.record_rate_limited(ks, retry_after=3600)
        assert mgr.all_on_cooldown()

    def test_status_summary_masks_keys(self):
        mgr = KeyManager("groq", ["gsk_abc1234XXXX"])
        summary = mgr.status_summary()
        assert summary[0]["key"] == "…XXXX"
        assert "gsk_abc1234XXXX" not in str(summary)

    def test_empty_key_list_raises(self):
        with pytest.raises(ValueError, match="empty key list"):
            KeyManager("groq", [])


# ── Helpers for integration tests ─────────────────────────────────────────

def _make_response(provider: str) -> MagicMock:
    """Minimal GatewayResponse-like object."""
    obj = MagicMock()
    obj.provider_used = provider
    return obj


def _make_request() -> MagicMock:
    req = MagicMock()
    req.conversation_id = "test-conv"
    req.user_id = "test-user"
    req.messages = [{"role": "user", "content": "hi"}]
    req.tools = None
    req.tool_choice = None
    req.cache_scope = None
    return req


def _rl(retry_after: float = 30) -> ProviderRateLimitedError:
    return ProviderRateLimitedError("rate limited", retry_after=retry_after)


def _conn() -> ProviderConnectionError:
    """Simulates Groq APIConnectionError (DNS/TCP failure)."""
    return ProviderConnectionError("connection refused")


def _server_down() -> ProviderUnavailableError:
    """Simulates a 5xx / auth failure — provider itself is broken."""
    return ProviderUnavailableError("500 server error")


def _make_managers(groq_keys: list[str], gemini_keys: list[str]) -> dict:
    return {
        "groq":   KeyManager("groq",   groq_keys),
        "gemini": KeyManager("gemini", gemini_keys),
    }


# ── Section B: integration tests for _route_with_key_rotation() ───────────

class TestRouteWithKeyRotation:
    """
    We patch _attempt_provider so no real network calls are made.
    We also inject a fresh _key_managers dict per test.
    """

    @pytest.mark.asyncio
    async def test_1_rate_limit_then_other_provider(self):
        """Primary (groq, key A) → 429.  Fallback (gemini, key A) → success."""
        calls = []

        async def mock_attempt(provider, request, estimated_tokens, reserve, api_key):
            calls.append((provider, api_key))
            if provider == "groq":
                raise _rl()
            return _make_response("gemini")

        managers = _make_managers(["groq_k1"], ["gem_k1"])

        with patch.object(_router_module, "_attempt_provider", side_effect=mock_attempt), \
             patch.object(_router_module, "_key_managers", managers):
            resp = await _router_module._route_with_key_rotation(
                "groq", _make_request(), estimated_tokens=100,
            )

        assert resp.provider_used == "gemini"
        assert calls == [("groq", "groq_k1"), ("gemini", "gem_k1")]

    @pytest.mark.asyncio
    async def test_2_both_providers_rate_limited_then_next_key(self):
        """groq k1 → 429.  gemini k1 → 429.  groq k2 → success."""
        calls = []

        async def mock_attempt(provider, request, estimated_tokens, reserve, api_key):
            calls.append((provider, api_key))
            if provider == "groq" and api_key == "groq_k1":
                raise _rl()
            if provider == "gemini" and api_key == "gem_k1":
                raise _rl()
            return _make_response(provider)   # groq k2 succeeds

        managers = _make_managers(["groq_k1", "groq_k2"], ["gem_k1"])

        with patch.object(_router_module, "_attempt_provider", side_effect=mock_attempt), \
             patch.object(_router_module, "_key_managers", managers):
            resp = await _router_module._route_with_key_rotation(
                "groq", _make_request(), estimated_tokens=100,
            )

        assert resp.provider_used == "groq"
        assert ("groq", "groq_k1") in calls
        assert ("gemini", "gem_k1") in calls
        assert ("groq", "groq_k2") in calls

    @pytest.mark.asyncio
    async def test_3_connection_error_falls_back_to_other_provider(self):
        """groq raises a connection error (DNS/TCP) — the router should
        fall back to Gemini immediately (the network may be fine on that side).
        This is what was BROKEN before: the old code stopped with a 503 even
        though Gemini was perfectly reachable."""
        calls = []

        async def mock_attempt(provider, request, estimated_tokens, reserve, api_key):
            calls.append(provider)
            if provider == "groq":
                raise _conn()     # DNS / TCP failure
            return _make_response("gemini")   # Gemini works fine

        managers = _make_managers(["groq_k1"], ["gem_k1"])

        with patch.object(_router_module, "_attempt_provider", side_effect=mock_attempt), \
             patch.object(_router_module, "_key_managers", managers):
            resp = await _router_module._route_with_key_rotation(
                "groq", _make_request(), estimated_tokens=100,
            )

        # Gemini succeeded, groq_k1 was NOT put on cooldown
        assert resp.provider_used == "gemini"
        assert calls == ["groq", "gemini"]

    @pytest.mark.asyncio
    async def test_3b_server_error_stops_immediately(self):
        """groq returns a 5xx / auth error — that IS a hard stop.
        Gemini should NOT be tried (the problem is with groq's config/server,
        not the network)."""
        calls = []

        async def mock_attempt(provider, request, estimated_tokens, reserve, api_key):
            calls.append(provider)
            if provider == "groq":
                raise _server_down()     # 5xx — hard stop
            return _make_response("gemini")

        managers = _make_managers(["groq_k1"], ["gem_k1"])

        with patch.object(_router_module, "_attempt_provider", side_effect=mock_attempt), \
             patch.object(_router_module, "_key_managers", managers):
            with pytest.raises(ProviderDownError) as exc_info:
                await _router_module._route_with_key_rotation(
                    "groq", _make_request(), estimated_tokens=100,
                )

        assert exc_info.value.provider == "groq"
        assert calls == ["groq"]   # gemini was never tried

    @pytest.mark.asyncio
    async def test_4_all_keys_exhausted(self):
        """Every key on every provider returns 429 →
        AllProvidersRateLimitedError with retry_after set."""
        calls = []

        async def mock_attempt(provider, request, estimated_tokens, reserve, api_key):
            calls.append((provider, api_key))
            raise _rl(retry_after=30)

        managers = _make_managers(["groq_k1", "groq_k2"], ["gem_k1", "gem_k2"])

        with patch.object(_router_module, "_attempt_provider", side_effect=mock_attempt), \
             patch.object(_router_module, "_key_managers", managers):
            with pytest.raises(AllProvidersRateLimitedError) as exc_info:
                await _router_module._route_with_key_rotation(
                    "groq", _make_request(), estimated_tokens=100,
                )

        err = exc_info.value
        assert err.retry_after is not None
        assert err.retry_after >= 0

        tried_providers = {p for p, _ in calls}
        tried_keys = {k for _, k in calls}
        assert "groq" in tried_providers
        assert "gemini" in tried_providers
        assert "groq_k1" in tried_keys
        assert "groq_k2" in tried_keys
        assert "gem_k1" in tried_keys
        assert "gem_k2" in tried_keys

    @pytest.mark.asyncio
    async def test_5_provider_down_on_fallback_stops_immediately(self):
        """groq k1 → 429.  gemini returns a DOWN error → ProviderDownError
        for gemini; groq k2 is NEVER tried."""
        calls = []

        async def mock_attempt(provider, request, estimated_tokens, reserve, api_key):
            calls.append((provider, api_key))
            if provider == "groq" and api_key == "groq_k1":
                raise _rl()
            if provider == "gemini":
                raise _server_down()
            return _make_response(provider)

        managers = _make_managers(["groq_k1", "groq_k2"], ["gem_k1"])

        with patch.object(_router_module, "_attempt_provider", side_effect=mock_attempt), \
             patch.object(_router_module, "_key_managers", managers):
            with pytest.raises(ProviderDownError) as exc_info:
                await _router_module._route_with_key_rotation(
                    "groq", _make_request(), estimated_tokens=100,
                )

        assert exc_info.value.provider == "gemini"
        assert ("groq", "groq_k2") not in calls   # groq k2 was never tried
