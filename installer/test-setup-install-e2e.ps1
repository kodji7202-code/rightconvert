# REAL per-user installation acceptance for a disposable machine (CI runner).
#
# Unlike the isolated regressions, this registers the production HKCU verbs and
# installs into %LOCALAPPDATA%\RightConvert, exactly like a customer clicking
# Install in RightConvertSetup.exe. It therefore refuses to run unless
# RIGHTCONVERT_E2E_REAL_INSTALL=1 and no RightConvert installation exists.
#
#  1. Extracts RightConvertPayload.zip + .sha256 from the *built Setup EXE* and
#     drives Setup's own unpack_payload() + powershell_install() code path.
#  2. Verifies files, the classic verb, COM classes, quick-menu handler and the
#     Programs and Features entry, then activates the COM class via registry.
#  3. Enumerates the real Shell context menu of a file (Shell.Application) and
#     invokes "Convert to webp" through the native IContextMenu handler.
#  4. Runs the registered UninstallString and verifies everything is removed.
[CmdletBinding()]
param(
    [string]$SetupExe = (Join-Path (Split-Path -Parent $PSScriptRoot) 'dist\RightConvertSetup.exe'),
    [string]$PackageDir = (Join-Path (Split-Path -Parent $PSScriptRoot) 'dist\RightConvert'),
    [Parameter(Mandatory = $true)][string]$Python
)
$ErrorActionPreference = 'Stop'
if ($env:RIGHTCONVERT_E2E_REAL_INSTALL -ne '1') {
    throw 'Refusing: this performs a real per-user install. Set RIGHTCONVERT_E2E_REAL_INSTALL=1 on a disposable machine.'
}
. (Join-Path $PSScriptRoot 'common.ps1')
$repo = Split-Path -Parent $PSScriptRoot
$Python = [IO.Path]::GetFullPath($Python)
$SetupExe = [IO.Path]::GetFullPath($SetupExe)
foreach ($required in @($Python, $SetupExe, (Join-Path $PackageDir 'RightConvert.exe'))) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "Missing: $required" }
}

$root = Get-RightConvertRoot
if (Test-Path -LiteralPath $root) { throw "An installation or user data already exists: $root" }
foreach ($relative in @($script:RightConvertVerbKey, $script:RightConvertUninstallKey,
                        $script:RightConvertClassicKey, $script:RightConvertQuickHandlerKey)) {
    $existing = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($relative)
    if ($null -ne $existing) { $existing.Close(); throw "Existing registration: HKCU\$relative" }
}

function Read-Value {
    param([string]$Relative, [string]$Name = '')
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($Relative)
    if ($null -eq $key) { throw "Missing registry key HKCU\$Relative" }
    try { return $key.GetValue($Name, $null, 'DoNotExpandEnvironmentNames') } finally { $key.Close() }
}
function Assert-Equal {
    param($Actual, $Expected, [string]$What)
    if ($Actual -ne $Expected) { throw "$What`: expected <$Expected>, got <$Actual>" }
}
function Assert-KeyAbsent {
    param([string]$Relative)
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($Relative)
    if ($null -ne $key) { $key.Close(); throw "Registry key still present: HKCU\$Relative" }
}

