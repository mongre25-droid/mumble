#!/usr/bin/env python3
r"""Tests for the per-session IPC token (branding.write/read/token_ok) that
authenticates Mumble's localhost command channels (49519 / 49520 / 49517).
Run:  .venv\Scripts\python.exe test_session_token.py    (exit 0 = all pass)"""

import os
import sys
import tempfile

import branding

fails = []


def check(name, cond):
    print(f"  [{'ok  ' if cond else 'FAIL'}] {name}")
    if not cond:
        fails.append(name)


# Redirect the token + data dir to a throwaway temp location so the test never
# touches the real %APPDATA%\Mumble.
_tmp = tempfile.mkdtemp(prefix="mumble_tok_")
branding.DATA_DIR = _tmp
branding.SESSION_TOKEN_PATH = os.path.join(_tmp, ".session_token")

print("== write/read round-trip ==")
tok = branding.write_session_token()
check("write returns a non-empty token", bool(tok))
check("token is reasonably long (>=32 chars)", len(tok) >= 32)
check("read returns exactly what was written", branding.read_session_token() == tok)
check("token file exists on disk", os.path.isfile(branding.SESSION_TOKEN_PATH))

print("\n== fresh token each session ==")
tok2 = branding.write_session_token()
check("second write yields a DIFFERENT token", tok2 != tok)
check("read now returns the newest token", branding.read_session_token() == tok2)

print("\n== token_ok: constant-time match semantics ==")
check("exact match -> True", branding.token_ok(tok2, tok2))
check("mismatch -> False", branding.token_ok(tok2, tok) is False)
check("empty supplied -> False", branding.token_ok("", tok2) is False)
check("empty expected -> False", branding.token_ok(tok2, "") is False)
check("both empty -> False (missing token never authenticates)",
      branding.token_ok("", "") is False)
check("None supplied -> False", branding.token_ok(None, tok2) is False)

print("\n== read with no token file -> '' (no controller running) ==")
try:
    os.remove(branding.SESSION_TOKEN_PATH)
except OSError:
    pass
check("read missing file -> empty string", branding.read_session_token() == "")
check("token_ok against absent token -> False",
      branding.token_ok("anything", branding.read_session_token()) is False)

try:
    os.rmdir(_tmp)
except OSError:
    pass

print(f"\n{'ALL GREEN' if not fails else 'FAILURES: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
