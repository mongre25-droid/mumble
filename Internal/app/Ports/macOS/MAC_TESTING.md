# MacMumble — testing guide

The Issue #26 candidate aligns the maintained macOS source with the accepted
shared Mumble contracts while keeping native macOS input, permissions, index,
drag, focus, window, menu-bar, audio-device, installer, updater, and LaunchAgent
seams. Windows-hosted tests can verify source contracts and injected native
doubles, but **TCC, audio devices, launchd, AppKit, packaging, signing, and
notarisation still need a real Mac or macOS CI**.

## What's in this build
- **Shared contracts:** curated dictation, insertion, Mumble Find, Meetings,
  History, Stats, Reader, and Web UI behaviour is generated from the accepted
  shared source. Native copy remains deterministic and macOS-specific.
- **macOS input fix (critical):** the old `keyboard` backend refuses Darwin hooks
  unless the whole app is root, and the old `mouse` package does not support
  Darwin. Both are replaced by one non-root `pynput`/Quartz listener. It supports
  hotkeys, hold/release bindings, X1/X2 mouse buttons, capture, and synthetic
  paste. Layout-aware ASCII characters win on non-US layouts; Option-produced
  glyphs (for example Option+D → `∂`) fall back to the physical virtual key.
  `test_bindings.py` exercises the Darwin event hub with fake pynput events.
- **Permission authority:** Microphone, Accessibility, and Input Monitoring are
  reported separately as ready, denied, revoked, or unknown. Denial keeps the
  protected action off; a later grant is detected without treating an unavailable
  probe as proof of denial.
- **Durable dictation and insertion:** live audio is written to bounded local
  PCM16 segments. Restart recovery saves one History record and never repeats an
  uncertain insertion. Pasteboard formats are restored only while Mumble still
  owns the same change count, and Quartz submission is never called confirmation.
- **Mumble Find and Web Search:** Ctrl+Option+F opens bounded local Spotlight
  results using opaque result identities. Ctrl+Option+S remains a separate,
  consent-gated online action; Mumble Find cannot invoke it.
- **Installer/runtime hardening:** Python 3.12–3.13, Tk, PyObjC and pynput are
  preflighted; the app bundle declares its microphone purpose; stale PID/port
  state cannot kill an unrelated process; and reinstall preserves an explicitly
  disabled Start at Login setting.
- **LaunchAgent accuracy:** the toggle checks the loaded launchd service (not just
  whether a plist exists) and uses modern `bootstrap`/`bootout` commands.
- **Real resource-saver:** `resource_saver` used to only change the look. It now
  also forces the lightest STT model (`tiny.en`), skips the live streaming worker
  (one full pass at stop), and never warms the AI — a genuine low-power mode.
- **Web-UI perf:** row shimmer hover-only, lighter card blur, single ambient layer,
  GPU-composited sweeps, and a complete `body.saver` override (now also strips
  `filter:`). Paste/History split; History is an in-app tab (flyout removed).

## Keybinds (Mac)
| Action | Mac hotkey | Windows |
|---|---|---|
| Record / dictate | **Ctrl+Option+D** | Ctrl+Win |
| Quick paste | **Ctrl+Option+V** | Ctrl+Alt+V |
| Open History | **Ctrl+Option+H** | Ctrl+Alt+H |
| Mumble Find | **Ctrl+Option+F** | Ctrl+Alt+F |
| Web search | **Ctrl+Option+S** | Ctrl+Alt+S |
| Prompt mode | **Prompt toggle in the island** | Prompt toggle |

Ctrl+Option mirrors Windows' Ctrl+Alt (Option == Alt) and avoids the Cmd+Option
macOS conflicts (Finder/Spotlight search, Hide Others, Move). Old installs migrate
automatically; everything is remappable in Settings.

## Local model
`faster-whisper` `small.en` (CPU+int8) — faster-than-realtime on Apple Silicon, and
the whole streaming pipeline is built on its API. Resource-saver drops to `tiny.en`.

### GPU acceleration (Apple Silicon / Metal)
On Apple Silicon Macs, `faster-whisper` runs on CPU via int8 quantisation and is
already faster-than-realtime for the `small.en` model. Two GPU-accelerated paths
exist for users who need even lower latency or want to run larger models:

