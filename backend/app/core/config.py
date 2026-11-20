from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    JINA_API_KEY: str = ""
    JINA_COST_PER_MILLION: float = 0.02

    DATABASE_URL: str = ""
    REDIS_URL: str = ""
    DEPLOYMENTS_FILE: str = ""

    GATEWAY_API_KEYS: str = ""
    CORS_ORIGINS: str = "*"

    OUTPUT_TOKEN_ALLOWANCE: int = 512
    DEFAULT_TIER: str = "smart"
    DAILY_RESERVE_PCT: float = 10.0

    MAX_QUEUE_WAIT_SECONDS: float = 10.0
    MAX_IN_FLIGHT: int = 32
    MAX_WAITING: int = 64

    PROVIDER_TIMEOUT_SECONDS: float = 30.0

    CACHE_SIMILARITY_THRESHOLD: float = 0.90
    CACHE_MAX_SIZE: int = 100

    APP_NAME: str = "LLM Gateway"
    VERSION: str = "2.0.0"

    @property
    def api_keys(self) -> set[str]:
        return {k.strip() for k in self.GATEWAY_API_KEYS.replace(";", ",").split(",") if k.strip()}

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()] or ["*"]


@lru_cache()
def get_settings() -> Settings:
    return Settings()
