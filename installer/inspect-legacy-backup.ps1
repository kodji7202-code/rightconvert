<#
.SYNOPSIS
Read-only audit of old RightConvert backup folders; optional explicit cleanup.
.DESCRIPTION
This utility handles *unmarked* Stage3 legacy backups containing exactly
RightConvertClassic.dll and RightConvertExplorer.dll. It does not use or modify
the installer registration, user preferences, Explorer, or HKCU RunOnce.

Without -Cleanup the utility is read-only, including when -BackupPath is set.
-Cleanup requires one explicit -BackupPath. It NEVER recursively deletes a
directory, follows reparse points, or deletes an unknown file.
.EXAMPLE
.\installer\inspect-legacy-backup.ps1
.EXAMPLE
.\installer\inspect-legacy-backup.ps1 -BackupPath "$env:LOCALAPPDATA\RightConvert\.backup-0123456789abcdef0123456789abcdef"
.EXAMPLE
.\installer\inspect-legacy-backup.ps1 -BackupPath "$env:LOCALAPPDATA\RightConvert\.backup-0123456789abcdef0123456789abcdef" -Cleanup
#>
[CmdletBinding()]
param(
    [string]$BackupPath,
    [switch]$Cleanup,
    [string]$TestFixtureRoot
)
$ErrorActionPreference = 'Stop'

$installerIdentity = 'RightConvert.PSInstaller.v1'
$expectedNames = @('RightConvertClassic.dll', 'RightConvertExplorer.dll')
$expectedIds = @{
    'RightConvertClassic.dll' = [guid]'d172f75d-5505-42a8-ae5a-380b0406b48c'
    'RightConvertExplorer.dll' = [guid]'aa7fb3f0-5e6c-4d75-a22f-c0a8d7a63211'
}

function Assert-NotReparse {
    param([Parameter(Mandatory = $true)][string]$Path)
    $attributes = [IO.File]::GetAttributes($Path)
    if (($attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing junction, symbolic link, or other reparse point: $Path"
    }
}

function Test-ContainsBytes {
    param([byte[]]$Data, [byte[]]$Needle)
    if ($Needle.Length -eq 0 -or $Data.Length -lt $Needle.Length) { return $false }
    for ($start = 0; $start -le $Data.Length - $Needle.Length; $start++) {
        if ($Data[$start] -ne $Needle[0]) { continue }
        $matched = $true
        for ($index = 1; $index -lt $Needle.Length; $index++) {
            if ($Data[$start + $index] -ne $Needle[$index]) {
                $matched = $false
                break
            }
        }
        if ($matched) { return $true }
    }
    return $false
}

function Assert-ExpectedRightConvertDll {
    param([string]$Path, [guid]$Clsid)
    Assert-NotReparse -Path $Path
    $info = Get-Item -LiteralPath $Path -Force
    if ($info.PSIsContainer -or $info.Length -lt 1024 -or $info.Length -gt 32MB) {
        throw "Unexpected native DLL length or type: $Path"
    }
    $data = [IO.File]::ReadAllBytes($Path)
    if ($data[0] -ne 0x4d -or $data[1] -ne 0x5a) {
        throw "Not a Windows PE image: $Path"
    }
    $ntOffset = [BitConverter]::ToInt32($data, 0x3c)
    if ($ntOffset -lt 0x40 -or $ntOffset -gt $data.Length - 0x1a) {
        throw "Invalid PE header offset: $Path"
    }
    if ($data[$ntOffset] -ne 0x50 -or $data[$ntOffset + 1] -ne 0x45 -or
        $data[$ntOffset + 2] -ne 0 -or $data[$ntOffset + 3] -ne 0) {
        throw "Invalid PE signature: $Path"
    }
    if ([BitConverter]::ToUInt16($data, $ntOffset + 4) -ne 0x8664 -or
        [BitConverter]::ToUInt16($data, $ntOffset + 24) -ne 0x20b -or
        ([BitConverter]::ToUInt16($data, $ntOffset + 22) -band 0x2000) -eq 0) {
        throw "Not an x64 Windows PE DLL: $Path"
    }
    # Distinct RightConvert COM identifiers plus both expected exported names.
    # This is structural evidence, NOT an Authenticode signature or cryptographic
    # proof of provenance. Cleanup still requires the user's explicit choice.
    if (-not (Test-ContainsBytes -Data $data -Needle $Clsid.ToByteArray())) {
        throw "Expected RightConvert COM CLSID is absent: $Path"
    }
    $asText = [Text.Encoding]::ASCII.GetString($data)
    foreach ($export in @('DllGetClassObject', 'DllCanUnloadNow')) {
        if (-not $asText.Contains($export)) {
            throw "Missing expected COM export $export in: $Path"
        }
    }
}

function Assert-ControlledRootState {
    param([string]$Root)
    if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
        throw 'The controlled RightConvert root does not exist.'
    }
    Assert-NotReparse -Path $Root
    $marker = Join-Path $Root 'install-marker.txt'
    $rootEntries = @([IO.Directory]::EnumerateFileSystemEntries($Root))
    $hasMarker = @($rootEntries | Where-Object {
        [IO.Path]::GetFileName($_).Equals('install-marker.txt',
                                       [StringComparison]::OrdinalIgnoreCase)
    }).Count -gt 0
    if ($hasMarker) {
        if (-not (Test-Path -LiteralPath $marker -PathType Leaf)) {
            throw 'Installation ownership marker is not a regular file.'
        }
        Assert-NotReparse -Path $marker
        if (([IO.File]::ReadAllText($marker).Trim()) -cne $installerIdentity) {
            throw 'Installation ownership marker is not RightConvert.'
        }
        return 'installed'
    }

    # Historical P1: an interrupted old uninstall removed install-marker.txt,
    # leaving only user prefs/logs and an unmarked .backup-GUID. This exact
    # data-only shape is also allowed, without granting ownership of arbitrary
    # folders or deleting user data.
    foreach ($entry in $rootEntries) {
        Assert-NotReparse -Path $entry
        $name = [IO.Path]::GetFileName($entry)
        $attributes = [IO.File]::GetAttributes($entry)
        $isDirectory = ($attributes -band [IO.FileAttributes]::Directory) -ne 0
        if ($name.Equals('settings.json', [StringComparison]::OrdinalIgnoreCase)) {
            if ($isDirectory) { throw 'settings.json is not a regular file.' }
        } elseif ($name.Equals('logs', [StringComparison]::OrdinalIgnoreCase)) {
            if (-not $isDirectory) { throw 'logs is not a directory.' }
        } elseif ($name -cmatch '^\.backup-[0-9a-f]{32}$') {
            if (-not $isDirectory) { throw 'A legacy backup candidate is not a directory.' }
        } else {
            throw "Unrecognized data-only root entry: $name"
        }
    }
    return 'data-only'
}

