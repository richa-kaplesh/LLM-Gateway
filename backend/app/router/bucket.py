import time

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