# Mumble current-state product and engineering audit

**Audit date:** 10 July 2026  
**Audited version:** source version 0.9, after complete removal of the two temporary assistant/archetype experiments  
**Environment:** Windows 11 workspace; macOS and Linux assessed from source/configuration only  
**Verdict:** **Not ready for version 1.0. Architectural intervention and several concrete reliability/privacy fixes are required before release.**

Evidence labels used below:

- **Confirmed — reproduced:** observed in the running application, logs, commands, or tests.
- **Confirmed — code:** traced through a reachable production path.
- **Strongly evidenced:** code and configuration support the finding, but the final failure was not reproduced on target hardware.
- **Potential:** a credible reachable risk that still needs a focused runtime/stress test.
- **Recommendation:** an improvement rather than a current defect.

## 1. Executive Summary

Mumble is a background desktop dictation application. Its central promise is excellent: press a global shortcut, speak, see an unobtrusive island communicate the state, and have cleaned text pasted into the current application. Speech-to-text is intended to be local by default, with optional cloud transcription and optional cloud/local AI shaping. Around that core, Mumble now includes a Deck/history workspace, statistics, a document Reader, Meeting recording, multiple AI providers, local-model support, clipboard capture, cloud sync, and Windows/macOS/Linux variants.

The strongest part of the product is the core idea and the care visible in its interaction design. The island state model, design tokens, empty states, reduced-motion/resource modes, local-first intention, fallback formatting, and breadth of defensive tests show real product and engineering thought. Mumble is substantially more than a prototype.

The current implementation does not yet deliver the reliability or trustworthiness implied by that design. The most serious confirmed defects are:

1. Canonical Windows local transcription is broken because `mumble.py` calls a missing `formatting.sanitize_hotwords()` function. This is reproduced in the live log.
2. Meeting audio can be lost if the user quits during post-recording processing, and deleting a meeting leaves its microphone recording on disk despite the UI promising permanent removal.
3. Update manifests are accepted without publisher signatures; the configured feed also currently returns HTTP 404.
4. Privacy statements claim audio is never uploaded even while the product exposes and currently uses Cloud transcription. Reader operations can send document text to external services without an adequate disclosure.
5. The visible Account/Sync feature cannot work in a clean installation because `supabase` is not installed and users are asked to provision developer infrastructure.
6. The tracked `Mumble.zip` is stale and materially different from the source being audited.
7. The offline test baseline is red (43 of 52 non-live scripts passed), the documented test command is not viable, and some tests reconstruct intended logic rather than invoking production code.
8. Windows, macOS, and Linux are maintained as copied application trees. Existing drift is substantial, with macOS materially behind the canonical Web UI.

The code is not “bad code” in the sense of being careless everywhere. There are good subsystems and strong defensive ideas. The problem is unevenness: newer packages are reasonably modular, while the controller, Web bridge, overlay, and fallback UI are very large; release engineering is manual; copied ports multiply every fix; and product scope has expanded faster than integration and validation discipline.

**Overall judgement:** Mumble has a strong foundation worth continuing, but the current build is not a safe or coherent 1.0. The right response is not a rewrite. Fix the confirmed core/data/privacy blockers, make the release and test baseline trustworthy, then incrementally extract shared services and replace copied platform trees with thin adapters.

## 2. Experimental Feature Removal Report

### What was removed

Both temporary implementations were removed completely:

- Hands-free computer-control assistant (SWE 1.7 and DeepSeek implementations).
- 20-hour milestone, speaker/archetype report, newsletter gate, related usage analysis and popup UI.
- Assistant hotkeys, recording/transcription hook, desktop actions, command parser, controller lifecycle hooks, command-server routes, island states, Web UI bridge calls, modal JavaScript/CSS, tests, documentation, bytecode, marker files, and persisted experiment state.

### Files changed

These files were restored exactly to their pre-experiment content:

- `Development Files/Core/HANDBOOK.html` — removed experiment inventory and restored the 55-test baseline.
- `Development Files/Core/LOGS.html` — removed the experiment release-log section.
- `Development Files/Core/README.html` — removed the experiment tree entry and restored the test count.
- `Development Files/Core/STATUS.html` — removed experiment status/release checklist entries and restored the test count.
- `Internal/app/mumble.py` — removed both imports/initialisers/shutdown hooks, assistant transcription API, and experimental command route.
- `Internal/app/overlay.py` — removed assistant-specific labels, colours, active states, and animation spans.
- `Internal/app/island_render.py` — removed assistant canonicalisation and animation mappings.
- `Internal/app/webui_shell.py` — removed report/newsletter APIs and popup command handling.
- `Internal/app/webui/app.js` — removed mock APIs and all milestone/report modal logic.
- `Internal/app/webui/app.css` — removed all experiment-specific styles.

### Files deleted

- `Development Files/Other/Experimental Feature Removal Audit - SWE.md`
- `Development Files/Other/Experimental Feature Removal Audit - DeepSeek.md`
- `Internal/app/test_experimental_swe17.py`
- `Internal/app/experimental_swe17/README.md`
- `Internal/app/experimental_swe17/__init__.py`
- `Internal/app/experimental_swe17/actions.py`
- `Internal/app/experimental_swe17/assistant.py`
- `Internal/app/experimental_swe17/commands.py`
- `Internal/app/experimental_swe17/milestone.py`
- `Internal/app/experimental_swe17/report.py`
- `Internal/app/experimental_swe17/store.py`
- `Internal/app/experimental_deepseek/__init__.py`
- `Internal/app/experimental_deepseek/assistant.py`
- `Internal/app/experimental_deepseek/desktop.py`
- `Internal/app/experimental_deepseek/milestone.py`
- `Internal/app/experimental_deepseek/archetype.py`
- `Internal/app/experimental_deepseek/webui/assistant.html`
- `Internal/app/experimental_deepseek/webui/assistant.css`
- `Internal/app/experimental_deepseek/webui/assistant.js`

Generated experiment `__pycache__` files, `.milestone_shown`, `.archetype_unlock`, and `%APPDATA%\Mumble\experimental_swe17_state.json` were also removed.

### Deliberately retained shared code

