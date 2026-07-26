[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$browserScript = Join-Path $scriptRoot 'verify-stage-a-ui.cjs'
$playwrightRoot = Get-ChildItem -LiteralPath (Join-Path $env:LOCALAPPDATA 'npm-cache\_npx') -Directory |
    Sort-Object LastWriteTime -Descending |
    ForEach-Object { Join-Path $_.FullName 'node_modules' } |
    Where-Object { Test-Path -LiteralPath (Join-Path $_ 'playwright') } |
    Select-Object -First 1

if (-not $playwrightRoot) {
    throw 'Playwright is not in the local npx cache.'
}

$previousNodePath = $env:NODE_PATH
$env:NODE_PATH = $playwrightRoot
try {
    & node $browserScript
    if ($LASTEXITCODE -ne 0) {
        throw "Mumble Find UI verification failed with exit code $LASTEXITCODE."
    }
}
finally {
    $env:NODE_PATH = $previousNodePath
}
