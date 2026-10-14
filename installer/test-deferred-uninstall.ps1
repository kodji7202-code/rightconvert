# Isolated, end-to-end uninstall regression with real loaded COM DLL.
# Uses a unique HKCU verb/CLSID/RunOnce name and new installer/out fixture.
# Never terminates Explorer or changes the user's installed RightConvert.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')

$id = [guid]::NewGuid().ToString('N')
$testParent = Join-Path $PSScriptRoot 'out'
New-Item -ItemType Directory -Path $testParent -Force | Out-Null
$fixture = Join-Path $testParent ('deferred-' + $id)
$app = Join-Path $fixture 'app'
$sourceDll = Join-Path (Split-Path -Parent $PSScriptRoot) 'shell\classic\out\x64\Release\RightConvertClassic.dll'
if (-not (Test-Path -LiteralPath $sourceDll -PathType Leaf)) {
    throw "Build the classic COM DLL before running this regression: $sourceDll"
}

# The fixture never uses the real HKCU RightConvert registry values.
$script:RightConvertVerbKey = 'Software\Classes\*\shell\RightConvertTest-' + $id
$script:RightConvertUninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\RightConvertTest-' + $id
$script:RightConvertClassicClsid = '{' + [guid]::NewGuid().ToString().ToUpperInvariant() + '}'
$script:RightConvertClassicKey = 'Software\Classes\CLSID\' + $script:RightConvertClassicClsid
$script:RightConvertRunOnceValue = '!RightConvertTest-' + $id
$script:RightConvertBackupRunOnceValue = '!RightConvertTest-Backup-' + $id
function Get-RightConvertRoot { return $fixture }

