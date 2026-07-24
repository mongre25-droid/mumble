# Mumble Aperture

Mumble Aperture is a sealed, static UI/UX experiment for Mumble. It explores a
centered, black-and-gold interface built around a single concentric voice
instrument. It is intentionally a visual prototype: every transcript, meeting,
document, statistic, setting, and interaction is local mock data.

## Run

Double-click **Launch Mumble Aperture.bat**, or open **index.html** in a modern
browser. No install, build step, web server, account, or internet connection is
required.

## What is represented

- Home voice instrument with idle, listening, processing, and ready states
- Dictation modes, local-processing route, recent utterance, and daily signal
- Deck transcript tape with filters, selection, actions, and transformations
- Meetings library, summaries, transcripts, action items, and a live session
- Reader shelf, chapter navigation, word tracking, playback, and bookmarks
- Stats chronometer, daily rhythm, activity history, and usage measurements
- Settings calibration console for voice, output, intelligence, shortcuts,
  privacy, and system behaviour
- Keyboard command menu, toasts, responsive layouts, and reduced-motion support

## Isolation guarantee

This folder does not import the production Web UI, Python bridge, settings,
stores, local command port, application data, microphone, clipboard, AI
provider, or cloud-sync service. The prototype writes no persistent data and
resets on refresh.

See **DESIGN.md** for the spatial model and motion system.
