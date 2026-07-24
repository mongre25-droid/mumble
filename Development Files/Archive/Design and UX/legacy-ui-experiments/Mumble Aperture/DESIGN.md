# Design rationale — Mumble Aperture

## The proposition

Mumble is most distinctive at the moment voice becomes useful text. Aperture
puts that moment at the physical centre of the product. The interface is
designed like a quiet precision instrument rather than a conventional app
dashboard: one voice core, six connected workspaces, and a visible route from
local audio to active-app output.

## Spatial model

The large home aperture is both status and action. Home, Deck, Stats, Meetings,
Reader, and Settings are arranged symmetrically around it, connected by
hairlines. Entering a workspace contracts the voice core into a persistent
top-centre control; the selected surface then opens below it.

Each surface gets its own visual metaphor instead of reusing one grid of cards:

- **Home** is the instrument and its latest signal.
- **Deck** is a continuous transcript tape with an inspection surface.
- **Stats** is a chronometer with satellite readings and one shared baseline.
- **Meetings** uses a reel-to-reel session console and resolved outputs.
- **Reader** is an editorial listening column with a progress ruler.
- **Settings** is a numbered calibration board and processing path.

## Visual language

The palette begins with near-black, warm graphite, bone, and champagne gold.
Gold is structural—ticks, connectors, state, and precise actions—not a fill
colour for every surface. Panels are solid and separated with hairlines.
Corners are square or lightly cut; there is no blue theme, large-area glass,
aurora, or floating dock.

Typography combines technical uppercase labels with warm, readable body copy.
The prototype uses only local system fonts and inline SVG geometry, so its
appearance does not depend on a network connection.

## Motion system

Motion belongs to the instrument:

- The dial arrives by scaling from its centre while navigation connectors draw.
- The tick ring makes one slow revolution; a restrained glint crosses the rim.
- Navigation performs an iris transition as the dial contracts or expands.
- Listening accelerates the ring, activates irregular waveform motion, and
  emits two staggered echo rings.
- Stopping passes through a gold processing sweep before resolving to ready.
- Meetings rotate paired reels; charts rise from a shared baseline; Reader
  playback advances a single gold word marker.

All continuous motion is disabled by the operating system's reduced-motion
preference.

## Prototype boundary

The interaction layer exists to communicate the design, not to claim working
product integration. Buttons update mock state, navigate, animate, or show
feedback. Nothing reaches Mumble's production runtime or user data.
