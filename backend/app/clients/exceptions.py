class ProviderUnavailableError(Exception):
    """Transient problem with THIS specific provider (rate limit, connection
    issue, server outage). Safe to retry against the other provider."""
    pass


class InvalidRequestError(Exception):
    """The request itself is malformed and will fail against any provider.
    Don't waste a second API call retrying elsewhere."""
    pass