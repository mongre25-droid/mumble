"""Optional local host for the 5.6 sol xtra high assistant experiment.

Run with ``python desktop_host.py``.  It serves the zero-CDN interface, exposes
the pure planner over localhost, watches Right Shift on Windows, and executes a
small allow-list of reversible actions.  It never imports or changes Mumble's
production runtime.
"""

from __future__ import annotations

import argparse
import ctypes
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import threading
from typing import Any
from urllib.parse import urlparse
import webbrowser

from assistant_core import Action, build_plan, validate_plan


ROOT = Path(__file__).resolve().parent
APP_COMMANDS = {
    "calculator": ["calc.exe"],
    "file-explorer": ["explorer.exe"],
    "notepad": ["notepad.exe"],
    "paint": ["mspaint.exe"],
    "settings": ["cmd.exe", "/c", "start", "", "ms-settings:"],
}


class ExperimentState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.hotkey_generation = 0
        self.hotkey_active = False
        self.last_event = "Host ready"

    def set_hotkey(self, active: bool) -> None:
        with self._lock:
            if active and not self.hotkey_active:
                self.hotkey_generation += 1
                self.last_event = "Right Shift pressed"
            self.hotkey_active = active

    def note(self, message: str) -> None:
        with self._lock:
            self.last_event = message

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "connected": True,
                "platform": os.name,
                "hotkey_generation": self.hotkey_generation,
                "hotkey_active": self.hotkey_active,
                "last_event": self.last_event,
                "saved_hours": 0,
            }


STATE = ExperimentState()


class RightShiftMonitor(threading.Thread):
    """Tiny Windows-only edge detector; stops with the host process."""

    VK_RSHIFT = 0xA1

    def __init__(self, state: ExperimentState) -> None:
        super().__init__(name="experimental-right-shift", daemon=True)
        self.state = state
        self._stop_event = threading.Event()

    def run(self) -> None:
        if os.name != "nt":
            return
        get_state = ctypes.windll.user32.GetAsyncKeyState
        previous = False
        while not self._stop_event.wait(0.035):
            down = bool(get_state(self.VK_RSHIFT) & 0x8000)
            if down != previous:
                self.state.set_hotkey(down)
                previous = down

    def stop(self) -> None:
        self._stop_event.set()


def _focus_window(title_fragment: str) -> tuple[bool, str]:
    if os.name != "nt":
        return False, "Window focusing is available only on Windows."
    user32 = ctypes.windll.user32
    matches: list[tuple[int, str]] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if not length:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value
        if title_fragment.casefold() in title.casefold():
            matches.append((hwnd, title))
        return True

    user32.EnumWindows(callback, 0)
    if not matches:
        return False, f"No visible window matched “{title_fragment}”."
    hwnd, title = matches[0]
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    ok = bool(user32.SetForegroundWindow(hwnd))
    message = (
        f"Focused “{title}”."
        if ok
        else f"Windows declined focus for “{title}”."
    )
    return ok, message


def _open_path(target: str) -> tuple[bool, str]:
    path = Path(os.path.expandvars(os.path.expanduser(target))).resolve()
    if not path.exists():
        return False, f"Path does not exist: {path}"
    if os.name != "nt" or not hasattr(os, "startfile"):
        return (
            False,
            "Path opening is available only on Windows in this prototype.",
        )
    os.startfile(str(path))  # type: ignore[attr-defined]
    return True, f"Opened {path.name or path}."


def execute_action(action: Action) -> tuple[bool, str]:
    if not action.executable:
        return False, action.note or "This action is not connected."
    if action.kind == "open_app":
        command = APP_COMMANDS.get(action.target)
        if not command:
            return False, "Application is outside the reviewed allow-list."
        subprocess.Popen(command, close_fds=True)
        return True, action.label
    if action.kind == "open_path":
        return _open_path(action.target)
    if action.kind == "focus_window":
        return _focus_window(action.target)
    if action.kind == "open_url":
        parsed = urlparse(action.value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return False, "Only valid HTTP(S) addresses can be opened."
        opened = webbrowser.open_new_tab(action.value)
        message = (
            action.label
            if opened
            else "The default browser declined the URL."
        )
        return bool(opened), message
    return False, f"No executor is registered for {action.kind}."


class Handler(SimpleHTTPRequestHandler):
    server_version = "MumbleExperiment/1.0"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, fmt: str, *args) -> None:
        print(f"[experiment] {self.address_string()} {fmt % args}")

    def end_headers(self) -> None:
        # The experiment is edited in place and has no build step.  Prevent an
        # evaluation browser from presenting stale CSS/JS after a review fix.
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def _json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        try:
            length = min(int(self.headers.get("Content-Length", "0")), 64_000)
            if not length:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, json.JSONDecodeError):
            return {}

    def do_GET(self) -> None:  # noqa: N802 - stdlib hook
        if self.path == "/api/health":
            self._json({"ok": True, "identity": "5.6 sol xtra high"})
            return
        if self.path == "/api/state":
            self._json(STATE.snapshot())
            return
        if self.path in {"/", "/index.html"}:
            self.path = "/index.html"
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802 - stdlib hook
        payload = self._read_json()
        if self.path == "/api/plan":
            try:
                plan = build_plan(str(payload.get("command", "")))
            except ValueError as exc:
                self._json(
                    {"ok": False, "error": str(exc)},
                    HTTPStatus.BAD_REQUEST,
                )
                return
            STATE.note(f"Planned: {plan.summary}")
            self._json({"ok": True, "plan": plan.to_dict()})
            return
        if self.path == "/api/execute":
            try:
                plan = validate_plan(payload.get("plan", {}))
            except ValueError as exc:
                self._json(
                    {"ok": False, "error": str(exc)},
                    HTTPStatus.BAD_REQUEST,
                )
                return
            if (
                plan.confirmation_required
                and payload.get("confirmed") is not True
            ):
                self._json(
                    {
                        "ok": False,
                        "error": "This plan requires explicit confirmation.",
                    },
                    HTTPStatus.CONFLICT,
                )
                return
            results = []
            all_ok = True
            for action in plan.actions:
                ok, message = execute_action(action)
                results.append(
                    {"ok": ok, "kind": action.kind, "message": message}
                )
                all_ok = all_ok and ok
                if not ok:
                    break
            STATE.note(
                "Workflow complete" if all_ok else "Workflow stopped safely"
            )
            self._json({"ok": all_ok, "results": results})
            return
        self._json(
            {"ok": False, "error": "Unknown endpoint."},
            HTTPStatus.NOT_FOUND,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    monitor = RightShiftMonitor(STATE)
    monitor.start()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"[experiment] 5.6 sol xtra high running at {url}")
    print(
        "[experiment] Press Ctrl+C to stop. "
        "Right Shift is watched only while this host runs."
    )
    if not args.no_browser:
        threading.Timer(0.35, lambda: webbrowser.open_new_tab(url)).start()
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        monitor.stop()
        server.server_close()
        print("[experiment] stopped")


if __name__ == "__main__":
    main()
