# Gateway Mastery Plan

Goal: **own this gateway completely**, first well enough to defend it in any interview, then well enough to write it again myself. 30 LPA in hand.

Companions: [project.md](project.md) logs what we built and why · [notes.md](notes.md) holds lessons · [study.md](study.md) holds outside topics. This file is the **path through all of it**, plus my progress.

---

## For any Claude session reading this file

You are my teacher. Find the first module below whose status isn't `done` and continue from there.

**One module per session, in this loop:**
1. **Map:** in 5 lines, where this module sits in the request flow and which files it covers.
2. **Walk:** go through each file in order: what it does, why it exists, which problem (P#) or decision (§4 in project.md) forced it, with real numbers. Skip basics I know.
3. **Teach-back:** I explain the module back in my own words. Correct me.
4. **Check questions:** ask the module's questions one at a time. Don't reveal answers until I've tried.
5. **Interview line:** give me the one-liner I'd say out loud.
6. **Update this file:** status, date, my weak spots.

Don't edit code in Stage A–C sessions. Never commit. Tell me when the chat is getting long.

---

## The shape of the work

| Stage | What | Done when |
|---|---|---|
| **A. Understand** | Every file, in the order a request travels through it | I can draw the request flow from memory and answer each module's questions |
| **B. Story** | Problems → fixes → numbers, as interview stories | I can tell 6 stories in ~2 min each with real numbers |
| **C. Defend** | Mock interviews: system design, deep dives, "why not X" | I survive 3 mock rounds without notes |
| **D. Rebuild** | Write the core gateway myself, from scratch, level by level | My version passes the same scenarios as the real one |

Stages A–C are interview readiness. Stage D is coding readiness. Do them in order: rebuilding is much faster once the *why* is solid.

---

## Stage A — Understand what exists

Backend is ~3,900 lines across ~40 files. Order = the path of one `POST /query`.

```
client ─► main.py (/query, auth, validation)
            │
          admission.py (in-flight / queue limits, load shedding)
            │
          router.py ─► selection.py (filter → P2C) ◄── health.py, sticky.py
            │              │
            │          limiter.py ─► meters (meter_logic / lua_scripts) ─► Store (memory | Redis)
            │          circuit_breaker.py, cooldowns.py
            │
          adapters.py ─► openai_compatible.py / gemini_client.py ─► provider
            │                 └─ maps every provider error to exceptions.py
            │
          attempt_log.py ─► tracker.py ─► Postgres (request_logs, attempt_logs) ─► metrics.py, status.py
```

| # | Module | Files | Status |
|---|---|---|---|
| A0 | Big picture | README, project.md §1–2, §6 | not started |
| A1 | Front door & config | `main.py`, `models/schemas.py`, `core/config.py`, `core/registry.py`, `models/deployment.py`, `deployments.yaml` | not started |
| A2 | Adapters & error taxonomy | `clients/adapters.py`, `openai_compatible.py`, `exceptions.py`, `gemini_client.py` | not started |
| A3 | Limits & meters | `router/limiter.py`, `state/meter_logic.py` | not started |
| A4 | Failure handling | `router/circuit_breaker.py`, `router/cooldowns.py` | not started |
| A5 | Choosing a key | `router/selection.py`, `router/health.py`, `router/sticky.py` | not started |
| A6 | The router loop & admission | `router/router.py`, `router/admission.py` | not started |
| A7 | Shared state | `state/store.py`, `memory_store.py`, `redis_store.py`, `lua_scripts.py` | not started |
| A8 | Observability | `router/attempt_log.py`, `tracker/tracker.py`, `tracker/metrics.py`, `router/status.py`, `schema.sql` | not started |
| A9 | Side pieces | `cache/cache.py`, `core/pricing.py`, `core/db.py`, `core/logging_setup.py`, `experiments/*` | not started |
| A10 | Testing & load | `tests/*`, `tools/mock_provider.py`, `tools/loadtest.py`, `deployments.mock*.yaml` | not started |
| A11 | Frontend tour (light) | `frontend/src/lib/api.ts`, `pages/KeysPage.tsx`, `DashboardPage.tsx` | not started |

### A0 — Big picture
- What problem does the gateway solve for QueryMind, in one sentence?
- What is a **deployment**, and why is it key × model instead of provider?
- Baseline vs now: error rate, p95, and the main reason each moved.
- Draw the request flow above from memory.

### A1 — Front door & config
- What does `/query` do before the router ever sees the request? (auth, validation, request id, attempt log, tracking)
- Why does the registry **fail fast** on a bad YAML entry instead of skipping it?
- Why are keys in env but limits in YAML? How does `key_env` fall back to `GROQ_API_KEYS`?
- Which endpoints need `X-API-Key`, which don't, and why?

### A2 — Adapters & error taxonomy
- Why one adapter per **protocol**, not per provider?
- Name the 7 error classes and, for each: whose fault, which scope (key or model), what reaction.
- How does a 429 become `quota_exhausted` instead of `rate_limited`?
- Why is `invalid_tool_call` its own class? (P16: what went wrong before, with numbers)
- What happens with `x-ratelimit-*` headers? (Phase 6)

### A3 — Limits & meters
- The four limits per key (RPM, TPM, RPD, TPD): which is binding for Groq and for OpenRouter, and why?
- Why token buckets for minutes but a **144-slot sliding window** for days? What would a token bucket do wrong for a day?
- What is the 10% daily reserve and who may use it?
- Reserve-then-settle: why reserve estimated tokens before the call?

### A4 — Failure handling
- Breaker states and transitions. Why keyed by provider × model?
- Exponential backoff numbers (30 s, ×2, cap 10 min, ±20% jitter): why jitter?
- Cooldown vs breaker: which errors go where, and why a 429 must never open the breaker.
- Why "fail over, don't retry in place"? (P3: 26.5 s → 0.99 s)
- Why only one half-open probe across all instances?

### A5 — Choosing a key
- Filter vs score: what's a hard rule, what's a soft signal?
- Why relative headroom, not absolute?
- P2C: how it works and why not argmax / round-robin.
- Stickiness at model level: why not key or provider level?
- P20: how P2C starves a key, and two ways to fix it.

### A6 — The router loop & admission
- Walk through `route()` → `_serve()` → `_attempt()` → `_give_up()` for: success; 429 then success; all keys exhausted.
- Failover budget: what counts as a hard failure, and why 429s don't (P18).
- `capacity_existed` (D8): what it means and why it's the headline routing metric.
- Admission: 32 in flight / 64 waiting / 10 s queue. Little's Law link. Why shedding past the knee isn't fast yet.

### A7 — Shared state
- Why does all state go through one `Store` interface? (L2: N instances = N× overspend)
- Lua vs `WATCH/MULTI` CAS: which is used where, and why both?
- Why the Redis server clock (`TIME`) and not each instance's clock?
- Meter logic exists in Python and Lua: how do the tests stop them drifting?
- P22: why ~37 commands per call, and what you'd do about it.
- If Redis dies: fail-open, fail-closed, or degraded? What does ours do?

### A8 — Observability
- Why an `attempt_logs` table on top of `request_logs`? (D7)
- How does a `ContextVar` carry the attempt log through the request?
- Pool share, imbalance, idle, provider 429s: what each tells you.
- `/status` must never consume a half-open probe. Why does that matter?

### A9 — Side pieces
- Cache: what's the key, what's the scope, why was hit rate 0.2%, and why a wrong hit is worse than a miss.
- Why the DB is optional (`DATABASE_URL` empty).

### A10 — Testing & load
- What the mock provider simulates, and why test the gateway against mocks (G6).
- The tester bug (one HTTP client per user, ~0.5 s SSL load each): how it faked 30 s gaps.
- Capacity numbers: ~2,500 calls/min at p95 0.79 s on 1 instance; knee 50–100 users; 2 instances on Redis with 0 provider 429s.
- Why does throughput **fall** past the knee instead of levelling off?

### A11 — Frontend tour
- What the Keys page shows and where each number comes from (`/status` vs `/metrics/summary`).
- Why the API key lives in `localStorage` and not in the build.

---

## Stage B — The story bank

Each story: **situation → what I measured → what I found → what I changed → result (number)**. Draft them in my own words after the matching A module.

| # | Story | Numbers | After module | Status |
|---|---|---|---|---|
| B1 | Gateway failing 1 in 5 requests: diagnosis to fix | 21.5% → 6.7% errors; p95 26.5 s → 0.99 s | A0, A6 | not started |
| B2 | Gemini "down" was really a 20-req/day quota + overload flapping | 98 opens / 6 closes | A2, A4 | not started |
| B3 | Invalid tool calls tripping the breaker for healthy keys | 18 attempts, 71% recover on retry | A2, A4 | not started |
| B4 | Making routing waste measurable (`capacity_existed`) | 7 of 11 rejections while capacity existed | A6, A8 | not started |
| B5 | Scaling out without double-spending quota | 2 instances, 6,015 calls, 0 provider 429s; Redis costs ~22% | A7, A10 | not started |
| B6 | The load test was lying (tester bug) | fake 30 s gaps from 0.5 s client creation × 50 | A10 | not started |
| B7 | Known weaknesses and what I'd do next | P20, P21, P22, per-instance admission | all | not started |

Also prepare: **2-minute project pitch** · **30-second version** · **"what was hardest"** · **"what would you do differently"**.

---

## Stage C — Defend it

| Round | Format | Status |
|---|---|---|
| C1 | "Design an LLM gateway" from a blank whiteboard (45 min). Claude plays interviewer, pushes on scale and failure. | not started |
| C2 | Deep dive: interviewer picks any file and asks "why this way?" | not started |
| C3 | Tradeoff grill: Redis vs alternatives, P2C vs least-loaded, fail-open vs closed, sliding window vs bucket, consistency across instances | not started |
| C4 | Compare to the market: LiteLLM, Portkey, OpenRouter. What does mine do that they do / don't? | not started |

---

## Stage D — Rebuild it myself

Write a smaller gateway in a separate folder (`rebuild/`, never touching `backend/`). Each level adds one idea. **I write the code; Claude explains the goal, gives hints when I'm stuck, and reviews.** After each level, compare with the real file.

| Level | Build | Concept | Done when | Compare with | Status |
|---|---|---|---|---|---|
| D1 | FastAPI `/query` that forwards to one OpenAI-compatible provider | async HTTP, request/response models | a real call works | `main.py`, `openai_compatible.py` | not started |
| D2 | Load deployments from YAML, fail fast on bad entries | config-driven design | bad YAML stops startup | `registry.py` | not started |
| D3 | Map provider errors to typed exceptions | error taxonomy | mock's 429 / 503 / 400 each raise the right class | `exceptions.py` | not started |
| D4 | Per-key token bucket for RPM/TPM | rate limiting | over-limit calls refused locally | `limiter.py`, `meter_logic.py` | not started |
| D5 | Daily sliding window + reserve | windows, budgets | 51st call refused on a 50/day key | `meter_logic.py` | not started |
| D6 | Cooldowns + circuit breaker with backoff + jitter | resilience | breaker opens, half-opens, closes in a test | `cooldowns.py`, `circuit_breaker.py` | not started |
| D7 | Failover loop with a hard-failure budget | retries vs failover | 429 on key A → served by key B | `router.py` | not started |
| D8 | Filter → P2C selection, model-level stickiness | load balancing | load spreads evenly across 3 mock keys | `selection.py`, `sticky.py` | not started |
| D9 | Admission control | backpressure, Little's Law | overload → instant 429, not a slow one | `admission.py` | not started |
| D10 | `Store` interface + Redis with one Lua check-and-reserve | distributed state, atomicity | 2 instances, 0 provider 429s on mock | `state/*` | not started |
| D11 | Attempt log + `capacity_existed` | observability | every attempt in a table | `attempt_log.py`, `tracker.py` | not started |
| D12 | Load test my version vs the real one | capacity planning | numbers side by side | `tools/loadtest.py` | not started |

---

## Progress log

| Date | Module | Weak spots / notes |
|---|---|---|
| 2026-11-22 | Plan created | — |
