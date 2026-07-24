# Shared Modules Audit: Windows Canonical → Linux Port

**Date:** 2026-07-04
**Windows canonical source:** `C:\Mumble v1\Internal\app\`
**Linux port copy:** `C:\Mumble v1\Internal\app\Ports\Linux\app\`
**Methodology:** SHA-256 byte-level comparison of every file; unified diffs for all divergences.

---

## Executive Summary

| Metric | Value |
|--------|-------|
| Total shared files (exist in both) | 67 |
| Byte-identical | 27 |
| Different | 40 |
| Missing in Linux (Windows-only) | 27 |
| Missing in Windows (Linux-only) | 8 |
| **Sync health (simple files)** | **40.3%** |
| **Sync health (webui/)** | **40.0%** (2/5 identical) |

**Overall assessment:** The Linux port is significantly behind the Windows canonical source. Only 40.3% of shared modules are byte-identical. The divergence is driven by three factors: (a) intentional platform seams in OS-specific modules, (b) substantial feature drift where Windows has received ongoing development that has not been propagated to Linux, and (c) a structural difference where Windows uses Python packages (`ai/`, `eval/`, `models/`, `perf/`, `pipeline/`, `platform/`) while the Linux port uses flattened monolithic `.py` files.

---

## 1. Complete File-by-File Comparison Table

### 1.1 Byte-Identical Files (27 files — CORRECT)

| File | Size (bytes) |
|------|-------------|
| `assets_gen.py` | 1,614 |
| `brand_exe.py` | 9,687 |
| `cloud_schema.sql` | 10,433 |
| `cloud_sync.py` | 51,462 |
| `foreign_boost.py` | 21,718 |
| `history.py` | 13,990 |
| `islamic_terms.py` | 32,567 |
| `mode_select.py` | 6,618 |
| `prompt_constitution.py` | 46,621 |
| `test_big_shift.py` | 5,317 |
| `test_bindings.py` | 5,011 |
| `test_cloud_sync.py` | 22,154 |
| `test_context_store.py` | 3,646 |
| `test_end_to_end.py` | 9,687 |
| `test_favorites.py` | 1,852 |
| `test_foreign_boost.py` | 7,128 |
| `test_lightweight.py` | 9,310 |
| `test_live_context.py` | 10,432 |
| `test_live_modes.py` | 6,060 |
| `test_live_prompts.py` | 10,057 |
| `test_local_wiring.py` | 5,029 |
| `test_mode_select.py` | 3,813 |
| `test_model_free.py` | 5,289 |
| `test_recording_gate.py` | 5,740 |
| `test_settings_merge.py` | 3,511 |
| `test_stats.py` | 12,064 |
| `test_stream_seam.py` | 5,865 |

### 1.2 Divergent Files (40 files — DRIFT OR SEAM)

| File | Win Size | Lin Size | Delta | % Diff | Category |
|------|----------|----------|-------|--------|----------|
| `app_window.py` | 100,660 | 98,323 | -2,337 | 2.3% | Platform seam + feature drift |
| `autostart.py` | 7,553 | 3,964 | -3,589 | 47.5% | Intentional platform rewrite |
| `bindings.py` | 14,495 | 14,154 | -341 | 2.4% | Cosmetic (hotkey doc text) |
| `branding.py` | 13,021 | 12,029 | -992 | 7.6% | Platform cleanup + feature drift |
| `clipboard.py` | 20,116 | 19,635 | -481 | 2.4% | Bug fix / logic change |
| `context_store.py` | 21,170 | 15,659 | -5,511 | 26.0% | **Feature drift (Windows ahead)** |
| `favorites.py` | 4,527 | 4,527 | 0 | ~0% | Cosmetic (docstring only) |
| `formatting.py` | 59,670 | 60,389 | +719 | 1.2% | Feature drift (Linux ahead) |
| `island_render.py` | 52,407 | 47,379 | -5,028 | 9.6% | Feature drift (Windows ahead) |
| `local_engine.py` | 38,435 | 20,000 | -18,435 | 48.0% | **Severe drift (Windows ahead)** |
| `meeting.py` | 31,652 | 14,846 | -16,806 | 53.1% | **Severe drift (Windows ahead)** |
| `meeting_diarise.py` | 23,985 | 9,451 | -14,534 | 60.6% | **Severe drift (Windows ahead)** |
| `meeting_store.py` | 9,094 | 7,859 | -1,235 | 13.6% | Feature drift (Windows ahead) |
| `model_free.py` | 8,910 | 8,920 | +10 | 0.1% | Minor |
| `mumble.py` | 239,765 | 209,385 | -30,380 | 12.7% | **Significant drift (Windows ahead)** |
| `overlay.py` | 101,922 | 95,405 | -6,517 | 6.4% | Feature drift (Windows ahead) |
| `presets.py` | 9,637 | 12,772 | +3,135 | 24.5% | **Feature drift (Linux ahead)** |
| `prompt_memory.py` | 8,108 | 7,939 | -169 | 2.1% | Minor drift |
| `reader_parser.py` | 34,069 | 33,158 | -911 | 2.7% | Minor drift |
| `reader_store.py` | 18,424 | 15,847 | -2,577 | 14.0% | Feature drift (Windows ahead) |
| `requirements.txt` | 155 | 749 | +594 | 79.3% | Intentional (Linux deps) |
| `settings.py` | 34,731 | 33,125 | -1,606 | 4.6% | Feature drift (Windows ahead) |
| `stats.py` | 20,363 | 18,566 | -1,797 | 8.8% | Feature drift (Windows ahead) |
| `test_cmd_auth.py` | 4,114 | 4,037 | -77 | 1.9% | Minor |
| `test_formatting.py` | 25,786 | 25,305 | -481 | 1.9% | Minor |
| `test_island_bar.py` | 6,654 | 4,052 | -2,602 | 39.1% | Test drift |
| `test_local_engine.py` | 10,663 | 7,789 | -2,874 | 27.0% | Test drift |
| `test_meeting.py` | 68,746 | 67,147 | -1,599 | 2.3% | Test drift |
| `test_meeting_controller.py` | 16,016 | 15,553 | -463 | 2.9% | Test drift |
| `test_presets.py` | 4,608 | 4,502 | -106 | 2.3% | Minor |
| `test_reader.py` | 20,418 | 16,705 | -3,713 | 18.2% | Test drift |
| `test_reader_parser.py` | 33,206 | 32,391 | -815 | 2.5% | Minor |
| `test_tips.py` | 8,271 | 3,279 | -4,992 | 60.4% | Test drift (severely stripped) |
| `test_ui.py` | 11,861 | 11,861 | 0 | ~0% | Cosmetic |
| `test_webui_api.py` | 23,408 | 6,842 | -16,566 | 70.8% | **Severely stripped test** |
| `tips.py` | 10,855 | 4,146 | -6,709 | 61.8% | **Severe drift (Windows ahead)** |
| `transcription.py` | 22,621 | 22,094 | -527 | 2.3% | Minor drift |
| `ui.py` | 7,088 | 6,965 | -123 | 1.7% | Minor drift |
| `update.py` | 18,266 | 14,361 | -3,905 | 21.4% | Platform seam + drift |
| `webui_shell.py` | 113,448 | 104,363 | -9,085 | 8.0% | Feature drift (Windows ahead) |

### 1.3 WebUI Directory

| File | Win Size | Lin Size | Status |
|------|----------|----------|--------|
| `app.css` | 118,106 | 98,897 | **DIFFERENT** (+12 / -533 lines) |
| `app.js` | 290,948 | 237,240 | **DIFFERENT** (+278 / -1105 lines) |
| `enhanced.css` | 40,728 | 40,728 | IDENTICAL |
| `index.html` | 203,917 | 186,712 | **DIFFERENT** (+124 / -313 lines) |
| `mumble.png` | 13,646 | 13,646 | IDENTICAL |

**WebUI sync health: 40%** (2/5 identical). The three divergent files all show Windows having significantly more content than Linux, with Linux missing ~1,900+ cumulative lines of CSS/JS/HTML.

---

## 2. Detailed Divergence Analysis

### 2.1 Intentional Platform Seams (EXPECTED — not bugs)

#### `autostart.py` (47.5% diff — COMPLETELY REWRITTEN)
- **Nature:** Intentional platform rewrite. Windows version uses PowerShell + WScript.Shell to create `.lnk` shortcuts in the Startup folder, Start Menu, and Desktop. Linux version implements the XDG autostart spec via `~/.config/autostart/mumble.desktop`.
- **Impact:** None — this is correct. Each platform needs its own autostart mechanism.
- **Assessment:** ✅ INTENTIONAL SEAM

#### `requirements.txt` (79.3% diff — EXPECTED)
- **Windows deps:** `keyboard>=0.13.5`, `mouse>=0.7.1` (Win32 global input hooks)
- **Linux deps:** `pynput>=1.7.6` (cross-platform input), `evdev>=1.6.1` (Wayland/mouse side buttons), plus extensive comments about system packages (PyGObject, GTK typelibs, libportaudio2)
- **Impact:** Correct platform-specific dependency split.
- **Assessment:** ✅ INTENTIONAL SEAM

#### `app_window.py` (2.3% diff — MIXED)
- **Platform seams (correct):**
  - Line ~1093: `os.startfile(p)` → `subprocess.run(["xdg-open", p], timeout=5)` — correct cross-platform file open
- **Feature drift (should be synced):**
  - Line ~213: Window default height 774 → 740 (minor UI tweak)
  - Lines ~478-580: History hotkey changed from `Ctrl+Alt+D` to `Ctrl+Alt+H` globally
  - Lines ~503-512: Added new "list" mode mini-button
  - Line ~1241: Added "list" to mode labels dict
- **Assessment:** ⚠️ MIXED — Platform seam is correct, but feature changes (hotkey rename, new "list" mode) should propagate to Linux if they are intended shared features.

#### `branding.py` (7.6% diff — MIXED)
- **Platform cleanup (correct for Linux):**
  - Removed `APP_DIR`, `MODELS_DIR`, `LLAMA_BIN_DIR`, `llama_cli_path()` — these reference Windows-specific `llama-cpp-bin/` directory and `.exe` binaries
  - Removed `get_hardware_info()` and `app_info()` — Windows-specific hardware detection
- **Feature drift (should be synced):**
  - Added "list" mode color (`#46C9A8` teal), label, and spoken commands
  - Added "reply" mode card definition
  - Ctrl+Alt+D → Ctrl+Alt+H hotkey text
  - "first ... second ... finally" and "make a list of a, b and c" spoken commands
