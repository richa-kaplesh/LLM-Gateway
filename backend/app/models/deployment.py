from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Limits(_Strict):
    rpm: Optional[int] = Field(gt=0)
    tpm: Optional[int] = Field(gt=0)
    rpd: Optional[int] = Field(gt=0)
    tpd: Optional[int] = Field(gt=0)

    @model_validator(mode="after")
    def _at_least_one_limit(self):
        if all(v is None for v in (self.rpm, self.tpm, self.rpd, self.tpd)):
            raise ValueError("at least one of rpm, tpm, rpd, tpd must be set")
        return self


class Capabilities(_Strict):
    tools: bool
    json_mode: bool = Field(alias="json")


class Price(_Strict):
    input: float = Field(ge=0)
    output: float = Field(ge=0)
    cached_input: Optional[float] = Field(ge=0)


class DeploymentSpec(_Strict):
    id: str = Field(min_length=1)
    enabled: bool = True
    provider: str = Field(min_length=1)
    adapter: Literal["openai_compatible", "gemini"]
    base_url: Optional[str] = None
    key_env: str = Field(min_length=1)
    model: str = Field(min_length=1)
    limits: Limits
    capabilities: Capabilities
    tier: Literal["smart", "fast"]
    price: Price
    rate_limit_headers: Optional[dict[str, Literal["rpm", "tpm", "rpd", "tpd"]]] = None

    @model_validator(mode="after")
    def _openai_compatible_needs_base_url(self):
        if self.adapter == "openai_compatible" and not self.base_url:
            raise ValueError("adapter openai_compatible needs a base_url")
        return self

    @property
    def model_group(self) -> tuple[str, str]:
        return self.provider, self.model


class Deployment(DeploymentSpec):
    api_key: SecretStr
