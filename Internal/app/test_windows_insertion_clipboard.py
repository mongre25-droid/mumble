#!/usr/bin/env python3
"""Deterministic Windows clipboard safety tests without touching the real clipboard."""

import ctypes
from contextlib import contextmanager
import struct
import unittest

from insertion import InsertionRequest, TargetContext
from windows_insertion import (
    _INPUT,
    CF_DIB,
    CF_HDROP,
    CF_UNICODETEXT,
    WindowsClipboardAdapter,
)


class MemoryWindowsClipboard(WindowsClipboardAdapter):
    def __init__(self, formats=None, names=None, max_formats=64,
                 max_total_bytes=32 * 1024 * 1024,
                 max_format_bytes=16 * 1024 * 1024):
        self.formats = dict(formats or {})
        self.names = dict(names or {})
        self.sequence = 10
        self.max_formats = max_formats
        self.max_total_bytes = max_total_bytes
        self.max_format_bytes = max_format_bytes
        self.history_id = 60001
        self.cloud_id = 60002
        self.corrupt_restore = False

    def _enumerate_format_ids(self):
        return list(self.formats)

    def _read_format_bytes(self, format_id):
        return self.formats.get(format_id)

    def _replace_formats(self, formats):
        self.formats = {format_id: bytes(data) for format_id, data in formats}
        if self.corrupt_restore and self.formats:
            first = next(iter(self.formats))
            self.formats[first] = b"corrupt"
        self.sequence += 1
        return self.sequence

    def _sequence(self):
        return self.sequence

    def _privacy_formats(self):
        return self.history_id, self.cloud_id

    def _format_name(self, format_id):
        if format_id in self.names:
            return self.names[format_id]
        return super()._format_name(format_id)


class TransactionalClipboard(WindowsClipboardAdapter):
    """In-memory Win32-shaped adapter that exercises the real replace logic."""

    class User32:
        def __init__(self, owner):
            self.owner = owner

        def EmptyClipboard(self):
            if not self.owner.empty_success:
                return False
            self.owner.formats.clear()
            self.owner.sequence += 1
            return True

    def __init__(self, formats, *, fail_calls=(), corrupt_readback=False,
                 external_mutation=False, empty_success=True,
                 external_mutation_during_recovery=False):
        self.formats = dict(formats)
        self.sequence = 20
        self.max_formats = 64
        self.max_total_bytes = 32 * 1024 * 1024
        self.max_format_bytes = 16 * 1024 * 1024
        self.history_id = 60001
        self.cloud_id = 60002
        self.user32 = self.User32(self)
        self.fail_calls = set(fail_calls)
        self.corrupt_readback = corrupt_readback
        self.external_mutation = external_mutation
        self.external_mutation_during_recovery = (
            external_mutation_during_recovery)
        self.recovery_mutation_done = False
        self.empty_success = empty_success
        self.set_calls = 0
        self.open_count = 0
        self.close_count = 0
        self.allocated = []
        self.transferred = []
        self.freed = []
        self._replacement_finished = False

    @contextmanager
    def _opened(self):
        self.open_count += 1
        try:
            yield self
        finally:
            self.close_count += 1

    def _enumerate_format_ids(self):
        return list(self.formats)

    def _read_format_bytes(self, format_id):
        value = self.formats.get(format_id)
        if (self.corrupt_readback and self._replacement_finished
                and format_id == CF_UNICODETEXT
                and value != b"prior\x00\x00"):
            return b"mismatch"
        return value

    def _replace_formats(self, formats, **kwargs):
        if (self.external_mutation_during_recovery
                and self._replacement_finished
                and not self.recovery_mutation_done):
            self.formats = {CF_UNICODETEXT: b"external recovery\x00\x00"}
            self.sequence += 1
            self.recovery_mutation_done = True
        try:
            return super()._replace_formats(formats, **kwargs)
        finally:
            self._replacement_finished = True

    def _read_format_bytes_open(self, format_id):
        return self._read_format_bytes(format_id)

    def _set_format(self, format_id, data):
        self.set_calls += 1
        handle = "handle-{}".format(self.set_calls)
        self.allocated.append(handle)
        if self.set_calls in self.fail_calls:
            self.freed.append(handle)
            if self.external_mutation:
                self.formats = {CF_UNICODETEXT: b"external\x00\x00"}
                self.sequence += 1
            raise RuntimeError("injected SetClipboardData failure")
        self.formats[format_id] = bytes(data)
        self.transferred.append(handle)

    def _sequence(self):
        return self.sequence

    def _privacy_formats(self):
        return self.history_id, self.cloud_id

    def _format_name(self, format_id):
        return "CF_UNICODETEXT" if format_id == CF_UNICODETEXT else str(format_id)


