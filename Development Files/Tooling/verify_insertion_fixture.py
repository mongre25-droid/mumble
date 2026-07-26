#!/usr/bin/env python3
"""Native two-field issue #15 verification fixture for normal-integrity Windows.

Tk focus/capture and inspection always run on the Tk event-loop thread. The
insertion transaction runs on a worker so real SendInput events can be handled
by Tk while the transaction waits. This is closed-loop fixture evidence only;
it is not Office/browser/Electron/elevation evidence.
"""

from __future__ import annotations

import argparse
import ctypes
from dataclasses import replace
import os
import sys
import threading
import time
import tkinter as tk
import uuid


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
APP = os.path.join(ROOT, "Internal", "app")
if APP not in sys.path:
    sys.path.insert(0, APP)

from insertion import (  # noqa: E402
    InsertionOutcome,
    InsertionRequest,
    InsertionTransaction,
    NativeAcceptance,
    TargetLease,
    TargetEditability,
)
from windows_insertion import (  # noqa: E402
    CF_UNICODETEXT,
    WindowsClipboardAdapter,
    WindowsNativeInputAdapter,
    WindowsTargetAdapter,
)


PAYLOAD = "Mumble exact-once fixture 7d98b2"
PRIOR_CLIPBOARD = "Mumble fixture prior clipboard"
EXTERNAL_CLIPBOARD = "Mumble fixture external mutation"
LOGICAL_TARGET_A = 0xF15A
LOGICAL_TARGET_B = 0xF15B
MODES = (
    "normal", "delayed-focus", "swallowed-input", "clipboard-mutation",
    "delayed-read", "zero-count", "partial-count", "restore-failure",
    "privilege-higher", "privilege-unknown", "unintended-field",
)


class FixtureTargetAdapter(WindowsTargetAdapter):
    def __init__(self, mode, logical_focus, focus_request, focus_ack):
        super().__init__()
        self.mode = mode
        self.logical_focus = logical_focus
        self.focus_request = focus_request
        self.focus_ack = focus_ack

    def current(self):
        native = super().current()
        if native is None:
            return None
        # Tk Entries are logical controls inside one native Tk window. These
        # fixture-only IDs drive the two-field oracle; they are deliberately
        # distinct from the real HWND/process/thread evidence returned above.
        logical_child = int(self.logical_focus.get("child") or 0)
        return replace(
            native, focused_child=logical_child,
            has_caret=bool(logical_child))

    def can_inject(self, target):
        if self.mode == "privilege-higher":
            return False
        if self.mode == "privilege-unknown":
            return None
        return super().can_inject(target)

    def restore(self, target, timeout_s):
        if self.mode != "delayed-focus":
            return super().restore(target, timeout_s)
        self.focus_request.set()
        if not self.focus_ack.wait(max(0.0, timeout_s)):
            return False
        return target.same_destination(self.current())


class FixtureClipboardAdapter(WindowsClipboardAdapter):
    def __init__(self, mode):
        super().__init__()
        self.mode = mode

    def _read_format_bytes(self, format_id):
        if self.mode == "delayed-read":
            time.sleep(0.08)
        return super()._read_format_bytes(format_id)

    def restore(self, snapshot, ownership=None):
        if self.mode == "restore-failure":
            return False
        return super().restore(snapshot, ownership)


class FixtureNativeInputAdapter(WindowsNativeInputAdapter):
    def __init__(self, mode):
        super().__init__()
        self.mode = mode

    def send_paste(self):
        if self.mode == "zero-count":
            return NativeAcceptance(4, 0, False, None, "fixture_zero_count")
        if self.mode == "partial-count":
            return NativeAcceptance(4, 2, True, None, "fixture_partial_count")
        return super().send_paste()


def _clipboard_text(root):
    try:
        return root.clipboard_get()
    except tk.TclError:
        return ""


