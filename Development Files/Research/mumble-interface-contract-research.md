# Mumble centred interface and information-architecture contract

- **Wayfinder decision:** Define the homepage, navigation, web-search, and centred interface contract
- **Research snapshot:** `6f12ed73` (`codex/wayfinder-6-interface-contract`)
- **Date:** 24 July 2026
**Scope:** Home, primary navigation, global shortcuts, Deck, Stats, Meetings, Reader, Settings, local app/file launcher, web search, and the primary and companion Islands. This report records decisions and evidence only; it does not change product code or supply a visual prototype.

## Decision in one page

Mumble should keep six persistent destinations: **Home, Deck, Stats, Meetings, Reader, and Settings**. A launcher or search command is a momentary global tool, not a destination, so neither belongs in the top-level navigation.

The two currently conflated discovery tools receive separate names and contracts:

- **Web Search** means sending selected, typed, or dictated words to the user's chosen provider: Perplexity, Brave Search, or Google. The generic word **Search** is reserved for this internet-facing action where a shorter label is necessary.
- **Mumble Find** means the local, private app/file/folder launcher. Its visible action label is **Find apps & files**. Do not call the product Finder or Launchpad: Apple lists both as registered trademarks and tells third parties not to use Apple trademarks as part of a product or service name without permission.[^apple-list][^apple-guidelines]

Home remains a centred, voice-first orientation surface. Deck, Meetings, Reader, Stats, and Settings use the same centred page frame and premium gold-black material system, while dense lists and control groups may be left aligned *inside* that centred frame for faster scanning. “Centred” is a composition rule, not a requirement to centre every sentence and button.

The present ten-minute dictation limit is real in this build, but it is a deliberate memory/upload policy, not a fundamental speech-recognition limit. Mumble already proves that disk-backed recording and bounded chunk processing are possible in its Meetings pipeline. Remove the ten-minute sentence from the Home hero; until long dictation is implemented, explain it contextually as **“Short dictation currently stops after 10 minutes. Use Meetings for longer recordings.”** Do not market unlimited dictation before the segmented path ships.

The primary Island remains a focus-preserving status display. Its companion becomes the **Island Control Rail**, a coherent extension of the same shape and material. During active dictation it must expose a labelled **Stop** action as well as the same keyboard toggle. An `X` alone is ambiguous between “hide” and “stop”; use a stop-square icon plus **Stop**, with an accessible name and a sufficiently large target.

## What the current source actually does

The running source is more advanced than some of the reported screenshots, but it also confirms the underlying product confusion.

