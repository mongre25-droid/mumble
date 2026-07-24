@echo off
rem ============================================================
rem   MUMBLE  —  Launch
rem   Double-click to start Mumble. First run installs it.
rem   (Tip: the installer also puts a golden Mumble icon on
rem    your Desktop and Start menu — those launch it too.)
rem ============================================================
title Mumble
set "APP=%~dp0Internal\app"
if not exist "%APP%\mumble.py" set "APP=%~dp0"

set "PYW=%APP%\.venv\Scripts\Mumble.exe"
if not exist "%PYW%" set "PYW=%APP%\.venv\Scripts\pythonw.exe"
if exist "%PYW%" (
    start "" "%PYW%" "%APP%\mumble.py"
    exit /b 0
)

echo.
echo    Mumble isn't installed yet - running the one-time setup now.
echo    (A few minutes the first time. The welcome window opens after.)
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%APP%\install.ps1"
if errorlevel 1 (
    echo.
    echo    Setup didn't finish - see the messages above, then run this again.
    echo.
    pause
    exit /b 1
)
exit /b 0
