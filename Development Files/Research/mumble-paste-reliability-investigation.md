# Mumble paste reliability and focus-restoration investigation

- **Wayfinder decision:** Define the paste reliability and focus-restoration evidence contract
- **Research snapshot:** `5e19b1ef` (`codex/wayfinder-8-paste`)
- **Date:** 24 July 2026
- **Scope:** Windows final-text insertion from dictation, Paste latest, Deck, and related controller paths. This is diagnosis and an implementation contract, not a product-code change.

## Decision in one page

Mumble's current final-insertion path has useful safety measures: it saves the result before insertion, serialises clipboard operations, confirms its text reached the clipboard before emitting `Ctrl+V`, releases common modifiers, waits before restoring the clipboard, and reports “Saved” when its *pre-send* editable-field check is false.[^code-persist][^code-paste]

It does **not**, however, possess a reliable “paste landed” signal. The value returned as `landed` is calculated before the keypress. A swallowed keypress, User Interface Privilege Isolation (UIPI) block, focus change, or modifier-release failure can therefore return `True` and display “Pasted” even when the intended field received nothing. Conversely, delayed focus can make the preflight return `False` even if the keypress later lands.[^code-focus][^code-paste] The current pinned `keyboard` package emits ordinary Windows key events with `keybd_event`, which Microsoft documents as superseded by `SendInput`; `keybd_event` has no delivery count.[^code-deps][^keyboard-source][^keybd-event]

The Windows contract should therefore be one idempotent insertion transaction with:

1. a stable operation identity and intended target-window snapshot;
2. bounded focus-readiness polling and an immediate pre-injection target recheck;
3. a confirmed clipboard write that preserves supported prior formats;
4. one atomic `SendInput` batch, with its returned event count recorded;
5. honest outcomes of **confirmed**, **sent but unconfirmed**, **not sent**, or **copied for manual paste**;
6. no automatic second `Ctrl+V` after an uncertain send;
7. conditional clipboard restoration that never overwrites a newer external clipboard change; and
8. privacy-safe local telemetry plus a physical application matrix before any universal reliability claim.

`SendInput` is the recommended Windows primary method because it is the current Win32 API, returns the number of events inserted into the input stream, and sends a supplied batch serially. It still cannot prove that an application consumed the paste and remains subject to UIPI. Therefore its count is an injection signal, not a universal content-confirmation signal.[^sendinput]

## Evidence boundary

This investigation established a fast, deterministic loop around Mumble's real `Mumble._paste` → `Mumble._paste_impl` seam. Operating-system effects were controlled substitutes so individual races could be forced without modifying a user's real clipboard or typing into an arbitrary application.

That loop is strong evidence for the seam's state and decision defects. It is **not** physical evidence that a particular Chrome, Brave, Firefox, Office, Electron, elevated, or rich-editor build fails in the same way. No browser or Office coverage is claimed. The physical matrix later in this report remains a release gate.

## Phase 1: red-capable feedback loop

### Exact command

From `Internal/app`:

```powershell
python _paste_reliability_harness.py
```

The temporary harness imported the real `mumble.py`, instantiated `Mumble` without starting the full application, and called the real `_paste`/`_paste_impl` and `_set_clipboard` methods. Only the external seams—clipboard reads/writes, keyboard events, foreground/focus state, integrity acceptance, and time—were deterministic substitutes. It asserted the user's exact symptom: whether the intended target received the text exactly once and whether clipboard/focus safety was preserved.

The harness was run three times after its final scenario set. All three runs produced the same verdict and the same scenario observations:

```text
{"summary":{"failed":8,"failures":["delayed_focus_restoration","intercepted_ctrl_v","stuck_modifier_release_failure","active_window_changes","clipboard_restoration_failure","clipboard_interceptor_change","privilege_elevation_mismatch","duplicate_retry_risk"],"passed":4}}
EXIT=1
```

The harness was deliberately removed after investigation. It was a temporary diagnostic, not a polished cross-application regression suite. The implementation ticket should turn the contract below into focused tests at a maintained platform seam rather than commit this throwaway file.

### Controlled results