Core hotkey registration, audio capture, Whisper transcription, statistics, authenticated localhost IPC, normal island states, Web UI modal infrastructure, subprocess handling, clipboard support, and desktop/platform utilities remain because the non-experimental product uses them.

### Verification

- The ten shared/documentation files compare with no differences against commit `ab303ab`'s parent (the exact pre-experiment baseline).
- Repository-wide searches found no remaining `experimental_swe17`, `experimental_deepseek`, `assistant_transcribe`, `experimental_popup`, or `pyExperimentalPopup` references.
- Python source compilation passed for canonical and port trees.
- JavaScript syntax checks passed for Windows, macOS, and Linux `webui/app.js`.
- The application was shut down through authenticated IPC, restarted from the modified source, reached `Mumble ready`, and returned authenticated status `{state: idle, text: Ready, recording: false}`. No experiment registered a hotkey or emitted a startup log entry.
- `git diff --check` passed.

**Uncertainty:** a post-removal physical microphone/global-hotkey dictation was not performed because that would take over active user input. macOS/Linux could not be run on their native operating systems in this environment. These are not claimed as verified.

## 3. Product Assessment

### Idea, users, and value

The strongest target is a knowledge worker who wants low-friction desktop dictation without uploading every recording: writers, operators, accessibility users, professionals composing email/prompts, and people moving text between applications. The core loop has clear value and a credible differentiator: local transcription plus system-wide paste, with visible state and graceful non-AI formatting.

### Coherence

Home, island, dictation, Deck/history, settings, and statistics belong together. Reader and Meetings can also fit a broader “voice and text workspace,” but they currently feel like adjacent products with different privacy/performance/reliability assumptions. Self-hosted Supabase setup, model IDs, GGUF management, six cloud providers, TTS credits, and platform-specific legacy mode workflows make the consumer product feel like a developer console.

### Execution and polish

The visual/interaction foundation is above average: consistent black/gold design, mode colours, responsive layout rules, empty states, disabled states in many important places, reduced-motion support, and explicit listening/transcribing/building/done states. The product loses polish through contradictions and false success states rather than through lack of styling. Privacy claims, shortcut instructions, mode instructions, meeting state, and cloud-account readiness disagree across surfaces.

### Areas that work well

- One-shortcut dictation proposition and stateful island.
- Local-first architecture and deterministic fallback intention.
- Deck/history search, preservation of selection, favourites, and presets.
- Reader format breadth and keyboard-aware document entries.
- Authenticated localhost command channel and defensive update extraction.
- Atomic/recovery-oriented persistence in several stores.

### Areas that feel unfinished

- Account/Sync, Share Mumble, update publishing, macOS parity, packaging/signing, and clean-install feature dependencies.
- Meeting durability and long-session resource design.
- A single canonical Smart Mode workflow across Web UI, Lite UI, documentation, and ports.
- Privacy disclosures that change with the selected engine/provider.

## 4. UI and UX Audit

### Home and dictation

- **High:** Home unconditionally says audio is local/never sent (`webui/index.html:87-90,374,655-656`) while Cloud transcription uploads audio (`:2322-2343,3177-3196`). `bootHome()` does not adapt the claim (`webui/app.js:1457-1505`). Make privacy/status text engine-aware.
- **Medium:** `bootHome()` writes “Ready” before authoritative polling (`app.js:1393-1505`), potentially masking an active state for up to the idle polling interval. Fetch status first and reject stale writes.
- **Strong point:** island state names, animation, mode colours, and resource/reduced-motion variants provide unusually clear feedback when the underlying controller state is correct.

### Deck/history

- **Medium:** user documentation labels Ctrl+Alt+V as Deck, while settings and UI define Ctrl+Alt+D; V pastes the latest transcript (`Internal/README.md:64`, `settings.py:65-66`, `webui/index.html:312-319`).
- **Medium:** copy paths suppress clipboard rejection and still toast success (`app.js:1718-1725`). Await success and fall back to the Python clipboard bridge.
- **Medium accessibility:** the unchecked selection control is unlabeled (`app.js:2169`).
- **Strong point:** live refresh preserves selections and the unified search/favourite/preset workflow is useful.

### Statistics

- **Low accessibility:** charts expose values mainly through mouse-hover `title` attributes and lack a focusable/text-table alternative (`app.js:2887-2897,2984-2994,3052-3092`).
- **Strong point:** estimated time is labelled as estimated and the heatmap/mode mix use real stored data.

### Reader

- **High privacy:** Reader sends document chunks to external TTS (`webui_shell.py:307-333`) and can send document text to cloud LLM summaries (`:593-626`), but the Reader UI only discusses keys/credits (`index.html:1082-1109`). Add a first-use and per-operation disclosure naming provider and payload.
- **Medium reliability:** fallback may preserve a failed provider/model and mask the useful error (`STATUS.html:322-332`, `webui_shell.py:307-333`). Persist the effective fallback and retain the primary error.
- **Low performance:** one-span-per-word rendering and unbounded long-document chunking need virtualization/safety limits (`STATUS.html:213-216`).
- **Strong point:** library, format badges, bookmarks, collections, resume and document-entry keyboard handling are substantial.

### Meetings

- **High privacy/state:** pause/resume UI always flips state and toasts success without checking `{ok:false}` (`app.js:6013-6033`, `webui_shell.py:818-832`). A user can be told recording is paused while it is not.
- **High privacy:** delete promises transcript and recording removal, but only deletes metadata (`app.js:5811,5872`, `meeting_store.py:229-233`).
- **Medium:** both star controls always pass `true`, so meetings cannot be unstarred (`app.js:5795-5799,5856-5858`).
- **Medium accessibility:** clickable meeting `<div>` cards lack keyboard semantics; icon buttons lack accessible labels (`app.js:5831-5854`).
- **Strong point:** record/pause/process/export structure and structured outputs are conceptually clear.

### Settings and onboarding