- **mlx-whisper** (Apple Silicon only): Uses the MLX framework with Metal
  acceleration. Reported 2-6× faster than CPU faster-whisper. Requires a
  `_local_transcribe` rewrite and `mlx-whisper` package.

- **whisper.cpp** (Apple Silicon + Intel Macs): Cross-platform C++ inference with
  Metal/CoreML backends. Available via `llama-cpp-python` or standalone.

Neither GPU path is integrated into the current macOS port — they are upgrades for
a future release. The CPU pipeline meets the realtime bar on all Apple Silicon
Macs (M1 and newer) and is the tested, supported path for v1.0.

On Intel Macs without Apple Silicon, `small.en` via CPU int8 still runs
faster-than-realtime on recent hardware (2018+). Older Intel Macs should use
`base.en` or `tiny.en` via the Settings model picker.

## Test checklist (run on the Mac)
1. **Install/launch:** use `Install Mumble.command` with Python 3.12–3.13 available.
   Grant Accessibility + Microphone + Input Monitoring when prompted. On a clean
   account, leave the app open while granting Accessibility and confirm hotkeys
   become ready without restarting.
2. **Hotkeys fire** — tap **Ctrl+Option+D** → island shows "Listening". Then
   Ctrl+Option+V / +H / +F / +S, then toggle **Prompt** in the island. Confirm
   +F stays local and +S presents the provider-named consent step.
3. **Record → transcribe → paste** into a text field.
4. **Workflows** — use the sticky Prompt toggle for prompt shaping and Deck for
   Email, List, Search, Reply, and the other explicit actions.
5. **History** — Ctrl+Option+H opens the History tab; quick-paste pastes latest.
6. **Resource Saver** (Settings → toggle): confirm the island/UI go flat AND that
   it reloads as the tiny model (the log prints `loading model 'tiny.en'`), live
   transcription is off, and CPU/battery use drops. Toggle back → restores your
   model.
7. **Settings (web UI)** — rebind a hotkey, change model/provider; labels read
   "Ctrl + Option + …".
8. **Overlay/island** — states, positioning, multi-monitor.

## Verify on-device — `TODO(mac-verify)`
1. **Hotkeys actually fire** *(top priority)*: test Ctrl+Option+D on US and at
   least one non-US layout, then X1/X2 mouse bindings and binding capture. The
   Windows-hosted fake event suite proves state logic, not the real Quartz/TCC
   delivery path.
2. **Insertion truth:** verify editable, read-only, protected, changed-target,
   newer-clipboard, and change-count cases in Notes, Mail, browsers, and chat.
   An unconfirmed Quartz submission must remain labelled unknown/saved.
3. **Window foreground on Ctrl+Option+H** relies on pywebview show/restore; if a
   minimized window only bounces the Dock icon, add an osascript activate.
4. **`boot()` double-run guard** vs WKWebView event order.
5. **Island click-through** — the Tk overlay may not be click-through on macOS
   (needs pyobjc `ignoresMouseEvents`).
6. **Spotlight and drag:** verify bounded cancellation, open, reveal, and a drag
   that begins from a live visible result row. The Windows-host source double is
   not physical AppKit evidence.
7. **Architecture/package:** build and launch separate Apple Silicon and supported
   Intel artifacts before considering a universal package. Source recognition of
   `arm64` and `x86_64` is not an Intel build receipt.

## Remaining Mac release gaps
- **No real-Mac CI lane:** Quartz/TCC, microphone capture, launchd and WKWebView
  regressions can only be caught manually today.
- **No Developer ID signing/notarization or packaged runtime:** the ad-hoc bundle
  still relies on a user-installed Python and network dependency installation.
  A signed, notarized universal app/DMG is required for a consumer-grade release
  and predictable permission identity across upgrades.
- **Signed update channel is not provisioned:** the updater is nonblocking and
  its restart path works, but publisher key + HTTPS feed remain owner-gated.
- **No Metal STT backend:** faster-whisper uses CPU/int8; MLX/whisper.cpp remains
  future work for larger models and lower Apple-Silicon latency.
