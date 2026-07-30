# Mumble Find

Mumble Find is the Windows local application, file, and folder launcher. Stage A
keeps one resident centred overlay and uses the registered Mumble Find shortcut
to show or hide it independently of dictation.

Installed applications come from Mumble's versioned catalogue. Interactive file
and folder discovery uses `win32com.client` from the pinned pywin32 312 runtime
to query Windows Search's SystemIndex through ADO. pywin32 is unmodified from
PyPI under its BSD-style licence; the retained licence and provenance boundary
are recorded in `requirements.txt` and `THIRD_PARTY_NOTICES.md`. Mumble does not
bundle or modify Windows Search.

The application process owns one reusable search service. Native ADO work stays
inside exactly two reusable child processes, never one process per query or
window. A deadline does not pretend to stop a native call that ignores
cancellation: its fixed slot remains owned, while newer queries return honest
app-only partial results. Final application shutdown terminates and joins the
fixed children. Search never falls back to recursive folder walking or a web
result. Each query owns a generation, deadline, result limit, and cancellation
signal so older work cannot replace newer results. Queries, paths, and usage
never leave the device.

The first page is limited to 12 rows. Text rows paint before visible application
icons are hydrated with a versioned cache and at most four concurrent workers.
If Windows Search is unavailable, Mumble Find keeps catalogue applications usable
and reports the missing indexed coverage honestly.

The action boundary is intentionally narrow: JavaScript receives opaque ids and
can open, reveal, favourite, or copy only items that the Python search owner
already knows. Query text is never executed as a shell command.

Native result dragging, packaged timing, physical Windows Search freshness, and
physical window/focus evidence remain Stage B work.

Product patterns were researched from the MIT-licensed Flow Launcher, Ueli, and
Microsoft PowerToys repositories. No upstream source, prompt, artwork, or UI was
copied into Mumble.
