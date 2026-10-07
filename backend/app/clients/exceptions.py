class ProviderUnavailableError(Exception):
    """Transient problem with THIS specific provider (connection issue, server
    outage, bad credentials/model name). Safe to retry against the other provider."""
    pass


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