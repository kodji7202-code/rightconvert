<#
.SYNOPSIS
Builds RightConvertClassic.dll (Win10/11 Explorer COM Player batch verb).
.EXAMPLE
.\installer\build-classic.ps1 -PackageDir '.\dist\RightConvert'
#>
[CmdletBinding()]
param(
    [ValidateSet('x64', 'ARM64')][string]$Architecture = 'x64',
    [string]$PackageDir
)
$ErrorActionPreference = 'Stop'
$source = Join-Path (Split-Path -Parent $PSScriptRoot) 'shell\classic'
$binaryDirectory = Join-Path $source ('out\' + $Architecture)

& cmake -S $source -B $binaryDirectory -A $Architecture
if ($LASTEXITCODE -ne 0) { throw "Classic CMake configure failed: $LASTEXITCODE" }
& cmake --build $binaryDirectory --config Release --parallel 4
if ($LASTEXITCODE -ne 0) { throw "Classic C++ build failed: $LASTEXITCODE" }
& ctest --test-dir $binaryDirectory -C Release --output-on-failure
if ($LASTEXITCODE -ne 0) { throw "Classic C++ integration test failed: $LASTEXITCODE" }

$dll = Join-Path $binaryDirectory 'Release\RightConvertClassic.dll'
if (-not (Test-Path -LiteralPath $dll -PathType Leaf)) {
    throw "Classic DLL was not built: $dll"
}

if ($PackageDir) {
    $target = (Resolve-Path -LiteralPath $PackageDir -ErrorAction Stop).ProviderPath
    if (-not (Test-Path -LiteralPath (Join-Path $target 'RightConvert.exe') -PathType Leaf)) {
        throw "Target is not a PyInstaller RightConvert folder: $target"
    }
    Copy-Item -LiteralPath $dll -Destination (Join-Path $target 'RightConvertClassic.dll') -Force
    Write-Host "Classic batch DLL packaged in: $target"
}
Write-Host "Classic batch COM DLL built and tested: $dll"
