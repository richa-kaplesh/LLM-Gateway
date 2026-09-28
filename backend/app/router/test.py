# test_breaker.py  (run from backend/: python test_breaker.py)
import time
from app.router.circuit_breaker import CircuitBreaker

b = CircuitBreaker(failure_threshold=2, cooldown_seconds=1)

assert b.allow_request() is True          # closed: allowed
b.record_failure(); b.record_failure()
assert b.state == "open"                  # 2 failures opens it
assert b.allow_request() is False         # open, cooldown not over: skipped
time.sleep(1.1)
assert b.allow_request() is True          # first request after cooldown = the probe
assert b.state == "half_open"
assert b.allow_request() is False         # second request: skipped while probe runs
b.record_failure()
assert b.state == "open"                  # failed probe reopens it
print("all passed")