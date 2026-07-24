# Meeting Recording Tools — Deep Evaluation for Mumble Integration

**Date:** 2026-06-28  
**Context:** Mumble is a local-first Python voice dictation desktop app (Windows/macOS/Linux) using faster-whisper for STT, tkinter UI, Cerebras/OpenAI for cloud AI, and llama-cpp-python for optional local LLM. The goal is to identify the best open-source meeting recording foundation to integrate or learn from.

---

## 1. Meetily — `github.com/Zackriya-Solutions/meetily`

### Architecture & Code Quality
- **Stack:** Rust (46.2%), TypeScript/React (29.7%), C++ (9.9%), Python (3.1%), Tauri desktop shell
- **556 commits**, 13 contributors, 1,458 forks, 12.9k stars
- **11 releases** (latest: v0.4.0, June 5, 2026)
- Code quality is **very high** — active CI/CD, release pipeline with signing, clear separation between backend (Rust audio/transcription), frontend (React), and a `llama-helper` Rust sidecar
- Well-structured PR history with detailed commit messages, comprehensive fixes for audio capture bugs (CPAL streams, WASAPI, Bluetooth resampling, macOS Core Audio echo)
- Has an AGENTS.md for Claude Code workflows

### License
- **MIT** — permissive, no restrictions on embedding

