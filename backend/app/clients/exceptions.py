class ProviderUnavailableError(Exception):
    """Transient problem with THIS specific provider (rate limit, connection
    issue, server outage). Safe to retry against the other provider."""
    pass


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
    """Every provider is currently out of RPM/TPM capacity. Temporary —
    safe and correct for the caller to retry after a short wait."""
    pass