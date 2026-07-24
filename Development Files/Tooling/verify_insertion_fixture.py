#!/usr/bin/env python3
"""Run Mumble's native two-field exact-once insertion fixture on Windows.

This is intentionally a physical verification helper, not an automated CI test:
it uses the real foreground window, Windows clipboard and SendInput APIs. It
creates two editable fields, inserts a known value into the first field through
the same transaction used by Mumble, and proves that the second stays empty.
"""

from __future__ import annotations

import os
import sys
import tkinter as tk
import uuid


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
APP = os.path.join(ROOT, "Internal", "app")
if APP not in sys.path:
    sys.path.insert(0, APP)

from insertion import InsertionOutcome, InsertionRequest, InsertionTransaction
from windows_insertion import (
    WindowsClipboardAdapter,
    WindowsNativeInputAdapter,
    WindowsTargetAdapter,
)


PAYLOAD = "Mumble exact-once fixture 7d98b2"


def main():
    if os.name != "nt":
        print("This verifier runs only on Windows.")
        return 2

    root = tk.Tk()
    root.title("Mumble insertion fixture")
    root.geometry("600x180")
    root.attributes("-topmost", True)

    tk.Label(root, text="Target A (must receive one payload)").pack(anchor="w", padx=18, pady=(16, 2))
    target_a = tk.Entry(root, width=72)
    target_a.pack(padx=18, fill="x")
    tk.Label(root, text="Target B (must stay empty)").pack(anchor="w", padx=18, pady=(12, 2))
    target_b = tk.Entry(root, width=72)
    target_b.pack(padx=18, fill="x")
    result_label = tk.Label(root, text="Preparing native insertion…", anchor="w")
    result_label.pack(padx=18, pady=(14, 0), fill="x")
    result = {"failed": False}

    def execute():
        target_a.focus_force()
        root.update_idletasks()
        transaction = InsertionTransaction(
            WindowsTargetAdapter(), WindowsClipboardAdapter(),
            WindowsNativeInputAdapter(),
        )
        activation_target = transaction._target.current()  # Fixture-only capture.
        terminal = transaction.insert(InsertionRequest(
            operation_id=uuid.uuid4().hex,
            source="native_two_field_fixture",
            content_kind="text",
            text=PAYLOAD,
            activation_target=activation_target,
        ))
        exact_once = target_a.get() == PAYLOAD and not target_b.get()
        terminal_ok = terminal.outcome in {
            InsertionOutcome.CONFIRMED, InsertionOutcome.SENT_UNCONFIRMED,
        }
        if exact_once and terminal_ok:
            result_label.config(
                text="PASS — Target A received one payload; Target B stayed empty. "
                     "Native outcome: {}.".format(terminal.outcome.value),
                fg="#187a2f",
            )
        else:
            result["failed"] = True
            result_label.config(
                text="FAIL — outcome {}; A={!r}; B={!r}.".format(
                    terminal.outcome.value, target_a.get(), target_b.get()),
                fg="#aa2020",
            )
        root.after(1500, root.destroy)

    root.after(450, execute)
    root.mainloop()
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
