import asyncio
from contextlib import asynccontextmanager

from app.clients.exceptions import GatewayOverloadedError
from app.core.config import get_settings

SHED_RETRY_AFTER_SECONDS = 1.0


class Admission:
    def __init__(self, max_in_flight: int, max_waiting: int, max_wait_seconds: float):
        self.max_in_flight = max_in_flight
        self.max_waiting = max_waiting
        self.max_wait_seconds = max_wait_seconds
        self._slots = asyncio.Semaphore(max_in_flight)
        self.in_flight = 0
        self.waiting = 0
        self.shed = 0

    def _reject(self, why: str) -> GatewayOverloadedError:
        self.shed += 1
        return GatewayOverloadedError(f"Gateway is at capacity ({why}); retry shortly",
                                      retry_after=SHED_RETRY_AFTER_SECONDS)

    @asynccontextmanager
    async def slot(self):
        if self._slots.locked() and self.waiting >= self.max_waiting:
            raise self._reject(f"{self.in_flight} in flight, {self.waiting} waiting")
        self.waiting += 1
        try:
            await asyncio.wait_for(self._slots.acquire(), self.max_wait_seconds)
        except asyncio.TimeoutError:
            raise self._reject(f"no slot within {self.max_wait_seconds:.0f} s")
        finally:
            self.waiting -= 1
        self.in_flight += 1
        try:
            yield
        finally:
            self.in_flight -= 1
            self._slots.release()

    def snapshot(self) -> dict:
        return {"in_flight": self.in_flight, "waiting": self.waiting, "shed_total": self.shed,
                "max_in_flight": self.max_in_flight, "max_waiting": self.max_waiting}


_admission: Admission | None = None


def get_admission() -> Admission:
    global _admission
    if _admission is None:
        s = get_settings()
        _admission = Admission(s.MAX_IN_FLIGHT, s.MAX_WAITING, s.MAX_QUEUE_WAIT_SECONDS)
    return _admission
