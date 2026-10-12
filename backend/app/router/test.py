# Manual check: a request too big for a key's TPM must not spend that key's RPM.
from app.router.bucket import buckets_for, key_fits, reserve_capacity

rpm, tpm = buckets_for("groq", "demo-key")
tpm.tokens = 10
before = rpm.tokens
ok, wait = reserve_capacity("groq", 500, "demo-key")
print("reserved:", ok, "| wait:", wait)
print("RPM leaked?", before != rpm.tokens)
