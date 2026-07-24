@echo off
REM Run Mumble with a visible console -- handy for first run or troubleshooting.
title Mumble
"%~dp0app\.venv\Scripts\python.exe" "%~dp0app\mumble.py"
echo.
echo Mumble exited. Press any key to close.
pause >nul
