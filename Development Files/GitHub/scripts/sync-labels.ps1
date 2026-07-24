[CmdletBinding()]
param(
    [string]$Repository = "mongre25-droid/mumble"
)

$ErrorActionPreference = "Stop"
$manifestPath = Join-Path $PSScriptRoot "..\labels.json"
$labels = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json

foreach ($label in $labels) {
    gh label create $label.name --repo $Repository --color $label.color --description $label.description --force
}

Write-Host "Synchronized $($labels.Count) labels in $Repository."
