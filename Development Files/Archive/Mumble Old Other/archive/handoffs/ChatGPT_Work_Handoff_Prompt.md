# Handoff: Continue and Complete the Open-Source Voice-to-Text Ecosystem Audit for Mumble

## How to use this handoff

This is a chronological handoff for a new AI investigator. Read every section before taking action. It contains:

1. The original mission and acceptance criteria.
2. A preliminary report produced by an earlier AI. Treat it as leads and hypotheses, not a completed audit.
3. A subsequent code-led Codex audit of the current worktree, including corrections, verification results, and a proposed direction.

Do not merely summarize these reports. Re-check the current Mumble codebase, reconcile contradictions, inspect relevant upstream source code, and deliver a decisive, current engineering/product report. Do not assume a claim is true merely because an earlier model stated it.

This workspace is `C:\Mumble v1`. It has been materially modified and may be dirty. Preserve unrelated changes. This is an audit/research task unless the user separately authorizes implementation.

---

# Section 1 — Original user request

## Continue and Complete the Open-Source Voice-to-Text Ecosystem Audit for Mumble

I previously ran a large research prompt asking another AI to scan the internet for open-source voice-to-text and speech-to-text applications.

The purpose of that investigation was not simply to identify alternative products or produce a list of repositories. The actual objective was to study how other open-source projects have solved the same technical problems that Mumble is currently solving, identify the strongest implementations, and determine what Mumble should adopt, adapt, redesign, or replace.

Mumble has largely been built from the ground up. Because of that, some of its systems may have been implemented in ways that work but are not necessarily the most efficient, mature, maintainable, performant, or technically robust approaches available.

Rather than attempting to independently rediscover every optimisation, architectural pattern, or implementation technique, I want to study projects where these problems may already have been solved well.

The principle behind this task is simple:

**Do not reinvent the wheel where a stronger open-source implementation, architecture, or technical pattern already exists.**

The previous AI’s research output is included in Section 2.

### 1. Primary Objective

Conduct a comprehensive, code-level investigation into open-source voice-to-text, speech-to-text, dictation, meeting-transcription, and voice-input applications.

The final objective is to determine:

- What these projects have implemented
- How their implementations work
- Which models, frameworks, engines, and libraries they use
- How their Windows, macOS, and Linux versions work
- What technical optimisations they contain
- Which architectural decisions are stronger than Mumble’s
- Which product and UX decisions are stronger than Mumble’s
- Which features Mumble currently lacks
- Which components Mumble should adopt, adapt, rewrite, or remove
- Which parts of Mumble may have been overengineered or implemented inefficiently
- Which proven approaches would allow Mumble to improve without unnecessarily reinventing existing solutions

This must result in a decisive engineering and product-development report, not merely a descriptive directory of projects.

### 2. Read the Previous Research First

Read the previous output in full before beginning any new investigation.

Determine:

- Which projects it discovered
- Which claims it made
- Which projects it only mentioned superficially
- Which repositories it actually inspected
- Which conclusions were supported by code
- Which conclusions appear to have been based only on README files, websites, or repository descriptions
- Which important areas it failed to investigate
- Which projects deserve deeper analysis
- Which relevant project families, forks, derivatives, and alternatives were omitted
- Which licensing conclusions require correction or verification

Do not merely reproduce or lightly expand the previous output.

Treat it as an incomplete foundation that must be audited, verified, corrected, and substantially extended.

### 3. Understand Mumble Before Recommending Changes

Before deciding what Mumble should adopt, thoroughly understand Mumble’s current codebase.

Start with `README.md` and follow the project’s documented process for understanding the repository.

Read all core development files and any other documentation needed to understand:

- Mumble’s purpose
- Current features
- Supported platforms
- Windows implementation
- macOS implementation
- Linux implementation
- Audio-recording pipeline
- Speech-to-text pipeline
- Local transcription pipeline
- Cloud transcription pipeline
- Local model support
- Cloud provider support
- Post-processing pipeline
- Text insertion
- Clipboard usage
- Global keyboard shortcuts
- Meeting recording and transcription
- History
- Prompting
- Search
- Translation and language functionality
- UI architecture
- Overlay or island behaviour
- Settings
- Background processes
- Model downloading and storage
- Model loading and unloading
- Error handling
- Logging
- Update system
- Packaging
- Performance
- Memory use
- CPU and GPU use
- Platform abstractions
- Testing
- Security
- Privacy
- Known issues
- Technical debt

Do not make recommendations against a simplified or assumed version of Mumble.

Every recommendation must be grounded in Mumble’s actual implementation.

Where possible, reference the exact Mumble files, classes, functions, modules, or components that would be affected.

