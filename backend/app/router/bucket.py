import time
import random


GROQ_WEIGHT = 30
GEMINI_WEIGHT = 15

class TokenBucket:
    def __init__(self, capacity:float, refill_rate:float):
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.tokens = capacity
        self.last_refill_time = time.time()

    def _refill(self):
        current_time = time.time()
        passed_time = current_time - self.last_refill_time
        coins_earned = passed_time*self.refill_rate
        self.tokens = min(self.capacity, self.tokens +coins_earned)
        self.last_refill_time = current_time

    def try_consume(self,amount:float=1)->bool:
        self._refill()
        if self.tokens>=amount:
            self.tokens-=amount
            return True
        return False

provider_buckets = {
    "groq": TokenBucket(capacity=30, refill_rate=30/60),
    "gemini": TokenBucket(capacity=15, refill_rate=15/60),
}

def select_provider():
    total = GROQ_WEIGHT + GEMINI_WEIGHT
    roll = random.uniform(0, total)

    if roll < GROQ_WEIGHT:
        first, second = "groq", "gemini"
    else:
        first, second = "gemini", "groq"

    if provider_buckets[first].try_consume():
        return first
    elif provider_buckets[second].try_consume():
        return second
    else:
        return None
   

conversation_provider_map: dict[str, str] = {}


def get_provider_for_conversation(conversation_id: str):
    if conversation_id in conversation_provider_map:
        return conversation_provider_map[conversation_id]

    provider = select_provider()
    if provider is not None:
        conversation_provider_map[conversation_id] = provider
    return provider


