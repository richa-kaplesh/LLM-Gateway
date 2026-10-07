class ProviderUnavailableError(Exception):
    """Server-side failure from THIS provider (5xx, auth/config error, bad
    model name). The provider itself is the problem — do NOT fall back and do
    NOT rotate keys. Return an error immediately."""
    pass


class ProviderConnectionError(ProviderUnavailableError):
    """Network-level failure: DNS resolution failed, TCP connection refused,
    or request timed out before the provider responded.  The provider itself
    may be perfectly healthy — this is a transient routing/network problem.
    Safe to fall back to the other provider for this request."""
    pass


class ProviderDownError(Exception):
    """Unrecoverable failure from a provider: 5xx server error or auth/config
    problem (bad key, wrong model name).  The provider is broken for this
    request. Do NOT rotate keys or fall back — return an error immediately
    that names the provider and includes the underlying cause.

    Connection errors use ProviderConnectionError instead, which allows the
    router to try the other provider (the network may be fine on that side)."""
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
    """Classify a provider exception into one of three routing decisions.

    Returns
    -------
    'rate_limit'
        HTTP 429 / quota exceeded.  Put the key on cooldown and rotate to
        the next available key / provider.
    'transient'
        Network-level failure (DNS, TCP, timeout).  The provider itself may
        be fine. Try the other provider for this request; do NOT put the key
        on cooldown.
    'provider_down'
        Server-side failure (5xx) or auth/config error (401/403/wrong model).
        The provider is broken. Stop immediately — no rotation, no fallback.

    This is the single authoritative place that encodes the retry policy so
    callers and tests don't have to inspect raw exception types or HTTP codes.
    """
    if isinstance(e, ProviderRateLimitedError):
        return "rate_limit"
    if isinstance(e, CircuitOpenError):
        # The breaker paused a provider after repeated 429s — treat as rate-limit
        # so the caller rotates to another key/provider instead of stopping.
        return "rate_limit"
    if isinstance(e, ProviderConnectionError):
        # Network-level failure: the provider may be fine, the network isn't.
        # Try the other provider; don't penalise this key.
        return "transient"
    # ProviderUnavailableError (5xx, bad key, wrong model) and anything else:
    # the provider itself is the problem — stop immediately.
    return "provider_down"