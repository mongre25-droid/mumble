# Test Suite Parity — Linux Port

Updated 2026-07-12 against the current Windows worktree.

- Windows currently has 61 `test_*.py` scripts; Linux has 49.
- `run_tests.py` is isolated and offline by default. It runs the cross-platform
  safe gate plus Linux platform/parity regressions; use `--all` for slow
  hardware and integration scripts.
- The port now includes the current core-engine, UI, provider, STT/TTS,
  downloader, model-backend, settings-recovery, formatting, and meeting tests.
- Windows DirectML, PowerShell installer, Win32 overlay, and `.bat` updater
  assertions are intentionally not Linux gates.
- Native release sign-off still requires real X11 and Wayland sessions for
  input permissions, focus/paste, GTK island behavior, login autostart, audio,
  and update relaunch/rollback.

Run:

```bash
python run_tests.py
python run_tests.py --all       # optional slow/native integration coverage
```
