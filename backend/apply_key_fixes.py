"""Apply the key-handling fixes to the gateway in one go.

Run ONCE from the backend/ folder:    python apply_key_fixes.py
Safe to run again: steps that are already applied are skipped.
If a step cannot find the text it expects, it says which file/step and changes nothing
for that step; send me that file.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "app"
problems = []


def load(rel):
    raw = (ROOT / rel).read_bytes().decode("utf-8")
    return raw.replace("\r\n", "\n"), ("\r\n" in raw)


def save(rel, text, crlf):
    if crlf:
        text = text.replace("\n", "\r\n")
    (ROOT / rel).write_bytes(text.encode("utf-8"))


def run(rel, steps):
    path = ROOT / rel
    if not path.exists():
        problems.append(f"{rel}: file not found"); print(f"  !! {rel}: file not found"); return
    s, crlf = load(rel)
    changed = False
    for name, marker, fn in steps:
        if marker in s:
            print(f"  ok (already applied)  {rel}: {name}"); continue
        new = fn(s)
        if new is None or new == s:
            problems.append(f"{rel}: {name}"); print(f"  !! COULD NOT APPLY    {rel}: {name}"); continue
        s, changed = new, True
        print(f"  applied               {rel}: {name}")
    if changed:
        save(rel, s, crlf)


def sub_once(pattern, repl, flags=0):
    def fn(s):
        new, n = re.subn(pattern, repl, s, count=1, flags=flags)
        return new if n else None
    return fn


def insert_before(anchor, text):
    def fn(s):
        i = s.find(anchor)
        return None if i < 0 else s[:i] + text + s[i:]
    return fn


# ───────────────────────── exceptions.py ─────────────────────────
KEY_ERROR_CLASS = '''class ProviderKeyError(ProviderUnavailableError):
    """THIS API KEY can't be used: rejected (401/403, 'API key not valid') or no access
    to the configured model. The provider itself is fine and its other keys may work,
    so the router disables just this key for a while and tries the next one. Only when
    every key of a provider is rejected does that provider count as unusable."""
    pass


'''
CLASSIFY_BRANCH = '''    if isinstance(e, ProviderKeyError):
        # This key was rejected. Skip the key (not the provider) and try the next one.
        return "bad_key"
'''
run("clients/exceptions.py", [
    ("ProviderKeyError class", "class ProviderKeyError",
     insert_before("class ProviderConnectionError(ProviderUnavailableError):", KEY_ERROR_CLASS)),
    ("classify_error branch", 'return "bad_key"',
     insert_before("    if isinstance(e, ProviderConnectionError):", CLASSIFY_BRANCH)),
])

# ───────────────────────── key_manager.py ─────────────────────────
RECORD_AUTH = '''    def record_auth_failed(self, ks: KeyState) -> None:
        ks.auth_failed = True
        ks.put_on_cooldown(self.AUTH_FAILED_COOLDOWN_SECONDS)
        log.error(
            "key %s on provider %s was REJECTED (invalid/unauthorized); benched for %.0f s. "
            "Fix or remove it in the env var.",
            ks.masked, self.provider, self.AUTH_FAILED_COOLDOWN_SECONDS,
        )

    def record_success(self, ks: KeyState) -> None:
        ks.auth_failed = False
'''
run("clients/key_manager.py", [
    ("KeyState.auth_failed flag", "self.auth_failed",
     sub_once(r"(        self\._cooldown_until: float = 0\.0[^\n]*\n)",
              r"\1        self.auth_failed: bool = False      # True while the key is benched for being rejected\n")),
    ("AUTH_FAILED_COOLDOWN_SECONDS", "AUTH_FAILED_COOLDOWN_SECONDS = ",
     sub_once(r"(    MAX_COOLDOWN_SECONDS = [^\n]*\n)",
              r"\1    # A rejected key (401/403/invalid) is benched this long, then retried once.\n"
              r"    AUTH_FAILED_COOLDOWN_SECONDS = 900.0\n")),
    ("record_auth_failed()", "def record_auth_failed",
     sub_once(r"    def record_success\(self, ks: KeyState\) -> None:\n", lambda m: RECORD_AUTH)),
    ("auth_failed in status_summary", '"auth_failed": ks.auth_failed',
     sub_once(r'(\n(\s+)"cooldown_remaining_s": round\(ks\.cooldown_remaining, 1\),\n)',
              lambda m: m.group(1) + m.group(2) + '"auth_failed": ks.auth_failed,\n')),
])

# ───────────────────────── groq_client.py ─────────────────────────
IMPORT_BLOCK = '''from app.clients.exceptions import (
    ProviderUnavailableError, ProviderConnectionError, InvalidRequestError,
    ProviderRateLimitedError, ProviderKeyError,
)
'''
GROQ_HANDLERS = '''    except (groq.AuthenticationError, groq.PermissionDeniedError) as e:
        # 401/403: this KEY is bad. The router benches it and tries the next one.
        raise ProviderKeyError(f"Groq rejected the API key (HTTP {getattr(e, 'status_code', '401/403')}): {str(e)[:200]}")
    except groq.InternalServerError as e:
        # 5xx (e.g. 503 "over capacity") is a temporary Groq-side problem: fall back to Gemini.
        raise ProviderConnectionError(f"Groq server error (HTTP {getattr(e, 'status_code', '5xx')}): {str(e)[:300]}")
'''
run("clients/groq_client.py", [
    ("import ProviderKeyError", "ProviderRateLimitedError, ProviderKeyError",
     sub_once(r"from app\.clients\.exceptions import [^\n(]*\n", lambda m: IMPORT_BLOCK)),
    ("401/403 + 5xx handlers", "groq.AuthenticationError",
     insert_before("    except Exception as e:\n        log.error(\"Groq unexpected error\"", GROQ_HANDLERS)),
])

# ───────────────────────── gemini_client.py ─────────────────────────
IS_BAD_KEY = '''def _is_bad_key(e: Exception) -> bool:
    """Gemini reports an invalid or expired key as HTTP 400 (reason API_KEY_INVALID),
    not 401, so check the message too."""
    msg = str(e)
    return "API_KEY_INVALID" in msg or "API key not valid" in msg or "API key expired" in msg


'''
GEMINI_KEY_BLOCK = '''        if code in (401, 403, 404) or _is_bad_key(e):
            # Bad/expired key, no access to this model for this key's project, or a wrong
            # GEMINI_MODEL. Treated per KEY: the router benches this key and tries the next.
            # If every key fails this way the final error says so (and names the cause).
            log.error("Gemini key unusable (HTTP %s) with GEMINI_MODEL=%s: %s",
                      code, settings.GEMINI_MODEL, str(e)[:300])
            raise ProviderKeyError(f"Gemini key unusable (HTTP {code}): {str(e)[:300]}")
'''
GEMINI_5XX = '''    except genai_errors.ServerError as e:
        # 5xx (e.g. 503 "model is experiencing high demand") is a temporary Google-side
        # problem. Treat it like a connection error so the router falls back to Groq.
        raise ProviderConnectionError(f"Gemini server error (HTTP {getattr(e, 'code', '5xx')}): {str(e)[:300]}")
'''
run("clients/gemini_client.py", [
    ("import ProviderKeyError", "ProviderRateLimitedError, ProviderKeyError",
     sub_once(r"from app\.clients\.exceptions import [^\n(]*\n", lambda m: IMPORT_BLOCK)),
    ("_is_bad_key() helper", "def _is_bad_key",
     insert_before("def _retry_after_seconds(e: Exception)", IS_BAD_KEY)),
    ("bad/expired key -> ProviderKeyError", "raise ProviderKeyError(f\"Gemini key unusable",
     sub_once(r"        if code in \(401, 403, 404\):\n.*?(?=        if code == 408:)",
              lambda m: GEMINI_KEY_BLOCK, flags=re.DOTALL)),
    ("503 -> fall back to Groq", "Gemini server error (HTTP",
     sub_once(r"    except genai_errors\.ServerError as e:\n        raise ProviderUnavailableError\([^\n]*\n",
              lambda m: GEMINI_5XX)),
])

# ───────────────────────── router.py ─────────────────────────
KEY_CLAUSE = '''    except ProviderKeyError as e:
        # this KEY was rejected; the provider itself is fine (don't count a breaker failure)
        breaker.record_reachable()
        release_capacity(p, estimated_tokens)
        log.error("provider %s rejected an API key: %s", p, e)
        raise
'''
NEW_ROTATION_BODY = '''    fallback = _other_provider(primary)
    km_primary = _key_managers[primary]
    km_fallback = _key_managers[fallback]

    # Track the last rate-limit error from each provider for building the final error
    last_rl_primary: ProviderRateLimitedError | None = None
    last_rl_fallback: ProviderRateLimitedError | None = None
    # Providers whose circuit breaker is open: every key on them is skipped.
    # An open breaker is NOT "provider down": it means "skip this one, use the other".
    circuit_open: dict[str, CircuitOpenError] = {}
    # Rejected keys per provider. One bad key must not take a provider down: it is
    # benched and the next key is tried; only if ALL keys are rejected is the provider out.
    key_errors: dict[str, list[ProviderKeyError]] = {}

    tried_primary_key: str | None = None
    tried_fallback_key: str | None = None

    # First attempt: primary provider (capacity already reserved in route())
    ks = km_primary.next_available()
    if ks is None:
        release_capacity(primary, estimated_tokens)   # reserved in route() but never used
        log.warning("all %s keys are on cooldown at start", primary)
    first_attempt = True
    while ks is not None:
        tried_primary_key = ks.key
        log.info("routing request: provider=%s key=%s", primary, ks.masked)
        try:
            # only the very first attempt uses the reservation made in route();
            # later keys re-reserve (the earlier reservation was released on failure)
            resp = await _attempt_provider(primary, request, estimated_tokens,
                                           reserve=not first_attempt, api_key=ks.key)
            km_primary.record_success(ks)
            return resp
        except InvalidRequestError:
            raise
        except ProviderKeyError as e:
            km_primary.record_auth_failed(ks)
            key_errors.setdefault(primary, []).append(e)
            log.warning("rotation: %s key %s rejected; trying this provider's next key", primary, ks.masked)
            first_attempt = False
            ks = km_primary.next_available(skip_key=ks.key)
            continue
        except ProviderRateLimitedError as e:
            km_primary.record_rate_limited(ks, e.retry_after)
            last_rl_primary = e
            log.warning("rotation: %s key %s rate-limited; trying %s", primary, ks.masked, fallback)
        except CircuitOpenError as e:
            circuit_open[primary] = e
            log.warning("rotation: %s circuit is open; skipping it, trying %s", primary, fallback)
        except ProviderConnectionError as e:
            # Transient failure (DNS/TCP/timeout/5xx). Don't penalise the key
            # (it may recover); go try the other provider.
            log.warning("rotation: %s transient error; falling back to %s: %s", primary, fallback, e)
        except Exception as e:
            # Server-side/config failure we can't classify - stop immediately
            raise ProviderDownError(primary, e) from e
        break

    # Alternating loop: fallback -> primary -> fallback -> ...
    # Each iteration picks one key from whichever side is "next".
    turn = fallback      # who we are about to try
    for _ in range(2 * (km_primary.key_count() + km_fallback.key_count()) + 2):
        if len(circuit_open) == 2:
            break
        if turn in circuit_open:
            turn = _other_provider(turn)
            continue

        km = km_fallback if turn == fallback else km_primary
        skip = tried_fallback_key if turn == fallback else tried_primary_key

        ks = km.next_available(skip_key=skip)
        if ks is None:
            log.info("no available keys left on %s, switching side", turn)
            turn = _other_provider(turn)
            if _all_sides_dry(km_primary, km_fallback, circuit_open):
                break
            continue

        if turn == fallback:
            tried_fallback_key = ks.key
        else:
            tried_primary_key = ks.key

        log.info("rotation: trying provider=%s key=%s", turn, ks.masked)
        try:
            # reserve=True on both sides: the first attempt's reservation was already
            # released (429 / open circuit / connection error), so re-reserve each time.
            resp = await _attempt_provider(turn, request, estimated_tokens,
                                           reserve=True, api_key=ks.key)
            km.record_success(ks)
            return resp
        except InvalidRequestError:
            raise
        except ProviderKeyError as e:
            km.record_auth_failed(ks)
            key_errors.setdefault(turn, []).append(e)
            log.warning("rotation: %s key %s rejected; trying this provider's next key", turn, ks.masked)
            continue                                   # same provider, next key (don't switch sides)
        except ProviderRateLimitedError as e:
            km.record_rate_limited(ks, e.retry_after)
            if turn == fallback:
                last_rl_fallback = e
            else:
                last_rl_primary = e
            log.warning("rotation: %s key %s rate-limited; switching to other side", turn, ks.masked)
        except CircuitOpenError as e:
            circuit_open[turn] = e
            log.warning("rotation: %s circuit is open; skipping the provider", turn)
        except ProviderConnectionError as e:
            # Transient error in the alternating loop - log and keep rotating
            # (other keys/providers may work; don't stop here).
            log.warning("rotation: %s transient error; continuing rotation: %s", turn, e)
        except Exception as e:
            # Server-side/config failure we can't classify - stop immediately
            raise ProviderDownError(turn, e) from e

        turn = _other_provider(turn)

    # Nothing could serve the request.
    notes = [f"{p}: {len(errs)} key(s) rejected, last error: {errs[-1]}" for p, errs in key_errors.items()]
    if (circuit_open or key_errors) and last_rl_primary is None and last_rl_fallback is None:
        parts = [str(e) for e in circuit_open.values()] + notes
        hints = [e.retry_after for e in circuit_open.values() if e.retry_after is not None]
        log.error("no provider available: %s", "; ".join(parts))
        raise AllProvidersUnavailableError(
            "No provider available: " + "; ".join(parts),
            retry_after=min(hints) if hints else None,
        )

    candidates = [km.soonest_retry_seconds()
                  for name, km in ((primary, km_primary), (fallback, km_fallback))
                  if name not in circuit_open]
    candidates += [e.retry_after for e in circuit_open.values()]
    soonest = min((t for t in candidates if t is not None), default=None)
    errors = " | ".join(filter(None, [str(last_rl_primary), str(last_rl_fallback)] + notes))
    log.error(
        "all keys exhausted on both providers (groq=%d keys, gemini=%d keys); "
        "soonest retry in %.0f s",
        _key_managers["groq"].key_count(), _key_managers["gemini"].key_count(), soonest or 0,
    )
    raise AllProvidersRateLimitedError(
        f"All keys on every provider are rate-limited. {errors}",
        retry_after=soonest,
    )


'''


def replace_rotation_body(s):
    a = s.find("    fallback = _other_provider(primary)\n    km_primary")
    b = s.find("def _all_sides_dry(")
    if a < 0 or b < 0 or b < a:
        return None
    return s[:a] + NEW_ROTATION_BODY + s[b:]


run("router/router.py", [
    ("import ProviderKeyError", "ProviderConnectionError, ProviderKeyError, classify_error",
     sub_once(r"ProviderDownError, ProviderConnectionError, classify_error,",
              "ProviderDownError, ProviderConnectionError, ProviderKeyError, classify_error,")),
    ("don't retry a rejected key", "ProviderConnectionError, ProviderKeyError):",
     sub_once(r"except \(InvalidRequestError, ProviderRateLimitedError, ProviderConnectionError\):",
              "except (InvalidRequestError, ProviderRateLimitedError, ProviderConnectionError, ProviderKeyError):")),
    ("rejected key must not count as a breaker failure", "rejected an API key",
     insert_before("    except asyncio.CancelledError:\n", KEY_CLAUSE)),
    ("rotation: skip bad key, try next key", "key_errors: dict[str, list[ProviderKeyError]]",
     replace_rotation_body),
])

# ───────────────────────── test_rotation_real.py (append) ─────────────────────────
NEW_TESTS = '''


# ── a rejected key is skipped, not fatal ───────────────────────────────────

@pytest.mark.asyncio
async def test_bad_key_is_skipped_and_next_key_on_same_provider_is_used(world):
    calls = world({"gA": ex.ProviderKeyError("401 invalid api key"), "*": OK}, {"*": OK})
    resp = await _go("groq")
    assert resp.provider_used == "groq"
    assert ("groq", "gB") in calls
    km = R._key_managers["groq"]
    assert [k["auth_failed"] for k in km.status_summary()] == [True, False, False]
    assert breakers["groq"].state == "closed" and breakers["groq"].consecutive_failures == 0


@pytest.mark.asyncio
async def test_bad_key_is_not_retried_while_benched(world):
    calls = world({"gA": ex.ProviderKeyError("401"), "*": OK}, {"*": OK})
    await _go("groq")
    await _go("groq")
    assert sum(1 for c in calls if c == ("groq", "gA")) == 1


@pytest.mark.asyncio
async def test_all_keys_of_a_provider_rejected_falls_back_to_the_other_provider(world):
    world({"*": ex.ProviderKeyError("401")}, {"*": OK})
    resp = await _go("groq")
    assert resp.provider_used == "gemini"


@pytest.mark.asyncio
async def test_all_keys_everywhere_rejected_gives_clear_503(world):
    world({"*": ex.ProviderKeyError("401 invalid api key")}, {"*": ex.ProviderKeyError("400 API key not valid")})
    with pytest.raises(ex.AllProvidersUnavailableError, match="key\\\\(s\\\\) rejected"):
        await _go("groq")


@pytest.mark.asyncio
async def test_bad_key_on_fallback_side_is_skipped_too(world):
    calls = world({"*": ex.ProviderRateLimitedError("429", 30)}, {"mA": ex.ProviderKeyError("bad"), "*": OK})
    resp = await _go("groq")
    assert resp.provider_used == "gemini"
    assert ("gemini", "mB") in calls
'''
tp = ROOT / "router/test_rotation_real.py"
if tp.exists():
    s, crlf = load("router/test_rotation_real.py")
    if "test_bad_key_is_skipped_and_next_key" in s:
        print("  ok (already applied)  router/test_rotation_real.py: bad-key tests")
    else:
        save("router/test_rotation_real.py", s.rstrip("\n") + NEW_TESTS, crlf)
        print("  applied               router/test_rotation_real.py: 5 bad-key tests appended")
else:
    problems.append("router/test_rotation_real.py not found"); print("  !! router/test_rotation_real.py not found")

print()
if problems:
    print("SOME STEPS COULD NOT BE APPLIED:"); [print("  -", p) for p in problems]
    print("Send me those files and I will fix the patch."); sys.exit(1)
print("All fixes applied. Now run:  pytest app/router     (expect: 58 passed)")