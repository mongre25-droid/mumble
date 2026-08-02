# Mumble website

The static marketing site for Mumble, built with Astro 7 and vanilla JavaScript.

## Commands

```powershell
npm.cmd ci --ignore-scripts
npm.cmd run test:release
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

1. Home (`/`) introduces the local-first offer, accepted product evidence,
   the five canonical jobs, privacy boundaries, the current release position,
   the Write journey, and a genuine Reader-led Listen journey.
2. Product (`/product/`) explains Write's deliberate command, visible Island
   states, local transcription default, Stop-time intended-cursor return, and
   Deck recovery. It also explains supported Reader documents, library and
   collections, configured online playback, find, bookmarks, saved position,
   progress, and optional summaries with distinct route and cost boundaries.
3. Use Cases (`/use-cases/`) applies the shared task-first model to everyday
   writing, long-document listening, resumed reading, passage finding, and
   retained progress without changing to profession-first navigation.
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

## Interaction and accessibility

- Core Home, navigation, release facts, and download access remain available
  without JavaScript.
- The shared Write and Listen stories remain complete as text without
  JavaScript; their direct-step controls add keyboard navigation when scripting
  is available.
- Operating-system detection changes only the recommended action; complete
  Downloads access remains available.
- The mobile menu contains focus and makes background content inert while open.
- The shared shell preserves skip navigation, semantic landmarks, metadata,
  visible keyboard focus, reduced motion, forced colours, and narrow reflow.
- Product media is local. The site uses no external fonts, analytics, CDN
  assets, tracking, or browser framework hydration.
