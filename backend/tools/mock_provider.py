import asyncio
import os
import random
import time
import uuid

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

SECONDS_PER_MINUTE = 60


class Behaviour(BaseModel):
    latency_ms: float = float(os.environ.get("MOCK_LATENCY_MS", 400))
    jitter_ms: float = float(os.environ.get("MOCK_JITTER_MS", 150))
    p_429: float = float(os.environ.get("MOCK_P_429", 0))
    p_503: float = float(os.environ.get("MOCK_P_503", 0))
    p_tool_fail: float = float(os.environ.get("MOCK_P_TOOL_FAIL", 0))
    rpm: int = int(os.environ.get("MOCK_RPM", 60))
    tpm: int = int(os.environ.get("MOCK_TPM", 30000))
    rpd: int = int(os.environ.get("MOCK_RPD", 5000))
    down: bool = False


class KeyBudget:
    def __init__(self):
        self.requests: list[float] = []
        self.tokens: list[tuple[float, int]] = []
        self.day_requests = 0

    def prune(self, now: float) -> None:
        cutoff = now - SECONDS_PER_MINUTE
        self.requests = [t for t in self.requests if t > cutoff]
        self.tokens = [(t, n) for t, n in self.tokens if t > cutoff]

    def tokens_used(self) -> int:
        return sum(n for _, n in self.tokens)

    def reset_in(self, now: float) -> float:
        oldest = min([t for t in self.requests] + [t for t, _ in self.tokens], default=now)
        return max(0.1, oldest + SECONDS_PER_MINUTE - now)


app = FastAPI(title="Mock LLM provider")
behaviour = Behaviour()
budgets: dict[str, KeyBudget] = {}
counters = {"requests": 0, "ok": 0, "rate_limited": 0, "overloaded": 0, "tool_fail": 0}


def _estimate_tokens(body: dict) -> int:
    text = " ".join(str(m.get("content") or "") for m in body.get("messages", []))
    return max(1, len(text) // 4)


def _headers(b: KeyBudget, now: float) -> dict:
    return {
        "x-ratelimit-limit-requests": str(behaviour.rpd),
        "x-ratelimit-remaining-requests": str(max(0, behaviour.rpd - b.day_requests)),
        "x-ratelimit-reset-requests": "1h0m0s",
        "x-ratelimit-limit-tokens": str(behaviour.tpm),
        "x-ratelimit-remaining-tokens": str(max(0, behaviour.tpm - b.tokens_used())),
        "x-ratelimit-reset-tokens": f"{b.reset_in(now):.2f}s",
    }


def _error(status: int, message: str, code: str | None = None, headers: dict | None = None) -> JSONResponse:
    return JSONResponse(status_code=status, headers=headers,
                        content={"error": {"message": message, "type": "mock", "code": code}})


@app.post("/v1/chat/completions")
async def chat(request: Request, authorization: str = Header(default="")):
    body = await request.json()
    key = authorization.removeprefix("Bearer ").strip() or "anonymous"
    budget = budgets.setdefault(f"{key}|{body.get('model')}", KeyBudget())
    counters["requests"] += 1
    now = time.time()
    budget.prune(now)
    prompt_tokens = _estimate_tokens(body)

    if behaviour.down:
        counters["overloaded"] += 1
        return _error(500, "mock provider is down")
    over_minute = (len(budget.requests) + 1 > behaviour.rpm
                   or budget.tokens_used() + prompt_tokens > behaviour.tpm)
    if over_minute or random.random() < behaviour.p_429:
        counters["rate_limited"] += 1
        headers = _headers(budget, now)
        headers["retry-after"] = f"{budget.reset_in(now):.0f}"
        return _error(429, "Rate limit reached on tokens per minute (TPM)", "rate_limit_exceeded", headers)
    if budget.day_requests + 1 > behaviour.rpd:
        counters["rate_limited"] += 1
        return _error(429, "Rate limit reached on requests per day (RPD)", "rate_limit_exceeded",
                      {"retry-after": "3600"})
    if random.random() < behaviour.p_503:
        counters["overloaded"] += 1
        return _error(503, "mock provider is over capacity")

    budget.requests.append(now)
    budget.tokens.append((now, prompt_tokens))
    budget.day_requests += 1
    delay = max(0.0, random.gauss(behaviour.latency_ms, behaviour.jitter_ms)) / 1000
    await asyncio.sleep(delay)

    tools = body.get("tools") or []
    last_role = (body.get("messages") or [{}])[-1].get("role")
    wants_tool = tools and body.get("tool_choice") != "none" and last_role != "tool"
    if wants_tool and random.random() < behaviour.p_tool_fail:
        counters["tool_fail"] += 1
        return _error(400, "Failed to call a function. Please adjust your prompt.", "tool_use_failed")

    message = {"role": "assistant", "content": None if wants_tool else "mock answer"}
    if wants_tool:
        name = tools[0].get("function", {}).get("name", "tool")
        message["tool_calls"] = [{"id": f"call_{uuid.uuid4().hex[:8]}", "type": "function",
                                  "function": {"name": name, "arguments": "{}"}}]
    counters["ok"] += 1
    return JSONResponse(headers=_headers(budget, time.time()), content={
        "id": f"mock-{uuid.uuid4().hex[:10]}", "object": "chat.completion", "created": int(now),
        "model": body.get("model", "mock"),
        "choices": [{"index": 0, "message": message,
                     "finish_reason": "tool_calls" if wants_tool else "stop"}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": 8, "total_tokens": prompt_tokens + 8},
    })


@app.get("/_control")
async def get_control():
    return {"behaviour": behaviour.model_dump(), "counters": counters, "keys": len(budgets)}


@app.post("/_control")
async def set_control(update: dict):
    global behaviour
    behaviour = behaviour.model_copy(update=update)
    if update.get("reset"):
        budgets.clear()
        for k in counters:
            counters[k] = 0
    return behaviour.model_dump()
