# Mumble tooling

These helpers build, package, verify, and synchronize Mumble. They are development tools, not application runtime code.

- `_rebuild_zip.py` creates `Internal/Releases/Mumble.zip` from the current runtime.
- `verify_windows_launch.ps1` checks the Windows launcher contract.
- `sync_to_live.ps1` synchronizes a chosen live installation and rebuilds the release ZIP.
