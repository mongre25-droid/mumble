# Linux Controller Audit: `mumble_linux.py` vs Canonical `mumble.py`

> Generated: 2026-07-04 • Source: `Internal/app/Ports/Linux/app/mumble_linux.py` (4089 lines) vs `Internal/app/mumble.py` (4994 lines)

---

## 1. Structural Gaps vs Windows

### 1.1 Imports present in Windows, missing from Linux

| Windows import (line ~) | Purpose | Linux status | Severity |
|---|---|---|---|
| `import meeting` (97) | Meeting/transcription recording | **MISSING** | **CRITICAL** |
| `from overlay import Island` (93) | Windows Tk overlay island | Uses `from overlay_linux import Island` instead — correct | OK |
| `import tkinter as tk` (64) | Tk root window | Uses `_GlibRoot` + GTK — correct | OK |

### 1.2 Imports present in Linux, not in Windows

| Linux import (line) | Purpose | Notes |
|---|---|---|
| `import gi` / `from gi.repository import Gtk, GLib` (80-82) | GTK GUI toolkit | Correct platform-specific |
| `import mode_select` (155) | Control-gated mode-window state machine | **DEAD CODE** — imported but never used; Windows also imports it but it's explicitly documented as legacy. Should not import on Linux. |

### 1.3 Methods in Windows but missing or stubbed on Linux