| Area | Verified current behaviour | Consequence |
| --- | --- | --- |
| Home and navigation | The static navigation contains Home, Deck, Stats, Meetings, Reader, and Settings. The local launcher loader then dynamically inserts another top-level button labelled **Search**, and that button opens a separate window.[^code-nav][^code-search-loader] | Preserve the six static destinations and remove the injected launcher tab. |
| Duration | Normal dictation stops at 600 seconds. Audio is accumulated in memory; cloud transcription rejects a request over the same limit. Meetings are disk-backed, can run for four hours, and use ten-minute long-form chunks.[^code-limits][^code-dictation-cap][^code-cloud-cap] | The current claim is technically true, but the limit is architectural policy and can be replaced by disk-backed segmentation. |
| Web search | Perplexity, Brave Search, and Google URL templates still exist. Deck can send its selected item to the chosen provider, and voice/action processing can also call the web-search path.[^code-web-search][^code-deck-web] | Web Search was not deleted; it lost a distinct product identity and global entry point. |
| Mumble Find | The local engine loads a persistent index, refreshes it in the background, ranks apps first, limits normal idle results to 12, and caches native icons. The UI asks for icons only for the first 16 rendered app slots.[^code-find-index][^code-find-query][^code-find-icons] | Do not commission a duplicate caching system. Measure the existing one and improve its cold path, persistence, and visible-result hydration. |
| Launcher window | The window is frameless, fixed-size, always on top, and configured with `easy_drag=False`; its header already carries pywebview's drag-region class. The official pywebview example confirms this is a supported arrangement.[^code-find-window][^code-find-header][^pywebview-drag] | Treat the user's inability to drag as a real installed-runtime defect. Keep a dedicated drag region and test it; do not make the entire results surface draggable. |
| Launcher shortcut | The launcher shortcut refuses to run while Mumble is recording, starting/stopping, or processing. The handler only requests “show”; it does not query visibility and toggle closed.[^code-find-hotkey] | The launcher lifecycle must be independent of dictation and implement one binding as show/hide. |
| Deck | The content tabs are strong, but the view still contains five primary toolbar actions, a More menu with five more actions, a separate search/sort row, an always-open Smart Mode/Presets block, and a selection action bar.[^code-deck-toolbar][^code-deck-workspace] | Retain the content browser, but consolidate command hierarchy and make shaping contextual. |
| Clipboard images | Image rows have a thumbnail, but their paste action uses the same `type` glyph used for text insertion.[^code-deck-image] | Use a distinct “paste image”/clipboard-image symbol and treat the thumbnail as the visual anchor. |
| Stats | Stats already provides accessible names, live loading state, chart summaries, a daily-values table, and separate Reader/Meetings activity.[^code-stats] | Preserve the information architecture; polish definitions and empty/loading states instead of redesigning the page. |
| Meetings | The current markup and final CSS already aim at a centred primary capture panel, a highlighted Record card, route disclosure, a three-step flow, and a separate live state with Pause and Stop & Save.[^code-meetings][^code-meetings-style] | Keep this state model. The runtime observation that it still looks overly dark means the rendered standard/enhanced variants need visual verification, not another unrelated widget. |
| Reader | Reader already separates connection, voice choice, library, new-document input, playback controls, document search, summary, and reading pane.[^code-reader] | Preserve the flow and reduce secondary density; do not replace the Reader's successful core. |
| Settings | Settings already explains that transcription handles audio and processing receives text, exposes effective route status, and keeps advanced provider details collapsible. It still labels its main destinations “Transcription” and “Processing” and uses “Pro Mode”.[^code-settings-overview][^code-settings-routes] | Keep the two-stage model but rename it in ordinary language and expose comparable privacy/speed/quality/cost facts. |
| Islands | The main status pill is deliberately click-through. The companion rail is deliberately smaller, has 20-pixel-tall chips with three-pixel gaps, offers modes/language/Deck while listening, and has no dictation-stop callback.[^code-island-controller][^code-island-layout][^code-island-visible] | Add a real stop command and redesign the rail as one family with the status pill. Its current targets are also too dense for the accessibility contract below. |

## Product and navigation contract

### 1. Persistent destinations

The top navigation is an answer to “where am I going to work?” It contains only:

1. **Home** — orientation, current status, recording entry, and the complete shortcut summary.
2. **Deck** — captured and generated content, selection, reuse, and shaping.
3. **Stats** — understandable activity evidence.
4. **Meetings** — long-form capture, import, processing, review, and export.
5. **Reader** — library, listening, document navigation, and reading progress.
6. **Settings** — behaviour, routes, devices, appearance, data, and maintenance.

Mumble Find is a transient launcher. Web Search is a transient command. Both belong on Home, in Settings shortcuts, and wherever context makes the action useful; neither receives a destination tab. This also resolves the current dead-tab pattern where “Search” visually promises a page but launches another window.

### 2. Discovery names and verbs

Use these exact user-facing names:

| Meaning | Feature name | Action labels | Setting key direction |
| --- | --- | --- | --- |
| Local apps/files/folders | **Mumble Find** | **Find apps & files**, **Open Mumble Find** | migrate `search_hotkey` to `launcher_hotkey` |
| Internet provider query | **Web Search** | **Search the web**, **Search with Perplexity/Brave/Google** | migrate `search_engine` to `web_search_engine`; add `web_search_hotkey` |
| Filtering content already on screen | No product name | **Filter Deck**, **Find in document**, **Find in transcript** | local component state only |

The word *launcher* may appear in help text for technical readers, but **Find apps & files** is the clearer beginner-facing verb. Microsoft itself describes PowerToys Run as a “quick launcher” for applications, folders, and files, which supports launcher as a category rather than a proprietary product imitation.[^powertoys-run]

