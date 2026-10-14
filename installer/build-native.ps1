<#
.SYNOPSIS
Compiles the native IExplorerCommand COM DLL for a Windows 11 sparse package.
.EXAMPLE
.\installer\build-native.ps1 -Architecture x64 -PackageDir .\dist\RightConvert
#>
[CmdletBinding()]
param(
    [ValidateSet('x64', 'ARM64')][string]$Architecture = 'x64',
    [string]$PackageDir
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$nativeRoot = Join-Path $projectRoot 'shell\windows11'
$output = Join-Path $nativeRoot ('out\' + $Architecture)

if (-not (Get-Command cmake -ErrorAction SilentlyContinue)) {
    throw 'CMake is required to build the native Explorer command.'
}
& cmake -S $nativeRoot -B $output -A $Architecture
if ($LASTEXITCODE -ne 0) { throw "CMake configure failed: exit code $LASTEXITCODE" }
& cmake --build $output --config Release --parallel 4
if ($LASTEXITCODE -ne 0) { throw "Native build failed: exit code $LASTEXITCODE" }

$dll = Join-Path $output 'Release\RightConvertExplorer.dll'
if (-not (Test-Path -LiteralPath $dll -PathType Leaf)) {
    throw "Expected native library not found: $dll"
}
Write-Host "Built native shell extension: $dll"

if (-not [string]::IsNullOrWhiteSpace($PackageDir)) {
    $destination = (Resolve-Path -LiteralPath $PackageDir -ErrorAction Stop).ProviderPath
    if (-not (Test-Path -LiteralPath (Join-Path $destination 'RightConvert.exe') -PathType Leaf)) {
        throw "The package directory must contain RightConvert.exe: $destination"
    }
    Copy-Item -LiteralPath $dll -Destination (Join-Path $destination 'RightConvertExplorer.dll') -Force
    Write-Host "Copied DLL next to RightConvert.exe: $destination"
}