- **High:** Account asks ordinary users to create a Supabase project and paste URL/key without installing the required schema; the dependency is absent and project status calls it blocked (`index.html:2769-2859`, `cloud_sync.py:145-159`, `STATUS.html:812-813`). Hide or mark developer-only.
- **Medium IA:** one screen mixes microphone and shortcuts with provider model IDs, Supabase, GGUF, identity and support (`index.html:1462-2937`). Split Basic/Advanced or searchable sections.
- **Medium:** the AI-key Skip warning can be bypassed with the global Next button (`app.js:4297-4300,6477-6491`).
- **Medium:** Web UI onboarding says modes need not be memorised, while Lite UI and README teach held Right Shift/spoken mode names (`index.html:3373-3430`, `app_window.py:447-487,577-581,1923`, `Internal/README.md:42-61`).
- **Informational:** unreachable onboarding `data-step="99"` remains although the wizard clamps to steps 1-10 (`index.html:3373`, `app.js:4273-4278`).

### Modals, navigation, sharing, and accessibility

- **Medium:** dynamic modals lack complete dialog semantics, focus trap, Escape handling, and focus restoration (`app.js:1236-1320`; update modal `index.html:3611-3694`).
- **Medium:** muted 9-12 px text uses approximately 4.06:1 contrast, below the 4.5:1 normal-text target (`app.css:21-40`).
- **Medium/product blocker if visible:** Share Mumble copies a literal placeholder GitHub URL (`app.js:6719-6735`).
- **Low:** navigation uses generic divs, does not set `aria-current`, and does not move focus to the destination heading (`index.html:31-53`, `app.js:1383-1407`).

## 5. Codebase and Architecture Assessment

The repository is a Python desktop application with a controller process (`mumble.py`), pywebview shell/bridge (`webui_shell.py`), HTML/CSS/JavaScript SPA, tkinter/Pillow island, local stores, transcription/AI engines, and copied macOS/Linux application trees.

Good boundaries exist in `models/`, `pipeline/`, `eval/`, `perf/`, provider modules, platform probes, stores, and update verification/extraction. Settings recovery, IPC authentication, model download cancellation, and several pure engines show careful defensive thinking.

The dominant architecture is nevertheless too coupled:

- `mumble.py`: about 5,107 lines and 198 functions; the `Mumble` class owns audio, transcription, processing, shortcuts, meeting orchestration, IPC, processes, tray, window lifecycle and shutdown.
- `webui_shell.py`: about 2,416 lines/142 functions; `Api` spans most of it.
- `overlay.py`: about 2,295 lines/102 functions; `Island` mixes state, rendering, windows, mode bar and fallback Deck.
- `app_window.py`: about 2,496 lines; its fallback UI is effectively a second product UI.
- `ai/__init__.py`: about 2,381 lines despite extracted provider/TTS/constitution modules.

Pyflakes-only analysis reported 31 issues, including 17 redefinitions. Most serious are facade imports immediately redefined in `ai/__init__.py` and duplicated provider/TTS functions. These are maintainability signals and can cause divergent behavior even when not immediately user-visible.

The largest architectural defect is copied platform trees. Only 13 of 36 comparable top-level runtime/config files are byte-identical on macOS and 8 on Linux. Canonical Windows uses newer packages, macOS retains legacy `ai.py/model_free.py`, and Linux retains both. The missing Windows sanitizer while both ports have it is a concrete drift regression.

**Maintainability verdict:** viable for continued work, but feature additions should pause until core services and platform sharing improve. Extract recording lifecycle, transcription, processing/router, command IPC, and app/window coordination incrementally. A wholesale rewrite would add risk.

## 6. Complete Issue Register

### MUM-001 — Canonical local transcription fails before Whisper

- **Category/severity/confidence/OS:** Functional correctness; High; Confirmed — reproduced; Windows canonical.
- **Files/area:** `Internal/app/mumble.py:1212-1236`, `_local_transcribe`; missing symbol in `Internal/app/formatting.py`.
- **Description/impact/cause:** every local transcription calls nonexistent `formatting.sanitize_hotwords`; live logs repeatedly show `AttributeError`, so local STT and cloud-to-local fallback fail before `model.transcribe`.
- **Reproduction:** select/use local STT and dictate; runtime log records the missing attribute and stream-worker failure.
- **Fix/scope/1.0:** restore/review the sanitizer from a port; unit-test it and controller-level mocked transcription. **Small; blocks 1.0.**

### MUM-002 — Tracked release archive is stale

- **Category/severity/confidence/OS:** Distribution; Critical; Confirmed — archive inspection; Windows release.
- **Files/area:** root `Mumble.zip`; `Development Files/Other/build-tools/_rebuild_zip.py`; `STATUS.html:279-291`.
- **Description/impact/cause:** the 28 June archive has 51 entries, differs in controller/formatting/Web UI/requirements, and contains legacy `ai.py`; users do not receive the audited product because release rebuilding is manual.
- **Reproduction:** list/extract `Mumble.zip` and compare manifest/hashes with `Internal/app`.
- **Fix/scope/1.0:** rebuild from clean checkout and perform extract/install/run/uninstall manifest validation in CI. **Medium; blocks 1.0.**

### MUM-003 — Update publisher authenticity is disabled

- **Category/severity/confidence/OS:** Security; High; Confirmed — code; all.
- **Files/area:** `update.py:45-57,65-94,125-254` and both port copies.
- **Description/impact/cause:** `UPDATE_PUBLIC_KEY=""` makes signature verification accept unsigned manifests. SHA-256 protects against accidental corruption but not a compromised feed, so an attacker controlling publishing/domain could deliver code.
- **Reproduction:** a structurally valid unsigned test manifest is accepted by the verification path.
- **Fix/scope/1.0:** offline Ed25519 key, embedded public key, no unsigned fallback, pinned `cryptography`, invalid/missing-signature tests. **Medium; blocks 1.0.**

### MUM-004 — Configured update feed is missing

- **Category/severity/confidence/OS:** Functional/release; Medium; Confirmed — reproduced; all.
- **Files/area:** `update.py:42,125-138`; runtime `mumble.log`.
- **Description/impact/cause:** `https://mumble-app.github.io/mumble-updates/update.json` repeatedly returns HTTP 404, so update discovery does not work and adds log noise.
- **Reproduction:** launch app or check the configured feed.
- **Fix/scope/1.0:** publish the signed feed or disable/hide update checks until the release channel exists. **Small; blocks 1.0 if updater is advertised.**

