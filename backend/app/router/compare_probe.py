# app/router/compare_probe.py  (run: python -m app.router.compare_probe)
import time
from app.router.circuit_breaker import CircuitBreaker

class OldBreaker(CircuitBreaker):
    # my original version: half_open fell through to "return True"
    def allow_request(self) -> bool:
        if self.state == "open":
            if time.time() - self.opened_at >= self.cooldown_seconds:
                self.state = "half_open"
                return True
            return False
        return True

def probes_let_through(breaker_cls):
    b = breaker_cls(failure_threshold=2, cooldown_seconds=1)
    b.record_failure(); b.record_failure()   # trip it open
    time.sleep(1.1)                          # cooldown passes
    return sum(b.allow_request() for _ in range(10))

print("old:", probes_let_through(OldBreaker))
print("new:", probes_let_through(CircuitBreaker))