# Shared helpers for RightConvert's per-user Windows installer.
# This file must also be copied next to uninstall.ps1 in the installation directory.

$script:RightConvertInstallerId = 'RightConvert.PSInstaller.v1'
$script:RightConvertVerbKey = 'Software\Classes\*\shell\RightConvert'
$script:RightConvertUninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\RightConvert'
$script:RightConvertClassicClsid = '{D172F75D-5505-42A8-AE5A-380B0406B48C}'
$script:RightConvertClassicKey = 'Software\Classes\CLSID\' + $script:RightConvertClassicClsid
$script:RightConvertQuickVerbKey = 'Software\Classes\*\shell\RightConvertQuick'
$script:RightConvertQuickHandlerKey = 'Software\Classes\*\shellex\ContextMenuHandlers\RightConvertQuick'
$script:RightConvertQuickCascadeClsid = '{839E65D5-A748-4A0F-A9F3-A846924A7535}'
$script:RightConvertQuickMarker = 'rightconvert-direct-cli-v2.marker'
# Keep the fixed COM class IDs and target strings aligned with
# shell/classic/RightConvertClassic.cpp and its native argv regression.
$script:RightConvertQuickTargets = [ordered]@{
    jpg  = '{E82A6CD6-85C7-4D15-9ED2-1A44A6500701}'
    png  = '{E82A6CD6-85C7-4D15-9ED2-1A44A6500702}'
    webp = '{E82A6CD6-85C7-4D15-9ED2-1A44A6500703}'
    mp3  = '{E82A6CD6-85C7-4D15-9ED2-1A44A6500704}'
    wav  = '{E82A6CD6-85C7-4D15-9ED2-1A44A6500705}'
    flac = '{E82A6CD6-85C7-4D15-9ED2-1A44A6500706}'
    mp4  = '{E82A6CD6-85C7-4D15-9ED2-1A44A6500707}'
    webm = '{E82A6CD6-85C7-4D15-9ED2-1A44A6500708}'
    pdf  = '{E82A6CD6-85C7-4D15-9ED2-1A44A6500709}'
    tiff = '{E82A6CD6-85C7-4D15-9ED2-1A44A6500710}'
    pngzip = '{E82A6CD6-85C7-4D15-9ED2-1A44A6500711}'
}
$script:RightConvertStageMarker = '.rightconvert-stage-owner.txt'
$script:RightConvertRunOnceKey = 'Software\Microsoft\Windows\CurrentVersion\RunOnce'
$script:RightConvertRunOnceValue = '!RightConvert.PendingCleanup'
$script:RightConvertBackupRunOnceValue = '!RightConvert.BackupCleanup'
$script:RightConvertBackupOwnerSuffix = '.owner'
# Recovery allowlist for the *one* independently inspected pre-marker backup
# left by the Stage 3 installer. It is user data, never adopted or cleaned.
# Do not broaden this to arbitrary .backup-GUID directories: the owner marker
# introduced later is the only authority for deletion.
$script:RightConvertKnownLegacyBackup = '.backup-4592261ac4804466b2299d7b1c6d3220'
$script:RightConvertKnownLegacyFiles = [ordered]@{
    'RightConvertClassic.dll' = '9EB897E798CCA1AC1A70A3C616CAC2DEEC4FD532C7E3218B43D3C12CD56CBC83'
    'RightConvertExplorer.dll' = '9DEB5409A66E3C481153B310131F6C01A53A09C3B77F3D8883160C3534D943D1'
}

function Get-RightConvertRoot {
    $localData = [Environment]::GetFolderPath('LocalApplicationData')
    if ([string]::IsNullOrWhiteSpace($localData)) {
        throw 'The current account has no LocalApplicationData directory.'
    }
    return [IO.Path]::GetFullPath((Join-Path $localData 'RightConvert'))
}

function Assert-RightConvertPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    $root = Get-RightConvertRoot
    $normalized = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    if (-not $normalized.Equals($root.TrimEnd('\'), [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to modify an installation outside $root"
    }
    if (Test-Path -LiteralPath $root) {
        $item = Get-Item -LiteralPath $root -Force
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw 'Refusing to use an installation directory that is a junction or symbolic link.'
        }
    }
}

function Assert-NotReparsePoint {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (Test-Path -LiteralPath $Path) {
        $item = Get-Item -LiteralPath $Path -Force
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Refusing to modify a junction or symbolic link: $Path"
        }
    }
}