### MUM-005 — Quitting during meeting processing loses the meeting

- **Category/severity/confidence/OS:** Reliability/data loss; High; Confirmed — code; all.
- **Files/area:** `mumble.py:586-609,4670-4788`; `meeting.py:103-149`; port quit paths.
- **Description/impact/cause:** Stop starts a daemon processing thread; frames are cleared and raw audio is not persisted until transcription/diarisation complete; forced `os._exit` kills the job. An ordinary stop-then-quit can destroy the full recording.
- **Reproduction:** record, Stop, then Quit during processing; no durable audio/record exists.
- **Fix/scope/1.0:** atomically stream/persist raw audio first, create resumable processing record, track/join jobs, and warn on unsafe quit. **Large; blocks 1.0.**

### MUM-006 — Delete Meeting leaves sensitive WAV on disk

- **Category/severity/confidence/OS:** Privacy/functional; High; Confirmed — code; all.
- **Files/area:** `meeting.py:845-867`; `meeting_store.py:146-165,229-233`; `webui_shell.py:742-746`; `app.js:5811,5872`.
- **Description/impact/cause:** metadata deletion never unlinks the stored recording, contradicting the UI promise and creating inaccessible sensitive orphans.
- **Reproduction:** record, note WAV, delete in UI, verify WAV remains.
- **Fix/scope/1.0:** safely resolve beneath `meetings_audio`, delete audio then metadata, report partial failure, migrate/clean orphans. **Small; blocks 1.0.**

### MUM-007 — Meeting pause/resume displays false success

- **Category/severity/confidence/OS:** Privacy/state correctness; High; Confirmed — code; all Web UI builds.
- **Files/area:** `webui/app.js:6013-6033`; `webui_shell.py:818-832`.
- **Description/impact/cause:** UI ignores `{ok:false}`, flips its state, and toasts success. The user can believe the microphone is paused while recording continues.
- **Reproduction:** make controller channel unavailable during a meeting and press Pause.
- **Fix/scope/1.0:** mutate only after authoritative success; retain prior state and show persistent error. **Small; blocks 1.0.**

### MUM-008 — Long meetings are fully buffered and copied in RAM

- **Category/severity/confidence/OS:** Performance/reliability; High; Confirmed — code; all.
- **Files/area:** `meeting.py:67-70,103-104,180-225`.
- **Description/impact/cause:** every audio callback copies a buffer; the whole list remains until another full concatenation. 16 kHz mono float32 is roughly 230 MB/hour before overhead, with another large allocation at Stop.
- **Reproduction:** monitor RSS during multi-hour recording/import.
- **Fix/scope/1.0:** stream PCM to durable temporary WAV, process bounded chunks, impose/test practical limits. **Large; blocks 1.0 for advertised Meetings.**

### MUM-009 — Meeting filenames can collide within one second

- **Category/severity/confidence/OS:** Data integrity; Medium; Strongly evidenced; all.
- **Files/area:** `meeting.py:854-855`; `mumble.py:611-627`.
- **Description/impact/cause:** whole-second filenames plus independently threaded imports can overwrite one recording or make records share a WAV.
- **Reproduction:** complete two saves in the same second.
- **Fix/scope/1.0:** UUID/exclusive tempfile and atomic rename. **Small; blocks 1.0 because recording loss is possible.**

### MUM-010 — Clean installation omits visible-feature dependencies

- **Category/severity/confidence/OS:** Dependencies/functional; High; Confirmed — installer/manifests; all.
- **Files/area:** `install.ps1:96-104`, `requirements.txt`; dynamic imports in `reader_parser.py:366-812`, `cloud_sync.py:146`, `meeting.py:205`; advertised formats `index.html:477-478,1240`.
- **Description/impact/cause:** installer installs a minimal list lacking Supabase, Reader parsers, soundfile and other advertised feature libraries. Developer venv success does not represent clean-user installs.
- **Reproduction:** create clean venv from requirements and exercise Account, formats, or meeting import.
- **Fix/scope/1.0:** define/install required dependencies and explicit optional extras; add clean-env feature import tests. **Medium; blocks 1.0.**

### MUM-011 — Visible Account/Sync is not consumer-ready

- **Category/severity/confidence/OS:** Product completeness; High; Confirmed; all.
- **Files/area:** `index.html:2769-2859`; `cloud_schema.sql`; `cloud_sync.py:145-159`; `STATUS.html:812-813`.
- **Description/impact/cause:** users are asked to provision Supabase, but schema setup is not explained and `supabase` is absent; sign-in fails in bundled Windows environment.
- **Reproduction:** configure credentials/sign in from clean supported install; module import fails.
- **Fix/scope/1.0:** hide as developer-only until a supported backend exists, or ship/document/test the complete service. **Medium; blocks 1.0 if visible.**

### MUM-012 — Home privacy claims are false in Cloud transcription mode

- **Category/severity/confidence/OS:** Privacy communication; High; Confirmed — code and current configured state; all.
- **Files/area:** `index.html:87-90,374,655-656,2322-2343,3177-3196`; `app.js:1457-1505`; core/user docs.
- **Description/impact/cause:** unconditional “local/never sent” copy remains while Cloud STT uploads microphone audio. This is a trust and informed-consent failure.
- **Reproduction:** enable Cloud transcription and return Home.
- **Fix/scope/1.0:** provider/state-aware privacy badges and accurate docs/onboarding. **Medium; blocks 1.0.**

### MUM-013 — Reader cloud operations lack clear document-data disclosure

- **Category/severity/confidence/OS:** Privacy; High; Confirmed — code; all.
- **Files/area:** `webui_shell.py:307-333,593-626`; `index.html:1082-1109`.
- **Description/impact/cause:** TTS/summary can transmit confidential document text, while UI mentions keys/credits rather than payload/provider.
- **Reproduction:** select cloud Reader TTS or summary and inspect request path.
- **Fix/scope/1.0:** first-use/per-operation disclosure, provider/payload label, and local/system TTS option. **Medium-large; blocks 1.0.**