Do not use **Finder**. Apple explicitly lists Finder as a registered mark for operating-system software, and its third-party guidance prohibits using an Apple trademark as part of another product or service name absent permission.[^apple-list][^apple-guidelines] This is a conservative product-naming decision, not legal advice.

### 3. Shortcut contract

Home and Settings must show five separate global actions:

| Action | Recommended default | Required behaviour |
| --- | --- | --- |
| Dictate | `Ctrl + Win` | Press once to start, again to stop. |
| Paste latest | `Ctrl + Alt + V` | Paste the newest result without opening Deck. |
| Open Deck | `Ctrl + Alt + D` | Toggle the Deck without changing dictation state. |
| Mumble Find | `Ctrl + Alt + F` | Show/hide the launcher, including while dictation is active. |
| Web Search | `Ctrl + Alt + S` | Search selected text when available; otherwise enter a focused query flow. |

`Alt + the physical key below Escape` should be offered through shortcut capture, not forced as the default. Its printed character varies by keyboard layout, and the owner already uses that chord for Flow Launcher. Mumble must detect both internal Mumble conflicts and failed global registration, then keep the previous working binding rather than displaying a dead shortcut.

The launcher must appear immediately on the first press and hide on the second. Closing it restores the previous focus without ending, pausing, or redirecting an active dictation. The current launcher already traps dialog focus and restores the prior element in embedded mode; preserve that behaviour when the native-window lifecycle is corrected.[^code-find-focus]

## Homepage contract

### Keep

- The centred hero, voice-first headline, waveform, primary dictation action, and direct Deck action.
- A complete, editable global-shortcut panel.
- The current visual hierarchy: one premium hero followed by progressively quieter capability and trust surfaces.
- Real route and privacy wording rather than broad “AI” claims.

### Change

- Replace the current local-launcher row labelled **Search** with **Mumble Find — Apps, files & folders**.
- Add a distinct **Web Search — Perplexity, Brave, or Google** row and its binding.
- Remove the ten-minute sentence from beneath the primary call to action. A time cap is operational detail, not the homepage's main benefit.
- Until segmented long dictation ships, put this honest sentence in contextual help and recording-limit feedback: **“Short dictation currently stops after 10 minutes. Use Meetings for longer recordings.”**
- After segmented dictation is verified, replace it with outcome wording such as **“Keep speaking; Mumble saves long dictation safely in the background.”** Do not switch the copy before the implementation and tests exist.

### Why segmentation is viable

The current cap protects an in-memory buffer and keeps a single cloud upload under a provider-size ceiling. Nothing in the speech-to-text domain requires one monolithic ten-minute file. Mumble's own Meetings path already records to disk and chunks long-form processing. A later runtime ticket can reuse that architecture for normal dictation with these non-negotiable semantics:

- append audio to bounded disk segments while capture continues;
- transcribe completed segments without losing the final partial segment;
- preserve ordering and language/model context across boundaries;
- keep partial text tentative and insert only the final joined result unless the user explicitly enables live insertion;
- allow Stop and Cancel to finish or discard safely;
- write one logical history item and one paste result;
- clean temporary audio on success, failure, crash recovery, and explicit discard;
- bound memory and disk usage and communicate recovery honestly.

This is a runtime architecture decision for the duration/latency programme, not product code to add in this ticket.

## Shared centred visual contract

1. **Centred frame:** Every destination uses the existing centred page container and a stable maximum width. The primary purpose, heading, and first decision sit on the centre line.
2. **Readable internals:** Lists, transcripts, filenames, settings labels, and multi-control toolbars are left aligned inside the centred surface. This preserves scan order while maintaining the owner's centred composition.
3. **One material family:** Primary surfaces use warm black glass, a restrained gold rim, soft depth, and a single highlight/shimmer layer. Workspace surfaces are quieter. Nested panels must not each compete with their own glow.
4. **Gold has meaning:** Gold identifies primary actions, active selections, and the Mumble frame. It cannot be the sole state signal; text, icon, shape, or programmatic state accompanies it.
5. **State over spectacle:** Recording, processing, success, warning, and error states have stable shapes and labels. Motion supports the state but never carries the state alone.
6. **Effects do not change usability:** Light, Standard, and Full-effects modes may change blur, ambient particles, and shimmer. They may not change layout, contrast, target size, focus treatment, available controls, or information.
7. **Reduced motion is end to end:** The Web UI already has several `prefers-reduced-motion` guards. Extend the same preference to the native Islands and stop waveform travel, shimmer, positional lift, and looping pulse while leaving a static recording/status cue. W3C advises supporting reduced-motion preferences and disabling non-essential interaction animation.[^wcag-motion]

