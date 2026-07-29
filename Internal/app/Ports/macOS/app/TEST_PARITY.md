# Test Suite Parity — macOS Port

Updated 2026-07-29 for the Issue #26 source-parity candidate.

- `run_tests.py` is isolated and offline by default. It runs the cross-platform
  safe gate plus `test_mac_platform.py`; use `--all` for slow hardware and
  integration scripts.
- `sync_macos_shared_contracts.py --check` verifies the curated shared
  dictation, insertion, Mumble Find, Meetings, History, Stats, and Web UI
  contracts. Its deterministic text transform keeps macOS shortcut, browser,
  startup, and data-folder copy native.
- `test_issue26_macos_parity.py` uses injected native doubles for Spotlight,
  pasteboard ownership, permission revocation/recovery, honest input outcomes,
  restart recovery without reinsertion, and Apple Silicon/Intel source seams.
- Windows-only DirectML, PowerShell installer, Win32 overlay, and `.bat`
  updater assertions are intentionally not macOS gates.
- Native release sign-off still requires real macOS for Microphone,
  Accessibility/Input Monitoring, LaunchAgent, codesign/notarisation, update
  relaunch/rollback, native drag, focus/window behaviour, audio devices, and
  global-hotkey checks. Source-level green evidence does not satisfy these.

Run:

```bash
python run_tests.py
python run_tests.py --all       # optional slow/native integration coverage
```