### MUM-014 — Sensitive POSIX files lack explicit permissions

- **Category/severity/confidence/OS:** Privacy; Medium; Strongly evidenced; macOS/Linux.
- **Files/area:** `branding.ensure_dirs`; `settings.py:255-274,587-592`; `cloud_sync.py:46-77`.
- **Description/impact/cause:** settings/API keys, refresh tokens, transcripts and recordings use ordinary directory/file creation; common umask may permit other local accounts to read them. UI says session is secure.
- **Reproduction:** install under umask 022 and inspect modes.
- **Fix/scope/1.0:** 0700 directory/0600 files, migration, OS credential stores for reusable secrets. **Medium; recommended 1.0 blocker on multi-user systems.**

### MUM-015 — Dependency resolution is not reproducible and currently conflicts

- **Category/severity/confidence/OS:** Tooling/dependencies; High release risk; Confirmed; all.
- **Files/area:** three `requirements.txt`; no lock/constraints/pyproject.
- **Description/impact/cause:** broad lower bounds and unpinned pywebview resolve differently over time. `pip check` currently reports `pyannote-metrics 4.1` needs NumPy >=2.2.2 while installed NumPy is 2.1.3 and Mumble constrains `<2.2`.
- **Reproduction:** `pip check`; compare clean installs over time.
- **Fix/scope/1.0:** platform locks/constraints with hashes and a resolved compatible diarisation stack. **Medium; blocks reproducible 1.0.**

### MUM-016 — Settings dirty-key merge has a race

- **Category/severity/confidence/OS:** Concurrency/reliability; Medium; Potential; all.
- **Files/area:** `settings.py:558-630`.
- **Description/impact/cause:** `set/update` mutate data and `_dirty` outside the save lock while `_do_save` reads/resets it under lock; concurrent changes may be lost in cross-process merge.
- **Reproduction:** concurrent setter/save stress test required.
- **Fix/scope/1.0:** one lock across mutation, dirty marking, merge and write. **Small; not a blocker unless reproduced.**

### MUM-017 — Platform trees are copied forks

- **Category/severity/confidence/OS:** Architecture/cross-platform; High; Confirmed; all.
- **Files/area:** canonical `Internal/app` versus `Ports/macOS/app` and `Ports/Linux/app`.
- **Description/impact/cause:** only 13/36 comparable files are identical on macOS and 8/36 on Linux; fixes must be triplicated and divergent architectures remain.
- **Reproduction:** file/hash comparison; the missing Windows sanitizer is a concrete regression.
- **Fix/scope/1.0:** shared core/UI package plus thin OS adapters; parity contract tests during migration. **Architectural; blocks credible cross-platform 1.0.**

### MUM-018 — macOS Web UI is materially behind

- **Category/severity/confidence/OS:** Cross-platform completeness; Medium; Confirmed — source; macOS.
- **Files/area:** port `webui/index.html/app.js` compared with canonical/Linux.
- **Description/impact/cause:** macOS lacks current meeting pause/deep outputs, structured Reader blocks and local-LLM hydration while presenting the same product version.
- **Reproduction:** source/capability comparison; native run still required.
- **Fix/scope/1.0:** one shared Web UI with capability adapters and parity tests. **Large; blocks 1.0 if parity is promised.**

### MUM-019 — Core orchestrators are oversized and coupled

- **Category/severity/confidence/OS:** Maintainability; High; Confirmed; all/canonical.
- **Files/area:** `mumble.py`, `webui_shell.py`, `overlay.py`, `app_window.py`, `ai/__init__.py`.
- **Description/impact/cause:** 2,300-5,100-line modules mix lifecycle, state, platform, UI, IPC and business logic; changes have broad regression surfaces.
- **Reproduction:** AST/line/function inspection.
- **Fix/scope/1.0:** incrementally extract recording, transcription, processing, IPC, and window coordination services. **Architectural; release risk, but do after functional blockers.**

### MUM-020 — Duplicate/redefined implementation remains in core packages

- **Category/severity/confidence/OS:** Technical debt; Medium; Confirmed; all.
- **Files/area:** `ai/__init__.py`, `ai/providers/local.py`, `perf/__init__.py`, `pipeline/__init__.py`.
- **Description/impact/cause:** pyflakes reports 31 F-class findings, including 17 redefinitions such as TTS/provider/constitution functions imported then redefined. Facades and implementations have not been fully separated.
- **Reproduction:** `flake8 --select=F` on canonical non-test code.
- **Fix/scope/1.0:** choose single implementation sources, reduce `__init__` files to facades, remove unused imports. **Medium; not alone a blocker.**

### MUM-021 — Offline test baseline is red and no authoritative runner exists

- **Category/severity/confidence/OS:** Testing; High; Confirmed — execution; Windows/cross-platform process.
- **Files/area:** 52 root `test_*.py`; `HANDBOOK.html:295`; absent root runner/pytest dependency.
- **Description/impact/cause:** 43/52 non-live scripts passed; 9 failed. `unittest discover` produces 21 import errors because many tests execute `sys.exit` at import. Documented pytest is not installed.
- **Reproduction:** sequential script run; `python -m unittest discover`; `python -m pytest`.
- **Fix/scope/1.0:** standardize one runner, separate unit/integration/live/platform tests, make baseline green and machine-readable. **Large; blocks 1.0.**

### MUM-022 — Tests overstate end-to-end coverage and miss production failure

- **Category/severity/confidence/OS:** Testing quality; High; Confirmed; canonical.
- **Files/area:** `test_end_to_end.py`; `test_api_stability.py`; `test_recording_gate.py`; `test_transcription_edge_cases.py:559-639`.
- **Description/impact/cause:** tests inspect source or reimplement sanitisation rather than invoke `_local_transcribe`, so “all features end-to-end” passed while local STT was broken.
- **Reproduction:** compare tests with production call graph/runtime log.
- **Fix/scope/1.0:** dependency-injected behavioral service tests for record→transcribe→process→paste and fallback. **Medium-large; blocks 1.0 test confidence.**

### MUM-023 — Test execution can unexpectedly use real API keys