- **Assessment:** ⚠️ MIXED — Platform cleanup is correct. "List" mode and new spoken commands should be synced.

#### `clipboard.py` (2.4% diff — BUG FIX PORT)
- **Change:** Image clipboard poll throttling logic changed. Windows version had a conditional `if not have_seq and ...` that skipped image grabs on platforms without sequence counters. Linux version simplified to `if (self._img_tick % self._img_every) != 0:` — always throttling, regardless of sequence counter availability.
- **Impact:** Linux version actually has the CORRECT behavior. The Windows version's `have_seq` branch was a workaround that could miss image copies.
- **Assessment:** 🔧 This appears to be a bug fix in Linux that should be BACKPORTED to Windows, not the other way around. Or they represent independent fix attempts.

#### `update.py` (21.4% diff — PARTIALLY INTENTIONAL)
- Windows version generates `.bat` swap scripts, uses `ctypes.windll` for app-root detection, and carries Windows venv update mechanisms.
- Linux version uses `.sh` swap scripts with `kill`/`mv`/Python relaunch.
- **Assessment:** ⚠️ MIXED — Platform-specific update logic is correct, but common logic (version checking, download, hash verification) should be shared.

### 2.2 Feature Drift — Windows Ahead (BUGS/DRIFT — needs sync)

