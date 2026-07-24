# Design rationale — Mumble Horizon

## Product model used

The experiment treats Mumble as a private voice operating layer, not a transcription
utility. Its core loop is still global dictation and paste, but the broader product is a
workspace for reusing speech, captured context, meetings, documents, and AI-shaped output.

The prototype was informed by the Core master documentation, current product guide,
current WebUI screen inventory, Python bridge surface, meeting/Reader stores, presets,
settings, statistics, processing modes, and privacy/fallback behaviour.

## Clean-sheet information architecture

- **Today** is an orientation surface, not a feature catalogue. It answers: am I ready,
  where is processing happening, and what can I resume?
- **Library** replaces the mental split between “history” and “Deck”. It is one searchable
  material space. **Smart Canvas** makes transformation a visible second step rather than
  mixing controls into every row.
- **Meetings** are a first-class workspace with a dedicated live-recording state and a
  post-meeting intelligence layout.
- **Reader** uses a two-pane library/book model and keeps playback physically attached to
  the text being read.
- **Insights** separates evidence from controls and calls time saved an estimate.
- **Settings** uses seven human-readable domains instead of a single long technical form.

## The new island

The island becomes an **ambient voice dock**. It is always spatially stable, exposes the
privacy-critical current state, keeps Prompt as the only immediate shaping toggle, and
expands into a waveform while recording. Its overflow panel reveals microphone, route,
output mode, and quick modes without making the resting control noisy.

## Visual directions

### Horizon

A luminous porcelain-and-blue environment: cool depth, generous radii, restrained glass,
and saturated blue reserved for voice/action moments. The intent is calm confidence rather
than “AI magic”.

### Noir

The same hierarchy expressed in near-black, warm graphite, and quiet gold. It is not based
on the production Golden Black UI; it shares Horizon's new spatial model, component shapes,
typography, and interaction patterns.

## Prototype boundary

Every screen uses illustrative mock state. Actions deliberately stop at visual feedback.
No Python bridge, command socket, application-data folder, API provider, microphone,
clipboard monitor, account, or production setting is contacted.

