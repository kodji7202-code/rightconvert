param([switch]$SkipTests)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).ProviderPath
Push-Location -LiteralPath $root
try {
    # Order matters: PyInstaller --clean replaces the entire app folder;
    # native COM DLLs MUST be copied into it *after* the Python freeze.
    & .\build.ps1 -SkipTests:$SkipTests
    if (-not $?) { throw 'Python application build failed' }
    & .\installer\build-classic.ps1 -Architecture x64 -PackageDir '.\dist\RightConvert'
    if (-not $?) { throw 'Classic Explorer COM build failed' }
    & .\installer\build-native.ps1 -Architecture x64 -PackageDir '.\dist\RightConvert'
    if (-not $?) { throw 'Windows 11 modern COM build failed' }
    foreach ($name in @('RightConvertClassic.dll','RightConvertExplorer.dll')) {
        if (-not (Test-Path -LiteralPath (Join-Path 'dist\RightConvert' $name))) {
            throw ('Missing native shell DLL in frozen application: ' + $name)
        }
    }
    & ctest --test-dir .\shell\classic\out\x64 -C Release --output-on-failure
    if ($LASTEXITCODE -ne 0) { throw 'Classic COM CTest failed' }
    & ctest --test-dir .\shell\windows11\out\x64 -C Release --output-on-failure
    if ($LASTEXITCODE -ne 0) { throw 'Modern COM CTest failed' }
    & python scripts\validate_stage5.py
    if ($LASTEXITCODE -ne 0) { throw 'Actual conversion matrix failed' }
    & python scripts\build_setup.py
    if ($LASTEXITCODE -ne 0) { throw 'Frozen standalone Setup failed' }
    & python scripts\package_release.py
    if ($LASTEXITCODE -ne 0) { throw 'Private QA source/binary ZIP packaging failed' }
    & python scripts\check_release.py
    if ($LASTEXITCODE -ne 0) { throw 'QA release CRC/SHA/contents verification failed' }
    & python scripts\package_customer_preview.py
    if ($LASTEXITCODE -ne 0) { throw 'Binary-only unsigned customer preview failed' }
    Write-Host 'PASS: RightConvert commercial PREVIEW build, conversion matrix, COM, QA archives, and NOT-FOR-SALE customer archive.'
} finally {
    Pop-Location
}
