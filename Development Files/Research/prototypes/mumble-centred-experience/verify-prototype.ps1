[CmdletBinding()]
param([switch]$Capture)

$ErrorActionPreference = 'Stop'
$prototypeRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$captureScript = Join-Path $prototypeRoot 'capture-and-verify.cjs'
$playwrightRoot = Get-ChildItem -LiteralPath (Join-Path $env:LOCALAPPDATA 'npm-cache\_npx') -Directory |
    Sort-Object LastWriteTime -Descending |
    ForEach-Object { Join-Path $_.FullName 'node_modules' } |
    Where-Object { Test-Path -LiteralPath (Join-Path $_ 'playwright') } |
    Select-Object -First 1

if (-not $playwrightRoot) {
    throw 'Playwright was not found in the local npx cache. Run: npx.cmd playwright --version'
}

$previousNodePath = $env:NODE_PATH
$env:NODE_PATH = $playwrightRoot
try {
    $arguments = @($captureScript)
    if ($Capture) { $arguments += '--capture' }
    & node @arguments
    if ($LASTEXITCODE -ne 0) { throw "Prototype verification failed with exit code $LASTEXITCODE." }
}
finally {
    $env:NODE_PATH = $previousNodePath
}