- **Category/severity/confidence/OS:** Testing/privacy/cost; Medium; Confirmed — reproduced.
- **Files/area:** import-time/live sections in test scripts (notably provider/API stability tests).
- **Description/impact/cause:** broad discovery imported procedural tests, detected saved keys, made live Cerebras/OpenRouter calls, and printed credit details. Live tests are not reliably isolated from offline discovery.
- **Reproduction:** `unittest discover` in a configured user environment.
- **Fix/scope/1.0:** explicit opt-in environment marker, isolated test settings/data dir, never load production secrets in offline tests. **Medium; blocks safe CI/test operation.**

### MUM-024 — No application CI/CD and unsigned executables

- **Category/severity/confidence/OS:** Release engineering; High; Confirmed; Windows plus all-platform pipeline.
- **Files/area:** absent repository `.github/workflows`; root and venv `Mumble.exe` signatures.
- **Description/impact/cause:** no automated tests/builds/archive validation; both inspected executables report `NotSigned`, causing trust/SmartScreen problems and allowing manual release drift.
- **Reproduction:** inspect repository workflows and `Get-AuthenticodeSignature`.
- **Fix/scope/1.0:** CI test/build gates, signed Windows artifact, macOS Developer ID/notarization, versioned release manifests. **Large/external coordination; blocks public 1.0.**

### MUM-025 — Mode workflows and documentation contradict one another

- **Category/severity/confidence/OS:** UX/documentation; Medium; Confirmed; Windows Lite, ports, docs.
- **Files/area:** `webui/index.html:3373-3430`; `app_window.py:447-487,577-581,1923`; `Internal/README.md:42-61`.
- **Description/impact/cause:** current Web UI teaches a simple toggle/no memorisation, while Lite/docs teach held Right Shift and spoken mode names; users learn different products.
- **Reproduction:** compare onboarding/Lite/user guide.
- **Fix/scope/1.0:** decide one canonical workflow and update all surfaces/ports together. **Large coordination; blocks coherent cross-platform 1.0.**

### MUM-026 — User guide assigns wrong Deck shortcut

- **Category/severity/confidence/OS:** Documentation; Medium; Confirmed; all users of guide.
- **Files/area:** `Internal/README.md:64`; `settings.py:65-66`; `index.html:312-319`.
- **Description/impact/cause:** guide says Ctrl+Alt+V opens Deck; actual V pastes latest and D opens Deck.
- **Reproduction:** follow guide.
- **Fix/scope/1.0:** correct and add doc/setting shortcut consistency test. **Small; not blocker alone.**

### MUM-027 — Clipboard actions can report false success

- **Category/severity/confidence/OS:** UX/error recovery; Medium; Strongly evidenced; all Web UI.
- **Files/area:** `app.js:1718-1725,6110-6113,6733-6735`.
- **Description/impact/cause:** promise rejection is suppressed and “Copied” is always shown.
- **Reproduction:** deny/break clipboard write then invoke copy/export/share.
- **Fix/scope/1.0:** await result, toast only on fulfillment, Python bridge fallback. **Small; not blocker.**

### MUM-028 — Meetings cannot be unstarred

- **Category/severity/confidence/OS:** Functional UX; Medium; Confirmed; all Web UI.
- **Files/area:** `app.js:5795-5799,5856-5858`.
- **Description/impact/cause:** controls always call `meeting_star(id,true)`.
- **Reproduction:** star then try to unstar.
- **Fix/scope/1.0:** render current state and toggle it. **Small; not blocker.**

### MUM-029 — Modal and interactive-card accessibility is incomplete

- **Category/severity/confidence/OS:** Accessibility; Medium; Confirmed; all Web UI.
- **Files/area:** `app.js:1236-1320,5831-5854`; `index.html:3611-3694`.
- **Description/impact/cause:** no complete dialog semantics/focus lifecycle; clickable div cards and icon buttons lack keyboard/name support.
- **Reproduction:** keyboard/screen-reader inspection.
- **Fix/scope/1.0:** shared accessible dialog controller, real buttons/links, labels, Enter/Space and focus restore. **Medium; recommended release gate.**

### MUM-030 — Home can temporarily show Ready during active work

- **Category/severity/confidence/OS:** State UX; Medium; Strongly evidenced; all Web UI.
- **Files/area:** `app.js:1393-1505,1561-1581`.
- **Description/impact/cause:** page boot overwrites state before polling; user may see Ready while recording/transcribing.
- **Reproduction:** navigate Home during work; runtime confirmation still required.
- **Fix/scope/1.0:** authoritative status fetch and monotonic state/versioning. **Small; not blocker alone.**

### MUM-031 — Share Mumble contains placeholder URL

- **Category/severity/confidence/OS:** Product polish; Medium; Confirmed; all Web UI.
- **Files/area:** `app.js:6719-6735`.
- **Description/impact/cause:** visible share action copies `github.com/YOUR-GITHUB/...`.
- **Reproduction:** use Share Mumble.
- **Fix/scope/1.0:** production branding URL or hide action. **Small; blocks 1.0 if visible.**

### MUM-032 — Small muted text does not meet normal-text contrast target

- **Category/severity/confidence/OS:** Accessibility; Medium; Confirmed from tokens; all Web UI.
- **Files/area:** `app.css:21-40` and uses of `--text-mute`.
- **Description/impact/cause:** approximately 4.06:1 on dark surface while used at 9-12 px; target is 4.5:1.
- **Reproduction:** contrast calculation and rendered-token inspection.
- **Fix/scope/1.0:** lighten token/reserve for nonessential large text, visual regression review. **Small; not blocker alone.**

### MUM-033 — Settings IA mixes consumer and developer controls

- **Category/severity/confidence/OS:** Information architecture; Informational recommendation; all.
- **Files/area:** `index.html:1462-2937`.
- **Description/impact/cause:** microphone/shortcuts compete with raw model IDs, Supabase, GGUF and provider infrastructure; basic tasks are harder to locate.
- **Reproduction:** inspect full Settings hierarchy.
- **Fix/scope/1.0:** Basic/Advanced categories, progressive disclosure and settings search. **Medium; not a defect blocker.**

## 7. Bugs and Functional Defects

