# Microsoft Store (full MSIX) build

The Store build is a **full MSIX package that contains the application
binaries**. It is separate from the non-Store distribution (Setup EXE +
per-user registry menu + optional identity-only *sparse* MSIX in
`shell/windows11/`), which is unchanged. Sparse packages cannot be published
in the Store.

| Item | Value (Partner Center, use verbatim) |
| --- | --- |
| Package/Identity/Name | `Kodji.RightConvert` |
| Package/Identity/Publisher | `CN=E1377659-5DF2-4C93-A649-DE2676D88DD2` |
| PublisherDisplayName | `Kodji` |
| DisplayName | `RightConvert` |
| Package family name | `Kodji.RightConvert_h9nm9abegh7ny` |
| Store ID | `9NJLJ335RDFM` |
| AUMID | `Kodji.RightConvert_h9nm9abegh7ny!RightConvert` |

Files: `msix-store/AppxManifest.xml` (manifest), `msix-store/make_assets.py`
(tile/icon PNGs from the original artwork), `msix-store/build-store-msix.ps1`
(layout + `makepri` + `makeappx` + `.msixupload`), CI in
`.github/workflows/store-msix.yml`.

## What the package contains

* `RightConvert.exe` one-dir PyInstaller app, `EntryPoint="Windows.FullTrustApplication"`,
  restricted capability `runFullTrust`, Windows 10 2004 (19041)+ desktop, x64.
* Start menu entry (default `AppListEntry`) with scaled, target-size and
  unplated icons (`resources.pri`).
* Command-line alias `RightConvert.exe` (`uap3:AppExecutionAlias`).
* Windows 11 first-level File Explorer command: the same
  `RightConvertExplorer.dll` / CLSID `AA7FB3F0-5E6C-4D75-A22F-C0A8D7A63211`
  as the sparse package, declared with `com:SurrogateServer` +
  `desktop4/desktop5:FileExplorerContextMenus`. Behaviour of this packaged
  verb on Windows 10's Explorer has not been verified.
* **Not** included: `RightConvertClassic.dll`, the PowerShell installer and the
  Setup EXE. Packaged installs are owned by Windows; nothing writes HKCU.

## Runtime behaviour when packaged

* `rightconvert.runtime.is_packaged()` (via `GetCurrentPackageFullName`) is
  true inside the package. The Setup EXE refuses to run with package identity.
  The application itself never writes registry verbs.
* Settings and the technical log stay in `%LOCALAPPDATA%\RightConvert`;
  Windows transparently redirects a packaged app's new AppData files to
  `%LOCALAPPDATA%\Packages\Kodji.RightConvert_h9nm9abegh7ny\LocalCache\Local\RightConvert`
  and removes them on uninstall. Converted files are written next to the
  originals or in the chosen output folder, as before (not virtualized).
* **LibreOffice** (optional, never bundled): discovery order is the folder or
  file chosen in the app → absolute `PATH` entries → the official installer's
  `HKLM\SOFTWARE\LibreOffice\UNO\InstallPath` → `%ProgramFiles%\LibreOffice\program`.
  A full-trust packaged app reads HKLM and Program Files normally, so a
  system-installed LibreOffice is found. Without it, Office→PDF targets are
  simply not offered (the app degrades gracefully, other conversions work).
  FFmpeg/FFprobe remain external in the same way.
* Diagnostics without GUI: `RightConvert.exe --self-check report.json` writes
  package identity, data folder and LibreOffice discovery result.

## Build locally (Windows, SDK + CMake + Python 3.12)

~~~powershell
python -m pip install -e ".[test]" pyinstaller
.\installer\build-native.ps1 -Architecture x64        # RightConvertExplorer.dll
.\build.ps1 -SkipTests                                # dist\RightConvert
.\msix-store\build-store-msix.ps1 -AppDirectory .\dist\RightConvert -Version 1.0.0.0
# optional local sideload test (self-signed, Publisher-matching cert):
.\msix-store\build-store-msix.ps1 -AppDirectory .\dist\RightConvert -TestSign
Import-Certificate .\msix-store\out\RightConvert-StoreTest.cer -CertStoreLocation Cert:\LocalMachine\TrustedPeople  # elevated
Add-AppxPackage .\msix-store\out\RightConvert_1.0.0.0_x64.test.msix
~~~

Outputs in `msix-store\out`: `RightConvert_<ver>_x64.msix` (unsigned) and
`RightConvert_<ver>_x64.msixupload` (**the file to upload**). Never upload the
`.test.msix`.

## CI

`Store MSIX` workflow (push to `main`, or *Run workflow* with a `version`
input): builds DLL + app, packs, validates the manifest identity and contents,
sideloads the test-signed copy, launches it by AUMID (`--self-check`, GUI
start, `--direct` PNG→WebP) and through the execution alias, probes the
packaged COM class, uninstalls, runs the Windows App Certification Kit when the
runner has it, and uploads the artifact **`RightConvert-Store-msixupload`**.

## Submitting (Partner Center, by the account owner)

1. Bump the version for every new submission: `Major.Minor.Build.0`
   (revision must stay 0; the Store rejects a version not higher than the last).
2. Download the `RightConvert-Store-msixupload` artifact, unzip the GitHub
   artifact wrapper, and upload the inner `.msixupload` under **Packages**.
3. **runFullTrust** is a restricted capability: Partner Center asks for a
   justification (text in `docs/STORE_LISTING_DRAFT.md`).
4. Fill in age rating (IARC questionnaire), privacy policy URL, listing text
   and screenshots (at least one 1366×768+ PNG).
5. Partner Center signs the package; do not sign the upload yourself.