### Maintenance Status
- **Last commit:** June 5, 2026 (3 weeks ago) — actively maintained
- Large community (12.9k stars, 1.4k forks), responsive to issues
- However: **speaker diarization is PRO-only** (not in Community Edition), "planned for mid-June" per README
- The project is split into **Community (free, open-source)** and **PRO (commercial)** editions — PRO codebase is not public
- Screen recording is tracked as a feature request (Issue #414, March 2026)

### System Audio Capture
- **Yes, excellent** — uses CPAL (Rust audio library) with WASAPI loopback on Windows, Core Audio on macOS
- Handles Bluetooth devices, audio resampling, EBU R128 normalization
- Dual capture (mic + system audio) with ffmpeg mixing
- Per-process app capture investigated but not yet shipped
- Windows 11 per-app capture NOT available

### Speaker Diarization
- **PRO only** — not available in Community Edition. Uses "Sortformer" technology per README tags
- Community edition has no diarization at all

### Long-Form Transcription (30-60 min)
- Built for this use case — meeting-length recordings are the primary target
- Uses Parakeet (NVIDIA) for ~4x faster transcription; Whisper fallback
- Chunk-based streaming transcription with silence detection (1.5s silence threshold)
- Transcript pagination and virtualization for performance (100 segments per page)
- Proven at production scale

### Python Integration Difficulty
- **Very difficult** — Rust/Tauri architecture, no Python API or library interface
- Could only be embedded as a subprocess (launch Meetily .exe and capture output)
- Audio capture code is in Rust (CPAL), transcription in C++/Rust (whisper-rs/Parakeet ONNX)
- The 3.1% Python is ancillary scripts only
- No Python SDK, no pip package, no importable modules

### Embeddability
- **Separate process only** — it's a full desktop app with its own UI
- No library mode, no headless mode documented
- Audio capture and transcription are deeply coupled to the Tauri frontend

### Disk/RAM Requirements
- Models: Parakeet ONNX (~50 MB), Whisper (~1-2 GB depending on model)
- PostgreSQL for analytics (heavy)
- ffmpeg required on path
- RAM: moderate (~500 MB - 2 GB during transcription)

### Platform Support
- macOS ✓, Windows ✓
- Linux: partial (build support exists but is secondary; CI has linux builds)
- Linux AppImage bundling was fixed but Linux is clearly second priority

### Verdict for Mumble
**NOT suitable.** Meetily is a polished end-user product, not a library or embeddable component. It's built in Rust/Tauri, so there's no Python integration path. Its best audio capture code is in Rust and would need a full rewrite to Python. The PRO split means key features (diarization) aren't even open-source. However, its architecture _design_ (WASAPI loopback via CPAL, dual-track recording, ffmpeg mixing) is a useful reference pattern.

---

## 2. Ownscribe — `github.com/paberr/ownscribe`

### Architecture & Code Quality
- **Stack:** Python (83.7%), Swift (16.1%), Shell (0.2%)
- **74 commits**, 10 contributors, 13 forks, 78 stars
- **13 releases** (latest: v0.13.0, June 23, 2026 — 5 days ago)
- Code quality is **very high** — clean, well-structured Python CLI using Click framework
- Uses WhisperX (faster-whisper + word timestamps), pyannote.audio for diarization, llama-cpp-python for local LLM summarization
- Swift helper for macOS system audio capture via ScreenCaptureKit (Core Audio Taps)
- Strong CI pipeline, comprehensive tests, uv-based packaging
- Has AGENTS.md and CONTRIBUTING.md

### License
- **MIT** — permissive

### Maintenance Status
- **Last commit:** June 23, 2026 (5 days ago) — very actively maintained
- Rapid development pace: 13 releases since initial commit ~4 months ago
- Single primary author (paberr) with active contributors
- Small but growing community (78 stars)

### System Audio Capture
- **macOS only (14.2+)** — uses ScreenCaptureKit via a Swift helper binary
- `sounddevice` backend available for other platforms but requires external audio source
- Supports system audio + mic dual capture
- No Windows WASAPI support, no Linux PulseAudio/PipeWire capture
- Requires Screen Recording permission on macOS

### Speaker Diarization
- **Yes, via pyannote.audio** — requires HuggingFace token (free)
- Uses `pyannote/speaker-diarization-community-1` model
- MPS (Apple Silicon GPU) acceleration for ~10x speedup
- Configurable min/max speaker counts

### Long-Form Transcription (30-60 min)
- Primary use case — full meeting pipeline
- Uses WhisperX (word-level timestamps, VAD-based segmentation)
- Supports silence auto-stop (default 5 minutes, configurable)
- Built-in local LLM (Phi-4-mini, ~2.4 GB) for summarization
- Multiple output formats (Markdown, JSON)
- "Ask your meetings" feature for searching across transcripts

### Python Integration Difficulty
- **Easy** — this is a Python CLI app
- Published on PyPI as `ownscribe` (`pip install ownscribe` / `uvx ownscribe`)
- Clean module structure under `src/ownscribe/`
- Uses Click CLI framework — subcommands are well-isolated
- Core modules (transcription, diarization, summarization) are Python-callable
- The Swift audio capture helper is a separate binary, but the Python side wraps it cleanly

### Embeddability
- **Good as a library** — can `import ownscribe` and call core functions
- The CLI could be driven as a subprocess too
- Main limitation: system audio capture is macOS-only and requires the Swift helper
- For Windows/Linux, would need to write your own audio capture and feed WAV to ownscribe's transcription pipeline

### Disk/RAM Requirements
- Python 3.12+
- WhisperX model download (~1-3 GB depending on model size)
- pyannote diarization model (~200 MB)
- Phi-4-mini LLM (~2.4 GB) — optional, downloads automatically
- ffmpeg required
- macOS 14.2+ for system audio capture
- RAM: ~4-8 GB during pipeline (WhisperX + pyannote + LLM)

### Platform Support
- macOS ✓ (primary, 14.2+)
- Windows: Python code works, but NO system audio capture (no Swift helper)
- Linux: Python code works, but NO system audio capture
- Could use `sounddevice` for mic-only on other platforms

### Verdict for Mumble
**Strong candidate for the transcription + diarization pipeline.** Ownscribe is a clean Python project that Mumble could import directly. Its WhisperX + pyannote + local LLM pipeline is exactly what Mumble needs for meetings. The main gap is system audio capture on Windows/Linux — the Swift helper is macOS-only. Mumble would need to implement its own WASAPI loopback capture (or adopt TalkTrack's approach) and then feed the WAV to ownscribe's transcription engine.

---

## 3. TalkTrack — `github.com/ObscureAintSecure/TalkTrack`

### Architecture & Code Quality
- **Stack:** Python (99.3%), Batch (0.7%)
- **180 commits**, 3 contributors, 5 forks, 20 stars
- **No releases** — still in active development (no tagged release)
- Code quality is **moderate to good** — functional, well-organized module structure under `app/`
- Uses PyQt6 for GUI, faster-whisper for transcription, pyannote.audio 4.0 for diarization
- WASAPI audio capture via sounddevice + comtypes + pycaw (Windows Core Audio API)
- Has CLAUDE.md for Claude Code workflows, uses uv for packaging
- Issue-tracked workflow (every commit references a GH issue)
- Tests exist (`tests/` directory with pytest)

### License
- **MIT** — permissive

### Maintenance Status
- **Last commit:** June 25, 2026 (3 days ago) — actively maintained
- Single primary developer (ObscureAintSecure)
- Very small community (20 stars, 5 forks)
- Active development: per-app capture for Win11, WASAPI loopback for Win10
- Good commit discipline with issue references

### System Audio Capture
- **Windows only, excellent** — the strongest system audio implementation of all tools evaluated
- **WASAPI loopback** (Windows 10+) — captures all system audio
- **Per-app capture** (Windows 11, Build 22000+) — picks specific apps like Teams/Chrome
- Uses `sounddevice` with WASAPI backend, `comtypes` for COM interop, `pycaw` for audio session enumeration
- Dual-channel recording: microphone + system/app audio captured separately
- Auto-detects call apps (Teams, Zoom, WebEx, Discord) via audio session monitoring
- Handles the `AUDCLNT_STREAMFLAGS_EXCLUDE_FROM_PROCESS_LOOPBACK_CAPTURE` opt-out that Teams/Zoom use
- Debug logging for audio RMS levels every 5 seconds

### Speaker Diarization
- **Two modes:**
  - **Simple** (no setup): Labels "You" vs "Remote" from mic vs system channels
  - **Full** (pyannote.audio): Individual speaker identification via HuggingFace token
- pyannote.audio 4.0 integration
- Speaker name panel for mapping generic labels to real names
- Names saved per recording

### Long-Form Transcription (30-60 min)
- Designed for meeting-length recordings
- Uses faster-whisper (same engine as Mumble!)
- Configurable min recording length (discard short recordings)
- Auto-stop on call app inactivity
- Interactive transcript viewer with click-to-replay
- Export to TXT, SRT, JSON formats

### Python Integration Difficulty
- **Easy** — pure Python, same ecosystem as Mumble (faster-whisper, pyannote)
- Core modules are Python files under `app/`: `audio_capture.py`, `recorder.py`, `transcriber.py`, `diarizer.py`
- Uses PyQt6 (Mumble uses tkinter) — UI is separate from audio logic
- Could import `app.audio.audio_capture` and `app.recording.recorder` directly

### Embeddability
- **Good** — modular Python structure, audio capture is well-separated from UI
- The `app/audio/audio_capture.py` and `app/recording/recorder.py` could be extracted
- However: Windows-only COM/WASAPI dependencies mean no cross-platform reuse
- Would need to write macOS (CoreAudio) and Linux (PulseAudio/PipeWire) equivalents

### Disk/RAM Requirements
- Python 3.10+
- faster-whisper model (same as Mumble — could share models)
- pyannote.audio models (~200 MB)
- PyTorch with optional CUDA (~2 GB if GPU)
- ffmpeg required for MP3 output
- RAM: ~2-4 GB during transcription

### Platform Support
- **Windows only** — uses WASAPI and Windows COM APIs (pywin32, pycaw, comtypes)
- No macOS or Linux support

### Verdict for Mumble
**Best reference for Windows system audio capture.** TalkTrack's WASAPI loopback and per-app capture implementation is the best-documented, most battle-tested Python implementation available. Its dual-channel approach (mic + system audio separate) is ideal for meeting transcription. The code is pure Python and could be adapted into Mumble's audio capture layer. However, it's Windows-only, so Mumble would need different implementations for macOS and Linux. The transcription/diarization pipeline mirrors what Mumble already has.

---

## 4. TranscriptionSuite — `github.com/homelab-00/TranscriptionSuite`

### Architecture & Code Quality
- **Stack:** Python (48%), TypeScript (48%), JavaScript (1.8%), Shell (1.4%)
- **1,229 commits**, 3 contributors, 45 forks, 530 stars
- **41 releases** (latest: v1.3.7, June 27, 2026 — yesterday)
- Code quality is **moderate** — self-described as "vibecoded" by the author (a mechanical engineer learning programming)
- Electron frontend + Python FastAPI/uvicorn backend running in Docker
- Multi-backend STT: Whisper (faster-whisper/whisper.cpp), NVIDIA NeMo, VibeVoice-ASR, SenseVoice (via FunASR, just added)
- Server/client architecture — not a library
- Has CI, tests, Docker builds, package publishing

### License
- **GPLv3+** — copyleft, problematic for embedding in MIT-licensed Mumble
- License was changed FROM MIT to GPLv3+ in February 2026

### Maintenance Status
- **Last commit:** June 28, 2026 (today, 2 hours ago) — extremely active
- Rapid development: 41 releases, frequent feature additions
- Single primary author, vibecoded, but dogfooding guarantees continued maintenance
- Active issue tracker with organized project board

### System Audio Capture
- **Limited** — primarily designed for file upload/recording, not live system audio capture
- Electron dashboard handles mic recording
- Live transcription mode exists but is secondary
- No dedicated system audio loopback capture for meetings
- More of a "transcription lab" than a meeting recorder

### Speaker Diarization
- **Yes, via WhisperX** — wraps pyannote.audio
- Voice fingerprinting across meetings (speaker recognition)
- Detailed documentation on diarization performance (single-threaded bottleneck documented)

### Long-Form Transcription (30-60 min)
- Excellent long-form support — recently fixed 5h+ recording handling
- Chunk-based transcription (10-minute chunks) with timestamp stitching
- Multiple GPU backends: NVIDIA CUDA, Apple Metal (MLX), AMD/Intel Vulkan
- Docker-based server for remote/headless operation

### Python Integration Difficulty
- **Difficult** — server/client architecture, Docker-based
- Python backend is a FastAPI server, not a library
- Could call the API endpoints, but that's a separate process
- GPLv3 license complicates any code reuse

### Embeddability
- **Separate process only** — it's a full-stack app (Electron + FastAPI + Docker)
- No library mode
- Could potentially use the API endpoint approach, but the server runs in Docker

### Disk/RAM Requirements
- Docker for server deployment
- Multiple model backends (heavy): NeMo, Whisper, SenseVoice
- GPU strongly recommended (CUDA/Metal/Vulkan)
- RAM: 8-16 GB recommended for server
- Models: multiple GB depending on backends enabled

### Platform Support
- Linux ✓ (primary, Docker)
- Windows ✓ (Dashboard app + Docker/WSL2)
- macOS ✓ (Dashboard app + Docker, Apple Silicon Metal supported)

### Verdict for Mumble
**NOT suitable.** TranscriptionSuite is a heavyweight server-based application, not a library or embeddable component. The GPLv3 license is incompatible with Mumble's likely MIT license. The "vibecoded" nature raises reliability concerns for a production integration. Its best value is as a reference architecture for multi-backend STT and long-form chunking strategies.

---

## 5. OpenWhispr — `github.com/OpenWhispr/openwhispr`

### Architecture & Code Quality
- **Stack:** TypeScript (48.5%), JavaScript (46.1%), C (3%), Swift (0.9%)
- **1,458 commits**, 60 contributors, 560 forks, 4.1k stars
- **79 releases** (latest: v1.7.3, June 24, 2026 — 4 days ago)
- Code quality is **very high** — professional-grade Electron app with React 19, TypeScript, Tailwind CSS v4
- Uses whisper.cpp and sherpa-onnx for local STT, Electron for desktop shell
- Professional CI/CD, code signing, auto-updates, 10-language i18n
- Extensive documentation at docs.openwhispr.com
- Public API and MCP server for programmatic access

### License
- **MIT** — permissive

### Maintenance Status
- **Last commit:** June 24, 2026 (4 days ago) — very actively maintained
- Large team (60 contributors), strong community (4.1k stars)
- Rapid release cadence (79 releases)
- Professional-grade maintenance with changelog

### System Audio Capture
- **Yes, cross-platform** — macOS (ScreenCaptureKit), Windows, Linux
- Meeting auto-detection for Zoom, Teams, FaceTime
- Google Calendar integration
- Uses Electron's screen capture APIs + native modules
- System audio + mic capture

### Speaker Diarization
- **Yes, local** — on-device speaker labeling with voice fingerprint recognition
- Works across meetings (speaker recognition persists)
- No HuggingFace token needed (uses sherpa-onnx based solution)
- Local processing, no cloud required

### Long-Form Transcription (30-60 min)
- Meeting-length recordings are a core use case
- Live speaker diarization during meetings
- Optional cloud transcription providers (Corti, Deepgram, AssemblyAI, OpenAI)
- Local models: Parakeet (fast), Whisper (accurate)

### Python Integration Difficulty
- **Very difficult to impossible** — it's a TypeScript/JavaScript Electron app
- No Python code at all
- Could potentially use the MCP server or public API for integration
- But core logic (audio capture, STT, diarization) is in C/JS, not Python
- Would need to run as separate process and communicate via IPC/API

### Embeddability
- **Separate process only** — full Electron desktop app
- Has an MCP server for AI assistant integration
- Has a public API for notes/transcription management
- Core audio/STT logic is tightly coupled to Electron's main process

### Disk/RAM Requirements
- Node.js 24+
- whisper.cpp models (~1-2 GB)
- Parakeet ONNX model (~50 MB)
- Electron runtime (~200 MB)
- RAM: ~1-3 GB at runtime

### Platform Support
- macOS ✓, Windows ✓, Linux ✓ (all first-class)
- True cross-platform with native builds for each

### Verdict for Mumble
**Not suitable for embedding** but worth studying for its architecture. OpenWhispr is the most polished and feature-complete meeting transcription app. Its cross-platform system audio capture, local diarization (no HF token needed), and meeting auto-detection are gold-standard implementations. However, it's a TypeScript/Electron app with zero Python code, making direct integration impossible. Mumble could use it as a reference for feature design and UX patterns, or launch it as a companion process for meeting-specific workflows.

---

## 6. Newer Tools Discovered (2026)

### 6a. `pasrom/meeting-transcriber` (macOS only)
- **Stack:** Swift (92.4%), 1,199 commits, 63 stars, MIT license
- **Last commit:** June 24, 2026 — actively maintained
- macOS menu bar app using Swift, WhisperKit/Parakeet via CoreML
- FluidAudio for speaker diarization (no HF token needed)
- Dual-track (app audio + mic) capture via CATapDescription
- Automatic meeting detection (Teams/Zoom/Webex)
- **Verdict:** Beautiful macOS-specific implementation. Swift-only, no Python integration. Excellent reference for macOS audio capture patterns.

### 6b. `lukasbach/pensieve` (cross-platform, Electron)
- **Stack:** TypeScript (97.5%), 277 commits, 114 stars, 39 releases, MIT license
- **Last commit:** May 30, 2026 — maintained but slower cadence
- Electron desktop app using bundled Whisper (whisper.cpp)
- Local LLM via Ollama for summarization
- System audio capture from running apps
- MCP server for AI integration
- **Verdict:** Good Electron-based alternative. No Python integration. Last commit a month ago suggests lower maintenance velocity.

### 6c. `NavodPeiris/speechlib` (Python library)
- **Stack:** Python library on PyPI
- Unifies speaker diarization + transcription + speaker recognition in a single pipeline
- Uses faster-whisper + pyannote.audio
- Designed to be importable (`pip install speechlib`)
- **Verdict:** Potentially useful as a drop-in Python library if Mumble wants a pre-packaged diarization pipeline without building from scratch.

---

## 7. Technical Deep-Dive: System Audio Capture

### Windows (WASAPI Loopback)
**Best approach:** Use `sounddevice` with WASAPI host API + loopback device enumeration.

TalkTrack's implementation is the best reference:
```python
import sounddevice as sd

# Enumerate WASAPI loopback devices
wasapi_devices = [d for d in sd.query_devices() 
                  if d['hostapi'] == wasapi_index]

# Find the loopback device for the default speaker
loopback_device = next(d for d in wasapi_devices 
                       if 'loopback' in d['name'].lower())

# Record system audio
audio = sd.rec(frames, samplerate=16000, channels=2, 
               device=loopback_device, dtype='float32')
```

**PyAudioWPatch** (`pypi.org/project/PyAudioWPatch/`) provides an alternative via a patched PyAudio with WASAPI loopback support.

**Per-app capture (Win11):** Uses `pycaw` for audio session enumeration + `comtypes` for WASAPI process loopback. This is actively developed in TalkTrack and getting stable.

### macOS (Core Audio / ScreenCaptureKit)
**Best approach:** ScreenCaptureKit (macOS 14.2+) via a Swift helper binary.

Ownscribe's Swift helper is the cleanest reference — it uses `SCContentSharingPicker` for content selection, then sets up a Core Audio Tap on the selected process audio.

For older macOS, AudioToolbox's `AudioObjectAddPropertyListener` + aggregate device creation can work but is fragile.

### Linux (PulseAudio / PipeWire)
**Best approach:** PulseAudio monitor sources or PipeWire capture.

PulseAudio creates monitor sources for each output device (e.g., `alsa_output.pci-0000_00_1f.3.analog-stereo.monitor`). PipeWire has equivalent functionality. Can use `sounddevice` on Linux with the PulseAudio host API. But per-app capture is significantly harder on Linux.

---

## 8. Technical Deep-Dive: Speaker Diarization

### pyannote.audio (most common)
- Used by: Ownscribe, TalkTrack, TranscriptionSuite, speechlib
- Requires HuggingFace token (free, but requires account + license acceptance)
- Models: `pyannote/speaker-diarization-community-1` or `pyannote/speaker-diarization-3.1`
- GPU acceleration: CUDA, MPS (Apple Silicon), CPU fallback
- **Integration:** `from pyannote.audio import Pipeline`

### WhisperX (wrapper)
- Wraps faster-whisper + pyannote + VAD in one pipeline
- Used by: Ownscribe, TranscriptionSuite
- Provides word-level timestamps + speaker labels in one pass
- **Integration:** `import whisperx`

### FluidAudio (macOS-only, CoreML)
- Used by: pasrom/meeting-transcriber
- On-device via Apple Neural Engine, no HF token needed
- ~10x faster than pyannote on Apple Silicon
- Closed-source but free to use

### sherpa-onnx (cross-platform, local)
- Used by: OpenWhispr
- ONNX-based, runs on CPU/GPU
- No cloud, no token needed
- Parakeet models for ASR, built-in speaker embedding

---

## 9. Summary Comparison Matrix

| Criteria | Meetily | Ownscribe | TalkTrack | Transcr.Suite | OpenWhispr |
|---|---|---|---|---|---|
| **Language** | Rust/TS | Python/Swift | Python | Python/TS | TS/JS |
| **License** | MIT | MIT | MIT | GPLv3+ | MIT |
| **Active** | ✓ (3w ago) | ✓ (5d ago) | ✓ (3d ago) | ✓ (today) | ✓ (4d ago) |
| **Stars** | 12.9k | 78 | 20 | 530 | 4.1k |
| **SysAudio Win** | ✓ WASAPI | ✗ | ✓ WASAPI | ✗ | ✓ |
| **SysAudio Mac** | ✓ CoreAudio | ✓ SCKit | ✗ | ✗ | ✓ |
| **SysAudio Linux** | Partial | ✗ | ✗ | ✗ | ✓ |
| **Diarization** | PRO only | pyannote | pyannote | pyannote | sherpa-onnx |
| **Long-form** | ✓ | ✓ | ✓ | ✓ | ✓ |
| **Python embed** | ✗ | ✓ Easy | ✓ Good | ✗ | ✗ |
| **Library mode** | ✗ | ✓ | Partial | ✗ | ✗ |
| **Disk (models)** | ~1-2 GB | ~3-7 GB | ~2-4 GB | ~5-15 GB | ~1-2 GB |
| **RAM** | ~1-2 GB | ~4-8 GB | ~2-4 GB | ~8-16 GB | ~1-3 GB |

---

## 10. Recommendation: Best Foundation for Mumble

### Primary Recommendation: **Ownscribe** (transcription pipeline) + **TalkTrack** (Windows audio capture patterns)

**Rationale:**

1. **Ownscribe's Python pipeline is the single best codebase to integrate.** It provides a clean, tested, MIT-licensed Python implementation of the full meeting transcription stack: WhisperX → pyannote diarization → local LLM summarization. All modules are importable. The code is modern (uv-based, typed, tested), actively maintained (5 days ago), and designed as a CLI that separates concerns cleanly.

2. **TalkTrack provides the missing Windows audio capture layer.** Mumble already runs on Windows. TalkTrack's WASAPI loopback + per-app capture implementation is the best Python reference available. The `app/audio/audio_capture.py` module is well-structured and could be adapted into Mumble's recording pipeline.

3. **Integration strategy:**
   - **Step 1:** Adapt TalkTrack's Windows WASAPI capture into Mumble (copy the `audio_capture.py` pattern, rewrite for tkinter instead of PyQt6).
   - **Step 2:** On macOS, use a Swift helper (inspired by ownscribe's helper) for ScreenCaptureKit system audio. On Linux, use PulseAudio monitor sources.
   - **Step 3:** Import ownscribe's transcription + diarization pipeline. Since Mumble already uses faster-whisper, WhisperX is a natural upgrade (adds word timestamps + VAD segmentation). 
   - **Step 4:** Add ownscribe's pyannote diarization as an opt-in feature (requires HuggingFace token).
   - **Step 5:** Optionally add ownscribe's local LLM summarization (Phi-4-mini via llama-cpp-python — Mumble already has llama-cpp-python integration).

4. **Why not the others:**
   - **Meetily:** Best end-user product but zero Python integration path. Rust audio capture code would need total rewrite.
   - **TranscriptionSuite:** GPLv3 license conflict, heavyweight Docker architecture.
   - **OpenWhispr:** Best cross-platform audio capture but TypeScript-only, zero Python code.

5. **Fallback:** If full integration is too heavy, **speechlib** (`NavodPeiris/speechlib`, PyPI) provides a pre-packaged "drop-in" diarization + transcription pipeline as a single Python library import. Less flexible than ownscribe but faster to adopt.

### Key Risks/Blockers:
- **Windows system audio capture is hard.** Windows 11 per-app capture has the `AUDCLNT_STREAMFLAGS_EXCLUDE_FROM_PROCESS_LOOPBACK_CAPTURE` opt-out that Teams/Zoom use. Device-level WASAPI loopback captures everything (including notifications). This is well-documented in TalkTrack.
- **macOS system audio capture requires a compiled Swift helper.** This adds build complexity. Ownscribe's helper is the cleanest starting point.
- **Linux system audio capture is platform-heterogeneous** (PulseAudio vs PipeWire vs ALSA). A complete solution across distros is significant effort.
- **pyannote.audio requires a HuggingFace token.** This is a user-experience friction point. OpenWhispr's local diarization (sherpa-onnx, no token) is more user-friendly but harder to implement from scratch.
