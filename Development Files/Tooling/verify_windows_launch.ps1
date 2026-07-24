param(
    [string]$RepositoryRoot = "",
    [switch]$SkipFlowCache
)

$ErrorActionPreference = "Stop"

if (-not $RepositoryRoot) {
    $RepositoryRoot = [IO.Path]::GetFullPath(
        (Join-Path $PSScriptRoot "..\..")
    )
}
$RepositoryRoot = [IO.Path]::GetFullPath($RepositoryRoot).TrimEnd('\')

$failures = New-Object System.Collections.Generic.List[string]
$checks = New-Object System.Collections.Generic.List[string]
$shell = New-Object -ComObject WScript.Shell

function Add-ExistingPathCheck {
    param(
        [string]$Label,
        [string]$Path
    )

    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) {
        $failures.Add("$Label is missing: $Path")
        return
    }
    $checks.Add("$Label exists: $Path")
}

function Get-NormalizedPath {
    param([string]$Path)

    if (-not $Path) {
        return ""
    }
    return [IO.Path]::GetFullPath($Path).TrimEnd('\')
}

function Add-ExactPathCheck {
    param(
        [string]$Label,
        [string]$Actual,
        [string]$Expected
    )

    if (-not $Actual) {
        $failures.Add("$Label is blank; expected: $Expected")
        return
    }

    $actualPath = Get-NormalizedPath $Actual
    $expectedPath = Get-NormalizedPath $Expected
    if ($actualPath -ine $expectedPath) {
        $failures.Add("$Label points to the wrong location: $actualPath (expected: $expectedPath)")
        return
    }
    $checks.Add("$Label matches the canonical path: $expectedPath")
}

function Get-ArgumentPaths {
    param([string]$Arguments)

    if (-not $Arguments) {
        return @()
    }

    $matches = [regex]::Matches(
        $Arguments,
        '(?:"(?<quoted>[A-Za-z]:\\[^\"]+)"|(?<plain>[A-Za-z]:\\[^\s]+))'
    )
    foreach ($match in $matches) {
        if ($match.Groups['quoted'].Success) {
            $match.Groups['quoted'].Value
        } else {
            $match.Groups['plain'].Value
        }
    }
}

function Test-MumbleShortcut {
    param(
        [string]$Label,
        [string]$ShortcutPath,
        [bool]$Required,
        [string]$ExpectedTarget,
        [string]$ExpectedArgument,
        [string]$ExpectedWorkingDirectory,
        [string]$ExpectedIcon
    )

    if (-not (Test-Path -LiteralPath $ShortcutPath)) {
        if ($Required) {
            $failures.Add("$Label shortcut is missing: $ShortcutPath")
        }
        return
    }

    $shortcut = $shell.CreateShortcut($ShortcutPath)
    Add-ExistingPathCheck "$Label target" $shortcut.TargetPath
    Add-ExactPathCheck "$Label target" $shortcut.TargetPath $ExpectedTarget

    Add-ExistingPathCheck "$Label working directory" $shortcut.WorkingDirectory
    Add-ExactPathCheck "$Label working directory" $shortcut.WorkingDirectory $ExpectedWorkingDirectory

    $argumentPaths = @(Get-ArgumentPaths $shortcut.Arguments)
    if ($argumentPaths.Count -ne 1) {
        $failures.Add(
            "$Label arguments are not the one canonical app path: $($shortcut.Arguments)"
        )
    } else {
        Add-ExistingPathCheck "$Label argument path" $argumentPaths[0]
        Add-ExactPathCheck "$Label argument path" $argumentPaths[0] $ExpectedArgument
    }

    if (-not $shortcut.IconLocation) {
        $failures.Add("$Label icon resource is blank: $ShortcutPath")
    } else {
        $iconPath = ($shortcut.IconLocation -replace ',[-+]?\d+$', '').Trim('"')
        Add-ExistingPathCheck "$Label icon resource" $iconPath
        Add-ExactPathCheck "$Label icon resource" $iconPath $ExpectedIcon
    }

    $checks.Add(
        "$Label shortcut: $ShortcutPath -> $($shortcut.TargetPath) $($shortcut.Arguments)"
    )
}

function Get-PrintableAsciiStrings {
    param([string]$Path)

    $bytes = [IO.File]::ReadAllBytes($Path)
    $builder = New-Object Text.StringBuilder
    foreach ($byte in $bytes) {
        if ($byte -ge 32 -and $byte -le 126) {
            [void]$builder.Append([char]$byte)
            continue
        }

        if ($builder.Length -ge 4) {
            $builder.ToString()
        }
        [void]$builder.Clear()
    }
    if ($builder.Length -ge 4) {
        $builder.ToString()
    }
}

$manualLauncher = Join-Path $RepositoryRoot "Mumble.exe"
$runtimeExecutable = Join-Path $RepositoryRoot "Internal\app\.venv\Scripts\Mumble.exe"
$applicationEntry = Join-Path $RepositoryRoot "Internal\app\mumble.py"
$applicationDirectory = Join-Path $RepositoryRoot "Internal\app"
$mumbleIcon = Join-Path $RepositoryRoot "Internal\app\assets\mumble.ico"

Add-ExistingPathCheck "Manual launcher" $manualLauncher
Add-ExistingPathCheck "Runtime executable" $runtimeExecutable
Add-ExistingPathCheck "Application entry point" $applicationEntry
Add-ExistingPathCheck "Mumble icon" $mumbleIcon

if (Test-Path -LiteralPath $mumbleIcon) {
    $iconBytes = [IO.File]::ReadAllBytes($mumbleIcon)
    if ($iconBytes.Length -lt 6 -or
            [BitConverter]::ToUInt16($iconBytes, 0) -ne 0 -or
            [BitConverter]::ToUInt16($iconBytes, 2) -ne 1 -or
            [BitConverter]::ToUInt16($iconBytes, 4) -lt 1) {
        $failures.Add("Mumble icon is not a valid Windows icon resource: $mumbleIcon")
    } else {
        $checks.Add("Mumble icon is a valid Windows icon resource: $mumbleIcon")
    }
}

$startMenuShortcut = Join-Path ([Environment]::GetFolderPath('StartMenu')) "Programs\Mumble.lnk"
$uninstallStartMenuShortcut = Join-Path ([Environment]::GetFolderPath('StartMenu')) "Programs\Uninstall Mumble.lnk"
$startupShortcut = Join-Path ([Environment]::GetFolderPath('Startup')) "Mumble.lnk"
$desktopShortcut = Join-Path ([Environment]::GetFolderPath('Desktop')) "Mumble.lnk"

Test-MumbleShortcut "Start menu" $startMenuShortcut $true $runtimeExecutable $applicationEntry $applicationDirectory $mumbleIcon
Test-MumbleShortcut "Windows startup" $startupShortcut $true $runtimeExecutable $applicationEntry $applicationDirectory $mumbleIcon
Test-MumbleShortcut "Desktop" $desktopShortcut $false $runtimeExecutable $applicationEntry $applicationDirectory $mumbleIcon

$unexpectedStartupItems = Get-ChildItem -LiteralPath ([Environment]::GetFolderPath('Startup')) -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match '(?i)Mumble' -and $_.Name -ne 'Mumble.lnk' }
foreach ($item in $unexpectedStartupItems) {
    $failures.Add("Unexpected competing Mumble startup item: $($item.FullName)")
}

$startupApprovedKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder'
$startupApproval = Get-ItemPropertyValue -LiteralPath $startupApprovedKey -Name 'Mumble.lnk' -ErrorAction SilentlyContinue
if ($null -eq $startupApproval) {
    $checks.Add("Windows startup shortcut has no disabled StartupApproved record")
} elseif ($startupApproval.Length -lt 1 -or $startupApproval[0] -ne 2) {
    $failures.Add("Windows has disabled the Mumble startup shortcut in Startup Apps")
} else {
    $checks.Add("Windows Startup Apps marks Mumble.lnk as enabled")
}

$runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
if (Test-Path -LiteralPath $runKey) {
    $runProperties = Get-ItemProperty -LiteralPath $runKey
    foreach ($property in $runProperties.PSObject.Properties) {
        if ($property.Name -notmatch '^PS' -and
                ("$($property.Name) $($property.Value)" -match '(?i)Mumble')) {
            $failures.Add(
                "Unexpected competing Mumble Run-key entry: $runKey -> $($property.Name)=$($property.Value)"
            )
        }
    }
}

if (-not $SkipFlowCache) {
    $flowCache = Join-Path $env:APPDATA "FlowLauncher\Cache\Plugins\Flow.Launcher.Plugin.Program\Win32.cache"
    $flowSettings = Join-Path $env:APPDATA "FlowLauncher\Settings\Plugins\Flow.Launcher.Plugin.Program\Settings.json"
    Add-ExistingPathCheck "Flow Launcher program index" $flowCache
    Add-ExistingPathCheck "Flow Launcher Program settings" $flowSettings

    if (Test-Path -LiteralPath $flowSettings) {
        $programSettings = Get-Content -LiteralPath $flowSettings -Raw | ConvertFrom-Json
        if ($programSettings.EnableStartMenuSource -ne $true) {
            $failures.Add("Flow Launcher Start-menu indexing is disabled: $flowSettings")
        } else {
            $checks.Add("Flow Launcher Start-menu indexing is enabled")
        }
    }

    if (Test-Path -LiteralPath $flowCache) {
        $cachedMumblePaths = Get-PrintableAsciiStrings $flowCache |
            ForEach-Object { $_.Trim('"') } |
            Where-Object { $_ -match '(?i)^[A-Za-z]:\\.*Mumble' } |
            Sort-Object -Unique

        if (-not $cachedMumblePaths) {
            $failures.Add("Flow Launcher has no indexed Mumble path in: $flowCache")
        }

        $allowedFlowPaths = @(
            $runtimeExecutable,
            $applicationEntry,
            $startMenuShortcut,
            $uninstallStartMenuShortcut
        ) | ForEach-Object { Get-NormalizedPath $_ }
        $foundCanonicalStartMenu = $false

        foreach ($cachedPath in $cachedMumblePaths) {
            $candidate = Get-NormalizedPath ($cachedPath.Trim('"'))
            if (-not (Test-Path -LiteralPath $candidate)) {
                $failures.Add("Flow Launcher indexes a missing Mumble path: $candidate")
            } elseif ($allowedFlowPaths -inotcontains $candidate) {
                $failures.Add("Flow Launcher indexes a competing Mumble location: $candidate")
            } else {
                $checks.Add("Flow Launcher path is canonical: $candidate")
            }
            if ($candidate -ieq (Get-NormalizedPath $startMenuShortcut)) {
                $foundCanonicalStartMenu = $true
            }
        }

        if (-not $foundCanonicalStartMenu) {
            $failures.Add(
                "Flow Launcher does not index the canonical Mumble shortcut: $startMenuShortcut"
            )
        }
    }
}

$checks | ForEach-Object { Write-Host "PASS: $_" }
if ($failures.Count -gt 0) {
    $failures | ForEach-Object { Write-Error "FAIL: $_" -ErrorAction Continue }
    exit 1
}

Write-Host "PASS: Windows manual, startup, shortcut, icon, and Flow Launcher paths are valid."
exit 0
