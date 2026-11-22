# LLM Gateway

A gateway that sits between client apps and several free-tier LLM providers. It turns a pile of small, separate API keys into one reliable pool: it tracks every key's per-minute and per-day quota, routes each call to the key with the most room, fails over in milliseconds when one refuses, and tells the caller exactly *why* when nothing can serve it.

**Live API:** https://llm-gateway-qewa.onrender.com/docs · **Dashboard:** https://llm-gateway-self.vercel.app/

## Results

Measured on the live deployment. **After:** a real client (QueryMind, an LLM data agent) running a 50-question evaluation (2026-11-20). **Before:** all gateway traffic on the original provider-routing design, 2026-10-01 → 2026-11-18 (1,444 requests). Both windows are saved as `/metrics/snapshots`.

| | Before | After |
|---|---|---|
| Gateway error rate | 21.0% | **6.7%** |
| Latency p50 / p95 | 0.61 s / 25.7 s | 0.52 s / **0.99 s** |
| Fallback rate | 20.0% | 7.8% |
| Questions with no answer | 3 of 45 | 0 of 50 |
| Spread across Groq keys | not measured | 61 / 58 / 58 attempts (1% imbalance) |

Capacity, measured locally against a mock provider with unlimited quota, so only the gateway can be the bottleneck:

| Setup | Load | Throughput | p95 |
|---|---|---|---|
| 1 instance, in-memory state | 50 users | ~2,500 calls/min | 0.79 s |
| 1 instance, Redis state | 50 users | ~1,940 calls/min | 1.80 s |
| 2 instances, shared Redis | 25 + 25 users | ~2,390 calls/min | ~0.80 s |
| 2 instances, shared Redis | 50 + 50 users | ~3,000 calls/min | ~2.3 s |

Two instances sharing one quota sent 6,000+ calls with **0 provider rate-limit errors**: no double-spending.

## Why it exists

Free LLM tiers are tiny and strict: Groq allows a few thousand tokens per minute and a daily token cap per key, OpenRouter 50 requests a day per account. A single app calling one provider directly runs out within minutes and then fails slowly. The original version of this gateway did exactly that: it treated providers as the unit of capacity, retried in place with sleeps, and spent up to 26 s before failing.

The fix was to change what the gateway thinks a "unit of capacity" is.

## Core idea: the deployment

The thing that really has a quota is **one API key × one model**, not a provider. Each one is a *deployment*, declared in [`backend/deployments.yaml`](backend/deployments.yaml):

```yaml
- id: groq-1
  provider: groq
  adapter: openai_compatible
  key_env: GROQ_API_KEY_1
  model: openai/gpt-oss-20b
  limits: {rpm: 30, tpm: 8000, rpd: 1000, tpd: 200000}
  tier: smart
```

The live pool is 8 deployments: 3 Groq keys × 2 models (a `smart` and a `fast` tier) plus 2 OpenRouter keys. Gemini is still in the file, disabled. Keys stay in env vars; the YAML only names them. The registry validates every entry at startup and **refuses to boot** on a bad one, because a silently skipped key is silently lost capacity.

## Architecture

```
Client ──► POST /query  (X-API-Key, tier = smart | fast)
              │
        Admission control ── 32 in flight, 64 waiting; beyond that → 429 overloaded at once
              │
        Selection ── filter: tier, tools, sticky model, breaker, cooldown, minute + daily headroom
              │      pick:   power of two choices on daily headroom − penalties
              │
        Attempt ──► Adapter (openai_compatible: Groq, OpenRouter · gemini)
              │        maps every provider error to one gateway error class
              │
        On failure ── classify → cool down the key OR trip the model's breaker → fail over
              │
        State store ── meters, cooldowns, breakers, health, sticky pins
              │        MemoryStore (one instance) or RedisStore (many instances)
              │
        Telemetry ── request_logs + attempt_logs (Postgres) → /metrics/summary, dashboard
```

## Design decisions

