<#
.SYNOPSIS
Installs RightConvert for the current user (no administrator privileges needed).
.EXAMPLE
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\installer\install.ps1
.EXAMPLE
.\installer\install.ps1 -PackageDir 'D:\Build output\RightConvert'
#>
[CmdletBinding()]
param(
    [string]$PackageDir,
    [string]$TestFixtureRoot,
    [switch]$EnableExperimentalQuickMenu,
    [switch]$DisableQuickMenu
)
$ErrorActionPreference = 'Stop'
# Resolve the default here, not in param(): Windows PowerShell 5.1 leaves
# $PSScriptRoot empty while evaluating param() defaults under -File.
if (-not $PackageDir) {
    $PackageDir = Join-Path (Split-Path -Parent $PSScriptRoot) 'dist\RightConvert'
}
. (Join-Path $PSScriptRoot 'common.ps1')

# Restricted integration-test sandbox. Test roots and HKCU keys are uniquely
# named under installer/out; production installations never use this option.
if ($TestFixtureRoot) {
    $testBase = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'out')).TrimEnd('\')
    $testPath = [IO.Path]::GetFullPath($TestFixtureRoot).TrimEnd('\')
    $leaf = [IO.Path]::GetFileName($testPath)
    if (-not ([IO.Path]::GetDirectoryName($testPath)).Equals(
              $testBase, [StringComparison]::OrdinalIgnoreCase) -or
        $leaf -notmatch '^install-regression-([0-9a-f]{32})$') {
        throw 'The test fixture must be a direct installer/out/install-regression-GUID child.'
    }
    Assert-NotReparsePoint -Path $testBase
    Assert-NotReparsePoint -Path $testPath
    $testGuid = $Matches[1]
    function Get-RightConvertRoot { return $testPath }
    $script:RightConvertVerbKey = 'Software\Classes\*\shell\RightConvertTest-' + $testGuid
    $script:RightConvertUninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\RightConvertTest-' + $testGuid
    $script:RightConvertClassicClsid = '{' + ([guid]$testGuid).ToString().ToUpperInvariant() + '}'
    $script:RightConvertClassicKey = 'Software\Classes\CLSID\' + $script:RightConvertClassicClsid
    $script:RightConvertQuickVerbKey = 'Software\Classes\*\shell\RightConvertQuickTest-' + $testGuid
    $script:RightConvertQuickHandlerKey = 'Software\Classes\*\shellex\ContextMenuHandlers\RightConvertQuickTest-' + $testGuid
    $script:RightConvertQuickCascadeClsid = '{' + ([guid]::NewGuid()).ToString().ToUpperInvariant() + '}'
    # Never allow a test fixture to inspect/delete production quick-target
    # CLSIDs. These historic classes are owned independently by the real app.
    $script:RightConvertQuickTargets = [ordered]@{}
    foreach ($target in @('jpg', 'png', 'webp', 'mp3', 'wav', 'flac', 'mp4',
                          'webm', 'pdf', 'tiff', 'pngzip')) {
        $script:RightConvertQuickTargets[$target] = '{' +
            ([guid]::NewGuid()).ToString().ToUpperInvariant() + '}'
    }
    $script:RightConvertRunOnceValue = '!RightConvertTest-Pending-' + $testGuid
    $script:RightConvertBackupRunOnceValue = '!RightConvertTest-Backup-' + $testGuid
}

$source = (Resolve-Path -LiteralPath $PackageDir -ErrorAction Stop).ProviderPath
if (-not (Test-Path -LiteralPath $source -PathType Container)) {
    throw "PyInstaller one-dir output not found: $source"
}
Assert-TreeHasNoReparsePoints -Path $source
if (-not (Test-Path -LiteralPath (Join-Path $source 'RightConvert.exe') -PathType Leaf)) {
    throw "Expected RightConvert.exe inside $source"
}

$root = Get-RightConvertRoot
Assert-RightConvertPath -Path $root
Assert-ExistingRegistryOwnership
$markerPath = Join-Path $root 'install-marker.txt'
$appDir = Join-Path $root 'app'
$installState = Get-RightConvertInstallState -Root $root
foreach ($staleStage in @(Get-RightConvertRecoverableStages -Root $root)) {
    # Recover only GUID stages carrying our installer marker. An active installer
    # keeps the marker file locked and aborts this cleanup safely.
    Remove-RightConvertOwnedTree -Root $root -Path $staleStage
}
New-Item -ItemType Directory -Path $root -Force | Out-Null
foreach ($name in @('common.ps1', 'uninstall.ps1', 'install-marker.txt')) {
    Assert-NotReparsePoint -Path (Join-Path $root $name)
}

$stage = Join-Path $root ('.stage-' + [guid]::NewGuid().ToString('N'))
$backup = Join-Path $root ('.backup-' + [guid]::NewGuid().ToString('N'))
$backedUp = $false
$backupMarked = $false
$activated = $false
$stageLease = $null
try {
    New-Item -ItemType Directory -Path $stage | Out-Null
    $stageOwner = Join-Path $stage $script:RightConvertStageMarker
    $stageLease = [IO.File]::Open($stageOwner, [IO.FileMode]::CreateNew,
                                  [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    $ownershipBytes = [Text.Encoding]::UTF8.GetBytes($script:RightConvertInstallerId)
    $stageLease.Write($ownershipBytes, 0, $ownershipBytes.Length)
    $stageLease.Flush()
    # Copy all of the PyInstaller one-dir folder contents, preserving Unicode names.
    Get-ChildItem -LiteralPath $source -Force | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $stage -Recurse -Force
    }
    if (-not (Test-Path -LiteralPath (Join-Path $stage 'RightConvert.exe') -PathType Leaf)) {
        throw 'The staged package is missing RightConvert.exe.'
    }
    if (Test-Path -LiteralPath $appDir) {
        Assert-RightConvertOwnedTree -Root $root -Path $appDir
        Move-Item -LiteralPath $appDir -Destination $backup
        $backedUp = $true
        # Keep ownership evidence outside the backup. A recursive delete may
        # fail partway through on Explorer's loaded native DLL; the independent
        # sidecar survives until the entire backup is safely removed.
        Write-RightConvertBackupMarker -Root $root -Backup $backup
        $backupMarked = $true
    }
    $stageLease.Dispose()
    $stageLease = $null
    Move-Item -LiteralPath $stage -Destination $appDir
    $activated = $true
    Remove-Item -LiteralPath (Join-Path $appDir $script:RightConvertStageMarker) -Force
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'uninstall.ps1') -Destination (Join-Path $root 'uninstall.ps1') -Force
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'common.ps1') -Destination (Join-Path $root 'common.ps1') -Force
    Register-RightConvert -Root $root -EnableExperimentalQuickMenu:$EnableExperimentalQuickMenu -DisableQuickMenu:$DisableQuickMenu
    [IO.File]::WriteAllText($markerPath, $script:RightConvertInstallerId, [Text.Encoding]::UTF8)
    # The new app is now registered. Only now cancel a previous uninstall:
    # leaving -Deferred behind could uninstall the freshly upgraded version
    # at the next Windows sign-in.
    if ($backedUp) { Register-RightConvertBackupCleanup -Root $root }
    Remove-RightConvertDeferredCleanup -Root $root
} catch {
    $originalError = $_
    if ($null -ne $stageLease) {
        $stageLease.Dispose()
        $stageLease = $null
    }
    if ($activated -and (Test-Path -LiteralPath $appDir)) {
        Remove-RightConvertOwnedTree -Root $root -Path $appDir
    }
    if ($backedUp -and (Test-Path -LiteralPath $backup)) {
        Move-Item -LiteralPath $backup -Destination $appDir
    }
    if ($backupMarked) {
        $owner = Get-RightConvertBackupMarker -Root $root -Backup $backup
        Assert-NotReparsePoint -Path $owner
        if (Test-Path -LiteralPath $owner -PathType Leaf) {
            $expected = $script:RightConvertInstallerId + '|' + [IO.Path]::GetFileName($backup)
            if (([IO.File]::ReadAllText($owner)) -eq $expected) {
                Remove-Item -LiteralPath $owner -Force
            }
        }
    }
    # Preserve any cleanup registration for backups that predated this failed
    # upgrade. A failed transaction must not orphan an older marked backup.
    if (@(Get-RightConvertOwnedBackups -Root $root -QuietUnmarked).Count -gt 0) {
        Register-RightConvertBackupCleanup -Root $root
    } else {
        Remove-RightConvertBackupCleanupRegistration -Root $root
    }
    if (Test-Path -LiteralPath $stage) {
        Remove-RightConvertOwnedTree -Root $root -Path $stage
    }
    if ($installState -ne 'installed') {
        # Roll back registration and installer files from a first install.
        Remove-RightConvertRegistration
        foreach ($name in @('common.ps1', 'uninstall.ps1', 'install-marker.txt')) {
            $file = Join-Path $root $name
            if (Test-Path -LiteralPath $file -PathType Leaf) {
                Assert-NotReparsePoint -Path $file
                Remove-Item -LiteralPath $file -Force
            }
        }
    }
    throw $originalError
}

try {
if (@(Get-RightConvertOwnedBackups -Root $root).Count -gt 0) {
        # Post-commit cleanup is best-effort. A loaded old DLL must never turn
        # a successful upgrade into a failing command.
        $backupStatus = Invoke-RightConvertBackupCleanup -Root $root
        if ($backupStatus -eq 'pending') {
            Write-Warning 'An old RightConvert DLL is still in use. Its marked backup will be cleaned at the next sign-in.'
        }
    }
} catch {
    # All critical work already committed and the dedicated backup-only RunOnce
    # was registered before the old uninstall RunOnce was canceled.
    Write-Warning ('The upgraded RightConvert is installed; backup cleanup is pending: ' +
                   $_.Exception.Message)
}
Write-Host "RightConvert installed: $appDir"
Write-Host 'Explorer classic menu: Convert with RightConvert'
Write-Host 'Windows 11: see installer/README.md to enable the optional modern menu.'
