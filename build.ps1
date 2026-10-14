param([switch] $SkipTests)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not $SkipTests) { python -m pytest -q; if ($LASTEXITCODE -ne 0) { throw 'Tests failed' } }
python scripts/make_icon.py
if ($LASTEXITCODE -ne 0) { throw 'Icon generation failed' }
# Keep only the public-key verifier in frozen code. The private license
# issuer (scripts/issue_license.py) stays outside every customer build.
# The PyInstaller pypdfium2/PDFium hooks collect the native DLL that the
# runtime needs. Avoid --collect-all here: it copies unnecessary .py source
# files into a nominally binary-only customer payload. Exact legal notices
# are copied and checked separately by scripts/collect_licenses.py.
# LibreOffice and FFmpeg remain external, never bundled.
python -m PyInstaller --noconfirm --clean --windowed --onedir --hidden-import rightconvert.licensing --hidden-import rightconvert.pdf_provider --hidden-import rightconvert.office_provider --name RightConvert --icon assets/RightConvert.ico --paths . launch.py
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed' }
python scripts/collect_licenses.py
if ($LASTEXITCODE -ne 0) { throw 'Third-party license notice collection failed' }
# Retain the legacy GUI quick-target marker only for backwards compatibility.
# The Stage7 native flat Explorer commands require the new --direct CLI marker.
$quickMarker = Join-Path (Join-Path $PSScriptRoot 'dist\RightConvert') 'rightconvert-quick-cli-v1.marker'
Set-Content -LiteralPath $quickMarker -Value 'rightconvert-quick-cli-v1' -Encoding Ascii
$directMarker = Join-Path (Join-Path $PSScriptRoot 'dist\RightConvert') 'rightconvert-direct-cli-v2.marker'
Set-Content -LiteralPath $directMarker -Value 'rightconvert-direct-cli-v2' -Encoding Ascii
Write-Host 'Built: dist/RightConvert/RightConvert.exe'