## Surface contracts

### Deck

The content-type selector is the Deck's strongest organising element and remains first. Everything below it follows three levels:

1. **Browse:** content type, starred state, one Deck filter field, and sort.
2. **Act on an item:** Paste latest, Copy latest, Capture, and context-specific **Search the web**.
3. **Shape a selection:** numbered selection summary, one compact Smart Mode selector, one compact Preset selector, and a primary Run action.

Pin, Refresh, Keyboard help, pre/post-AI display, and Clear belong in one labelled overflow menu. Pin is a toggle with icon, text, and `aria-pressed`; “Unpin” must not be communicated by gold text alone. The current Search button's two behaviours must be split: **Search the web** is enabled when an item is selected; **Open Mumble Find** is a separate command and should not be a fallback hidden behind an empty selection.

The always-open grid of modes and presets should become contextual: a compact summary is always discoverable, while the detailed preset catalogue opens when the user chooses to shape content or selects items. This preserves capability without allowing presets to dominate the browsing workspace.

For image clipboard rows:

- make the thumbnail the first visual anchor;
- label the type as **Image** with dimensions/size where available;
- use a clipboard/image or picture-with-paste-arrow glyph, never the text-cursor glyph;
- keep the visible label or tooltip **Paste image** and accessible name **Paste image into previous app**;
- retain Delete as a separate, consistently placed secondary action;
- use the same selection, hover, focus, and action-reveal rules as text rows.

### Stats

Stats already has the right hierarchy: snapshot, daily output, Reader/Meeting activity, mode mix, trends, and rhythms. Keep it calm. Improvements are explanatory rather than structural:

- define “output words”, “active day”, “WPM”, and estimates beside the first occurrence;
- keep the daily-value table and screen-reader summaries paired with every visual chart;
- make empty, loading, partial, and reset states explicit;
- ensure gold bars are not the only way series or current-day states are identified;
- keep destructive Reset outside the reading flow and confirm its exact scope.

### Meetings

Use one centred **Before / During / After** state model.

- **Before:** one premium capture panel. Record meeting is the gold primary action; Import audio is a quieter peer. The route badge says Local or names the cloud provider before capture begins.
- **During:** the same shell transforms rather than being replaced by a visually unrelated widget. Show state, elapsed time, static/live level feedback, Pause/Resume, and a prominent **Stop & save** action.
- **After:** move attention to the searchable library and then the selected meeting's transcript/outcomes.

The current code already models these states. Visual verification must compare Standard and Full-effects rendering against the user's observation that the recording widget appears like a flat dark block. The pass condition is not merely “gold CSS exists”; the rendered widget must share the same border lift, warm highlights, depth, and focus treatment as Home and Deck.

### Reader

Reader remains a library-first experience. Keep Continue Reading, library filtering, AI voice choice, new-document input, playback, in-document find, bookmarks, summary, and reading progress. Refine it by:

- keeping provider/model choices secondary to the human choice of voice;
- disclosing when playback or summary uses paid credits before the action;
- preserving focus and reading position when opening/closing Find and Summary;
- keeping the player controls sticky only when they do not obscure focused document content;
- offering a no-motion highlight treatment when reduced motion is active;
- retaining format and parsing limitations next to import, not in the primary reading flow.

### Primary Island and Island Control Rail

The primary Island answers only **what is Mumble doing?** It remains click-through so it cannot steal the user's text cursor. The Control Rail answers **what can I do now?** and is the only clickable floating surface.

During listening, order the Control Rail as:

1. active mode/language state;
2. Deck when useful;
3. a separated **Stop** action at the trailing edge.

