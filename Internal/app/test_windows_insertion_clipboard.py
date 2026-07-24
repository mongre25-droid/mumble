#!/usr/bin/env python3
"""Deterministic Windows clipboard safety tests without touching the real clipboard."""

import ctypes
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


def text_request(text="private dictation"):
    return InsertionRequest(
        operation_id="clipboard-test",
        source="dictation",
        content_kind="text",
        activation_target=TargetContext(1, 2, 3, 4, "medium"),
        text=text,
    )


class WindowsClipboardTests(unittest.TestCase):
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
