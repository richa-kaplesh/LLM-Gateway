"""Rate card, real token usage and cost: one place computes cost, thinking tokens are billed."""
import types
import pytest

from app.core import pricing
from app.core.config import get_settings
from app.models.schemas import GatewayRequest
from app.clients import groq_client, gemini_client

S = get_settings()


def _req():
    return GatewayRequest(conversation_id="c", user_id="u", messages=[{"role": "user", "content": "hi"}])


# ── rate card ───────────────────────────────────────────────────────────────

def test_cost_is_input_plus_output_per_million():
    cost, known = pricing.price_usage(S.GROQ_MODEL, 1_000_000, 1_000_000)
    assert known and cost == pytest.approx(S.GROQ_INPUT_COST_PER_MILLION + S.GROQ_OUTPUT_COST_PER_MILLION)


def test_unknown_model_is_flagged_not_silently_priced():
    cost, known = pricing.price_usage("some-model-not-on-the-card", 1000, 1000)
    assert cost == 0.0 and known is False


def test_missing_token_counts_are_zero_not_a_crash():
    cost, known = pricing.price_usage(S.GEMINI_MODEL, None, None)
    assert cost == 0.0 and known is True


def test_cached_tokens_use_cached_price_and_are_clamped(monkeypatch):
    monkeypatch.setattr(S, "GROQ_CACHED_INPUT_COST_PER_MILLION", 0.01)
    full, _ = pricing.price_usage(S.GROQ_MODEL, 1_000_000, 0, cached_tokens=0)
    half, _ = pricing.price_usage(S.GROQ_MODEL, 1_000_000, 0, cached_tokens=500_000)
    assert half == pytest.approx(0.5 * S.GROQ_INPUT_COST_PER_MILLION + 0.5 * 0.01) and half < full
    over, _ = pricing.price_usage(S.GROQ_MODEL, 1000, 0, cached_tokens=10**9)   # provider bug
    assert over >= 0.0
    neg, _ = pricing.price_usage(S.GROQ_MODEL, 1000, 0, cached_tokens=-5)
    assert neg == pytest.approx(1000 * S.GROQ_INPUT_COST_PER_MILLION / 1e6)


def test_no_cached_price_configured_means_no_discount(monkeypatch):
    monkeypatch.setattr(S, "GROQ_CACHED_INPUT_COST_PER_MILLION", -1.0)
    a, _ = pricing.price_usage(S.GROQ_MODEL, 1000, 0, cached_tokens=0)
    b, _ = pricing.price_usage(S.GROQ_MODEL, 1000, 0, cached_tokens=1000)
    assert a == pytest.approx(b)


# ── Groq client reports real usage ──────────────────────────────────────────

class _GroqClient:
    def __init__(self, usage):
        msg = types.SimpleNamespace(content="ok", tool_calls=None)
        resp = types.SimpleNamespace(usage=usage, choices=[types.SimpleNamespace(message=msg, finish_reason="stop")])

        async def create(**kw):
            return resp
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=create))


@pytest.mark.asyncio
async def test_groq_response_carries_tokens_and_cost(monkeypatch):
    usage = types.SimpleNamespace(prompt_tokens=1200, completion_tokens=300,
                                  prompt_tokens_details=types.SimpleNamespace(cached_tokens=200))
    monkeypatch.setattr(groq_client, "_make_client", lambda key: _GroqClient(usage))
    r = await groq_client.complete(_req(), api_key="k")
    assert (r.input_tokens, r.output_tokens, r.cached_tokens) == (1200, 300, 200)
    assert r.price_known is True
    assert r.cost_usd == pytest.approx(pricing.price_usage(S.GROQ_MODEL, 1200, 300, 200)[0])


@pytest.mark.asyncio
async def test_groq_missing_usage_fields_do_not_fail_a_good_answer(monkeypatch):
    monkeypatch.setattr(groq_client, "_make_client",
                        lambda key: _GroqClient(types.SimpleNamespace(prompt_tokens=None, completion_tokens=None)))
    r = await groq_client.complete(_req(), api_key="k")
    assert r.content == "ok" and r.cost_usd == 0.0 and r.input_tokens == 0


# ── Gemini: thinking tokens are billed as output ────────────────────────────

class _GeminiClient:
    def __init__(self, meta):
        part = types.SimpleNamespace(function_call=None, thought_signature=None)
        cand = types.SimpleNamespace(content=types.SimpleNamespace(parts=[part]), finish_reason="STOP")
        resp = types.SimpleNamespace(usage_metadata=meta, function_calls=None, text="answer", candidates=[cand])

        async def generate_content(**kw):
            return resp
        self.aio = types.SimpleNamespace(models=types.SimpleNamespace(generate_content=generate_content))


@pytest.mark.asyncio
async def test_gemini_thinking_tokens_are_counted_as_output(monkeypatch):
    meta = types.SimpleNamespace(prompt_token_count=1000, candidates_token_count=200,
                                 thoughts_token_count=800, cached_content_token_count=None)
    monkeypatch.setattr(gemini_client, "_make_client", lambda key: _GeminiClient(meta))
    r = await gemini_client.complete(_req(), api_key="k")
    assert (r.input_tokens, r.output_tokens, r.thought_tokens) == (1000, 1000, 800)
    old_cost = (1000 * S.GEMINI_INPUT_COST_PER_MILLION + 200 * S.GEMINI_OUTPUT_COST_PER_MILLION) / 1e6
    assert r.cost_usd == pytest.approx(pricing.price_usage(S.GEMINI_MODEL, 1000, 1000)[0])
    assert r.cost_usd > old_cost          # the old formula under-charged by the thinking tokens


@pytest.mark.asyncio
async def test_gemini_none_token_fields_do_not_crash(monkeypatch):
    meta = types.SimpleNamespace(prompt_token_count=50, candidates_token_count=None,
                                 thoughts_token_count=None, cached_content_token_count=None)
    monkeypatch.setattr(gemini_client, "_make_client", lambda key: _GeminiClient(meta))
    r = await gemini_client.complete(_req(), api_key="k")
    assert r.input_tokens == 50 and r.output_tokens == 0


# ── tracker never fails because the schema is one step behind ───────────────

class _Pool:
    def __init__(self):
        self.sql = []

    async def execute(self, sql, *args):
        self.sql.append((sql, args))


@pytest.mark.asyncio
async def test_tracker_writes_token_columns_only_when_they_exist(monkeypatch):
    from app.tracker import tracker as T
    from app.models.schemas import GatewayResponse
    pool = _Pool()
    monkeypatch.setattr(T, "get_pool", lambda: pool)
    resp = GatewayResponse(model_used="m", provider_used="groq", cost_usd=0.1, latency_ms=5, cache_hit=False,
                           input_tokens=10, output_tokens=20, thought_tokens=3, cached_tokens=1)
    t = T.CostTracker()
    await t.log("u", "c", resp, "success")
    assert "input_tokens" not in pool.sql[-1][0] and len(pool.sql[-1][1]) == 14
    t.token_columns = True
    await t.log("u", "c", resp, "success")
    assert "input_tokens" in pool.sql[-1][0] and pool.sql[-1][1][-4:] == (10, 20, 3, 1)
    await t.log("u", "c", None, "error", error_type="X")                  # errors have no usage
    assert pool.sql[-1][1][-4:] == (None, None, None, None)
