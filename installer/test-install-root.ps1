# No registry or application installation changes. Filesystem ownership regression.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')

$testParent = Join-Path $PSScriptRoot 'out'
New-Item -ItemType Directory -Path $testParent -Force | Out-Null
$root = Join-Path $testParent ('regression-' + [guid]::NewGuid().ToString('N'))
$outside = $null
$external = $null

function Assert-State {
    param([string]$Expected)
    $actual = Get-RightConvertInstallState -Root $root
    if ($actual -ne $Expected) {
        throw "Expected install state '$Expected', got '$actual'"
    }
}

function Assert-Rejected {
    $rejected = $false
    try {
        $null = Get-RightConvertInstallState -Root $root
    } catch {
        $rejected = $true
    }
    if (-not $rejected) { throw 'Unexpected root was accepted.' }
}

try {
    Assert-State 'absent'
    New-Item -ItemType Directory -Path $root | Out-Null
    Assert-State 'data-only'

    [IO.File]::WriteAllText((Join-Path $root 'settings.json'), '{"same_folder":true}')
    New-Item -ItemType Directory -Path (Join-Path $root 'logs') | Out-Null
    [IO.File]::WriteAllText((Join-Path $root 'logs\technical.log'), 'log data')
    Assert-State 'data-only'
    # Repeat simulates a second uninstall followed by reinstall preflight.
    Assert-State 'data-only'

    # A PowerShell setup timeout may leave an exclusively-locked stage; the
    # next installer must refuse it while active, then recover after it exits.
    $stage = Join-Path $root ('.stage-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $stage | Out-Null
    $stageOwner = Join-Path $stage $script:RightConvertStageMarker
    [IO.File]::WriteAllText($stageOwner, $script:RightConvertInstallerId)
    $stageContent = Join-Path $stage 'incomplete.tmp'
    [IO.File]::WriteAllText($stageContent, 'half copied payload')
    Assert-State 'data-only'
    $recoverable = @(Get-RightConvertRecoverableStages -Root $root)
    if ($recoverable.Count -ne 1 -or $recoverable[0] -ne $stage) {
        throw 'Owned stale stage was not recovered by preflight.'
    }
    $lease = [IO.File]::Open($stageOwner, [IO.FileMode]::Open,
                            [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    try { Assert-Rejected } finally { $lease.Dispose() }
    Remove-RightConvertOwnedTree -Root $root -Path $stage
    Assert-State 'data-only'

    $foreignStage = Join-Path $root ('.stage-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $foreignStage | Out-Null
    [IO.File]::WriteAllText((Join-Path $foreignStage $script:RightConvertStageMarker), 'foreign')
    Assert-Rejected
    Remove-RightConvertOwnedTree -Root $root -Path $foreignStage
    Assert-State 'data-only'

    [IO.File]::WriteAllText((Join-Path $root 'other-app.txt'), 'unowned content')
    Assert-Rejected
    Remove-Item -LiteralPath (Join-Path $root 'other-app.txt')
    New-Item -ItemType Directory -Path (Join-Path $root 'app') | Out-Null
    Assert-Rejected
    Remove-Item -LiteralPath (Join-Path $root 'app')

    [IO.File]::WriteAllText((Join-Path $root 'install-marker.txt'), 'not our installer')
    Assert-Rejected
    [IO.File]::WriteAllText((Join-Path $root 'install-marker.txt'), $script:RightConvertInstallerId)
    Assert-State 'installed'
    Remove-Item -LiteralPath (Join-Path $root 'install-marker.txt')

    Remove-Item -LiteralPath (Join-Path $root 'logs') -Recurse
    $external = Join-Path $testParent ('regression-target-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $external | Out-Null
    try {
        # Windows directory junctions work without a Developer Mode switch.
        New-Item -ItemType Junction -Path (Join-Path $root 'logs') -Target $external | Out-Null
        Assert-Rejected
    } finally {
        $linked = Join-Path $root 'logs'
        if (Test-Path -LiteralPath $linked) { [IO.Directory]::Delete($linked) }
        if (Test-Path -LiteralPath $external) { Remove-Item -LiteralPath $external }
    }
    # A nested junction under app must be rejected before recursive deletion.
    $outside = Join-Path $testParent ('keep-outside-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $outside | Out-Null
    [IO.File]::WriteAllText((Join-Path $outside 'important.txt'), 'keep')
    $ownedApp = Join-Path $root 'app'
    New-Item -ItemType Directory -Path (Join-Path $ownedApp 'internal') -Force | Out-Null
    try {
        New-Item -ItemType Junction -Path (Join-Path $ownedApp 'internal\escape') -Target $outside | Out-Null
        $rejectedNested = $false
        try {
            Remove-RightConvertOwnedTree -Root $root -Path $ownedApp
        } catch {
            $rejectedNested = $true
        }
        if (-not $rejectedNested) { throw 'Nested junction was not rejected.' }
        if (-not (Test-Path -LiteralPath (Join-Path $outside 'important.txt'))) {
            throw 'Recursive deletion followed a nested junction outside the application.'
        }
    } finally {
        $escape = Join-Path $ownedApp 'internal\escape'
        if (Test-Path -LiteralPath $escape) { [IO.Directory]::Delete($escape) }
        Remove-RightConvertOwnedTree -Root $root -Path $ownedApp
        Remove-Item -LiteralPath $outside -Recurse -Force
    }
    Write-Host 'PASS: preflight stage recovery/active lock, portable data, foreign and nested reparse refusal.'
} finally {
    # Unlink any surviving junction WITHOUT traversing it, even when an
    # assertion fails. Refuse recursive cleanup if any other links remain.
    foreach ($link in @((Join-Path $root 'app\internal\escape'), (Join-Path $root 'logs'))) {
        if (Test-Path -LiteralPath $link) {
            $attributes = [IO.File]::GetAttributes($link)
            if (($attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                [IO.Directory]::Delete($link)
            }
        }
    }
    if (Test-Path -LiteralPath $root) {
        Assert-TreeHasNoReparsePoints -Path $root
        Remove-Item -LiteralPath $root -Recurse -Force
    }
    foreach ($sandbox in @($outside, $external)) {
        if ($null -ne $sandbox -and (Test-Path -LiteralPath $sandbox)) {
            Assert-TreeHasNoReparsePoints -Path $sandbox
            Remove-Item -LiteralPath $sandbox -Recurse -Force
        }
    }
}
