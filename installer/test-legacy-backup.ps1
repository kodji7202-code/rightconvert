# Isolated legacy-backup safety regression. No real install, RunOnce, or Explorer
# changes. Only installer/out/legacy-regression-GUID disposable fixtures.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$auditScript = Join-Path $PSScriptRoot 'inspect-legacy-backup.ps1'
$base = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'out')).TrimEnd('\')
$id = [guid]::NewGuid().ToString('N')
$root = Join-Path $base ('legacy-regression-' + $id)
$outside = Join-Path $base ('legacy-outside-' + $id)
$classic = Join-Path (Split-Path -Parent $PSScriptRoot) 'shell\classic\out\x64\Release\RightConvertClassic.dll'
$explorer = Join-Path (Split-Path -Parent $PSScriptRoot) 'shell\windows11\out\x64\Release\RightConvertExplorer.dll'
if (-not (Test-Path -LiteralPath $classic -PathType Leaf) -or
    -not (Test-Path -LiteralPath $explorer -PathType Leaf)) {
    throw 'Compile classic + Windows11 shell DLLs before running legacy-backup tests.'
}

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class RightConvertLegacyLockTest {
    [DllImport("kernel32.dll", EntryPoint="LoadLibraryW", CharSet=CharSet.Unicode, SetLastError=true)]
    public static extern IntPtr LoadLibrary(string dll);
    [DllImport("kernel32.dll", EntryPoint="FreeLibrary", SetLastError=true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool FreeLibrary(IntPtr module);
}
'@

function New-Candidate {
    param([string]$Folder = ('.backup-' + [guid]::NewGuid().ToString('N')))
    $target = Join-Path $root $Folder
    New-Item -ItemType Directory -Path $target | Out-Null
    Copy-Item -LiteralPath $classic -Destination (Join-Path $target 'RightConvertClassic.dll')
    Copy-Item -LiteralPath $explorer -Destination (Join-Path $target 'RightConvertExplorer.dll')
    return $target
}

function Invoke-Audit {
    param([string]$Candidate)
    $results = @(& $auditScript -TestFixtureRoot $root -BackupPath $Candidate)
    if ($results.Count -ne 1) { throw "Expected one audit result, got $($results.Count)" }
    return $results[0]
}

function Assert-Refused {
    param([string]$Candidate)
    $result = Invoke-Audit -Candidate $Candidate
    if ($result.Status -ne 'Refused') {
        throw "Expected refusal for $Candidate, got $($result.Status): $($result.Detail)"
    }
    $threw = $false
    try {
        & $auditScript -TestFixtureRoot $root -BackupPath $Candidate -Cleanup | Out-Null
    } catch { $threw = $true }
    if (-not $threw) { throw "Unsafe candidate was not blocked from cleanup: $Candidate" }
}

$module = [IntPtr]::Zero
try {
    New-Item -ItemType Directory -Path $base -Force | Out-Null
    New-Item -ItemType Directory -Path $root | Out-Null
    New-Item -ItemType Directory -Path $outside | Out-Null
    [IO.File]::WriteAllText((Join-Path $root 'install-marker.txt'), 'RightConvert.PSInstaller.v1')
    [IO.File]::WriteAllText((Join-Path $root 'settings.json'), '{"keep":true}')
    New-Item -ItemType Directory -Path (Join-Path $root 'logs') | Out-Null
    [IO.File]::WriteAllText((Join-Path $root 'logs\keep.log'), 'historical logs')
    [IO.File]::WriteAllText((Join-Path $outside 'untouched.txt'), 'outside file')

    # Dry-run with two compiled, genuine COM DLLs is eligible and immutable.
    $valid = New-Candidate
    $classicPath = Join-Path $valid 'RightConvertClassic.dll'
    $validHash = (Get-FileHash -LiteralPath $classicPath -Algorithm SHA256).Hash
    $before = Invoke-Audit -Candidate $valid
    if ($before.Status -ne 'Eligible' -or $before.ClassicSHA256 -ne $validHash) {
        throw "Valid read-only inspection failed: $($before.Detail)"
    }
    if (-not (Test-Path -LiteralPath $classicPath -PathType Leaf)) {
        throw 'Default dry-run unexpectedly deleted a DLL.'
    }
    # Must refuse while DLL is mapped into a process, without stopping it.
    $module = [RightConvertLegacyLockTest]::LoadLibrary($classicPath)
    if ($module -eq [IntPtr]::Zero) { throw 'Unable to map native COM DLL for lock test.' }
    $locked = Invoke-Audit -Candidate $valid
    if ($locked.Status -ne 'Locked' -or $locked.Detail -notmatch 'Sign out') {
        throw "Loaded COM DLL should report sign-out required: $($locked.Detail)"
    }
    $blocked = $false
    try {
        & $auditScript -TestFixtureRoot $root -BackupPath $valid -Cleanup | Out-Null
    } catch { $blocked = $true }
    if (-not $blocked -or -not (Test-Path -LiteralPath $classicPath -PathType Leaf)) {
        throw 'Cleanup did not refuse the loaded DLL without partial deletion.'
    }
    if (-not [RightConvertLegacyLockTest]::FreeLibrary($module)) {
        throw 'Could not release test DLL.'
    }
    $module = [IntPtr]::Zero
    $deleted = @(& $auditScript -TestFixtureRoot $root -BackupPath $valid -Cleanup)
    if ($deleted.Count -ne 1 -or $deleted[0].Status -ne 'Eligible' -or
        (Test-Path -LiteralPath $valid)) {
        throw 'Explicit cleanup did not remove exactly the eligible backup.'
    }
    if (-not (Test-Path -LiteralPath (Join-Path $root 'settings.json'))) {
        throw 'Cleanup modified unrelated user preferences.'
    }

    # Stage3 real-world historical state: prior uninstall removed the root
    # install-marker while a pair of locked shell DLLs remained in a backup.
    Remove-Item -LiteralPath (Join-Path $root 'install-marker.txt') -Force
    $dataOnly = New-Candidate
    $history = Invoke-Audit -Candidate $dataOnly
    if ($history.Status -ne 'Eligible' -or $history.Detail -notmatch 'data-only') {
        throw "Historical data-only root was rejected: $($history.Detail)"
    }
    $allCandidates = @(& $auditScript -TestFixtureRoot $root)
    if ($allCandidates.Count -ne 1 -or $allCandidates[0].Status -ne 'Eligible') {
        throw 'Default read-only discovery did not find the data-only backup.'
    }
    $null = @(& $auditScript -TestFixtureRoot $root -BackupPath $dataOnly -Cleanup)
    if (Test-Path -LiteralPath $dataOnly) {
        throw 'Data-only backup cleanup did not finish.'
    }
    if (([IO.File]::ReadAllText((Join-Path $root 'settings.json'))) -ne '{"keep":true}' -or
        -not (Test-Path -LiteralPath (Join-Path $root 'logs\keep.log'))) {
        throw 'Data-only cleanup altered settings or logs.'
    }

    $invalidDataOnly = New-Candidate
    [IO.File]::WriteAllText((Join-Path $root 'unknown-user-file.txt'), 'foreign')
    Assert-Refused -Candidate $invalidDataOnly
    if (-not (Test-Path -LiteralPath (Join-Path $root 'unknown-user-file.txt'))) {
        throw 'Foreign root file was altered.'
    }
    Remove-Item -LiteralPath (Join-Path $root 'unknown-user-file.txt') -Force
    [IO.File]::WriteAllText((Join-Path $root 'install-marker.txt'), 'RightConvert.PSInstaller.v1')

    # Additional files, directories and sidecar ownership markers are refused.
    $extra = New-Candidate
    [IO.File]::WriteAllText((Join-Path $extra 'keep.txt'), 'do not delete')
    Assert-Refused -Candidate $extra
    if (-not (Test-Path -LiteralPath (Join-Path $extra 'keep.txt'))) {
        throw 'Unrelated file lost.'
    }
    $nested = New-Candidate
    New-Item -ItemType Directory -Path (Join-Path $nested 'subfolder') | Out-Null
    Assert-Refused -Candidate $nested
    $owned = New-Candidate
    [IO.File]::WriteAllText(($owned + '.owner'), 'RightConvert.PSInstaller.v1')
    Assert-Refused -Candidate $owned
    $linkedOwner = New-Candidate
    $ownerLink = $linkedOwner + '.owner'
    New-Item -ItemType Junction -Path $ownerLink -Target $outside | Out-Null
    try {
        Assert-Refused -Candidate $linkedOwner
    } finally {
        if (Test-Path -LiteralPath $ownerLink) { [IO.Directory]::Delete($ownerLink) }
    }

    # Forged/renamed native files cannot satisfy the expected PE+COM evidence.
    $forged = New-Candidate
    [IO.File]::WriteAllText((Join-Path $forged 'RightConvertExplorer.dll'), 'not a PE DLL')
    Assert-Refused -Candidate $forged
    $swapped = New-Candidate
    Copy-Item -LiteralPath $classic -Destination (Join-Path $swapped 'RightConvertExplorer.dll') -Force
    Assert-Refused -Candidate $swapped

    # A directory reparse point must never be traversed or cleaned.
    $linked = Join-Path $root ('.backup-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Junction -Path $linked -Target $outside | Out-Null
    try {
        Assert-Refused -Candidate $linked
    } finally {
        if (Test-Path -LiteralPath $linked) { [IO.Directory]::Delete($linked) }
    }
    $outsideCandidate = Join-Path $outside ('.backup-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $outsideCandidate | Out-Null
    $escaped = $false
    try {
        & $auditScript -TestFixtureRoot $root -BackupPath $outsideCandidate -Cleanup | Out-Null
    } catch { $escaped = $true }
    if (-not $escaped -or -not (Test-Path -LiteralPath $outsideCandidate)) {
        throw 'Path outside the controlled root was not rejected.'
    }

    # Absent explicit target must never bulk delete.
    $bulkRejected = $false
    try {
        & $auditScript -TestFixtureRoot $root -Cleanup | Out-Null
    } catch { $bulkRejected = $true }
    if (-not $bulkRejected) { throw 'Cleanup without explicit -BackupPath was accepted.' }
    Write-Host 'PASS: legacy backup dry-run, exact DLL content/CLSID/PE, loaded-DLL refusal, explicit cleanup, foreign files/junction/path protection.'
} finally {
    if ($module -ne [IntPtr]::Zero) {
        $null = [RightConvertLegacyLockTest]::FreeLibrary($module)
    }
    # Only named disposable fixture roots in installer/out are ever removed by
    # the regression, after unlinking potential junctions and verifying paths.
    foreach ($candidate in @($root, $outside)) {
        $full = [IO.Path]::GetFullPath($candidate).TrimEnd('\')
        if (-not ([IO.Path]::GetDirectoryName($full)).Equals(
                     $base, [StringComparison]::OrdinalIgnoreCase) -or
            [IO.Path]::GetFileName($full) -notmatch ('^legacy-(regression|outside)-' + $id + '$')) {
            throw "Refusing fixture teardown outside installer/out: $full"
        }
        if (Test-Path -LiteralPath $full) {
            foreach ($entry in [IO.Directory]::EnumerateFileSystemEntries($full)) {
                if (([IO.File]::GetAttributes($entry) -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                    throw "Refusing to recurse into unexpected remaining reparse point: $entry"
                }
            }
            Remove-Item -LiteralPath $full -Recurse -Force
        }
    }
}
