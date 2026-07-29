#!/usr/bin/env python3
"""Synchronize platform-neutral accepted contracts into the macOS product tree."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "Internal" / "app"
MAC = APP / "Ports" / "macOS" / "app"

SHARED_FILES = (
    "dictation_session.py",
    "dictation_trace.py",
    "history.py",
    "insertion.py",
    "macos_insertion.py",
    "macos_focus.py",
    "macos_permissions.py",
    "mumble_find.py",
    "meeting.py",
    "meeting_store.py",
    "stats.py",
)

WEBUI_FILES = (
    "app.css",
    "app.js",
    "enhanced.css",
    "focus-stage-contract.json",
    "focus-stage.css",
    "focus-stage.js",
    "index.html",
    "remaster.css",
    "search-popup-loader.js",
    "search-popup.html",
    "system-search-loader.js",
)


MAC_TEXT_REPLACEMENTS = {
    "webui/index.html": (
        ("Ctrl + Win", "Ctrl + Option + D"),
        ("Ctrl+Win", "Ctrl+Option+D"),
        ("Ctrl + Alt + V", "Ctrl + Option + V"),
        ("Ctrl+Alt+V", "Ctrl+Option+V"),
        ("Ctrl + Alt + D", "Ctrl + Option + H"),
        ("Ctrl+Alt+D", "Ctrl+Option+H"),
        ("ctrl+alt+d", "ctrl+option+h"),
        ("Ctrl + Alt + F", "Ctrl + Option + F"),
        ("Ctrl+Alt+F", "Ctrl+Option+F"),
        ("Ctrl + Alt + S", "Ctrl + Option + S"),
        ("Ctrl+Alt+S", "Ctrl+Option+S"),
        ("Start with Windows", "Start at Login"),
        ("%APPDATA%\\Mumble", "~/Library/Application Support/Mumble"),
        ('<option value="edge">Microsoft Edge</option>',
         '<option value="safari">Safari</option>'),
    ),
    "webui/app.js": (
        ("Ctrl + Win", "Ctrl + Option + D"),
        ("Ctrl+Win", "Ctrl+Option+D"),
        ("ctrl+windows", "ctrl+option+d"),
        ("Ctrl + Alt + V", "Ctrl + Option + V"),
        ("Ctrl+Alt+V", "Ctrl+Option+V"),
        ("ctrl+alt+v", "ctrl+option+v"),
        ("Ctrl + Alt + D", "Ctrl + Option + H"),
        ("Ctrl+Alt+D", "Ctrl+Option+H"),
        ("ctrl+alt+d", "ctrl+option+h"),
        ("Ctrl + Alt + F", "Ctrl + Option + F"),
        ("Ctrl+Alt+F", "Ctrl+Option+F"),
        ("ctrl+alt+f", "ctrl+option+f"),
        ("Ctrl + Alt + S", "Ctrl + Option + S"),
        ("Ctrl+Alt+S", "Ctrl+Option+S"),
        ("ctrl+alt+s", "ctrl+option+s"),
        ("anywhere on Windows", "anywhere on your Mac"),
    ),
}

MACOS_PERMISSION_CARD = '''                <div class="card pad" id="macos-permissions" data-settings-category="system">
                    <div class="flex items-center gap8 mb12"><div class="icon-tile"><span data-icon="shield"></span></div><h3 class="fs13 fw6">macOS permissions</h3></div>
                    <p class="help mb8">Mumble reports current operating-system access honestly. Restore revoked access here; shortcuts re-arm without a restart.</p>
                    <div class="set-row"><div><div class="set-label">Microphone</div><div class="set-sub" data-macos-permission-status="microphone">Checking…</div></div><div class="set-ctl flex gap8"><button type="button" class="btn btn-sm" data-macos-permission-request="microphone">Request</button><button type="button" class="btn btn-sm" data-macos-permission-open="microphone">Open Settings</button></div></div>
                    <div class="set-row"><div><div class="set-label">Accessibility</div><div class="set-sub" data-macos-permission-status="accessibility">Checking…</div></div><div class="set-ctl flex gap8"><button type="button" class="btn btn-sm" data-macos-permission-request="accessibility">Request</button><button type="button" class="btn btn-sm" data-macos-permission-open="accessibility">Open Settings</button></div></div>
                    <div class="set-row"><div><div class="set-label">Input Monitoring</div><div class="set-sub" data-macos-permission-status="input_monitoring">Checking…</div></div><div class="set-ctl flex gap8"><button type="button" class="btn btn-sm" data-macos-permission-request="input_monitoring">Request</button><button type="button" class="btn btn-sm" data-macos-permission-open="input_monitoring">Open Settings</button></div></div>
                </div>

'''


def mappings():
    for relative in SHARED_FILES:
        yield APP / relative, MAC / relative
    for relative in WEBUI_FILES:
        yield APP / "webui" / relative, MAC / "webui" / relative
    search = APP / "experimental" / "system_search"
    for source in sorted(search.iterdir()):
        if source.is_file() and source.suffix in {".py", ".js", ".css", ".md"}:
            yield source, MAC / "experimental" / "system_search" / source.name


def expected_bytes(source, target):
    payload = source.read_bytes()
    try:
        relative = target.relative_to(MAC).as_posix()
    except ValueError:
        return payload
    replacements = MAC_TEXT_REPLACEMENTS.get(relative)
    if not replacements:
        return payload
    text = payload.decode("utf-8")
    for old, new in replacements:
        text = text.replace(old, new)
    if relative == "webui/index.html":
        marker = "Make Mumble easy to find"
        marker_at = text.find(marker)
        if marker_at >= 0:
            start = text.rfind('<div class="field mb8"', 0, marker_at)
            tip = text.find('<p class="help text-center">', marker_at)
            end = text.find("</p>", tip) + len("</p>") if tip >= 0 else -1
            if start >= 0 and end > start:
                start = text.rfind("\n", 0, start) + 1
                text = text[:start] + text[end:]
        system_marker = ('                <div class="card pad" '
                         'id="settings-system" data-settings-category="system">')
        if system_marker not in text:
            raise RuntimeError("macOS permissions insertion marker is missing")
        text = text.replace(system_marker, MACOS_PERMISSION_CARD + system_marker, 1)
    return text.encode("utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    drift = []
    copied = []
    for source, target in mappings():
        if not source.is_file():
            drift.append(f"missing authority: {source.relative_to(ROOT)}")
            continue
        expected = expected_bytes(source, target)
        same = target.is_file() and expected == target.read_bytes()
        if same:
            continue
        if args.check:
            drift.append(str(target.relative_to(ROOT)))
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(expected)
        copied.append(str(target.relative_to(ROOT)))
    if drift:
        print("macOS shared-contract drift:")
        for value in drift:
            print(f"  {value}")
        return 1
    if copied:
        print(f"Synchronized {len(copied)} macOS shared-contract files.")
    else:
        print("macOS shared contracts are synchronized.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
