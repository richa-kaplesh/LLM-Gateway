"""Real numbers for the Baseline tab and the Dashboard.   Run from the backend folder:
    python apply_baseline_metrics.py        (safe to re-run; --force overwrites local edits)

Adds GET /metrics/summary, GET+POST /metrics/snapshots (the Baseline tab called these but the
backend never had them), computed from request_logs / breaker_events. Also makes /stats/global
and /stats/requests count only real successful requests. Adds schema.sql (IF NOT EXISTS only).
"""
import base64, hashlib, os, sys, zlib

FORCE = "--force" in sys.argv
FILES = [
    ('app/main.py', 'fde708d955e9884dd81c5fd1c26d50aac0f839c4c015ad8ce677dde8fe75fb38', (
        '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'
        'NtXo06tvxNekg5uQGA35HzI2XII='
    )),
    ('app/tracker/tracker.py', '4d1655b659b1236ac6ae2d322c9d02a240233b3850ed5203fc2709ad1112bc9e', (
        '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'
        'xw=='
    )),
    ('app/tracker/metrics.py', None, (
        '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'
    )),
    ('schema.sql', None, (
        'eNqVVMFO4zAQvfcr5gZItOx9TwEMym5IUWIk4BK5ydBYJHbW41D69ztpmtJAYdlcKvXNG7958+zpFBLMrSHv2txjAU/O1uBLhDBO'
        'RSLpLBWRuJAE2oBqmjPvVP6MbvidNWtQphhBNXqnc2JoNplOQbygWwN55bFG40EThFcQzyWI+zCV6SmQBdcao80StAe1VJr1wNq2'
        'DvBVk++AGK2BQnm1UIRd27xUZokExvqyK/ClYm7lUBXrnkYzSFrTtey082RI5a4FeAtLZAasrHvuGixZ4Eqx5slFIgIpQAbnkRhr'
        'BYd/WiSfVXZJcDwB/nQB4+88vGbrwiCC2yS8CZIH+C0eTje1XtfMVnUz1MrwRqQyuLmVj5tj4rsogktxFdxFkkdbHZ/0xJbQZfsn'
        'SXEve4i3xw6T8tqaTckb1Dj7ogtmMr0Ys2pbYLX7fwStFGVPqqoWvM5+oPk8EkH8UeBVEKViUMGutPSm8HJ+19l3m4iLMA3nB9g/'
        'embFvpt8ndX0CbMvY990zaVF5u0zGg5RLMW1SLbHq7zErORdD0v4juYNiXLb4HsPuri2BGO/PzY7ojbPkehoq9E56zK/HvrtNcR6'
        'wdL3Zv1kyk3ZnpnvyyYnP4d8hvGluP8in9kubRyLV+AVjNO7g7nlV5lf8J3ie53hC1/fA6n/Xt6/m/Qhs+8971FbFdnmKTmIGlwd'
        'RCf/mLB/sTIyqqHS+oM3+4spK7XA6mBQti6wfSZT/r9u/EqbwvI42uS4TxqhrfG6+ohuX+Be0K90Hp+PrPgLDK23ng=='
    )),
]

def sha(b):
    return hashlib.sha256(b.replace(b"\r\n", b"\n")).hexdigest()

if not os.path.isdir("app/tracker"):
    sys.exit("Run this from the backend folder (the one containing app/).")
changed = skipped = 0
for path, baseline, blob in FILES:
    new = zlib.decompress(base64.b64decode("".join(blob)))
    crlf = False
    if os.path.exists(path):
        cur = open(path, "rb").read()
        if sha(cur) == sha(new):
            print("already applied :", path); continue
        if baseline and sha(cur) != baseline and not FORCE:
            print("SKIPPED (local edits, use --force to overwrite):", path); skipped += 1; continue
        crlf = b"\r\n" in cur
        open(path + ".bak", "wb").write(cur)
    open(path, "wb").write(new.replace(b"\n", b"\r\n") if crlf else new)
    print("updated         :", path); changed += 1
print(f"\ndone: {changed} updated, {skipped} skipped")
sys.exit(1 if skipped else 0)