#### `local_engine.py` (48.0% diff — SEVERE)
- **Delta:** 38,435 → 20,000 bytes (-472 lines in Linux)
- Windows has nearly 2x the code. This suggests significant engine improvements on Windows (model discovery, backend management, fallback logic) that never reached Linux.
- **Impact:** HIGH — The Linux port may have degraded local LLM capabilities, missing model discovery paths, or different backend selection logic.
- **Assessment:** 🔴 DRIFT — Must be investigated and synced.

#### `meeting.py` (53.1% diff — SEVERE)
- **Delta:** 31,652 → 14,846 bytes (-448 lines in Linux)
- Windows has >2x the meeting logic. Likely missing meeting modes, processing stages, or recording management.
- **Impact:** HIGH — Meeting functionality on Linux may be incomplete.
- **Assessment:** 🔴 DRIFT — Must be investigated and synced.

#### `meeting_diarise.py` (60.6% diff — SEVERE)
- **Delta:** 23,985 → 9,451 bytes (-338 lines in Linux)
- Windows has ~2.5x the diarisation logic. Linux appears to have a stripped-down version.
- **Impact:** HIGH — Speaker diarisation on Linux may be significantly degraded.
- **Assessment:** 🔴 DRIFT — Must be investigated and synced.

#### `tips.py` (61.8% diff — SEVERE)
- **Delta:** 10,855 → 4,146 bytes (-185 lines in Linux)
- Linux has only ~38% of the tips content. Many tips removed.
- **Impact:** MEDIUM — Users miss onboarding guidance.
- **Assessment:** 🟡 DRIFT — Lost content, should be synced.

