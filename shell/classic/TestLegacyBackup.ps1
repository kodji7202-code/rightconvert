# Stage 5 regression for the exact, unmarked Stage 3 backup (NO registry edits).
# Gets the frozen Stage 3 DLL snapshot from the existing release ZIP; never
# reads or modifies the installed user's %LOCALAPPDATA%\RightConvert tree.
[CmdletBinding()]
param([string]$ReleaseArchive)
$ErrorActionPreference = 'Stop'
. (Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) 'installer\common.ps1')

if ([string]::IsNullOrWhiteSpace($ReleaseArchive)) {
    $ReleaseArchive = Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) 'release\RightConvert-0.1.0-win-x64.zip'
}
if (-not (Test-Path -LiteralPath $ReleaseArchive -PathType Leaf)) {
    throw 'The frozen Stage 3 release archive is missing; do not fabricate a passing signature fixture.'
}
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [IO.Compression.ZipFile]::OpenRead((Resolve-Path -LiteralPath $ReleaseArchive).ProviderPath)
$knownBytes = @{}
try {
    foreach ($name in @($script:RightConvertKnownLegacyFiles.Keys)) {
        $entryName = 'dist/RightConvert/' + $name
        $entry = $zip.Entries | Where-Object { $_.FullName -ceq $entryName } | Select-Object -First 1
        if ($null -eq $entry -or $entry.Length -ne 138240) {
            throw "Frozen release archive is missing the exact Stage 3 DLL: $entryName"
        }
        $stream = $entry.Open()
        $memory = New-Object IO.MemoryStream
        try {
            $stream.CopyTo($memory)
            $knownBytes[$name] = $memory.ToArray()
        } finally {
            $memory.Dispose()
            $stream.Dispose()
        }
        $digest = [BitConverter]::ToString(
            [Security.Cryptography.SHA256]::Create().ComputeHash($knownBytes[$name])).Replace('-', '')
        if ($digest -ne $script:RightConvertKnownLegacyFiles[$name]) {
            throw "Frozen release DLL digest mismatch: $entryName"
        }
    }
} finally { $zip.Dispose() }

$testParent = Join-Path $PSScriptRoot 'out'
New-Item -Path $testParent -ItemType Directory -Force | Out-Null
$root = Join-Path $testParent ('legacy-smoke-' + [guid]::NewGuid().ToString('N'))
$backup = Join-Path $root $script:RightConvertKnownLegacyBackup
$external = Join-Path $testParent ('legacy-external-' + [guid]::NewGuid().ToString('N'))
function Get-RightConvertRoot { return $root }

function Assert-State {
    param([string]$Expected)
    $actual = Get-RightConvertInstallState -Root $root
    if ($actual -ne $Expected) {
        throw "State mismatch: wanted $Expected, got $actual"
    }
}
function Assert-UnsafeRejected {
    param([string]$Case)
    $rejected = $false
    try { $null = Get-RightConvertInstallState -Root $root }
    catch { $rejected = $true }
    if (-not $rejected) { throw "Dangerous legacy backup was accepted: $Case" }
}

try {
    New-Item -Path $backup -ItemType Directory -Force | Out-Null
    New-Item -Path (Join-Path $root 'logs') -ItemType Directory | Out-Null
    [IO.File]::WriteAllText((Join-Path $root 'settings.json'), '{"keep":true}')
    foreach ($name in @($script:RightConvertKnownLegacyFiles.Keys)) {
        [IO.File]::WriteAllBytes((Join-Path $backup $name), $knownBytes[$name])
    }

    Assert-State 'data-only'
    if (@(Get-RightConvertOwnedBackups -Root $root -QuietUnmarked).Count -ne 0) {
        throw 'The known legacy backup was adopted as installer-owned.'
    }
    $shapeBefore = @(Get-ChildItem -LiteralPath $backup -Force).Count
    Assert-State 'data-only'
    if (@(Get-ChildItem -LiteralPath $backup -Force).Count -ne $shapeBefore) {
        throw 'Read-only legacy preflight changed the backup.'
    }

    $unknown = Join-Path $backup 'foreign.txt'
    [IO.File]::WriteAllText($unknown, 'unrelated')
    Assert-UnsafeRejected 'extra foreign file'
    Remove-Item -LiteralPath $unknown

    $classic = Join-Path $backup 'RightConvertClassic.dll'
    $corrupt = [IO.File]::ReadAllBytes($classic)
    $corrupt[0] = $corrupt[0] -bxor 1
    [IO.File]::WriteAllBytes($classic, $corrupt)
    Assert-UnsafeRejected 'tampered DLL digest'
    [IO.File]::WriteAllBytes($classic, $knownBytes['RightConvertClassic.dll'])
    Assert-State 'data-only'

    New-Item -Path $external -ItemType Directory | Out-Null
    $alias = Join-Path $external 'shared.dll'
    New-Item -ItemType HardLink -Path $alias -Target $classic | Out-Null
    Assert-UnsafeRejected 'hardlink to external alias'
    Remove-Item -LiteralPath $alias
    Assert-State 'data-only'

    $junction = Join-Path $backup 'linked-directory'
    New-Item -ItemType Junction -Path $junction -Target $external | Out-Null
    try { Assert-UnsafeRejected 'nested junction' }
    finally { [IO.Directory]::Delete($junction) }
    Assert-State 'data-only'

    $other = Join-Path $root ('.backup-' + [guid]::NewGuid().ToString('N'))
    New-Item -Path $other -ItemType Directory | Out-Null
    foreach ($name in @($script:RightConvertKnownLegacyFiles.Keys)) {
        [IO.File]::WriteAllBytes((Join-Path $other $name), $knownBytes[$name])
    }
    Assert-UnsafeRejected 'unrecognized legacy GUID even with identical DLLs'
    Remove-Item -LiteralPath $other -Recurse -Force
    Assert-State 'data-only'

    Write-Host 'PASS: exact Stage3 backup recognized but unowned; foreign, tampered, hardlinked, junction and other GUID rejected.'
} finally {
    foreach ($link in @((Join-Path $backup 'linked-directory'))) {
        if (Test-Path -LiteralPath $link) {
            $attributes = [IO.File]::GetAttributes($link)
            if (($attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                [IO.Directory]::Delete($link)
            }
        }
    }
    $absoluteParent = [IO.Path]::GetFullPath($testParent).TrimEnd('\')
    foreach ($path in @($root, $external)) {
        if (-not [IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($path).TrimEnd('\')).Equals(
            $absoluteParent, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Fixture cleanup path escaped isolated test root: $path"
        }
        if (Test-Path -LiteralPath $path) {
            Assert-TreeHasNoReparsePoints -Path $path
            Remove-Item -LiteralPath $path -Recurse -Force
        }
    }
}