function Assert-TreeHasNoReparsePoints {
    # Do not use Get-ChildItem -Recurse: Windows PowerShell 5.1 can traverse
    # directory junctions and follow paths outside the intended subtree.
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $stack = New-Object 'System.Collections.Generic.Stack[string]'
    $stack.Push([IO.Path]::GetFullPath($Path))
    while ($stack.Count -gt 0) {
        $current = $stack.Pop()
        $attributes = [IO.File]::GetAttributes($current)
        if (($attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Refusing to recurse into a junction or symbolic link: $current"
        }
        if (($attributes -band [IO.FileAttributes]::Directory) -ne 0) {
            foreach ($child in [IO.Directory]::EnumerateFileSystemEntries($current)) {
                $stack.Push($child)
            }
        }
    }
}

function Assert-RightConvertOwnedTree {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Path
    )
    $expectedRoot = [IO.Path]::GetFullPath($Root).TrimEnd('\')
    $fullPath = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    $parent = [IO.Path]::GetDirectoryName($fullPath)
    if (-not $parent.Equals($expectedRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to delete a directory outside the RightConvert installation: $fullPath"
    }
    $leaf = [IO.Path]::GetFileName($fullPath)
    if ($leaf -ne 'app' -and $leaf -notmatch '^\.(stage|backup)-[0-9a-f]{32}$') {
        throw "Refusing recursive deletion of an unknown RightConvert folder: $leaf"
    }
    Assert-TreeHasNoReparsePoints -Path $fullPath
}

function Remove-RightConvertOwnedTree {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Path
    )
    Assert-RightConvertOwnedTree -Root $Root -Path $Path
    if (Test-Path -LiteralPath $Path) {
        Remove-Item -LiteralPath $Path -Recurse -Force
    }
}

function Get-RightConvertBackupMarker {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Backup
    )
    Assert-RightConvertPath -Path $Root
    $normalized = [IO.Path]::GetFullPath($Backup).TrimEnd('\')
    if (-not ([IO.Path]::GetDirectoryName($normalized)).Equals(
              [IO.Path]::GetFullPath($Root).TrimEnd('\'),
              [StringComparison]::OrdinalIgnoreCase)) {
        throw "Backup is not directly inside the installation root: $Backup"
    }
    $leaf = [IO.Path]::GetFileName($normalized)
    if ($leaf -notmatch '^\.backup-[0-9a-f]{32}$') {
        throw "Not a RightConvert backup GUID directory: $Backup"
    }
    return (Join-Path $Root ($leaf + $script:RightConvertBackupOwnerSuffix))
}

function Write-RightConvertBackupMarker {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Backup
    )
    $marker = Get-RightConvertBackupMarker -Root $Root -Backup $Backup
    Assert-RightConvertOwnedTree -Root $Root -Path $Backup
    if (-not (Test-Path -LiteralPath $Backup -PathType Container)) {
        throw "Backup directory is missing: $Backup"
    }
    Assert-NotReparsePoint -Path $marker
    $leaf = [IO.Path]::GetFileName([IO.Path]::GetFullPath($Backup).TrimEnd('\'))
    $contents = $script:RightConvertInstallerId + '|' + $leaf
    # CreateNew prevents overwriting an existing owner's marker.
    $stream = [IO.File]::Open($marker, [IO.FileMode]::CreateNew,
                              [IO.FileAccess]::Write, [IO.FileShare]::None)
    try {
        $bytes = [Text.Encoding]::UTF8.GetBytes($contents)
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush()
    } finally { $stream.Dispose() }
}

function Get-RightConvertOwnedBackups {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [switch]$QuietUnmarked
    )
    Assert-RightConvertPath -Path $Root
    if (-not (Test-Path -LiteralPath $Root -PathType Container)) { return }
    foreach ($item in @(Get-ChildItem -LiteralPath $Root -Force)) {
        if ($item.Name -notmatch '^\.backup-[0-9a-f]{32}$') { continue }
        if (-not $item.PSIsContainer) {
            throw "Backup path must be a directory: $($item.FullName)"
        }
        $marker = Get-RightConvertBackupMarker -Root $Root -Backup $item.FullName
        if (-not (Test-Path -LiteralPath $marker -PathType Leaf)) {
            # Preserve legacy backup from earlier releases with no ownership
            # proof. Never adopt/delete it based on the directory name alone.
            if (-not $QuietUnmarked) {
                Write-Warning "Leaving unmarked legacy backup untouched: $($item.FullName)"
            }
            continue
        }
        Assert-NotReparsePoint -Path $marker
        Assert-RightConvertOwnedTree -Root $Root -Path $item.FullName
        $expected = $script:RightConvertInstallerId + '|' + $item.Name
        if (([IO.File]::ReadAllText($marker, [Text.Encoding]::UTF8)) -ne $expected) {
            throw "Backup owner marker does not match its directory: $marker"
        }
        Write-Output $item.FullName
    }
}

function Remove-RightConvertMarkedBackup {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Backup
    )
    $marker = Get-RightConvertBackupMarker -Root $Root -Backup $Backup
    if (-not (@(Get-RightConvertOwnedBackups -Root $Root -QuietUnmarked) -contains
               [IO.Path]::GetFullPath($Backup).TrimEnd('\'))) {
        throw "Refusing removal of unowned backup: $Backup"
    }
    $oldDll = Join-Path $Backup 'RightConvertClassic.dll'
    if (Test-Path -LiteralPath $oldDll -PathType Leaf) {
        Assert-NotReparsePoint -Path $oldDll
        $stream = $null
        try {
            $stream = [IO.File]::Open($oldDll, [IO.FileMode]::Open,
                                     [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
        } catch [IO.IOException] {
            throw 'The previous classic COM DLL is still loaded.'
        } catch [UnauthorizedAccessException] {
            throw 'The previous classic COM DLL cannot be deleted yet.'
        } finally {
            if ($null -ne $stream) { $stream.Dispose() }
        }
    }
    # Sidecar marker survives a partially failing recursive removal, including
    # an Explorer-locked DLL. It is removed only when the directory is gone.
    Remove-RightConvertOwnedTree -Root $Root -Path $Backup
    if (Test-Path -LiteralPath $Backup) { throw "Backup is still present: $Backup" }
    Assert-NotReparsePoint -Path $marker
    Remove-Item -LiteralPath $marker -Force
}

function Get-RightConvertRecoverableStages {
    param([Parameter(Mandatory = $true)][string]$Root)
    if (-not (Test-Path -LiteralPath $Root -PathType Container)) { return }
    foreach ($item in @(Get-ChildItem -LiteralPath $Root -Force)) {
        if ($item.Name -notmatch '^\.stage-[0-9a-f]{32}$') { continue }
        if (-not $item.PSIsContainer) {
            throw "Installer stage is not a directory: $($item.FullName)"
        }
        Assert-RightConvertOwnedTree -Root $Root -Path $item.FullName
        $marker = Join-Path $item.FullName $script:RightConvertStageMarker
        Assert-NotReparsePoint -Path $marker
        if (-not (Test-Path -LiteralPath $marker -PathType Leaf)) {
            throw "Unrecognized installer stage without ownership marker: $($item.FullName)"
        }
        # During an active installation the owner keeps this file exclusively
        # locked. Get-Content will fail sharing checks, preventing cleanup.
        if ((Get-Content -LiteralPath $marker -Raw).Trim() -ne $script:RightConvertInstallerId) {
            throw "Unrecognized installer stage marker: $($item.FullName)"
        }
        Write-Output $item.FullName
    }
}

function Assert-RightConvertSingleLink {
    # A normal Get-FileHash cannot distinguish a foreign NTFS hardlink from a
    # private DLL file. Inspect the kernel file identity through a READ handle,
    # never opening a write handle or following/replacing external aliases.
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not ('RightConvertLegacyFileIdentity' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
public static class RightConvertLegacyFileIdentity {
    [StructLayout(LayoutKind.Sequential)]
    public struct BY_HANDLE_FILE_INFORMATION {
        public uint Attributes;
        public System.Runtime.InteropServices.ComTypes.FILETIME CreationTime;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastAccessTime;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastWriteTime;
        public uint VolumeSerial;
        public uint FileSizeHigh;
        public uint FileSizeLow;
        public uint NumberOfLinks;
        public uint FileIndexHigh;
        public uint FileIndexLow;
    }
    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool GetFileInformationByHandle(
        SafeFileHandle handle, out BY_HANDLE_FILE_INFORMATION info);
}
'@
    }
    $sharing = [IO.FileShare]([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete)
    $stream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, $sharing)
    try {
        $info = New-Object RightConvertLegacyFileIdentity+BY_HANDLE_FILE_INFORMATION
        if (-not [RightConvertLegacyFileIdentity]::GetFileInformationByHandle(
            $stream.SafeFileHandle, [ref]$info)) {
            throw "Cannot check hardlink identity for legacy backup file: $Path"
        }
        if ($info.NumberOfLinks -ne 1) {
            throw "Legacy backup file has external hardlink aliases: $Path"
        }
    } finally { $stream.Dispose() }
}

function Test-RightConvertKnownLegacyBackup {
    # This function only allows one frozen, byte-for-byte verified legacy
    # backup to COEXIST with preserved settings/logs. It grants NO ownership:
    # Get-RightConvertOwnedBackups continues to ignore it, even after install.
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Path
    )
    $fullRoot = [IO.Path]::GetFullPath($Root).TrimEnd('\')
    $fullPath = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    if (-not [IO.Path]::GetDirectoryName($fullPath).Equals(
        $fullRoot, [StringComparison]::OrdinalIgnoreCase) -or
        -not [IO.Path]::GetFileName($fullPath).Equals(
        $script:RightConvertKnownLegacyBackup, [StringComparison]::Ordinal)) {
        return $false
    }
    Assert-NotReparsePoint -Path $fullPath
    if (-not (Test-Path -LiteralPath $fullPath -PathType Container)) {
        throw "Expected known legacy backup directory: $fullPath"
    }
    $found = @(Get-ChildItem -LiteralPath $fullPath -Force)
    if ($found.Count -ne $script:RightConvertKnownLegacyFiles.Count) {
        throw 'Known legacy backup does not have its exact two-file shape.'
    }
    foreach ($entry in $found) {
        Assert-NotReparsePoint -Path $entry.FullName
        if ($entry.PSIsContainer -or -not $script:RightConvertKnownLegacyFiles.Contains($entry.Name)) {
            throw "Unknown or non-file content in known legacy backup: $($entry.FullName)"
        }
        if ($entry.Length -ne 138240) {
            throw "Unexpected DLL size in known legacy backup: $($entry.FullName)"
        }
        Assert-RightConvertSingleLink -Path $entry.FullName
        $digest = (Get-FileHash -LiteralPath $entry.FullName -Algorithm SHA256).Hash
        if (-not $digest.Equals(
            $script:RightConvertKnownLegacyFiles[$entry.Name], [StringComparison]::OrdinalIgnoreCase)) {
            throw "Known legacy backup file does not match the verified Stage 3 digest: $($entry.FullName)"
        }
    }
    return $true
}

function Get-RightConvertInstallState {
    # Pure filesystem check, also used by the regression test.
    # A portable RightConvert launch can create settings.json and logs before
    # the installer ever runs. Those user files must survive reinstallations.
    param([Parameter(Mandatory = $true)][string]$Root)
    if (-not (Test-Path -LiteralPath $Root)) { return 'absent' }
    Assert-NotReparsePoint -Path $Root
    if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
        throw "Installation path exists but is not a directory: $Root"
    }

    $marker = Join-Path $Root 'install-marker.txt'
    if (Test-Path -LiteralPath $marker) {
        Assert-NotReparsePoint -Path $marker
        if (-not (Test-Path -LiteralPath $marker -PathType Leaf) -or
            ((Get-Content -LiteralPath $marker -Raw).Trim() -ne $script:RightConvertInstallerId)) {
            throw "Unrecognized installation marker in $Root"
        }
        return 'installed'
    }

    $stages = @(Get-RightConvertRecoverableStages -Root $Root)
    foreach ($item in @(Get-ChildItem -LiteralPath $Root -Force)) {
        Assert-NotReparsePoint -Path $item.FullName
        if ($stages -contains $item.FullName) {
            continue
        } elseif ($item.Name.Equals('settings.json', [StringComparison]::OrdinalIgnoreCase)) {
            if ($item.PSIsContainer) { throw "Expected a settings file: $($item.FullName)" }
        } elseif ($item.Name.Equals('logs', [StringComparison]::OrdinalIgnoreCase)) {
            if (-not $item.PSIsContainer) { throw "Expected a log directory: $($item.FullName)" }
        } elseif (Test-RightConvertKnownLegacyBackup -Root $Root -Path $item.FullName) {
            # Preservation only: never add marker, adopt, or recursively delete.
            continue
        } else {
            throw "Unexpected existing data without installer ownership: $($item.FullName)"
        }
    }
    return 'data-only'
}

function Assert-ExistingRegistryOwnership {
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($script:RightConvertVerbKey)
    if ($null -ne $key) {
        try {
            if ($key.GetValue('InstallerId', '') -ne $script:RightConvertInstallerId) {
                throw 'A different application owns the RightConvert Explorer verb.'
            }
        } finally {
            $key.Close()
        }
    }
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($script:RightConvertUninstallKey)
    if ($null -ne $key) {
        try {
            if ($key.GetValue('InstallerId', '') -ne $script:RightConvertInstallerId) {
                throw 'A different application owns the RightConvert uninstall registry entry.'
            }
        } finally {
            $key.Close()
        }
    }
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($script:RightConvertClassicKey)
    if ($null -ne $key) {
        try {
            if ($key.GetValue('InstallerId', '') -ne $script:RightConvertInstallerId) {
                throw 'A different application owns the RightConvert classic COM CLSID.'
            }
        } finally {
            $key.Close()
        }
    }
    Assert-RightConvertQuickOwnership
}

function Assert-RightConvertQuickOwnership {
    foreach ($relative in @($script:RightConvertQuickHandlerKey,
                              ('Software\Classes\CLSID\' + $script:RightConvertQuickCascadeClsid))) {
        $owned = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($relative)
        if ($null -ne $owned) {
            try {
                if ($owned.GetValue('InstallerId', '') -ne $script:RightConvertInstallerId) {
                    throw "A different application owns the native conversion submenu: $relative"
                }
            } finally { $owned.Close() }
        }
    }
    $root = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($script:RightConvertQuickVerbKey)
    if ($null -ne $root) {
        try {
            if ($root.GetValue('InstallerId', '') -ne $script:RightConvertInstallerId) {
                throw 'A different application owns the RightConvert quick-target menu.'
            }
            # Both historical shapes remain admissible for *owner-verified
            # removal only*. New installs use ExtendedSubCommandsKey, while
            # the SubCommands + shell layout made an empty Win11 submenu.
            $unexpected = @($root.GetSubKeyNames() | Where-Object {
                $_ -ne 'ExtendedSubCommandsKey' -and $_ -ne 'shell'
            })
            if ($unexpected.Count -gt 0) {
                throw 'The quick-target menu contains unknown child registrations.'
            }
            foreach ($containerName in @('ExtendedSubCommandsKey', 'shell')) {
                $extended = $root.OpenSubKey($containerName)
                if ($null -eq $extended) { continue }
                try {
                    $shell = $null
                    if ($containerName -eq 'ExtendedSubCommandsKey') {
                        $unknown = @($extended.GetSubKeyNames() | Where-Object { $_ -ne 'shell' })
                        if ($unknown.Count -gt 0) {
                            throw 'The quick-target cascade contains unknown registrations.'
                        }
                        $shell = $extended.OpenSubKey('shell')
                    } else {
                        $shell = $extended
                    }
                    if ($null -ne $shell) {
                        try {
                            foreach ($target in @($shell.GetSubKeyNames())) {
                                if (-not $script:RightConvertQuickTargets.Contains($target)) {
                                    throw "Unknown quick-target verb: $target"
                                }
                                $leaf = $shell.OpenSubKey($target)
                                try {
                                    if ($leaf.GetValue('InstallerId', '') -ne $script:RightConvertInstallerId) {
                                        throw "Quick-target verb is not owned by RightConvert: $target"
                                    }
                                    $foreignChildren = @($leaf.GetSubKeyNames() |
                                        Where-Object { $_ -ne 'command' })
                                    if ($foreignChildren.Count -gt 0) {
                                        throw "Quick-target verb has foreign child registrations: $target"
                                    }
                                } finally { $leaf.Close() }
                            }
                        } finally {
                            if ($containerName -ne 'shell') { $shell.Close() }
                        }
                    }
                } finally { $extended.Close() }
            }
        } finally { $root.Close() }
    }
    foreach ($target in @($script:RightConvertQuickTargets.Keys)) {
        $relative = 'Software\Classes\CLSID\' + $script:RightConvertQuickTargets[$target]
        $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($relative)
        if ($null -ne $key) {
            try {
                if ($key.GetValue('InstallerId', '') -ne $script:RightConvertInstallerId) {
                    throw "A different application owns quick-target COM registration: $target"
                }
            } finally { $key.Close() }
        }
    }
}

function Remove-RightConvertQuickCommands {
    Assert-RightConvertQuickOwnership
    $handlerSplit = $script:RightConvertQuickHandlerKey.LastIndexOf('\')
    $handlerParent = $script:RightConvertQuickHandlerKey.Substring(0, $handlerSplit)
    $handlerLeaf = $script:RightConvertQuickHandlerKey.Substring($handlerSplit + 1)
    $handlerRoot = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($handlerParent, $true)
    if ($null -ne $handlerRoot) {
        try { $handlerRoot.DeleteSubKeyTree($handlerLeaf, $false) }
        finally { $handlerRoot.Close() }
    }
    $classRoot = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Software\Classes\CLSID', $true)
    if ($null -ne $classRoot) {
        try { $classRoot.DeleteSubKeyTree($script:RightConvertQuickCascadeClsid, $false) }
        finally { $classRoot.Close() }
    }
    $split = $script:RightConvertQuickVerbKey.LastIndexOf('\')
    $parent = $script:RightConvertQuickVerbKey.Substring(0, $split)
    $leaf = $script:RightConvertQuickVerbKey.Substring($split + 1)
    $parentKey = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($parent, $true)
    if ($null -ne $parentKey) {
        try { $parentKey.DeleteSubKeyTree($leaf, $false) }
        finally { $parentKey.Close() }
    }
    $classes = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Software\Classes\CLSID', $true)
    if ($null -ne $classes) {
        try {
            foreach ($target in @($script:RightConvertQuickTargets.Keys)) {
                $classes.DeleteSubKeyTree($script:RightConvertQuickTargets[$target], $false)
            }
        } finally { $classes.Close() }
    }
}

function Register-RightConvertQuickCommands {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string]$Dll
    )
    Assert-RightConvertPath -Path $Root
    Assert-RightConvertQuickOwnership
    $marker = Join-Path (Join-Path $Root 'app') $script:RightConvertQuickMarker
    if (-not (Test-Path -LiteralPath $marker -PathType Leaf)) {
        # Older binaries must not advertise a CLI they cannot understand.
        Remove-RightConvertQuickCommands
        return
    }
    Assert-NotReparsePoint -Path $marker
    if (([IO.File]::ReadAllText($marker, [Text.Encoding]::ASCII)).Trim() -ne
        'rightconvert-direct-cli-v2') {
        throw 'The quick-target CLI capability marker is invalid.'
    }

    # Replace only independently owner-verified old quick registrations.
    # Native IContextMenu adds visible individual direct-conversion verbs.
    # This avoids the 22x26px empty registry/CommandStore nested submenu.
    Remove-RightConvertQuickCommands
    $classKey = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey(
        ('Software\Classes\CLSID\' + $script:RightConvertQuickCascadeClsid))
    try {
        $classKey.SetValue('', 'RightConvert native direct conversion menu',
                           [Microsoft.Win32.RegistryValueKind]::String)
        $classKey.SetValue('InstallerId', $script:RightConvertInstallerId,
                           [Microsoft.Win32.RegistryValueKind]::String)
        $inproc = $classKey.CreateSubKey('InprocServer32')
        try {
            $inproc.SetValue('', $Dll, [Microsoft.Win32.RegistryValueKind]::String)
            $inproc.SetValue('ThreadingModel', 'Apartment',
                             [Microsoft.Win32.RegistryValueKind]::String)
        } finally { $inproc.Close() }
    } finally { $classKey.Close() }
    $handler = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($script:RightConvertQuickHandlerKey)
    try {
        $handler.SetValue('', $script:RightConvertQuickCascadeClsid,
                          [Microsoft.Win32.RegistryValueKind]::String)
        $handler.SetValue('InstallerId', $script:RightConvertInstallerId,
                          [Microsoft.Win32.RegistryValueKind]::String)
    } finally { $handler.Close() }
}

function Register-RightConvert {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [switch]$EnableExperimentalQuickMenu,
        [switch]$DisableQuickMenu
    )
    Assert-RightConvertPath -Path $Root
    Assert-ExistingRegistryOwnership

    $exe = Join-Path (Join-Path $Root 'app') 'RightConvert.exe'
    $uninstaller = Join-Path $Root 'uninstall.ps1'
    if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) {
        throw "Missing application executable: $exe"
    }
    if (-not (Test-Path -LiteralPath $uninstaller -PathType Leaf)) {
        throw "Missing uninstaller: $uninstaller"
    }
    $classicDll = Join-Path (Join-Path $Root 'app') 'RightConvertClassic.dll'
    $nativeClassic = Test-Path -LiteralPath $classicDll -PathType Leaf
    if ($nativeClassic) {
        Assert-NotReparsePoint -Path $classicDll
        $classKey = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($script:RightConvertClassicKey)
        try {
            $classKey.SetValue('', 'RightConvert classic batch verb', [Microsoft.Win32.RegistryValueKind]::String)
            $classKey.SetValue('InstallerId', $script:RightConvertInstallerId, [Microsoft.Win32.RegistryValueKind]::String)
            $inproc = $classKey.CreateSubKey('InprocServer32')
            try {
                $inproc.SetValue('', $classicDll, [Microsoft.Win32.RegistryValueKind]::String)
                $inproc.SetValue('ThreadingModel', 'Apartment', [Microsoft.Win32.RegistryValueKind]::String)
            } finally {
                $inproc.Close()
            }
        } finally {
            $classKey.Close()
        }
    }

    $verb = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($script:RightConvertVerbKey)
    try {
        $verb.SetValue('', 'Convert with RightConvert', [Microsoft.Win32.RegistryValueKind]::String)
        $verb.SetValue('MUIVerb', 'Convert with RightConvert', [Microsoft.Win32.RegistryValueKind]::String)
        $verb.SetValue('Icon', ('"' + $exe + '",0'), [Microsoft.Win32.RegistryValueKind]::String)
        # Native DelegateExecute COM Player receives ALL selected files in a
        # single IShellItemArray, including selections > 15. Without the DLL
        # the original static Document verb remains usable.
        $verb.SetValue('MultiSelectModel', $(if ($nativeClassic) { 'Player' } else { 'Document' }),
                       [Microsoft.Win32.RegistryValueKind]::String)
        $verb.SetValue('InstallerId', $script:RightConvertInstallerId, [Microsoft.Win32.RegistryValueKind]::String)
        $command = $verb.CreateSubKey('command')
        try {
            $command.SetValue('', ('"' + $exe + '" --files "%1"'), [Microsoft.Win32.RegistryValueKind]::String)
            if ($nativeClassic) {
                $command.SetValue('DelegateExecute', $script:RightConvertClassicClsid,
                                  [Microsoft.Win32.RegistryValueKind]::String)
            } else {
                $command.DeleteValue('DelegateExecute', $false)
            }
        } finally {
            $command.Close()
        }
    } finally {
        $verb.Close()
    }
    if (-not $nativeClassic) {
        Remove-RightConvertClassicClass
    }
    if ($nativeClassic -and (-not $DisableQuickMenu)) {
        Register-RightConvertQuickCommands -Root $Root -Executable $exe -Dll $classicDll
    } else {
        # The classic Player GUI menu remains the stable fallback. A caller
        # may explicitly opt out of the native cascade on an unusual host.
        Remove-RightConvertQuickCommands
    }

    $entry = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($script:RightConvertUninstallKey)
    try {
        $entry.SetValue('DisplayName', 'RightConvert', [Microsoft.Win32.RegistryValueKind]::String)
        $entry.SetValue('Publisher', 'RightConvert', [Microsoft.Win32.RegistryValueKind]::String)
$entry.SetValue('DisplayVersion', '0.3.0', [Microsoft.Win32.RegistryValueKind]::String)
        $entry.SetValue('InstallLocation', $Root, [Microsoft.Win32.RegistryValueKind]::String)
        $entry.SetValue('DisplayIcon', ('"' + $exe + '",0'), [Microsoft.Win32.RegistryValueKind]::String)
        # ExecutionPolicy Bypass applies only to the uninstaller process; no policy is changed.
        # Absolute System32 path: Settings/appwiz must never resolve a bare
        # powershell.exe through its own directory, CWD or PATH.
        $powershell = Join-Path ([Environment]::SystemDirectory) 'WindowsPowerShell\v1.0\powershell.exe'
        $entry.SetValue('UninstallString', ('"' + $powershell + '" -NoProfile -ExecutionPolicy Bypass -File "' + $uninstaller + '"'), [Microsoft.Win32.RegistryValueKind]::String)
        $entry.SetValue('NoModify', 1, [Microsoft.Win32.RegistryValueKind]::DWord)
        $entry.SetValue('NoRepair', 1, [Microsoft.Win32.RegistryValueKind]::DWord)
        $entry.SetValue('InstallerId', $script:RightConvertInstallerId, [Microsoft.Win32.RegistryValueKind]::String)
    } finally {
        $entry.Close()
    }
}

function Remove-RightConvertClassicClass {
    $relative = $script:RightConvertClassicKey
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($relative)
    if ($null -eq $key) { return }
    try {
        if ($key.GetValue('InstallerId', '') -ne $script:RightConvertInstallerId) {
            throw 'Refusing to remove a classic COM CLSID owned by another application.'
        }
    } finally { $key.Close() }
    $parent = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Software\Classes\CLSID', $true)
    if ($null -ne $parent) {
        try {
            $parent.DeleteSubKeyTree($script:RightConvertClassicClsid, $false)
        } finally { $parent.Close() }
    }
}

function Remove-RightConvertRegistration {
    # Only delete keys that our installer explicitly owns.
    Assert-RightConvertQuickOwnership
    foreach ($relative in @($script:RightConvertVerbKey, $script:RightConvertUninstallKey)) {
        $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($relative)
        if ($null -eq $key) { continue }
        try {
            if ($key.GetValue('InstallerId', '') -ne $script:RightConvertInstallerId) {
                throw "Refusing to remove a registry key owned by another installer: $relative"
            }
        } finally {
            $key.Close()
        }
        $separator = $relative.LastIndexOf('\')
        $parent = $relative.Substring(0, $separator)
        $leaf = $relative.Substring($separator + 1)
        $parentKey = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($parent, $true)
        if ($null -ne $parentKey) {
            try {
                $parentKey.DeleteSubKeyTree($leaf, $false)
            } finally {
                $parentKey.Close()
            }
        }
    }
    Remove-RightConvertClassicClass
    Remove-RightConvertQuickCommands
}

function Test-RightConvertDllBusy {
    param([Parameter(Mandatory = $true)][string]$Root)
    $dll = Join-Path (Join-Path $Root 'app') 'RightConvertClassic.dll'
    if (-not (Test-Path -LiteralPath $dll -PathType Leaf)) { return $false }
    Assert-NotReparsePoint -Path $dll
    # Explorer's in-process COM activation normally retains an image-section
    # reference. Probe without renaming/deleting the DLL or touching Explorer.
    $stream = $null
    try {
        $stream = [IO.File]::Open($dll, [IO.FileMode]::Open,
                                 [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
        return $false
    } catch [IO.IOException] {
        return $true
    } catch [UnauthorizedAccessException] {
        return $true
    } finally {
        if ($null -ne $stream) { $stream.Dispose() }
    }
}

function Get-RightConvertDeferredCommand {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [switch]$PurgeUserData,
        [switch]$AllowMissingScript
    )
    $scriptFile = Join-Path $Root 'uninstall.ps1'
    Assert-NotReparsePoint -Path $scriptFile
    if (-not $AllowMissingScript -and -not (Test-Path -LiteralPath $scriptFile -PathType Leaf)) {
        throw "The installed uninstaller is missing: $scriptFile"
    }
    $powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    if (-not (Test-Path -LiteralPath $powershell -PathType Leaf)) {
        throw 'Windows PowerShell is unavailable for deferred cleanup.'
    }
    $command = '"' + $powershell + '" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' +
               $scriptFile + '" -Deferred'
    if ($PurgeUserData) { $command += ' -PurgeUserData' }
    return $command
}

function Register-RightConvertDeferredCleanup {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [switch]$PurgeUserData
    )
    Assert-RightConvertPath -Path $Root
    $command = Get-RightConvertDeferredCommand -Root $Root -PurgeUserData:$PurgeUserData
    $runOnce = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($script:RightConvertRunOnceKey)
    try {
        $old = $runOnce.GetValue($script:RightConvertRunOnceValue, $null)
        if ($null -ne $old -and $old -ne $command) {
            $existingStandard = Get-RightConvertDeferredCommand -Root $Root
            $existingPurge = Get-RightConvertDeferredCommand -Root $Root -PurgeUserData
            if ($old -ne $existingStandard -and $old -ne $existingPurge) {
                throw 'Refusing to replace an unrelated RunOnce startup entry.'
            }
        }
        $runOnce.SetValue($script:RightConvertRunOnceValue, $command,
                          [Microsoft.Win32.RegistryValueKind]::String)
    } finally {
        $runOnce.Close()
    }
}

function Update-RightConvertInstalledUninstaller {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$SourceDirectory
    )
    Assert-RightConvertPath -Path $Root
    if ((Get-RightConvertInstallState -Root $Root) -ne 'installed') { return }
    Assert-ExistingRegistryOwnership
    $source = [IO.Path]::GetFullPath($SourceDirectory).TrimEnd('\')
    if ($source.Equals([IO.Path]::GetFullPath($Root).TrimEnd('\'),
                       [StringComparison]::OrdinalIgnoreCase)) { return }
    # When fixing an already installed older release, the next sign-in must
    # execute THIS version of the cleanup script. Never defer to a stale
    # uninstall.ps1 that does not recognize -Deferred.
    foreach ($name in @('uninstall.ps1', 'common.ps1')) {
        $from = Join-Path $source $name
        $to = Join-Path $Root $name
        Assert-NotReparsePoint -Path $from
        Assert-NotReparsePoint -Path $to
        if (-not (Test-Path -LiteralPath $from -PathType Leaf) -or
            -not (Test-Path -LiteralPath $to -PathType Leaf)) {
            throw "Cannot update the installed uninstall helper: $name"
        }
        Copy-Item -LiteralPath $from -Destination $to -Force
    }
}

function Remove-RightConvertDeferredCleanup {
    param([Parameter(Mandatory = $true)][string]$Root)
    $runOnce = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($script:RightConvertRunOnceKey, $true)
    if ($null -eq $runOnce) { return }
    try {
        $current = $runOnce.GetValue($script:RightConvertRunOnceValue, $null)
        if ($null -eq $current) { return }
        $standard = Get-RightConvertDeferredCommand -Root $Root -AllowMissingScript
        $purge = Get-RightConvertDeferredCommand -Root $Root -PurgeUserData -AllowMissingScript
        if ($current -ne $standard -and $current -ne $purge) {
            throw 'Refusing to remove an unrelated RunOnce entry.'
        }
        $runOnce.DeleteValue($script:RightConvertRunOnceValue, $false)
    } finally {
        $runOnce.Close()
    }
}

function Get-RightConvertBackupCleanupCommand {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [switch]$AllowMissingScript
    )
    $command = Get-RightConvertDeferredCommand -Root $Root -AllowMissingScript:$AllowMissingScript
    # A separate argument is the essential guard: this RunOnce command must
    # never invoke the full uninstaller against a freshly upgraded app.
    $suffix = ' -Deferred'
    if (-not $command.EndsWith($suffix, [StringComparison]::Ordinal)) {
        throw 'Internal cleanup command is missing its expected deferred argument.'
    }
    return ($command.Substring(0, $command.Length - $suffix.Length) +
            ' -CleanupBackupsOnly')
}

function Register-RightConvertBackupCleanup {
    param([Parameter(Mandatory = $true)][string]$Root)
    Assert-RightConvertPath -Path $Root
    if (@(Get-RightConvertOwnedBackups -Root $Root -QuietUnmarked).Count -eq 0) { return }
    $command = Get-RightConvertBackupCleanupCommand -Root $Root
    $key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($script:RightConvertRunOnceKey)
    try {
        $existing = $key.GetValue($script:RightConvertBackupRunOnceValue, $null)
        if ($null -ne $existing -and $existing -ne $command) {
            throw 'Refusing to replace an unrelated backup-cleanup RunOnce entry.'
        }
        $key.SetValue($script:RightConvertBackupRunOnceValue, $command,
                      [Microsoft.Win32.RegistryValueKind]::String)
    } finally { $key.Close() }
}

function Remove-RightConvertBackupCleanupRegistration {
    param([Parameter(Mandatory = $true)][string]$Root)
    Assert-RightConvertPath -Path $Root
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($script:RightConvertRunOnceKey, $true)
    if ($null -eq $key) { return }
    try {
        $existing = $key.GetValue($script:RightConvertBackupRunOnceValue, $null)
        if ($null -eq $existing) { return }
        if ($existing -ne (Get-RightConvertBackupCleanupCommand -Root $Root -AllowMissingScript)) {
            # A different installation or a concurrent test may own this
            # RunOnce value. Leave it untouched; uninstalling the current
            # app must not require deleting someone else's startup command.
            return
        }
        $key.DeleteValue($script:RightConvertBackupRunOnceValue, $false)
    } finally { $key.Close() }
}

function Invoke-RightConvertBackupCleanup {
    param([Parameter(Mandatory = $true)][string]$Root)
    Assert-RightConvertPath -Path $Root
    $remaining = @()
    foreach ($backup in @(Get-RightConvertOwnedBackups -Root $Root -QuietUnmarked)) {
        try {
            Remove-RightConvertMarkedBackup -Root $Root -Backup $backup
        } catch {
            Write-Warning ("RightConvert backup remains locked or unavailable: " +
                           $backup + " (" + $_.Exception.Message + ")")
            $remaining += $backup
        }
    }
    if ($remaining.Count -eq 0) {
        Remove-RightConvertBackupCleanupRegistration -Root $Root
        return 'complete'
    }
    Register-RightConvertBackupCleanup -Root $Root
    return 'pending'
}

function Invoke-RightConvertUninstallation {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [switch]$PurgeUserData,
        [switch]$Deferred
    )
    Assert-RightConvertPath -Path $Root
    $state = Get-RightConvertInstallState -Root $Root
    Assert-ExistingRegistryOwnership
    $appDir = Join-Path $Root 'app'
    $logsDir = Join-Path $Root 'logs'
    $settings = Join-Path $Root 'settings.json'
    $modernMarker = Join-Path $Root 'modern-package-fullname.txt'

    if ($state -eq 'installed') {
        # Validate all owned paths before touching registry or scheduling work.
        if (Test-Path -LiteralPath $appDir) {
            Assert-RightConvertOwnedTree -Root $Root -Path $appDir
        }
        foreach ($name in @('common.ps1', 'uninstall.ps1', 'install-marker.txt',
                            'modern-package-fullname.txt')) {
            Assert-NotReparsePoint -Path (Join-Path $Root $name)
        }
    }
    if ($PurgeUserData) {
        if (Test-Path -LiteralPath $settings) {
            Assert-NotReparsePoint -Path $settings
            if (-not (Test-Path -LiteralPath $settings -PathType Leaf)) {
                throw "Expected a settings file: $settings"
            }
        }
        if (Test-Path -LiteralPath $logsDir) {
            if (-not (Test-Path -LiteralPath $logsDir -PathType Container)) {
                throw "Expected a log directory: $logsDir"
            }
            Assert-TreeHasNoReparsePoints -Path $logsDir
        }
    }

    if ($state -eq 'installed') {
        # Register recovery *before* unregistering COM or deleting anything.
        # This also repairs a previous uninstall that removed the registry
        # entry while leaving a DLL loaded in Explorer.
        Register-RightConvertDeferredCleanup -Root $Root -PurgeUserData:$PurgeUserData
        if (Test-Path -LiteralPath $modernMarker -PathType Leaf) {
            $fullName = (Get-Content -LiteralPath $modernMarker -Raw).Trim()
            if ($fullName) {
                $package = Get-AppxPackage -Name 'RightConvert.ContextMenu' |
                    Where-Object { $_.PackageFullName -eq $fullName } |
                    Select-Object -First 1
                if ($null -ne $package) {
                    Remove-AppxPackage -Package $fullName -ErrorAction Stop
                }
            }
        }
        Remove-RightConvertRegistration
        if (Test-RightConvertDllBusy -Root $Root) {
            Write-Warning ('RightConvertClassic.dll is still in use by Windows Explorer. ' +
                           'Files will be removed automatically at your next sign-in. ' +
                           'You can sign out when convenient; Explorer was not restarted.')
            return 'pending'
        }
        if (Test-Path -LiteralPath $appDir) {
            try {
                Remove-RightConvertOwnedTree -Root $Root -Path $appDir
            } catch {
                # Other modules may also be open. A verified RunOnce command
                # remains available; never force-stop Explorer or other apps.
                Write-Warning ("RightConvert files are still in use or cannot be removed: " +
                               $_.Exception.Message +
                               ". Cleanup is scheduled for the next sign-in.")
                return 'pending'
            }
        }
    } else {
        # Idempotent: registry can already be absent after a partial uninstall.
        Remove-RightConvertRegistration
    }

    # Backups from a previous upgrade belong to the installation but can
    # contain another Explorer-loaded COM DLL. Do not remove the uninstall
    # helper while owned backups still require a deferred cleanup.
    if (@(Get-RightConvertOwnedBackups -Root $Root -QuietUnmarked).Count -gt 0) {
        $backupStatus = Invoke-RightConvertBackupCleanup -Root $Root
        if ($backupStatus -eq 'pending') {
            Register-RightConvertDeferredCleanup -Root $Root -PurgeUserData:$PurgeUserData
            Write-Warning 'Previous-version backup still locked; full removal is scheduled for the next sign-in.'
            return 'pending'
        }
    }

    if ($PurgeUserData) {
        if (Test-Path -LiteralPath $settings -PathType Leaf) {
            Remove-Item -LiteralPath $settings -Force
        }
        if (Test-Path -LiteralPath $logsDir -PathType Container) {
            Assert-TreeHasNoReparsePoints -Path $logsDir
            Remove-Item -LiteralPath $logsDir -Recurse -Force
        }
    }
    # Once the actual application DLLs are gone, disable the startup helper.
    Remove-RightConvertBackupCleanupRegistration -Root $Root
    Remove-RightConvertDeferredCleanup -Root $Root
    if ($state -eq 'installed') {
        foreach ($name in @('common.ps1', 'uninstall.ps1', 'install-marker.txt',
                            'modern-package-fullname.txt')) {
            $path = Join-Path $Root $name
            if (Test-Path -LiteralPath $path -PathType Leaf) {
                Assert-NotReparsePoint -Path $path
                Remove-Item -LiteralPath $path -Force
            }
        }
    }
    if (Test-Path -LiteralPath $Root -PathType Container) {
        if (@(Get-ChildItem -LiteralPath $Root -Force).Count -eq 0) {
            Remove-Item -LiteralPath $Root
        }
    }
    if ($state -ne 'installed') {
        Write-Host 'RightConvert was already uninstalled.'
    } else {
        Write-Host 'RightConvert uninstalled for the current user.'
    }
    if ($PurgeUserData) {
        Write-Host 'User settings and logs removed.'
    } else {
        Write-Host 'User settings and logs preserved.'
    }
    return 'complete'
}
