# Mumble macOS and Windows deep audit

Date: 12 July 2026
Primary scope: `Internal/app/Ports/macOS`, with shared-core and Windows parity review in `Internal/app`
Method: parallel platform, core-logic, UI/parity, and final integration reviews; static analysis; dependency audit; isolated offline test suites.

## Executive summary

The macOS port was not release-ready at the start of this audit. Its controller could crash before the UI loop while checking Accessibility, its global input stack depended on Darwin-incompatible/root-oriented packages, several current Reader and Meeting safeguards had not reached the port, and UI actions were calling controller commands that did not exist. The installer also had unsafe process-replacement behavior and accepted runtime combinations that could not install the pinned dependencies.

The confirmed software defects found in scope have been remediated. macOS now uses a non-root pynput/Quartz input layer with dead-listener recovery; performs the correct TCC check; authenticates both localhost command channels; bounds dictation, meeting, Reader, archive, and cloud-upload workloads; uses durable Meeting and settings recovery; routes the complete Mac UI contract; and has a native macOS CI/release-build gate. Shared Windows/Mac/Linux logic was also corrected where the defect lived in common code.

This does **not** mean the Mac release is ready to ship publicly. A physical Mac still must validate TCC, Quartz/Input Monitoring, microphone capture, AppKit focus/click-through, launchd, and real multi-monitor geometry. Distribution also remains unsigned, unnotarized, dependent on a separately installed Python, and without a provisioned signed update feed.

## Confirmed findings and disposition

| ID | Severity | Finding | Disposition |
| --- | --- | --- | --- |
| MAC-001 | Critical | Startup called `AXIsProcessTrusted` from AppKit, where it does not exist; the uncaught exception occurred before the main loop. | Fixed: use ApplicationServices, fail safely, prompt before listener creation, and poll for approval. |
| MAC-002 | Critical | The Darwin hotkey layer used `keyboard==0.13.5`, whose macOS backend is unsuitable for an ordinary non-root desktop app; `mouse==0.7.1` was also not a valid Mac foundation. | Fixed: replaced with pinned `pynput==1.8.2` and a shared Quartz keyboard/mouse hub. |
| MAC-003 | High | A failed Quartz event tap could leave pynput's sticky `running` flag true after its listener thread died, permanently disabling hooks. | Fixed: health is based on thread liveness and a watchdog recreates dead listeners. |
| MAC-004 | High | Installer/uninstaller fallback process killing could target unrelated processes sharing a PID file or port. | Fixed: validate process command identity and listening-port ownership; removed broad kill fallback. |
| MAC-005 | High | Installer declared no microphone purpose string and did not preflight Tk/PyObjC frameworks. | Fixed: added `NSMicrophoneUsageDescription` and post-venv framework imports. |
| MAC-006 | High | Installer accepted Python versions outside the tested wheel set. It initially accepted any Python, then 3.14 despite pinned NumPy 2.2.6 lacking a cp314 wheel. | Fixed: explicit Python 3.12–3.13 range with Tk validation and matching documentation/tests. |
| MAC-007 | High | Reinstall silently forced autostart on; launchd state was inferred from a plist rather than the loaded service. | Fixed: preserve the saved preference and reconcile with `launchctl print/bootstrap/bootout`. |
| MAC-008 | High | Local controller and WebUI ports accepted unauthenticated commands from any local process or browser able to reach loopback. | Fixed: rotating per-session token, fail-closed validation, authenticated second-window show signal. |
| MAC-009 | High | The Mac UI emitted `grab_selection`, `capture_conversation`, `focused`, `set_island_mode`, and `meeting_retry`, but the controller returned `unknown cmd`. | Fixed: all five routes implemented; status now returns `active_mode`. Windows received the missing island-mode route too. |
| MAC-010 | High | Dictation had no hard duration bound, allowing an accidental recording to consume memory indefinitely and exceed direct cloud upload limits. | Fixed: exact 10-minute/sample cap, safe worker-thread stop, cloud request guard, sleep/resume reset. |
| MAC-011 | High | Meeting recording/import lacked the current four-hour lifecycle and durable retry behavior. | Fixed: four-hour cap, import preflight, bounded long-form chunks, capture-error preservation, durable processing/retry, UI reset. |
| MAC-012 | High | Meeting and dictation could race to open the microphone while dictation held only a pre-start `busy` reservation. | Fixed: Meeting participates in the same locked ownership gate; dictation cannot re-enter during prior processing. |
| MAC-013 | High | One-second Meeting Start and ten-second Stop RPC timeouts were shorter than real macOS device/TCC startup and bounded writer finalization, producing ghost success/failure states. | Fixed: 15-second start and 20-second stop windows plus UI state reconciliation. |
| MAC-014 | High | Reader storage could replace a corrupt library with an empty one, silently cap the library, and parse unbounded files/ZIP expansion. | Fixed: corrupt-data preservation/recovery, explicit limits, 32 MiB source cap, 600k stored-character cap, 5,000 PDF pages, 20k archive members, 256 MiB expansion cap. |
| MAC-015 | High | Settings recovered from `.bak` in memory but left the corrupt primary file in place, repeating recovery forever. | Fixed in Windows and Mac: atomically heal the primary while preserving the corrupt artifact. |
| CORE-001 | High | HTTP retry/backoff policy existed but `_json_request` bypassed it, so transient 429/5xx failures immediately surfaced. | Fixed in Windows, Mac, and Linux; regression coverage added. |
| CORE-002 | High | Pipeline stages permanently cached a missing model or failed backend construction, so installing/fixing a model required restart. | Fixed in Windows, Mac, and Linux: re-probe missing files, retry construction, clear failure state on unload. |
| CORE-003 | High | Model downloads used the requested revision for the file URL but main-branch metadata for size/hash verification. | Fixed in Windows, Mac, and Linux: revision-pinned metadata URL and tests. |
| CORE-004 | Medium | The configured model-process memory budget was not enforced on macOS; free-memory checks were Windows-only. | Fixed: cross-platform free/RSS measurement and budget-triggered pressure handling. |
| MAC-016 | Medium | Reader TTS used a provider-wide Gemini voice for Voxtral/MAI, causing valid requests to fail. | Fixed: per-model supported voice catalogues/defaults and fallback metadata. |
| MAC-017 | Medium | Cocoa bottom-left coordinates were mixed with Tk top-left coordinates; overlay placement churned native window enumeration and fallback click-through was incomplete. | Fixed: explicit coordinate conversion, active-monitor placement, throttling/frame caching, click-through. |
| MAC-018 | Medium | WebView's fixed 960×1180 design exceeded short Mac work areas; pin UI claimed success even when the platform operation was a no-op. | Fixed: screen-aware clamping and truthful native `on_top` behavior with rollback. |
| MAC-019 | Medium | Reader/Meeting web UI had drifted behind the Windows contract, including processing states, retry/play/export, cache race handling, and truthful Mac copy. | Fixed and covered by JS/API contract tests. |
| MAC-020 | Medium | Manual update check blocked the UI and restart code lacked a module import; legacy Windows autostart cleanup was called on Mac. | Fixed: daemon update check, nonblocking restart, removed Windows-only call. |
| MAC-021 | Medium | macOS had no CI job, release archive validation, dependency audit, or Mac test artifact. | Fixed: ARM `macos-15` offline build/test/lint/security job with zip/checksum artifacts. |

