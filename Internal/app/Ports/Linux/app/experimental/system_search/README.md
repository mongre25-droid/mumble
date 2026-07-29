# Mumble Find

Mumble Find is the private Windows/Linux application and file launcher.
It replaces the former autonomous Computer Control experiment.

On Linux the production engine queries the desktop-owned index: GNOME
LocalSearch/Tracker or KDE Baloo. If neither is available, plocate is an explicit
degraded filename-only fallback whose database may be stale. With no supported
index it says the route is unsupported; it never begins an unbounded home-folder
crawl. Queries, paths, and usage never leave the device.

The action boundary is intentionally narrow: JavaScript receives opaque ids and
can open, reveal, favourite, copy, or request drag only for results that the
Python index already returned. Query text is passed as bounded command arguments,
never executed as a shell command. Wayland compositors commonly restrict native
drag; in that case Mumble reports the limitation and retains Open and Show in
folder alternatives. The feature is omitted on macOS, where the product
continues to rely on Spotlight/Finder.

Product patterns were researched from the MIT-licensed Flow Launcher, Ueli, and
Microsoft PowerToys repositories. No upstream source, prompt, artwork, or UI was
copied into Mumble.