The Stop control must call the same controller transition as the dictation hotkey; it must not merely hide the Island. Keep the user's focused application active and provide immediate “Stopping” then “Transcribing” feedback. During post-recording processing, show Cancel only if the backend can safely cancel and define the retained result; otherwise do not display a false control.

The current rail's 20-pixel chips separated by three pixels are too small/dense for the target contract. WCAG 2.2 requires a 24-by-24 CSS-pixel target or sufficient spacing; W3C's example notes that 20-pixel targets need four pixels of separation.[^wcag-target] Use at least 28-pixel rail targets, and aim for 36 pixels for Stop because it is time-critical.

Visually, the rail uses the same corner logic, warm rim, top light, and gold material as the primary pill. It may be quieter, but not thinner, flatter, or less legible. In reduced-motion mode, replace pulse/wave travel with a static red recording dot, elapsed timer, and explicit text.

### Settings

Rename the visible destinations:

- **Overview**
- **Speech to text** (currently Transcription)
- **Text shaping** (currently Processing)
- **Deck & data**
- **System**

The Overview keeps the two-stage diagram and answers, for each stage:

| Question | Speech to text | Text shaping |
| --- | --- | --- |
| What does it receive? | microphone audio | resulting transcript text |
| What engine is active? | named local model/runtime or cloud provider/model | local rules/model or hosted provider/model |
| Where does it run? | This device / Cloud | This device / Cloud |
| What leaves the device? | Nothing / recorded audio | Nothing / transcript text |
| Trade-off | privacy, offline ability, load, speed, accuracy | privacy, speed, output quality, cost |
| Cost | free local / provider terms | free local / provider terms |

Replace **Pro Mode** with the explicit route choice **Text shaping: On this device / Hosted provider**. Keep provider keys and exact models in an Advanced disclosure. Route status must always show the *effective* engine, including fallbacks, rather than merely echoing the requested setting.

Basic/Standard/Enhanced currently names visual effects, while the owner also uses Basic/Enhanced to describe processing state. Remove the collision by using **Light**, **Standard**, and **Full effects** for appearance. The separate persistence defect where the menu shows the wrong active value remains a settings-state ticket; this contract requires that requested, persisted, and effective values be distinct in code and that the selected control reflect the effective saved value after hydration.

## Accessibility and interaction acceptance contract

These requirements apply to every surface and both floating windows:

1. **Keyboard parity:** Every pointer action has a keyboard route. W3C WCAG 2.2 requires functionality to be operable through a keyboard interface.[^wcag-keyboard]
2. **Visible focus:** Every focusable control has a persistent visible focus indicator. W3C describes this as necessary for sighted keyboard operation.[^wcag-focus]
3. **Logical focus order:** Focus follows the visual task sequence—destination, browse/filter, results, contextual actions—and does not jump into hidden or inactive panels.[^wcag-order]
4. **Focus restoration:** Closing Mumble Find, a Deck overflow, a Reader summary, or a meeting detail returns focus to the invoking control or safely to the prior app for a native overlay.
5. **Current destination:** Primary navigation exposes the selected destination programmatically, for example with `aria-current="page"`; current source changes only the visual `active` class.[^code-nav-state]
6. **Names match labels:** Icon-only controls have accessible names; visible “Stop”, “Paste image”, “Search the web”, and “Find apps & files” wording appears in the accessible name.
7. **Target size:** Targets are at least 24 by 24 CSS pixels or meet the spacing exception; time-critical actions aim for 36–44 pixels.[^wcag-target]
8. **No hover dependency:** Deck row actions become visible on keyboard focus as well as hover, and their location does not shift between input methods.
9. **Status announcements:** Loading counts, route changes, recording state, errors, and completed actions use concise live-region announcements without repeating continuously.
10. **Reduced motion:** Honour both the operating-system preference and Mumble's Light setting. Status remains understandable with all non-essential movement removed.[^wcag-motion]
11. **Reflow and zoom:** At narrower widths, control groups wrap into the same semantic order rather than shrinking targets or clipping labels.
12. **Drag without exclusion:** Mumble Find has a clear drag region that excludes its close button and other controls. pywebview officially supports `.pywebview-drag-region` with `frameless=True, easy_drag=False`; this exact installed path needs a regression test.[^pywebview-drag]

