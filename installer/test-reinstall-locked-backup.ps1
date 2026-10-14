# Exercises install.ps1 against an isolated filesystem and unique HKCU values.
# A real loaded RightConvertClassic.dll reproduces Explorer's pinned COM DLL.
# Never changes the normal %LOCALAPPDATA%\RightConvert installation.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')

$id = [guid]::NewGuid().ToString('N')
$base = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'out'))
New-Item -ItemType Directory -Path $base -Force | Out-Null
$root = Join-Path $base ('install-regression-' + $id)
$source = Join-Path $base ('payload-regression-' + $id)
$app = Join-Path $root 'app'
$nativeDll = Join-Path (Split-Path -Parent $PSScriptRoot) 'shell\classic\out\x64\Release\RightConvertClassic.dll'
if (-not (Test-Path -LiteralPath $nativeDll -PathType Leaf)) {
    throw "Build the native classic COM DLL first: $nativeDll"
}

$script:RightConvertVerbKey = 'Software\Classes\*\shell\RightConvertTest-' + $id
$script:RightConvertUninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\RightConvertTest-' + $id
$script:RightConvertClassicClsid = '{' + ([guid]$id).ToString().ToUpperInvariant() + '}'
$script:RightConvertClassicKey = 'Software\Classes\CLSID\' + $script:RightConvertClassicClsid
$script:RightConvertRunOnceValue = '!RightConvertTest-Pending-' + $id
$script:RightConvertBackupRunOnceValue = '!RightConvertTest-Backup-' + $id
function Get-RightConvertRoot { return $root }