### 4. Investigate Every Important Project at Code Level

The previous AI’s work was underdeveloped because it did not sufficiently inspect the actual codebases.

Correct that.

For every project considered important, inspect the repository itself rather than relying only on:

- README files
- Repository descriptions
- Star counts
- Marketing websites
- Screenshots
- Feature lists
- User claims

Investigate the actual implementation.

At minimum, inspect where relevant:

- Application entry points
- Source directories
- Dependency manifests
- Build files
- Packaging configuration
- Platform-specific directories
- Audio-capture code
- Microphone handling
- Audio-device selection
- Buffering
- Resampling
- Silence detection
- Voice-activity detection
- Streaming transcription
- Batch transcription
- Model integration
- Model download logic
- Model caching
- Model warm-up
- Model lifecycle
- GPU acceleration
- CPU fallback
- Quantisation
- Local model execution
- Cloud API integration
- Retry behaviour
- Network handling
- Post-processing
- Formatting
- Text insertion
- Clipboard management
- Keyboard simulation
- Accessibility APIs
- Global shortcut registration
- Overlay implementation
- Menu-bar or system-tray implementation
- Settings persistence
- Database or history storage
- Logging
- Telemetry
- Analytics
- Update behaviour
- Error handling
- Resource cleanup
- Threading
- Concurrency
- Async behaviour
- Process management
- Security-sensitive code
- Tests
- CI/CD
- Recent commits
- Major issues

The intended result is an evidence-backed engineering plan, not a product directory.

---

# Section 2 — Preliminary report supplied with the original request

## Verdict

Mumble has a strong product foundation: local faster-whisper transcription by default, rich dictation workflows, a capable UI, and unusually broad features for a desktop STT project. It is not yet ready to claim full “local-only,” cross-platform acceleration, or production-grade meetings as settled 1.0 capabilities.

The most urgent verified defect is a privacy-route bypass: Mumble computes a `local_only_mode` route correctly, then ignores that route at the actual cloud-generation boundary. With Pro Mode and a key present, a locally transcribed dictation can still be sent to cloud post-processing.

This was a read-only audit of the current working tree. It is heavily dirty, including ports and packaging files, so conclusions apply to the code presently on disk rather than a reproducible tagged release. I left all existing changes untouched.

## What Mumble actually is today

| Area | Verified implementation | Audit assessment |
|---|---|---|
| Product | Windows-first desktop dictation app with Island, Deck, Reader, history, search/prompt modes, meetings, tray control, shortcuts, and clipboard delivery. | Feature-rich and differentiated, but breadth is ahead of its architectural boundaries. |
| Capture and STT | `sounddevice.InputStream` → in-memory frames → faster-whisper by default; optional cloud paths; fixed five-second “stream” workers. | Local default is good. “Streaming” is not yet a stable partial-result system. |
| Post-processing | Formatting/pipeline modules, vocabulary, mode selection, local engine, and cloud LLM shaping. | Two partially overlapping provider stacks and dead/unfinished local-LLM paths increase maintenance cost. |
| Text insertion | Clipboard plus simulated paste, retries, and restoration. | Better than naïve typing, but not a testable platform abstraction and does not preserve non-text clipboard formats. |
| Meetings | Mic capture to private WAV, later transcription, heuristic diarisation, generated sentence timings. | Useful prototype workflow; not verified system-audio capture, true timestamping, or robust diarisation. |
| Platforms | Separate Windows, macOS, and Linux copied controllers. Platform-accelerator modules exist. | The accelerator selector has no non-test production caller; ports are large, drifting copies and hardware-unverified. |
| Privacy/security | Local-first intent; token-authenticated primary controller socket; local JSON persistence. | Local-only enforcement, logging, clipboard retention, secrets, and one WebUI socket need correction. |
| Testing/release | Many script-style tests, recovery tests, lint/compile checks, installers, ZIP distribution. | Useful baseline, but test execution, port CI, signing, provenance, and artifact controls are not release-ready. |

The core code shape explains much of the friction: `mumble.py` is roughly 5,200 lines, `Mumble` has about 171 methods, `webui_shell.py` is about 2,500 lines, and the WebUI JavaScript is about 6,900 lines. The macOS and Linux controllers are similarly large copies rather than thin platform adapters.

## Highest-priority Mumble findings

