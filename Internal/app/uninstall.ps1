# Mumble uninstaller — removes everything Mumble installed:
#   * stops this exact installed copy (branded Mumble.exe, pythonw, python)
#   * deletes the Desktop / Start-menu / Startup / Uninstall shortcuts
#   * clears the legacy HKCU..\Run startup entry
#   * optionally deletes your saved transcripts + settings (%APPDATA%\Mumble)
#   * optionally deletes the Mumble program folder itself
# Safe to re-run. Layout: this script lives in Internal\app; the launchers sit
# in Internal\; the program folder is the parent of Internal.
$ErrorActionPreference = 'SilentlyContinue'
$app      = Split-Path -Parent $MyInvocation.MyCommand.Path   # ...\Internal\app
$internal = Split-Path -Parent $app                            # ...\Internal
$dist     = Split-Path -Parent $internal                       # program folder

Write-Host ""
Write-Host "  Uninstalling Mumble" -ForegroundColor Cyan
Write-Host "  ===================" -ForegroundColor DarkGray
Write-Host ""

function Test-MumbleProcessForApp($Process, [string]$AppRoot) {
    if ($Process.Name -notin @('Mumble.exe', 'pythonw.exe', 'python.exe')) { return $false }
    $commandLine = [string]$Process.CommandLine
    if (-not $commandLine) { return $false }
    $resolvedRoot = [IO.Path]::GetFullPath($AppRoot).TrimEnd('\')
    $productRoot = Split-Path (Split-Path $resolvedRoot -Parent) -Parent
    $entries = @(
        (Join-Path $resolvedRoot 'mumble.py'),
        (Join-Path $resolvedRoot 'webui_shell.py'),
        (Join-Path $resolvedRoot '.venv\Scripts\Mumble.exe'),
        (Join-Path $productRoot 'Mumble.exe')
    )
    foreach ($entry in $entries) {
        $pattern = '(?i)(?:^|\s|")' + [regex]::Escape($entry) + '(?:"|\s|$)'
        if ($commandLine -match $pattern) { return $true }
    }
    return $false
}

# 1. Stop this exact installed copy (branded exe included) ----------------------
#    The canonical app path prevents one install from stopping a sibling copy.
Get-CimInstance Win32_Process |
    Where-Object { Test-MumbleProcessForApp $_ $app } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 400
Write-Host "  Stopped any running Mumble." -ForegroundColor DarkGray

# 2. Remove shortcuts + startup, via the app's own helper when the venv is intact
$vpy = Join-Path $app ".venv\Scripts\python.exe"
$removedViaApp = $false
if (Test-Path $vpy) {
    & $vpy -c "import autostart; autostart.disable(); autostart.remove_start_menu(); autostart.remove_desktop_shortcut(); autostart.remove_uninstall_start_menu()" 2>$null
    if ($LASTEXITCODE -eq 0) { $removedViaApp = $true }
}

# 2b. Fallback (and belt-and-braces): delete the .lnk files + Run key directly,
#     so uninstall works even if Python/the venv is already gone.
$ws = New-Object -ComObject WScript.Shell
$desktop = $ws.SpecialFolders.Item('Desktop')
$programs = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
$startup  = Join-Path $programs 'Startup'
@(
    (Join-Path $desktop  'Mumble.lnk'),
    (Join-Path $programs 'Mumble.lnk'),
    (Join-Path $programs 'Uninstall Mumble.lnk'),
    (Join-Path $startup  'Mumble.lnk')
) | ForEach-Object { if (Test-Path $_) { Remove-Item $_ -Force } }
Remove-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name 'Mumble' -ErrorAction SilentlyContinue
Write-Host "  Removed Desktop / Start-menu / Startup shortcuts." -ForegroundColor DarkGray

# 3. Personal data (transcripts, clipboard history, settings) -------------------
$data = Join-Path $env:APPDATA 'Mumble'
if (Test-Path $data) {
    $ans = Read-Host "  Also delete your saved transcripts and settings? (y/N)"
    if ($ans -match '^[Yy]') {
        Remove-Item $data -Recurse -Force
        Write-Host "  Deleted $data" -ForegroundColor DarkGray
    } else {
        Write-Host "  Kept your transcripts in $data" -ForegroundColor DarkGray
    }
}

# 4. The program folder itself --------------------------------------------------
#    Guards: never recursively delete a drive root (possible if a malformed
#    layout places this script at C:\Internal\app), and never auto-delete a
#    developer checkout.
$resolvedDist = [IO.Path]::GetFullPath($dist).TrimEnd('\')
$driveRoot = [IO.Path]::GetPathRoot($resolvedDist).TrimEnd('\')
if (-not $resolvedDist -or $resolvedDist -eq $driveRoot) {
    Write-Host ""
    Write-Host "  Refusing to delete an unsafe program-folder path: '$resolvedDist'." -ForegroundColor Yellow
} elseif (Test-Path (Join-Path $resolvedDist '.git')) {
    Write-Host ""
    Write-Host "  This is a development checkout (.git found) - leaving the folder in place." -ForegroundColor DarkGray
    Write-Host "  Delete '$resolvedDist' by hand if you really want it gone." -ForegroundColor DarkGray
} else {
    $ans2 = Read-Host "  Delete the Mumble program folder too? ('$resolvedDist') (y/N)"
    if ($ans2 -match '^[Yy]') {
        # We're running from inside $dist, so hand the delete to a detached
        # process that waits for this script (and its python child) to exit,
        # then removes the whole folder. Built by concatenation so the embedded
        # quotes and ampersand stay literal.
        Set-Location $env:TEMP
        $rm = '/c ping 127.0.0.1 -n 3 >nul & rmdir /s /q "' + $resolvedDist + '"'
        Start-Process cmd.exe -ArgumentList $rm -WindowStyle Hidden
        Write-Host "  The Mumble folder will be removed in a moment. Uninstall complete." -ForegroundColor Green
        return
    }
}

Write-Host ""
Write-Host "  Mumble has been uninstalled." -ForegroundColor Green
Write-Host ""
