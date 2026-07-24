@echo off
title Mumble - Enhanced mode demo (localhost)
REM Owner-review demo of the proposed ENHANCED visual tier (homepage et al.).
REM Serves the real web UI on localhost and opens it with the ?enhanced flag,
REM which loads enhanced.css on top of the shipped design. Nothing in the
REM installed app uses this tier until it is approved.
echo.
echo    Serving the Enhanced-mode demo at  http://localhost:49533/index.html?enhanced
echo    Close this window to stop the demo server.
echo.
start "" "http://localhost:49533/index.html?enhanced"
cd /d "%~dp0webui"
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" -m http.server 49533
) else (
    python -m http.server 49533
)
