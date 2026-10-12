from pydantic_settings import BaseSettings
from functools import lru_cache
import os
import re
import os



def _parse_key_list(env_value: str) -> list[str]:
    """Parse a list of API keys, tolerating the usual copy/paste mistakes.

    Keys may be separated by commas, semicolons, spaces or newlines (one key per line is
    common when pasting into a dashboard). Surrounding quotes/brackets are stripped and
    duplicates are dropped. Real API keys never contain these characters, so this cannot
    split a genuine key. (A key list that is NOT split correctly turns into one garbage
    'key' or into keys with stray quote characters, and every call using them fails.)
    """
    keys: list[str] = []
    for part in re.split(r"[,;\s]+", env_value or ""):
        key = part.strip().strip("\"'`[]()")
        if key and key not in keys:
            keys.append(key)
    return keys

class Settings(BaseSettings):
    # ── Single-key vars (kept for backward compatibility) ──────────────────
    # These are read when the multi-key list vars are absent.  If you set
    # GROQ_API_KEYS / GEMINI_API_KEYS they take precedence; GROQ_API_KEY /
    # GEMINI_API_KEY are then ignored.  Either way, at least one key per
    # provider must be present at startup.
    GROQ_API_KEY: str = ""
    GEMINI_API_KEY: str = ""

    # ── Multi-key vars (new) ───────────────────────────────────────────────
    # Comma-separated list, e.g.:
    #   GROQ_API_KEYS=gsk_key1,gsk_key2,gsk_key3
    #   GEMINI_API_KEYS=AIza_key1,AIza_key2
    # Leading/trailing spaces around commas are stripped.  Empty entries are
    # ignored so a trailing comma is harmless.
    GROQ_API_KEYS: str = ""
    GEMINI_API_KEYS: str = ""

    JINA_API_KEY: str

    GROQ_MODEL: str = "openai/gpt-oss-20b"
    GEMINI_MODEL: str = "gemini-3.8-flash"

    GROQ_INPUT_COST_PER_MILLION: float = 0.15
    GROQ_OUTPUT_COST_PER_MILLION: float = 0.60

    GEMINI_INPUT_COST_PER_MILLION: float = 0.075
    GEMINI_OUTPUT_COST_PER_MILLION: float = 0.30

    # Set from Jina's pricing page; 0.0 means embedding cost is NOT being counted
    JINA_COST_PER_MILLION: float = 0.02

    # ── Provider limits (free tier). Set these to YOUR model's real limits. ──
    # Groq openai/gpt-oss-20b free tier: 30 RPM, 8K TPM (and 200K tokens/day,
    # which no per-minute bucket can protect you from — watch the Groq console).
    GROQ_RPM: int = 30
    GROQ_TPM: int = 8000
    # Gemini: confirm the real numbers for GEMINI_MODEL in AI Studio; TPM is a placeholder.
    GEMINI_RPM: int = 15
    GEMINI_TPM: int = 32000

    # Rough allowance for the tokens the model will GENERATE (TPM counts input + output)
    OUTPUT_TOKEN_ALLOWANCE: int = 512

    # Hard timeout for one upstream call; without it a hung provider hangs the request
    PROVIDER_TIMEOUT_SECONDS: float = 60.0

    CACHE_SIMILARITY_THRESHOLD: float = 0.90
    CACHE_MAX_SIZE: int = 100

    APP_NAME: str = "LLM Gateway"
    VERSION: str = "1.0.0"

    class Config:
        env_file = ".env"
        extra = "ignore"

    # ── Resolved key lists (computed from env, not fields) ─────────────────
    def groq_keys(self) -> list[str]:
        """Return the list of Groq API keys to use.

        Priority:
        1. GROQ_API_KEYS  (comma-separated, new multi-key var)
        2. GROQ_API_KEY   (legacy single-key var)
        Raises ValueError at startup if no key is configured at all.
        """
        if self.GROQ_API_KEYS:
            keys = _parse_key_list(self.GROQ_API_KEYS)
            if keys:
                return keys
        keys = _parse_key_list(self.GROQ_API_KEY)
        if keys:
            return keys
        raise ValueError(
            "No Groq API key found. Set GROQ_API_KEYS (comma-separated) or GROQ_API_KEY."
        )

    def gemini_keys(self) -> list[str]:
        """Return the list of Gemini API keys to use.

        Priority:
        1. GEMINI_API_KEYS  (comma-separated, new multi-key var)
        2. GEMINI_API_KEY   (legacy single-key var)
        Raises ValueError at startup if no key is configured at all.
        """
        if self.GEMINI_API_KEYS:
            keys = _parse_key_list(self.GEMINI_API_KEYS)
            if keys:
                return keys
        keys = _parse_key_list(self.GEMINI_API_KEY)
        if keys:
            return keys
        raise ValueError(
            "No Gemini API key found. Set GEMINI_API_KEYS (comma-separated) or GEMINI_API_KEY."
        )


@lru_cache()
def get_settings() -> Settings:
    return Settings()