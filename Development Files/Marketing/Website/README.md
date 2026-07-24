# Mumble website

The static marketing site for Mumble, built with Astro 7 and vanilla JavaScript.

## Commands

```powershell
npm install
npm.cmd run check
npm.cmd run build
npm.cmd run preview -- --host 127.0.0.1
```

`npm.cmd run build` first checks that `public/Mumble.zip` exactly matches the
release ZIP at the repository root, then generates the static site in `dist/`.
Node 22.12 or later is required.

## Page structure

The homepage is intentionally compact and product-led:

1. Direct Windows download and current product capture
2. Interactive dictation flow
3. Accessible workflow tabs for Dictation, Meetings, Smart Modes, and Reader
4. Island, Deck, and secondary feature inventory
5. Explicit Local, Cloud Transcription, and Pro Mode data paths
6. Release requirements, platform status, and FAQ

The source lives in `src/`. Product captures are optimized WebP files in
`public/product/`. The site uses no external fonts, analytics, CDN assets, or
framework runtime in the browser.

## Interaction and accessibility

- Core content remains available without JavaScript.
- Workflow tabs implement labelled tab panels, roving focus, and arrow keys.
- The mobile menu contains focus and makes background content inert while open.
- Motion respects `prefers-reduced-motion`.
- Skip navigation and visible keyboard focus are preserved.

Mumble is currently available for 64-bit Windows 10 and later. macOS and Linux
versions are in development.
