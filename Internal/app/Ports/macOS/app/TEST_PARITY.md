# Test Suite Parity — macOS Port

Updated 2026-07-12 against the current Windows worktree.

- Windows currently has 61 `test_*.py` scripts; macOS has 45.
- `run_tests.py` is isolated and offline by default. It runs the cross-platform
  safe gate plus `test_mac_platform.py`; use `--all` for slow hardware and
  integration scripts.
- The port now includes the current core-engine, UI, provider, STT/TTS,
  downloader, model-backend, settings-recovery, formatting, and meeting tests.
- Windows-only DirectML, PowerShell installer, Win32 overlay, and `.bat`
  updater assertions are intentionally not macOS gates.
- Native release sign-off still requires real macOS for Microphone,
  Accessibility/Input Monitoring, LaunchAgent, codesign/notarisation, update
  relaunch/rollback, and global-hotkey checks.

Run:

```bash
python run_tests.py
python run_tests.py --all       # optional slow/native integration coverage
```
