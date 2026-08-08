# Mumble tooling

These helpers build, package, verify, and synchronize Mumble. They are development tools, not application runtime code.

- `_rebuild_zip.py` creates `Internal/Releases/Mumble.zip` from the current runtime.
- `verify_windows_launch.ps1` checks the Windows launcher contract.
- `sync_reader_speech_contracts.py` synchronizes the bounded Reader speech disclosure and source-documentation contract from Windows into the maintained macOS and Linux ports. It validates the complete approved source/target/marker set before mutation, rejects redirected or duplicate paths, stages replacement and restoration bytes, and rolls back an interrupted multi-file apply; use `--check` to detect drift, and repeated successful runs must be byte-idempotent.
- `sync_to_live.ps1` synchronizes a chosen live installation and rebuilds the release ZIP.
