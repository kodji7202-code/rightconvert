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
    [string]$SetupExe,
    [string]$PackageDir,
    [Parameter(Mandatory = $true)][string]$Python
)
$ErrorActionPreference = 'Stop'
# Windows PowerShell 5.1 leaves $PSScriptRoot empty in param() defaults.
$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $SetupExe) { $SetupExe = Join-Path $repoRoot 'dist\RightConvertSetup.exe' }
if (-not $PackageDir) { $PackageDir = Join-Path $repoRoot 'dist\RightConvert' }
if ($env:RIGHTCONVERT_E2E_REAL_INSTALL -ne '1') {
    throw 'Refusing: this performs a real per-user install. Set RIGHTCONVERT_E2E_REAL_INSTALL=1 on a disposable machine.'
}
. (Join-Path $PSScriptRoot 'common.ps1')
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
    $menuProbe = Join-Path $repoRoot 'shell\classic\out\x64\Release\RightConvertMenuProbe.exe'
    if (-not (Test-Path -LiteralPath $menuProbe -PathType Leaf)) { throw "Build the native menu probe first: $menuProbe" }
    function Invoke-MenuProbe {
        param([string[]]$Arguments)
        # Windows PowerShell 5.1 turns child stderr into a terminating error
        # under 'Stop'; capture it as text so failures print diagnostics.
        $ErrorActionPreference = 'Continue'
        $lines = @(& $menuProbe @Arguments 2>&1 | ForEach-Object { "$_" })
        $code = $LASTEXITCODE
        $ErrorActionPreference = 'Stop'
        $lines | ForEach-Object { Write-Host "  [$($Arguments[0])] $_" }
        return [pscustomobject]@{ Code = $code; Items = @($lines | Where-Object { $_ -like 'ITEM: *' } | ForEach-Object { $_.Substring(6) }); Lines = $lines }
    }
    # The registered handler alone: activated via HKCU COM registration.
    $handler = Invoke-MenuProbe @('--handler', $script:RightConvertQuickCascadeClsid, $image)
    if ($handler.Code -ne 0) { throw "Registered quick-menu handler failed: exit $($handler.Code)" }
    $expectedImageTargets = @('Convert to jpg', 'Convert to webp', 'Convert to pdf', 'Convert to tiff')
    if (Compare-Object @($handler.Items) $expectedImageTargets) {
        throw "Handler items for a PNG differ from the image targets: $($handler.Items -join ' | ')"
    }
    # Same-format actions can only fail, including extension aliases.
    $jpeg = Join-Path $probeDir 'photo.jpeg'
    & $Python -c "import sys; from PIL import Image; Image.new('RGB', (40, 30), (20, 130, 60)).save(sys.argv[1], 'JPEG')" $jpeg
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the JPEG probe' }
    $jpegMenu = Invoke-MenuProbe @('--handler', $script:RightConvertQuickCascadeClsid, $jpeg)
    if ($jpegMenu.Code -ne 0 -or
        (Compare-Object @($jpegMenu.Items) @('Convert to png', 'Convert to webp', 'Convert to pdf', 'Convert to tiff'))) {
        throw "Handler items for a .jpeg are wrong: $($jpegMenu.Items -join ' | ')"
    }
    # Explorer's complete item menu, then click "Convert to webp" in it.
    $shellMenu = Invoke-MenuProbe @('--shell', $image, 'Convert to webp')
    if ($shellMenu.Items -notcontains 'Convert with RightConvert') {
        throw 'Shell item context menu does not offer "Convert with RightConvert"'
    }
    if ($shellMenu.Code -ne 0 -or -not ($shellMenu.Lines -contains 'INVOKED: Convert to webp')) {
        throw "Shell item context menu could not invoke the native 'Convert to webp': exit $($shellMenu.Code)"
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

    # --- 5. README developer path: install.ps1 with no arguments ------------
    # Uses the default -PackageDir (dist\RightConvert) resolved by the script.
    $installScript = Join-Path $PSScriptRoot 'install.ps1'
    $proc = Start-Process -FilePath $powershell -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass',
        '-File', ('"' + $installScript + '"')) -WorkingDirectory $repoRoot -Wait -PassThru
    Assert-Equal $proc.ExitCode 0 'install.ps1 (no arguments) exit code'
    Assert-Equal (Get-FileHash -LiteralPath $exe).Hash (Get-FileHash -LiteralPath (Join-Path $PackageDir 'RightConvert.exe')).Hash 'Default-package install hash'
    Assert-Equal (Read-Value ($verb + '\command')) ('"' + $exe + '" --files "%1"') 'Default-package verb command'
    $uninstallScript = Join-Path $PSScriptRoot 'uninstall.ps1'
    $proc = Start-Process -FilePath $powershell -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass',
        '-File', ('"' + $uninstallScript + '"'), '-PurgeUserData') -Wait -PassThru
    Assert-Equal $proc.ExitCode 0 'uninstall.ps1 -PurgeUserData exit code'
    foreach ($relative in @($script:RightConvertVerbKey, $script:RightConvertUninstallKey,
                            $script:RightConvertClassicKey, $script:RightConvertQuickHandlerKey)) {
        Assert-KeyAbsent $relative
    }
    if (Test-Path -LiteralPath $root) { throw "-PurgeUserData left the RightConvert folder: $root" }
    Write-Host 'README install.ps1 (default package) / uninstall -PurgeUserData: PASS'
} finally {
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}
