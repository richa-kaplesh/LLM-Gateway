# LLM Gateway

A lightweight API gateway that sits between client applications and multiple LLM providers (Groq, Gemini), centralizing routing, rate limiting, caching, fallback, and cost tracking behind a single, provider-agnostic API.

**Live backend:** https://llm-gateway-qewa.onrender.com/docs
**Live frontend:** https://llm-gateway-self.vercel.app/

## Why this exists

Calling an LLM provider's SDK directly from application code couples that code to one provider's request/response shape, gives no protection against exhausting that provider's rate limits, and leaves cost/usage invisible. This gateway solves that by giving callers **one consistent API** — `messages`, `tools`, `tool_choice` in an OpenAI-compatible shape — and handling everything provider-specific internally.

## Architecture

```
Client (e.g. QueryMind)
      │
      │  POST /query  { conversation_id, user_id, messages, tools?, tool_choice? }
      ▼
┌─────────────────────────────────────────────┐
│                LLM Gateway                    │
│                                                │
│  1. Semantic cache check (Jina embeddings)    │
│  2. Sticky provider routing (per conversation)│
│  3. Token-bucket rate limiting (per provider) │
│  4. Provider call (Groq or Gemini)            │
│  5. Fallback to other provider on failure     │
│  6. Cost/token/latency tracking               │
└─────────────────────────────────────────────┘
      │                           │
      ▼                           ▼
  Groq API                   Gemini API
  (OpenAI-compatible)        (native SDK, translated)
```

## Core design decisions

- **One request shape, provider-specific translation internally.** `groq_client.py` passes `messages` through almost unchanged (Groq is OpenAI-compatible). `gemini_client.py` translates OpenAI-style messages/tools/tool_choice into Gemini's native `Content`/`Part`/`FunctionDeclaration` shapes. Both return the same normalized `GatewayResponse`, so callers never see provider differences.

- **Sticky routing per conversation.** The first message of a conversation picks a provider (via token-bucket availability); every subsequent message in that conversation reuses the same provider, so a multi-turn exchange doesn't get split across models with different message-format expectations.

- **Token bucket rate limiting, per provider.** Each provider has a bucket sized to its real rate limit (requests/minute), refilling continuously. A request only proceeds if the bucket for the selected provider has capacity — this prevents one caller from exhausting a provider's quota for everyone.

- **Fallback, not blind retry.** If the primary provider fails, the gateway tries the other provider and re-pins the conversation to it. A distinct `InvalidRequestError` (malformed request) skips fallback entirely — no point burning a second API call on a request that will fail everywhere.

- **Semantic caching.** Repeated or paraphrased queries (matched via Jina embeddings + cosine similarity, not exact string match) return a cached response at zero cost/latency. Tool-calling requests are never cached, since replaying a cached answer would skip the actual tool execution.

- **Stateless for message content, stateful for routing.** The gateway never stores conversation history — callers send the full message history each time. It only tracks `conversation_id → provider` and per-provider bucket state, in memory.

## Tech stack

**Backend:** FastAPI, Python 3.11, Groq SDK, Google Gen AI SDK (`google-genai`), httpx (Jina embeddings), numpy, Pydantic
**Frontend:** React, TypeScript, Vite, Tailwind CSS, shadcn/ui
**Deployment:** Docker, Render

## API

| Endpoint | Method | Purpose |
|---|---|---|
| `/query` | POST | Send a request, get a routed/cached completion |
| `/health` | GET | Check provider connectivity |
| `/stats/global` | GET | Aggregate usage/cost across all users |
| `/stats/user/{user_id}` | GET | Per-user usage/cost stats |
| `/stats/requests` | GET | Raw request history |

Full interactive schema: `/docs` (Swagger UI).

### Request shape

```json
{
  "conversation_id": "conv_123",
  "user_id": "user_abc",
  "messages": [
    {"role": "user", "content": "What's the weather in Mandi?"}
  ],
  "tools": null,
  "tool_choice": null,
  "stream": false
}
```

### Response shape

```json
{
  "content": "...",
  "tool_calls": null,
  "finish_reason": "stop",
  "model_used": "openai/gpt-oss-120b",
  "cost_usd": 0.000037,
  "latency_ms": 771.6,
  "cache_hit": false
}
```

## Setup

### Backend

```bash
cd backend
python -m venv venv
venv\Scripts\activate        # Windows
pip install -r requirements.txt
```

Create `backend/.env`:
```
GROQ_API_KEY=...
GEMINI_API_KEY=...
JINA_API_KEY=...
```

Run:
```bash
uvicorn app.main:app --reload
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Point `src/lib/api.ts` at the backend URL (local or deployed).

### Docker

```bash
cd backend
docker build -t llm-gateway .
docker run -p 8000:8000 --env-file .env llm-gateway
```

## Current limitations (known, not yet addressed)

- **In-memory state doesn't scale horizontally.** `provider_buckets` and `conversation_provider_map` live in a Python dict. Running multiple gateway instances behind a load balancer would give each instance its own separate rate-limit bucket, silently multiplying the real ceiling — and a restart resets all sticky routing.
- **No streaming support yet.** The `stream` field exists in the request schema but isn't wired to real token-by-token streaming in either provider client.
- **Fallback, not a full circuit breaker.** Every request re-attempts a failing provider fresh; there's no "stop trying this provider for N seconds after repeated failures" state.
- **Cache is context-blind.** Only the most recent user message is embedded for cache matching — multi-turn context ("earlier you said Germany... what's the capital?") isn't captured in the cache key.
- **No authentication.** `/query` trusts whatever `user_id` is sent; there's no verification that the caller is who they claim to be. This is by design for now — see Future Scope.

## Future scope

**Reliability**
- Full circuit breaker (closed/open/half-open states) per provider, replacing plain retry-on-failure.
- Move `provider_buckets` and `conversation_provider_map` to Redis so state survives restarts and is shared correctly across multiple gateway instances.
- Idempotency keys for retried requests, to avoid double-processing if a request actually succeeded server-side before a client-side timeout fired.

**Features**
- Real token-by-token streaming (SSE) through both provider clients and the `/query` endpoint.
- Additional providers (OpenAI, Anthropic) behind the same adapter interface.
- Cache TTL / explicit invalidation, instead of size-based eviction only.
- Typed `messages` (discriminated Pydantic models per role) instead of `list[dict]`, to catch malformed message shapes at the schema layer instead of at the provider call.

**Operations**
- Structured logging (request id, provider, latency, retry count) replacing ad hoc print statements, for real production debugging.
- Per-user rate limiting (distinct from per-provider), so one user can't exhaust the shared quota for everyone else.
- Authentication layer in the calling application (QueryMind), passing a verified `user_id` downstream — the gateway itself stays focused on cost attribution, not identity verification.

**Integration**
- Full QueryMind integration: RAG and CSV tool-calling flows routed through this gateway instead of calling Groq directly.
- Seam-level resilience testing between QueryMind and the gateway — timeout propagation, partial-failure handling, gateway-down fallback behavior.