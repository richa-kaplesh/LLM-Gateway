import argparse
import asyncio
import json
import os
import random
import statistics
import time
import uuid
from collections import Counter
from datetime import datetime
from pathlib import Path

import httpx

TOOLS = [{"type": "function", "function": {
    "name": "run_query", "description": "Run a query on the dataset",
    "parameters": {"type": "object", "properties": {"sql": {"type": "string"}}, "required": ["sql"]}}}]
SCHEMA_PADDING = "column_name type sample_value " * 300


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(pct / 100 * (len(ordered) - 1))))
    return round(ordered[index], 1)


class Results:
    def __init__(self):
        self.calls: list[dict] = []
        self.questions: list[dict] = []

    def add_call(self, **row):
        self.calls.append(row)

    def summary(self, elapsed: float, args) -> dict:
        ok = [c for c in self.calls if c["status"] == 200]
        failed = [c for c in self.calls if c["status"] != 200]
        lat_ok = [c["ms"] for c in ok]
        lat_fail = [c["ms"] for c in failed]
        answered = [q for q in self.questions if q["ok"]]
        return {
            "config": {"users": args.users, "duration_s": args.duration, "think_s": args.think,
                       "fast_share": args.fast_share, "target": args.url},
            "elapsed_s": round(elapsed, 1),
            "calls": len(self.calls),
            "calls_per_min": round(len(self.calls) / elapsed * 60, 1),
            "call_success_pct": round(100 * len(ok) / len(self.calls), 2) if self.calls else None,
            "status_codes": dict(Counter(str(c["status"]) for c in self.calls)),
            "error_codes": dict(Counter(c["code"] for c in failed)),
            "latency_ms_success": {"p50": percentile(lat_ok, 50), "p95": percentile(lat_ok, 95),
                                   "p99": percentile(lat_ok, 99),
                                   "mean": round(statistics.fmean(lat_ok), 1) if lat_ok else None},
            "latency_ms_failure": {"p50": percentile(lat_fail, 50), "p95": percentile(lat_fail, 95)},
            "attempts_per_call": dict(Counter(str(c.get("attempts")) for c in ok)),
            "fallbacks": sum(1 for c in ok if c.get("fallback")),
            "by_deployment": dict(Counter(c.get("deployment") for c in ok)),
            "questions": len(self.questions),
            "questions_answered_pct": round(100 * len(answered) / len(self.questions), 2) if self.questions else None,
        }


async def call(client: httpx.AsyncClient, args, results: Results, conversation: str, messages: list,
               tier: str, label: str) -> dict | None:
    body = {"conversation_id": conversation, "user_id": label, "messages": messages, "tools": TOOLS, "tier": tier}
    headers = {"X-Request-ID": f"load-{label}-{uuid.uuid4().hex[:6]}"}
    if args.api_key:
        headers["X-API-Key"] = args.api_key
    started = time.perf_counter()
    try:
        response = await client.post(f"{args.url}/query", json=body, headers=headers)
        status = response.status_code
        data = response.json()
    except Exception as e:
        status, data = 0, {"error": {"code": f"client_{type(e).__name__}"}}
    ms = (time.perf_counter() - started) * 1000
    if status == 200:
        results.add_call(status=status, ms=ms, code="ok", attempts=data.get("attempts"),
                         fallback=data.get("was_fallback"), deployment=data.get("deployment_id"))
        return data
    error = data.get("error") if isinstance(data, dict) else None
    code = error.get("code") if isinstance(error, dict) else f"http_{status}"
    results.add_call(status=status, ms=ms, code=code)
    return None


async def user(n: int, args, results: Results, stop_at: float):
    async with httpx.AsyncClient(timeout=args.timeout) as client:
        q = 0
        while time.monotonic() < stop_at:
            q += 1
            conversation = f"load-u{n}-q{q}-{uuid.uuid4().hex[:4]}"
            tier = "fast" if random.random() < args.fast_share else "smart"
            messages = [{"role": "system", "content": SCHEMA_PADDING[: args.prompt_chars]},
                        {"role": "user", "content": f"question {q} from user {n}"}]
            ok = True
            for step in range(args.calls_per_question):
                data = await call(client, args, results, conversation, messages, tier, f"u{n}")
                if data is None:
                    ok = False
                    break
                if data.get("tool_calls"):
                    messages = messages + [
                        {"role": "assistant", "content": None, "tool_calls": data["tool_calls"]},
                        {"role": "tool", "tool_call_id": data["tool_calls"][0]["id"], "content": "42"}]
            results.questions.append({"ok": ok})
            await asyncio.sleep(random.uniform(0.5, 1.5) * args.think)


async def main():
    parser = argparse.ArgumentParser(description="Concurrent load test for the LLM gateway")
    parser.add_argument("--url", default="http://127.0.0.1:8001")
    parser.add_argument("--users", type=int, default=10)
    parser.add_argument("--duration", type=float, default=60)
    parser.add_argument("--think", type=float, default=2.0)
    parser.add_argument("--calls-per-question", type=int, default=3)
    parser.add_argument("--fast-share", type=float, default=0.0)
    parser.add_argument("--prompt-chars", type=int, default=6000)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--api-key", default=os.environ.get("GATEWAY_API_KEY", ""))
    parser.add_argument("--label", default="run")
    parser.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "loadtest_results"))
    args = parser.parse_args()

    results = Results()
    started = time.monotonic()
    stop_at = started + args.duration
    await asyncio.gather(*(user(n, args, results, stop_at) for n in range(args.users)))
    summary = results.summary(time.monotonic() - started, args)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{datetime.now():%Y%m%d-%H%M%S}-{args.label}.json"
    path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"saved {path}")


if __name__ == "__main__":
    asyncio.run(main())
