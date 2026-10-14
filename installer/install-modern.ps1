<#
.SYNOPSIS
Adds the signed sparse MSIX registration for Windows 11's modern context menu.
.EXAMPLE
.\installer\install-modern.ps1 -SignedMsix .\installer\out\RightConvert.ContextMenu.x64.msix
#>
[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$SignedMsix)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')

if ([Environment]::OSVersion.Version.Build -lt 22000) {
    throw 'Windows 11 build 22000 or newer is required for the modern context menu.'
}
$root = Get-RightConvertRoot
Assert-RightConvertPath -Path $root
$marker = Join-Path $root 'install-marker.txt'
if (-not (Test-Path -LiteralPath $marker -PathType Leaf) -or
    ((Get-Content -LiteralPath $marker -Raw).Trim() -ne $script:RightConvertInstallerId)) {
    throw 'Install RightConvert via installer/install.ps1 before registering the modern context menu.'
}
$app = Join-Path $root 'app'
Assert-NotReparsePoint -Path $app
foreach ($name in @('RightConvert.exe', 'RightConvertExplorer.dll',
                    'Assets\StoreLogo.png', 'Assets\Square44x44Logo.png',
                    'Assets\Square150x150Logo.png')) {
    if (-not (Test-Path -LiteralPath (Join-Path $app $name) -PathType Leaf)) {
        throw "Missing modern context-menu dependency in $app : $name"
    }
}
$msix = (Resolve-Path -LiteralPath $SignedMsix -ErrorAction Stop).ProviderPath

# Windows checks the MSIX signature and certificate trust during registration.
# This script never enables Developer Mode or installs/trusts certificates.
Add-AppxPackage -Path $msix -ExternalLocation $app -ErrorAction Stop
$installed = Get-AppxPackage -Name 'RightConvert.ContextMenu' |
    Where-Object { $_.Architecture -eq 'X64' -or $_.Architecture -eq 'x64' } |
    Sort-Object -Property Version -Descending |
    Select-Object -First 1
if ($null -eq $installed) {
    throw 'Windows did not report an installed RightConvert.ContextMenu package.'
}
[IO.File]::WriteAllText((Join-Path $root 'modern-package-fullname.txt'),
                         $installed.PackageFullName, [Text.Encoding]::UTF8)
Write-Host "Windows 11 context menu package registered: $($installed.PackageFullName)"
Write-Host 'Right-click a file in Explorer; if necessary restart File Explorer or sign out and back in.'
