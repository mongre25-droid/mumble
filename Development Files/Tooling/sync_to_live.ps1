# sync_to_live.ps1 - keep the dev folder, the LIVE install, and the zip in sync.
#
# Owner section 26 (2026-06-13): a new build must NOT require download-zip /
# extract / launch-another-copy. This pushes the current source straight into
# the running install IN PLACE, and refreshes Mumble.zip in one step. After it
# runs, just restart Mumble (the launcher self-heals any stale instance).
# (ASCII-only on purpose: Windows PowerShell 5.1 mis-reads non-BOM UTF-8.)
param([string]$Live = "")
$src = $PSScriptRoot

# Build the list of install dirs to update. An "install dir" is whatever folder
# holds mumble.py (layouts differ: some keep the app in ...\Internal, some in
# ...\Internal\app). 2026-06-20: the prior version only knew about Terraria/
# Documentos and updated the FIRST match, so it silently missed the copy the
# owner actually ran (Desktop\Mumble V0.9\Internal) -> "you updated something
# random, not the running one". Now we (a) follow the RUNNING process to its
# real folder, (b) probe every known location, and (c) update ALL of them.
$dsts = New-Object System.Collections.Generic.List[string]
function Add-Dst([string]$p) {
    if ($p -and (Test-Path (Join-Path $p "mumble.py"))) {
        $full = (Resolve-Path $p).Path
        if (-not ($dsts -contains $full)) { $dsts.Add($full) }
    }
}

if ($Live) { Add-Dst $Live }

# (a) The folder the running Mumble is actually executing from — the one that
#     MUST be updated. Read the live process command line and take its dir.
try {
    Get-CimInstance Win32_Process -Filter "Name='Mumble.exe' OR Name='pythonw.exe' OR Name='python.exe'" |
        Where-Object { $_.CommandLine -match 'mumble\.py' } |
        ForEach-Object {
            if ($_.CommandLine -match '"?([A-Za-z]:\\[^"]*?)mumble\.py') { Add-Dst $matches[1] }
        }
} catch {}

# (b) Every known install location, both layouts. (The project's own
#     "Mumble V0.9\Internal" is the source the Mumble V0.9.zip is built from, so
#     keep it in sync too — its zip is rebuilt separately, see HANDBOOK deploy.)
$cands = @(
    "$env:USERPROFILE\OneDrive\Desktop\The Official Mumble\Mumble V0.9\Internal",
    "$env:USERPROFILE\OneDrive\Desktop\Mumble V0.9\Internal",
    "$env:USERPROFILE\OneDrive\Terraria - Paradis\Mumble\Internal\app",
    "$env:USERPROFILE\OneDrive\Documentos\Mumble\Internal\app",
    "$env:USERPROFILE\OneDrive\Documentos\mumble version 0.9\Internal",
    "$env:USERPROFILE\OneDrive\Documents\Mumble\Internal\app",
    "$env:USERPROFILE\OneDrive\Pictures\Mumble\Internal\app",
    "$env:USERPROFILE\Downloads\Mumble\Internal\app",
    "$env:USERPROFILE\Downloads\mumble version 0.9"
)
foreach ($c in $cands) { Add-Dst $c }

if ($dsts.Count -eq 0) {
    Write-Host "No live install found. Re-run as:  .\sync_to_live.ps1 -Live <path to the folder containing mumble.py>" -ForegroundColor Yellow
    exit 1
}

Write-Host "Syncing source to $($dsts.Count) install(s):"
foreach ($dst in $dsts) {
    Write-Host "  -> $dst"
    robocopy $src $dst *.py *.vbs *.bat /NJH /NJS /NDL /NP | Out-Null
    robocopy "$src\webui" "$dst\webui" /E /NJH /NJS /NDL /NP | Out-Null
    robocopy "$src\assets" "$dst\assets" /E /NJH /NJS /NDL /NP | Out-Null
    $pc = Join-Path $dst "__pycache__"
    if (Test-Path $pc) { Remove-Item $pc -Recurse -Force -ErrorAction SilentlyContinue }
}
Write-Host "  all live installs updated in place." -ForegroundColor Green

$py = Join-Path $src ".venv\Scripts\python.exe"
& $py (Join-Path $src "_rebuild_zip.py")

# _rebuild_zip.py writes the repository release directly to Internal\Releases.
$releaseZip = Join-Path (Split-Path $src -Parent) "Releases\Mumble.zip"
if (Test-Path $releaseZip) {
    Write-Host "  Internal\Releases\Mumble.zip rebuilt from the live runtime." -ForegroundColor Green
}

Write-Host ""
Write-Host "Done. Restart Mumble to pick up the changes." -ForegroundColor Green
Write-Host "The launcher clears any stale instance first, so you never get a half-running copy." -ForegroundColor Green