## Implementation boundaries and follow-on evidence

This research resolves the interface contract, but several later tickets must supply evidence before claiming the experience is complete:

- **Duration/latency:** design and measure disk-backed segmented dictation, crash recovery, ordering, memory, temporary-file cleanup, and final insertion.
- **Launcher lifecycle/performance:** record hotkey-to-first-paint, cache-load, query, and icon-hydration timing; test open/close during every dictation state; verify dragging in the packaged WebView2 build.
- **UI prototype/review:** render Deck command hierarchy, Meetings Standard/Full-effects states, and the unified Island/Control Rail at normal and reduced motion. This owner-facing prototype review is a later HITL gate, not something this unattended research ticket can truthfully approve.
- **Accessibility:** keyboard-only walkthrough, 200% zoom/reflow, Windows text scaling, screen-reader smoke test, visible-focus audit, high-contrast check, and target-size measurement.
- **Settings state:** separately diagnose the Basic/Enhanced hydration mismatch; do not mask it with display-only forcing.
- **Platform parity:** macOS and Linux must follow the same names and mental model, with platform-native shortcuts and overlay seams verified separately.
- **Legal:** “Finder” is rejected under the conservative naming rule above. Any desire to use it despite Apple's published guidance requires qualified legal review or written permission.

## Sources

### Authoritative external sources

