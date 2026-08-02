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

The current static shell exposes only complete destinations:

1. Home introduces the local-first offer, one accepted product capture, the five
   canonical jobs, privacy boundaries, and the current release position.
2. Privacy exposes six separately controlled routes: Local Transcription, Cloud
   Transcription, Text Shaping, Reader speech, Mumble Find, and Web Search. Each
   keeps the same visual path and complete semantic-table alternative.
3. Downloads presents the hash-bound Windows candidate and visible gated states
   for macOS and Linux from one release authority.
4. The not-found page returns visitors to Home or Downloads.

Canonical job content is in `src/data/jobs.json`. Release channel, version,
publication state, platform facts, integrity, requirements, artifact location,
release notes, and recommendation labels are in `src/data/release.json`.
`npm.cmd run build` fails closed when that authority drifts from source version or
the canonical and public Windows artifact bytes.

## Interaction and accessibility

- Core Home, navigation, release facts, and download access remain available
  without JavaScript.
- Operating-system detection changes only the recommended action; complete
  Downloads access remains available.
- The mobile menu contains focus and makes background content inert while open.
- The Privacy explorer preserves all six local/online routes without JavaScript;
  JavaScript adds orientation-aware keyboard tabs without changing route truth.
- The shared shell preserves skip navigation, semantic landmarks, metadata,
  visible keyboard focus, reduced motion, forced colours, and narrow reflow.
- Product media is local. The site uses no external fonts, analytics, CDN
  assets, tracking, or browser framework hydration.
