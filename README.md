# RightConvert

**Open-source Windows file converter · MIT License · Python 3.10+**

RightConvert converts local files and integrates with the classic Windows
File Explorer right-click menu. It operates locally: no cloud file conversion,
account or telemetry service is required for normal use.

**Project stage:** v0.3.0 source release. Windows 11 x64 classic Explorer
integration has undergone local QA. Clean Windows 10 installs, SmartScreen
behavior across other PCs and native first-level Windows 11 shell integration
are not guaranteed. This is open-source source code, **not** a claim that the
currently available unsigned binary has been approved for all environments.

## Features

- Local conversion of commonly used images: JPG, PNG, WebP, BMP, TIFF,
  single-frame GIF input; options vary with source contents.
- PDF to compatible image output (single-page) or multi-page TIFF/PNG ZIP;
  compatible opaque images or 2–64 images to PDF.
- Audio and video conversions with **separately installed FFmpeg + FFprobe**,
  supporting compatible containers/codecs; not every file is convertible.
- DOCX/XLSX/PPTX/ODT/ODS/ODP to PDF when **LibreOffice is installed or
  configured separately**. Macros, formula-containing spreadsheets, external
  links and active/embedded content are deliberately rejected.
- Batch conversion, folder imports, presets, cancellation, safe output
  publication that seeks to leave source files untouched.
- The classic **Convert with RightConvert** context-menu command in Windows 11
  under **Show more options**. Experimental modern-menu/MSIX integration is
  not the default installation method.

## Run from source

On Windows with Python 3.10+, from a PowerShell terminal:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\python.exe -m rightconvert
```

Or use `python launch.py` after installing dependencies. Some media and Office
features additionally require independent FFmpeg/FFprobe and LibreOffice.

## Build the Windows app / installer

Windows build tools and Python dependencies are required.

```powershell
python -m pip install -e ".[test]" pyinstaller
python -m pytest -q
.\build.ps1
python scripts/build_setup.py
```

The frozen app is generated as `dist\RightConvert\RightConvert.exe`, and
the standalone graphical installer as `dist\RightConvertSetup.exe`. Neither
generated binary nor a release ZIP is committed to this repository.

To install the classic right-click command from a built application:

```powershell
.\installer\install.ps1
```

To remove it:

```powershell
.\installer\uninstall.ps1
```

**Security note:** Depending on your machine, an unsigned executable may be
blocked by Windows SmartScreen, Defender or application-control policies. Do
not disable security protection to run an untrusted installer. Build from
source or wait for a verified release.

## Development

- Python source: `rightconvert/`
- Windows Explorer native components: `shell/classic/`, `shell/windows11/`
- Installer source: `installer/`, `scripts/build_setup.py`
- Tests: `tests/`
- Format matrices and technical notes: `docs/`

Run `python -m pytest -q`. Tests requiring native Windows integration,
installed FFmpeg or LibreOffice may skip according to environment.

## Open source license

**RightConvert first-party source code is licensed under MIT.** See [LICENSE](LICENSE).
You may use, modify, redistribute, and sell copies of the licensed code,
provided the copyright and permission notices are retained. Optional
third-party runtime dependencies are governed by their **own** licenses and
notices; see [THIRD_PARTY.md](THIRD_PARTY.md). FFmpeg and LibreOffice are
**not bundled** in this source distribution.

This repository intentionally **does not include** the separate RightConvert
website, Cloudflare D1/R2/Turnstile beta administration, private beta tester
records, signing keys, build outputs, or unapproved installer packages.

Project website: https://rightconvert-beta-site.tradingagents-mt5-cloudflare-pages.workers.dev/

## Contributions

Bug reports and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).
Please do not upload confidential files or customer documents in issues.
