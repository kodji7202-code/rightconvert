<#
.SYNOPSIS
Safely removes RightConvert's per-user Explorer integration and application.
.DESCRIPTION
Windows Explorer can keep the RightConvertClassic.dll COM server mapped after
its menu has been used. In that case, defer file cleanup until the next user
sign-in through a verified HKCU RunOnce entry. Do not kill Explorer.
.PARAMETER PurgeUserData
Also remove settings.json and logs after the installed application is removed.
.PARAMETER Deferred
Internal argument supplied by the per-user RunOnce cleanup entry.
.PARAMETER CleanupBackupsOnly
Internal, separately registered RunOnce operation. Deletes verified old backup
directories only; never unregisters or deletes the current application.
#>
[CmdletBinding()]
param(
    [switch]$PurgeUserData,
    [switch]$Deferred,
    [switch]$CleanupBackupsOnly
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')

$root = Get-RightConvertRoot
if ($CleanupBackupsOnly) {
    $result = Invoke-RightConvertBackupCleanup -Root $root
    if ($result -eq 'pending') {
        Write-Warning 'A RightConvert backup is still locked. It remains marked for later cleanup.'
    }
    return
}
Update-RightConvertInstalledUninstaller -Root $root -SourceDirectory $PSScriptRoot
$result = Invoke-RightConvertUninstallation -Root $root -PurgeUserData:$PurgeUserData -Deferred:$Deferred
if ($result -eq 'pending') {
    Write-Host 'RightConvert removal is pending until the next Windows sign-in.'
}
