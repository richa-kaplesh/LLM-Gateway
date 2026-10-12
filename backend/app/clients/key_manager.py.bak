"""Key manager — per-key cooldown state and rotation logic.

Each API key gets its own ``KeyState`` that tracks whether it is on cooldown
(i.e. recently returned a 429).  The ``KeyManager`` for a provider holds all
of that provider's keys and picks the next available one.

Design choices
──────────────
- Cooldowns are stored per-key, not per-provider, so a 429 on key A doesn't
  block key B on the same provider.
- Keys are tried in round-robin order starting from the key after the last
  used one, so load is spread evenly across all keys.
- ``_mask_key`` shows only the last 4 characters so logs are useful without
  ever leaking a full key.
"""

import time
import logging

log = logging.getLogger(__name__)


def _mask_key(key: str) -> str:
    """Return '…XXXX' where XXXX is the last 4 chars.  Never logs the full key."""
    if len(key) <= 4:
        return "…" + key
    return "…" + key[-4:]


class KeyState:
    """Cooldown state for a single API key."""

    def __init__(self, key: str):
        self.key = key
        self.masked = _mask_key(key)
        self._cooldown_until: float = 0.0   # monotonic timestamp
        self.auth_failed: bool = False      # True while the key is benched for being rejected

    @property
    def available(self) -> bool:
        return time.monotonic() >= self._cooldown_until

    @property
    def cooldown_remaining(self) -> float:
        """Seconds left on the cooldown, or 0.0 if available."""
        return max(0.0, self._cooldown_until - time.monotonic())

    def put_on_cooldown(self, seconds: float) -> None:
        self._cooldown_until = time.monotonic() + seconds
        log.warning("key %s put on cooldown for %.0f s", self.masked, seconds)

    def soonest_retry(self) -> float:
        """Absolute monotonic timestamp when this key comes off cooldown."""
        return self._cooldown_until


class KeyManager:
    """Round-robin key pool with per-key cooldown tracking.

    Usage:
        mgr = KeyManager("groq", ["key1", "key2", "key3"])

        # pick the next available key (returns KeyState or None if all are on cooldown)
        ks = mgr.next_available(skip_key=None)
        if ks is None:
            # all keys exhausted
            ...
        try:
            result = await call_provider(ks.key)
            mgr.record_success(ks)
        except RateLimitError as e:
            mgr.record_rate_limited(ks, retry_after=e.retry_after)
    """

    DEFAULT_COOLDOWN_SECONDS = 60.0   # used when Retry-After is absent
    MAX_COOLDOWN_SECONDS = 600.0
    # A rejected key (401/403/invalid) is benched this long, then retried once.
    AUTH_FAILED_COOLDOWN_SECONDS = 900.0
    def __init__(self, provider: str, keys: list[str]):
        if not keys:
            raise ValueError(f"KeyManager for {provider!r} received an empty key list")
        self.provider = provider
        self._states: list[KeyState] = [KeyState(k) for k in keys]
        self._last_index: int = -1   # index of the last key we returned

    # ── Public interface ────────────────────────────────────────────────────

    def next_available(self, skip_key: str | None = None) -> KeyState | None:
        """Return the next KeyState that is not on cooldown and is not ``skip_key``.

        Iterates in round-robin order starting from the key after the last used
        one.  Returns None if every key is either on cooldown or is skip_key.
        """
        n = len(self._states)
        start = (self._last_index + 1) % n
        for offset in range(n):
            idx = (start + offset) % n
            ks = self._states[idx]
            if skip_key and ks.key == skip_key:
                continue
            if ks.available:
                self._last_index = idx
                return ks
        return None

    def record_auth_failed(self, ks: KeyState) -> None:
        ks.auth_failed = True
        ks.put_on_cooldown(self.AUTH_FAILED_COOLDOWN_SECONDS)
        log.error(
            "key %s on provider %s was REJECTED (invalid/unauthorized); benched for %.0f s. "
            "Fix or remove it in the env var.",
            ks.masked, self.provider, self.AUTH_FAILED_COOLDOWN_SECONDS,
        )

    def record_success(self, ks: KeyState) -> None:
        ks.auth_failed = False
        log.info("key %s on provider %s: request succeeded", ks.masked, self.provider)

    def record_rate_limited(self, ks: KeyState, retry_after: float | None) -> None:
        cooldown = retry_after if retry_after is not None else self.DEFAULT_COOLDOWN_SECONDS
        cooldown = min(max(cooldown, 1.0), self.MAX_COOLDOWN_SECONDS)

        ks.put_on_cooldown(cooldown)
        log.warning(
            "key %s on provider %s: rate-limited, cooldown=%.0f s (retry_after=%s)",
            ks.masked, self.provider, cooldown, retry_after,
        )

    def all_on_cooldown(self) -> bool:
        return all(not ks.available for ks in self._states)

    def soonest_retry_seconds(self) -> float | None:
        """Seconds until the least-loaded key comes off cooldown.
        Returns 0.0 if any key is already available.  Returns None if the
        list is empty (shouldn't happen).
        """
        if not self._states:
            return None
        now = time.monotonic()
        earliest = min(ks.soonest_retry() for ks in self._states)
        return max(0.0, earliest - now)

    def key_count(self) -> int:
        return len(self._states)

    def status_summary(self) -> list[dict]:
        """Diagnostic snapshot — safe to log (keys are masked)."""
        return [
            {
                "key": ks.masked,
                "available": ks.available,
                "cooldown_remaining_s": round(ks.cooldown_remaining, 1),
                "auth_failed": ks.auth_failed,
            }
            for ks in self._states
        ]
