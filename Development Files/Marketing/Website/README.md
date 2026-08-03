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

1. Home (`/`) introduces the local-first offer, accepted product captures, the
   five canonical jobs, privacy boundaries, the current release position, and
   complete Write and Find journeys. Find keeps searchable Deck material,
   local Mumble Find, and deliberate Web Search visibly separate.
2. Product (`/product/`) explains Write's deliberate command, visible Island
   states, local transcription default, Stop-time intended-cursor return, Deck
   recovery and reuse, local app/file finding, and consent-led Web Search.
3. Use Cases (`/use-cases/`) turns Write and Find into complete task sequences:
   everyday notes, longer text, cross-application work, finding prior text,
   reopening useful Deck material, locating a local app or file, and choosing a
   web provider deliberately.
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
bound to the accepted Focus Stage evidence manifest. The Mumble Find and Web
Search captures are deterministic renders of the current accepted product
source with visibly labelled demonstration data. Regenerate those two captures
only with `npm.cmd run capture:find-evidence`, then update and verify their exact
source and output hashes with `npm.cmd run test:find`.

## Interaction and accessibility

- Core Home, complete Write and Find journeys, navigation, release facts, and
  download access remain available without JavaScript.
- Operating-system detection changes only the recommended action; complete
  Downloads access remains available.
- The mobile menu contains focus and makes background content inert while open.
- The shared shell preserves skip navigation, semantic landmarks, metadata,
  visible keyboard focus, reduced motion, forced colours, and narrow reflow.
- Product media is local. The site uses no external fonts, analytics, CDN
  assets, tracking, or browser framework hydration.