[^apple-list]: Apple, [Trademark List](https://www.apple.com/legal/intellectual-property/trademark/appletmlist.html) — lists **Finder®** as operating-system software and **Launchpad®** as an operating-system feature.
[^apple-guidelines]: Apple, [Guidelines for Using Apple Trademarks and Copyrights](https://www.apple.com/legal/intellectual-property/guidelinesfor3rdparties.html) — prohibits third parties from using an Apple trademark as part of a company, product, or service name except where specifically permitted.
[^powertoys-run]: Microsoft, [PowerToys Run utility](https://learn.microsoft.com/en-us/windows/powertoys/run) — defines the category as a quick launcher for apps, folders, and files; documents a configurable show/hide activation shortcut.
[^pywebview-drag]: pywebview, [Drag Region example](https://pywebview.flowrl.com/examples/drag_region) and [API drag-area documentation](https://pywebview.flowrl.com/api/#drag-area) — documents `.pywebview-drag-region` for a frameless window with `easy_drag=False`.
[^wcag-keyboard]: W3C, [WCAG 2.2, Success Criterion 2.1.1 Keyboard](https://www.w3.org/TR/WCAG22/#keyboard) — all functionality must be operable through a keyboard interface except genuinely path-dependent input.
[^wcag-focus]: W3C WAI, [Understanding SC 2.4.7 Focus Visible](https://www.w3.org/WAI/WCAG22/Understanding/focus-visible) — keyboard focus needs a persistent visible indicator.
[^wcag-order]: W3C WAI, [Understanding SC 2.4.3 Focus Order](https://www.w3.org/WAI/WCAG22/Understanding/focus-order) — sequential focus must preserve meaning and operability.
[^wcag-target]: W3C WAI, [Understanding SC 2.5.8 Target Size (Minimum)](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum) — 24-by-24 CSS pixels or sufficient spacing; includes the 20-pixel/four-pixel-spacing example.
[^wcag-motion]: W3C WAI, [Understanding SC 2.3.3 Animation from Interactions](https://www.w3.org/WAI/WCAG22/Understanding/animation-from-interactions) — supports disabling non-essential animation and honouring reduced-motion preferences.

### Current repository evidence

[^code-nav]: [`Internal/app/webui/index.html`, primary navigation](../../Internal/app/webui/index.html#L33-L55).
[^code-search-loader]: [`Internal/app/webui/system-search-loader.js`, injected Search navigation button](../../Internal/app/webui/system-search-loader.js#L7-L35).
[^code-limits]: [`Internal/app/recording_limits.py`, dictation/meeting/chunk limits and rationale](../../Internal/app/recording_limits.py#L1-L23).
[^code-dictation-cap]: [`Internal/app/mumble.py`, recording callback stops at the dictation sample cap](../../Internal/app/mumble.py#L1108-L1134).
[^code-cloud-cap]: [`Internal/app/transcription.py`, single cloud request guard](../../Internal/app/transcription.py#L232-L248).
[^code-web-search]: [`Internal/app/mumble.py`, web provider templates and browser opening](../../Internal/app/mumble.py#L3780-L3843).
[^code-deck-web]: [`Internal/app/webui/app.js`, Deck selected-item web-search behaviour](../../Internal/app/webui/app.js#L8170-L8186).
[^code-find-index]: [`Internal/app/experimental/system_search/engine.py`, cache load and background refresh](../../Internal/app/experimental/system_search/engine.py#L108-L142) and [cache refresh](../../Internal/app/experimental/system_search/engine.py#L194-L280).
[^code-find-query]: [`Internal/app/experimental/system_search/engine.py`, bounded ranking and web fallback](../../Internal/app/experimental/system_search/engine.py#L679-L739).
[^code-find-icons]: [`Internal/app/experimental/system_search/engine.py`, icon cache](../../Internal/app/experimental/system_search/engine.py#L741-L774) and [`ui.js`, first-16 hydration](../../Internal/app/experimental/system_search/ui.js#L517-L573).
[^code-find-window]: [`Internal/app/webui_shell.py`, native launcher window configuration](../../Internal/app/webui_shell.py#L3405-L3425).
[^code-find-header]: [`Internal/app/experimental/system_search/ui.js`, launcher dialog and drag-region header](../../Internal/app/experimental/system_search/ui.js#L176-L212).
[^code-find-hotkey]: [`Internal/app/mumble.py`, launcher shortcut blocked by dictation state](../../Internal/app/mumble.py#L3746-L3765).
[^code-find-focus]: [`Internal/app/experimental/system_search/ui.js`, modal inert state and focus restoration](../../Internal/app/experimental/system_search/ui.js#L298-L346).
[^code-deck-toolbar]: [`Internal/app/webui/index.html`, Deck toolbar and filter row](../../Internal/app/webui/index.html#L780-L939).
[^code-deck-workspace]: [`Internal/app/webui/index.html`, always-open shaping workspace and selection actions](../../Internal/app/webui/index.html#L941-L1051).
[^code-deck-image]: [`Internal/app/webui/app.js`, clipboard-image row and text-style paste glyph](../../Internal/app/webui/app.js#L2614-L2625).
[^code-stats]: [`Internal/app/webui/index.html`, Stats structure and accessible chart alternatives](../../Internal/app/webui/index.html#L1054-L1197).
[^code-meetings]: [`Internal/app/webui/index.html`, Before/During/After meeting flow](../../Internal/app/webui/index.html#L1456-L1546).
[^code-meetings-style]: [`Internal/app/webui/remaster.css`, premium meeting capture/live surfaces](../../Internal/app/webui/remaster.css#L1095-L1276).
[^code-reader]: [`Internal/app/webui/index.html`, Reader connection/library/player flow](../../Internal/app/webui/index.html#L1204-L1453).
[^code-settings-overview]: [`Internal/app/webui/index.html`, Settings two-stage overview](../../Internal/app/webui/index.html#L1661-L1725).
[^code-settings-routes]: [`Internal/app/webui/index.html`, text-processing route disclosure](../../Internal/app/webui/index.html#L2195-L2263) and [speech-to-text route disclosure](../../Internal/app/webui/index.html#L2388-L2446).
[^code-island-controller]: [`Internal/app/overlay.py`, click-through primary Island and companion rail](../../Internal/app/overlay.py#L742-L832).
[^code-island-layout]: [`Internal/app/island_render.py`, 20-pixel chips, three-pixel gaps, and no Stop target](../../Internal/app/island_render.py#L678-L783).
[^code-island-visible]: [`Internal/app/overlay.py`, listening-time control visibility](../../Internal/app/overlay.py#L1472-L1483).
[^code-nav-state]: [`Internal/app/webui/app.js`, visual-only primary navigation state](../../Internal/app/webui/app.js#L1524-L1529).
