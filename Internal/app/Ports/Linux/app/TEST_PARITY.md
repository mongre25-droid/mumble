# Test Suite Parity — Linux Port

Updated 2026-07-29 for Issue #27 from exact accepted baseline `c34e7baa`.

- Linux now carries focused source-contract coverage for durable dictation,
  exact-once text/image insertion, the six-destination shared interface,
  Meetings/Island, Stats, Reader, Settings, Mumble Find, and consent-gated Web
  Search. Shared processing authority remains synchronized with Windows.
- `run_tests.py` is isolated and offline by default. It runs the cross-platform
  safe gate plus Linux platform/parity regressions; use `--all` for slow
  hardware and integration scripts.
- The port now includes the current core-engine, UI, provider, STT/TTS,
  downloader, model-backend, settings-recovery, formatting, and meeting tests.
- Windows DirectML, PowerShell installer, Win32 overlay, and `.bat` updater
  assertions are intentionally not Linux gates.
- Deterministic probes cover GNOME LocalSearch/Tracker, KDE Baloo, degraded
  plocate, the XDG GlobalShortcuts portal, native X11/Wayland clipboard helpers,
  PipeWire/PulseAudio states, and compositor-limited drag alternatives. They do
  not claim that those tools or desktops were physically exercised.
- Native release sign-off still requires real GNOME Wayland, KDE Wayland, a
  representative X11 desktop, supported distributions, and sandboxed packages
  for shortcut approval, focus/paste, text/image clipboard, search freshness,
  GTK island/control rail, tray, autostart, permissions, audio/device changes,
  trusted open/reveal/drag, update relaunch, and rollback.

Run:

```bash
python run_tests.py
python run_tests.py --all       # optional slow/native integration coverage
```
