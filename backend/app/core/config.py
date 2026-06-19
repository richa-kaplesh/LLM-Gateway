from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    GROQ_API_KEY: str
    GEMINI_API_KEY: str

    GROQ_MODEL: str = "llama-3.1-8b-instant"
    GEMINI_MODEL: str = "gemini-1.5-flash"

    GROQ_COST_PER_MILLION_TOKENS: float = 0.05
    GEMINI_COST_PER_MILLION_TOKENS: float = 0.075

    SIMPLE_QUERY_WORD_LIMIT: int = 15

    CACHE_SIMILARITY_THRESHOLD: float = 0.85
    CACHE_MAX_SIZE: int = 100

    APP_NAME: str = "LLM Gateway"
    VERSION: str = "1.0.0"

    class Config:
        env_file = ".env"


@lru_cache()
def get_settings() -> Settings:
    return Settings()