### Reproduced

- MUM-001 local STT `AttributeError` in runtime logs.
- MUM-004 updater feed HTTP 404 in runtime logs.
- MUM-010/MUM-011 clean environment lacks Supabase; Account cannot connect.
- MUM-015 `pip check` NumPy/pyannote conflict.
- MUM-021 full offline script baseline 43/52 and discovery errors.
- MUM-023 discovery invoked live provider calls using configured keys.
- MUM-024 executable signatures are `NotSigned`.

### Confirmed through reachable code

- MUM-005 meeting loss on quit during daemon processing.
- MUM-006 meeting deletion leaves WAV.
- MUM-007 false pause/resume success.
- MUM-008 full-session RAM buffering.
- MUM-009 timestamp filename collision.
- MUM-012/MUM-013 inaccurate privacy communication.
- MUM-026 wrong Deck shortcut.
- MUM-028 meetings cannot be unstarred.
- MUM-031 placeholder sharing.

### Potential/runtime verification still required

- MUM-014 effective POSIX exposure under target umask.
- MUM-016 concurrent settings write loss.
- MUM-030 Home Ready-state flicker.
- Native macOS/Linux workflows, permissions, focus, packaging and visual parity.
- Physical post-removal hotkey/microphone/audio-device/sleep-wake tests.

## 8. Security and Privacy Assessment

### Confirmed weaknesses

- Unsigned updater (MUM-003) is the primary security vulnerability. It is reachable and can become remote code execution if the publishing channel is compromised.
- Meeting deletion violates an explicit data-removal promise (MUM-006).
- Privacy disclosures are materially inaccurate for Cloud STT and Reader (MUM-012/013).
- Production secrets can be consumed by an ostensibly offline test run (MUM-023).

### Strongly evidenced risks

- POSIX sensitive-file permissions and plaintext reusable tokens/API keys (MUM-014).
- Unsigned application binaries and manual distribution chain (MUM-024).

### Areas handled well

- Command IPC binds to localhost, uses a fresh 256-bit token, constant-time comparison, request limits, fail-closed token persistence and best-effort 0600 permissions (`mumble.py:2085-2157,2340-2355`).
- API keys are masked before return to Web UI and excluded from cloud-synced settings (`cloud_sync.py:335-342`).
- Update downloads enforce HTTPS, mandatory SHA-256, version sanitisation and zip-slip protection (`update.py:133-138,196-254`).
- No reachable arbitrary shell-command injection was confirmed in the non-experimental product.

## 9. Performance Assessment

The most serious confirmed bottleneck is Meetings: roughly 230 MB/hour raw float32 audio plus Python/list overhead and a second full allocation at Stop, before transcription/diarisation models. This is not suitable for long meetings or low-memory devices and compounds the data-loss design.

Reader can create one span per word and lacks hard chunk/document limits; extreme documents may degrade DOM/memory responsiveness. The formatting-only memory test also exceeded its 200 MB target, but that result may be test-process contamination and requires clean-process profiling.

Positive points include resource tiers, reduced-motion mode, deferred local model loading in Cloud STT mode, background model warm-up, idle polling backoff, and model/backend performance infrastructure. A professional benchmark should measure cold start, idle, record, local transcription, cloud transcription, long meeting, Reader book, and post-operation memory in isolated processes.

## 10. Testing and Quality Assessment

The repository has many thoughtful edge-case tests, especially around providers, stores, settings recovery, command authentication, models, Reader parsing, and update extraction. The quantity is not the problem; execution model and integration fidelity are.

Validation results:

- 52 non-live root scripts executed sequentially: **43 passed, 9 failed** in about 450 seconds.
- Failures: `test_error_recovery.py`, `test_fallback.py`, `test_lightweight.py`, `test_local_engine.py`, `test_pipeline.py`, `test_pipeline_stage2_stage3.py`, `test_presets.py`, `test_stage_cleanup.py`, `test_transcription_edge_cases.py`.
- `test_meeting.py` and `test_transcription_diag.py` returned success while emitting torchcodec DLL load failures.
- Standard `unittest discover` is unusable: 21 import errors from import-time `sys.exit`, missing legacy `model_free`, and procedural test design.
- Documented pytest command is unusable because pytest is absent.
- `test_ui.py` and `test_webui_api.py` passed; JavaScript syntax passed on all three ports.
- Compilation passed for application Python sources.

Highest-risk missing behavioral coverage:

1. Real controller local transcription and cloud→local fallback.
2. Record→transcribe→process→paste with rapid start/stop and device loss.
3. Meeting durable stop, quit/restart recovery, delete audio, pause failure and long duration.
4. Clean installation and every advertised optional feature import.
5. Signed update end-to-end including invalid manifest and rollback.
6. Port capability parity and native permission/focus/shortcut behavior.
7. Accessible keyboard traversal and rendered layout snapshots.

The present suite is not strong enough to support safe releases because it is red, not centrally runnable, can use live keys, and missed a broken core path.

## 11. Cross-Platform Comparison

| Area | Windows | Linux | macOS |
|---|---|---|---|
| Canonical status | Most complete/current | Fairly close Web UI, mixed old/new runtime | Controller improvements exist, Web UI materially behind |
| Local STT | **Currently broken by missing sanitizer** | Sanitizer exists; native run unverified | Sanitizer exists; native run unverified |
| Meetings | Full UI, but shared durability/delete/RAM bugs | Similar shared bugs; native stack unverified | Missing current pause/deep/structured UI features |
| Reader/local AI | Most complete | Relatively close but copied | Missing structured Reader/local-LLM hydration parity |
| Input/display | Windows keyboard/tk/Pillow | X11/Wayland permissions and dependencies require native validation | Accessibility/mic/focus permissions require native validation |
| Packaging | Branded but unsigned; stale tracked zip | install script, no native package verified | ZIP/scripts; signing/notarisation not verified/present |
| Updater | Unsigned and 404 | Same authenticity/feed concern | Same authenticity/feed concern |

Windows is not “good while ports are bad”: it is the most complete but contains the confirmed local-STT regression. The copied-tree architecture makes platform quality a moving target. A single shared core/UI with narrow adapters is necessary for credible long-term parity.

