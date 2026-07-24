# Mumble centred experience — throwaway prototype

> **Prototype only.** This development-only area is not loaded, packaged, or referenced by the production application.

## Question

Which centred visual system best unifies Home, Deck, Meetings, Mumble Find, Settings, and the Island controls while preserving the successful Stats and Reader structure?

## Open it

From this folder, run:

```powershell
.\open-prototype.cmd
```

The prototype is a self-contained local HTML file. It makes no network requests and changes no settings or saved data.

## Shared URLs

- `index.html?variant=A&surface=home&state=ready`
- `index.html?variant=B&surface=deck&state=ready`
- `index.html?variant=C&surface=find&state=loading`

Use the development controls above the page to change surface, state, and motion. Use the floating bottom switcher—or the left/right arrow keys when an input is not focused—to compare variants.

## Verify it

From this folder, run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\verify-prototype.ps1
```

Add `-Capture` to refresh the checked screenshots. The helper uses the local Playwright browser package already cached by `npx`; it does not add a production dependency.

See [`../../mumble-centred-experience-prototype-report.md`](../../mumble-centred-experience-prototype-report.md) for the objective comparison, selected synthesis, screenshot index, and production implementation contract.

## Boundaries

- All content is representative, inert prototype data.
- Mumble Find appears as a momentary overlay, never as a seventh navigation destination.
- Stats and Reader are preservation studies, not redesign proposals.
- The prototype explores appearance and hierarchy; it does not prove native hotkeys, focus restoration, dragging, recording, provider routing, or performance.
