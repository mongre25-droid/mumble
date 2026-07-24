# Experimental correction learning

This isolated package turns a user's explicit before/after transcript edit into
Mumble personal-vocabulary entries. It is a clean-room implementation: the
analyzer, persistence and span tracker use only Python's standard library; the
optional Windows observer lazily uses Mumble's existing `comtypes` dependency.
Importing it does not change settings, start services, or retain transcript text.

## Public API

```python
from experimental.correction_learning import (
    CorrectionLearningManager,
    InsertedSpanTracker,
    UIAEditMonitor,
    analyze_correction,
)

analysis = analyze_correction(
    "Open mum bull settings",
    "Open Mumble settings",
)

manager = CorrectionLearningManager(settings, data_directory)
preview = manager.preview(original_text, corrected_text)
result = manager.learn(original_text, corrected_text)
undo = manager.undo_last()
status = manager.status()

# Call immediately after Mumble pastes text into the focused edit control.
monitor = UIAEditMonitor()
monitor.prewarm()  # call earlier, when the experiment is enabled
def request_review(original, corrected):
    # Show manager.preview(original, corrected) in trusted UI first. Call
    # manager.learn(...) only after the user explicitly approves that preview.
    show_correction_review(original, corrected, manager.preview)

monitor.start(pasted_text, request_review)
```

`CorrectionLearningManager` accepts a Settings-like object with
`get(key, default)` plus either `update(**values)` or `set(key, value)`. A plain
mutable mapping is accepted too. The path may be a directory (which receives
`correction_learning.json`) or an explicit `.json` filename.

Mumble's real `Settings` also provides `atomic_vocabulary_update(mutator)`.
Learn, undo, and their error-compensation paths use that transaction to re-read
the latest disk collections under the shared settings lock, so a stale
controller cannot overwrite a manual edit from the web process. A separate
token-checked history lock serializes accidental manager instances across both
threads and processes.

Mumble's `Settings.atomic_vocabulary_update()` is used when available. It
serializes cross-process read/merge/write operations so a background correction
and a manual vocabulary save cannot erase one another. The history store has a
separate token-checked transaction lock, so accidental duplicate managers also
retain every undo session.

`preview()` and `learn()` return a dictionary with `ok`, `message`, `changes`,
and `session_id`. `learn()` adds exact heard-form to corrected-form mappings to
`vocabulary`, and adds the spelling to `vocabulary_terms`. It is callable only
in response to an explicit correction even when the experiment toggle is off;
`status()["enabled"]` reads `correction_learning_enabled` from settings so the
host UI can decide whether to offer the workflow.

## Safety boundaries

The analyzer compares lexical tokens with `difflib.SequenceMatcher`. It accepts
one-word substitutions, up to four heard words collapsing into one corrected
term, deliberate casing, and short acronyms. It refuses to learn:

- punctuation and whitespace editing;
- insertions or deletions;
- common grammar/function-word swaps and ordinary inflections;
- automatic sentence-start capitalization; and
- edits that rewrite most of an utterance.

These filters intentionally prefer missing a possible term over installing a
risky global replacement. The host should always show `preview()` changes in
the correction UI before invoking `learn()`.

## Privacy and undo

The atomic JSON history contains only learned mapping deltas, corrected terms,
timestamps, random session IDs, and undo metadata. It never stores either full
transcript. Writes use a same-directory temporary file, `fsync`, and
`os.replace`; a malformed existing file is preserved and learning fails closed.

Undo reverts a mapping only while its current value still equals the value this
package installed. A hash guards the corrected-term list. If settings were
edited after learning, undo preserves those later values and reports skipped
items instead of overwriting them. History is capped at 250 sessions to keep
the local privacy footprint bounded.

## Bounded edit observation

`UIAEditMonitor` is an optional Windows-only bridge for noticing when a user
edits the text Mumble just pasted. It loads `UIAutomationCore` through
`comtypes` lazily; missing packages and non-Windows platforms return an
unavailable result without breaking this package. It watches only the initially
focused editable control,
refuses password controls, polls every 0.5 seconds, waits for 1.2 seconds of
stable text, and expires after at most 30 seconds. Changing focus, foreground
window, or text outside the pasted span cancels observation.

Cold UI Automation imports can be slow, so the host should call the asynchronous
`prewarm()` when this experiment is enabled. Prewarming imports modules only;
all COM initialization, focused-control capture, and subsequent UIA access stay
inside the monitor worker thread. `start()` fails fast with `warming_up` until
prewarming finishes rather than missing its bounded setup window.

`InsertedSpanTracker` implements the platform-independent boundary logic. It
keeps the pasted segment plus lengths and SHA-256 digests of the exact prefix
and suffix. Full field values are transient samples only: neither the tracker
nor monitor logs, persists, or retains the surrounding field text.
