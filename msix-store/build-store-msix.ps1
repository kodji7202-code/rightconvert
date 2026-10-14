<#
.SYNOPSIS
Builds the Microsoft Store FULL MSIX for RightConvert plus a .msixupload.

.DESCRIPTION
Lays out the PyInstaller one-dir app (dist\RightConvert) together with the
Windows 11 IExplorerCommand DLL, generates the visual assets and resources.pri,
writes msix-store\AppxManifest.xml with the requested version, and runs
MakeAppx (with full manifest/semantic validation, i.e. without /nv).

Outputs (in -OutputDirectory, default msix-store\out):
  RightConvert_<ver>_x64.msix         UNSIGNED - the Store signs it
  RightConvert_<ver>_x64.msixupload   zip of the .msix: upload this to Partner Center
  with -TestSign additionally:
  RightConvert_<ver>_x64.test.msix    signed with a throw-away self-signed cert
  RightConvert-StoreTest.cer          that cert's public part (trust it to sideload)

The test-signed variant is ONLY for local/CI sideload testing. Never upload it.

.EXAMPLE
.\build.ps1 -SkipTests
.\installer\build-native.ps1 -Architecture x64
.\msix-store\build-store-msix.ps1 -AppDirectory .\dist\RightConvert -Version 1.0.0.0
.EXAMPLE
.\msix-store\build-store-msix.ps1 -AppDirectory .\dist\RightConvert -TestSign
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$AppDirectory,
    [string]$ExplorerDll,
    [string]$Version = '1.0.0.0',
    [string]$OutputDirectory,
    [string]$Python = 'python',
    [switch]$TestSign
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2

$projectRoot = Split-Path -Parent $PSScriptRoot
$manifestTemplate = Join-Path $PSScriptRoot 'AppxManifest.xml'

function Find-WindowsSdkTool {
    param([Parameter(Mandatory = $true)][string]$Name)
    $found = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -ne $found) { return $found.Source }
    $base = Join-Path ([Environment]::GetEnvironmentVariable('ProgramFiles(x86)')) 'Windows Kits\10\bin'
    if (Test-Path -LiteralPath $base) {
        $releases = Get-ChildItem -LiteralPath $base -Directory | Sort-Object -Property Name -Descending
        foreach ($release in $releases) {
            $candidate = Join-Path $release.FullName ('x64\' + $Name)
            if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
        }
    }
    throw "Windows SDK tool missing: $Name (install the Windows 10/11 SDK)"
}

# --- Inputs -----------------------------------------------------------------
if ($Version -notmatch '^\d{1,5}\.\d{1,5}\.\d{1,5}\.0$') {
    throw "Version '$Version' must be Major.Minor.Build.0: the Store requires revision 0."
}
$app = (Resolve-Path -LiteralPath $AppDirectory -ErrorAction Stop).ProviderPath
if (-not (Test-Path -LiteralPath (Join-Path $app 'RightConvert.exe') -PathType Leaf)) {
    throw "RightConvert.exe not found in $app (run .\build.ps1 first)"
}
if ([string]::IsNullOrWhiteSpace($ExplorerDll)) {
    foreach ($candidate in @((Join-Path $app 'RightConvertExplorer.dll'),
                             (Join-Path $projectRoot 'shell\windows11\out\x64\Release\RightConvertExplorer.dll'))) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { $ExplorerDll = $candidate; break }
    }
}
if ([string]::IsNullOrWhiteSpace($ExplorerDll) -or -not (Test-Path -LiteralPath $ExplorerDll -PathType Leaf)) {
    throw 'RightConvertExplorer.dll not found (run .\installer\build-native.ps1 -Architecture x64 first)'
}
$ExplorerDll = (Resolve-Path -LiteralPath $ExplorerDll).ProviderPath
if ([string]::IsNullOrWhiteSpace($OutputDirectory)) { $OutputDirectory = Join-Path $PSScriptRoot 'out' }
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$out = (Resolve-Path -LiteralPath $OutputDirectory).ProviderPath

$makeappx = Find-WindowsSdkTool 'makeappx.exe'
$makepri = Find-WindowsSdkTool 'makepri.exe'
Write-Host "MakeAppx: $makeappx"
Write-Host "MakePri:  $makepri"