| Simulation | Observed current result | Contract verdict | What it proves at the seam |
| --- | --- | --- | --- |
| Baseline editable target | One intended insertion; prior text clipboard restored; `landed=True` | Green | The ordinary mocked path works. |
| Delayed focus restoration (target ready at 120 ms) | `Ctrl+V` sent at 40 ms; no insertion; `landed=False` | Red | One fixed wait does not cover delayed focus. |
| Clipboard settle delay (first three writes unavailable) | Retry succeeds; one insertion; prior clipboard restored | Green | Confirmed clipboard retries handle a bounded transient in this simulation. |
| Intercepted `Ctrl+V` | No insertion; `landed=True` | Red | Pre-send editability is not delivery confirmation. |
| Initially stuck common modifiers, all releasable | All five releases observed; one insertion | Green | The current happy-path modifier cleanup is useful. |
| Modifier release raises and remains stuck | No insertion; `landed=True` | Red | Swallowed release errors can produce a false success. |
| Active window changes during preparation | Text inserted once into the wrong window; `landed=True` | Red | The operation is not bound to an intended target. |
| Focus never returns | No insertion; `landed=False` | Green | It fails without a wrong-target insertion in this controlled case, but supplies no recovery. |
| Clipboard restoration stays busy | Intended insertion occurs; Mumble text remains on clipboard; `landed=True` | Red | Restoration failure is ignored by the outcome. |
| Target/interceptor changes clipboard after paste | Later clipboard value is overwritten with the old value | Red | Restoration is unconditional and can clobber a newer external change. |
| Higher-integrity target rejects injected input | No insertion; `landed=True` | Red | No integrity/delivery evidence reaches the outcome. |
| Caller repeats an uncertain operation | Two serial insertions; both return `True` | Red | The lock serialises work but does not make it idempotent. |

The repository's existing narrow command also passed:

```powershell
python test_core_regressions.py
# exit 0
```

Its current paste regression proves only that a failed clipboard write prevents `Ctrl+V`; it does not model target identity, input rejection, target consumption, focus delay, restoration failure, or duplicate retry.[^code-current-test]

## Phase 2: minimal reproductions

Each red scenario reduced to the same minimum production seam plus one changed external fact. The green baseline used identical controller construction and dependencies. Removing the one changed fact returned the baseline to green.

- **False success:** editable preflight `True` + verified clipboard write + swallowed `Ctrl+V` is sufficient. No recording, transcription, Deck, Island, model, or network is involved.
- **Wrong target:** target foreground at preflight + different foreground at send time is sufficient. The current code never stores or rechecks an intended target.
- **Delayed focus:** no focus at preflight/send + focus after the fixed 40 ms is sufficient.
- **Restoration loss:** successful insertion + restore writes that never read back is sufficient. The restore return value is discarded.
- **Duplicate:** two sequential calls with the same text and target are sufficient. The existing lock ensures order, not at-most-once behaviour.

This is the smallest justified causal boundary. Which external applications create those facts, and how often, remains a physical measurement question.

## Phase 3 and 4: ranked hypotheses and probes

| Rank | Falsifiable prediction | Controlled probe | Result |
| --- | --- | --- | --- |
| 1 | If success is only a pre-send guess, intercepted or integrity-blocked `Ctrl+V` will still return `True`. | Accept editability and clipboard writes, reject the keyboard event. | Confirmed twice: both cases returned `True` with zero intended insertions. |
| 2 | If the operation has no target identity, changing foreground after preflight will redirect text. | Switch the active editable window 20 ms after entry. | Confirmed: one insertion reached the other window. |
| 3 | If fixed timing is insufficient, a target becoming ready after 40 ms will miss. | Make focus available at 120 ms. | Confirmed: no insertion. |
| 4 | If cleanup failures are ignored, restore failure or a newer clipboard value will not alter success and may leave/clobber data. | Reject restore writes; separately mutate clipboard after the send. | Confirmed in both cases. |
| 5 | If retries lack operation identity, repeating an uncertain request will duplicate. | Invoke the same operation twice through the locked real seam. | Confirmed: two insertions. |

No product debug logging was added. The controlled harness itself was the targeted probe and has been removed.

## Current production path

### What is already good

1. The normal dictation result is persisted to History before insertion, so a failed paste does not lose the transcription.[^code-persist]
2. `_paste_lock` serialises dictation, Deck, correction, and quick-paste clipboard work.[^code-paste]
3. `_set_clipboard` writes and reads back the exact text before allowing `Ctrl+V`.[^code-paste]
4. The clipboard monitor is paused and Mumble marks its own output so it does not become context input.[^code-paste]
5. The existing local trace deliberately excludes audio, transcript words, clipboard contents, prompts, paths, provider payloads, and exception messages.[^code-trace]

