<#
.SYNOPSIS
Builds the identity-only sparse MSIX for the Windows 11 Explorer menu.
.DESCRIPTION
Signing is optional at build time, but mandatory before installing the MSIX.
The Publisher must match the subject of the signing certificate.
.EXAMPLE
.\installer\build-sparse.ps1 -ExternalDirectory .\dist\RightConvert
.EXAMPLE
.\installer\build-sparse.ps1 -ExternalDirectory .\dist\RightConvert -Publisher 'CN=My Software Publisher' -CertificateThumbprint '0123456789ABCDEF...'
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ExternalDirectory,
    [string]$Publisher = 'CN=RightConvert',
    [string]$CertificateThumbprint,
    [string]$OutputPath
)
$ErrorActionPreference = 'Stop'

function Find-WindowsSdkTool {
    param([Parameter(Mandatory = $true)][string]$Name)
    $found = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -ne $found) { return $found.Source }
    $programFilesX86 = [Environment]::GetEnvironmentVariable('ProgramFiles(x86)')
    $base = Join-Path $programFilesX86 'Windows Kits\10\bin'
    if (Test-Path -LiteralPath $base) {
        $releases = Get-ChildItem -LiteralPath $base -Directory |
            Sort-Object -Property Name -Descending
        foreach ($release in $releases) {
            $candidate = Join-Path $release.FullName ('x64\' + $Name)
            if (Test-Path -LiteralPath $candidate -PathType Leaf) {
                return $candidate
            }
        }
    }
    throw "Windows SDK tool missing: $Name (install the Windows 10/11 SDK)"
}

$external = (Resolve-Path -LiteralPath $ExternalDirectory -ErrorAction Stop).ProviderPath
foreach ($name in @('RightConvert.exe', 'RightConvertExplorer.dll')) {
    if (-not (Test-Path -LiteralPath (Join-Path $external $name) -PathType Leaf)) {
        throw "Sparse package requires $name in $external"
    }
}
if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputPath = Join-Path $PSScriptRoot 'out\RightConvert.ContextMenu.x64.msix'
}
$fullOutput = [IO.Path]::GetFullPath($OutputPath)
$outputFolder = Split-Path -Parent $fullOutput
New-Item -Path $outputFolder -ItemType Directory -Force | Out-Null

$makeappx = Find-WindowsSdkTool 'makeappx.exe'
$signtool = $null
if (-not [string]::IsNullOrWhiteSpace($CertificateThumbprint)) {
    $signtool = Find-WindowsSdkTool 'signtool.exe'
    $thumbprint = $CertificateThumbprint.Replace(' ', '')
    $certificate = Get-Item -LiteralPath ("Cert:\CurrentUser\My\" + $thumbprint) -ErrorAction Stop
    if (-not $certificate.HasPrivateKey) {
        throw 'The signing certificate in CurrentUser\My has no private key.'
    }
    if (-not $certificate.Subject.Equals($Publisher, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Publisher '$Publisher' must match certificate subject '$($certificate.Subject)'."
    }
}

# Logo paths from the manifest resolve against the app's external install folder.
# Generate them once; they remain outside the identity-only MSIX as Microsoft requires.
Add-Type -AssemblyName System.Drawing
$assets = Join-Path $external 'Assets'
New-Item -ItemType Directory -Path $assets -Force | Out-Null
foreach ($logo in @(
    @{ Name = 'StoreLogo.png'; Size = 50 },
    @{ Name = 'Square44x44Logo.png'; Size = 44 },
    @{ Name = 'Square150x150Logo.png'; Size = 150 }
)) {
    $path = Join-Path $assets $logo.Name
    if (Test-Path -LiteralPath $path) { continue }
    $size = [int]$logo.Size
    $bitmap = New-Object System.Drawing.Bitmap($size, $size)
    $canvas = [System.Drawing.Graphics]::FromImage($bitmap)
    $font = New-Object System.Drawing.Font('Segoe UI', [single]($size * 0.58), [System.Drawing.FontStyle]::Bold, [System.Drawing.GraphicsUnit]::Pixel)
    $brush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::White)
    try {
        $canvas.Clear([System.Drawing.Color]::FromArgb(28, 39, 54))
        $canvas.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit
        $canvas.DrawString('R', $font, $brush, [single]($size * 0.13), [single]($size * 0.08))
        $bitmap.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
    } finally {
        $brush.Dispose()
        $font.Dispose()
        $canvas.Dispose()
        $bitmap.Dispose()
    }
}

$stage = Join-Path ([IO.Path]::GetTempPath()) ('RightConvertSparse-' + [guid]::NewGuid().ToString('N'))
try {
    New-Item -Path $stage -ItemType Directory -Force | Out-Null
    [xml]$xml = Get-Content -LiteralPath (Join-Path (Split-Path -Parent $PSScriptRoot) 'shell\windows11\AppxManifest.xml') -Raw
    $identity = $xml.Package.Identity
    $identity.Publisher = $Publisher
    $xml.Save((Join-Path $stage 'AppxManifest.xml'))
    & $makeappx pack /o /nv /d $stage /p $fullOutput
    if ($LASTEXITCODE -ne 0) { throw "MakeAppx failed: exit code $LASTEXITCODE" }
    if ($null -ne $signtool) {
        & $signtool sign /fd SHA256 /s My /sha1 $thumbprint $fullOutput
        if ($LASTEXITCODE -ne 0) { throw "SignTool failed: exit code $LASTEXITCODE" }
        Write-Host "Signed sparse package: $fullOutput"
    } else {
        Write-Warning 'The MSIX is unsigned. It cannot be installed until signed with a trusted publisher certificate.'
        Write-Host "Unsigned sparse package: $fullOutput"
    }
} finally {
    if (Test-Path -LiteralPath $stage) {
        Remove-Item -LiteralPath $stage -Recurse -Force
    }
}
