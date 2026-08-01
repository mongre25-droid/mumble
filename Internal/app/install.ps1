# Mumble installer -- sets up Python, dependencies, the app icon, Start-menu and
# run-at-login shortcuts, pre-downloads the speech model, then launches Mumble.
# Safe to re-run. Works on a fresh PC (installs Python automatically if needed).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
Write-Host ""
Write-Host "  Installing Mumble" -ForegroundColor Cyan
Write-Host "  =================" -ForegroundColor DarkGray
Write-Host ""

function Test-MumbleProcessForApp($Process, [string]$AppRoot) {
    if ($Process.Name -notin @('python.exe', 'pythonw.exe', 'Mumble.exe')) { return $false }
    $commandLine = [string]$Process.CommandLine
    $executablePath = [string]$Process.ExecutablePath
    if (-not $commandLine -or -not $executablePath) { return $false }
    $resolvedRoot = [IO.Path]::GetFullPath($AppRoot).TrimEnd('\')
    $productRoot = Split-Path (Split-Path $resolvedRoot -Parent) -Parent
    $resolvedExe = [IO.Path]::GetFullPath($executablePath)

    function Test-ExactPath([string]$Actual, [string]$Expected) {
        return [string]::Equals(
            [IO.Path]::GetFullPath($Actual), [IO.Path]::GetFullPath($Expected),
            [StringComparison]::OrdinalIgnoreCase)
    }
    function Test-CommandToken([string]$Value, [string]$Token, [bool]$First) {
        $prefix = if ($First) { '^\s*' } else { '(?:^|\s)' }
        $pattern = '(?i)' + $prefix + '(?:"' + [regex]::Escape($Token) +
            '"|' + [regex]::Escape($Token) + ')(?=\s|$)'
        return $Value -match $pattern
    }
    function Test-CommandPair([string]$Value, [string]$Executable, [string]$Entry) {
        $pattern = '(?i)^\s*(?:"' + [regex]::Escape($Executable) +
            '"|' + [regex]::Escape($Executable) + ')\s+(?:"' +
            [regex]::Escape($Entry) + '"|' + [regex]::Escape($Entry) +
            ')(?=\s|$)'
        return $Value -match $pattern
    }

    $rootLauncher = Join-Path $productRoot 'Mumble.exe'
    if ($Process.Name -eq 'Mumble.exe' -and
            (Test-ExactPath $resolvedExe $rootLauncher)) {
        return Test-CommandToken $commandLine $rootLauncher $true
    }

    $allowedExecutables = @{
        'Mumble.exe'  = (Join-Path $resolvedRoot '.venv\Scripts\Mumble.exe')
        'pythonw.exe' = (Join-Path $resolvedRoot '.venv\Scripts\pythonw.exe')
        'python.exe'  = (Join-Path $resolvedRoot '.venv\Scripts\python.exe')
    }
    $expectedExe = $allowedExecutables[$Process.Name]
    if (-not $expectedExe -or -not (Test-ExactPath $resolvedExe $expectedExe) -or
            -not (Test-CommandToken $commandLine $expectedExe $true)) {
        return $false
    }
    foreach ($entry in @('mumble.py', 'webui_shell.py')) {
        if (Test-CommandPair $commandLine $expectedExe (Join-Path $resolvedRoot $entry)) {
            return $true
        }
    }
    return $false
}

# 0. Detect a previous install and stop any running copy, so files aren't locked
#    and the new version cleanly takes over. Settings + history are preserved
#    (they live in %APPDATA%\Mumble, separate from the app folder).
$startupLnk = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup\Mumble.lnk'
if ((Test-Path $startupLnk) -or (Test-Path (Join-Path $env:APPDATA 'Mumble'))) {
    Write-Host "  Found an existing Mumble - updating it (your settings are kept)." -ForegroundColor DarkGray
}
try {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { Test-MumbleProcessForApp $_ $root } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
} catch { }
Start-Sleep -Milliseconds 500

# Mumble v1.x registered the INSTALLER in the registry Run key, so every boot
# re-ran it from wherever the old download lived (a red PowerShell error at
# login once that folder moved). v2+ uses a Startup-folder shortcut instead —
# any Run value named Mumble is stale and gets removed here.
try {
    Remove-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name 'Mumble' -ErrorAction Stop
    Write-Host "  Removed a leftover startup entry from an old Mumble version." -ForegroundColor DarkGray
} catch { }

function Find-Python {
    function Test-PythonCandidate([string]$candidate) {
        if (-not (Test-Path -LiteralPath $candidate)) { return $false }
        try {
            $minor = & $candidate -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
            if ($LASTEXITCODE -ne 0) { return $false }
            $versionLine = $minor | Where-Object { $_ -match '^\d+\.\d+$' } |
                Select-Object -Last 1
            if (-not $versionLine) { return $false }
            $version = [Version]$versionLine
            return $version -ge [Version]'3.11' -and $version -lt [Version]'3.14'
        } catch {
            return $false
        }
    }
    $cands = @(
        "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe")
    foreach ($c in $cands) { if (Test-PythonCandidate $c) { return $c } }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -notlike "*WindowsApps*" -and
            (Test-PythonCandidate $cmd.Source)) {
        return $cmd.Source
    }
    return $null
}