$baseName = "RightConvert_${Version}_x64"
$msix = Join-Path $out "$baseName.msix"
$upload = Join-Path $out "$baseName.msixupload"
$work = Join-Path ([IO.Path]::GetTempPath()) ('RightConvertStore-' + [guid]::NewGuid().ToString('N'))
$layout = Join-Path $work 'layout'
try {
    # --- Package layout --------------------------------------------------------
    New-Item -ItemType Directory -Path $layout -Force | Out-Null
    Copy-Item -Path (Join-Path $app '*') -Destination $layout -Recurse -Force
    # Not applicable inside a package: the classic per-user registry COM verb
    # and the sparse-package/Setup artefacts. The package declares its own menu.
    foreach ($excluded in @('RightConvertClassic.dll', 'AppxManifest.xml', 'Assets')) {
        $path = Join-Path $layout $excluded
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Recurse -Force }
    }
    Copy-Item -LiteralPath $ExplorerDll -Destination (Join-Path $layout 'RightConvertExplorer.dll') -Force

    & $Python (Join-Path $PSScriptRoot 'make_assets.py') $layout
    if ($LASTEXITCODE -ne 0) { throw "Asset generation failed: exit code $LASTEXITCODE" }

    [xml]$xml = Get-Content -LiteralPath $manifestTemplate -Raw -Encoding UTF8
    $xml.Package.Identity.Version = $Version
    $publisher = $xml.Package.Identity.Publisher
    $xml.Save((Join-Path $layout 'AppxManifest.xml'))

    # --- resources.pri (scale/targetsize asset variants) -------------------------
    # Index only Assets\ + manifest: indexing the whole one-dir app would put
    # thousands of Python runtime files into the PRI for no benefit.
    $priRoot = Join-Path $work 'pri-root'
    New-Item -ItemType Directory -Path $priRoot -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $layout 'Assets') -Destination $priRoot -Recurse
    Copy-Item -LiteralPath (Join-Path $layout 'AppxManifest.xml') -Destination $priRoot
    $priConfig = Join-Path $work 'priconfig.xml'
    & $makepri createconfig /cf $priConfig /dq en-US /pv 10.0.0 /o
    if ($LASTEXITCODE -ne 0) { throw "MakePri createconfig failed: exit code $LASTEXITCODE" }
    & $makepri new /pr $priRoot /cf $priConfig /mn (Join-Path $priRoot 'AppxManifest.xml') /of (Join-Path $work 'resources.pri') /o
    if ($LASTEXITCODE -ne 0) { throw "MakePri new failed: exit code $LASTEXITCODE" }
    Copy-Item -LiteralPath (Join-Path $work 'resources.pri') -Destination (Join-Path $layout 'resources.pri') -Force

    # --- Pack (validating) -------------------------------------------------------
    if (Test-Path -LiteralPath $msix) { Remove-Item -LiteralPath $msix -Force }
    & $makeappx pack /o /h SHA256 /d $layout /p $msix
    if ($LASTEXITCODE -ne 0) { throw "MakeAppx pack failed: exit code $LASTEXITCODE" }
    Write-Host "Unsigned Store package: $msix"

    # --- .msixupload (what Partner Center takes) -----------------------------------
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    if (Test-Path -LiteralPath $upload) { Remove-Item -LiteralPath $upload -Force }
    $zip = [IO.Compression.ZipFile]::Open($upload, [IO.Compression.ZipArchiveMode]::Create)
    try {
        [void][IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
            $zip, $msix, [IO.Path]::GetFileName($msix), [IO.Compression.CompressionLevel]::NoCompression)
        $sym = Join-Path $out "$baseName.appxsym"
        if (Test-Path -LiteralPath $sym) {
            [void][IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
                $zip, $sym, [IO.Path]::GetFileName($sym), [IO.Compression.CompressionLevel]::Optimal)
        }
    } finally { $zip.Dispose() }
    Write-Host "Store upload file: $upload"

    # --- Optional test-signed copy for sideloading ---------------------------------
    if ($TestSign) {
        $signtool = Find-WindowsSdkTool 'signtool.exe'
        $cert = New-SelfSignedCertificate -Type Custom -Subject $publisher `
            -KeyUsage DigitalSignature -KeyAlgorithm RSA -KeyLength 2048 `
            -FriendlyName 'RightConvert Store sideload TEST ONLY' `
            -CertStoreLocation 'Cert:\CurrentUser\My' -NotAfter (Get-Date).AddDays(7) `
            -TextExtension @('2.5.29.37={text}1.3.6.1.5.5.7.3.3', '2.5.29.19={text}')
        $cer = Join-Path $out 'RightConvert-StoreTest.cer'
        Export-Certificate -Cert $cert -FilePath $cer -Force | Out-Null
        $testMsix = Join-Path $out "$baseName.test.msix"
        Copy-Item -LiteralPath $msix -Destination $testMsix -Force
        & $signtool sign /fd SHA256 /sha1 $cert.Thumbprint /s My $testMsix
        if ($LASTEXITCODE -ne 0) { throw "SignTool failed: exit code $LASTEXITCODE" }
        Write-Host "TEST-signed package (do NOT upload): $testMsix"
        Write-Host "Test certificate: $cer (thumbprint $($cert.Thumbprint))"
    }
} finally {
    if (Test-Path -LiteralPath $work) { Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue }
}