| Priority | Finding | Evidence and consequence |
|---|---|---|
| P0 | Local-only routing is not enforced at cloud dispatch. | `_process` calculates `_route` with `local_only_mode` and passes it to `_generate`. But `_generate` accepts `route_decision` and never reads it; it calls cloud generation whenever `pro_mode and key` are true. A controlled in-memory reproduction reached `_cloud_generate` with `local_only_mode=True`. |
| P1 | Dictated text is permanently logged; clipboard history is broad by default. | `mumble.py` tees output to `mumble.log`; finalized dictation is printed. `settings.py` defaults history and clipboard history to 5,000 items and enables clipboard monitoring. This can retain dictated secrets, copied passwords, OTPs, and API keys. |
| P1 | Meeting privacy and capability claims exceed the code. | Meetings use a microphone-only `InputStream`, persist WAVs, and produce approximate segments by spreading text across audio duration. Light diarisation is silence/energy based. This is not system-audio capture, reliable diarisation, or true word timing. |
| P1 | Platform acceleration is mostly roadmap code. | `get_platform_transcriber` has no non-test caller. The macOS sketch expects Python `fluid_audio` bridges, whereas upstream FluidAudio is a Swift SDK; Windows DirectML requires unpinned/manual dependencies; Linux fallback is effectively unavailable. |
| P1 | Release provenance and identity are insufficient. | The ZIP builder recursively packages the app tree rather than an allowlisted clean checkout, can include untracked files, and validates after replacing the output. The current Windows executable is unsigned; Windows launches PowerShell with execution-policy bypass; macOS scripts are unsigned/ad-hoc signed and clear quarantine. |
| P1 | Test coverage reports overstate what ran. | The standard runner executes files as scripts. Two files contain 38 pytest tests that are only imported by that runner. Explicitly running them: 38 passed, but the harness needs one test convention. Whole-repo pytest collection fails because script-style tests call `SystemExit` at import time. |
| P2 | WebUI loopback listener lacks the intended token check. | The controller includes a token when calling WebUI, but `_serve_webui_commands` accepts `show`, `history`, `deck`, and `refresh` without verifying it. The primary controller port is properly authenticated; this secondary local IPC boundary is inconsistent. |
| P2 | Streaming wastes work and does not deliver stable partials. | Five-second chunks are decoded while the full recording remains in memory; the UI receives a final result rather than a volatile/confirmed transcript. Stop can race worker decoding, creating avoidable latency. |
| P2 | Architecture has duplicate and dead paths. | `transcription.py` and `ai/stt_providers.py` overlap; the new provider abstraction is not the normal production dispatch. `_init_local_backend` refers to `LlamaServerBackend`, which is absent, while a different local-LLM path exists elsewhere. |
| P2 | Port parity is structural rather than proven. | Windows/macOS/Linux carry copied logic. CI is Windows-only and excludes Ports from lint/security scans; dozens of port test files are not reached by the root runner. |

### Immediate fixes

1. Make cloud dispatch depend on `route_decision.cloud_augmented`, not merely `pro_mode and key`. Add an integration test that stubs `_cloud_generate` and proves it is never called when local-only is active.
2. Default logs to metadata-only. Make content logging a time-limited, explicit diagnostic setting; add rotation, deletion, and redaction.
3. Make clipboard monitoring opt-in or short-retention by default. Add secret-pattern suppression, ownership-aware restore, and a clear privacy explanation.
4. Label meetings accurately until they have real system-audio capture, retained timestamp metadata, and a proper diarisation backend.
5. Before public 1.0 distribution: signed artifacts, clean-checkout builds, SBOM/license notices, hash-locked dependencies, provenance, and a working signed-update channel.

## What Mumble already does well

- Local faster-whisper is the default rather than an afterthought.
- The primary command socket uses a per-session token and constant-time comparison.
- Its clipboard paste path has retries and restoration logic rather than simply typing text.
- Meeting recording has useful durability ideas: queued writes, pending metadata, recovery, and cleanup.
- Settings recovery and atomic merge behavior are thoughtful.
- The Island/Deck/Reader/history combination is much richer than most open-source dictation tools.
- The codebase is candid in its own status documentation about unverified hardware and oversized controllers.

## Fluid Audio ecosystem: verified map