These safeguards should be retained.

### Where truth is lost

The current sequence is:

1. `_focused_editable()` examines the *current* foreground thread, caret, focus handle, and broad class-name keywords. Browser/Electron surface keywords count as editable even when the exact DOM field cannot be identified.[^code-focus]
2. That Boolean is stored in `landed` before the clipboard changes.
3. Mumble reads only the clipboard's Unicode text representation, writes its text, and confirms the readback.[^code-paste] The pinned `pyperclip` path is text-only; rich text, HTML, images, files, and application-private formats cannot be faithfully restored through it.[^code-deps][^clipboard-formats]
4. Common modifiers and the configured mode key are released, with all errors swallowed.
5. After 40 ms, `keyboard.send("ctrl+v")` is called. The pinned Windows backend uses `keybd_event`, whose API returns no acceptance count.[^keyboard-source][^keybd-event]
6. Mumble waits `min(1.2, 0.18 + len(text) / 20000)` seconds and attempts to restore the prior *text* clipboard. The restore result is ignored; if the prior text was empty, no restoration is attempted.[^code-paste]
7. The pre-send `landed` value is returned, drives `paste_finished success`, the final trace outcome, “Pasted” Island copy, and correction-candidate eligibility.[^code-persist]

Deck adds another unverified assumption: it minimises its window, sleeps a fixed 250 ms in the controller command, and then calls the same paste path. It does not carry the previous target handle across that transition.[^code-deck]

### Timeout and duplicate exposure

`pyperclip` 1.11.0's Windows path can itself retry `OpenClipboard` for roughly 500 ms per call. Mumble may call its write/read loop six times, and may then run another six-attempt restore. Deck's bridge waits four seconds for a synchronous controller response.[^code-deps][^code-deck] This does not prove that a real timeout has caused a duplicate, but it makes the following race structurally possible:

1. the UI times out while the controller is still inside the clipboard transaction;
2. the user retries because the outcome is unknown;
3. the second operation waits on `_paste_lock`; and
4. both eventually emit `Ctrl+V` because there is no shared operation identity.

The deterministic duplicate probe confirms the at-most-once guarantee is absent once two calls exist.

## Windows constraints the design must respect

| Constraint | Contract consequence |
| --- | --- |
| `GetForegroundWindow` can temporarily return `NULL` while activation changes.[^foreground] | Poll a bounded readiness condition; do not equate one empty snapshot with permanent failure. |
| Windows restricts `SetForegroundWindow`, can deny it even when documented conditions appear satisfied, and prevents an app from simply forcing foreground while the user works elsewhere.[^setforeground] | Restore only a known target that a Mumble surface displaced; verify the result. Never paste merely because a focus-forcing call was attempted. |
| `GUITHREADINFO` exposes active, focused, and caret handles for a GUI thread.[^guithread] | Capture a richer target snapshot than a broad class-name Boolean and revalidate immediately before injection. |
| Only one window can have the clipboard open at a time.[^clipboard-ops] | Use bounded retries with elapsed-time telemetry, not unbounded sleeps. |
| `GetClipboardSequenceNumber` proves that clipboard contents changed; delayed rendering can defer the increment.[^clipboard-seq] | Use it to protect write/restore ordering, never as proof that the target consumed a paste. |
| Windows clipboards can carry multiple standard, registered, private, and synthesized formats.[^clipboard-formats] | A text-only snapshot is not full clipboard preservation. Define and test supported-format restoration explicitly. |
| `keybd_event` is superseded by `SendInput`.[^keybd-event] | The Windows seam should own a direct, tested `SendInput` implementation instead of relying on `keyboard.send` for final insertion. |
| `SendInput` reports the number of input events inserted, is subject to UIPI, cannot identify UIPI as the specific cause, and does not reset keys already held.[^sendinput] | Record its count, detect integrity levels separately, and gate on modifier state. A full count is still not content confirmation. |
| `GetAsyncKeyState` can report whether a key is currently down, though access itself can be restricted.[^async-key] | Wait briefly for physical modifiers to be released; do not blindly synthesize key-up events for keys the user is actively holding. |
| UI Automation Text is generally read-only; some controls support modification through Value/TextEdit or direct keyboard input, but support varies.[^uia-text][^uia-value] | Use UI Automation only for opportunistic, privacy-safe confirmation on known controls, not as a universal replacement for paste. |