#### `context_store.py` (26.0% diff — SIGNIFICANT)
- **Delta:** 21,170 → 15,659 bytes (-107 lines in Linux)
- Linux version missing `'ui'` field in capture result (documented in TEST_PARITY.md).
- **Impact:** MEDIUM — UI integration for context capture may be broken on Linux.
- **Assessment:** 🟡 DRIFT — Should be investigated and synced.

#### `mumble.py` (12.7% diff — SIGNIFICANT)
- **Delta:** 239,765 → 209,385 bytes (-560 lines in Linux)
- 560 lines removed. This is the main application orchestrator — a 12.7% reduction is substantial.
- **Impact:** HIGH — Core application logic diverged. May affect recording pipeline, mode dispatch, or state management.
- **Assessment:** 🔴 DRIFT — Needs thorough review.

#### `meeting_store.py` (13.6% diff — MODERATE)
- Linux meeting records lack `key_decisions`, `open_questions`, and `processing_mode` fields (documented in TEST_PARITY.md).
- **Impact:** MEDIUM — Meeting data model incomplete.
- **Assessment:** 🟡 DRIFT — Missing fields should be added.

#### `reader_store.py` (14.0% diff — MODERATE)
- Linux missing `save_doc_parsed()` function (documented in TEST_PARITY.md).
- **Impact:** MEDIUM — Document parsing pipeline may not store results correctly.
- **Assessment:** 🟡 DRIFT — Missing function should be added.

### 2.3 Feature Drift — Linux Ahead (DRIFT — needs assessment)

#### `presets.py` (24.5% diff — LINUX HAS MORE)
- **Delta:** 9,637 → 12,772 bytes (+104 lines in Linux)
- Linux has MORE preset definitions than Windows.
- **Impact:** LOW-MEDIUM — Linux may have presets Windows users can't access; or Windows has intentionally fewer.
- **Assessment:** 🟡 Needs investigation — determine whether Linux additions should be ported to Windows.

#### `formatting.py` (1.2% diff — LINUX HAS SLIGHTLY MORE)
- **Delta:** 59,670 → 60,389 bytes (+76 lines in Linux)
- Minor additional formatting logic in Linux.
- **Impact:** LOW — Small formatting behavior difference.
- **Assessment:** 🟢 Minor — should be investigated but low risk.

