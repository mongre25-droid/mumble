# Mumble website

The static marketing site for Mumble, built with Astro 7 and vanilla JavaScript.

## Commands

```powershell
npm.cmd ci --ignore-scripts
npm.cmd run test:release
npm.cmd run test:find
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

The current maintained site exposes six complete outputs:

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
4. Privacy (`/privacy/`) explains Local Transcription and Mumble Find as
   separate local routes, keeps optional online routes distinct, and states the
   static website's zero-CDN, no-analytics, and no-tracking boundary.
5. Downloads (`/downloads/`) presents the hash-bound Windows candidate and
   visible gated states for macOS and Linux from one release authority.
6. The not-found recovery page (`/404.html`) returns visitors to Home or
   Downloads.

Canonical job content is in `src/data/jobs.json`. Release channel, version,
publication state, platform facts, integrity, requirements, artifact location,
release notes, and recommendation labels are in `src/data/release.json`.
`npm.cmd run build` fails closed when that authority drifts from source version or
the canonical and public Windows artifact bytes.

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
  release facts, and download access remain available without JavaScript.
- The Home guide never requests microphone access or plays audio. Ordinary
  motion performs at most one guided pass and never loops; direct interaction,
  reduced motion, save-data, and completed playback settle into manual control.
- The shared Write and Capture stories remain complete as text without
  JavaScript; their step controls add direct keyboard navigation when scripting
  is available.
- The complete Shape before/after, mode, privacy/provider/cost, and task-sequence
  content remains readable without JavaScript and stable under reduced motion.
- The Listen story remains complete as text without JavaScript; its direct-step
  controls add keyboard navigation when scripting
  is available.
- Operating-system detection changes only the recommended action; complete
  Downloads access remains available.
- The mobile menu contains focus and makes background content inert while open.
- The shared shell preserves skip navigation, semantic landmarks, metadata,
  visible keyboard focus, reduced motion, forced colours, and narrow reflow.
- Product media is local. The site uses no external fonts, analytics, CDN
  assets, tracking, or browser framework hydration.