**Filter, then score.** Hard rules first: a key with no headroom, an open breaker, or a cooldown is removed, and no score can bring it back. The survivors are ranked by *relative* daily headroom (percent of the day's budget left), so keys of different sizes run out together instead of the biggest one draining first. Penalties for recent 429s, failures and slowness are subtracted.

**Power of two choices, not argmax.** Draw two survivors at random and take the better one. Argmax sends every request to whichever key's (lagging) score looks best, so they all pile onto it. P2C never picks the worst key and doesn't herd.

**Two failure scopes.** A 429 or quota error belongs to one account, so only **that key** cools down. A 5xx, timeout or connection error means the model itself is broken, so a circuit breaker opens for **provider × model**, and every key of that model is skipped. A per-key breaker would make a dead model fail once on every key first.

**A failure taxonomy instead of one reaction.** `rate_limited`, `quota_exhausted`, `bad_key`, `overloaded`, `down`, `invalid_tool_call`, `bad_request`. Each class sets the scope and the wait:
- `Retry-After` wins when the provider sends one.
- Breakers back off exponentially: 30 s, doubling, capped at 10 min, ±20% jitter, reset on success. A fixed 30 s cooldown had made the Gemini breaker flap 98 times.
- A 429 also drains that key's minute buckets, so the gateway's estimate stops disagreeing with the provider's.
- An invalid tool call from the model is a problem with *this output*, not an outage. It retries elsewhere without touching the breaker. Counting it as an outage used to open the breaker and block three healthy keys.

**Fail over, don't retry in place.** At most 2 extra attempts after hard failures (6 in total). 429s and local refusals don't use up that budget, so a congested free model can't eat all the retries while another key still has room.

**Daily quotas are first-class.** Per-minute token buckets can't see a daily cap, which is why evals used to degrade across a day (3 → 13 → 20 → 21 of 25 failed). Daily limits are a sliding window of 144 ten-minute slots. The last 10% of each key is a **reserve** for conversations already mid tool-loop, so work that has already spent calls can finish.

**Sticky by model, not by key.** A conversation with tool history must stay on the same model. Any key of that model may serve it. If none has room, the gateway queues briefly and then returns a structured error. It never switches models silently.

**Admission control and load shedding.** Each instance admits 32 requests at a time and queues 64 more for up to 10 s. Beyond that it returns `429 overloaded` immediately. If the soonest key frees up later than the queue limit, the request fails now instead of waiting to fail.

**Shared state in Redis, atomically.** Meters are one Redis hash per deployment, updated by **Lua scripts** that check and reserve every limit in one atomic step, using the Redis server's clock. Breakers, cooldowns and health use optimistic compare-and-set (`WATCH`/`MULTI`). The same meter logic exists in Python (for the in-memory store) and in Lua, and the test suite runs every case against both so they can't drift. Without `REDIS_URL` the gateway runs on the in-memory store.

**Adaptive limits from provider headers.** Groq's `x-ratelimit-remaining-*` / `reset-*` headers cap the gateway's own meters until their reset, so the provider's numbers are the ground truth.

**Adapters own provider quirks.** One adapter per *protocol*, not per provider: Groq and OpenRouter share `openai_compatible`. Adapters map every provider error, including errors hidden inside a 200 body, to gateway exceptions. The router never inspects a provider response. A new OpenAI-compatible provider is a YAML entry.

**Measure first.** Every provider call, failed ones included, is a row in `attempt_logs`. Without that, a request that fails on key A and succeeds on key B looks clean, and A's error never shows up. The headline routing metric is **rejected while capacity existed**: we refused a request while some deployment still had room by our own counters. That's pure routing waste. Its first real reading was 7 of 11 rejections.

## Known problems (found by load testing, not fixed yet)

| ID | Problem | Evidence |
|---|---|---|
| P22 | **~37 Redis commands per call**, mostly sequential round trips, including a "script loaded?" check on every call | Costs ~22% throughput and ~1 s of p95 per instance at 50 users |
| P20 | **P2C can starve a key for good.** With 3 keys the worst one loses every pair; its latency score only updates when it serves a call, so one slow sample freezes it | One key served 0 calls at 100 and 150 users; with Redis the starvation is cluster-wide |
| — | **Throughput falls past the knee** instead of levelling off: gateway CPU per call grows from ~11 ms to ~28 ms between 100 and 150 users, and rejected calls wait up to the 10 s queue limit | 150 users: ~1,500–2,300 calls/min, p95 6.9 s |
| P21 | **Provider clients are built lazily**, and each one blocks the event loop ~0.5 s (SSL bundle load), so the first calls after a restart freeze for up to ~4 s | Timed at ~520 ms per client |
| — | Admission limits are per instance, not global; breaker reads can be slightly stale across instances | design gap |
| — | OpenRouter's rate-limit header format is unverified, so only Groq headers feed the meters | |

The full list, with severities and status, is in [`project.md`](project.md) §3.

## API

| Endpoint | Method | Auth | Purpose |
|---|---|---|---|
| `/query` | POST | `X-API-Key` | Route a chat completion |
| `/status` | GET | — | Live view per deployment: meters, cooldown, breaker, verdict, `ready_in_s` |
| `/health` | GET | — | State store, database and auth status |
| `/metrics/summary` | GET | — | Error rate, percentiles, fallbacks, per-deployment share, imbalance, rejected-while-capacity-existed (`?since=&until=`) |
| `/metrics/snapshots` | GET / POST | POST: key | Save and compare measurement windows |
| `/stats/global`, `/stats/user/{id}`, `/stats/requests` | GET | — | Usage and cost (`limit` ≤ 1000) |
| `/experiments` | GET / POST | POST: key | Logged before/after experiments |

Reads stay open for the public dashboard. With `GATEWAY_API_KEYS` empty, writes are open too, and the gateway logs a warning at startup.

**Request**

```json
{
  "conversation_id": "conv_123",
  "user_id": "user_abc",
  "messages": [{"role": "user", "content": "How many rows have status = failed?"}],
  "tools": [{"type": "function", "function": {"name": "run_query", "parameters": {}}}],
  "tier": "smart"
}
```

**Response** (abridged): `content`, `tool_calls`, `provider_used`, `model_used`, `deployment_id`, `tier`, `attempts`, `was_fallback`, `latency_ms`, `cost_usd`, `input_tokens`, `output_tokens`.

**Errors** are structured, with a `Retry-After` header when waiting helps:

```json
{"detail": "...", "error": {"code": "rate_limited", "message": "...", "retry_after_s": 4.2,
 "request_id": "load-u03-q2", "attempts": [{"deployment_id": "groq-2", "provider": "groq", "model": "openai/gpt-oss-20b", "outcome": "rate_limited", "retry_after_s": 4.2}]}}
```

| Status | `code` | What the caller should do |
|---|---|---|
| 429 | `rate_limited` | Wait `retry_after_s` seconds |
| 429 | `quota_exhausted` | Daily budget gone: wait for the reset or use another tier |
| 429 | `overloaded` | Gateway shedding load: back off briefly |
| 502 | `invalid_tool_call` | The model kept producing broken tool calls |
| 503 | `provider_unavailable` | Every eligible model is down |
| 400 / 413 / 401 | — | Malformed request / too long for any deployment / missing or wrong key |

## Running it

```bash
cd backend
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

Set the keys named in `deployments.yaml` in `.env` (`GROQ_API_KEY_1..3`, `OPENROUTER_API_KEY_1..2`). Everything else is optional:

| Variable | Effect when empty |
|---|---|
| `DATABASE_URL` | No request history or metrics (useful for local load tests). The schema is applied at startup from `schema.sql` / `ensure_tables()` |
| `REDIS_URL` | In-memory state: lost on restart, not shared between instances |
| `GATEWAY_API_KEYS` | Write endpoints are open |
| `DEPLOYMENTS_FILE` | Uses `deployments.yaml` |

Tuning: `DEFAULT_TIER`, `DAILY_RESERVE_PCT`, `MAX_IN_FLIGHT`, `MAX_WAITING`, `MAX_QUEUE_WAIT_SECONDS`, `PROVIDER_TIMEOUT_SECONDS`.

**Tests:** `pip install -r requirements-dev.txt`, then `pytest`. 24 tests cover the meters (memory and fake Redis), router behaviour and admission.

**Frontend:** `cd frontend && npm install && npm run dev`, with `VITE_API_URL` pointing at the backend. The Keys page shows live cards from `/status` and per-deployment history from `/metrics/summary`.

**Docker:** `docker compose up` from the repo root, or build `backend/Dockerfile`.

### Load testing without burning quota

```bash
# a mock OpenAI-compatible provider with real per-key limits, Groq-style headers,
# and switchable 429 / 503 / invalid-tool-call rates (POST /_control)
uvicorn tools.mock_provider:app --port 9200

# point the gateway at it
DEPLOYMENTS_FILE=deployments.mock-capacity.yaml DATABASE_URL= uvicorn app.main:app --port 8011

# N users with think time and tool loops; prints and saves p50/p95/p99, error codes, per-deployment split
python tools/loadtest.py --url http://127.0.0.1:8011 --users 50 --duration 45
```

`deployments.mock.yaml` has realistic limits (to test behaviour at capacity); `deployments.mock-capacity.yaml` has effectively unlimited ones (to find the gateway's own limits). Results go to `backend/loadtest_results/`.

## Project layout

```
backend/app/
  core/      config, registry (loads + validates deployments.yaml), pricing, db, logging
  clients/   adapters (openai_compatible, gemini) and the gateway's exception classes
  router/    router, selection (filter + P2C), limiter, circuit_breaker, cooldowns,
             health, sticky, admission, attempt_log, status
  state/     Store interface, MemoryStore, RedisStore, meter logic (Python + Lua)
  tracker/   request + attempt logging, metrics and snapshots
  cache/     semantic cache (Jina embeddings), only for non-tool requests with a cache_scope
backend/tools/   mock_provider.py, loadtest.py
backend/tests/   meters, router, admission
frontend/        React + TypeScript + Vite + Tailwind dashboard
```

## Engineering log: bugs worth remembering

- **Retrying in place made failure slow.** Three 2 s sleeps on the same key plus a 20 s queue gave a p95 of 25.7 s. Failing over at once brought it to 0.99 s.
- **An overloaded provider was treated as dead.** Gemini's 503 "high demand" counted as a breaker failure, and a fixed 30 s cooldown made it flap 98 times. The real cause was a 20-requests-a-day free tier. Fixed with separate `overloaded` / `down` classes and exponential backoff.
- **`"None | None"` in 429 errors.** `str(None)` is truthy. It also revealed that no provider had refused those requests: the gateway's own estimates had.
- **Invalid tool calls opened the breaker.** Groq's 400 `tool_use_failed` was mapped to "unavailable", so three in a row blocked all three healthy keys. In the eval, 10 of 14 such requests succeeded on the next attempt.
- **Daily-quota failures were reported as "unavailable".** They were dropped before the give-up step. Caught by the router tests.
- **The load tester was the bottleneck.** Each simulated user built its own HTTP client, at ~0.5 s of blocking CPU each, which showed up as fake 30 s stalls at 50 users. The fix was one shared client.
- **Earlier, from v1:** a half-open breaker that let every request through, a probe that never reported back, rate-limit capacity leaking on rejected requests, a cache that could serve one user's document to another, Gemini's `thought_signature` dropped on multi-turn tool calls, and sync SDK calls blocking the event loop (5 concurrent calls: 5.05 s → 1.07 s).

## Docs

- [`project.md`](project.md): goals, baseline, every problem found, every decision with the alternatives rejected, measurements, dated log
- [`notes.md`](notes.md): the concepts behind each decision (capacity math, P2C, backoff, local vs shared state)

**Stack:** Python 3.11, FastAPI, asyncio, Redis (Lua), PostgreSQL (asyncpg), OpenAI SDK · React, TypeScript, Vite, Tailwind, Recharts · Docker, Render, Vercel, Neon