### 2.4 Cosmetic / Documentation-Only Drift (LOW IMPACT)

| File | Nature of Difference |
|------|---------------------|
| `bindings.py` | Ctrl+Alt+D → Ctrl+Alt+H in a docstring/comment |
| `favorites.py` | Docstring only: "ctrl+alt+d" → "Ctrl+Alt+H" |
| `model_free.py` | <1% diff — minimal |
| `prompt_memory.py` | ~2% diff — minor |
| `reader_parser.py` | ~3% diff — minor |
| `test_cmd_auth.py` | ~2% diff — minor |
| `test_formatting.py` | ~2% diff — minor |
| `test_presets.py` | ~2% diff — minor |
| `test_ui.py` | Same size, different hash — cosmetic/docstring only |
| `transcription.py` | ~2% diff — minor |
| `ui.py` | ~2% diff — minor |

**Assessment for cosmetic diffs:** These are mostly the `Ctrl+Alt+D` → `Ctrl+Alt+H` hotkey rename that has propagated inconsistently. This should be resolved ONE WAY (pick a standard hotkey) and synced everywhere.

### 2.5 Test File Drift

| File | Delta | Assessment |
|------|-------|------------|
| `test_island_bar.py` | 39.1% diff (-2,602B) | Linux version heavily stripped |
| `test_local_engine.py` | 27.0% diff (-2,874B) | Tests don't match Linux's `local_engine.py` API |
| `test_meeting.py` | 2.3% diff (-1,599B) | Minor — likely field name differences |
| `test_meeting_controller.py` | 2.9% diff (-463B) | Linux lacks `_meeting_start()` method |
| `test_reader.py` | 18.2% diff (-3,713B) | Missing `save_doc_parsed` tests |
| `test_reader_parser.py` | 2.5% diff (-815B) | Minor |
| `test_tips.py` | 60.4% diff (-4,992B) | Severely stripped — most Windows tips tests removed |
| `test_webui_api.py` | 70.8% diff (-16,566B) | **Severely stripped** — Linux has only 29% of Windows test coverage |

**Assessment:** Test drift is a significant concern. Several test files have been severely reduced in the Linux port, reducing test coverage. The Linux `TEST_PARITY.md` documents some of these exclusions as intentional (missing API features), but several files appear to have been arbitrarily trimmed.

---

## 3. Missing Files

### 3.1 Files in Windows but NOT in Linux (27 files — SEVERITY VARIES)

#### High Severity (shared logic missing from Linux):
| File | Size | Reason |
|------|------|--------|
| `test_api_stability.py` | 23,348 | API stability regression tests |
| `test_cache.py` | 21,069 | Model cache tests |
| `test_constitution.py` | 12,677 | Constitution/prompt tests |
| `test_downloader.py` | 37,331 | Model downloader tests |
| `test_error_recovery.py` | 37,807 | Error recovery tests |
| `test_eval.py` | 17,638 | Eval harness tests |
| `test_eval_reporter.py` | 24,235 | Eval reporting tests |
| `test_fallback.py` | 26,194 | Fallback mode tests |
| `test_foreign_mode_cross.py` | 7,429 | Cross-mode foreign tests |
| `test_model_backend.py` | 31,374 | Model backend tests |
| `test_models.py` | 16,532 | Model infrastructure tests |
| `test_output_quality.py` | 21,849 | Output quality tests |
| `test_perf.py` | 47,790 | Performance tests |
| `test_pipeline.py` | 13,614 | Pipeline stage 1 tests |
| `test_pipeline_stage2_stage3.py` | 21,735 | Pipeline stages 2-3 tests |
| `test_providers.py` | 43,320 | AI provider tests |
| `test_settings_recovery.py` | 5,789 | Settings recovery tests |
| `test_stage_cleanup.py` | 17,383 | Stage cleanup tests |
| `test_stt_providers.py` | 4,834 | STT provider tests |
| `test_transcription_diag.py` | 19,343 | Transcription diagnostics tests |
| `test_transcription_edge_cases.py` | 30,715 | Transcription edge case tests |
| `test_tts_providers.py` | 9,926 | TTS provider tests |
| `test_update.py` | 3,911 | Update mechanism tests |

