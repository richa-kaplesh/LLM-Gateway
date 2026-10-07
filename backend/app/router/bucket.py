import time
import random
import logging

from app.core.config import get_settings

log = logging.getLogger(__name__)
settings = get_settings()

GROQ_WEIGHT = 30
GEMINI_WEIGHT = 15
MAX_TRACKED_CONVERSATIONS = 5000


class TokenBucket:
    def __init__(self, capacity: float, refill_rate: float):
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.tokens = capacity
        self.last_refill_time = time.monotonic()

    def _refill(self):
        current_time = time.monotonic()
        passed_time = current_time - self.last_refill_time
        self.tokens = min(self.capacity, self.tokens + passed_time * self.refill_rate)
        self.last_refill_time = current_time

    def can_consume(self, amount: float = 1) -> bool:
        self._refill()
        return self.tokens >= amount

    def consume(self, amount: float = 1):
        self.tokens -= amount

    def refund(self, amount: float = 1):
        """Give back capacity that was reserved but never actually used."""
        self._refill()
        self.tokens = min(self.capacity, self.tokens + amount)

    def seconds_until(self, amount: float = 1) -> float | None:
        """How long until `amount` is available. None = can never fit."""
        self._refill()
        if amount > self.capacity:
            return None
        deficit = amount - self.tokens
        return max(0.0, deficit / self.refill_rate)


# RPM buckets — requests per minute, from each provider's free-tier limit (config.py)
rpm_buckets = {
    "groq": TokenBucket(capacity=settings.GROQ_RPM, refill_rate=settings.GROQ_RPM / 60),
    "gemini": TokenBucket(capacity=settings.GEMINI_RPM, refill_rate=settings.GEMINI_RPM / 60),
}

# TPM buckets — input + output tokens per minute (config.py)
tpm_buckets = {
    "groq": TokenBucket(capacity=settings.GROQ_TPM, refill_rate=settings.GROQ_TPM / 60),
    "gemini": TokenBucket(capacity=settings.GEMINI_TPM, refill_rate=settings.GEMINI_TPM / 60),
}


def reserve_capacity(p: str, estimated_tokens: int) -> tuple[bool, float | None]:
    """Atomically check + spend RPM and TPM for provider p.
    Returns (True, 0.0) on success, else (False, seconds_to_wait or None if it can never fit).
    can_consume/consume are split so a failed TPM check never spends RPM."""
    rpm, tpm = rpm_buckets[p], tpm_buckets[p]
    if estimated_tokens > tpm.capacity:
        return False, None
    if rpm.can_consume() and tpm.can_consume(estimated_tokens):
        rpm.consume()
        tpm.consume(estimated_tokens)
        return True, 0.0
    waits = [rpm.seconds_until(1), tpm.seconds_until(estimated_tokens)]
    return False, max(w for w in waits if w is not None)


def release_capacity(p: str, estimated_tokens: int) -> None:
    """Refund a reservation for a request that never consumed provider quota
    (circuit open, invalid request, 429, cancelled before completion)."""
    rpm_buckets[p].refund(1)
    tpm_buckets[p].refund(estimated_tokens)


def capacity_retry_after(estimated_tokens: int, providers=("groq", "gemini")) -> float | None:
    """Earliest time any provider could fit this request, for the Retry-After header."""
    waits = []
    for p in providers:
        w = [rpm_buckets[p].seconds_until(1), tpm_buckets[p].seconds_until(estimated_tokens)]
        if None not in w:
            waits.append(max(w))
    return min(waits) if waits else None


def select_provider(estimated_tokens: int, exclude: tuple[str, ...] = ()):
    """Picks a provider by weighted random order, then reserves RPM+TPM capacity.
    Does NOT check the circuit breaker — that happens exactly once, in
    router.py's _attempt_provider(), so a request isn't blocked by a probe it
    itself just triggered (see: double allow_request() bug, found before shipping)."""
    total = GROQ_WEIGHT + GEMINI_WEIGHT
    roll = random.uniform(0, total)
    first, second = ("groq", "gemini") if roll < GROQ_WEIGHT else ("gemini", "groq")

    for p in (first, second):
        if p in exclude:
            continue
        ok, _ = reserve_capacity(p, estimated_tokens)
        if ok:
            log.info("provider selected: %s (fresh weighted pick, preferred=%s%s)",
                     p, first, "" if p == first else ", preferred was out of capacity")
            return p
        if estimated_tokens > tpm_buckets[p].capacity:
            log.warning("skipping %s: prompt of %d tokens exceeds its TPM capacity of %d",
                        p, estimated_tokens, tpm_buckets[p].capacity)
        else:
            log.warning("token bucket empty: %s (rpm_tokens=%.1f, tpm_tokens=%.0f, needs %d tokens)",
                        p, rpm_buckets[p].tokens, tpm_buckets[p].tokens, estimated_tokens)
    log.warning("no provider has capacity (tried %s then %s)", first, second)
    return None


conversation_provider_map: dict[str, str] = {}


def pin_conversation(conversation_id: str, provider: str) -> None:
    conversation_provider_map.pop(conversation_id, None)      # refresh recency
    conversation_provider_map[conversation_id] = provider
    while len(conversation_provider_map) > MAX_TRACKED_CONVERSATIONS:
        conversation_provider_map.pop(next(iter(conversation_provider_map)))


def get_provider_for_conversation(conversation_id: str, estimated_tokens: int, must_stick: bool = False):
    """must_stick=True when the conversation already has tool-call history: that
    history can only be replayed safely to the provider that produced it
    (Gemini's thought_signature). Without tool history the request is effectively
    stateless, so a full sticky provider may be swapped for the other one."""
    sticky = conversation_provider_map.get(conversation_id)
    if sticky is not None:
        ok, _ = reserve_capacity(sticky, estimated_tokens)
        if ok:
            log.info("provider selected: %s (sticky routing, conversation=%s)", sticky, conversation_id)
            return sticky
        if must_stick:
            log.warning("rate limit: sticky provider %s has no capacity for conversation=%s "
                        "(rpm_tokens=%.1f, tpm_tokens=%.0f, needs %d tokens); conversation has "
                        "tool history so it cannot move; rejecting",
                        sticky, conversation_id, rpm_buckets[sticky].tokens,
                        tpm_buckets[sticky].tokens, estimated_tokens)
            return None
        log.info("sticky provider %s is full for conversation=%s and it has no tool history; re-picking",
                 sticky, conversation_id)
        provider = select_provider(estimated_tokens, exclude=(sticky,))
    else:
        provider = select_provider(estimated_tokens)

    if provider is not None:
        pin_conversation(conversation_id, provider)
        log.info("conversation=%s pinned to %s", conversation_id, provider)
    return provider