function Get-Audit {
    param([string]$Root, [string]$Candidate)
    $result = [ordered]@{
        Backup = $Candidate
        Status = 'Refused'
        Detail = ''
        ClassicSHA256 = ''
        ExplorerSHA256 = ''
    }
    try {
        $rootState = Assert-ControlledRootState -Root $Root
        if (-not (Test-Path -LiteralPath $Candidate -PathType Container)) {
            throw 'Backup directory does not exist.'
        }
        Assert-NotReparse -Path $Candidate
        if (-not ([IO.Path]::GetDirectoryName($Candidate)).Equals(
                 $Root, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Backup is not a direct child of the controlled root.'
        }
        $leaf = [IO.Path]::GetFileName($Candidate)
        if ($leaf -cnotmatch '^\.backup-[0-9a-f]{32}$') {
            throw 'Not a .backup-GUID directory.'
        }
        $sidecar = Join-Path $Root ($leaf + '.owner')
        # Enumerate the parent entry by its own filename, not by resolving the
        # target: even a dangling sidecar symlink is an ownership marker or
        # untrusted reparse entry and must block this legacy-only workflow.
        $ownerEntryExists = $false
        foreach ($entry in [IO.Directory]::EnumerateFileSystemEntries($Root)) {
            if ([IO.Path]::GetFileName($entry).Equals(
                    [IO.Path]::GetFileName($sidecar),
                    [StringComparison]::OrdinalIgnoreCase)) {
                $ownerEntryExists = $true
                break
            }
        }
        if ($ownerEntryExists) {
            throw 'Marked backup: use the standard installer cleanup, not this legacy tool.'
        }
        $entries = @([IO.Directory]::EnumerateFileSystemEntries($Candidate))
        if ($entries.Count -ne 2) {
            throw 'Legacy backup must contain exactly two DLL files, with no extra files or folders.'
        }
        $actualNames = @($entries | ForEach-Object { [IO.Path]::GetFileName($_) })
        foreach ($name in $expectedNames) {
            if (-not ($actualNames -ccontains $name)) {
                throw "Unexpected backup contents. Expected exactly: $($expectedNames -join ', ')"
            }
        }
        foreach ($name in $expectedNames) {
            $dll = Join-Path $Candidate $name
            if (-not (Test-Path -LiteralPath $dll -PathType Leaf)) {
                throw "Expected DLL is missing or not a regular file: $name"
            }
            Assert-ExpectedRightConvertDll -Path $dll -Clsid $expectedIds[$name]
            if ($name -ceq 'RightConvertClassic.dll') {
                $result.ClassicSHA256 = (Get-FileHash -LiteralPath $dll -Algorithm SHA256).Hash
            } else {
                $result.ExplorerSHA256 = (Get-FileHash -LiteralPath $dll -Algorithm SHA256).Hash
            }
        }
        # Open both files exclusively before attempting any cleanup. A mapped
        # DLL in Explorer cannot be opened this way for read/write.
        $handles = @()
        try {
            foreach ($name in $expectedNames) {
                try {
                    $handle = [IO.File]::Open(
                        (Join-Path $Candidate $name), [IO.FileMode]::Open,
                        [IO.FileAccess]::ReadWrite, [IO.FileShare]::None
                    )
                    $handles += $handle
                } catch [IO.IOException] {
                    $result.Status = 'Locked'
                    $result.Detail = 'DLL is in use. Sign out and sign back in before cleanup; Explorer will not be stopped.'
                    return [pscustomobject]$result
                } catch [UnauthorizedAccessException] {
                    $result.Status = 'Locked'
                    $result.Detail = 'DLL access denied. Close programs or sign out before cleanup; no deletion attempted.'
                    return [pscustomobject]$result
                }
            }
        } finally {
            foreach ($handle in $handles) { $handle.Dispose() }
        }
        $result.Status = 'Eligible'
        $result.Detail = "Root state: $rootState. Exact two known RightConvert x64 COM DLLs; no links or unrelated files; not currently locked."
    } catch {
        $result.Status = 'Refused'
        $result.Detail = $_.Exception.Message
    }
    return [pscustomobject]$result
}

function Resolve-ControlledRoot {
    if ($TestFixtureRoot) {
        # Only a direct disposable test directory under installer/out is valid;
        # callers cannot use this parameter to delete arbitrary folders.
        $base = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'out')).TrimEnd('\')
        $fixture = [IO.Path]::GetFullPath($TestFixtureRoot).TrimEnd('\')
        if (-not ([IO.Path]::GetDirectoryName($fixture)).Equals(
                    $base, [StringComparison]::OrdinalIgnoreCase) -or
            ([IO.Path]::GetFileName($fixture)) -cnotmatch '^legacy-regression-[0-9a-f]{32}$') {
            throw 'TestFixtureRoot must be an installer/out/legacy-regression-GUID direct child.'
        }
        if (Test-Path -LiteralPath $base) { Assert-NotReparse -Path $base }
        return $fixture
    }
    $local = [Environment]::GetFolderPath('LocalApplicationData')
    if ([string]::IsNullOrWhiteSpace($local)) { throw 'LocalApplicationData unavailable.' }
    return [IO.Path]::GetFullPath((Join-Path $local 'RightConvert')).TrimEnd('\')
}

$root = Resolve-ControlledRoot
if ($Cleanup -and -not $BackupPath) {
    throw 'Cleanup requires an explicit -BackupPath; bulk deletion is not supported.'
}
if ($BackupPath) {
    $candidate = [IO.Path]::GetFullPath($BackupPath).TrimEnd('\')
    if (-not ([IO.Path]::GetDirectoryName($candidate)).Equals(
              $root, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Refusing a path outside the controlled root, or a nested path.'
    }
    $targets = @($candidate)
} elseif (Test-Path -LiteralPath $root -PathType Container) {
    Assert-NotReparse -Path $root
    $targets = @(
        [IO.Directory]::EnumerateFileSystemEntries($root) |
            Where-Object { [IO.Path]::GetFileName($_) -cmatch '^\.backup-[0-9a-f]{32}$' }
    )
} else {
    $targets = @()
}

if ($targets.Count -eq 0) {
    Write-Host 'No legacy .backup-GUID candidates found. No changes made.'
    return
}
foreach ($candidate in $targets) {
    $audit = Get-Audit -Root $root -Candidate $candidate
    Write-Output $audit
    if (-not $Cleanup) { continue }
    if ($audit.Status -ne 'Eligible') {
        throw "Cleanup refused ($($audit.Status)): $($audit.Detail)"
    }
    # Repeat all checks immediately before removal. Delete only the exact two
    # filenames individually. No recursive deletion: any extra file appearing
    # after the audit will make Directory.Delete(non-recursive) fail safely.
    $again = Get-Audit -Root $root -Candidate $candidate
    if ($again.Status -ne 'Eligible' -or
        $again.ClassicSHA256 -cne $audit.ClassicSHA256 -or
        $again.ExplorerSHA256 -cne $audit.ExplorerSHA256) {
        throw 'Backup changed or became locked after audit. No cleanup attempted.'
    }
    foreach ($name in $expectedNames) {
        $file = Join-Path $candidate $name
        Assert-NotReparse -Path $candidate
        Assert-NotReparse -Path $file
        [IO.File]::Delete($file)
    }
    [IO.Directory]::Delete($candidate, $false)
    Write-Host 'Removed only the verified legacy backup directory. The active application is unchanged.'
}
