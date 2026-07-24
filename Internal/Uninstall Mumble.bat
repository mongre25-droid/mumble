@echo off
title Uninstall Mumble
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0app\uninstall.ps1"
echo.
pause