## Major implementation areas

### macOS platform/runtime

- Rebuilt `bindings.py` around pynput/Quartz: tap and hold bindings, non-US layout handling, synthetic paste, X1/X2 mouse buttons, re-arming, dead-thread detection, and watchdog recovery.
- Corrected Accessibility/TCC startup order in `mumble_mac.py` and made permission approval live without a restart.
- Hardened `Install Mumble.command`, `Uninstall Mumble.command`, `autostart.py`, update restart, PID ownership, and Python/Tk/PyObjC preflight.
- Added microphone purpose metadata and preserved disabled autostart across reinstall.

### Controller, Meeting, and recording lifecycle

- Added ten-minute dictation and four-hour Meeting limits, exact final-block trimming, long-form processing chunks, import duration checks, and cloud request caps.
- Eliminated Meeting/dictation microphone ownership races and processing re-entry.
- Preserved contiguous audio on device/writer failure, added durable retry, and reconciled WebUI state for automatic stops and capture errors.
- Completed the Mac controller command surface and added the corresponding Windows island-mode route.

### Reader, storage, AI, and models

- Ported current Reader parsing/storage limits, recovery, and collections behavior to Mac.
- Added atomic settings backup self-healing.
- Enabled transient HTTP retry/backoff across platform copies.
- Made model-stage recovery re-probeable and enforced cross-platform memory budgets.
- Pinned downloader metadata to the requested Hugging Face revision.
- Added correct model-specific OpenRouter TTS voices/defaults.

### UI and security

- Synced Reader and Meetings UI/API behavior, native save/playback, processing/retry states, and Mac-specific copy/shortcuts.
- Authenticated both loopback command services with rotating session tokens.
- Fixed Mac window sizing, pin semantics, focus signals, multi-monitor overlay conversion, and click-through behavior.

## What Windows and macOS still lack

These are product/release gaps, not regressions left unfixed by this audit.

