class ProviderUnavailableError(Exception):
    """Transient problem with THIS specific provider (connection issue, server
    outage, bad credentials/model name). Safe to retry against the other provider."""
    pass


class ProviderDownError(Exception):
    """Non-rate-limit failure from a provider: 5xx, connection error, timeout,
    or auth failure.  The provider is considered DOWN for this request.
    Do NOT rotate keys or fall back — return this error immediately with a
    clear message that names the provider and includes the underlying cause.

    Distinct from ProviderUnavailableError (which the router uses internally
    as a fallback signal) so that the multi-key rotation layer can tell the
    difference between "try the next key" and "stop immediately"."""
    def __init__(self, provider: str, cause: Exception):
        self.provider = provider
        self.cause = cause
        super().__init__(f"Provider {provider!r} is down: {cause}")


class ProviderRateLimitedError(Exception):
    """THIS provider answered 'slow down' (HTTP 429). The provider is alive, so
    this must NOT count as an outage for the circuit breaker, and retrying the
    same provider immediately is pointless. retry_after is in seconds, if the
    provider told us."""
    def __init__(self, message: str = "", retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class CircuitOpenError(Exception):
    """We skipped a provider without calling it because its breaker is open
    (or a half-open probe is already in flight)."""
    def __init__(self, message: str = "", retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class InvalidRequestError(Exception):
    """The request itself is malformed and will fail against any provider.
    Don't waste a second API call retrying elsewhere."""
    pass


class TooLongError(Exception):
    """The prompt structurally cannot fit within any configured provider's
    token limit, even with no other traffic. Retrying won't help — the
    request needs to be shortened."""
    pass


class AllProvidersRateLimitedError(Exception):
    """Every provider is currently out of RPM/TPM capacity (or answered 429).
    Temporary — safe and correct for the caller to retry after retry_after seconds."""
    def __init__(self, message: str = "", retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class AllProvidersUnavailableError(Exception):
    """No provider could serve the request and it was not (only) a rate limit:
    circuits open, upstream errors, bad config. Maps to HTTP 503, not 500 —
    this is a dependency problem, not a bug in the gateway."""
    def __init__(self, message: str = "", retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


# ── Error classifier ──────────────────────────────────────────────────────────

def classify_error(e: Exception) -> str:
    """Return 'rate_limit' or 'provider_down' for a provider exception.

    Rule:
    - HTTP 429 / quota exceeded  → 'rate_limit'   (rotate to next key / provider)
    - Everything else            → 'provider_down' (stop immediately, no rotation)

    This is the single authoritative place that encodes the retry policy so
    callers and tests don't have to inspect raw exception types or HTTP codes.
    """
    if isinstance(e, ProviderRateLimitedError):
        return "rate_limit"
    if isinstance(e, CircuitOpenError):
        # The breaker paused a provider after repeated 429s — treat as rate-limit
        # so the caller rotates to another key/provider instead of stopping.
        return "rate_limit"
    # ProviderUnavailableError, ProviderDownError, connection errors, 5xx, auth
    # failures, timeouts, and anything else: the provider is considered down.
    return "provider_down"