# Mumble Find

Mumble Find is the experimental Windows/Linux application and file launcher.
It replaces the former autonomous Computer Control experiment.

The engine uses only Python's standard library plus Mumble's existing clipboard
dependency. It indexes installed-app locations and bounded user folders in the
background, ranks name/path matches with local fuzzy scoring, and remembers only
favourites and launch frequency. Queries, paths, and usage never leave the
device.

The action boundary is intentionally narrow: JavaScript receives opaque ids and
can open, reveal, favourite, or copy only items that the Python index already
knows. Query text is never executed as a shell command. The feature is omitted
on macOS, where the product continues to rely on Spotlight/Finder.

Product patterns were researched from the MIT-licensed Flow Launcher, Ueli, and
Microsoft PowerToys repositories. No upstream source, prompt, artwork, or UI was
copied into Mumble.