#### Medium Severity (Windows-specific, expected):
| File | Size | Reason |
|------|------|--------|
| `test_macos_audio.py` | 19,065 | macOS-specific audio tests |
| `test_windows_dml.py` | 17,595 | Windows DirectML tests |

#### Low Severity (Windows installer/ops):
| File | Size | Reason |
|------|------|--------|
| `install.ps1` | 7,409 | Windows PowerShell installer |
| `uninstall.ps1` | 4,468 | Windows PowerShell uninstaller |

**Note:** The Linux `TEST_PARITY.md` documents that many test exclusions are intentional because the Linux port lacks the corresponding production modules or APIs. However, this creates a coverage gap — the excluded tests would catch regressions if the missing functionality is later added.

### 3.2 Files in Linux but NOT in Windows (8 files)

| File | Size | Category |
|------|------|----------|
| `mumble_linux.py` | 188,892 | **Linux entry point** — platform-specific main launcher |
| `overlay_linux.py` | 42,270 | **Linux overlay** — platform-specific overlay implementation |
| `ai.py` | 111,017 | **Flattened monolith** — Linux flattens `ai/` package into single file |
| `test_linux_platform.py` | 8,473 | **Linux platform tests** — tests Linux-specific functionality |
| `run_tests.py` | 1,818 | **Test runner** — Linux-specific test orchestrator |
| `_rebuild_zip.py` | 3,105 | **Build script** — Linux distribution zip builder |
| `TEST_PARITY.md` | 3,372 | **Documentation** — test parity documentation |
| `READ ME FIRST.txt` | 1,831 | **Documentation** — Linux port instructions |

**Assessment:** All Linux-only files are justified platform additions. `ai.py` (111K) is a flattened version of Windows' `ai/` package (43 files across `ai/` and `ai/providers/`). The `mumble_linux.py` and `overlay_linux.py` are the Linux-specific entry point and overlay implementations.

---

## 4. Package Structure Divergence

Windows uses a modular package structure where Linux uses flattened monoliths:

| Windows Package | Files | Linux Counterpart | Size |
|----------------|-------|-------------------|------|
| `ai/` (11 files) | `__init__.py` (115K), `base.py`, `constitution.py`, `stt_providers.py`, `transport.py`, `tts_providers.py`, `providers/` (7 files) | `ai.py` (111K) | Flattened |
| `eval/` (5 files) | `__init__.py`, `harness.py`, `reporter.py`, `metrics.py`, `scenarios.py` | *(no Linux counterpart)* | Missing entirely |
| `models/` (6 files) | `__init__.py`, `registry.py`, `manager.py`, `downloader.py`, `cache.py`, `backend.py` | *(no Linux counterpart)* | Missing entirely |
| `perf/` (4 files) | `__init__.py`, `diagnostics.py`, `profiler.py`, `optimizer.py` | *(no Linux counterpart)* | Missing entirely |
| `pipeline/` (6 files) | `__init__.py`, `stage_punctuation.py`, `stage_grammar.py`, `stage_formatting.py`, `stage_cleanup.py`, `grammar.py` | *(no Linux counterpart)* | Missing entirely |
| `platform/` (3 files) | `__init__.py`, `windows_dml.py`, `macos_audio.py` | *(no Linux counterpart)* | Missing entirely |

**Impact:** This is a structural design decision — the Linux port uses flattened monoliths instead of packages. The `eval/`, `models/`, `perf/`, `pipeline/`, and `platform/` packages have NO Linux counterparts at all. This means:
- No eval harness on Linux
- No model downloader/manager on Linux
- No performance profiling on Linux
- No pipeline stages on Linux (punctuation, grammar, formatting stages are presumably inlined elsewhere)
- No platform abstraction layer on Linux

