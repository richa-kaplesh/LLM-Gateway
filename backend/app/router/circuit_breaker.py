import time


class CircuitBreaker:
    """Three-state breaker (closed / open / half_open) plus a separate
    rate-limit pause.

    - Real failures (outage, 5xx, connection errors) count toward the threshold
      and can open the circuit.
    - Rate limits (HTTP 429) do NOT count as failures: the provider is alive.
      They only pause the provider for retry_after seconds (record_rate_limited).
    - Half-open lets exactly one probe through. If that probe never reports back
      (request cancelled, DB hiccup, hung upstream), probe_timeout_seconds later
      another probe is allowed — the breaker can never wedge in half_open.
    """

    def __init__(self, failure_threshold: int = 3, cooldown_seconds: float = 30,
                 probe_timeout_seconds: float = 30,
                 default_rate_limit_pause: float = 15, max_rate_limit_pause: float = 600):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.probe_timeout_seconds = probe_timeout_seconds
        self.default_rate_limit_pause = default_rate_limit_pause
        self.max_rate_limit_pause = max_rate_limit_pause

        self.consecutive_failures = 0
        self.state = "closed"          # closed | open | half_open
        self.opened_at: float | None = None
        self.probe_started_at: float | None = None
        self.paused_until = 0.0        # rate-limit pause, independent of state
        self.rate_limit_streak = 0

    @staticmethod
    def _now() -> float:
        return time.monotonic()     # immune to wall-clock jumps

    def pause_remaining(self) -> float:
        return max(0.0, self.paused_until - self._now())

    def retry_after(self) -> float:
        """Seconds until this provider might accept a request again (0 = now)."""
        now = self._now()
        wait = self.pause_remaining()
        if self.state == "open" and self.opened_at is not None:
            wait = max(wait, self.cooldown_seconds - (now - self.opened_at))
        elif self.state == "half_open" and self.probe_started_at is not None:
            wait = max(wait, self.probe_timeout_seconds - (now - self.probe_started_at))
        return max(0.0, wait)

    def allow_request(self) -> bool:
        now = self._now()
        if now < self.paused_until:
            return False
        if self.state == "open":
            if now - self.opened_at >= self.cooldown_seconds:
                self.state = "half_open"
                self.probe_started_at = now
                return True
            return False
        if self.state == "half_open":
            if self.probe_started_at is None or now - self.probe_started_at >= self.probe_timeout_seconds:
                self.probe_started_at = now      # the previous probe was lost; try again
                return True
            return False
        return True

    def record_success(self):
        self.consecutive_failures = 0
        self.rate_limit_streak = 0
        self.state = "closed"
        self.probe_started_at = None

    def record_failure(self):
        self.consecutive_failures += 1
        if self.state == "half_open" or self.consecutive_failures >= self.failure_threshold:
            self.state = "open"
            self.opened_at = self._now()
            self.probe_started_at = None

    def record_rate_limited(self, retry_after: float | None = None):
        """Provider answered 429. Pause it; do not count a failure."""
        self.rate_limit_streak += 1
        if retry_after is None:
            retry_after = self.default_rate_limit_pause * (2 ** (self.rate_limit_streak - 1))
        pause = min(max(retry_after, 1.0), self.max_rate_limit_pause)
        self.paused_until = max(self.paused_until, self._now() + pause)
        if self.state == "half_open":
            # it answered, so it is reachable: close, and let the pause handle the rest
            self.state = "closed"
            self.consecutive_failures = 0
            self.probe_started_at = None

    def record_inconclusive(self):
        """Probe ended for reasons unrelated to provider health (invalid request,
        cancelled). Go back to open and restart the cooldown."""
        if self.state == "half_open":
            self.state = "open"
            self.opened_at = self._now()
            self.probe_started_at = None

    def snapshot(self) -> dict:
        return {
            "state": self.state,
            "consecutive_failures": self.consecutive_failures,
            "paused_for_seconds": round(self.pause_remaining(), 1),
            "retry_in_seconds": round(self.retry_after(), 1),
        }


breakers = {"groq": CircuitBreaker(), "gemini": CircuitBreaker()}