if ($script:RightConvertRunOnceValue -notlike ('*' + $id) -or
    $script:RightConvertBackupRunOnceValue -notlike ('*' + $id) -or
    $script:RightConvertBackupRunOnceValue -eq '!RightConvert.BackupCleanup') {
    throw 'Reinstall test registry isolation is not configured.'
}

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class RightConvertUpgradeDll {
    [DllImport("kernel32.dll", EntryPoint = "LoadLibraryW", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern IntPtr LoadLibrary(string filename);
    [DllImport("kernel32.dll", EntryPoint = "FreeLibrary", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool FreeLibrary(IntPtr handle);
}
'@

$dllHandle = [IntPtr]::Zero
try {
    New-Item -Path $app -ItemType Directory -Force | Out-Null
    New-Item -Path $source -ItemType Directory -Force | Out-Null
    [IO.File]::WriteAllText((Join-Path $app 'RightConvert.exe'), 'old version')
    Copy-Item -LiteralPath $nativeDll -Destination (Join-Path $app 'RightConvertClassic.dll')
    [IO.File]::WriteAllText((Join-Path $source 'RightConvert.exe'), 'new version')
    Copy-Item -LiteralPath $nativeDll -Destination (Join-Path $source 'RightConvertClassic.dll')
    [IO.File]::WriteAllText((Join-Path $root 'install-marker.txt'), $script:RightConvertInstallerId)
    [IO.File]::WriteAllText((Join-Path $root 'settings.json'), '{"retained":true}')
    New-Item -Path (Join-Path $root 'logs') -ItemType Directory | Out-Null
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'common.ps1') -Destination (Join-Path $root 'common.ps1')
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'uninstall.ps1') -Destination (Join-Path $root 'uninstall.ps1')
    Register-RightConvert -Root $root
    Register-RightConvertDeferredCleanup -Root $root

    # A real previous release left an unmarked backup before marker support.
    # The upgraded installer must leave such a folder alone, without allowing
    # it to block a current installation or authorizing its deletion.
    $legacyBackup = Join-Path $root ('.backup-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $legacyBackup | Out-Null
    [IO.File]::WriteAllText((Join-Path $legacyBackup 'legacy-keep.txt'), 'not marked')

    $dllHandle = [RightConvertUpgradeDll]::LoadLibrary((Join-Path $app 'RightConvertClassic.dll'))
    if ($dllHandle -eq [IntPtr]::Zero) { throw 'The fixture DLL could not be loaded.' }
    if (-not (Test-RightConvertDllBusy -Root $root)) { throw 'The old DLL was not locked.' }

    # Execute the ACTUAL install.ps1 code; -TestFixtureRoot restricts both
    # filesystem changes and registry keys to the unique test fixture.
    & (Join-Path $PSScriptRoot 'install.ps1') -PackageDir $source -TestFixtureRoot $root
    if (([IO.File]::ReadAllText((Join-Path $app 'RightConvert.exe'))) -ne 'new version') {
        throw 'The new app did not replace the old executable.'
    }
    $oldBackups = @(Get-RightConvertOwnedBackups -Root $root)
    if ($oldBackups.Count -ne 1) { throw 'The locked former app backup was not marked and retained.' }
    $oldBackup = $oldBackups[0]
    if (([IO.File]::ReadAllText((Join-Path $oldBackup 'RightConvert.exe'))) -ne 'old version') {
        throw 'The marked backup is not the old app.'
    }
    $run = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($script:RightConvertRunOnceKey)
    try {
        if ($null -ne $run.GetValue($script:RightConvertRunOnceValue, $null)) {
            throw 'A stale RunOnce uninstall would erase the newly installed app.'
        }
        $backupCommand = $run.GetValue($script:RightConvertBackupRunOnceValue, $null)
        if ($backupCommand -ne (Get-RightConvertBackupCleanupCommand -Root $root)) {
            throw 'The backup-only deferred cleanup command is incorrect.'
        }
    } finally { $run.Close() }
    if (-not (Test-Path -LiteralPath (Join-Path $root 'settings.json'))) {
        throw 'The user preferences were lost during upgrade.'
    }
    if (-not (Test-Path -LiteralPath (Join-Path $legacyBackup 'legacy-keep.txt'))) {
        throw 'The old unmarked backup was deleted without ownership proof.'
    }

    if (-not [RightConvertUpgradeDll]::FreeLibrary($dllHandle)) {
        throw 'Unable to unload the test DLL.'
    }
    $dllHandle = [IntPtr]::Zero
    if ((Invoke-RightConvertBackupCleanup -Root $root) -ne 'complete') {
        throw 'Backup cleanup failed after the old DLL was released.'
    }
    if (Test-Path -LiteralPath $oldBackup) { throw 'The old backup was not removed.' }
    if (-not (Test-Path -LiteralPath (Join-Path $legacyBackup 'legacy-keep.txt'))) {
        throw 'Backup cleanup improperly removed the unmarked legacy directory.'
    }
    if (-not (Test-Path -LiteralPath (Join-Path $app 'RightConvertClassic.dll'))) {
        throw 'Backup cleanup deleted the active application.'
    }
    if (([IO.File]::ReadAllText((Join-Path $app 'RightConvert.exe'))) -ne 'new version') {
        throw 'Backup cleanup corrupted the new executable.'
    }
    $run = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($script:RightConvertRunOnceKey)
    try {
        if ($null -ne $run.GetValue($script:RightConvertBackupRunOnceValue, $null)) {
            throw 'Backup RunOnce entry remained after successful cleanup.'
        }
    } finally { $run.Close() }

    $forgedMarker = Get-RightConvertBackupMarker -Root $root -Backup $legacyBackup
    [IO.File]::WriteAllText($forgedMarker, 'foreign-app|not-owned')
    $rejectedForeign = $false
    try {
        $null = @(Get-RightConvertOwnedBackups -Root $root)
    } catch {
        $rejectedForeign = $true
    }
    if (-not $rejectedForeign) {
        throw 'Forged backup ownership was not rejected.'
    }
    if (-not (Test-Path -LiteralPath (Join-Path $legacyBackup 'legacy-keep.txt'))) {
        throw 'Foreign backup was deleted despite invalid ownership marker.'
    }
    Remove-Item -LiteralPath $forgedMarker -Force

    Write-Host 'PASS: installer exits 0 with locked old DLL; new app survives; stale uninstall canceled; backup-only cleanup succeeds after unload.'
} finally {
    if ($dllHandle -ne [IntPtr]::Zero) {
        $null = [RightConvertUpgradeDll]::FreeLibrary($dllHandle)
    }
    Remove-RightConvertDeferredCleanup -Root $root
    Remove-RightConvertBackupCleanupRegistration -Root $root
    Remove-RightConvertRegistration
    foreach ($target in @($root, $source)) {
        if (Test-Path -LiteralPath $target) {
            Assert-TreeHasNoReparsePoints -Path $target
            Remove-Item -LiteralPath $target -Recurse -Force
        }
    }
}