# 1. Find or install Python 3.13 -------------------------------------------------
$py = Find-Python
if (-not $py) {
    Write-Host "  Python isn't installed. Trying winget..." -ForegroundColor Yellow
    try {
        winget install -e --id Python.Python.3.13 --scope user --silent `
            --accept-package-agreements --accept-source-agreements | Out-Null
    } catch { }
    $py = Find-Python
}
if (-not $py) {
    Write-Host "  Downloading Python 3.13 from python.org..." -ForegroundColor Yellow
    $ver = "3.13.12"
    $exe = Join-Path $env:TEMP "python-$ver-amd64.exe"
    try {
        Invoke-WebRequest -Uri "https://www.python.org/ftp/python/$ver/python-$ver-amd64.exe" `
            -OutFile $exe -UseBasicParsing
        # HTTPS alone is not publisher authentication if a proxy/root store is
        # compromised.  Refuse to execute an installer that is not signed by
        # the Python Software Foundation.
        $sig = Get-AuthenticodeSignature -LiteralPath $exe
        if ($sig.Status -ne 'Valid' -or
            $sig.SignerCertificate.Subject -notmatch 'Python Software Foundation') {
            Remove-Item -LiteralPath $exe -Force -ErrorAction SilentlyContinue
            throw "Downloaded Python installer has no valid PSF signature."
        }
        Start-Process $exe -ArgumentList "/quiet InstallAllUsers=0 PrependPath=1 Include_test=0" -Wait
    } catch {
        Write-Host "  Automatic Python install failed." -ForegroundColor Red
    }
    $py = Find-Python
}
if (-not $py) {
    Write-Host ""
    Write-Host "  Could not set up Python automatically." -ForegroundColor Red
    Write-Host "  Please install Python 3.13 from https://python.org (tick 'Add to PATH')" -ForegroundColor Red
    Write-Host "  and run this installer again." -ForegroundColor Red
    Write-Host ""
    Read-Host "  Press Enter to close"
    exit 1
}
Write-Host "  Using Python: $py" -ForegroundColor DarkGray

# 2. Private environment + dependencies -----------------------------------------
$venv = Join-Path $root ".venv"
$vpy = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $vpy)) {
    Write-Host "  Creating a private environment..." -ForegroundColor DarkGray
    & $py -m venv $venv
    if (-not (Test-Path $vpy)) {
        Write-Host "  Could not create the Python environment. Install aborted." -ForegroundColor Red
        Read-Host "  Press Enter to close"
        exit 1
    }
}
Write-Host "  Installing components (a couple of minutes the first time)..." -ForegroundColor DarkGray
& $vpy -m pip install --upgrade pip setuptools==83.0.0 --quiet --disable-pip-version-check
if ($LASTEXITCODE -ne 0) {
    Write-Host "  Secure installer-tool update FAILED. Mumble was not installed." -ForegroundColor Red
    Write-Host "  Check your internet connection and re-run Install Mumble.bat." -ForegroundColor Red
    Read-Host "  Press Enter to close"
    exit 1
}
# $ErrorActionPreference doesn't see native exit codes — without this check a
# failed dependency install still printed "All done!" and launched a dead app.
& $vpy -m pip install -r (Join-Path $root "requirements.txt") --quiet --disable-pip-version-check
if ($LASTEXITCODE -ne 0) {
    Write-Host "  Dependency install FAILED (see errors above). Mumble was not installed." -ForegroundColor Red
    Write-Host "  Check your internet connection and re-run Install Mumble.bat." -ForegroundColor Red
    Read-Host "  Press Enter to close"
    exit 1
}

# 3. App icon + branded executable ------------------------------------------------
& $vpy (Join-Path $root "assets_gen.py") | Out-Null
# Build a REAL Mumble.exe: a copy of the base interpreter (no launcher shim,
# so no second unbranded pythonw.exe child in Task Manager) with Mumble's
# name, version and gold icon written into its Windows resources.
& $vpy (Join-Path $root "brand_exe.py")
if ($LASTEXITCODE -ne 0) {
    # Resource patch failed — fall back to the plain venv copy so shortcuts
    # still say Mumble even if the file metadata stays Python's.
    try {
        Copy-Item (Join-Path $venv "Scripts\pythonw.exe") (Join-Path $venv "Scripts\Mumble.exe") -Force
    } catch { Write-Host "  (Could not create Mumble.exe - shortcuts will use pythonw)" -ForegroundColor DarkGray }
}

# 4. Pre-download the speech model (~145 MB, one time) --------------------------
Write-Host "  Downloading the speech model (~145 MB, one time)..." -ForegroundColor DarkGray
& $vpy -c "import os; os.environ['HF_HUB_DISABLE_XET']='1'; os.environ['HF_HUB_DISABLE_SYMLINKS_WARNING']='1'; from faster_whisper import WhisperModel; WhisperModel('base.en', device='cpu', compute_type='int8'); print('  model ready')"
if ($LASTEXITCODE -ne 0) {
    Write-Host "  (Model will download on first use instead.)" -ForegroundColor DarkGray
}

# 5. Shortcuts: Start menu + run at login ---------------------------------------
& $vpy -c "import autostart; results=(autostart.install_start_menu(), autostart.install_desktop_shortcut(), autostart.install_uninstall_start_menu(), autostart.enable()); raise SystemExit(0 if all(results) else 1)" | Out-Null
if ($LASTEXITCODE -eq 0) {
    Write-Host "  Added a Desktop icon + Start-menu entry, and set to start with Windows." -ForegroundColor DarkGray
} else {
    Write-Host "  Shortcuts could not all be created. You can still launch Mumble from this folder." -ForegroundColor Yellow
}

# 6. Launch ---------------------------------------------------------------------
$pyw = Join-Path $venv "Scripts\Mumble.exe"
if (-not (Test-Path $pyw)) { $pyw = Join-Path $venv "Scripts\pythonw.exe" }
Start-Process $pyw -ArgumentList ('"' + (Join-Path $root 'mumble.py') + '"') -WorkingDirectory $root
Write-Host ""
Write-Host "  All done! Mumble is running in your system tray (bottom-right)." -ForegroundColor Green
Write-Host "  Tap Ctrl + Win to dictate. It will start automatically from now on." -ForegroundColor Green
Write-Host ""