## Required insertion transaction

### 1. Operation identity and state

Every insertion request receives an opaque `operation_id` before it enters the controller. The operation records only:

- source: dictation, quick paste, Deck, correction, or command;
- target snapshot and whether Mumble itself displaced it;
- state: `prepared`, `clipboard_ready`, `input_started`, `input_sent`, `confirmed`, `uncertain`, `fallback`, `finished`;
- whether any input event has been submitted; and
- cleanup status.

All callers of the same request observe the same operation. A UI timeout queries that operation; it never creates a replacement implicitly.

### 2. Target selection and focus

- Capture the foreground top-level window, process/thread identity, focused child, caret presence, control class, and integrity relation when the insertion target is chosen.
- For ordinary hotkey dictation, choose the editable target active when Stop is requested. Retain the start target only as diagnostic context; do not force text back into an app the user has intentionally left.
- For Deck or another Mumble surface that takes focus, capture the external target before showing/taking focus, then minimise/hide Mumble and poll for that exact target to return for a bounded period such as 400 ms.
- A best-effort `SetForegroundWindow` is permitted only when Mumble caused the displacement and the target still exists. Verify foreground and focused-child identity afterwards.
- Recheck the exact top-level target and acceptable focused child immediately before input. If another application is active, stop and use the manual fallback; never redirect silently.

### 3. Clipboard preparation

- Preserve the current clipboard as a short-lived transaction before overwriting it. The implementation must enumerate and clone a documented bounded set of formats or use an appropriately short-lived OLE `IDataObject` strategy; it must not call text-only preservation “complete.” Microsoft recommends OLE-aware applications use the OLE clipboard functions, while warning that a returned data object may make cross-process calls and should be held only briefly.[^ole-clipboard]
- At minimum, the physical contract covers Unicode text, RTF/HTML, a device-independent bitmap, and file-drop data. Unknown/private formats may require an honest “cannot safely preserve” fallback rather than destructive insertion.
- Write `CF_UNICODETEXT` plus the Windows clipboard-history exclusion formats so private dictation text is not unnecessarily added to Windows Cloud Clipboard/history.[^clipboard-formats]
- Confirm exact readback and capture the sequence number owned by this transaction.
- Bound clipboard acquisition by elapsed time. Report the number of attempts and open-window contention without logging clipboard data or window titles.

### 4. Modifier and input preparation

- Read left/right Ctrl, Alt, Shift, and Win state. Wait a short bounded interval for physically held modifiers to be released.
- Do not blanket-release a modifier that remains physically down. If it does not clear, do not inject; copy for manual paste and explain the reason.
- Build one four-event `SendInput` array: Ctrl down, V down, V up, Ctrl up. Submit it once and retain the returned count.
- If fewer than all events are accepted, mark `not_sent` or `uncertain`; do not claim “Pasted.” Detect process integrity levels before the call so a higher-integrity target can receive a specific, useful fallback message.

### 5. Confirmation states

There is no honest universal confirmation that an arbitrary application consumed clipboard text. Use these outcomes:

| Outcome | Evidence | User-facing meaning |
| --- | --- | --- |
| `confirmed` | Exact target remained active and a supported privacy-safe control signal proves the expected insertion once. | **Pasted** |
| `sent_unconfirmed` | Clipboard verified, target stable, and all `SendInput` events accepted, but target content cannot be inspected safely. | **Sent to the active field**; result remains in Deck. |
| `not_sent` | Target/focus invalid, modifier blocked, clipboard unavailable, or zero/partial input count. | **Copied—paste manually** or **Saved in Deck**, with the reason. |
| `uncertain` | Input was submitted but focus changed or confirmation became unavailable. | **Paste not confirmed—check the field.** Never auto-retry. |
| `cleanup_warning` | Insertion outcome known but prior clipboard could not be restored. | Show a quiet warning and retain recovery affordance where safe. |

UI Automation confirmation must be in-memory, narrowly scoped, disabled for password/protected fields, and never persist before/after content. A focus/caret or `SendInput` signal alone cannot upgrade `sent_unconfirmed` to `confirmed`.

### 6. Clipboard settle and restoration

