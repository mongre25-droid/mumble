# Mumble website

The static marketing site for Mumble, built with Astro 7 and vanilla JavaScript.

## Commands

```powershell
npm.cmd ci --ignore-scripts
npm.cmd run test:release
npm.cmd run test:find
npm.cmd run test:home
node scripts/check-release.mjs
npm.cmd run build
npm.cmd exec -- astro check
npm.cmd run audit:dependencies
```

The real-browser contract runs against the built static site. Install Playwright's
managed Chromium, or point the harness at an existing Chromium executable:

```powershell
$env:PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
npm.cmd run test:browser
```

Node 22.12 or later is required.

## Page structure

The current maintained site exposes seven complete outputs:

1. Home (`/`) opens with one voice-first line, the release-authority platform
   action, and one application-matched guided stage for Write, Capture, Shape,
   Listen, and Find. Direct job selection, Previous, Next, Play, and Pause remain
   manual after at most one ordinary-motion pass; reduced-motion and save-data
   visitors start manual, while JavaScript-off and narrow layouts keep all five
   jobs readable. The early privacy summary distinguishes local transcription,
   optional online routes, local Mumble Find, and consent-gated Web Search.
   Below the fold, the accepted complete per-job demonstrations, genuine and
   labelled evidence, relevant Product actions, supporting Settings and Stats,
   free/MIT/no-account/source truth, platform action, and Help route remain.
2. Product (`/product/`) explains Write's deliberate command, visible Island
   states, local transcription default, Stop-time intended-cursor return, Deck
   recovery and reuse, selected-microphone capture, supported audio imports,
   durable local records, explicit transcription routes, search, playback,
   export, optional transcript-only analysis, bounded Text, Prompt, Email,
   Reply, Foreign, preset, custom, provider and local-model Shape authority;
   supported Reader documents, library and collections, configured online
   playback, find, bookmarks, saved position, progress and optional summaries;
   local app/file finding; and consent-led Web Search. Future media inputs remain
   visibly unavailable.
3. Use Cases (`/use-cases/`) applies the shared job-story model to everyday
   notes, longer text, cross-application writing, meetings, lectures, supported
   existing audio, AI prompts, business email, contextual replies,
   language-assisted text, long-document listening, resumed reading, passage
   finding, retained progress, prior Deck material, local apps/files and
   deliberate Web Search without changing to profession-first navigation.
4. Privacy (`/privacy/`) exposes Local Transcription, Cloud Transcription, Text
   Shaping, Reader speech, Mumble Find, and Web Search as six separately
   controlled routes. Each keeps the same visual path and complete semantic-table
   alternative, while the static website states its zero-CDN, no-analytics, and
   no-tracking boundary.
5. Downloads (`/downloads/`) presents the hash-bound Windows candidate and
   visible gated states for macOS and Linux from one release authority.
6. Help (`/help/`) provides a searchable, task-led Getting Started path,
   accepted platform status, shortcut and insertion recovery, and complete
   static topic browsing.
7. The not-found recovery page (`/404.html`) returns visitors to Home,
   Downloads, Help, or public issue reporting.
Canonical job content is in `src/data/jobs.json`. Release channel, version,
publication state, platform facts, integrity, requirements, artifact location,
release notes, visitor resources, public source and issue-reporting destinations,
and recommendation labels are in `src/data/release.json`.
Privacy route identity, explanatory content, visual steps, control evidence, and
semantic facts are centralized in `src/data/privacy-routes.ts`; the browser
contract keeps an independent expected fact matrix.
`npm.cmd run build` fails closed when that authority drifts from source version or
the canonical and public Windows artifact bytes.

Each canonical job also owns its Home-montage copy and a reference to existing
canonical media. `src/data/home-montage.mjs` validates and resolves that
five-job projection; `HomeMontage.astro` only renders it. The structured Home
authority test rejects missing claims, unsupported or broken media references,
copied media paths or dimensions, and alternative text not owned by canonical
job data.
The installed-Chrome contract then compares the visitor-visible montage back to
that canonical data so component-local copies cannot drift silently.

The Find job's route cards, boundary rows, examples, actions, provider and
consent facts, failure wording, capture labels, dimensions, alt text, and source
provenance all come from `src/data/jobs.json`; `JobStory.astro` renders that
schema without a Find-specific content branch. The genuine Deck capture remains
bound to the accepted Focus Stage evidence manifest and has one pinned,
byte-identical PNG-to-WebP conversion through Sharp 0.35.3/libvips 8.18.3. The
accepted Mumble Find and Web Search PNGs are environment-bound browser renders
of current accepted product source with fixed, visibly labelled demonstration
data. `npm.cmd run capture:find-evidence` consumes the same canonical structured
fixtures and reports the exact runtime environment, but browser updates, the
Windows build, and fonts can change PNG bytes; browser byte-identical replay is
therefore not claimed. `npm.cmd run test:find` preserves each accepted public
hash and dimensions, verifies source and semantic authority, replays Deck bytes
exactly, and creates browser comparison renders without replacing accepted
evidence.

## Interaction and accessibility

- Core Home, complete Write, Capture, Shape, Listen, and Find journeys, navigation,
  Help, release facts, and download access remain available without JavaScript.
- The Home guide never requests microphone access or plays audio. Ordinary
  motion performs at most one guided pass and never loops; direct interaction,
  reduced motion, save-data, and completed playback settle into manual control.
- The five Home job choices form one automatic-selection tab interface with
  stable tab-to-panel relationships, roving keyboard focus, Left/Right/Home/End
  navigation, visible focus, and one correctly hidden panel state. Without
  JavaScript, all five labelled panels remain visible as ordinary content.
- The shared Write and Capture stories remain complete as text without
  JavaScript; their step controls add direct keyboard navigation when scripting
  is available.
- The complete Shape before/after, mode, privacy/provider/cost, and task-sequence
  content remains readable without JavaScript and stable under reduced motion.
- The Listen story remains complete as text without JavaScript; its direct-step
  controls add keyboard navigation when scripting
  is available.
- Help search filters the maintained static article set; semantic category,
  popular-task, and platform links remain the complete no-JavaScript path.
- Operating-system detection changes only the recommended action; complete
  Downloads access remains available.
- The mobile menu contains focus and makes background content inert while open.
- The Privacy explorer preserves all six local/online routes without JavaScript;
  JavaScript adds orientation-aware keyboard tabs without changing route truth.
- The shared shell preserves skip navigation, semantic landmarks, metadata,
  visible keyboard focus, reduced motion, forced colours, and narrow reflow.
- Product media is local. The site uses no external fonts, analytics, CDN
  assets, tracking, or browser framework hydration.
