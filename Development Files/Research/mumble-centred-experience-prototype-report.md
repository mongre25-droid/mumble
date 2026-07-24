# Mumble centred experience prototype report

**Issue:** [#11 — Prototype the centred Mumble experience and select a coherent visual system](https://github.com/mongre25-droid/mumble/issues/11)  
**Prototype:** [`prototypes/mumble-centred-experience/index.html`](prototypes/mumble-centred-experience/index.html)  
**Status:** Development-only decision evidence; no production behaviour changed

## Decision

Use **Direction A — Focus Stage** as Mumble's primary visual and interaction system.

Adopt two bounded ideas from the other directions:

1. Use Direction B's **context ledger** only on dense work surfaces where the current truth changes the next decision: Deck selection, Meetings recording, and Settings routing.
2. Use Direction C's **numbered spine** only for genuinely sequential explanation: first-run guidance, empty-state teaching, and the Before → During → After meeting explanation.

Do not combine all three layouts on ordinary screens. The normal Mumble experience remains one centred Focus Stage with one visually dominant task.

This is the most reversible synthesis because the shell, tokens, state surfaces, and centred stage can be introduced before any page-specific restructuring. The optional ledger and spine are contained patterns, not a second application shell.

## What was compared

The three directions were produced independently against the same contract and existing product evidence.

### A — Focus Stage

One calm surface changes with the task. Secondary context sits below or in a quiet adjoining zone. Home prioritises dictation; Deck transforms one command bar after selection; Meetings transforms one premium recording instrument.

### B — Centred Workbench

A stable three-zone structure keeps navigation or filters to the left, work in the centre, and current truth/actions to the right. It gives experts excellent scanability but exposes more information at once.

### C — Guided Spine

A vertical sequence explains the journey as Speak → Shape → Use, Browse → Choose → Shape, or Before → During → After. It is exceptionally clear for learning and narrow layouts, but too procedural for repeated daily actions.

## Objective comparison

Scores use a five-point scale where five is strongest. They compare the prototype structures, not production performance.

| Criterion | A: Focus Stage | B: Centred Workbench | C: Guided Spine |
|---|---:|---:|---:|
| Beginner clarity | **5** | 3 | 5 |
| Common dictation journey | **5** | 4 | 4 |
| Expert scanability | 3 | **5** | 3 |
| Deck selection context | 4 | **5** | 4 |
| Meetings state clarity | **5** | 5 | 5 |
| Narrow-window reflow | 4 | 3 | **5** |
| Visual calm | **5** | 3 | 4 |
| Structural implementation risk | **4** | 2 | 3 |
| Fit with current dark/gold identity | **5** | 5 | 4 |
| Overall | **40/45** | 35/45 | 37/45 |

### Why A wins

- It gives the most important action—Start dictation—unambiguous priority.
- It removes simultaneous dashboard choices without hiding the complete five-shortcut contract.
- It naturally supports one premium Meetings instrument and one transforming Deck command bar.
- It has the smallest risk of turning Settings, Stats, or Reader into dense dashboards.
- It keeps the centred design goal visible on desktop and remains understandable when reflowed.

### Why B is not the global shell

The persistent side zones are valuable for Deck, Meetings, and route truth, but on Home they compete with the dictation action. On narrower windows both side zones must relocate, so the structure changes more than Direction A.

### Why C is not the global shell

The spine teaches beautifully, especially on a first visit. Repeated daily use should not require visually stepping through an explanation every time. Its sequence pattern should appear only where order is meaningful.

## Selected visual system

### Shell and navigation

- Keep exactly six destinations: Home, Deck, Stats, Meetings, Reader, Settings.
- Keep Mumble Find and Web Search as momentary commands, not navigation destinations.
- Centre the application frame at a maximum width around 1,200 pixels.
- At narrow widths, reflow the six destinations to two rows of three. Do not replace labels with unexplained icons.

### Material and hierarchy

- Background: near-black graphite, with one restrained warm radial highlight.
- Primary surface: opaque dark gradient with one warm rim, one top light, and restrained depth.
- Gold: active destination, current step, focus outline, and one primary action per state.
- Green: local/ready truth only. Red: recording stop, destructive actions, and errors only.
- Use blur on at most one major layer. Nested surfaces should use borders and opaque gradients.
- Effects tiers may change decoration, never information, layout, contrast, focus, or available actions.

### Home

- Use one Focus Stage with a voice-first headline and primary Start dictation action.
- Keep the five global shortcuts together in a quiet ledger.
- Keep Mumble Find and Web Search visibly separate and explain the privacy difference.
- Use the exact contextual duration wording: “Short dictation currently stops after 10 minutes. Use Meetings for longer recordings.”
- Do not restore a permanent marketing-style capability grid.

### Deck

- Keep one command bar in a stable position.
- Browse state contains content type, filter, sort, Starred, Pin, and labelled More.
- Selection state exposes selection count, Smart Mode, Preset, Run shaping, Search the web, and clear selection.
- Keep image items thumbnail-led with the explicit action **Paste image**.
- Accessible name: **Paste image into previous app**.
- Keep useful item actions visible on keyboard focus and at narrow widths, not hover-only.

### Meetings

- Keep Before, During, and After as transformations of one recording instrument.
- During recording, keep Recording, elapsed time, stable audio level, local storage truth, Pause, and prominent Stop & save visible.
- Give the instrument warm rim depth and top light; do not render it as a flat black block.
- Use a bounded right context ledger for microphone, saved location, effective transcription route, and any later analysis route.

### Mumble Find

- Keep a centred shortcut-only overlay with a dedicated window-drag header.
- Show the input and placeholders before provider work completes.
- Cap the first page at 12 results.
- Keep loading, empty, partial-error, and complete-error states inside a stable result region.
- Preserve usable app results when file coverage is degraded.
- Never add Web Search as a fallback.
- Keep the window drag area separate from file-result drag affordances; native code must resolve opaque result identities.

### Settings

- Use Overview, Speech to text, Text shaping, Deck & data, and System.
- Present two route stages: Speech to text, then Text shaping.
- For each stage show what it receives, effective engine, location, what leaves the device, speed, privacy/quality boundary, and cost.
- Distinguish saved choice from effective route and explain why they differ.
- Replace Pro Mode with On this device / Hosted provider.
- Appearance uses Light / Standard / Full effects and must remain separate from processing capability.
- Do not ship a privacy or local-processing claim unless the shared runtime policy actually enforces it for that feature.

### Island controls

- Keep the primary Island click-through and status-only.
- Use a visually attached but independently interactive rail for mode/language, Deck when useful, and a labelled Stop action.
- Aim for 36–44 pixel Stop and Cancel targets; never go below 28 pixels.
- Show Cancel processing only when cancellation is safe and the retained-result behaviour is defined.

### Stats and Reader

- Preserve their current information architecture.
- Limit change to the shared shell, typography, material, state treatment, definitions, contrast, and focus.
- Preserve Stats metrics and accessible alternatives.
- Preserve Reader's library-first flow, reading position, voice choice, bookmarks, search, summary, and progress.

## Accessibility and state contract

- Focus order follows the visible task order.
- Every control receives a crisp high-contrast ring; glow is never the only focus indicator.
- Closing overlays or disclosures must restore focus to the invoking control.
- Loading, empty, degraded, error, and success states use a stable shape with a plain heading, one explanation, preserved usable content, and at most one primary recovery action.
- Reduced motion removes shimmer, pulse, travelling waveform movement, hover lift, and large stage transitions.
- Recording remains understandable without motion through a static indicator, explicit label, elapsed time, and fixed level bars.

## Screenshot evidence

| Evidence | File |
|---|---|
| A Home, desktop | [`01-focus-stage-home-desktop.png`](prototypes/mumble-centred-experience/screenshots/01-focus-stage-home-desktop.png) |
| B Home, desktop | [`02-centred-workbench-home-desktop.png`](prototypes/mumble-centred-experience/screenshots/02-centred-workbench-home-desktop.png) |
| C Home, desktop | [`03-guided-spine-home-desktop.png`](prototypes/mumble-centred-experience/screenshots/03-guided-spine-home-desktop.png) |
| Deck command bar and image action | [`04-focus-stage-deck-image-action.png`](prototypes/mumble-centred-experience/screenshots/04-focus-stage-deck-image-action.png) |
| Meetings recording instrument | [`05-workbench-meetings-recording.png`](prototypes/mumble-centred-experience/screenshots/05-workbench-meetings-recording.png) |
| Settings route truth | [`06-focus-stage-settings-route-truth.png`](prototypes/mumble-centred-experience/screenshots/06-focus-stage-settings-route-truth.png) |
| Mumble Find empty/loading/error | [`screenshots/`](prototypes/mumble-centred-experience/screenshots/) |
| Reduced-motion loading | [`08-find-loading-reduced-motion.png`](prototypes/mumble-centred-experience/screenshots/08-find-loading-reduced-motion.png) |
| Visible keyboard focus | [`09-focus-stage-keyboard-focus.png`](prototypes/mumble-centred-experience/screenshots/09-focus-stage-keyboard-focus.png) |
| Narrow reflow | [`10-guided-spine-home-narrow.png`](prototypes/mumble-centred-experience/screenshots/10-guided-spine-home-narrow.png) |
| Processing Island Cancel | [`11-guided-spine-island-cancel.png`](prototypes/mumble-centred-experience/screenshots/11-guided-spine-island-cancel.png) |

## Verification record

The local browser verifier checks the rendered prototype rather than only searching source text.

Command:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "Development Files\Research\prototypes\mumble-centred-experience\verify-prototype.ps1" -Capture
```

Result on 24 July 2026:

```text
ok: true
checks: 35
failed: []
```

Coverage includes three directions; exactly six navigation destinations; every required surface; Deck image and web actions; Meetings Stop & save; Settings route names; Mumble Find loading, empty, and error states with no Web Search fallback; reduced motion; visible programmatic focus; desktop and narrow overflow; narrow navigation reflow; and processing Island Cancel labelling.

## Prototype boundaries

- The prototype is standalone HTML, CSS, and JavaScript under `Development Files/Research/prototypes/`.
- It makes no network requests and does not read or write Mumble settings or data.
- It is not imported, packaged, or referenced by the production application.
- Representative controls are inert; screenshots prove hierarchy and rendered states, not native hotkeys, focus restoration, dragging, audio capture, cancellation safety, provider routing, or performance.
- Production implementation should begin from this contract only after a separate implementation ticket names the affected product files, runtime truth source, tests, and acceptance evidence.