- After input submission, hold Mumble's clipboard value for a bounded settle window informed by payload size and measured target behaviour.
- Before restoring, require that the clipboard sequence/value still belongs to this transaction. If the user or target changed it, do not overwrite the newer value.
- Restore the supported multi-format snapshot and verify it. Record cleanup failure separately from insertion success.
- If a full snapshot could not be taken, choose the declared fallback before injection. Do not silently destroy an image, file list, rich selection, or private format and then describe the clipboard as preserved.

## Bounded retry and fallback ladder

1. **Persist first.** Keep the completed result in Deck/History as today.
2. **Resolve target.** Poll the exact expected focus for up to the measured focus budget. Safe to retry because no input has been submitted.
3. **Snapshot clipboard.** If supported data cannot be preserved, stop or obtain an explicit user action later; do not overwrite silently.
4. **Write and verify.** Retry transient clipboard contention with bounded backoff. Safe to retry before input.
5. **Wait for modifiers.** If keys remain physically held, stop with a manual-copy fallback.
6. **Revalidate target.** Any target change cancels automatic injection.
7. **Inject once.** Call `SendInput` exactly once for this operation ID.
8. **Classify.** Use accepted event count, target stability, integrity relation, and any supported control confirmation.
9. **Restore conditionally.** Never overwrite a clipboard sequence no longer owned by the operation.
10. **Fallback.** Leave/copy the completed text for a user-driven paste and give a specific reason. Do not attempt `WM_PASTE`, UI Automation `SetValue`, typing every character, or a second `Ctrl+V` automatically after an uncertain send; each can duplicate, replace an entire field, lose rich-editor semantics, or cross a different security/compatibility boundary.

## At-most-once and no-wrong-target guarantees

The implementation is not acceptable unless the automated harness proves all of these:

1. one operation ID can cross `input_started` only once;
2. duplicate controller messages return the existing result;
3. caller timeout does not cancel or restart a live operation;
4. retries occur only before input submission;
5. post-submit retry requires positive proof that **zero** events were accepted and still requires a fresh explicit user action;
6. the intended top-level target is identical immediately before injection;
7. another foreground window receives zero events/data;
8. clipboard/capture operations share the same transaction lock without converting serialisation into duplicate execution; and
9. “Pasted” is impossible without `confirmed` evidence.

## Privacy-safe telemetry contract

Extend the existing opt-in, local-only trace rather than creating an unrestricted second log. Keep its bounded rotation and strict field allowlist.[^code-trace]

Allowed insertion metadata:

- opaque session-local operation ID or ordinal;
- source and outcome enum;
- elapsed times for lock, focus wait, clipboard snapshot, clipboard write, input, confirmation, settle, and restore;
- target captured/present/same booleans;
- focused-child/caret/editable booleans;
- same/lower/higher/unknown integrity relation;
- modifier-ready Boolean and wait duration, not typed keys;
- clipboard format categories and count, never payloads;
- clipboard write/restore attempt counts and sequence-match Boolean;
- input API name, requested event count, accepted event count;
- confirmation method enum and fallback reason enum; and
- payload-size bucket, not transcript text.

Forbidden data:

- transcript words, clipboard contents, selected text, prompts, before/after field content;
- window titles, document names, URLs, file paths, usernames, keys, provider payloads;
- raw exception messages or unrestricted process command lines; and
- screenshots or audio.

Executable identity should be collected only in a deliberate local diagnostic mode, reduced to a coarse app category or allowlisted basename, and excluded from exported traces by default.

## Automated test architecture required before implementation sign-off

### Fast deterministic seam suite

Convert the temporary investigation cases into maintained tests around a Windows `InsertionTransaction` seam. They must run without touching the real clipboard and cover at least:

- normal insertion;
- target focus ready at 0, 40, 120, and 400+ ms;
- clipboard busy below and beyond the retry budget;
- swallowed/intercepted input;
- partial and zero `SendInput` counts;
- releasable and physically held modifiers;
- target change before input;
- focus restoration failure;
- same-, lower-, higher-, and unknown-integrity relations;
- restore failure and a newer external clipboard sequence;
- duplicate controller messages, UI timeout, and concurrent capture; and
- supported/unsupported clipboard formats.

Every failure assertion must check intended field contents, unintended field contents, operation outcome, input count, and clipboard state—not merely “did not throw.”

### Real native fixture

Add a tiny test-only Windows fixture process with two edit fields and selectable modes: delayed focus, swallow `Ctrl+V`, mutate clipboard, delay clipboard read, and record exact insert count. Run the packaged Mumble insertion seam against it. A separate elevated launch is required for the UIPI boundary; that step may need an approved test runner or interactive UAC and must not be faked in normal CI.