| Method | Windows line | Purpose | Linux status | Severity |
|---|---|---|---|---|
| `__init__` → meeting recorder state | 207-209 | `self.meeting_recorder = None; self.meeting_recording = False` | **MISSING** | **CRITICAL** |
| `_meeting_island_cb` | 283-304 | Island callback for meeting recording (timer, speaker count) | **MISSING** | **CRITICAL** |
| `_meeting_start` | 361-374 | Start meeting recording | **MISSING** | **CRITICAL** |
| `_meeting_stop` | 376-401 | Stop meeting recording + background processing | **MISSING** | **CRITICAL** |
| `_meeting_import` | 403-417 | Import external audio as meeting | **MISSING** | **CRITICAL** |
| `_bump_feature` | 306-314 | Feature-adoption counter for tips | **MISSING** | HIGH |
| `_tip_usage` | 316-357 | Usage snapshot for island tips gating | **MISSING** | HIGH |
| `_maybe_island_tip` | 359-382 | "Occasionally surface a spaced, self-retiring island TIP" | **MISSING** | HIGH |
| `_show_island_tip` | 384-388 | Main-thread tip show after flash clears | **MISSING** | HIGH |
| `_fire_island_tip` | 390-396 | Fire the island tip hint | **MISSING** | HIGH |
| `_init_local_llm` | ~3840-3880 | Wire on-device LLM backend at boot | **MISSING** | HIGH |
| `local_llm_status` | ~3882-3893 | Report on-device LLM state | **MISSING** | HIGH |
| `_local_llm_generate` | ~3895-3912 | On-device smart-mode shaping via local LLM | **MISSING** | HIGH |
| `island_modes` | ~3990-3996 | Island mode deck offerings (prompt/email/reply) | **MISSING** | HIGH |
| `_push_island_bar_state` | ~3998-4010 | Push live mode-deck snapshot to island | **MISSING** | HIGH |
| `set_active_mode` | ~4012-4037 | Select/clear island-forced processing mode | **MISSING** | HIGH |
| `toggle_island_foreign` | ~4039-4054 | Flip Foreign mode from island toggle | **MISSING** | HIGH |
| `set_prompt_mode` | ~3961-3965 | "THE BIG SHIFT: set sticky Prompt toggle" | **MISSING** | HIGH |
| `toggle_prompt_mode` | ~3967-3970 | Flip Prompt toggle | **MISSING** | HIGH |
| `capture_conversation` | ~3220-3245 | ITEM 5: grab focused AI conversation as context | **MISSING** | HIGH |
| `_capture_conversation_quiet` | ~3170-3218 | Ctrl+A then Ctrl+C the focused chat | **MISSING** | HIGH |
| `_conv_context_for_prompt` | ~3500-3515 | Conversation context for Prompt Mode | **MISSING** | HIGH |
| `_resolved_tier` | ~2660-2681 | Effective hardware tier (auto-detect CPU/RAM) | **MISSING** | HIGH |
| `_apply_hardware_model` | ~2683-2725 | Hardware-aware model selection from tier+language | **MISSING** | HIGH |
| `_collect_text` → TRUNC_MARKER handling | 3417-3428 | Handle truncated AI responses | **MISSING** (Linux uses shared module's _collect_text) | MEDIUM |

### 1.4 The `_handle_second_opinion` method differs

**Windows** (line ~3520): accepts `cfg` and `prompt_cfg` parameters (frozen AI config), includes `_conv_context_for_prompt` for the redo path, handles `ai_mode in ("email", "reply")` (no "list").

**Linux** (line ~3120): does NOT accept `cfg`/`prompt_cfg` parameters, handles `ai_mode in ("email", "list", "reply")` (still has "list" which was retired on Windows). No conversation context injection.

### 1.5 The `_generate` method differs

**Windows**: 
- Accepts `config_snap` and uses it properly for `pro_mode` and `format_enabled`
- Has `_local_llm_generate` fallback before the builder
- Has `pipeline.get_orchestrator()` fallback before the builder  
- Freezes AI config at call start for VAL-CROSS-011
- Injects `_conv_context_for_prompt` for prompt mode

**Linux**:
- Accepts `config_snap` but doesn't consistently use all fields
- No local LLM fallback
- No pipeline orchestrator fallback
- No conversation context injection
- No frozen AI config snapshot

### 1.6 The `_offer_mode_pick` method differs

**Windows** (line ~870): `modes = ("text", "prompt", "email", "reply")` — List removed.

**Linux** (line ~810): `modes = ("text", "prompt", "email", "list", "reply")` — "list" still present even though List mode was retired.

---

## 2. Feature Parity Issues

### 2.1 Meeting Mode — CRITICAL

| What | Windows | Linux | Severity |
|---|---|---|---|
| Meeting recorder object | Created in `run()`: `self.meeting_recorder = meeting.MeetingRecorder(...)` | **Not created** | CRITICAL |
| Meeting state in `__init__` | `self.meeting_recorder = None; self.meeting_recording = False` | **Missing** | CRITICAL |
| Meeting commands in cmd server | `meeting_record_start`, `meeting_record_stop`, `meeting_record_pause`, `meeting_record_resume`, `meeting_import_audio` | **Missing** | CRITICAL |
| Meeting island callback | `_meeting_island_cb` shows timer, speaker count on island | **Missing** | CRITICAL |
| Dictation veto during meeting | `start_recording` checks `meeting_recording` and vetoes | **Missing** | HIGH |

**Impact**: The entire meeting recording feature is absent from Linux. Users cannot record, transcribe, or diarize meetings on Linux.

### 2.2 Island Mode Deck — HIGH

| What | Windows | Linux | Severity |
|---|---|---|---|
| `island_modes()` method | Returns (key, label) pairs for island mode chips | **Missing** | HIGH |
| `_push_island_bar_state()` | Pushes live mode-deck snapshot to island widget | **Missing** | HIGH |
| `set_active_mode(key)` | Select or clear island-forced processing mode | **Missing** | HIGH |
| `toggle_island_foreign()` | Flip Foreign mode toggle from island | **Missing** | HIGH |
| Island widget callback wiring | `self.island.set_widget_callbacks(on_mode=..., on_deck=..., on_foreign=...)` | **Missing** | HIGH |
| Prompt mode sync | `prompt_mode_enabled` synced with `active_mode` | **Missing** | HIGH |

**Impact**: The island mode deck (clickable chips for Prompt/Email/Reply/Foreign) is the primary UI for selecting processing mode. On Linux, users can only use the web Settings toggle for Prompt mode — there is no Email, Reply, or Foreign mode selection surface. The `active_mode` field is set in `__init__` but never pushed to the island widget.

### 2.3 Island Tips — HIGH

| What | Windows | Linux | Severity |
|---|---|---|---|
| `_bump_feature(name)` | Nudge feature-adoption counter so tips retire | **Missing** | HIGH |
| `_tip_usage()` | Assemble usage snapshot for tip gating | **Missing** | HIGH |
| `_maybe_island_tip(landed)` | Occasionally surface island tip after dictation lands | **Missing** | HIGH |
| `_show_island_tip(text)` | Show tip in island hint chip after flash clears | **Missing** | HIGH |
| `_fire_island_tip(text)` | Fire the actual tip on island | **Missing** | HIGH |
| `import tips` call | In `_maybe_island_tip` | **Missing** | HIGH |
| `_bump_feature("quick_paste")` call | In `on_quick_paste` | **Missing** | MEDIUM |
| `_bump_feature("deck")` call | In `on_open_history` | **Missing** | MEDIUM |
| `_bump_feature("search")` call | In `on_search_hotkey` | **Missing** | MEDIUM |
| `_bump_feature("preset_run")` call | In `_run_deck_job` | **Missing** | MEDIUM |
| `_maybe_island_tip(pasted)` call | In `_process` after paste | **Missing** | HIGH |
| `_maybe_island_tip` call | In `_finalize_text` | **Missing** | HIGH |

**Impact**: No usage-aware island tips on Linux. Tips never show, never retire, and feature-adoption counters are never bumped. The island tips system (which surfaces features the user hasn't yet discovered) is completely absent.

### 2.4 Conversation Capture — HIGH

| What | Windows | Linux | Severity |
|---|---|---|---|
| `capture_conversation()` | ITEM 5: grab focused AI conversation as context | **Missing** | HIGH |
| `_capture_conversation_quiet()` | Ctrl+A then Ctrl+C focused chat, restore clipboard | **Missing** | HIGH |
| `_conv_context_for_prompt()` | Format conversation context for Prompt Mode | **Missing** | HIGH |
| `grab_selection` cmd handler | In `_start_cmd_server` | **Missing** | HIGH |
| `capture_conversation` cmd handler | In `_start_cmd_server` | **Missing** | HIGH |
| Conversation auto-injection in Prompt | In `_generate` → `_conv_context_for_prompt` | **Missing** | HIGH |
| Conversation auto-injection in Reply | In `_gather_context` using `conv_store.has_conversation()` | **Missing** | HIGH |

**Impact**: Users cannot capture entire AI chat threads as prompting context on Linux. The "Capture chat" feature (ITEM 5 from the owner) doesn't exist.

### 2.5 On-Device LLM Backend — HIGH

| What | Windows | Linux | Severity |
|---|---|---|---|
| `_init_local_llm()` | Wire LLM backend at boot | **Missing** | HIGH |
| `local_llm_status()` | Report LLM state for diagnostics | **Missing** | HIGH |
| `_local_llm_generate()` | Local LLM smart-mode shaping | **Missing** | HIGH |
| LLM call in `_generate` | `_local_llm_generate` fallback before builder | **Missing** | HIGH |

**Impact**: On-device LLM (llama.cpp GGUF models) cannot be used for smart-mode shaping on Linux even if a GGUF file is present. This is a significant offline-capability gap.

### 2.6 Hardware-Tier Model Selection — HIGH

| What | Windows | Linux | Severity |
|---|---|---|---|
| `_resolved_tier()` | Auto-detect CPU/RAM to pick right model tier | **Missing** | HIGH |
| `_apply_hardware_model()` | Resolve model from tier + language + English-only | **Missing** | HIGH |
| `_stt_device`, `_stt_compute`, `_stt_model` | Performance diagnostic tracking | **Missing** | MEDIUM |
| `ai.set_language_context()` | Cloud constitution language-aware | **Missing** | HIGH |

**Impact**: Linux always defaults to "base.en" (line 258). There is no hardware-aware auto-selection. A low-end Linux machine gets the same default as a workstation. The Windows default is "small.en" (better quality), but Linux users on weak hardware can't auto-downgrade and users on strong hardware can't auto-upgrade.

### 2.7 Pipeline Orchestrator Fallback — MEDIUM

| What | Windows | Linux | Severity |
|---|---|---|---|
| `pipeline.get_orchestrator()` fallback | In `_generate`: 4-stage merged-model pipeline before builder | **Missing** | MEDIUM |

**Impact**: The merged-model pipeline (punctuation → grammar → formatting → cleanup via GGUF models) is available on Windows but not wired on Linux. Falls through directly to the deterministic builder.

### 2.8 Confidence Gating & Hotword Sanitization — MEDIUM

| What | Windows | Linux | Severity |
|---|---|---|---|
| `formatting.sanitize_hotwords(terms)` | Sanitize vocabulary terms before STT | **Missing** | MEDIUM |
| `formatting.gate_segment(s)` | Drop hallucination segments based on confidence | **Missing** | MEDIUM |
| `self.settings.get("confidence_gating", True)` check | Gate low-confidence segments | **Missing** | MEDIUM |

**Impact**: Linux doesn't sanitize vocabulary terms before passing them to STT (potential prompt injection via special chars). It also doesn't gate low-confidence segments, which may result in more hallucinations in the transcription output.

### 2.9 TRUNC_MARKER Handling — LOW

| What | Windows | Linux | Severity |
|---|---|---|---|
| `ai.TRUNC_MARKER` handling | Notifies user when AI response truncated | **Missing** | LOW |

**Linux uses the same shared `ai._collect_text` but the Windows controller has additional handling for truncation markers. The shared module may handle this already, but the user notification when truncation occurs is missing.

### 2.10 Memory/GC After Dictation — MEDIUM

| What | Windows | Linux | Severity |
|---|---|---|---|
| `gc.collect()` after dictation | Aggressive GC after each dictation | **Missing** | MEDIUM |
| `gc.collect()` after model swap | Free old model memory | **Missing** | MEDIUM |
| `gc.collect()` after boot | Reclaim transient allocations | **Missing** | MEDIUM |

**Impact**: Linux may hold more idle memory than Windows, particularly after model swaps or long dictation sessions. The VAL-PERF-003 target (<1GB idle) may not be met.

### 2.11 System Sleep Detection — LOW

| What | Windows | Linux | Severity |
|---|---|---|---|
| Sleep detection in `_audio_cb` | Detect >2s gap in audio callbacks as system sleep, discard stale buffer | **Missing** | LOW |

**Impact**: If the system goes to sleep mid-recording on Linux, stale pre-sleep audio may produce garbled transcription on wake.

### 2.12 Auto-Update Behavior

The update module (`import update`) is imported and used identically in both controllers. The Linux `_on_check_updates` handler auto-starts install (no modal dialog since GTK has no tkinter.messagebox). The `_on_update_install` and `_update_progress` methods work correctly. However, `_restart` is noted as "stubbed on Linux for now" (comment at line 3887), meaning the auto-update restart flow may not fully work.

### 2.13 `mode_select` Import

Both controllers import `mode_select` (line 155 Linux, line 93 Windows). On Windows, the Big Shift explicitly retired mode detection — `_process` no longer uses `mode_select`. Linux imports it but the import is dead code (no usage anywhere in the file). Should be removed.

---

## 3. Bug / Logic Issues Found

### 3.1 CRITICAL: `bindings._session_type()` doesn't exist

**File**: `mumble_linux.py`, line 3393
**Code**: `wayland = bindings._session_type() == "wayland"`
**Issue**: The Linux `bindings.py` module does NOT define `_session_type()`. This call will raise `AttributeError` whenever `copy_image()` is invoked.
**Fix**: Either add `_session_type()` to bindings.py (checking `WAYLAND_DISPLAY` or `XDG_SESSION_TYPE`), or inline the check in `copy_image()`.

### 3.2 Outdated default model

**File**: `mumble_linux.py`, line 258
**Code**: `self.model_name = self.settings.get("model", "base.en")`
**Issue**: Linux defaults to "base.en" while Windows defaults to "small.en" (line 140). The Windows default was upgraded for better quality but Linux was not updated to match. This means Linux users get lower-quality transcription by default.

### 3.3 Outdated default history hotkey

**File**: `mumble_linux.py`, line 260
**Code**: `self.history_hotkey = self.settings.get("history_hotkey", "ctrl+alt+h")`
**Issue**: The owner (v9) changed the History hotkey from Ctrl+Alt+H to Ctrl+Alt+D. Windows reflects this (line 142: `"ctrl+alt+d"`). Linux still uses `"ctrl+alt+h"`. The help text in `_process` flash also references the wrong hotkey: "Saved · Ctrl+Alt+H" (Linux line ~1310) vs "Saved · Ctrl+Alt+D" (Windows).

### 3.4 "List" mode still in `_offer_mode_pick` tuple

**File**: `mumble_linux.py`, line ~810
**Code**: `modes = ("text", "prompt", "email", "list", "reply")`
**Issue**: Windows removed "list" from this tuple (line ~870: `("text", "prompt", "email", "reply")`). List mode was retired. Linux still includes it, meaning the mode picker chip could offer a mode that no longer has a dedicated processing lane.

### 3.5 `_offer_mode_pick` "list" mode fallthrough

Related to #3.4: if "list" is offered in the picker and selected, it would route through `_reprocess` → `_generate` → `_cloud_generate`, which on Linux still has `if mode_hint == "list":` (line ~3030). Windows dropped the `list` lane from `_cloud_generate`, routing it through the plain text path instead. Linux hasn't removed it.

### 3.6 `stop_recording` idempotency

**File**: `mumble_linux.py`, line 917
**Issue**: Unlike Windows (line 996-1002), Linux `stop_recording` has no idempotent guard. A double hotkey press could enter `stop_recording` twice before `self.recording` flips, causing `np.concatenate(self.frames)` on an already-cleared list — resulting in `"need at least one array to concatenate"`. Windows guards this with:

```python
with self.lock:
    if not self.recording:
        return
    self.recording = False
```

### 3.7 `on_hotkey` missing `_processing` guard

**File**: `mumble_linux.py`, line 1497
**Issue**: Linux `on_hotkey` doesn't check `self._processing`. Windows does (line 1711-1718):

```python
if self._processing:
    # Previous dictation is still transcribing/pasting — starting now
    # would corrupt shared stream state + open a second mic stream.
    return None
```

This means on Linux, pressing the hotkey while a previous dictation is still in its transcribe→AI→paste pipeline could start a new recording on top of the in-flight one.

### 3.8 `_safe_start` missing cleanup on error

**File**: `mumble_linux.py`, line 1520
**Issue**: Linux `_safe_start` doesn't clean up a partially-started stream on error. Windows does:

```python
if self.recording:
    try:
        self.stop_recording()
    except Exception:
        pass
```

### 3.9 `_paste_impl` clipboard resume missing exception handler

**File**: `mumble_linux.py`, line ~1420
**Issue**: Windows version has `try/except` around `self.clipboard.resume(skip_current=True)`:

```python
try:
    self.clipboard.resume(skip_current=True)
except Exception as e:
    print("clipboard resume error after selection grab:", e)
```

Linux version is missing this try/except, meaning a clipboard resume error could propagate.

### 3.10 `_quit` missing crash recovery

**File**: `mumble_linux.py`, line 3780
**Issue**: Windows `_quit` saves partial dictation history, stops local LLM backend, and cancels active model downloads before shutting down. Linux `_quit` does none of these — a mid-dictation quit would lose the captured audio/text.

### 3.11 `_toggle_pause` missing auto-stop of recording

**File**: `mumble_linux.py`, line ~3710
**Issue**: Windows `_toggle_pause` auto-stops any active recording when pausing. Linux doesn't:

```python
if self.paused and self.recording:
    with self.lock:
        self.busy = True
    threading.Thread(target=self._safe_stop, daemon=True).start()
```

### 3.12 `_GlibRoot` headless fallback thread safety

**File**: `mumble_linux.py`, lines 125-133
**Issue**: The headless fallback uses `threading.Timer` but this callback doesn't marshal onto any main thread. If GTK/GLib fails to import and the app runs headless, `_tk_schedule` puts items on a queue but the GLib-free `_pump` loop (which uses `self.root.after()`) will never drain them because `Gtk` is None and the fallback `t.sleep(3600)` loop doesn't process the queue. Similar issues exist for all island-related calls.

### 3.13 `_pump` won't process `_tk_queue` in headless mode

Related to #3.12: when `Gtk` is `None`, `_GlibRoot.mainloop()` enters an infinite sleep loop. The `_pump` method would still need to be re-scheduled via `self.root.after()`, but `_GlibRoot.after()` falls back to a `Timer` which doesn't create a continuous pump. After the first `Timer` fires and runs `_pump` once, there's nothing scheduling the next `_pump` call. The command queue would drain exactly once.

---

## 4. Error Handling Gaps

### 4.1 Missing try/except around clipboard resume

See #3.9 above. Linux `_paste_impl` lacks exception handling around `self.clipboard.resume()`.

### 4.2 Missing try/except in `_grab_selection_quiet` resume

**File**: `mumble_linux.py` (same as Windows `_grab_selection_quiet`)
The Linux version was not inspected in detail, but since the general clipboard resume pattern is missing guards on Linux, this path likely also has gaps.

### 4.3 `_cmd_token_ok` crash on missing token

**File**: `mumble_linux.py`, line 1813
**Code**: `want = getattr(self, "_cmd_token", "") or ""`
**Issue**: If `_cmd_token` hasn't been set yet (race condition between cmd server start and token generation), this silently accepts all requests because `want` would be `""` and `hmac.compare_digest("", anything)` could produce unexpected results. Windows has the same code, but the issue exists in both.

### 4.4 Missing error handling in `_pump` for individual items

**File**: `mumble_linux.py`, lines ~3750-3770
**Issue**: The `_pump` drains `_tk_queue` with a blanket `try/except Exception: pass` around each function call. This is correct and matches Windows. However, the Linux `_pump` is only re-scheduled if `self.root is not None` — and in headless mode, the re-scheduling via `self.root.after()` uses a one-shot `Timer`, which doesn't create a continuous loop (see #3.13).

---

## 5. Platform-Specific Concerns

### 5.1 `_GlibRoot` Design — Generally correct but fragile

**File**: `mumble_linux.py`, lines 83-134
The `_GlibRoot` shim is a clean adapter that maps Tk's `root.after()`/`quit()`/`mainloop()` onto GLib/GTK. However:

- The headless fallback (lines 125-133) uses `threading.Timer` for `after()`, but there's no continuous pump. Once the Timer fires once, no more `_pump` calls happen. The island, tray, and command queue all stop working.
- `withdraw()`, `iconbitmap()`, `destroy()` are all no-ops — fine since those are Windows-specific, but callers might assume they work.
- `quit()` calls `Gtk.main_quit()` which stops the GLib main loop, but the `_pump` loop might still be running and trying to queue more work.

### 5.2 Wayland vs X11 Detection

**File**: `mumble_linux.py`, lines 63-66
The controller forces `GDK_BACKEND=x11` when `WAYLAND_DISPLAY` is set but `GDK_BACKEND` is not. This is well-reasoned (native-Wayland GTK can't position overlays correctly). The comment at line 60-66 explains this clearly.

However, `copy_image()` (line 3385-3393) does its own Wayland detection using `bindings._session_type()` which **doesn't exist** (see #3.1). This is a crash bug.

### 5.3 Clipboard Probing

**File**: `mumble_linux.py`, lines 211-228
The `_pyperclip_ok` probe is a good defensive check. It correctly handles the "no xclip/xsel/wl-clipboard" case. However, the probe runs `pyperclip.paste()` once at startup — if the clipboard backend is installed but broken (e.g. xclip present but no X server), the probe would incorrectly mark it as available (the `except PyperclipException` catches only the explicit "no mechanism" error, all others fall through to `_pyperclip_ok = True`).

### 5.4 System Tray

**File**: `mumble_linux.py`, lines 3975-3988
Uses `pystray.Icon.run_detached()` to avoid running a second GTK main loop. Correct approach. The error message for GNOME without AppIndicator extension is helpful. However, there's no fallback tray mechanism (e.g., using Gtk.StatusIcon directly) if `run_detached()` fails.

### 5.5 Notifications

**File**: `mumble_linux.py`, lines 389-413
Good: tries `notify-send` first (works even without tray), then falls back to tray icon `notify()`. The `notify-send` path is async (subprocess.Popen) and won't block.

### 5.6 evdev Hotkey Handling

Not directly in the controller — handled by `bindings.py`. The Linux bindings module uses the `keyboard` library (same as Windows) plus the `mouse` library. There's no evdev integration visible at the controller level; the controller just uses `bindings.register_hotkey()` which delegates to the shared bindings layer.

### 5.7 Process Priority

**File**: `mumble_linux.py`, lines 416-425
`_boost_priority()` returns `None` (no-op) with a good comment explaining why (needs CAP_SYS_NICE on Linux). `_restore_priority()` is also a no-op. Correctly handled.

### 5.8 Image Copy

**File**: `mumble_linux.py`, lines 3380-3470
Good: uses `wl-copy` on Wayland, `xclip` on X11, with proper fallback. CRASH BUG: `bindings._session_type()` doesn't exist (see #3.1). Also uses `subprocess.Popen` (fire-and-forget) for `xclip`/`wl-copy` which handle selection ownership by staying alive — the `_clipboard_has_image` poll waits for confirmation.

---

## 6. All TODO/FIXME/HACK Comments

**Neither file contains TODO, FIXME, HACK, XXX, or WORKAROUND comments.** The search across both files returned zero results for these patterns. This is unusual for a port of this size — it suggests comments that might flag incomplete work were never written, rather than that the work is complete.

---

## 7. Code Quality Concerns

### 7.1 File Size

Both files are extremely large (4089 and 4994 lines). The Linux file at 4089 lines is a "port" with significant functionality removed but still retains ~82% the size of the canonical version. This ratio suggests the port carries significant redundant code.

### 7.2 Duplicated Code

Large sections are copy-pasted between Windows and Linux:
- `_ai_cfg`, `_prompt_ai_cfg`, `_pro_fallback_notice`: identical
- `_cloud_generate`: very similar with minor differences in mode handling
- `_build_menu`, `_recent_items`, `_preview`: identical
- Hotkey registration, settings live-apply: nearly identical
- `_transcribe`, `_local_transcribe`, `_cloud_transcription_on`: nearly identical with feature gaps

### 7.3 Inconsistent `_snap` dictionary

**File**: `mumble_linux.py`, lines ~1060-1070
Linux `_snap` captures: `pro_mode`, `format_enabled`, `foreign_mode`, `foreign_languages`, `english_only`, `local_only_mode`
Windows `_snap` captures: `pro_mode`, `local_only_mode`, `llm_provider`, `english_only`, `foreign_mode`, `foreign_languages`, `format_enabled`, `prompt_provider`

Linux is missing: `llm_provider`, `prompt_provider`. These are used by Windows for config isolation (VAL-CROSS-020).

### 7.4 Inconsistent `_local_transcribe` feature set

Linux `_local_transcribe` is missing:
- `formatting.sanitize_hotwords(terms)` call
- `confidence_gating` segment dropout
- ITEM 20 realtime-factor performance diagnostic
- `_stt_device`, `_stt_compute`, `_stt_model` tracking

### 7.5 `_process` structure differs subtly

Windows `_process` doesn't manage `self._processing` internally — `stop_recording` sets it before calling `_process` and `finally` clears it. Linux `_process` manages `self._processing` itself with its own try/finally. This is a structural divergence that could cause confusion during future refactoring.

### 7.6 The `_collect_text` method

Linux does not define its own `_collect_text` — it relies on the shared `ai` module's version. Windows defines its own `_collect_text` that additionally handles `TRUNC_MARKER`. The inconsistency means the Linux path won't notify users of truncated AI responses.

### 7.7 Settings live-apply gaps

Linux `_apply_settings_change` is missing handlers for:
- `prompt_mode_enabled` (Windows syncs this through active_mode)
- `island_modes`, `island_foreign_toggle`, `foreign_mode`, `island_active_mode` (Windows re-pushes island bar state)
- `hardware_tier`, `english_only`, `primary_language` (Windows re-applies hardware model)

### 7.8 Single-instance lock robustness

Windows `_acquire_single_instance` has a 3-attempt retry with back-off and a liveness probe. Linux has a single attempt with no retry. This makes Linux more likely to show "already running" when the previous instance crashed and the OS hasn't released the port yet.

---

## Summary of Critical Issues

1. **Meeting mode entirely absent** — entire feature class missing
2. **Island mode deck absent** — no Email/Reply/Foreign mode surface
3. **Island tips absent** — no usage-aware feature discovery
4. **Conversation capture absent** — no "Capture chat" feature
5. **On-device LLM backend absent** — no local smart-mode shaping
6. **`bindings._session_type()` crash bug** — `copy_image` will fail with AttributeError
7. **Missing `_processing` guard in `on_hotkey`** — can double-start recording
8. **`stop_recording` not idempotent** — can crash on double hotkey
9. **Hardware-tier model selection absent** — users get suboptimal defaults
10. **Default hotkey mismatch (H vs D)** — History hotkey doesn't match canonical