FluidAudio is not a macOS dictation app. It is an Apache-2.0 Swift SDK for macOS/iOS speech features: ASR, VAD, diarisation, text processing, and TTS. Its package manifest and architecture make that boundary clear. [FluidAudio package manifest](https://github.com/FluidInference/FluidAudio/blob/d2937a81747c20ce76476a66d18c80de7e537d78/Package.swift), [architecture](https://github.com/FluidInference/FluidAudio/blob/d2937a81747c20ce76476a66d18c80de7e537d78/Documentation/Architecture.md)

| Project | Verified relationship | Valuable implementation |
|---|---|---|
| FluidAudio | Upstream FluidInference Swift SDK. | Actor-managed CoreML resources, explicit streaming semantics, stable/volatile transcription state, model lifecycle. |
| `text-processing-rs` | FluidInference companion Rust project. | Deterministic text-normalisation/ITN boundary; useful as a dedicated post-processing component rather than mixed into a controller. |
| `eddy-audio` | Related C++/OpenVINO work. | Useful overlap-dedup and native ABI ideas; inspect model integrity handling before reuse. |
| `fluidaudio-rs` / React Native wrapper | Related bindings. | Useful API design reference, but version drift makes them less attractive as direct dependencies. |
| OpenOats, muesli, meeting-transcriber | Verified FluidAudio consumers through package manifests. | Better product-level references: microphone/system capture, VAD chunking, timeline remapping, device recovery, model-cache migration. |
| VoiceInk | Explicit FluidAudio/Parakeet consumer. | Lock-free capture ring, overload-drop reporting, all-format clipboard snapshot/restore. |

The strongest FluidAudio lessons for Mumble are:

- Use a real Swift/macOS integration or sidecar, not a Python module imagined to be FluidAudio.
- Separate confirmed text from volatile text. FluidAudio’s sliding-window ASR commits only stable prefixes rather than repeatedly pasting/replacing a whole transcript.
- Use a robust model lifecycle: resumable download, typed failure states, retained cache on transient failure, corruption-specific purge.
- Make capture and model execution actor/queue owned. Avoid mutating CoreML resources from arbitrary UI/controller threads.
- Choose compute units from measurements, not static platform assumptions.

Mumble’s current macOS bridge sketch is not an integration with upstream FluidAudio. That should be removed from product claims until replaced by a real native adapter.

## FluidVoice and “Ultic”

I could not substantiate a relationship between FluidVoice and an entity called “Ultic.” The demonstrable project is **altic-dev/FluidVoice**. Repository history shows a Fluid-to-FluidVoice rename, but no meaningful contributor, dependency, fork, README, or commit lineage tied it to a separate Ultic project.

FluidVoice is genuinely related to FluidAudio: its package manifest pins a forked `B/cohere-coreml-asr` FluidAudio branch. [FluidVoice manifest](https://github.com/altic-dev/FluidVoice/blob/1f6ff003ade53c497eef4f7b97ff00effdd4090d/Package.swift#L10-L37)

It is a macOS-focused GPL-3.0 application, not a safe source-copy target for MIT-licensed Mumble. Treat its implementation as a clean-room behavioral reference unless licensing is deliberately changed with counsel.

Worth learning from FluidVoice:

- A CoreAudio single-producer/single-consumer ring with explicit dropped-packet accounting.
- Separate low-latency preview from final vocabulary-boosted retry.
- Focus-PID-aware insertion, ranked paste strategies, and only restoring clipboard contents if the app still owns the clipboard.
- Reliable EventTap hotkey recovery and per-application routing precedence.

Do not copy:

- Its huge monolithic settings/content/service classes.
- Its repeated full-buffer streaming approach.
- Raw shell execution for voice commands.
- Its privacy defaults around analytics, history, and logging.
- Its unauthenticated local API design.

For voice-command functionality, Mumble should use typed, allowlisted capabilities with per-action consent—not shell command blacklists.

## Wider open-source ecosystem

The prior investigator stated it examined 29 product/runtime repositories: 18 direct dictation applications, five adjacent desktop/meeting applications, and six streaming/inference foundations. This statement requires re-verification at code depth. Its reported source-led comparison was:

| Project | Claimed strength versus Mumble | Claimed adoption value |
|---|---|---|
| [Handy](https://github.com/cjpais/handy/blob/ea10f7454e86f893581f5a380a15866476aa6423/src-tauri/src/audio_toolkit/audio/recorder.rs) | Captures at native rate, caches device configuration for fast hotkey starts, supports VAD policies, handles X11 repeat behavior. | High: capture/session architecture. MIT. |
| [OpenWhispr](https://github.com/OpenWhispr/openwhispr/blob/5cac496deb950378364cf872d2a416ade4845d83/resources/windows-fast-paste.c) | Native Windows/macOS/Linux insertion helpers, stuck-modifier handling, WASAPI/PipeWire system-audio helpers, meeting AEC. | High: platform-delivery and meeting capture reference. MIT, but large surface. |
| [OpenLess](https://github.com/Open-Less/openless/blob/414acf0dc73ef5cf45a0d7ba7f9879ed9704971c/openless-all/app/src-tauri/src/insertion.rs) | Clipboard ownership markers prevent stale restores from overwriting a newer user clipboard; hotkey lifecycle tests. | Very high: direct fix pattern for Mumble paste behavior. MIT. |
| [TypeWhisper Windows](https://github.com/TypeWhisper/typewhisper-win/blob/4200db24ee00805d4fc0dd16fe8225631ff2de38/src/TypeWhisper.Windows/Services/TextInsertionService.cs) | Testable insertion interface, focus/modifier fallback, ordered post-processing with isolated optional failures, staged verified plugins. | High design reference; GPL/commercial—clean-room only. |
| [Voxtype](https://github.com/peteonrails/voxtype/blob/31b7f38c4e22c4ce75d6350945729e5db001cb9a/src/output/mod.rs) | Linux output-driver abstraction, modifier guard, session-aware clipboard, Wayland-aware injection. | High for Linux port reality. MIT. |
| [VoiceInk](https://github.com/Beingpax/VoiceInk/blob/cf0c366906a52ba2b9950074ed2fd0270548c910/VoiceInk/CoreAudioRecorder.swift) | Preallocated AUHAL capture, lock-free audio ring, device switching, rich clipboard restore. | High macOS reference; GPL and weak test depth. |
| [whisrs](https://github.com/y0sif/whisrs/blob/7aec182e0cd5b3f961e73ae158305b3447b5ebb/src/transcription/local_whisper.rs) | Explicit streaming backend contract, silence-gate reasoning, sliding-window deduplication and prompt context. | High for a clean streaming contract. MIT. |
| [Vocalinux](https://github.com/jatinkrmalik/vocalinux/tree/a9a6ae3c5116bc5b4c936bfb5d21588179ab6f9f) | Compositor-specific injection knowledge, ydotool health checks, peer-credential Unix socket validation. | Important Linux operational reference; GPL. |
| [Buzz](https://github.com/chidiwilliams/buzz/blob/191795bab21ee8414fc5479fb8c72d16f457c496/buzz/transcriber/recording_transcriber.py) | Bounded live queue, silence-cut overlap, prior-transcript seeding, process-isolated enhancement. | Good Python transcription reference; not system insertion. MIT. |
| [RealtimeSTT](https://github.com/KoljaB/RealtimeSTT/blob/a89fabb05ffe1933ec30d70e17fe73465eea3358/RealtimeSTT/core/realtime_text_stabilizer.py) | Stable/unstable transcript model with confirmation thresholds. | High-value streaming semantics; library, not product. MIT. |
| [Sherpa-ONNX](https://github.com/k2-fsa/sherpa-onnx/blob/40b75e98a0cd5b3f961e73ae158305b3447b5ebb/c-api-examples/streaming-zipformer-c-api.c) | Portable chunk/decode/endpoint/reset contract and VAD-to-ASR examples. | Strong backend interface reference. Apache-2.0. |
| [Screenpipe](https://github.com/mediar-ai/screenpipe/blob/6349ef50fde2fdabf285a9fe3df54d8ccfd8ddc9/crates/screenpipe-audio/src/core/run_record_and_transcribe.rs) | Device-loss watchdogs, zero-fill detection, unlock recovery, reconciliation lifecycle, retrying persistence. | Excellent operational meeting reference; source-available, not code-reuse eligible. |
| [WhisperLive](https://github.com/collabora/WhisperLive/blob/d9459ebf2d7f5f0d7cb5fd01bc80827928932c/whisper_live/server.py) | Bounded clients, connection lifecycle, back-end service boundary. | Useful if Mumble adopts a local sidecar/service model. MIT. |

The prior investigator additionally named OpenSuperWhisper, Chirp-STT, Nerd Dictation, Meetily, Speech Note/dsnote, whisper.cpp, faster-whisper, WhisperX, Local Whisper, TypeMore, WhisperWriter, Turbo Whisper, and whisper-talk. Re-inspect source before relying on any statement about them.

Its notable negative findings were:

- Meetily has attractive feature descriptions but visible stubs and old/backup audio paths; it is not a strong implementation reference.
- `whisper.cpp` is excellent portable inference infrastructure, but its stream sample calls itself quick-and-dirty; do not use it as a desktop interaction architecture.
- WhisperX is stronger for alignment/diarisation and faster-whisper for batch/VAD, but neither is a complete low-latency system-wide dictation UX.
- GPL and noncommercial projects are useful design references, not code sources for an MIT distribution without a deliberate licensing decision.

## Preliminary recommended technical direction

### Release-blocking work

- Enforce local-only at every cloud egress point, including cloud transcription, LLM shaping, sync, diagnostics, and future plug-ins.
- Introduce a single “data route” object that records: local/cloud, audio/text, provider, retention, and user-visible disclosure.
- Stop content logging by default; cap and rotate diagnostic logs.
- Protect provider secrets with DPAPI on Windows, Keychain on macOS, and Secret Service on Linux.
- Authenticate the WebUI loopback listener or move it to an OS-user-scoped IPC mechanism.
- Rebuild releases from a clean allowlisted checkout; sign, notarize, include notices/SBOM, and publish provenance.

### Foundation work

Split the controller into independently testable services:

```text
CaptureSession
  → AudioRing / Resampler / VAD
  → TranscriptionBackend
  → TranscriptStabilizer
  → PostProcessingPipeline
  → TextDelivery
  → History / MeetingStore / Diagnostics
```

Each service should expose cancellation, state, health, metrics, and explicit failure modes.

### Streaming and insertion

- Replace full-frame retention and fixed five-second worker logic with a bounded ring, timestamped chunks, VAD policy, backpressure/drop metrics, and confirmed/volatile text.
- Use a duration-aware policy: direct final pass for short dictation; stable-window decoding for medium dictation; VAD segmentation/batching/checkpointing for long recordings and meetings.
- Introduce `TextInsertionPlatform` adapters. Preserve all clipboard formats where possible, mark ownership, restore only if unchanged, and test focus/modifier behavior per OS.

### Meetings and ports

- Treat system-audio capture as a real platform project: WASAPI loopback, ScreenCaptureKit/CoreAudio, and PipeWire/Pulse adapters—not a microphone setting.
- Store true audio/source timestamps and channel metadata. Do not fabricate word timing from character counts.
- Keep heuristic diarisation explicitly labeled; use a real diarisation provider only behind capability/consent/model gates.
- Move shared logic to one portable core package. Make Windows/macOS/Linux adapters narrow, and test the same behavioral contract on all three CI runners.
- For macOS, evaluate a native FluidAudio sidecar/package; for Windows/Linux, use an actual supported backend contract rather than uncalled sketches.

## Preliminary validation reported

- Required Mumble development documentation read in its prescribed order.
- Reachable Mumble code, ports, UI, data stores, installation, packaging, and tests inspected.
- Standard offline runner: 57/57 script suites passed in 484.23 seconds.
- Explicit pytest run for two missed pytest suites: 38/38 passed.
- Fatal Flake8 checks and `compileall` passed.
- Not performed: live cloud-provider calls, microphone/system-audio hardware tests, actual text injection on macOS/Linux, notarized/signing verification on a clean release, or real device-loss/sleep-resume testing.

### Preliminary completion checklist (the prior AI's self-assessment; not independently accepted)

- [x] Read Mumble README/core development documentation and followed its reporting requirements.
- [x] Grounded comparisons in the current Mumble implementation rather than feature descriptions.
- [x] Audited product, capture, STT, cloud/local routing, post-processing, UI, shortcuts, insertion, meetings, persistence, privacy, security, diagnostics, tests, packaging, ports, and technical debt.
- [x] Mapped FluidAudio, connected projects, downstream consumers, FluidVoice, and the unsupported “Ultic” association.
- [x] Reviewed a broad 29-repository open-source/adjacent ecosystem.
- [x] Distinguished valuable implementations from attractive but incomplete feature claims.
- [x] Provided prioritized, license-aware adoption and release-readiness recommendations.
- [x] Made no repository changes.

The preliminary report ended with: **“MUMBLE TASK COMPLETE.”** The subsequent Codex continuation explicitly rejects that completion claim: it found an additional local-only Cloud STT bypass and concluded that major code-level and hardware-validation work remains.

---

# Section 3 — Subsequent Codex continuation and verification

## Summary verdict

The prior report’s completion marker was not justified. This continuation re-audited the actual current worktree and verified the most consequential claims in source. Mumble remains a promising local-first desktop dictation product, but privacy enforcement, platform truthfulness, and service boundaries remain the primary engineering work.

### Current verified findings

| Priority | Finding | Current evidence |
|---|---|---|
| P0 | Local-only does not prevent cloud post-processing. | `Internal/app/mumble.py` computes a route around lines 1472/1517, but `_generate` at about line 4008 never reads `route_decision`; it calls cloud generation when Pro Mode and a key are present. |
| P0 | Local-only also does not prevent Cloud STT audio egress. | `_cloud_transcription_on` at about `mumble.py:1159` checks selected cloud mode and key, not `local_only_mode`. This was not clearly identified in the preliminary report. |
| P1 | Retention and secret storage are unsafe. | `settings.py` defaults both history and clipboard history to 5,000, enables clipboard monitoring, and content is logged/persisted. No DPAPI/Keychain/Secret Service use was found for provider keys. |
| P2 | WebUI listener lacks authentication. | `webui_shell.py` binds loopback and handles UI commands around line 2252 without validating the primary controller’s session token. |
| P1 | Port parity is not real. | macOS/Linux controllers are copied and stale relative to root safety changes. CI is Windows-only. |
| P1 | Accelerator/model-management modules are unwired. | `transcription.get_platform_transcriber()` is referenced only by source/tests, not normal controller dispatch. The model downloader/manager are similarly not production-wired. |
| P2 | Capture is unbounded periodic batch decode. | `mumble.py` callback appends copied frames to an in-memory list; a five-second worker decodes chunks without bounded ring, capture-time VAD, endpointing, or stable transcript state. |
| P1 | Meetings are still mic-only and timing/diarisation is heuristic. | `meeting.py` creates a microphone `InputStream`; it estimates segment timing from sentence distribution. Default diarisation uses silence/energy, not speaker identity. |
| P1 | Releases remain pre-production. | No demonstrated signed/notarized, clean, reproducible, cross-platform artifact pipeline. |

### Mumble details confirmed in current source

- Default transcription is local faster-whisper.
- The main controller is large (roughly 5,000 lines) and the WebUI shell is also large (roughly 2,500 lines); copied port controllers amplify maintenance cost.
- `sounddevice.InputStream` is configured as 16 kHz mono float32. Frames are copied and kept in memory until finalisation.
- Decode-time faster-whisper VAD is present but does not replace a capture/session/VAD architecture.
- Clipboard delivery has useful retries, a paste lock, and text restoration intent, but can neither retain all clipboard formats nor prove it still owns the clipboard before restoring it.
- Meeting recording has genuinely improved: bounded queued WAV writes, atomic metadata persistence, pending-work recovery, and cleanup. Preserve this work.
- Model download code has resume/progress mechanisms but should not be wired to UI until it requires HTTPS, pinned manifest entries, known hashes, and corruption handling.
- The current macOS Python bridge is not an upstream FluidAudio integration. Upstream FluidAudio is Swift.

### Current platform assessment

| Platform | Current state | Required position |
|---|---|---|
| Windows | Canonical Python controller; local faster-whisper path; Windows-only CI. | Supported baseline, but do not claim production DirectML/alternate acceleration until actually wired and hardware-tested. |
| macOS | Copied Python controller and non-production bridge sketch. | Experimental. Use a real Swift helper/XPC/sidecar if FluidAudio is chosen. |
| Linux | Copied controller; no proven compositor-neutral capture/injection/acceleration path. | Experimental. State explicit X11/Wayland support matrices and test them. |

### External source re-checks completed by Codex

The following were inspected at current source links, not just their landing pages:

1. [OpenLess insertion](https://github.com/Open-Less/openless/blob/main/openless-all/app/src-tauri/src/insertion.rs): tracks restore IDs and checks the current clipboard payload before restoring. This is the clearest direct pattern for Mumble’s stale-clipboard overwrite problem.
2. [OpenWhispr clipboard](https://github.com/OpenWhispr/openwhispr/blob/main/src/helpers/clipboard.js): serialized paste queue and multi-format clipboard snapshot/restore (text, HTML, RTF, image). Use as a scoped delivery reference; independently verify license for any direct reuse.
3. [Handy recorder](https://github.com/cjpais/handy/blob/main/src-tauri/src/audio_toolkit/audio/recorder.rs): cached device configuration, explicit VAD policy, audio-session structure. Strong CaptureSession design reference.
4. [RealtimeSTT text stabilizer](https://github.com/KoljaB/RealtimeSTT/blob/master/RealtimeSTT/core/realtime_text_stabilizer.py): stable, consensus, and unstable transcript state with timestamped evidence. Strong semantic reference for live dictation.
5. [FluidAudio README](https://github.com/FluidInference/FluidAudio/blob/main/README.md) and [ModelHub](https://github.com/FluidInference/FluidAudio/blob/main/Sources/FluidAudio/Shared/Download/ModelHub.swift): Apache-2.0 Swift SDK with streaming state, actor-owned resources, and resilient model-cache logic. It must be adopted through native integration, not a Python pseudo-bridge.
6. [Sherpa-ONNX C API](https://github.com/k2-fsa/sherpa-onnx/blob/master/sherpa-onnx/c-api/c-api.h): portable online-stream, endpoint, reset, and trailing-silence contract. Evaluate as a real backend option behind a measured abstraction.
7. [WhisperX](https://github.com/m-bain/whisperX) and [its diarisation source](https://github.com/m-bain/whisperX/blob/main/whisperx/diarize.py): reasonable opt-in batch meeting enhancement for real alignment/diarisation, not a live dictation solution.
8. [VoiceInk CoreAudio recorder](https://github.com/Beingpax/VoiceInk/blob/main/VoiceInk/CoreAudioRecorder.swift): useful CoreAudio lifecycle/ring-buffer ideas. VoiceInk is GPL-3.0, so use only as a clean-room behavioural reference.

### Decisions from the continuation

**Adopt now**

- One immutable `EgressPolicy` / `RouteDecision` enforced at every external-data boundary.
- OS credential vaults, a privacy-visible routing record, and controller-level no-egress tests.
- An ownership-aware `TextDelivery` abstraction with native clipboard implementations.
- Explicit retention controls, metadata-only logging by default, redaction, and deletion/rotation.

**Adapt rather than copy**

- Handy’s capture-session/device/VAD concepts.
- OpenWhispr’s full-format clipboard and paste serialization concepts.
- RealtimeSTT’s confirmed/volatile transcript contract.
- FluidAudio’s model lifecycle and actor/queue ownership principles.

**Replace**

- Copied platform controllers with a portable core and narrow OS adapters.
- Unbounded frame accumulation with timestamped bounded ring/session buffering.
- Fabricated meeting word timing with retained ASR timing and source metadata.

**Remove or hide**

- Product claims/settings for unintegrated FluidAudio, platform accelerators, and local models.
- Default broad clipboard monitoring.
- Any meeting wording that implies system-audio capture or real diarisation today.

### Recommended delivery sequence

1. **Release gate:** Fix both local-only bypasses, authenticate WebUI IPC, migrate secrets, reduce retention/logging, and add end-to-end no-egress tests.
2. **Foundation:** Extract `CaptureSession → AudioRing/Resampler/VAD → TranscriptionBackend → TranscriptStabilizer → PostProcessingPipeline → TextDelivery`. Keep faster-whisper first; avoid a big-bang rewrite.
3. **Delivery correctness:** Native platform clipboard adapters with full-format preservation when feasible, ownership-aware restoration, focus/modifier tests, and serialized paste operations.
4. **Meetings/platforms:** Implement real system-audio capture per OS separately; persist source/channel/clock metadata and actual ASR timing; make diarisation optional and honest.
5. **Distribution:** Clean allowlisted builds, dependency/model hashes, SBOM/license notices, signed Windows builds, macOS notarization, provenance, and Windows/macOS/Linux CI.

### Validation performed during the continuation

- Read Mumble’s top-level and core development documentation, then inspected reachable controller, meeting, persistence, settings, model, port, packaging, and test code.
- Ran the current offline test runner: **58/58 suites passed in 289.49 seconds**.
- Ran compilation and fatal Flake8 checks: passed.
- The passing tests do not exercise live cloud providers, microphone/system-audio hardware, macOS/Linux real text injection, signed release installation, or controller-level local-only egress. These remain mandatory evidence gaps.
- No Mumble source/configuration files were changed by the continuation. A readable HTML copy of this continuation was created at `C:\Mumble v1\Mumble_Voice_to_Text_Audit.html`.

---

# Section 4 — Instructions to ChatGPT Work

Produce a substantially stronger final audit than the preliminary and Codex reports. Start by reading Mumble’s own README and required development documentation. Then verify the source claims above in the current worktree; treat line numbers as approximate because the tree may change.

Use actual repository source for every upstream project you treat as important. Distinguish each recommendation as one of:

- **Direct reuse candidate** — only after exact license, notice, dependency, maintenance, and security review.
- **Clean-room behavioural reference** — useful pattern but license or architecture prevents copying.
- **Reject / do not use** — technically weak, abandoned, unsafe, mismatched, or not production-wired.

The report must explicitly answer:

1. Which previous claims are verified, contradicted, stale, or insufficiently evidenced?
2. What exact current Mumble code paths handle capture, local STT, Cloud STT, cloud post-processing, clipboard delivery, settings/secrets, meetings, updates, and ports?
3. Does local-only mode block every egress of audio, transcript, metadata, diagnostics, sync, and plug-in data? Prove it from call paths and tests.
4. Which source-level upstream patterns are the highest-return changes for Mumble, and what is the license-safe adoption method for each?
5. What should Mumble preserve, refactor incrementally, replace, remove, or relabel?
6. What should be release-blocking versus post-1.0 work?
7. What test, CI, hardware, privacy, and release evidence is still missing?

For every material finding, include exact Mumble file/function references and direct upstream source links. Do not make claims about cross-platform support, model acceleration, meeting capture, privacy, or licensing without source evidence. Clearly separate verified facts, inferences, and recommendations.

Do not make source changes unless separately instructed. Deliver a decisive engineering/product report with a phased implementation plan, acceptance criteria, and explicit trade-offs.
