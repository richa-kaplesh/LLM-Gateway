
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

## Engineering log — real bugs found and fixed

Not a changelog of features added — a record of specific defects found by testing against real behavior, with before/after evidence logged in `/experiments`:

- **Circuit breaker half-open race:** the breaker let every request through during its recovery window instead of exactly one probe, defeating its own purpose. Found by tracing the state machine by hand, fixed, verified with a unit test.
- **Stuck-probe bug:** a probe request that failed for a reason unrelated to provider health (`InvalidRequestError`) never reported back to the breaker, leaving it permanently stuck. Fixed with an explicit "inconclusive" outcome that resets the cooldown instead of corrupting the state.
- **RPM/TPM capacity leak:** a request that failed its token-budget check had already spent its request-budget check moments earlier, silently leaking rate-limit capacity on every rejected request. Fixed by separating check-only and commit steps.
- **Cross-user cache leak:** an internal reviewer call was accidentally cache-eligible, risking one user's document content being served to another via a semantically similar query. Fixed with explicit, opt-in cache scoping.
- **Cross-provider tool-call corruption:** removing per-conversation provider stickiness (reasoned to be unnecessary, since full message history is resent every turn) broke multi-step tool-calling conversations — Gemini rejects replaying a function call with no `thought_signature`, which only Gemini itself produces. Stickiness was restored, with the reasoning for *why* documented above instead of just the fix.
- **Gemini thought-signature drop:** the unified OpenAI-shaped schema had no field for Gemini's `thought_signature`, silently dropping it on every multi-turn tool call and causing a 400 on the second turn. Fixed by capturing and replaying it through the Gemini adapter specifically.
- **Blanket 500s on every failure type**, making it impossible for a caller to distinguish "never retry" from "retry later" from "actually broken." Replaced with status codes that mean what they say.
- **Blocking event loop on every request:** both provider clients used their synchronous SDK client with no `await`, freezing the entire gateway — every other in-flight request — for the duration of each call. Found while building streaming support; fixed by switching to each SDK's real async client.

## Current limitations (known, not yet addressed)

- **No real streaming yet.** `stream` exists on the request schema but isn't wired up — the gateway only returns complete responses. Streaming is designed (including reassembling fragmented tool-call arguments, which Groq streams in pieces and Gemini doesn't) but not yet built.
- **No authentication.** `/query` trusts whatever `user_id` is sent — there's no API key, no verification the caller is who they claim to be, and no per-caller budget. Deliberately deferred, not forgotten.
- **In-memory routing/rate-limit state doesn't scale horizontally.** Running multiple gateway instances would give each its own separate buckets and breaker state, silently multiplying the real ceiling.
- **No RBAC, SSO, or audit logging.** Fine for a single-tenant or small-client deployment; a real gap for a multi-tenant enterprise product, named honestly rather than pretended away.

## Future scope

**Reliability**
- Move rate-limit buckets, breaker state, and sticky routing to Redis, so state survives restarts and is shared correctly across multiple instances.
- Idempotency keys for retried requests.

**Features**
- Real SSE streaming, including tool-call reassembly, with retry/fallback only available before the first token is sent to the client.
- Additional providers behind the same adapter interface.
- Cache TTL / explicit invalidation, instead of size-based eviction only.

**Operations**
- Per-key API authentication with budget caps.
- Structured, queryable tracing per request (beyond what `/stats` currently gives).

**Integration**
- Full QueryMind integration across both RAG and CSV tool-calling flows.