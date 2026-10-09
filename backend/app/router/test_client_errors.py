"""How each provider client classifies upstream errors. The router's policy depends on
this: transient errors (ProviderConnectionError) fall back to the other provider; a rejected
key (ProviderKeyError) benches that key and tries the next one."""
import httpx
import groq
import pytest
from google.genai import errors as genai_errors

import app.clients.gemini_client as gemini
import app.clients.groq_client as groq_client
from app.clients import exceptions as ex
from app.models.schemas import GatewayRequest


def _req():
    return GatewayRequest(conversation_id="c", user_id="u", messages=[{"role": "user", "content": "hi"}])


def _http(code):
    return httpx.Response(code, request=httpx.Request("POST", "http://x"))


class _FakeGemini:
    def __init__(self, exc):
        self.exc, self.aio, self.models = exc, self, self

    async def generate_content(self, **kw):
        raise self.exc


class _FakeGroq:
    def __init__(self, exc):
        self.exc, self.chat, self.completions = exc, self, self

    async def create(self, **kw):
        raise self.exc


async def _call(module, fake, monkeypatch):
    monkeypatch.setattr(module, "_make_client", lambda key: fake)
    with pytest.raises(Exception) as e:
        await module.complete(_req(), api_key="k")
    return e.value


@pytest.mark.asyncio
async def test_gemini_503_overload_falls_back(monkeypatch):
    err = genai_errors.ServerError(503, {"error": {"message": "high demand", "status": "UNAVAILABLE"}})
    assert isinstance(await _call(gemini, _FakeGemini(err), monkeypatch), ex.ProviderConnectionError)


@pytest.mark.asyncio
async def test_gemini_404_is_a_key_level_error(monkeypatch):
    """Wrong model / no access for THIS key's project: bench the key, try the next one."""
    err = genai_errors.ClientError(404, {"error": {"message": "not found", "status": "NOT_FOUND"}})
    assert isinstance(await _call(gemini, _FakeGemini(err), monkeypatch), ex.ProviderKeyError)


@pytest.mark.asyncio
async def test_gemini_invalid_key_comes_back_as_400_and_is_a_key_error(monkeypatch):
    """Gemini reports a bad key as HTTP 400 'API key not valid', not 401."""
    err = genai_errors.ClientError(400, {"error": {"message": "API key not valid. Please pass a valid API key.",
                                                    "status": "INVALID_ARGUMENT"}})
    assert isinstance(await _call(gemini, _FakeGemini(err), monkeypatch), ex.ProviderKeyError)


@pytest.mark.asyncio
async def test_gemini_other_400_is_still_an_invalid_request(monkeypatch):
    err = genai_errors.ClientError(400, {"error": {"message": "Request contains an invalid argument.",
                                                    "status": "INVALID_ARGUMENT"}})
    assert isinstance(await _call(gemini, _FakeGemini(err), monkeypatch), ex.InvalidRequestError)


@pytest.mark.asyncio
async def test_gemini_429_is_rate_limit(monkeypatch):
    err = genai_errors.ClientError(429, {"error": {"message": "quota", "status": "RESOURCE_EXHAUSTED"}})
    assert isinstance(await _call(gemini, _FakeGemini(err), monkeypatch), ex.ProviderRateLimitedError)


@pytest.mark.asyncio
async def test_groq_503_falls_back(monkeypatch):
    err = groq.InternalServerError("over capacity", response=_http(503), body=None)
    assert isinstance(await _call(groq_client, _FakeGroq(err), monkeypatch), ex.ProviderConnectionError)


@pytest.mark.asyncio
async def test_groq_401_is_a_key_level_error(monkeypatch):
    err = groq.AuthenticationError("Invalid API Key", response=_http(401), body=None)
    e = await _call(groq_client, _FakeGroq(err), monkeypatch)
    assert isinstance(e, ex.ProviderKeyError)
    assert "Invalid API Key" in str(e)