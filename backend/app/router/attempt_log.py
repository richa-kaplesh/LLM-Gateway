import asyncio
from contextvars import ContextVar
from dataclasses import dataclass, field

from app.clients.exceptions import (
    CircuitOpenError, InvalidRequestError, LocalCapacityError, ProviderKeyError,
    ProviderRateLimitedError,
)
from app.models.deployment import Deployment


@dataclass(frozen=True)
class Attempt:
    attempt_no: int
    deployment_id: str
    provider: str
    model: str
    outcome: str
    error_type: str | None
    latency_ms: float | None
    retry_after_s: float | None


@dataclass
class AttemptLog:
    attempts: list[Attempt] = field(default_factory=list)
    capacity_existed: bool | None = None

    def record(self, deployment: Deployment, latency_ms: float | None,
               error: BaseException | None = None) -> Attempt:
        attempt = Attempt(
            attempt_no=len(self.attempts) + 1,
            deployment_id=deployment.id,
            provider=deployment.provider,
            model=deployment.model,
            outcome=outcome_for(error),
            error_type=type(error).__name__ if error is not None else None,
            latency_ms=round(latency_ms, 1) if latency_ms is not None else None,
            retry_after_s=getattr(error, "retry_after", None),
        )
        self.attempts.append(attempt)
        return attempt

    @property
    def count(self) -> int:
        return len(self.attempts)

    @property
    def final_deployment_id(self) -> str | None:
        last = self.attempts[-1] if self.attempts else None
        return last.deployment_id if last and last.outcome == "success" else None


def outcome_for(error: BaseException | None) -> str:
    if error is None:
        return "success"
    if isinstance(error, LocalCapacityError):
        return "local_capacity"
    if isinstance(error, ProviderRateLimitedError):
        return "rate_limited"
    if isinstance(error, CircuitOpenError):
        return "breaker_open"
    if isinstance(error, ProviderKeyError):
        return "bad_key"
    if isinstance(error, InvalidRequestError):
        return "bad_request"
    if isinstance(error, asyncio.CancelledError):
        return "cancelled"
    return "failed"


_current: ContextVar[AttemptLog | None] = ContextVar("attempt_log", default=None)


def start() -> AttemptLog:
    attempt_log = AttemptLog()
    _current.set(attempt_log)
    return attempt_log


def current() -> AttemptLog:
    attempt_log = _current.get()
    if attempt_log is None:
        attempt_log = start()
    return attempt_log
