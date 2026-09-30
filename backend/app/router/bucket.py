import time
import random


GROQ_WEIGHT = 30
GEMINI_WEIGHT = 15

class TokenBucket:
    def __init__(self, capacity: float, refill_rate: float):
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.tokens = capacity
        self.last_refill_time = time.time()

    def _refill(self):
        current_time = time.time()
        passed_time = current_time - self.last_refill_time
        coins_earned = passed_time * self.refill_rate
        self.tokens = min(self.capacity, self.tokens + coins_earned)
        self.last_refill_time = current_time

    
    def can_consume(self, amount: float = 1) -> bool:
        self._refill()
        return self.tokens >= amount

    def consume(self, amount: float = 1):
        self.tokens -= amount


# RPM buckets — unchanged, still counting 1 per request
rpm_buckets = {
    "groq": TokenBucket(capacity=30, refill_rate=30/60),
    "gemini": TokenBucket(capacity=15, refill_rate=15/60),
}

# NEW: TPM buckets — actual token counts. Confirm Gemini's real TPM
# in AI Studio before trusting this number; 32000 is a placeholder.
tpm_buckets = {
    "groq": TokenBucket(capacity=12000, refill_rate=12000/60),
    "gemini": TokenBucket(capacity=32000, refill_rate=32000/60),
}

def select_provider(estimated_tokens: int):
    total = GROQ_WEIGHT + GEMINI_WEIGHT
    roll = random.uniform(0, total)
    first, second = ("groq", "gemini") if roll < GROQ_WEIGHT else ("gemini", "groq")

    for p in (first, second):
        if estimated_tokens > tpm_buckets[p].capacity:
            continue
        if rpm_buckets[p].can_consume() and tpm_buckets[p].can_consume(estimated_tokens):
            rpm_buckets[p].consume()
            tpm_buckets[p].consume(estimated_tokens)
            return p
    return None


conversation_provider_map: dict[str, str] = {}


def get_provider_for_conversation(conversation_id: str, estimated_tokens: int):
    if conversation_id in conversation_provider_map:
        return conversation_provider_map[conversation_id]

    provider = select_provider(estimated_tokens)
    if provider is not None:
        conversation_provider_map[conversation_id] = provider
    return provider