from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    GROQ_API_KEY: str
    GEMINI_API_KEY: str
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


@lru_cache()
def get_settings() -> Settings:
    return Settings()