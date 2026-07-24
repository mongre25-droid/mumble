# Mumble Find launcher architecture research

- **Wayfinder decision:** Choose the launcher indexing, rendering, and shortcut lifecycle architecture ([issue #7](https://github.com/mongre25-droid/mumble/issues/7))
- **Research snapshot:** `5e19b1ef` (`codex/wayfinder-7-launcher`)
- **Date:** 24 July 2026
- **Scope:** Evidence and architecture only. This report does not change product behaviour.

## Decision in one page

Keep Mumble's existing Python and pywebview launcher surface, but replace its single, eager JSON catalogue with a **two-provider search architecture**:

1. a small, persistent **application catalogue**, refreshed in the background from platform-native application sources—Start menu/App Paths/`Get-StartApps` on Windows, application bundles and launch metadata on macOS, and `.desktop` entries on Linux; and
2. a **file/folder provider** that asks the operating system's existing search index for a bounded candidate set, then applies Mumble's own fuzzy and usage ranking.

The provider contract is mandatory on **Windows, macOS, and Linux**, while its implementation is platform-native. Windows is the first measured implementation gate and should query **Windows Search `SystemIndex`**. macOS should query the system Spotlight metadata store through `MDQuery` or `NSMetadataQuery`; Apple documents bounded result counts, scoped searches, asynchronous collection, cancellation, and live updates.[^apple-mdquery][^apple-metadata-guide] Linux should negotiate an available indexed service: GNOME LocalSearch over its D-Bus SPARQL endpoint, KDE Baloo through `Baloo::Query`, or a clearly labelled filename-only `plocate` fallback.[^linux-localsearch][^linux-baloo][^linux-plocate] None of these providers may recursively walk the user's folders on a shortcut or query path.

Flow Launcher, PowerToys Run, and the current Windows implementation in Vicinae independently support the bounded operating-system-index pattern on Windows.[^flow-explorer][^powertoys-indexer][^vicinae-windows]

Keep the launcher window alive but hidden while Mumble is running. The shortcut must call a real `toggle` command, not an unconditional `show`, and it must work during recording and processing. Showing Mumble Find must capture the previously focused application/window or compositor surface through the platform adapter; hiding it must restore or naturally return focus to that target where the platform permits. Dictation continues independently and retains its original paste target.

Render a maximum of **12 first-page rows**. This is already the current visible limit, so the browser does not need a complex virtual-list library. Text and placeholders render first; only those visible rows request icons. Further results use explicit paging or a cursor. Cancel obsolete backend queries rather than merely ignoring their late JavaScript responses.

Treat the two kinds of dragging as separate contracts:

- the header is the only window-movement region; and
- a file/folder result begins a native operating-system file drag, resolved from an opaque result ID: OLE `CF_HDROP` on Windows, AppKit file-URL pasteboard data on macOS, and a GTK/GDK file content provider on Linux. No platform accepts a path supplied by browser JavaScript.[^windows-dnd][^apple-dnd][^linux-dnd]

Mumble Find remains a **shortcut-only overlay**, not a seventh navigation destination. Home and Settings may expose **Mumble Find — Find apps & files**, but the dynamically injected Search tab must be removed. Web Search remains a separate command and must not appear as a fallback row inside local results. This follows the approved centred-interface contract.[^interface-contract]

## Why the current design does not scale

The current implementation has useful foundations: opaque result IDs, atomic cache replacement, a bounded first page, background refresh, usage/favourite scoring, and an icon batch limit. Its slow paths are architectural rather than cosmetic.

| Path | Verified current behaviour | User impact |
| --- | --- | --- |
| Window startup | The search engine is created lazily. It synchronously loads a JSON cache when the search API is first used. If the web shell is absent, the controller starts a new process, waits, and polls its command port before the launcher can appear.[^current-window] | The first shortcut press pays process, webview, cache, and render startup costs. |
| File discovery | Python recursively walks Desktop, Documents, Downloads, Pictures, Music, and Videos, with a 75,000-item cap. A stale cache triggers another whole walk; there is no incremental file watcher.[^current-engine] | Large or network-backed folders can make refresh extremely slow and waste work already done by Windows Search. |
| Query | Every query copies the catalogue, normalises strings again, scans every item, and sorts all matches. The 75 ms browser delay reduces call frequency but does not reduce the cost of one call.[^current-engine][^current-ui] | A cache that is safe to store is not necessarily safe to search interactively. At the configured maximum, typing stalls for about a second. |
| Cancellation | JavaScript numbers requests and ignores a stale response, but the Python search that produced it keeps running.[^current-ui] | Fast typing can leave several expensive searches competing for the same process. |
| Icons | The browser renders all returned rows and then requests up to 16 application icons. Python extracts them sequentially and keeps only a 128-entry memory cache.[^current-icons] | A cold icon path can lag after the row, and the work is repeated across process restarts. |
| Window lifecycle | `_ensure_search_front` always shows and raises the window even though hidden state is tracked. The shortcut handler always calls this show path and refuses to run while recording, busy, or processing.[^current-window][^current-hotkey] | The same shortcut cannot hide Mumble Find and cannot open it during dictation. |
| Focus | The embedded overlay remembers a prior browser element, but the native launcher does not explicitly preserve the external foreground window.[^current-ui][^current-window] | Hiding the launcher can return focus unpredictably; active dictation risks targeting the wrong window. |
| Dragging | The header uses pywebview's drag-region class, but result rows are not draggable.[^current-ui][^pywebview-drag] | Window movement and dragging a result out are currently conflated in the product request but require different mechanisms. |
| Product location | A loader injects a top-level **Search** navigation item that opens a separate window. Non-empty local queries also receive a Web Search fallback result.[^current-nav][^current-engine] | A transient tool looks like a dead destination and local/private search is mixed with an internet action. |
| Platform parity | Windows has the canonical implementation and Linux carries a mirrored search package. The existing macOS port has no Mumble Find package or loader.[^current-platform] | macOS absence is a **parity defect**, not intended product scope. The Linux mirror also needs a native indexed provider and physical verification rather than assuming source similarity proves parity. |

### Current-machine measurements

These measurements were made against the checked-out source on the current Windows machine (AMD64, 16 logical processors, Python 3.12). The existing user cache was read without changing it. Isolated pywebview runs used temporary data and command ports; the already-running installed Mumble process was not stopped or modified.

| Measurement | Result | Sample and boundary |
| --- | ---: | --- |
| Existing cache | 7,263 items: 221 apps, 6,886 files, 156 folders; 3,291,770 bytes | One current user cache |
| Existing cache load | **36.402 ms median** | 5 loads; sample p95 36.415 ms |
| Idle query | **0.632 ms median**, 0.763 ms sample p95 | 50 runs; 221 matches, first 12 returned |
| `mumble` query | **133.186 ms median**, 151.902 ms sample p95 | 50 runs; 16 matches |
| `settings` query | **150.831 ms median**, 163.912 ms sample p95 | 50 runs |
| `document` query | **146.707 ms median**, 177.912 ms sample p95 | 50 runs |
| No local match | **142.511 ms median**, 164.593 ms sample p95 | 50 runs; one Web Search fallback returned |
| Application-only discovery | **1,119.387 ms** | One actual refresh; 221 apps |
| Full configured user-folder discovery | **Did not finish within 124 seconds** | One safely bounded attempt, then terminated |
| Cold process start to result-row DOM commit | **1,261.429 ms median**, range 1,234.468–1,366.356 ms | 6 fresh-process runs using the actual cache |
| Warm show to result-row DOM commit | **97.086 ms median**, range 90.653–277.230 ms | 3 runs |

The DOM mark was taken immediately after the first result rows were committed. It is not a photographed first-pixel measurement, and it deliberately excludes later icon hydration. Three warm samples are not enough to claim a p95.

To test the configured maximum without reading more private user files, a synthetic 75,000-item catalogue was generated in a temporary directory:

| Synthetic measurement | Result |
| --- | ---: |
| Build catalogue | 263.820 ms |
| Save JSON cache | 235.935 ms; 16,654,859 bytes |
| Load JSON cache | 248.021 ms median; about 68,374,128 bytes peak Python allocation |
| Idle query | 23.044 ms median; 27.179 ms sample p95 |
| `project item 12345` | 1,068.896 ms median; 1,165.782 ms sample p95 |
| `item 74999` | 970.600 ms median; 1,091.080 ms sample p95 |
| No match | 896.550 ms median; 988.797 ms sample p95 |

The result is falsifiable and decisive: the current all-items scan and repeated normalisation are not suitable for the declared 75,000-item ceiling. Faster painting alone cannot repair this query path.

### Measurement limits that still matter

- The packaged Mumble executable must be physically tested for first visible pixels, focus restoration, shortcut conflicts, header movement, and native result dragging.
- The system Python used for the isolated benchmark does not include `win32gui`, so native icon extraction was not timed. Icon timing needs a packaged-runtime trace.
- Windows Search availability, index coverage, and behaviour when the service is disabled need tests on representative Windows installations.
- Spotlight, LocalSearch, Baloo, and `plocate` were researched from primary documentation but have not been benchmarked or exercised in Mumble here.
- The current machine's full walk was intentionally stopped at 124 seconds. That is a lower bound, not a completion time.
- Windows remains the first measured implementation gate. Full Mumble Find parity still requires later implementation and physical gates on both macOS and Linux; neither platform may be marked complete from this Windows-only study.

## Pinned implementation comparison

All comparator conclusions below come from source pinned to a commit, not a product screenshot or marketing page.

| Project | Pin and licence | Relevant evidence | Adopt, adapt, or reject |
| --- | --- | --- | --- |
| Flow Launcher | [`07a958d1`](https://github.com/Flow-Launcher/Flow.Launcher/tree/07a958d19fa69a2e10a258b0cf455f0156ed5989), MIT | It has explicit show/hide state, cancellable searches, a virtualising result panel, asynchronous cached image loading, and a Windows-index-backed Explorer provider.[^flow-toggle][^flow-results][^flow-images][^flow-explorer] | **Adopt the patterns:** explicit lifecycle, cancellation, bounded candidate providers, and visible-result icon hydration. Do not import its plugin framework or WPF UI. |
| PowerToys Run | [`fc680d35`](https://github.com/microsoft/PowerToys/tree/fc680d350f74f1f4eec8a64296420e614724e74d), MIT | It records separate cold and warm **hotkey-to-visible** timings, registers a Windows hotkey after the native window handle exists, cancels old searches, virtualises results, watches app sources, and queries `SystemIndex` with a bounded result count.[^powertoys-window][^powertoys-results][^powertoys-apps][^powertoys-indexer] | **Adopt the measurement and provider seams.** Do not take the wider PowerToys runtime or plugin weight. |
| ueli | [`2216a755`](https://github.com/oliverschwendener/ueli/tree/2216a755e3bf5b20bdd7547e79ddc20c4a6ec008), MIT | Its toggler distinguishes visible/focused state and uses a Windows-specific minimise-then-hide step to restore previous focus. Its rows use HTML drag events whose main-process handler starts an explicit file drag. Images are lazy and search indexes are extension-scoped.[^ueli-toggle][^ueli-drag][^ueli-index] | **Adopt the behavioural contracts, then implement them through Mumble's native seam.** Do not migrate to Electron or pass arbitrary paths from browser code. |
| Albert | [`79adc659`](https://github.com/albertlauncher/albert/tree/79adc659fc0ca148e3288adcea86efb278057457), custom Albert licence 1.1 | Its item index uses token and n-gram candidate retrieval before edit-distance ranking, demonstrating why indexed candidates beat a full scan.[^albert-index] Its licence permits personal use and unmodified redistribution but restricts modification and Windows/macOS binary distribution.[^albert-licence] | **Architecture reference only. Reject source reuse** for a normal distributable Windows product. |
| Vicinae | [`97afee7c`](https://github.com/vicinaehq/vicinae/tree/97afee7cf3c4e9068a958c2116fcf5aecc9ab499), GPL-3.0 | Its current Windows adapter queries Windows Search, caps candidates at at least 250 or 20 times the requested limit, then fuzzy-ranks that bounded set. Its newer Linux indexer uses a transactional SQLite/FTS database, incremental events, and bounded candidate scoring.[^vicinae-windows][^vicinae-db] | **Strong current architecture comparator.** Reimplement the ideas; do not copy GPL code unless Mumble deliberately accepts GPL obligations. The newer SQLite indexer is currently wired with Linux-only watcher/background sources, so it is not evidence of Windows parity.[^vicinae-cmake] |

### What the comparison rules out

- A launcher framework rewrite is not justified. The useful ideas fit behind Mumble's existing Python API.
- Recursive crawling is not the right primary Windows file provider when Windows Search already maintains an index.
- Virtualising thousands of browser rows is not the first fix. Candidate retrieval must be bounded before rendering.
- Persisting extracted icon data in the main result database is unnecessary. A separate invalidatable icon cache is easier to bound and repair.
- Neither Albert nor Vicinae is a safe source-copy donor under Mumble's current licensing assumptions.

## Chosen architecture

```text
Shortcut or Home action
        |
        v
Resident window controller ---- captures/restores foreground window
        |
        v
Query coordinator ---- generation number + cancellation + timing trace
        |
        +---- App provider: small SQLite catalogue + platform sources/watchers
        |
        +---- Platform file provider (bounded candidates)
        |       Windows: SystemIndex
        |       macOS: Spotlight MDQuery/NSMetadataQuery
        |       Linux: LocalSearch, Baloo, or labelled plocate fallback
        |
        v
Mumble ranker: pre-normalised fields + usage/favourite boost
        |
        v
First page: 12 rows, text/placeholders first, visible icons second
        |
        +---- Open / reveal / favourite via opaque result ID
        +---- Native file drag via opaque result ID
```

### 1. Storage and provider boundary

Use a versioned SQLite database for Mumble-owned metadata: applications, favourites, usage, exclusions, source fingerprints, and refresh generations. SQLite transactions keep the previous successful generation usable until a replacement commits. Store normalised names and tokens when an item changes, not every time the user types. The schema and result contract are shared; provider code, application discovery, shortcut registration, focus handling, icon extraction, opening/revealing, and drag payloads are platform adapters.

Do **not** copy an operating system's file catalogue into this database merely to search it again. Every file provider returns opaque IDs plus the minimum metadata needed to rank and render, supports a strict result limit, reports coverage/freshness/availability, and cooperates with query cancellation. If the preferred index is unavailable, show a clear degraded state and use only an explicitly selected fallback; never begin a silent full-home crawl on the hotkey path.

| Platform | Required primary provider | Degraded or fallback order | Platform boundary |
| --- | --- | --- | --- |
| Windows | Windows Search `SystemIndex`, queried with escaped/bound parameters and a bounded candidate count.[^windows-search] | Apps remain available; a future user-approved scoped Mumble index may cover a missing location. | Existing Windows measurements are the first implementation and performance gate. |
| macOS | Spotlight metadata through `MDQuery` or `NSMetadataQuery`, with a user-home/configured scope and a provider-side bound (`MDQuerySetMaxCount` when using `MDQuery`; bounded collection and stop when using `NSMetadataQuery`).[^apple-mdquery][^apple-metadata-guide] | Apps remain available; excluded/unindexed locations are reported. A user-approved scoped Mumble index is the final fallback. | Spotlight search results are platform metadata results. Mumble still owns ranking, usage, opaque IDs, and UI. The currently absent macOS implementation is a parity defect. |
| Linux | Select the session-native service: GNOME LocalSearch's D-Bus SPARQL endpoint on GNOME and KDE Baloo's index API on KDE; probe both when the desktop is ambiguous.[^linux-localsearch][^linux-baloo] | `plocate --limit … --null --existing` may provide a labelled **filename-only, potentially stale** fallback when installed. It uses an `updatedb` index and must be invoked with an argument vector, never a shell command.[^linux-plocate][^linux-updatedb] A user-approved scoped Mumble index is last. | Desktop services differ by distribution/session. Provider capability detection is part of startup; Mumble must not install, enable, or reconfigure a service silently. |

The application provider remains Mumble-owned because each platform's launchable-application set is small and useful: Start menu/App Paths/Store metadata on Windows, application bundles and platform launch metadata on macOS, and `.desktop` entries on Linux. Refresh it in the background and watch its known source locations with native mechanisms. File-watcher overflow, provider error, or schema mismatch must schedule a scoped rebuild while the last successful generation remains readable.

The shared provider interface must expose `status`, `query`, `cancel`, `resolve`, `open`, `reveal`, and `begin_drag`, plus capability data for coverage, freshness, paging, and degraded reason. Windows, macOS, and Linux must pass the same contract tests. Platform-specific tests then prove the native provider and desktop integration; one platform's passing results cannot be used as evidence for another.

### 2. Query coordinator and ranking

Each keystroke receives a monotonically increasing generation and cancellation token. Starting generation `n+1` cancels provider and ranking work for `n`; the UI also ignores an impossible late response as a second safety layer.

The query stages are:

1. retrieve bounded candidates from each enabled provider;
2. merge by stable opaque identity;
3. fuzzy-rank only that candidate set using pre-normalised fields;
4. apply bounded favourite, frequency, and recency boosts;
5. return the first 12 rows plus a continuation cursor where the provider supports one.

An empty query returns a small deterministic set of favourites and recent applications. It does not scan files. A local no-match result remains a local no-match result; it never manufactures a Web Search row.

### 3. Rendering and icon order

Keep the current first page at 12 rows. Replacing 12 rows as one DOM batch is acceptable and simpler than introducing a virtual-list dependency. If a later design displays more than 40 simultaneous rows, add windowing then; do not pre-optimise a list users cannot currently see.

The paint order is strict:

1. window frame, input, and empty/loading state;
2. result text and generic type placeholders;
3. application icons for the currently visible rows;
4. icons for a newly revealed page only after that page becomes visible.

Extract application icons during background app refresh where safe, storing them in a bounded disk cache keyed by stable item ID, source modification fingerprint, theme, and scale. Decode/extract misses off the UI thread with bounded parallelism. A missing or corrupt icon keeps the generic placeholder; it never delays a usable row.

### 4. Window, shortcut, focus, and dictation state

Create the search API and its hidden window as part of the normal Mumble web-shell startup. The hotkey path must not spawn a process or parse the full index. If the shell crashes, the controller may restart it, but that is a recovery path with separate telemetry, not the normal interaction.

Expose distinct controller commands:

- `show_find` — used by Home/Settings actions;
- `hide_find` — used by Escape, close, and result activation; and
- `toggle_find` — used by the single global shortcut.

Before `show_find`, capture the platform's prior focus identity and the active dictation target. On `hide_find`, restore or yield back to the prior target only if it is still valid, visible, and not Mumble Find itself:

- **Windows:** retain and validate the foreground window handle; do not loosen system-wide foreground-lock settings.
- **macOS:** retain `NSWorkspace.frontmostApplication` plus the relevant Mumble-side window state, then use AppKit's cooperative activation path. Apple explicitly says an activation request is not guaranteed, so packaged behaviour—not the API call alone—is the gate.[^apple-focus]
- **Linux:** under Wayland, consume the activation token supplied by the Global Shortcuts portal when showing the window and let the compositor arbitrate focus; under X11, use the window manager's supported activation path. Hiding must be tested to return keyboard input to the prior surface rather than forcing focus through an unsupported bypass.[^linux-shortcuts]

Remove the shortcut guard that rejects opening during recording or processing. Launcher visibility is a separate state machine from dictation:

| Dictation state | Mumble Find opens? | Dictation change | Paste target rule |
| --- | --- | --- | --- |
| Idle | Yes | None | Restore prior valid window when hidden |
| Listening | Yes | Continue listening | Preserve the window captured for dictation before Find took focus |
| Stopping/finalising | Yes | Continue finalising | Never paste into Find; use the preserved valid target |
| Result ready | Yes | None | If the original target is gone or unsafe, retain the transcript as pending and explain why it was not pasted |

The existing internal collision detector should remain: it already compares normalised chords, catches modifier subset/superset overlap, registers the replacement before removing the old binding, and rolls back failure.[^current-bindings] However, `keyboard.add_hotkey(..., suppress=False)` does not exclusively reserve a chord against other applications.

Each platform adapter must confirm what was actually registered, display the platform's returned trigger description where available, and preserve the previous working binding if registration is refused. Windows may later use `RegisterHotKey` after modifier-only dictation and accessibility tests.[^registerhotkey] Linux Wayland should prefer the XDG Global Shortcuts portal, whose bind response can be a subset or even an empty set and whose activation signal carries a window activation token.[^linux-shortcuts] macOS needs a packaged global-shortcut adapter and collision test; this report does not claim one is already chosen or verified.

Preserve the approved alternative-shortcut rule: **Alt/Option plus the physical key below Escape must be offered through shortcut capture, never forced as the default.** The printed character changes with keyboard layout, and the owner already uses that physical chord for Flow Launcher. Capture must preserve the physical key identity, show the user's local label, detect the external collision where the platform can report it, and otherwise make the conflict visible in the physical test. The default remains the platform-appropriate rendering of the approved Mumble Find binding, currently `Ctrl + Alt + F` on Windows.[^interface-contract]

### 5. Result dragging and window movement

Keep `.pywebview-drag-region` on a dedicated header and provide an explicit non-drag close button. Test it in packaged Windows, macOS, and Linux builds because correct markup on one webview backend does not prove another backend or window manager.[^pywebview-drag]

For result dragging:

1. browser code sends only an opaque result ID to a native `begin_drag` API;
2. native code resolves the current ID and revalidates that it represents an allowed existing file or folder;
3. Windows starts an OLE drag data object containing `CF_HDROP`;
4. macOS starts an AppKit `NSDraggingSession` whose pasteboard contains validated file URLs; AppKit uses the dragging pasteboard for cross-application transfer.[^apple-dnd]
5. Linux starts a GTK/GDK drag source containing validated `GFile` data, allowing the backend to serialize the platform transfer correctly on Wayland or X11.[^linux-dnd]
6. applications are draggable only when the platform exposes a real portable file object, such as a Windows `.lnk`, macOS application bundle URL, or Linux `.desktop` file whose export is appropriate. Activation-only identifiers remain open-only.

This protects the bridge from arbitrary path injection and clearly separates moving the launcher window from dragging a result into Explorer, Finder, a Linux file manager, an email, or another application.

### 6. Product placement and empty states

Remove the dynamically injected Search navigation item. The six durable destinations remain Home, Deck, Stats, Meetings, Reader, and Settings. Home and Settings expose the command as **Mumble Find — Find apps & files** and display the shortcut actually bound by that platform. Windows begins with the approved `Ctrl + Alt + F` default; macOS and Linux require native registration and collision verification before their displayed defaults are accepted. **Web Search** remains a separate command. Alt/Option plus the physical key below Escape remains a capture option, not a forced default.[^interface-contract]

The overlay has four honest states:

- **Ready, empty query:** favourites and recent apps;
- **Starting:** built-in shell actions and the last valid app catalogue, with a quiet refresh status;
- **No local match:** a local no-match message, with no internet fallback;
- **Provider unavailable:** what is unavailable, what remains searchable, and the smallest recovery action.

## Measurable acceptance gates

The numeric baselines below are derived from current **Windows** evidence rather than invented as marketing goals. Windows is the first measured gate. The same interaction targets are provisional product gates for macOS and Linux until each has its own 30-run packaged baseline; no macOS or Linux pass is claimed by this report.

| Gate | Initial threshold | Why this threshold is defensible |
| --- | ---: | --- |
| Warm hotkey to first result-row DOM commit | **Median ≤ 110 ms** | Current Windows median is 97.086 ms; 110 ms is a rounded 10% non-regression allowance and a provisional parity target elsewhere. |
| Backend non-empty query | **p95 ≤ 75 ms after 30 runs per platform/provider** | The current UI waits 75 ms between typing and dispatch. Current real-cache Windows queries fail this gate at roughly 152–178 ms. |
| App catalogue load | **Windows median ≤ 41 ms; macOS/Linux baseline required** | 41 ms is a rounded 10% allowance over the measured Windows 36.402 ms. File data must not inflate this load on any platform. |
| App catalogue refresh | **Windows ≤ 1.25 s in the current fixture; macOS/Linux baseline required** | Rounded allowance over the one measured Windows 1.119 s refresh; repeat on every platform before treating it as stable. |
| First page | **12 rows maximum** | Preserves the current product contract and keeps rendering bounded. |
| Hotkey-path crawling | **0 directories** | Index refresh and file discovery are forbidden from the interaction path. |
| Obsolete query work | **Cancelled before ranking/return** | A generation test must prove that only the newest query reaches rendering. |
| First row dependency on native icon extraction | **None** | Text and a placeholder must commit before icon work. |
| Toggle correctness | **100% in the state matrix** | A second shortcut press hides; active dictation never pauses, stops, or changes target. |
| Focus correctness | **Prior valid target receives keyboard input after every hide** | Packaged state-matrix gate; the native mechanism differs by platform. |
| Drag correctness | **Native file/folder payload accepted by the platform file manager and one other target** | Packaged platform gate; invalid/stale opaque IDs must fail safely. |

Do not claim a p95 for hotkey-to-visible or icon hydration yet. Instrument at least 30 cold and 30 warm packaged runs and report median, p95, range, cache state, hardware, item count, and whether the mark is DOM commit or observed pixels. PowerToys Run's separate cold/warm hotkey-to-visible events are the right precedent.[^powertoys-window]

### Mandatory platform parity matrix

Every row below is a future test requirement, not a result from this report. Use disposable fixtures or provider fault injection; do not disable or reconfigure the owner's real system index.

| Platform | Normal indexed provider | Provider unavailable or degraded | Shortcut and physical-key capture | Focus and dictation | Native drag |
| --- | --- | --- | --- | --- | --- |
| Windows | 30 non-empty `SystemIndex` queries; p95 ≤ 75 ms; ≤12 rows; stale generations cancelled | Controlled provider failure still shows apps/status within the warm-frame target; **0** directories walked on the hotkey path | 20 alternating presses produce 10 shows/10 hides; rejected external binding keeps the prior shortcut; captured below-Escape key conflicts visibly with Flow Launcher | 10 cycles in each idle/listening/finalising state return typing to the original valid window and never redirect dictation | One file and one folder accepted by Explorer and one other app; 10/10 stale-ID attempts rejected |
| macOS | 30 scoped Spotlight queries through `MDQuery`/`NSMetadataQuery`; p95 ≤ 75 ms; ≤12 rows; cancellation stops superseded queries | Simulated unavailable plus an excluded/unindexed fixture keeps app results, reports incomplete coverage, and performs **0** hotkey-path walks | 20 alternating presses produce 10 shows/10 hides; registration refusal keeps the prior binding; Option+physical-below-Escape capture shows the local key label and is not forced | 10 cycles per dictation state return keyboard input to the prior AppKit app when still valid; unsuccessful activation is reported as a failed gate, not hidden | One file, one folder, and one `.app` bundle accepted by Finder and one other app; 10/10 stale-ID attempts rejected |
| Linux | Run the 30-query p95/limit/cancellation gate separately with LocalSearch on GNOME and Baloo on KDE; label and separately measure `plocate` if used | With each service absent, apps remain usable, capability/status names the fallback, no service is installed/enabled, and **0** hotkey-path walks occur | Under GNOME Wayland and KDE Wayland, 20 presses use the portal session/returned binding and produce 10 shows/10 hides; repeat one X11 session; capture the physical below-Escape key without assuming its printed character | 10 cycles per dictation state on GNOME Wayland, KDE Wayland, and one X11 session return input to the prior surface; missing compositor support is a parity blocker | One file, one folder, and an eligible `.desktop` file accepted by Nautilus and Dolphin plus one other app; 10/10 stale-ID attempts rejected |

## Implementation sequence and safe boundaries

This report is not implementation approval. When issue #7 is converted into implementation tickets, use this order:

1. **Instrumentation and lifecycle:** add cold/warm timing marks, precreate the hidden window, implement show/hide/toggle, and prove focus restoration without changing search ranking.
2. **Dictation independence:** remove the recording guard only after preserved-target tests cover listening, stopping, processing, unavailable target, and launcher result activation.
3. **Provider seam:** separate apps from files behind cancellable provider interfaces while preserving current result IDs and open/reveal behaviour.
4. **Windows file provider and first measured gate:** add bounded `SystemIndex` queries and an explicit unavailable/degraded state; retain the old walk only as a separately approved diagnostic or user-scoped fallback.
5. **macOS parity provider:** implement bounded Spotlight queries, macOS app discovery, global shortcut, cooperative focus, reveal/open, icon, and AppKit drag adapters. Treat the currently missing Mumble Find surface as a defect to close.
6. **Linux parity providers:** implement capability negotiation for LocalSearch, Baloo, and the labelled `plocate` fallback, plus portal/X11 shortcut, focus, reveal/open, icon, and GTK/GDK drag adapters.
7. **Persistent app catalogue and icons:** move app metadata/usage to transactional SQLite, add platform source invalidation, then add the bounded disk icon cache.
8. **Render contract:** keep 12 rows, text first, visible icons second, continuation paging, and backend cancellation.
9. **Native drag:** security-test each opaque-ID native drag adapter independently of the header drag repair.
10. **Product cleanup:** remove the injected Search navigation item and Web Search fallback only when the distinct Home/Settings commands and shortcut labels are present on all supported platforms.
11. **Packaged verification:** run the 30-run protocol and full parity matrix on Windows, macOS, GNOME Wayland, KDE Wayland, and one supported Linux X11 session.

Platform provider steps 4–6 can be developed in isolated worktrees after the shared provider/result contract is fixed, with Windows first because it has the measured baseline. Steps 1 and 2 should remain one coordinated lifecycle workstream because both touch focus and dictation targeting. Platform work may be staged, but Mumble Find parity is not complete until all three packaged gates pass. Product cleanup must not ship before the replacement entry points exist.

## Risks and unresolved verification

| Risk | Mitigation or required proof |
| --- | --- |
| Windows Search is disabled, stale, or excludes a requested location | Detect service/provider availability; label degraded coverage; keep apps usable; provide a user-controlled scope/fallback rather than a silent crawl. |
| Spotlight is unavailable, still gathering, or excludes a requested location | Report scope and gathering/degraded state, keep apps usable, and test cancellation/live updates. Never present excluded locations as complete results. |
| LocalSearch and Baloo are both absent | Offer only an already-installed `plocate` provider or an explicitly approved scoped Mumble index; do not install or enable services automatically. |
| `plocate` data is stale or omits content metadata | Label it filename-only and show freshness/coverage where discoverable; use `--existing`, `--limit`, and NUL output; never claim content-search parity. |
| SQL-like query construction accepts unsafe or expensive patterns | Centralise escaping, bind where supported, cap words and candidates, and fuzz-test quotes, `%`, `_`, `[`, Unicode, and very long queries. Vicinae's pinned adapter is useful evidence for explicit escaping and candidate caps, not code to copy.[^vicinae-windows] |
| Watchers miss changes or overflow | Treat watcher data as hints. Record a dirty scope, debounce, and reconcile that scope; preserve the prior committed generation until success. |
| Focus restoration is constrained by Windows, AppKit, or a Wayland compositor | Use documented native activation paths and validate real packaged behaviour. Do not bypass platform focus protections; a platform that cannot pass the focus matrix remains blocked. |
| Dictation finalises while Find owns focus | Keep an immutable dictation target captured before opening Find; if invalid, hold the transcript for explicit user recovery rather than pasting into Find. |
| Disk icon cache grows or becomes stale | Bound bytes/count, key by source fingerprint/theme/scale, use atomic writes, and rebuild individual failures. |
| GPL or custom-licence code crosses into Mumble | Record comparator commits; reimplement concepts; require a separate legal/product decision before copying any Albert or Vicinae source. |
| macOS/Linux behaviour drifts from Windows | Share contracts and test vectors, not platform internals. Windows is only the first measured gate; require independent macOS, GNOME Wayland, KDE Wayland, and Linux X11 evidence. |

## Required handoff from this decision

Issue #7 can be considered architecturally answered when the owner accepts these boundaries:

- shortcut-only Mumble Find overlay, separate from Web Search and top navigation;
- resident hidden window with explicit show/hide/toggle and preserved focus;
- dictation-independent launcher visibility with a protected dictation target;
- small transactional app catalogue plus mandatory Windows Search, macOS Spotlight, and negotiated Linux indexed-provider adapters;
- bounded, cancellable candidate retrieval and first page of 12;
- visible text/placeholders before visible-only icon hydration;
- opaque-ID native drag seams for OLE, AppKit, and GTK/GDK; and
- independent packaged cold/warm and parity-matrix verification on Windows, macOS, and Linux before parity or release claims.

The next action should be to split implementation into the ordered tickets above, beginning with instrumentation/lifecycle and dictation-target preservation. Do not begin with styling: current evidence shows that startup, query complexity, and state ownership are the constraints users actually feel.

## Sources

[^interface-contract]: [Mumble centred interface and information-architecture contract](mumble-interface-contract-research.md).
[^current-engine]: Mumble [`engine.py`](../../Internal/app/experimental/system_search/engine.py), inspected at the research snapshot.
[^current-icons]: Mumble [`engine.py`](../../Internal/app/experimental/system_search/engine.py) and [`ui.js`](../../Internal/app/experimental/system_search/ui.js), inspected at the research snapshot.
[^current-ui]: Mumble [`ui.js`](../../Internal/app/experimental/system_search/ui.js), inspected at the research snapshot.
[^current-window]: Mumble [`webui_shell.py`](../../Internal/app/webui_shell.py), inspected at the research snapshot.
[^current-hotkey]: Mumble [`mumble.py`](../../Internal/app/mumble.py), `on_search_hotkey`, inspected at the research snapshot.
[^current-bindings]: Mumble [`bindings.py`](../../Internal/app/bindings.py) and [`mumble.py`](../../Internal/app/mumble.py), inspected at the research snapshot.
[^current-nav]: Mumble [`system-search-loader.js`](../../Internal/app/webui/system-search-loader.js), inspected at the research snapshot.
[^current-platform]: Mumble's [`Linux system_search` mirror](../../Internal/app/Ports/Linux/app/experimental/system_search/) exists at the research snapshot; the [`macOS app port`](../../Internal/app/Ports/macOS/app/) has no equivalent package or loader.
[^pywebview-drag]: pywebview, [drag region example](https://pywebview.flowrl.com/examples/drag_region) and [drag-area API notes](https://pywebview.flowrl.com/api/#drag-area).
[^windows-search]: Microsoft Learn, [Windows Search overview](https://learn.microsoft.com/en-us/windows/win32/search/-search-3x-wds-overview).
[^registerhotkey]: Microsoft Learn, [`RegisterHotKey`](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-registerhotkey).
[^windows-dnd]: Microsoft Learn, [Shell clipboard formats, including `CF_HDROP`](https://learn.microsoft.com/en-us/windows/win32/shell/clipboard#cf_hdrop) and [`DoDragDrop`](https://learn.microsoft.com/en-us/windows/win32/api/ole2/nf-ole2-dodragdrop).
[^apple-mdquery]: Apple Developer Documentation, [`MDQuery`](https://developer.apple.com/documentation/coreservices/file_metadata/mdquery) and [`NSMetadataQuery`](https://developer.apple.com/documentation/foundation/nsmetadataquery). `MDQuerySetMaxCount` supplies a native bound, while both APIs support asynchronous gathering and stopping/cancelling a query.
[^apple-metadata-guide]: Apple, [File Metadata Search Programming Guide](https://developer.apple.com/library/archive/documentation/Carbon/Conceptual/SpotlightQuery/) and [search-scope guidance](https://developer.apple.com/library/archive/documentation/Carbon/Conceptual/SpotlightQuery/Concepts/QueryingMetadata.html).
[^apple-focus]: Apple Developer Documentation, [`NSWorkspace.frontmostApplication`](https://developer.apple.com/documentation/appkit/nsworkspace/frontmostapplication) and [cooperative application activation](https://developer.apple.com/documentation/appkit/passing-control-from-one-app-to-another-with-cooperative-activation).
[^apple-dnd]: Apple Developer Documentation, [`NSDraggingSession`](https://developer.apple.com/documentation/appkit/nsdraggingsession), [`NSPasteboard`](https://developer.apple.com/documentation/appkit/nspasteboard), and its [file-URL pasteboard type](https://developer.apple.com/documentation/appkit/nspasteboard/pasteboardtype/fileurl).
[^linux-localsearch]: GNOME LocalSearch, [overview](https://gnome.pages.gitlab.gnome.org/localsearch/overview.html), [D-Bus SPARQL endpoint](https://gnome.pages.gitlab.gnome.org/localsearch/endpoint.html), and [bounded `search --limit/--offset` command contract](https://gnome.pages.gitlab.gnome.org/localsearch/commandline.html).
[^linux-baloo]: KDE API Reference, [Baloo file indexing and `Query`](https://api.kde.org/baloo.html).
[^linux-plocate]: plocate, [official project description](https://plocate.sesse.net/) and [`plocate(1)`](https://plocate.sesse.net/plocate.1.html), including `--existing`, `--limit`, and `--null`.
[^linux-updatedb]: plocate, [`updatedb(8)`](https://plocate.sesse.net/updatedb.8.html), including the default daily refresh, visibility controls, pruning, and user-scoped database example.
[^linux-shortcuts]: XDG Desktop Portal, [Global Shortcuts interface](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.GlobalShortcuts.html), including returned bindings and activation tokens.
[^linux-dnd]: GTK 4, [`GtkDragSource`](https://docs.gtk.org/gtk4/class.DragSource.html) and [drag-and-drop overview](https://docs.gtk.org/gtk4/drag-and-drop.html), including typed `GFile` content providers.
[^flow-toggle]: Flow Launcher [`MainViewModel.cs`](https://github.com/Flow-Launcher/Flow.Launcher/blob/07a958d19fa69a2e10a258b0cf455f0156ed5989/Flow.Launcher/ViewModel/MainViewModel.cs).
[^flow-results]: Flow Launcher [`ResultsViewModel.cs`](https://github.com/Flow-Launcher/Flow.Launcher/blob/07a958d19fa69a2e10a258b0cf455f0156ed5989/Flow.Launcher/ViewModel/ResultsViewModel.cs) and [`ResultListBox.xaml`](https://github.com/Flow-Launcher/Flow.Launcher/blob/07a958d19fa69a2e10a258b0cf455f0156ed5989/Flow.Launcher/ResultListBox.xaml).
[^flow-images]: Flow Launcher [`ImageLoader.cs`](https://github.com/Flow-Launcher/Flow.Launcher/blob/07a958d19fa69a2e10a258b0cf455f0156ed5989/Flow.Launcher.Infrastructure/Image/ImageLoader.cs).
[^flow-explorer]: Flow Launcher [Explorer plugin source tree](https://github.com/Flow-Launcher/Flow.Launcher/tree/07a958d19fa69a2e10a258b0cf455f0156ed5989/Plugins/Flow.Launcher.Plugin.Explorer).
[^powertoys-window]: PowerToys Run [`MainWindow.xaml.cs`](https://github.com/microsoft/PowerToys/blob/fc680d350f74f1f4eec8a64296420e614724e74d/src/modules/launcher/PowerLauncher/MainWindow.xaml.cs).
[^powertoys-results]: PowerToys Run [`MainViewModel.cs`](https://github.com/microsoft/PowerToys/blob/fc680d350f74f1f4eec8a64296420e614724e74d/src/modules/launcher/PowerLauncher/ViewModel/MainViewModel.cs), [`ResultViewModel.cs`](https://github.com/microsoft/PowerToys/blob/fc680d350f74f1f4eec8a64296420e614724e74d/src/modules/launcher/PowerLauncher/ViewModel/ResultViewModel.cs), and [`ResultList.xaml`](https://github.com/microsoft/PowerToys/blob/fc680d350f74f1f4eec8a64296420e614724e74d/src/modules/launcher/PowerLauncher/ResultList.xaml).
[^powertoys-apps]: PowerToys Run [`Win32ProgramRepository.cs`](https://github.com/microsoft/PowerToys/blob/fc680d350f74f1f4eec8a64296420e614724e74d/src/modules/launcher/Plugins/Microsoft.Plugin.Program/Storage/Win32ProgramRepository.cs).
[^powertoys-indexer]: PowerToys Run [`WindowsSearchAPI.cs`](https://github.com/microsoft/PowerToys/blob/fc680d350f74f1f4eec8a64296420e614724e74d/src/modules/launcher/Plugins/Microsoft.Plugin.Indexer/SearchHelper/WindowsSearchAPI.cs).
[^ueli-toggle]: ueli [`BrowserWindowToggler.ts`](https://github.com/oliverschwendener/ueli/blob/2216a755e3bf5b20bdd7547e79ddc20c4a6ec008/src/main/Core/SearchWindow/BrowserWindowToggler/BrowserWindowToggler.ts).
[^ueli-drag]: ueli [`SearchResultListItem.tsx`](https://github.com/oliverschwendener/ueli/blob/2216a755e3bf5b20bdd7547e79ddc20c4a6ec008/src/renderer/Core/Search/SearchResultListItem.tsx) and [`DragAndDropModule.ts`](https://github.com/oliverschwendener/ueli/blob/2216a755e3bf5b20bdd7547e79ddc20c4a6ec008/src/main/Core/DragAndDrop/DragAndDropModule.ts).
[^ueli-index]: ueli [`SearchIndex.ts`](https://github.com/oliverschwendener/ueli/blob/2216a755e3bf5b20bdd7547e79ddc20c4a6ec008/src/main/Core/SearchIndex/SearchIndex.ts).
[^albert-index]: Albert [`itemindex.cpp`](https://github.com/albertlauncher/albert/blob/79adc659fc0ca148e3288adcea86efb278057457/src/util/itemindex.cpp).
[^albert-licence]: Albert [`LICENSE.md`](https://github.com/albertlauncher/albert/blob/79adc659fc0ca148e3288adcea86efb278057457/LICENSE.md).
[^vicinae-windows]: Vicinae [`win-file-indexer.cpp`](https://github.com/vicinaehq/vicinae/blob/97afee7cf3c4e9068a958c2116fcf5aecc9ab499/src/server/src/services/files-service/windows/win-file-indexer.cpp).
[^vicinae-db]: Vicinae [`file-indexer-db.cpp`](https://github.com/vicinaehq/vicinae/blob/97afee7cf3c4e9068a958c2116fcf5aecc9ab499/src/file-indexer/src/file-indexer-db.cpp) and [`file-indexer-query-engine.cpp`](https://github.com/vicinaehq/vicinae/blob/97afee7cf3c4e9068a958c2116fcf5aecc9ab499/src/file-indexer/src/file-indexer-query-engine.cpp).
[^vicinae-cmake]: Vicinae [`src/file-indexer/CMakeLists.txt`](https://github.com/vicinaehq/vicinae/blob/97afee7cf3c4e9068a958c2116fcf5aecc9ab499/src/file-indexer/CMakeLists.txt).