def text_request(text="private dictation"):
    return InsertionRequest(
        operation_id="clipboard-test",
        source="dictation",
        content_kind="text",
        activation_target=TargetContext(1, 2, 3, 4, "medium"),
        text=text,
    )


class WindowsClipboardTests(unittest.TestCase):
    def test_pre_replacement_failure_does_not_run_destructive_recovery(self):
        prior = {CF_UNICODETEXT: b"prior\x00\x00"}
        clipboard = TransactionalClipboard(prior, empty_success=False)
        snapshot = clipboard.snapshot()

        with self.assertRaises(RuntimeError) as caught:
            clipboard.write(text_request(), snapshot)

        self.assertEqual(prior, clipboard.formats)
        self.assertEqual(0, clipboard.set_calls)
        self.assertFalse(getattr(caught.exception,
                                 "clipboard_restored", False))
        self.assertFalse(getattr(caught.exception,
                                 "clipboard_changed_externally", False))
        self.assertEqual("", getattr(caught.exception,
                                     "cleanup_warning", ""))

    def test_second_and_third_format_failures_restore_the_previous_clipboard(self):
        prior = {CF_UNICODETEXT: b"prior\x00\x00"}
        for failed_call in (2, 3):
            with self.subTest(failed_call=failed_call):
                clipboard = TransactionalClipboard(
                    prior, fail_calls={failed_call})
                snapshot = clipboard.snapshot()

                with self.assertRaises(RuntimeError) as caught:
                    clipboard.write(text_request(), snapshot)

                self.assertEqual(prior, clipboard.formats)
                self.assertTrue(getattr(caught.exception,
                                        "clipboard_restored", False))
                self.assertEqual(clipboard.open_count, clipboard.close_count)
                self.assertEqual(len(clipboard.allocated),
                                 len(clipboard.transferred) + len(clipboard.freed))

    def test_readback_mismatch_restores_the_previous_clipboard(self):
        prior = {CF_UNICODETEXT: b"prior\x00\x00"}
        clipboard = TransactionalClipboard(prior, corrupt_readback=True)
        snapshot = clipboard.snapshot()

        with self.assertRaises(RuntimeError) as caught:
            clipboard.write(text_request(), snapshot)

        self.assertEqual(prior, clipboard.formats)
        self.assertTrue(getattr(caught.exception, "clipboard_restored", False))
        self.assertEqual(clipboard.open_count, clipboard.close_count)

    def test_external_mutation_during_failed_write_is_preserved(self):
        clipboard = TransactionalClipboard(
            {CF_UNICODETEXT: b"prior\x00\x00"}, fail_calls={2},
            external_mutation=True)
        snapshot = clipboard.snapshot()

        with self.assertRaises(RuntimeError) as caught:
            clipboard.write(text_request(), snapshot)

        self.assertEqual({CF_UNICODETEXT: b"external\x00\x00"},
                         clipboard.formats)
        self.assertTrue(getattr(caught.exception,
                                "clipboard_changed_externally", False))
        self.assertFalse(getattr(caught.exception,
                                 "clipboard_restored", False))
        self.assertEqual(clipboard.open_count, clipboard.close_count)

    def test_external_mutation_during_recovery_is_preserved(self):
        clipboard = TransactionalClipboard(
            {CF_UNICODETEXT: b"prior\x00\x00"}, fail_calls={2},
            external_mutation_during_recovery=True)
        snapshot = clipboard.snapshot()

        with self.assertRaises(RuntimeError) as caught:
            clipboard.write(text_request(), snapshot)

        self.assertEqual(
            {CF_UNICODETEXT: b"external recovery\x00\x00"},
            clipboard.formats)
        self.assertTrue(getattr(caught.exception,
                                "clipboard_changed_externally", False))
        self.assertFalse(getattr(caught.exception,
                                 "clipboard_restored", False))
        self.assertEqual(clipboard.open_count, clipboard.close_count)

    def test_restore_failure_reports_cleanup_warning_and_never_hides_partial_state(self):
        clipboard = TransactionalClipboard(
            {CF_UNICODETEXT: b"prior\x00\x00"}, fail_calls={2, 3})
        snapshot = clipboard.snapshot()

        with self.assertRaises(RuntimeError) as caught:
            clipboard.write(text_request(), snapshot)

        self.assertFalse(getattr(caught.exception,
                                 "clipboard_restored", False))
        self.assertIn("could not be restored",
                      getattr(caught.exception, "cleanup_warning", ""))
        self.assertEqual(clipboard.open_count, clipboard.close_count)

    def test_sendinput_structure_matches_the_native_pointer_width(self):
        expected = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28
        self.assertEqual(expected, ctypes.sizeof(_INPUT))

    def test_text_write_verifies_exact_readback_and_sets_both_privacy_formats(self):
        clipboard = MemoryWindowsClipboard({CF_UNICODETEXT: b"prior\x00\x00"})
        snapshot = clipboard.snapshot()

        ownership = clipboard.write(text_request(), snapshot)

        expected = "private dictation".encode("utf-16-le") + b"\x00\x00"
        self.assertEqual(expected, clipboard.formats[CF_UNICODETEXT])
        self.assertEqual(struct.pack("<I", 0), clipboard.formats[clipboard.history_id])
        self.assertEqual(struct.pack("<I", 0), clipboard.formats[clipboard.cloud_id])
        self.assertTrue(clipboard.still_owns(ownership))
        clipboard.formats[CF_UNICODETEXT] = b"mutated"
        self.assertFalse(clipboard.still_owns(ownership))

    def test_restore_requires_exact_format_readback(self):
        clipboard = MemoryWindowsClipboard({CF_UNICODETEXT: b"prior\x00\x00"})
        snapshot = clipboard.snapshot()
        ownership = clipboard.write(text_request(), snapshot)
        clipboard.corrupt_restore = True

        self.assertFalse(clipboard.restore(snapshot, ownership))

    def test_supported_rich_image_and_file_formats_fit_inside_budgets(self):
        formats = {
            CF_UNICODETEXT: b"plain\x00\x00",
            CF_DIB: b"dib-bytes",
            CF_HDROP: b"drop-bytes",
            50001: b"{\\rtf1 rich}",
            50002: b"Version:1.0\r\n<html></html>",
        }
        names = {50001: "Rich Text Format", 50002: "HTML Format"}
        snapshot = MemoryWindowsClipboard(formats, names).snapshot()

        self.assertTrue(snapshot.restorable)
        self.assertEqual(5, len(snapshot.formats))

    def test_private_delayed_and_over_budget_formats_fail_closed(self):
        cases = [
            ("private", MemoryWindowsClipboard(
                {50003: b"secret"}, {50003: "Vendor Private"})),
            ("delayed", MemoryWindowsClipboard(
                {CF_UNICODETEXT: None})),
            ("format-budget", MemoryWindowsClipboard(
                {CF_DIB: b"12345"}, max_format_bytes=4)),
            ("total-budget", MemoryWindowsClipboard(
                {CF_DIB: b"1234", CF_HDROP: b"5678"}, max_total_bytes=7)),
            ("count-budget", MemoryWindowsClipboard(
                {CF_DIB: b"1", CF_HDROP: b"2"}, max_formats=1)),
        ]
        for name, clipboard in cases:
            with self.subTest(name=name):
                snapshot = clipboard.snapshot()
                self.assertFalse(snapshot.restorable)
                self.assertTrue(snapshot.reason)


if __name__ == "__main__":
    unittest.main()