| Capability/gap | Windows | macOS | Recommended next step |
| --- | --- | --- | --- |
| Signed production update channel | Missing: publisher key/feed intentionally disabled. | Missing: publisher key/feed intentionally disabled. | Provision an offline-held signing key, signed manifest feed, rotation/revocation procedure, and release drill. |
| Trusted public distribution | Publisher signing/installer provenance still needs a formal release gate. | No Developer ID signing, hardened runtime, notarization, stapling, universal app, or DMG. | Build a credentialed release pipeline; do not ship the current Mac zip as a consumer release. |
| Self-contained runtime | Installer can fetch Python 3.12 and builds a venv, but is not a single self-contained binary/MSIX. | Requires an existing Python 3.12–3.13 with Tk; no bundled runtime. | Package and sign a fixed runtime; on Mac produce universal2 or separately signed ARM/Intel artifacts. |
| Native acceleration | Optional Windows DirectML path exists, but default STT remains CTranslate2/CPU-oriented and needs hardware qualification. | No MLX/Metal/CoreML STT backend; CPU/int8 only. | Benchmark and integrate a supported native backend with fallback and model-format management. |
| Native/hardware coverage | CI covers offline Windows logic, not microphone/hotkey/overlay hardware permutations. | New ARM Mac CI covers offline/build logic, but not TCC/AppKit/mic/launchd; no Intel runner. | Maintain physical Windows and ARM/Intel Mac smoke matrices with permission-reset and multi-monitor cases. |
| Meeting system audio | Captures selected microphone/room audio; no WASAPI loopback/app-audio capture. | Captures selected microphone/room audio; no ScreenCaptureKit/app-audio capture. | Add explicit, consented system-audio sources with privacy UI and echo/dual-source handling. |
| OCR | Scanned PDFs and clipboard images are not OCR'd. | Same. | Add an opt-in local OCR pipeline with page/image limits and language packs. |
| Default high-quality diarisation | Lightweight heuristic is default; pyannote is optional and needs dependencies, token, and model terms. | Same. | Offer a packaged, licensed local speaker model or a clearly provisioned advanced setup flow. |
| Experimental correction/control/wake word | Present behind experimental gates; not production-qualified. | Not ported. | First security/UX-qualify on Windows, then design platform-native Mac automation and wake-word ownership. |
| Non-text clipboard restoration | Text paste cannot fully snapshot/restore arbitrary image/RTF/file clipboard formats. | Same limitation. | Add platform-native multi-format clipboard snapshots with strict size limits. |
| Platform-core architecture | Large copied platform trees make fixes easy to miss across ports. | The drift found here is a direct symptom. | Extract shared controller/Reader/Meeting/AI code into one package; keep small platform adapters and parity contract tests. |

## Verification

### macOS exact final code

- Authoritative isolated offline-all runner: **47/47 test files passed** in 923.9 seconds.
- Post-review targeted rerun: bindings **59 checks passed**; platform/installer **all green**; Meeting controller **all checks passed**; final UI/recording-limit pytest set **15/15 passed**.
- Reader, Meeting, settings recovery, downloader, pipeline recovery, AI retry, performance, stream-seam, model-memory, and controller regression suites passed in focused runs.
- `compileall`/`py_compile`: clean.
- Flake8 fatal classes `E9,F63,F7,F82`: clean.
- Node syntax check for Mac WebUI: clean.
- Bandit: no high-severity findings; medium findings reviewed (primarily guarded URL opens and executable-bit operations).
- `pip-audit -r requirements.txt`: **No known vulnerabilities found**.
- Mac `.command` and build scripts: `bash -n` clean after final installer edits.
- GitHub Actions workflow: YAML parsed successfully.

### Windows/shared core

- Full isolated Windows snapshot: **62/63 test files passed** in 1,137.7 seconds. The sole failure was the explicitly experimental Computer Control file while its selector schema and tests were changing concurrently elsewhere in the shared worktree.
- Immediate current-file rerun of that sole target: **1/1 test file passed, 72/72 internal tests passed**. Across the completed full snapshot plus the exact failed-target rerun, all 63 runner targets are green; there is deliberately no claim that the first snapshot was atomic across unrelated concurrent edits.
- Focused settings recovery, downloader revision, model backend, pipeline recovery, AI retry, and bug-regression suites passed.
- Flake8 fatal classes and Python compilation are clean.

## Release decision

**Code audit status:** remediated for the confirmed software defects in scope.
**macOS public-release status:** **hold** until physical-Mac validation, Developer ID signing/notarization, packaged runtime, and signed update provisioning are complete.
**Windows release status:** shared fixes and all 63 runner targets are green using the full snapshot plus sole-target final rerun; experimental Computer Control remains non-production and should keep its own security review gate.

## External compatibility references

- Apple microphone purpose key: <https://developer.apple.com/documentation/BundleResources/Information-Property-List/NSMicrophoneUsageDescription>
- pynput package/release metadata: <https://pypi.org/project/pynput/>
- NumPy 2.2.6 artifact metadata: <https://pypi.org/pypi/numpy/2.2.6/json>
- SciPy supported-Python metadata: <https://pypi.org/project/scipy/>
- GitHub hosted runner images: <https://github.com/actions/runner-images>
