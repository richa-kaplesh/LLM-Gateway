# backend/test_bucket_tpm.py
import random
from app.router.bucket import rpm_buckets, tpm_buckets, select_provider

random.uniform = lambda a, b: 0   # forces roll=0, which is always < GROQ_WEIGHT → groq goes first, guaranteed

tpm_buckets["groq"].tokens = 10

rpm_before = rpm_buckets["groq"].tokens
result = select_provider(estimated_tokens=500)
rpm_after = rpm_buckets["groq"].tokens

print("provider picked:", result)
print("groq RPM before:", rpm_before, "| after:", rpm_after)
print("Groq was definitely tried first:", "yes, forced by monkey-patch")
print("RPM leaked?", rpm_before != rpm_after)