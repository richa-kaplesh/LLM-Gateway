"""Call each provider directly with each configured key, bypassing the gateway.

Run from the backend/ folder:   python diagnose.py

It uses the same settings (keys, model names) the gateway uses, so whatever it
prints is what the gateway is hitting. Keys are shown masked (last 4 chars).
"""
import asyncio
import httpx
from app.core.config import get_settings

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_LIST_URL = "https://generativelanguage.googleapis.com/v1beta/models"

SHOW_HEADERS = ("retry-after", "x-ratelimit-limit-requests", "x-ratelimit-remaining-requests",
                "x-ratelimit-limit-tokens", "x-ratelimit-remaining-tokens")


def mask(key: str) -> str:
    return "…" + key[-4:]


def verdict(status: int) -> str:
    if status == 200:
        return "OK"
    if status in (401, 403):
        return "BAD KEY (or no access)"
    if status == 404:
        return "WRONG MODEL NAME (or endpoint)"
    if status == 429:
        return "RATE LIMITED / QUOTA USED UP"
    if status >= 500:
        return "PROVIDER SERVER ERROR"
    return "REJECTED"


def report(r: httpx.Response) -> None:
    headers = {h: r.headers[h] for h in SHOW_HEADERS if h in r.headers}
    body = " ".join(r.text.split())[:350]
    print(f"    -> HTTP {r.status_code}  [{verdict(r.status_code)}]")
    if headers:
        print(f"       headers: {headers}")
    if r.status_code != 200:
        print(f"       body: {body}")


async def check_groq(client: httpx.AsyncClient, s) -> None:
    print(f"\nGROQ  model={s.GROQ_MODEL}  keys={len(s.groq_keys())}")
    for key in s.groq_keys():
        print(f"  key {mask(key)}")
        try:
            r = await client.post(
                GROQ_URL,
                headers={"Authorization": f"Bearer {key}"},
                json={"model": s.GROQ_MODEL, "max_completion_tokens": 64,
                      "messages": [{"role": "user", "content": "Say hi in one word."}]},
            )
            report(r)
        except httpx.HTTPError as e:
            print(f"    -> NETWORK ERROR: {type(e).__name__}: {e}")


async def check_gemini(client: httpx.AsyncClient, s) -> None:
    print(f"\nGEMINI  model={s.GEMINI_MODEL}  keys={len(s.gemini_keys())}")
    saw_404 = False
    for key in s.gemini_keys():
        print(f"  key {mask(key)}")
        try:
            r = await client.post(
                GEMINI_URL.format(model=s.GEMINI_MODEL),
                headers={"x-goog-api-key": key},      # header, not ?key=, so keys never land in URLs/logs
                json={"contents": [{"parts": [{"text": "Say hi in one word."}]}]},
            )
            report(r)
            saw_404 = saw_404 or r.status_code == 404
        except httpx.HTTPError as e:
            print(f"    -> NETWORK ERROR: {type(e).__name__}: {e}")

    if saw_404:
        print("\n  The model name was not found. Models this key can use (flash ones):")
        try:
            r = await client.get(GEMINI_LIST_URL, headers={"x-goog-api-key": s.gemini_keys()[0]},
                                 params={"pageSize": 200})
            names = [m["name"].removeprefix("models/") for m in r.json().get("models", [])
                     if "generateContent" in m.get("supportedGenerationMethods", [])]
            for n in [n for n in names if "flash" in n][:20]:
                print(f"    {n}")
            print("  Set GEMINI_MODEL to one of these.")
        except Exception as e:
            print(f"    could not list models: {type(e).__name__}: {e}")


async def main() -> None:
    s = get_settings()
    async with httpx.AsyncClient(timeout=30) as client:
        await check_groq(client, s)
        await check_gemini(client, s)
    print("\nDone. Paste this whole output back if anything is not OK.")


if __name__ == "__main__":
    asyncio.run(main())