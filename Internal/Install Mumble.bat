@echo off
title Install Mumble
echo.
echo    MUMBLE  -  private voice-to-text
echo    -------------------------------
echo.
echo    Before it starts:
echo      *  If you opened this from INSIDE a .zip: close it, right-click the
echo         zip, choose "Extract All", then run this from the new folder.
echo      *  Keep this folder somewhere permanent (like Documents). Don't
echo         move it after installing.
echo.
echo    Setting up now - a few minutes the first time, then a welcome window
echo    opens. Just let it finish.
echo.

rem -- Locate install.ps1 no matter how the zip was laid out / extracted.
rem    (Older zips shipped the bat one level above the app folder; be robust.)
set "PS1=%~dp0install.ps1"
if not exist "%PS1%" set "PS1=%~dp0Internal\app\install.ps1"
if not exist "%PS1%" set "PS1=%~dp0mumble\install.ps1"
if not exist "%PS1%" set "PS1=%~dp0app\install.ps1"
if not exist "%PS1%" (
    echo    ERROR: install.ps1 was not found next to this file.
    echo.
    echo    This usually means the zip wasn't fully extracted. Right-click the
    echo    zip, choose "Extract All", then run Install Mumble.bat from the
    echo    extracted folder ^(the one that contains install.ps1^).
    echo.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%"
if errorlevel 1 (
    echo.
    echo    Something went wrong during setup - see the messages above.
    echo    Nothing was broken; fix the issue ^(usually internet or Python^)
    echo    and run this installer again.
    echo.
    pause
    exit /b 1
)

echo.
echo    All done - Mumble is in your system tray. You can close this window.
pause