$work = Join-Path ([IO.Path]::GetTempPath()) ('rc-e2e-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
try {
    # --- 1. Setup EXE payload -> Setup's own install code --------------------
    $driver = Join-Path $work 'drive_setup.py'
    [IO.File]::WriteAllText($driver, @'
import pathlib, sys, tempfile
from PyInstaller.archive.readers import CArchiveReader

setup_exe, installer_dir, resources = (pathlib.Path(arg) for arg in sys.argv[1:4])
resources.mkdir(parents=True, exist_ok=True)
frozen = CArchiveReader(str(setup_exe))
for name in ("RightConvertPayload.zip", "RightConvertPayload.sha256"):
    (resources / name).write_bytes(frozen.extract(name))
sys.path.insert(0, str(installer_dir))
import setup_gui
setup_gui.resource_dir = lambda: resources
steps = []
with tempfile.TemporaryDirectory(prefix="RightConvertSetup-") as temporary:
    extracted = pathlib.Path(temporary) / "payload"
    setup_gui.unpack_payload(extracted, lambda value, text: steps.append(value))
    output = setup_gui.powershell_install(extracted, lambda value, text: steps.append(value))
assert steps and steps[-1] == 100, steps
print(output)
print("SETUP_INSTALL_OK")
'@, (New-Object Text.UTF8Encoding($false)))
    $resources = Join-Path $work 'setup-resources'
    $driverOutput = & $Python $driver $SetupExe $PSScriptRoot $resources 2>&1
    $driverOutput | ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -ne 0 -or -not ($driverOutput -match 'SETUP_INSTALL_OK')) {
        throw "Setup install code path failed: exit $LASTEXITCODE"
    }

    # --- 2. Files and registry ----------------------------------------------
    $app = Join-Path $root 'app'
    $exe = Join-Path $app 'RightConvert.exe'
    $dll = Join-Path $app 'RightConvertClassic.dll'
    foreach ($file in @($exe, $dll, (Join-Path $app 'rightconvert-direct-cli-v2.marker'),
                        (Join-Path $root 'uninstall.ps1'), (Join-Path $root 'common.ps1'))) {
        if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "Not installed: $file" }
    }
    Assert-Equal (Get-FileHash -LiteralPath $exe).Hash (Get-FileHash -LiteralPath (Join-Path $PackageDir 'RightConvert.exe')).Hash 'Installed RightConvert.exe hash'
    Assert-Equal (Get-FileHash -LiteralPath $dll).Hash (Get-FileHash -LiteralPath (Join-Path $PackageDir 'RightConvertClassic.dll')).Hash 'Installed classic DLL hash'

    $verb = $script:RightConvertVerbKey
    Assert-Equal (Read-Value $verb 'MUIVerb') 'Convert with RightConvert' 'MUIVerb'
    Assert-Equal (Read-Value $verb 'MultiSelectModel') 'Player' 'MultiSelectModel'
    Assert-Equal (Read-Value $verb 'InstallerId') $script:RightConvertInstallerId 'Verb owner'
    Assert-Equal (Read-Value ($verb + '\command')) ('"' + $exe + '" --files "%1"') 'Verb command'
    Assert-Equal (Read-Value ($verb + '\command') 'DelegateExecute') $script:RightConvertClassicClsid 'DelegateExecute'
    Assert-Equal (Read-Value ($script:RightConvertClassicKey + '\InprocServer32')) $dll 'Classic InprocServer32'
    Assert-Equal (Read-Value ($script:RightConvertClassicKey + '\InprocServer32') 'ThreadingModel') 'Apartment' 'Classic ThreadingModel'
    Assert-Equal (Read-Value $script:RightConvertQuickHandlerKey) $script:RightConvertQuickCascadeClsid 'Quick menu handler'
    $quickClass = 'Software\Classes\CLSID\' + $script:RightConvertQuickCascadeClsid
    Assert-Equal (Read-Value ($quickClass + '\InprocServer32')) $dll 'Quick InprocServer32'

    $uninstall = $script:RightConvertUninstallKey
    Assert-Equal (Read-Value $uninstall 'DisplayName') 'RightConvert' 'Uninstall DisplayName'
    Assert-Equal (Read-Value $uninstall 'InstallLocation') $root 'InstallLocation'
    $uninstallString = Read-Value $uninstall 'UninstallString'
    if ($uninstallString -notmatch '^"([^"]+)" (.+)$') { throw "Unexpected UninstallString: $uninstallString" }
    $uninstallExe = $Matches[1]
    $uninstallArgs = $Matches[2]
    if (-not [IO.Path]::IsPathRooted($uninstallExe) -or
        -not (Test-Path -LiteralPath $uninstallExe -PathType Leaf)) {
        throw "UninstallString must name an absolute, existing PowerShell: $uninstallString"
    }
    if ($uninstallArgs -notlike ('*-File "' + (Join-Path $root 'uninstall.ps1') + '"')) {
        throw "UninstallString does not run the installed uninstaller: $uninstallString"
    }

    # --- 3. COM activation and the real Shell context menu ------------------
    # Separate processes, so this script never pins the DLL during uninstall.
    $probeDir = Join-Path $work 'Ședință 東京 folder'
    New-Item -ItemType Directory -Path $probeDir | Out-Null
    $image = Join-Path $probeDir 'probe image.png'
    & $Python -c "import sys; from PIL import Image; Image.new('RGB', (40, 30), (200, 30, 60)).save(sys.argv[1])" $image
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the probe image' }
    $shellProbe = Join-Path $work 'shell-probe.ps1'
    [IO.File]::WriteAllText($shellProbe, @'
param([string]$Clsid, [string]$Folder, [string]$Name)
$ErrorActionPreference = 'Stop'
$type = [Type]::GetTypeFromCLSID([guid]$Clsid)
$instance = [Activator]::CreateInstance($type)
if ($null -eq $instance) { throw 'COM activation through HKCU registration failed' }
[void][Runtime.InteropServices.Marshal]::ReleaseComObject($instance)
Write-Output 'COM_ACTIVATION_OK'
$shell = New-Object -ComObject Shell.Application
$item = $shell.Namespace($Folder).ParseName($Name)
if ($null -eq $item) { throw 'Shell could not parse the probe file' }
$verbs = @($item.Verbs())
$names = @($verbs | ForEach-Object { $_.Name -replace '&', '' })
Write-Output ('VERBS: ' + ($names -join ' | '))
$webp = $verbs | Where-Object { ($_.Name -replace '&', '') -eq 'Convert to webp' } | Select-Object -First 1
if ($names -contains 'Convert with RightConvert') { Write-Output 'MAIN_VERB_OK' }
if ($null -ne $webp) {
    $webp.DoIt()
    Write-Output 'QUICK_VERB_INVOKED'
}
'@, (New-Object Text.UTF8Encoding($true)))
    $powershell = Join-Path ([Environment]::SystemDirectory) 'WindowsPowerShell\v1.0\powershell.exe'
    $probeOutput = & $powershell -NoProfile -ExecutionPolicy Bypass -STA -File $shellProbe `
        -Clsid $script:RightConvertQuickCascadeClsid -Folder $probeDir -Name 'probe image.png' 2>&1
    $probeOutput | ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -ne 0 -or -not ($probeOutput -match 'COM_ACTIVATION_OK')) {
        throw 'The registered quick-menu COM class could not be activated'
    }
    if (-not ($probeOutput -match 'MAIN_VERB_OK')) {
        throw 'Shell context menu does not offer "Convert with RightConvert"'
    }
    if (-not ($probeOutput -match 'QUICK_VERB_INVOKED')) {
        throw 'Shell context menu does not offer the native "Convert to webp" action'
    }
    $converted = Join-Path $probeDir 'probe image.webp'
    $deadline = (Get-Date).AddSeconds(60)
    while (-not (Test-Path -LiteralPath $converted -PathType Leaf)) {
        if ((Get-Date) -gt $deadline) { throw "Context-menu conversion produced no output: $converted" }
        Start-Sleep -Milliseconds 250
    }
    # Wait until the separate --direct process has published and exited.
    $deadline = (Get-Date).AddSeconds(60)
    while (@(Get-Process -Name 'RightConvert' -ErrorAction SilentlyContinue |
             Where-Object { $_.Path -eq $exe }).Count -gt 0) {
        if ((Get-Date) -gt $deadline) { throw 'RightConvert --direct did not exit' }
        Start-Sleep -Milliseconds 250
    }
    $header = [IO.File]::ReadAllBytes($converted)[0..11]
    if ([Text.Encoding]::ASCII.GetString($header, 0, 4) -ne 'RIFF' -or
        [Text.Encoding]::ASCII.GetString($header, 8, 4) -ne 'WEBP') {
        throw 'Context-menu output is not a WebP file'
    }
    Write-Host "Context-menu conversion OK: $converted"

    # Direct CLI of the installed app as well (no Shell involved).
    $proc = Start-Process -FilePath $exe -ArgumentList @('--files', ('"' + $image + '"'), '--target', 'jpg', '--direct') `
        -WorkingDirectory $probeDir -Wait -PassThru
    Assert-Equal $proc.ExitCode 0 'Installed --direct exit code'
    if (-not (Test-Path -LiteralPath (Join-Path $probeDir 'probe image.jpg') -PathType Leaf)) {
        throw 'Installed --direct conversion produced no JPG'
    }

    # --- 4. Uninstall exactly like Programs and Features --------------------
    $proc = Start-Process -FilePath $uninstallExe -ArgumentList $uninstallArgs -Wait -PassThru
    Assert-Equal $proc.ExitCode 0 'Uninstaller exit code'
    $pending = $null
    $runOnce = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($script:RightConvertRunOnceKey)
    if ($null -ne $runOnce) {
        try { $pending = $runOnce.GetValue($script:RightConvertRunOnceValue, $null) } finally { $runOnce.Close() }
    }
    if ($null -ne $pending) { throw "Uninstall deferred although no Explorer held the DLL: $pending" }
    foreach ($relative in @($script:RightConvertVerbKey, $script:RightConvertUninstallKey,
                            $script:RightConvertClassicKey, $script:RightConvertQuickHandlerKey,
                            $quickClass)) {
        Assert-KeyAbsent $relative
    }
    if (Test-Path -LiteralPath $app) { throw "Application folder was not removed: $app" }
    if (Test-Path -LiteralPath (Join-Path $root 'uninstall.ps1')) { throw 'Installed uninstaller left behind' }
    Write-Host 'REAL SETUP INSTALL / CONTEXT MENU / UNINSTALL: PASS'
} finally {
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}
