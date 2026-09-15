from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    GROQ_API_KEY: str
    GEMINI_API_KEY: str

    GROQ_MODEL: str = "openai/gpt-oss-20b"
    GEMINI_MODEL: str = "gemini-3.8-flash"

    GROQ_INPUT_COST_PER_MILLION: float = 0.15
    GROQ_OUTPUT_COST_PER_MILLION: float = 0.60

    GEMINI_INPUT_COST_PER_MILLION: float = 0.075
    GEMINI_OUTPUT_COST_PER_MILLION: float = 0.30

    CACHE_SIMILARITY_THRESHOLD: float = 0.70
    CACHE_MAX_SIZE: int = 100

    APP_NAME: str = "LLM Gateway"
    VERSION: str = "1.0.0"

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    return Settings()