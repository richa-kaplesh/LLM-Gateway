import time

class CircuitBreaker:
    def __init__(self, failure_threshold: int = 3, cooldown_seconds: float = 30):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.consecutive_failures = 0
        self.state = "closed"          # closed | open | half_open
        self.opened_at: float | None = None

    def allow_request(self) -> bool:
        if self.state == "open":
            if time.time() - self.opened_at >= self.cooldown_seconds:
                self.state = "half_open"
                return True             # let exactly one test request through
            return False
        return True

    def record_success(self):
        self.consecutive_failures = 0
        self.state = "closed"

    def record_failure(self):
        self.consecutive_failures += 1
        if self.state == "half_open" or self.consecutive_failures >= self.failure_threshold:
            self.state = "open"
            self.opened_at = time.time()

    def snapshot(self) -> dict:
        return {"state": self.state, "consecutive_failures": self.consecutive_failures}

breakers = {"groq": CircuitBreaker(), "gemini": CircuitBreaker()}