def _set_controlled_clipboard_text(value):
    WindowsClipboardAdapter()._replace_formats((
        (CF_UNICODETEXT, str(value).encode("utf-16-le") + b"\x00\x00"),
    ))


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=MODES, default="normal")
    parser.add_argument("--linger-ms", type=int, default=1200)
    args = parser.parse_args(argv)
    if os.name != "nt":
        print("This verifier runs only on Windows.")
        return 2

    root = tk.Tk()
    root.title("Mumble insertion fixture — {}".format(args.mode))
    root.geometry("680x220")
    root.attributes("-topmost", True)
    tk.Label(root, text="Target A (intended)").pack(anchor="w", padx=18, pady=(16, 2))
    target_a = tk.Entry(root, width=78)
    target_a.pack(padx=18, fill="x")
    tk.Label(root, text="Target B (must never receive the payload)").pack(
        anchor="w", padx=18, pady=(12, 2))
    target_b = tk.Entry(root, width=78)
    target_b.pack(padx=18, fill="x")
    result_label = tk.Label(root, text="Preparing native insertion…", anchor="w",
                            justify="left", wraplength=640)
    result_label.pack(padx=18, pady=(14, 0), fill="x")
    state = {"terminal": None, "failed": False, "worker": None,
             "prior": PRIOR_CLIPBOARD,
             "capture_deadline": time.monotonic() + 2.0}
    focus_request = threading.Event()
    focus_ack = threading.Event()
    logical_focus = {"child": 0}
    target_a.bind(
        "<FocusIn>",
        lambda _event: logical_focus.update(child=LOGICAL_TARGET_A))
    target_b.bind(
        "<FocusIn>",
        lambda _event: logical_focus.update(child=LOGICAL_TARGET_B))

    _set_controlled_clipboard_text(PRIOR_CLIPBOARD)

    def receive_native_paste(event):
        if args.mode == "swallowed-input":
            return "break"
        try:
            event.widget.insert("insert", root.clipboard_get())
        except tk.TclError:
            pass
        if args.mode == "clipboard-mutation":
            root.after(
                20, lambda: _set_controlled_clipboard_text(EXTERNAL_CLIPBOARD))
        return "break"

    target_a.bind("<Control-v>", receive_native_paste)

    def inspect_when_done():
        worker = state["worker"]
        if worker is not None and worker.is_alive():
            root.after(25, inspect_when_done)
            return
        terminal = state["terminal"]
        a_value, b_value = target_a.get(), target_b.get()
        insertion_count = a_value.count(PAYLOAD) + b_value.count(PAYLOAD)
        focus_widget = root.focus_get()
        focus_logical = ("A" if focus_widget is target_a else
                         "B" if focus_widget is target_b else "none")
        clipboard_value = _clipboard_text(root)
        expected_count = 1 if args.mode in {"normal", "delayed-focus",
                                            "delayed-read", "restore-failure",
                                            "clipboard-mutation"} else 0
        expected_a = expected_count == 1
        expected_clipboard = (
            EXTERNAL_CLIPBOARD if args.mode == "clipboard-mutation" else
            PAYLOAD if args.mode == "restore-failure" else PRIOR_CLIPBOARD)
        expected_outcomes = {
            "normal": {InsertionOutcome.SENT_UNCONFIRMED},
            "delayed-focus": {InsertionOutcome.SENT_UNCONFIRMED},
            "swallowed-input": {InsertionOutcome.SENT_UNCONFIRMED},
            "clipboard-mutation": {InsertionOutcome.SENT_UNCONFIRMED},
            "delayed-read": {InsertionOutcome.SENT_UNCONFIRMED},
            "zero-count": {InsertionOutcome.NOT_SENT},
            "partial-count": {InsertionOutcome.UNCERTAIN},
            "restore-failure": {InsertionOutcome.SENT_UNCONFIRMED},
            "privilege-higher": {InsertionOutcome.SAVED_ONLY},
            "privilege-unknown": {InsertionOutcome.SAVED_ONLY},
            "unintended-field": {InsertionOutcome.SAVED_ONLY},
        }
        expected_focus = {
            mode: ("B" if mode == "unintended-field" else "A")
            for mode in MODES
        }
        expected_native = {
            "normal": (4, 4, 1),
            "delayed-focus": (4, 4, 1),
            "swallowed-input": (4, 4, 1),
            "clipboard-mutation": (4, 4, 1),
            "delayed-read": (4, 4, 1),
            "zero-count": (4, 0, 1),
            "partial-count": (4, 2, 1),
            "restore-failure": (4, 4, 1),
            "privilege-higher": (0, 0, 0),
            "privilege-unknown": (0, 0, 0),
            "unintended-field": (0, 0, 0),
        }
        expected_wording = {
            "normal": "Sent", "delayed-focus": "Sent",
            "swallowed-input": "Sent", "clipboard-mutation": "Sent",
            "delayed-read": "Sent", "restore-failure": "Sent",
            "zero-count": "Not sent", "partial-count": "not confirmed",
            "privilege-higher": "Not sent", "privilege-unknown": "Not sent",
            "unintended-field": "Not sent",
        }
        expected_cleanup_warning = args.mode in {
            "clipboard-mutation", "restore-failure"}
        native_requested, native_accepted, send_count = expected_native[args.mode]
        outcome_ok = bool(
            terminal and terminal.outcome in expected_outcomes[args.mode])
        passed = all((
            outcome_ok,
            insertion_count == expected_count,
            (a_value == PAYLOAD) == expected_a,
            b_value == "",
            clipboard_value == expected_clipboard,
            bool(terminal and expected_wording[args.mode].casefold()
                 in terminal.message.casefold()),
            bool(terminal and terminal.native_requested == native_requested),
            bool(terminal and terminal.native_accepted == native_accepted),
            bool(terminal and terminal.send_count == send_count),
            bool(terminal and bool(terminal.cleanup_warning)
                 == expected_cleanup_warning),
            focus_logical == expected_focus[args.mode],
        ))
        state["failed"] = not passed
        detail = (
            "{} — outcome={}; count={}; A={!r}; B={!r}; focus={}; "
            "requested={}; accepted={}; sends={}; clipboard={!r}; message={}; "
            "cleanup_warning={!r}".format(
                "PASS" if passed else "FAIL",
                terminal.outcome.value if terminal else "missing",
                insertion_count, a_value, b_value,
                focus_logical,
                terminal.native_requested if terminal else "missing",
                terminal.native_accepted if terminal else "missing",
                terminal.send_count if terminal else "missing",
                clipboard_value,
                terminal.message if terminal else "missing terminal result",
                terminal.cleanup_warning if terminal else "missing"))
        result_label.config(text=detail, fg="#187a2f" if passed else "#aa2020")
        print(detail)
        root.after(max(100, args.linger_ms), root.destroy)

    def capture_on_tk_thread():
        child_hwnd = int(root.winfo_id())
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        top_hwnd = int(user32.GetAncestor(child_hwnd, 2) or child_hwnd)
        foreground = int(user32.GetForegroundWindow() or 0)
        own_tid = int(kernel32.GetCurrentThreadId())
        foreground_tid = int(
            user32.GetWindowThreadProcessId(foreground, None) or 0)
        attached = bool(foreground_tid and user32.AttachThreadInput(
            own_tid, foreground_tid, True))
        try:
            user32.ShowWindow(top_hwnd, 9)
            user32.BringWindowToTop(top_hwnd)
            user32.SetForegroundWindow(top_hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(own_tid, foreground_tid, False)
        root.lift()
        target_a.focus_force()
        root.update()
        target_adapter = FixtureTargetAdapter(
            args.mode, logical_focus, focus_request, focus_ack)
        captured = target_adapter.current()
        if captured is None or captured.window != top_hwnd:
            if time.monotonic() < state["capture_deadline"]:
                root.after(25, capture_on_tk_thread)
                return
            state["failed"] = True
            result_label.config(
                text="FAIL — Windows did not grant the fixture foreground focus; no input was sent.",
                fg="#aa2020")
            print(result_label.cget("text"))
            root.after(max(100, args.linger_ms), root.destroy)
            return
        # This fixture owns this exact Tk Entry and therefore has direct writable
        # evidence. Production browser/editor support is never inferred from a
        # class name; those surfaces remain in the separate physical matrix.
        captured = replace(captured, editability=TargetEditability.EDITABLE)
        lease = TargetLease(
            captured, "native_fixture", "fixture_capture",
            args.mode == "delayed-focus")
        if args.mode in {"unintended-field", "delayed-focus"}:
            target_b.focus_force()
            # Process B's FocusIn completely before the worker is allowed to
            # request restoration. update_idletasks() does not drain focus
            # events and was the remaining source of nondeterministic ordering.
            root.update()
        if args.mode == "delayed-focus":
            focus_probe = {"stable_turns": 0}

            def service_focus_request():
                if not focus_request.is_set():
                    root.after(5, service_focus_request)
                    return
                if focus_ack.is_set():
                    return
                user32.ShowWindow(top_hwnd, 9)
                user32.BringWindowToTop(top_hwnd)
                user32.SetForegroundWindow(top_hwnd)
                root.lift()
                target_a.focus_force()
                if (root.focus_get() is target_a
                        and logical_focus.get("child") == LOGICAL_TARGET_A):
                    focus_probe["stable_turns"] += 1
                else:
                    focus_probe["stable_turns"] = 0
                # Three separate Tk event-loop turns must observe A before the
                # worker is released. This is an acknowledged focus transition,
                # not an assumed fixed delay.
                if focus_probe["stable_turns"] >= 3:
                    focus_ack.set()
                    return
                # A foreground transition can be temporarily refused by Windows.
                # Retry only until the observable focus barrier acknowledges A;
                # the production transaction's original timeout remains decisive.
                root.after(5, service_focus_request)

            root.after(5, service_focus_request)

        def worker():
            transaction = InsertionTransaction(
                target_adapter,
                FixtureClipboardAdapter(args.mode),
                FixtureNativeInputAdapter(args.mode),
            )
            state["terminal"] = transaction.insert(InsertionRequest(
                operation_id=uuid.uuid4().hex,
                source="native_fixture",
                content_kind="text",
                text=PAYLOAD,
                activation_target=captured,
                target_lease=lease,
                focus_timeout_s=0.40,
            ))

        state["worker"] = threading.Thread(
            target=worker, name="insertion-fixture-worker", daemon=True)
        state["worker"].start()
        root.after(25, inspect_when_done)

    root.after(350, capture_on_tk_thread)
    root.mainloop()
    return 1 if state["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