## 12. Technical Debt and Redundancy

- Three copied application trees and duplicated Web UI assets.
- 5,107-line controller, 2,416-line API bridge, 2,295-line overlay and 2,496-line fallback UI.
- `ai/__init__.py` remains a 2,381-line implementation/facade hybrid.
- 31 pyflakes F findings, including 17 redefinitions and duplicated TTS/provider functions.
- Legacy `ai.py/model_free.py` architecture remains in ports and stale archive.
- Dead onboarding step 99.
- Broad exception suppression in cleanup/persistence/UI paths often omits diagnostics.
- Manual tracked release archive and no manifest/CI source of truth.
- Stale tests and documentation that report green/accurate behavior while code has moved.
- Unpinned dependencies and no lock/constraints files.

## 13. Version 1.0 Readiness Verdict

**Verdict: Requires architectural intervention before release.**

Mumble is not ready for 1.0 because a core default capability is broken, meeting data can be lost or retained contrary to user intent, privacy copy is inaccurate, the updater lacks authenticity, the deliverable is stale/unsigned, clean installs omit visible-feature dependencies, and the test baseline is red. These are not minor polish items.

The architecture does not require a ground-up rewrite before every fix. Immediate blockers are targeted. However, a cross-platform public 1.0 should not ship while three copied runtime trees and UIs remain the mechanism for parity; they have already produced concrete regressions and materially different products.

## 14. Prioritised Remediation Plan

### 1. Immediate critical fixes

1. Restore/test `sanitize_hotwords`; run real local dictation and cloud-fallback smoke tests.
2. Stop distributing the stale `Mumble.zip`; rebuild only after a clean install/run/uninstall validation.
3. Persist meeting audio durably before processing; make processing resumable and shutdown-safe.
4. Make meeting deletion remove its WAV and migrate orphaned recordings.
5. Make pause/resume UI authoritative and failure-aware.

These meeting items should be one “durable meeting lifecycle” workstream because persistence, shutdown, deletion, filename generation and streamed capture share invariants.

### 2. Version 1.0 blockers

1. Sign update manifests and publish a working feed; fail closed.
2. Reconcile every visible feature with clean-install dependencies; hide Account until complete.
3. Correct Cloud STT and Reader privacy disclosures throughout Home, onboarding, Settings and docs.
4. Lock dependencies, resolve NumPy/pyannote, and make a clean build reproducible.
5. Sign Windows artifacts; establish macOS signing/notarisation and supported Linux packaging strategy.
6. Remove placeholder sharing and contradictory mode/shortcut instructions.

### 3. High-priority reliability work

1. Stream meeting audio to disk instead of RAM; use UUID/exclusive filenames.
2. Exercise rapid start/stop, mic disconnect, sleep/wake, network loss, API error and forced quit.
3. Fix Reader fallback state/error behavior and impose chunk/document limits.
4. Lock settings mutation/save and stress concurrent writers.
5. Replace silent broad exception handling with contextual logging in user/data paths.

### 4. UI and UX improvements

1. Split Settings into Basic/Advanced with progressive disclosure.
2. Implement shared accessible dialogs and semantic meeting/navigation controls.
3. Fix copy feedback, unstar, onboarding validation, status flicker and contrast.
4. Provide accessible chart tables/labels.
5. Conduct native rendered QA at common sizes/scales after functional state is trustworthy.

### 5. Code-quality and architectural improvements

1. Consolidate canonical core/UI; keep platform adapters narrow.
2. Extract recording, transcription, processing/router, IPC and application coordination services from `Mumble`.
3. Reduce `Api`, `Island`, fallback UI and `ai/__init__` incrementally.
4. Remove legacy port architectures and duplicated definitions only after behavioral tests exist.

Platform consolidation and controller extraction should be separate projects: doing both simultaneously would enlarge risk.

### 6. Testing and automation improvements

1. One authoritative runner with offline/unit/integration/live/platform markers.
2. Never read production settings/secrets in tests; use isolated temp data.
3. Make 52/52 offline scripts green or retire obsolete scripts.
4. Add Windows CI first: syntax, lint, clean install, offline tests, release manifest.
5. Add portable macOS/Linux CI and separate real-hardware release sign-off.
6. Add true behavioral tests for core/meeting/update/privacy state rather than source-text checks.

### 7. Longer-term enhancements

1. Local/system Reader TTS and clearer provider data controls.
2. Virtualized Reader rendering.
3. Performance budgets and telemetry-free local diagnostics export.
4. A stable, consumer-ready optional sync service only after privacy/security design review.
5. Product-scope review: keep features that reinforce “speak, it types,” and avoid exposing infrastructure as UX.

## 15. Final Professional Judgement

**Is Mumble well built?** Parts are. The core interaction design, local-first intent, stores, provider abstractions, update extraction protections and many edge-case tests are professionally thoughtful. The complete product is not yet professionally release-controlled.

**Is the foundation strong?** Yes, strong enough to continue. The value proposition and several subsystem boundaries are sound. Do not discard them.

**Does the code appear professionally structured?** Unevenly. Newer subsystems do; the main controller, Web bridge, overlay/fallback UI, AI facade, platform forks and release process do not meet a professional 1.0 maintainability bar.

**Are the problems normal for this stage?** Scope growth, monoliths and stale tests are common before 1.0. A broken default local-transcription call, data-loss window, false deletion/privacy promises, unsigned updater, stale release archive and red baseline are unusually serious if the product is being considered for public 1.0.

**Is continued development on the current architecture sensible?** Yes only with a change in sequence: stop adding surface area, fix trust/data/release blockers, create behavioral safety nets, then consolidate shared platform/core code incrementally. Continuing feature development without that intervention will make the architecture materially more fragile.

**The three most important next actions:**

1. Restore and behaviorally test the complete core dictation path, then establish a green, isolated CI baseline.
2. Redesign Meetings around durable streamed recording, safe shutdown and truthful deletion/pause states.
3. Make distribution and trust real: accurate privacy disclosures, complete dependencies, signed working updates, rebuilt/signed artifacts, and one shared cross-platform core.