# Every registry value the fixture may write or remove is uniquely named.
# In particular, never use the live !RightConvert.BackupCleanup value.
if ($script:RightConvertRunOnceValue -notlike ('*' + $id) -or
    $script:RightConvertBackupRunOnceValue -notlike ('*' + $id) -or
    $script:RightConvertBackupRunOnceValue -eq '!RightConvert.BackupCleanup') {
    throw 'Deferred-uninstall test registry isolation is not configured.'
}

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class RightConvertTestDll {
    [DllImport("kernel32.dll", EntryPoint = "LoadLibraryW", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern IntPtr LoadLibrary(string filename);
    [DllImport("kernel32.dll", EntryPoint = "FreeLibrary", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool FreeLibrary(IntPtr handle);
}
'@

$loaded = [IntPtr]::Zero
$foreignBackupCommand = 'C:\Foreign Product\uninstall.ps1 -CleanupBackupsOnly'
try {
    New-Item -ItemType Directory -Path $app -Force | Out-Null
    [IO.File]::WriteAllText((Join-Path $app 'RightConvert.exe'), 'test only')
    Copy-Item -LiteralPath $sourceDll -Destination (Join-Path $app 'RightConvertClassic.dll')
    [IO.File]::WriteAllText((Join-Path $fixture 'common.ps1'), 'legacy helper')
    [IO.File]::WriteAllText((Join-Path $fixture 'uninstall.ps1'), 'legacy uninstaller')
    [IO.File]::WriteAllText((Join-Path $fixture 'install-marker.txt'), $script:RightConvertInstallerId)
    [IO.File]::WriteAllText((Join-Path $fixture 'settings.json'), '{"same_folder":true}')
    New-Item -ItemType Directory -Path (Join-Path $fixture 'logs') | Out-Null
    [IO.File]::WriteAllText((Join-Path $fixture 'logs\technical.log'), 'retain')
    Register-RightConvert -Root $fixture
    Update-RightConvertInstalledUninstaller -Root $fixture -SourceDirectory $PSScriptRoot
    if ((Get-FileHash -LiteralPath (Join-Path $fixture 'uninstall.ps1')).Hash -ne
        (Get-FileHash -LiteralPath (Join-Path $PSScriptRoot 'uninstall.ps1')).Hash) {
        throw 'Old installed uninstaller was not updated for deferred execution.'
    }
    # Simulate the collision observed on the real profile, but exclusively
    # within this fixture's unique RunOnce value. The command belongs to a
    # different root and must survive both uninstall cycles.
    $runOnce = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($script:RightConvertRunOnceKey)
    try {
        if ($null -ne $runOnce.GetValue($script:RightConvertBackupRunOnceValue, $null)) {
            throw 'Unique backup RunOnce fixture value unexpectedly exists.'
        }
        $runOnce.SetValue($script:RightConvertBackupRunOnceValue, $foreignBackupCommand,
                          [Microsoft.Win32.RegistryValueKind]::String)
    } finally { $runOnce.Close() }

    $dll = Join-Path $app 'RightConvertClassic.dll'
    $loaded = [RightConvertTestDll]::LoadLibrary($dll)
    if ($loaded -eq [IntPtr]::Zero) { throw 'Could not load the native test DLL.' }
    if (-not (Test-RightConvertDllBusy -Root $fixture)) {
        throw 'Loaded COM DLL was not recognized as locked.'
    }
    $result = Invoke-RightConvertUninstallation -Root $fixture
    if ($result -ne 'pending') { throw "Locked DLL uninstall must defer; returned $result" }
    if (-not (Test-Path -LiteralPath $dll -PathType Leaf)) {
        throw 'Pending cleanup deleted the loaded DLL before it was released.'
    }
    if (-not (Test-Path -LiteralPath (Join-Path $fixture 'install-marker.txt') -PathType Leaf)) {
        throw 'Pending cleanup removed the marker needed for recovery.'
    }
    foreach ($registryPath in @($script:RightConvertVerbKey, $script:RightConvertClassicKey,
                                $script:RightConvertUninstallKey)) {
        $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($registryPath)
        if ($null -ne $key) { $key.Close(); throw "Unregister left a key behind: $registryPath" }
    }
    $runOnce = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($script:RightConvertRunOnceKey)
    try {
        $pending = $runOnce.GetValue($script:RightConvertRunOnceValue, $null)
        if ($pending -notmatch ' -Deferred$') { throw 'RunOnce cleanup command is missing or malformed.' }
    } finally { $runOnce.Close() }

    if (-not [RightConvertTestDll]::FreeLibrary($loaded)) {
        throw 'Unable to unload the native test DLL.'
    }
    $loaded = [IntPtr]::Zero
    # Simulate the next Windows sign-in by invoking the already registered
    # cleanup after unloading our test DLL. No actual logoff/reboot is needed.
    $result = Invoke-RightConvertUninstallation -Root $fixture -Deferred
    if ($result -ne 'complete') { throw 'Deferred cleanup did not complete.' }
    if (Test-Path -LiteralPath $app) { throw 'Deferred cleanup left the app folder.' }
    if (Test-Path -LiteralPath (Join-Path $fixture 'install-marker.txt')) {
        throw 'Deferred cleanup left the installation marker.'
    }
    foreach ($dataFile in @('settings.json', 'logs\technical.log')) {
        if (-not (Test-Path -LiteralPath (Join-Path $fixture $dataFile))) {
            throw "Default uninstall must preserve $dataFile"
        }
    }
    $runOnce = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($script:RightConvertRunOnceKey)
    try {
        if ($null -ne $runOnce.GetValue($script:RightConvertRunOnceValue, $null)) {
            throw 'Successful deferred cleanup left RunOnce persistence.'
        }
    } finally { $runOnce.Close() }

    # Separate second cycle verifies explicit data purge is delayed while the
    # binary is loaded, then fully executed once that DLL is released.
    New-Item -ItemType Directory -Path $app -Force | Out-Null
    [IO.File]::WriteAllText((Join-Path $app 'RightConvert.exe'), 'test only')
    Copy-Item -LiteralPath $sourceDll -Destination (Join-Path $app 'RightConvertClassic.dll')
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'uninstall.ps1') -Destination (Join-Path $fixture 'uninstall.ps1')
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'common.ps1') -Destination (Join-Path $fixture 'common.ps1')
    [IO.File]::WriteAllText((Join-Path $fixture 'install-marker.txt'), $script:RightConvertInstallerId)
    Register-RightConvert -Root $fixture
    $loaded = [RightConvertTestDll]::LoadLibrary((Join-Path $app 'RightConvertClassic.dll'))
    if ($loaded -eq [IntPtr]::Zero) { throw 'Could not reload the DLL for the purge test.' }
    $result = Invoke-RightConvertUninstallation -Root $fixture -PurgeUserData
    if ($result -ne 'pending') { throw 'Locked DLL purge must defer.' }
    if (-not (Test-Path -LiteralPath (Join-Path $fixture 'settings.json'))) {
        throw 'Purge removed preferences before the DLL was released.'
    }
    $runOnce = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($script:RightConvertRunOnceKey)
    try {
        if ($runOnce.GetValue($script:RightConvertRunOnceValue, '') -notmatch ' -Deferred -PurgeUserData$') {
            throw 'Pending full removal did not preserve the user purge option.'
        }
    } finally { $runOnce.Close() }
    if (-not [RightConvertTestDll]::FreeLibrary($loaded)) {
        throw 'Cannot release the DLL for the purge test.'
    }
    $loaded = [IntPtr]::Zero
    $result = Invoke-RightConvertUninstallation -Root $fixture -PurgeUserData -Deferred
    if ($result -ne 'complete') { throw 'Deferred purge did not finish.' }
    if (Test-Path -LiteralPath (Join-Path $fixture 'settings.json')) {
        throw 'Full removal left settings behind.'
    }
    if (Test-Path -LiteralPath (Join-Path $fixture 'logs')) {
        throw 'Full removal left technical logs behind.'
    }
    $runOnce = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($script:RightConvertRunOnceKey)
    try {
        if ($runOnce.GetValue($script:RightConvertBackupRunOnceValue, $null) -ne
            $foreignBackupCommand) {
            throw 'Deferred uninstall changed an unrelated backup RunOnce command.'
        }
    } finally { $runOnce.Close() }
    Write-Host 'PASS: loaded native COM DLL defers, HKCU integration removed, later cleanup completes, user data preserved.'
} finally {
    if ($loaded -ne [IntPtr]::Zero) {
        $null = [RightConvertTestDll]::FreeLibrary($loaded)
    }
    # Only remove our unique RunOnce value; do not touch the real user entry.
    $runOnce = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($script:RightConvertRunOnceKey, $true)
    if ($null -ne $runOnce) {
        try {
            $runOnce.DeleteValue($script:RightConvertRunOnceValue, $false)
            # Remove only the sentinel created by this test. Never remove a
            # different command even from the unique fixture's registry name.
            if ($runOnce.GetValue($script:RightConvertBackupRunOnceValue, $null) -eq
                $foreignBackupCommand) {
                $runOnce.DeleteValue($script:RightConvertBackupRunOnceValue, $false)
            }
        }
        finally { $runOnce.Close() }
    }
    Remove-RightConvertRegistration
    if (Test-Path -LiteralPath $fixture) {
        Assert-TreeHasNoReparsePoints -Path $fixture
        Remove-Item -LiteralPath $fixture -Recurse -Force
    }
}
