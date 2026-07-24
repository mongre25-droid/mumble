#!/usr/bin/env python3
"""Command-channel token auth — the unauthenticated-port fix.

The controller's localhost command port (49519) and the single-instance signal
(49517) used to accept ANY local process's commands: `paste` (types into the
focused app), `grab_selection` (reads the highlighted text), `deck_job`, `quit`.
They now require a per-session token the controller mints and writes to a
same-user-only file; the web window and a second launch read it back.

Offline: no sockets, no audio, no network. Exercises the dispatch gate
(`_cmd_token_ok`) and the controller-write -> client-read file round-trip, all
against an isolated temp data dir so the live token file is never touched.

Run:  .venv\\Scripts\\python.exe test_cmd_auth.py
"""
import os
import shutil
import stat
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import branding  # noqa: E402
import mumble_linux as mumble  # noqa: E402  (exercise the controller Linux launches)
import webui_shell  # noqa: E402

_fails = []


def check(desc, cond):
    print(f"  [{'ok  ' if cond else 'FAIL'}] {desc}")
    if not cond:
        _fails.append(desc)


# Isolate from the live %APPDATA%\Mumble so the test never rotates a running
# instance's token (cmd_token_path() reads branding.DATA_DIR live).
_orig_data_dir = branding.DATA_DIR
_tmp = tempfile.mkdtemp(prefix="mumble_cmdauth_")
branding.DATA_DIR = _tmp
try:
    # ---- _cmd_token_ok: the dispatch gate --------------------------------
    print("== _cmd_token_ok accepts only this session's token ==")
    app = mumble.Mumble.__new__(mumble.Mumble)  # bypass the heavy __init__
    app._cmd_token = "test-session-token-placeholder"
    check("right token accepted",
          app._cmd_token_ok({"token": "test-session-token-placeholder", "cmd": "paste"}) is True)
    check("wrong token rejected",
          app._cmd_token_ok({"token": "nope", "cmd": "paste"}) is False)
    check("missing token rejected", app._cmd_token_ok({"cmd": "paste"}) is False)
    check("empty token rejected",
          app._cmd_token_ok({"token": "", "cmd": "paste"}) is False)

    # An app whose token never minted must fail CLOSED (no attribute at all).
    app2 = mumble.Mumble.__new__(mumble.Mumble)
    check("no-token app fails closed", app2._cmd_token_ok({"token": "anything"}) is False)
    # A blank session token must never validate (else clients could send blank).
    app3 = mumble.Mumble.__new__(mumble.Mumble)
    app3._cmd_token = ""
    check("blank session token never validates", app3._cmd_token_ok({"token": ""}) is False)

    # ---- file round-trip: controller writes, client reads ----------------
    print("\n== token file round-trip (controller write -> client read) ==")
    app4 = mumble.Mumble.__new__(mumble.Mumble)
    app4._ensure_cmd_token()
    check("token minted (64 hex chars)",
          isinstance(app4._cmd_token, str) and len(app4._cmd_token) == 64)
    on_disk = ""
    try:
        with open(branding.cmd_token_path(), encoding="utf-8") as f:
            on_disk = f.read().strip()
    except OSError:
        pass
    check("token written to the token file", on_disk == app4._cmd_token)
    if os.name != "nt":
        mode = stat.S_IMODE(os.stat(branding.cmd_token_path()).st_mode)
        check("token file is owner-only at creation", mode == 0o600)

    webui_shell._CMD_TOKEN = None  # clear the client cache
    check("client reads the controller's token",
          webui_shell._read_cmd_token() == app4._cmd_token)

    # A fresh mint rotates the token; a forced re-read picks up the new one
    # (mirrors a controller restart, which _ctrl_send recovers from on 'unauthorized').
    old = app4._cmd_token
    app4._ensure_cmd_token()
    check("re-mint rotates the token", app4._cmd_token != old)
    check("forced re-read sees the rotated token",
          webui_shell._read_cmd_token(force=True) == app4._cmd_token)
finally:
    branding.DATA_DIR = _orig_data_dir
    webui_shell._CMD_TOKEN = None
    shutil.rmtree(_tmp, ignore_errors=True)

# ===================================================================== final
if _fails:
    print(f"\n{len(_fails)} FAILED: {_fails}")
    sys.exit(1)
print("\nALL GREEN")
sys.exit(0)