The fixture supplies closed-loop native evidence, but it still does not replace real browser/Office/Electron checks.

## Physical application matrix

This matrix is **not yet executed**. Run it on the packaged Windows build, with Mumble at normal integrity unless the row says otherwise.

| Class | Required targets and surfaces | Special checks |
| --- | --- | --- |
| Chromium browsers | Current Edge, Chrome, and Brave: address bar, plain `<input>`, `<textarea>`, local `contenteditable`, and one complex web editor | Browser chrome versus renderer focus; intercepted web keyboard handlers; multiline/Unicode. |
| Firefox | Current Firefox: address bar, plain fields, local `contenteditable`, one complex editor | Mozilla focus classes and different event handling. |
| Standard native fields | Notepad single document plus a dedicated Win32 edit-control fixture | Exact once, selection replacement, no field focused, delayed focus. |
| Desktop native apps | At least one common non-browser editor used by the owner | Focus restoration, long text, clipboard formats. |
| Electron | VS Code editor and one current Electron message/editor surface | Renderer focus, shortcut interception, slow renderer. |
| Microsoft Office | Current Word document, Outlook unsent compose body/subject, and an Excel cell/formula field | Rich formatting, selection replacement, protected/read-only fields, long multiline text. |
| Rich web editors | A local ProseMirror/Quill/TipTap fixture plus any supported production editor | Input handlers, sanitisation, rich selection, duplicate prevention. |
| Delayed-focus path | Deck paste after minimise/restore at controlled 0–500 ms delays | Exact original target, no other-window insertion, truthful timeout result. |
| Clipboard-intercepting path | Test fixture that swallows `Ctrl+V`, delays clipboard reading, and replaces clipboard content | Honest unconfirmed/fallback state; no overwrite of newer clipboard. |
| Privilege boundary | Elevated test fixture/Notepad with normal Mumble; normal target with elevated test Mumble only if release policy supports elevation | UIPI classification, no false “Pasted,” safe manual fallback. |
| Different foreground app | Switch to a second editable app during processing | Zero wrong-target inserts; saved/manual fallback. |

### Trial protocol

For every applicable cell:

1. test dictation, Paste latest, and Deck paste entry points;
2. use short ASCII, multiline, Unicode/emoji/right-to-left, and a long 20,000-character fixture payload;
3. begin with plain-text, RTF/HTML, image, and file-list clipboards where supported;
4. test empty selection, selected replacement, caret middle/end, read-only field, and no focused field;
5. run at least 20 warm repetitions plus five first-use repetitions per critical surface;
6. introduce real typing/mouse movement during processing and a foreground switch in dedicated rows;
7. record operation trace, target result, wrong-target result, clipboard restoration, focus, user-visible status, and elapsed time; and
8. use fixed non-sensitive fixtures only—never real email, credentials, or personal clipboard data.

### Acceptance threshold

- **100%** exact-once insertion in supported normal-integrity editable surfaces.
- **Zero** wrong-target and duplicate insertions.
- **Zero** stale-clipboard insertions.
- **100%** honest classification: no “Pasted” without confirmation.
- **100%** supported-format restoration unless the clipboard changed externally; then preserve the newer value.
- Higher-integrity and unsupported controls must produce a clear, non-destructive manual fallback.
- Any intermittent failure keeps the physical release gate open and must include the local operation trace for reproduction.

## Recommended implementation order

1. Extract the current logic behind a Windows insertion transaction seam and commit the deterministic regression suite first.
2. Add operation identity and target capture/revalidation.
3. Replace final-insertion `keyboard.send` with a direct counted `SendInput` batch; leave unrelated hotkey registration scoped separately.
4. Add integrity/modifier gates and honest outcome states.
5. Implement multi-format clipboard snapshot, conditional restore, and recovery reporting.
6. Extend the strict local trace allowlist and Deck/controller response schema.
7. Add the native fixture and run its normal-integrity closed loop in CI.
8. Execute and archive the physical matrix before changing the release claim.
9. Port the same semantic contract—not necessarily the Windows APIs—to macOS and Linux only after the Windows contract is measured.

## Remaining uncertainty

