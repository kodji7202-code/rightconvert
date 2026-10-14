# End-to-end registry-shape regression in uniquely named HKCU keys and a local
# fixture. Does not modify the installed RightConvert verb or COM CLSID.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')

$testId = [guid]::NewGuid().ToString('N')
$fixtureParent = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'out'))
New-Item -ItemType Directory -Path $fixtureParent -Force | Out-Null
$fixture = Join-Path $fixtureParent ('registry-test-' + $testId)
$app = Join-Path $fixture 'app'
New-Item -ItemType Directory -Path $app -Force | Out-Null
[IO.File]::WriteAllText((Join-Path $app 'RightConvert.exe'), 'mock fixture binary')
[IO.File]::WriteAllText((Join-Path $fixture 'uninstall.ps1'), 'fixture')

# Redirect every registry mutation to a unique disposable key.
$script:RightConvertVerbKey = 'Software\Classes\*\shell\RightConvertTest-' + $testId
$script:RightConvertUninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\RightConvertTest-' + $testId
$script:RightConvertClassicClsid = '{' + [guid]::NewGuid().ToString().ToUpperInvariant() + '}'
$script:RightConvertClassicKey = 'Software\Classes\CLSID\' + $script:RightConvertClassicClsid
$script:RightConvertQuickVerbKey = 'Software\Classes\*\shell\RightConvertQuickTest-' + $testId
$script:RightConvertQuickHandlerKey = 'Software\Classes\*\shellex\ContextMenuHandlers\RightConvertQuickTest-' + $testId
$script:RightConvertQuickCascadeClsid = '{' + [guid]::NewGuid().ToString().ToUpperInvariant() + '}'
# Never touch the production quick-target CLSIDs in a fixture.
$script:RightConvertQuickTargets = [ordered]@{}
foreach ($format in @('jpg', 'png', 'webp', 'mp3', 'wav', 'flac', 'mp4', 'webm', 'pdf', 'tiff', 'pngzip')) {
    $script:RightConvertQuickTargets[$format] = '{' + [guid]::NewGuid().ToString().ToUpperInvariant() + '}'
}

