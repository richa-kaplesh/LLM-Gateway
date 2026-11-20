class ProviderError(Exception):
    def __init__(self, message: str = "", retry_after: float | None = None, rate_limit=None):
        super().__init__(message)
        self.retry_after = retry_after
        self.rate_limit = rate_limit


class ProviderUnavailableError(ProviderError):
    pass


class ProviderConnectionError(ProviderUnavailableError):
    pass


class ProviderOverloadedError(ProviderUnavailableError):
    pass


class ProviderKeyError(ProviderError):
    pass


class ProviderRateLimitedError(ProviderError):
    pass


class QuotaExhaustedError(ProviderRateLimitedError):
    pass


class InvalidToolCallError(ProviderError):
    pass


class LocalCapacityError(Exception):
    def __init__(self, message: str = "", retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class CircuitOpenError(Exception):
    def __init__(self, message: str = "", retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class InvalidRequestError(Exception):
    pass


class TooLongError(Exception):
    pass


class GatewayRejection(Exception):
    status_code = 503
    code = "provider_unavailable"

    def __init__(self, message: str = "", retry_after: float | None = None,
                 attempts: list[dict] | None = None):
        super().__init__(message)
        self.retry_after = retry_after
        self.attempts = attempts or []


class AllProvidersRateLimitedError(GatewayRejection):
    status_code = 429
    code = "rate_limited"


class AllQuotaExhaustedError(GatewayRejection):
    status_code = 429
    code = "quota_exhausted"


class AllProvidersUnavailableError(GatewayRejection):
    status_code = 503
    code = "provider_unavailable"


class AllInvalidToolCallsError(GatewayRejection):
    status_code = 502
    code = "invalid_tool_call"


class GatewayOverloadedError(GatewayRejection):
    status_code = 429
    code = "overloaded"