- No physical browser, Office, Electron, rich-editor, elevated, or administrator application was driven in this unattended research ticket.
- The controlled harness demonstrates structural failure capability, not the real-world frequency of each trigger.
- A universal target-consumption confirmation API does not exist in the inspected Windows documentation. Exact confirmation coverage will depend on control support and privacy constraints.
- Full-fidelity restoration of arbitrary private/delayed clipboard formats needs a bounded prototype and adversarial tests. The current text-only path is definitely incomplete, but the final OLE/format-cloning design is not selected here.
- Windows may deny foreground activation even when an application believes the documented conditions apply; manual fallback must remain part of the product contract.[^setforeground]

## Sources

### Authoritative external sources

[^keybd-event]: Microsoft, [`keybd_event` function](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-keybd_event) — marks the API superseded and directs applications to `SendInput`.
[^sendinput]: Microsoft, [`SendInput` function](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput) — event-count return, serial insertion, UIPI boundary, and current-key-state warning.
[^foreground]: Microsoft, [`GetForegroundWindow` function](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getforegroundwindow) — identifies the user's foreground window and documents temporary `NULL` results.
[^setforeground]: Microsoft, [`SetForegroundWindow` function](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setforegroundwindow) — foreground restrictions, possible denial, and taskbar-flash behaviour.
[^guithread]: Microsoft, [`GUITHREADINFO` structure](https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-guithreadinfo) and [`GetGUIThreadInfo`](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getguithreadinfo) — active, focused, and caret window handles.
[^clipboard-ops]: Microsoft, [Clipboard Operations](https://learn.microsoft.com/en-us/windows/win32/dataxchg/clipboard-operations) — exclusive clipboard opening, ownership, formats, and delayed rendering.
[^clipboard-seq]: Microsoft, [`GetClipboardSequenceNumber`](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getclipboardsequencenumber) — change sequence and delayed-rendering limitation.
[^clipboard-formats]: Microsoft, [Clipboard Formats](https://learn.microsoft.com/en-us/windows/win32/dataxchg/clipboard-formats) — standard, registered, private, multiple, synthesized, history, and cloud-control formats.
[^async-key]: Microsoft, [`GetAsyncKeyState`](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getasynckeystate) — current physical key state and access limitations.
[^uia-text]: Microsoft, [About the Text and TextRange Control Patterns](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-about-text-and-textrange-patterns) — Text is not a universal modification mechanism; Value/TextEdit/direct input support varies.
[^uia-value]: Microsoft, [`IUIAutomationValuePattern::SetValue`](https://learn.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationvaluepattern-setvalue) — supported-control requirements and explicit success/error result.
[^ole-clipboard]: Microsoft, [`OleGetClipboard`](https://learn.microsoft.com/en-us/windows/win32/api/ole2/nf-ole2-olegetclipboard) — `IDataObject`, OLE/non-OLE handling, possible cross-process calls, and short holding period.
[^keyboard-source]: `keyboard` project, [pinned v0.13.5 Windows backend](https://github.com/boppreh/keyboard/blob/v0.13.5/keyboard/_winkeyboard.py#L595-L620) — `press` and `release` route through `keybd_event`.

### Current repository evidence

[^code-deps]: [`Internal/app/requirements.txt`, pinned keyboard and clipboard packages](../../Internal/app/requirements.txt#L6-L8).
[^code-persist]: [`Internal/app/mumble.py`, persist-before-paste and outcome propagation](../../Internal/app/mumble.py#L2537-L2605).
[^code-focus]: [`Internal/app/mumble.py`, editable-field preflight](../../Internal/app/mumble.py#L2634-L2713).
[^code-paste]: [`Internal/app/mumble.py`, clipboard verification, serial insertion, fixed waits, send, and restoration](../../Internal/app/mumble.py#L2715-L2826).
[^code-deck]: [`Internal/app/webui_shell.py`, Deck minimise/request/restore flow](../../Internal/app/webui_shell.py#L335-L379) and [`Internal/app/mumble.py`, fixed controller delay](../../Internal/app/mumble.py#L3264-L3283).
[^code-current-test]: [`Internal/app/test_core_regressions.py`, failed-clipboard-write regression](../../Internal/app/test_core_regressions.py#L714-L739).
[^code-trace]: [`Internal/app/dictation_trace.py`, bounded event and field allowlists](../../Internal/app/dictation_trace.py#L1-L97) and [`Internal/app/mumble.py`, insertion trace events](../../Internal/app/mumble.py#L2751-L2795).
