# Mumble Horizon

Mumble Horizon is a sealed, static UI/UX experiment for Mumble. It is a clean-sheet
prototype: no production modules, stores, settings, services, or API bridge are imported.
All content is local mock data and all interactions reset when the page reloads.

## Run

Double-click `Launch Mumble Horizon.bat`, or open `index.html` in a modern browser.

See `DESIGN.md` for the product model, information architecture, island redesign, and the
reasoning behind both visual directions.

## Product surface represented

- Global dictation, processing states, Prompt toggle, microphone selection, and the island
- Deck/library: transcripts, clipboard captures, prompts, favourites, search, filtering,
  multi-select, Smart Modes, workflows/presets, copy/paste, and capture actions
- Meetings: recording controls, processing modes, speaker-labelled transcript, summaries,
  decisions, questions, action items, audio import, and export formats
- Reader: document library, supported-file import, collections, bookmarks, resume position,
  TTS voice/speed controls, sleep timer, summaries, and reading progress
- Insights: words, transcripts, pace, streak, time saved, activity, mode mix, and Reader stats
- Settings: shortcuts, search, language, vocabulary, local/cloud transcription, hardware/model,
  AI providers, prompt preferences, microphone, history limits, sync, updates, startup,
  resource saver, privacy, visual effects, and data controls
- Blue `Horizon` and gold/black `Noir` visual themes

## Isolation guarantee

This folder is independently launchable and has no reference to `Internal/app`, the current
`webui`, Python APIs, `%APPDATA%/Mumble`, or Mumble's command ports. It is intentionally not
linked from the production application or its settings.
