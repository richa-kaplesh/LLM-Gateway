from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    JINA_API_KEY: str

    # Set from Jina's pricing page; 0.0 means embedding cost is NOT being counted
    JINA_COST_PER_MILLION: float = 0.02

    # Rough allowance for the tokens the model will GENERATE (TPM counts input + output)
    OUTPUT_TOKEN_ALLOWANCE: int = 512

    # When every key is merely out of budget / cooling down, wait up to this long for one to
    # free up instead of failing the request. 0 = fail immediately with 429 + Retry-After.
    MAX_QUEUE_WAIT_SECONDS: float = 20.0

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
