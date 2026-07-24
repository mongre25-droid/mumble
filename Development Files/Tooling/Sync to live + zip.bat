@echo off
REM One-command build sync (owner §26): push THIS source into the live, running
REM Mumble install IN PLACE (no re-extracting a new copy) AND refresh Mumble.zip,
REM so the dev folder, the live install, and the zip never drift apart.
REM Just double-click after making changes, then restart Mumble.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0sync_to_live.ps1"
pause