function Get-RightConvertRoot { return $fixture }
function Read-TestKey {
    param([string]$Relative)
    return [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($Relative)
}

try {
    Register-RightConvert -Root $fixture
    $verb = Read-TestKey $script:RightConvertVerbKey
    try {
        if ($verb.GetValue('MultiSelectModel') -ne 'Document') {
            throw 'Missing DLL must preserve the legacy Document fallback.'
        }
        $cmd = $verb.OpenSubKey('command')
        try {
            if ($null -ne $cmd.GetValue('DelegateExecute', $null)) {
                throw 'Legacy fallback may not retain stale DelegateExecute.'
            }
            if ($cmd.GetValue('') -notmatch '--files "%1"') {
                throw 'Legacy command lost safely quoted file argument.'
            }
        } finally { $cmd.Close() }
    } finally { $verb.Close() }
    if ($null -ne (Read-TestKey $script:RightConvertQuickVerbKey)) {
        throw 'Quick-target menu must not be advertised without the native DLL and CLI marker.'
    }

    [IO.File]::WriteAllText((Join-Path $app 'RightConvertClassic.dll'), 'fixture DLL')
    Register-RightConvert -Root $fixture
    $verb = Read-TestKey $script:RightConvertVerbKey
    try {
        if ($verb.GetValue('MultiSelectModel') -ne 'Player') {
            throw 'Classic native batch handler must activate Player selection.'
        }
        $cmd = $verb.OpenSubKey('command')
        try {
            if ($cmd.GetValue('DelegateExecute') -ne $script:RightConvertClassicClsid) {
                throw 'Classic DelegateExecute CLSID does not match registration.'
            }
        } finally { $cmd.Close() }
    } finally { $verb.Close() }
    $clsid = Read-TestKey $script:RightConvertClassicKey
    try {
        if ($clsid.GetValue('InstallerId') -ne $script:RightConvertInstallerId) {
            throw 'Classic COM class is missing installer ownership.'
        }
        $inproc = $clsid.OpenSubKey('InprocServer32')
        try {
            if ($inproc.GetValue('') -ne (Join-Path $app 'RightConvertClassic.dll')) {
                throw 'COM binary path was not registered correctly.'
            }
            if ($inproc.GetValue('ThreadingModel') -ne 'Apartment') {
                throw 'COM in-process threading model is incorrect.'
            }
        } finally { $inproc.Close() }
    } finally { $clsid.Close() }
    if ($null -ne (Read-TestKey $script:RightConvertQuickVerbKey)) {
        throw 'Quick-target menu must remain disabled until the app advertises its CLI capability.'
    }

    # The release build produces this marker only after CLI tests succeed.
    [IO.File]::WriteAllText((Join-Path $app $script:RightConvertQuickMarker),
                           'rightconvert-direct-cli-v2', [Text.Encoding]::ASCII)
    Register-RightConvert -Root $fixture
    if ($null -eq (Read-TestKey $script:RightConvertQuickHandlerKey)) {
        throw 'A capable default installation must register the verified native submenu.'
    }
    # Real native IContextMenu registration; no registry CommandStore cascade.
    Register-RightConvert -Root $fixture -EnableExperimentalQuickMenu
    if ($null -ne (Read-TestKey $script:RightConvertQuickVerbKey)) {
        throw 'Native handler must not register the broken static cascade.'
    }
    $handler = Read-TestKey $script:RightConvertQuickHandlerKey
    try {
        if ($null -eq $handler -or
            $handler.GetValue('') -ne $script:RightConvertQuickCascadeClsid -or
            $handler.GetValue('InstallerId') -ne $script:RightConvertInstallerId) {
            throw 'Native ContextMenuHandlers registration missing or not owned.'
        }
    } finally { if ($handler) { $handler.Close() } }
    $quickClass = Read-TestKey ('Software\Classes\CLSID\' + $script:RightConvertQuickCascadeClsid)
    try {
        if ($null -eq $quickClass -or $quickClass.GetValue('InstallerId') -ne $script:RightConvertInstallerId) {
            throw 'Native quick cascade COM class missing owner marker.'
        }
        $inproc = $quickClass.OpenSubKey('InprocServer32')
        try {
            if ($null -eq $inproc -or $inproc.GetValue('') -ne (Join-Path $app 'RightConvertClassic.dll') -or
                $inproc.GetValue('ThreadingModel') -ne 'Apartment') {
                throw 'Native quick cascade DLL path or threading model incorrect.'
            }
        } finally { if ($inproc) { $inproc.Close() } }
    } finally { if ($quickClass) { $quickClass.Close() } }

    # Migrate an owner-marked legacy ExtendedSubCommandsKey tree to the native handler.
    $legacy = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($script:RightConvertQuickVerbKey)
    try {
        $legacy.SetValue('InstallerId', $script:RightConvertInstallerId)
        $sub = $legacy.CreateSubKey('ExtendedSubCommandsKey\shell\jpg')
        try { $sub.SetValue('InstallerId', $script:RightConvertInstallerId) }
        finally { $sub.Close() }
    } finally { $legacy.Close() }
    Register-RightConvert -Root $fixture -EnableExperimentalQuickMenu
    if ($null -ne (Read-TestKey $script:RightConvertQuickVerbKey) -or
        $null -eq (Read-TestKey $script:RightConvertQuickHandlerKey)) {
        throw 'Owned empty submenu was not migrated to the native handler.'
    }

    Remove-RightConvertRegistration
    if ($null -ne (Read-TestKey $script:RightConvertVerbKey) -or
        $null -ne (Read-TestKey $script:RightConvertClassicKey) -or
        $null -ne (Read-TestKey $script:RightConvertQuickVerbKey) -or
        $null -ne (Read-TestKey $script:RightConvertQuickHandlerKey) -or
        $null -ne (Read-TestKey ('Software\Classes\CLSID\' + $script:RightConvertQuickCascadeClsid)) -or
        $null -ne (Read-TestKey $script:RightConvertUninstallKey)) {
        throw 'Uninstall left a native quick cascade or original registration in HKCU.'
    }
    foreach ($format in @($script:RightConvertQuickTargets.Keys)) {
        if ($null -ne (Read-TestKey ('Software\Classes\CLSID\' + $script:RightConvertQuickTargets[$format]))) {
            throw "Obsolete quick-target COM CLSID not cleaned up: $format"
        }
    }

    # A foreign quick-menu key must stop registration without being overwritten.
    $foreignMenu = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($script:RightConvertQuickVerbKey)
    try {
        $foreignMenu.SetValue('InstallerId', 'another-installer')
    } finally { $foreignMenu.Close() }
    try {
        $rejected = $false
        try { Register-RightConvert -Root $fixture } catch { $rejected = $true }
        if (-not $rejected) { throw 'Foreign quick-target menu ownership was ignored.' }
        $check = Read-TestKey $script:RightConvertQuickVerbKey
        try {
            if ($check.GetValue('InstallerId') -ne 'another-installer') {
                throw 'Foreign quick-target menu marker was overwritten.'
            }
        } finally { $check.Close() }
    } finally {
        $split = $script:RightConvertQuickVerbKey.LastIndexOf('\')
        $parent = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey(
            $script:RightConvertQuickVerbKey.Substring(0, $split), $true)
        try { $parent.DeleteSubKeyTree($script:RightConvertQuickVerbKey.Substring($split + 1), $false) }
        finally { $parent.Close() }
    }

    $foreignClassId = $script:RightConvertQuickTargets['jpg']
    $foreignClass = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey(
        'Software\Classes\CLSID\' + $foreignClassId)
    try { $foreignClass.SetValue('InstallerId', 'another-installer') }
    finally { $foreignClass.Close() }
    try {
        $rejected = $false
        try { Register-RightConvert -Root $fixture } catch { $rejected = $true }
        if (-not $rejected) { throw 'Foreign quick-target COM class ownership was ignored.' }
        $check = Read-TestKey ('Software\Classes\CLSID\' + $foreignClassId)
        try {
            if ($check.GetValue('InstallerId') -ne 'another-installer') {
                throw 'Foreign quick-target COM marker was overwritten.'
            }
        } finally { $check.Close() }
    } finally {
        $classes = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Software\Classes\CLSID', $true)
        try { $classes.DeleteSubKeyTree($foreignClassId, $false) }
        finally { $classes.Close() }
    }

    Write-Host 'PASS: fallback, native IContextMenu quick cascade, ownership refusal and owned unregister.'
} finally {
    Remove-RightConvertRegistration
    if (Test-Path -LiteralPath $fixture) {
        Assert-TreeHasNoReparsePoints -Path $fixture
        Remove-Item -LiteralPath $fixture -Recurse -Force
    }
}
