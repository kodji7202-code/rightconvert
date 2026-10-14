# Isolated real-payload installation, direct CLI and uninstall acceptance.
# Never registers the production HKCU RightConvert classes/verbs, and never
# replaces the installed application. Requires the private Stage7 app bundle.
[CmdletBinding()]
param([string]$PackageDir)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
if (-not $PackageDir) {
    $PackageDir = Join-Path (Split-Path -Parent $PSScriptRoot) 'build\stage7\packed\RightConvert'
}

$id = [guid]::NewGuid().ToString('N')
$fixtureParent = Join-Path $PSScriptRoot 'out'
New-Item -ItemType Directory -Path $fixtureParent -Force | Out-Null
$fixture = Join-Path $fixtureParent ('install-regression-' + $id)
$script:RightConvertVerbKey = 'Software\Classes\*\shell\RightConvertTest-' + $id
$script:RightConvertUninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\RightConvertTest-' + $id
$script:RightConvertClassicClsid = '{' + ([guid]$id).ToString().ToUpperInvariant() + '}'
$script:RightConvertClassicKey = 'Software\Classes\CLSID\' + $script:RightConvertClassicClsid
$script:RightConvertQuickVerbKey = 'Software\Classes\*\shell\RightConvertQuickTest-' + $id
$script:RightConvertQuickHandlerKey = 'Software\Classes\*\shellex\ContextMenuHandlers\RightConvertQuickTest-' + $id
$script:RightConvertRunOnceValue = '!RightConvertTest-Pending-' + $id
$script:RightConvertBackupRunOnceValue = '!RightConvertTest-Backup-' + $id
# Production quick-target GUIDs are deliberately NOT in this test's allowlist.
$script:RightConvertQuickTargets = [ordered]@{}
$script:RightConvertQuickCascadeClsid = '{' + ([guid]::NewGuid()).ToString().ToUpperInvariant() + '}'
function Get-RightConvertRoot { return $fixture }

$originalLocalAppdata = $env:LOCALAPPDATA
try {
    # Installer itself applies uniquely suffixed registry redirection.
    & (Join-Path $PSScriptRoot 'install.ps1') -PackageDir $PackageDir -TestFixtureRoot $fixture
    $app = Join-Path $fixture 'app'
    foreach ($name in @('RightConvert.exe', 'RightConvertClassic.dll',
                         'rightconvert-direct-cli-v2.marker')) {
        if (-not (Test-Path -LiteralPath (Join-Path $app $name) -PathType Leaf)) {
            throw "Missing installed native or direct CLI capability: $name"
        }
    }
    if ((Get-FileHash (Join-Path $app 'RightConvert.exe')).Hash -ne
        (Get-FileHash (Join-Path $PackageDir 'RightConvert.exe')).Hash) {
        throw 'Installed EXE differs from the private preview source.'
    }
    $hk = [Microsoft.Win32.Registry]::CurrentUser
    $handler = $hk.OpenSubKey($script:RightConvertQuickHandlerKey)
    if ($null -eq $handler) { throw 'Installed native direct verbs handler is missing.' }
    try {
        if ($handler.GetValue('InstallerId', '') -ne $script:RightConvertInstallerId) {
            throw 'Native handler not owned by test installer.'
        }
        $script:RightConvertQuickCascadeClsid = $handler.GetValue('', '')
    } finally { $handler.Close() }
    $native = $hk.OpenSubKey('Software\Classes\CLSID\' + $script:RightConvertQuickCascadeClsid)
    if ($null -eq $native) { throw 'Installed native COM CLSID missing.' }
    try {
        if ($native.GetValue('InstallerId', '') -ne $script:RightConvertInstallerId) {
            throw 'Installed native CLSID not owned by test installer.'
        }
    } finally { $native.Close() }
    $verb = $hk.OpenSubKey($script:RightConvertVerbKey)
    if ($null -eq $verb) { throw 'Installed classic GUI menu missing.' }
    try {
        if ($verb.GetValue('MultiSelectModel') -ne 'Player') {
            throw 'Installed classic GUI COM Player fallback missing.'
        }
    } finally { $verb.Close() }

    # Exercise the ACTUALLY INSTALLED frozen app from this isolated fixture.
    $env:LOCALAPPDATA = Join-Path $fixture 'appdata-isolated'
    $env:STAGE7_PRIVATE_EXE = Join-Path $app 'RightConvert.exe'
    $env:STAGE7_PRIVATE_SAMPLE = Join-Path $fixture 'test-Știință-東京.png'
    @'
from pathlib import Path
from PIL import Image
from hashlib import sha256
import os, subprocess
file = Path(os.environ['STAGE7_PRIVATE_SAMPLE'])
Image.new('RGB', (86, 44), (21, 60, 107)).save(file)
original = sha256(file.read_bytes()).digest()
result = subprocess.run([os.environ['STAGE7_PRIVATE_EXE'], '--files', str(file),
                         '--target', 'webp', '--direct'], timeout=45)
assert result.returncode == 0, result.returncode
with Image.open(file.with_suffix('.webp')) as image:
    assert image.format == 'WEBP' and image.size == (86, 44)
assert sha256(file.read_bytes()).digest() == original
print('ISOLATED_INSTALLED_EXE_UNICODE_DIRECT_WEBP_PASS')
'@ | python -
    if ($LASTEXITCODE -ne 0) { throw 'The installed frozen EXE direct conversion failed.' }

    $result = Invoke-RightConvertUninstallation -Root $fixture
    if ($result -ne 'complete') { throw "Fixture uninstall remained pending: $result" }
    foreach ($path in @($script:RightConvertVerbKey, $script:RightConvertQuickHandlerKey,
                      ('Software\Classes\CLSID\' + $script:RightConvertQuickCascadeClsid),
                      $script:RightConvertUninstallKey)) {
        $key = $hk.OpenSubKey($path)
        if ($key) { $key.Close(); throw "Leftover fixture registry: $path" }
    }
    if (Test-Path -LiteralPath $app) { throw 'Uninstall left installed app DLLs.' }
    Write-Host 'PASS: real private Stage7 payload install + owned HKCU COM + frozen Unicode WebP + uninstall.'
} finally {
    $env:LOCALAPPDATA = $originalLocalAppdata
    Remove-RightConvertDeferredCleanup -Root $fixture
    Remove-RightConvertBackupCleanupRegistration -Root $fixture
    Remove-RightConvertRegistration
    if (Test-Path -LiteralPath $fixture) {
        Assert-TreeHasNoReparsePoints -Path $fixture
        Remove-Item -LiteralPath $fixture -Recurse -Force
    }
}
