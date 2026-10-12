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


# ── Per-key buckets ─────────────────────────────────────────────────────────
# Providers meter each API key's account separately, so every key gets its own RPM
# and TPM bucket. A provider's capacity is the sum over its keys.
# GROQ_RPM / GROQ_TPM / GEMINI_RPM / GEMINI_TPM in config.py are PER-KEY limits.
_key_buckets: dict[tuple[str, str], tuple[TokenBucket, TokenBucket]] = {}


def per_key_limits(p: str) -> tuple[int, int]:
    """(rpm, tpm) limit of ONE key on provider p."""
    if p == "groq":
        return settings.GROQ_RPM, settings.GROQ_TPM
    return settings.GEMINI_RPM, settings.GEMINI_TPM


def buckets_for(p: str, api_key: str) -> tuple[TokenBucket, TokenBucket]:
    pair = _key_buckets.get((p, api_key))
    if pair is None:
        rpm, tpm = per_key_limits(p)
        pair = (TokenBucket(rpm, rpm / 60), TokenBucket(tpm, tpm / 60))
        _key_buckets[(p, api_key)] = pair
    return pair


def reset_buckets() -> None:
    _key_buckets.clear()


def max_request_tokens() -> int:
    """Largest request any single key could ever serve (TPM bucket capacity)."""
    return max(per_key_limits("groq")[1], per_key_limits("gemini")[1])


def key_fits(p: str, api_key: str, estimated_tokens: int) -> bool:
    """Peek only: would this key's RPM and TPM allow the request right now?"""
    rpm, tpm = buckets_for(p, api_key)
    return rpm.can_consume() and tpm.can_consume(estimated_tokens)


def key_wait(p: str, api_key: str, estimated_tokens: int) -> float | None:
    """Seconds until this key's buckets fit the request. None = can never fit."""
    rpm, tpm = buckets_for(p, api_key)
    waits = [rpm.seconds_until(1), tpm.seconds_until(estimated_tokens)]
    if None in waits:
        return None
    return max(waits)


def reserve_capacity(p: str, estimated_tokens: int, api_key: str) -> tuple[bool, float | None]:
    """Check + spend RPM and TPM on ONE key. Returns (True, 0.0) on success, else
    (False, seconds_to_wait or None if it can never fit). can_consume/consume are
    split so a failed TPM check never spends RPM."""
    rpm, tpm = buckets_for(p, api_key)
    if estimated_tokens > tpm.capacity:
        return False, None
    if rpm.can_consume() and tpm.can_consume(estimated_tokens):
        rpm.consume()
        tpm.consume(estimated_tokens)
        return True, 0.0
    return False, key_wait(p, api_key, estimated_tokens)


def release_capacity(p: str, estimated_tokens: int, api_key: str) -> None:
    """Refund a reservation for a request that never consumed provider quota
    (circuit open, invalid request, 429, cancelled before completion)."""
    rpm, tpm = buckets_for(p, api_key)
    rpm.refund(1)
    tpm.refund(estimated_tokens)


def select_provider(estimated_tokens: int, fits, exclude: tuple[str, ...] = (), weights: dict | None = None):
    """Weighted random provider pick among those where fits(p) is True (some key has
    capacity). Peek only: nothing is spent here; the RPM/TPM reservation happens per key
    in router._attempt_provider. Does NOT check the circuit breaker (that happens exactly
    once, in _attempt_provider, so a request isn't blocked by a probe it itself triggered)."""
    w = weights or {"groq": GROQ_WEIGHT, "gemini": GEMINI_WEIGHT}
    total = w["groq"] + w["gemini"]
    roll = random.uniform(0, total)
    first, second = ("groq", "gemini") if roll < w["groq"] else ("gemini", "groq")

    for p in (first, second):
        if p in exclude:
            continue
        if fits(p):
            log.info("provider selected: %s (fresh weighted pick, preferred=%s%s)",
                     p, first, "" if p == first else ", preferred was out of capacity")
            return p
        if estimated_tokens > per_key_limits(p)[1]:
            log.warning("skipping %s: prompt of %d tokens exceeds its per-key TPM capacity of %d",
                        p, estimated_tokens, per_key_limits(p)[1])
        else:
            log.warning("no key on %s has capacity for %d tokens right now", p, estimated_tokens)
    log.warning("no provider has capacity (tried %s then %s)", first, second)
    return None


conversation_provider_map: dict[str, str] = {}


def pin_conversation(conversation_id: str, provider: str) -> None:
    conversation_provider_map.pop(conversation_id, None)      # refresh recency
    conversation_provider_map[conversation_id] = provider
    while len(conversation_provider_map) > MAX_TRACKED_CONVERSATIONS:
        conversation_provider_map.pop(next(iter(conversation_provider_map)))


def get_provider_for_conversation(conversation_id: str, estimated_tokens: int, fits,
                                  must_stick: bool = False, weights: dict | None = None):
    """must_stick=True when the conversation already has tool-call history: that
    history can only be replayed safely to the provider that produced it
    (Gemini's thought_signature). Without tool history the request is effectively
    stateless, so a full sticky provider may be swapped for the other one."""
    sticky = conversation_provider_map.get(conversation_id)
    if sticky is not None:
        if fits(sticky):
            log.info("provider selected: %s (sticky routing, conversation=%s)", sticky, conversation_id)
            return sticky
        if must_stick:
            log.warning("rate limit: no key on sticky provider %s has capacity for conversation=%s "
                        "(needs %d tokens); conversation has tool history so it cannot move; rejecting",
                        sticky, conversation_id, estimated_tokens)
            return None
        log.info("sticky provider %s is full for conversation=%s and it has no tool history; re-picking",
                 sticky, conversation_id)
        provider = select_provider(estimated_tokens, fits, exclude=(sticky,), weights=weights)
    else:
        provider = select_provider(estimated_tokens, fits, weights=weights)

    if provider is not None:
        pin_conversation(conversation_id, provider)
        log.info("conversation=%s pinned to %s", conversation_id, provider)
    return provider