This is by far the largest architectural gap between the two ports.

---

## 5. Requirements.txt Comparison

| Aspect | Windows | Linux |
|--------|---------|-------|
| Total lines | 9 (155 bytes) | 17 (749 bytes) |
| Shared dependencies | 7 | 7 |
| Windows-only | `keyboard>=0.13.5`, `mouse>=0.7.1` | — |
| Linux-only | — | `pynput>=1.7.6`, `evdev>=1.6.1` |
| Comments | None | Extensive (system package notes) |

**Shared deps (version-identical):** `faster-whisper`, `sounddevice`, `numpy`, `pyperclip`, `pystray`, `Pillow`, `pywebview`

**Assessment:** ✅ Dependency split is correct. Windows uses `keyboard`+`mouse` for global input hooks; Linux uses `pynput`+`evdev`. The Linux requirements.txt is more thoroughly documented with system package guidance.

---

## 6. Summary: Overall Sync Health

### Simple Shared Files
```
Byte-identical:  27 / 67  =  40.3%
Different:       40 / 67  =  59.7%
```

### Test Files Only
```
Byte-identical:  15 / 32  =  46.9%  (of shared test files)
Different:       17 / 32  =  53.1%
Missing (Win→Lin): 23 test files not in Linux
```

### WebUI
```
Byte-identical:   2 /  5  =  40.0%
Different:        3 /  5  =  60.0%
```

### Overall Assessment

| Severity | Count | Description |
|----------|-------|-------------|
| 🔴 CRITICAL | 6 | `local_engine.py`, `meeting.py`, `meeting_diarise.py`, `mumble.py`, `tips.py`, `webui/` (3 files) — >10% drift in core modules |
| 🟡 HIGH | 8 | `context_store.py`, `meeting_store.py`, `reader_store.py`, `island_render.py`, `overlay.py`, `stats.py`, `update.py`, `webui_shell.py` — 5-15% drift |
| 🟢 MEDIUM | 6 | `branding.py`, `clipboard.py`, `presets.py`, `formatting.py`, `settings.py`, `app_window.py` — mixed platform seams + drift |
| ⚪ LOW | 20 | Cosmetic/docstring changes, minor test diffs |

### Recommendations

1. **IMMEDIATE: Sync `local_engine.py`** — 48% divergence in the local AI engine is unacceptable. Determine what features Windows gained and propagate.

2. **IMMEDIATE: Sync meeting modules** — `meeting.py` (53%), `meeting_diarise.py` (61%), `meeting_store.py` (14%) are severely behind on Linux.

3. **HIGH: Resolve hotkey inconsistency** — `Ctrl+Alt+D` vs `Ctrl+Alt+H` appears across 8+ files. Standardize on one and update everywhere.

4. **HIGH: Decide on package vs monolith architecture** — The Linux port flattens `ai/` into `ai.py` but entirely omits `eval/`, `models/`, `perf/`, `pipeline/`, and `platform/`. Decide whether these should be ported.

5. **HIGH: Port the "list" mode** — It exists in Windows `app_window.py` and `branding.py` but not consistently in Linux. If it's a shared feature, sync it.

6. **MEDIUM: Backport Linux clipboard fix to Windows** — The simplified image poll throttling in Linux `clipboard.py` may be the correct fix.

7. **MEDIUM: Audit `presets.py`** — Linux has MORE presets than Windows. Decide which is canonical.

8. **LOW: Sync webui/** — Three webui files are significantly behind. Copy them verbatim from Windows.

9. **LOW: Evaluate test coverage** — 23 Windows test files missing from Linux. While many are documented as intentionally excluded, the coverage gap is large.

---

*Report generated by automated byte-level SHA-256 comparison with unified diff analysis.*
*Raw comparison data: `C:\Mumble v1\mission\audit-fixes\_comparison_data.json`*
