[CmdletBinding()]
param(
    [string]$RepositoryRoot,
    [string]$Revision = 'HEAD'
)

$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($RepositoryRoot)) {
    $RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..\..')).Path
}


if ($env:OS -ne 'Windows_NT') {
    throw 'This regression must run on Windows.'
}

$RepositoryRoot = (Resolve-Path -LiteralPath $RepositoryRoot).Path
$resolvedRoot = [System.IO.Path]::GetFullPath(
    (& git -C $RepositoryRoot rev-parse --show-toplevel).Trim().Replace('/', '\')
)
if ($LASTEXITCODE -ne 0 -or $resolvedRoot -ne $RepositoryRoot) {
    throw "RepositoryRoot is not the exact Git checkout root: $RepositoryRoot"
}

$sourceRevision = (& git -C $RepositoryRoot rev-parse $Revision).Trim()
if ($LASTEXITCODE -ne 0 -or $sourceRevision -notmatch '^[0-9a-f]{40}$') {
    throw "Could not resolve revision '$Revision' in $RepositoryRoot."
}

$driveRoot = [System.IO.Path]::GetPathRoot($RepositoryRoot)
$temporaryRoot = Join-Path $driveRoot ('mumble-website-clean-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
$checkout = Join-Path $temporaryRoot 'repo'

function Invoke-Checked {
    param(
        [Parameter(Mandatory)] [scriptblock]$Command,
        [Parameter(Mandatory)] [string]$Description
    )

    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE."
    }
}

try {
    New-Item -ItemType Directory -Path $temporaryRoot | Out-Null

    Invoke-Checked { git clone --no-checkout --no-local --quiet $RepositoryRoot $checkout } 'Fresh clone'
    Invoke-Checked { git -C $checkout config core.longpaths true } 'Long-path configuration'
    Invoke-Checked { git -C $checkout config core.autocrlf true } 'Windows line-ending configuration'
    Invoke-Checked { git -C $checkout checkout --detach --quiet $sourceRevision } 'Fresh checkout'

    $checkedRevision = (& git -C $checkout rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw 'Could not resolve the checked revision.'
    }

    $before = @(& git -C $checkout status --porcelain=v1)
    if ($LASTEXITCODE -ne 0 -or $before.Count -ne 0) {
        throw "Fresh checkout was not clean before website verification:`n$($before -join "`n")"
    }

    $website = Join-Path $checkout 'Development Files\Marketing\Website'
    Push-Location $website
    try {
        Invoke-Checked { npm.cmd ci --ignore-scripts } 'Locked website dependency installation'
        Invoke-Checked { node scripts/check-release.mjs } 'Website release check'
        Invoke-Checked { npm.cmd run build } 'Website build'
        Invoke-Checked { npm.cmd exec -- astro check } 'Astro diagnostics'
    }
    finally {
        Pop-Location
    }

    $generatedDeclarations = @(
        'Development Files/Marketing/Website/.astro/content.d.ts',
        'Development Files/Marketing/Website/.astro/types.d.ts'
    )
    foreach ($declaration in $generatedDeclarations) {
        $declarationPath = Join-Path $checkout ($declaration -replace '/', '\')
        if (-not (Test-Path -LiteralPath $declarationPath -PathType Leaf)) {
            throw "Astro did not regenerate required declaration: $declaration"
        }

    }

    $after = @(& git -C $checkout status --porcelain=v1)
    if ($LASTEXITCODE -ne 0) {
        throw 'Could not inspect final checkout cleanliness.'
    }
    if ($after.Count -ne 0) {
        throw "Website verification changed the fresh checkout:`n$($after -join "`n")"
    }

    foreach ($declaration in $generatedDeclarations) {
        & git -C $checkout check-ignore --quiet -- $declaration
        if ($LASTEXITCODE -ne 0) {
            throw "Generated Astro declaration is not ignored: $declaration"
        }
    }

    Write-Output "Windows website cleanliness passed at $checkedRevision."
    Write-Output 'Release check, build, and Astro diagnostics left empty porcelain.'
    Write-Output 'Astro regenerated both required declarations under the ignored .astro directory.'
}
finally {
    if (Test-Path -LiteralPath $temporaryRoot) {
        $resolvedTemporaryRoot = (Resolve-Path -LiteralPath $temporaryRoot).Path
        if (
            $resolvedTemporaryRoot -ne $temporaryRoot -or
            [System.IO.Path]::GetPathRoot($resolvedTemporaryRoot) -ne $driveRoot -or
            (Split-Path -Leaf $resolvedTemporaryRoot) -notlike 'mumble-website-clean-*'
        ) {
            throw "Refusing to remove unexpected temporary path: $resolvedTemporaryRoot"
        }

        Get-ChildItem -LiteralPath $resolvedTemporaryRoot -Recurse -Force | ForEach-Object {
            if (-not $_.PSIsContainer -and $_.IsReadOnly) {
                $_.IsReadOnly = $false
            }
        }
        [System.IO.Directory]::Delete($resolvedTemporaryRoot, $true)
    }
}
