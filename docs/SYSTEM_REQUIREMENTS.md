# RightConvert v0.3.0 private candidate — Windows requirements and installation guide

**Release status (13 October 2026):** private, locally tested Windows build under preparation for a **proprietary / closed-source commercial launch**. The product owner's source-licensing direction has been selected; the legal licensor/publisher identity, binding EULA, customer acceptance flow and commercial release approval **are not final**. This guide is technical documentation, not a software license. See [EULA_DRAFT.md](EULA_DRAFT.md), [COMMERCIAL_RELEASE_CHECKLIST.md](COMMERCIAL_RELEASE_CHECKLIST.md) and [LICENSES.md](LICENSES.md).

## Computer requirements

| Item | Requirement |
| --- | --- |
| Operating system | Windows 10 or Windows 11, 64-bit x64. **Actual native Explorer/UI testing was performed only on Windows 11 Pro build 26200.** |
| Permissions | A normal user account and a writable `%LOCALAPPDATA%` folder. Classic menu integration installs per user, without administrator rights. |
| PowerShell | Windows PowerShell 5.1 for installer scripts; shipped Windows 10/11 normally provide it. |
| Python | **Not required** to run the installer or packaged application. Python 3.12 and build tools are required only for developing/rebuilding from source. |
| Image conversion | Packaged Pillow supports compatible static raster JPG, PNG, WebP, BMP, TIFF and single-frame GIF **inputs**. The image output families are JPG, PNG, WebP, BMP and TIFF; some target combinations are intentionally refused to avoid silent data loss. |
| PDF conversion | Bundled pypdfium2/PDFium required for reading/rasterizing PDFs; Pillow writes image→PDF locally. One-page PDFs may output PNG/JPG/WebP; multiple pages remain intact via PNG page ZIP or multipage TIFF. Input/page/pixel limits apply. No PDF→editable Word/OCR conversion. |
| Office conversion | Optional trusted local LibreOffice 26.x (or validated compatible version) provided separately; only passive DOCX, XLSX, PPTX, ODT, ODS, ODP → PDF when selected and validated. Macro/embedded/external-link and spreadsheet-formula files are rejected; PDF layout/fidelity depends on fonts and the installed LibreOffice version. |
| Audio/video | Independently installed and locally executable **ffmpeg.exe and ffprobe.exe**, preferably together in a trusted absolute directory; the required encoders must be present. Neither executable is bundled or downloaded by RightConvert. |
| Free disk space | Allow approximately **500 MB or more free** on the system volume for temporary installer extraction, installed app and updates, **plus enough space on the chosen output drive for converted results**. This is a planning recommendation, not a measured hard minimum; large videos can require many GB. |
| Memory | No formally measured minimum; large images/media need substantially more memory and storage than tiny sample files. Close other memory-heavy programs if conversion fails with resource errors. |
| Internet | No connection is required for the normal local conversion workflow. The app has no cloud conversion or telemetry feature. An offline-protocol/loopback regression passed, but a full disconnected-host packet-capture test of the packaged app remains outstanding. |
| Windows Explorer | The standard-user **classic** Windows 11 command was tested in native Explorer, including Unicode selection and batch conversion in one GUI. It appears under **Show more options**. Windows 10 Explorer activation remains untested. The first-level **modern** Windows 11 command requires a separately signed, trusted sparse MSIX and has **not** passed end-to-end native activation. |

## Format support in v0.2.0

The v0.2 implementation recognizes **16 source families**, based on file contents rather than extension alone, with **57 directed routes independently tested** using small, valid fixture files. The count describes the tested *conditional format matrix*, **not** 57 guaranteed conversions for every installation or input. Available targets depend on source properties, stream types, external media binaries and installed encoders.

| Family | Accepted inputs | Output families |
| --- | --- | --- |
| Static raster images (6) | JPG, PNG, WebP, BMP, TIFF (single-page), GIF (single-frame) | JPG, PNG, WebP, BMP, TIFF where compatible |
| Audio (5) | MP3, WAV, FLAC, Ogg/Vorbis, Ogg/Opus | MP3, WAV, FLAC, Ogg/Vorbis, Opus subject to FFmpeg encoders |
| Video (5) | MP4, WebM, MOV, AVI, MKV | MP4, WebM, MKV subject to FFmpeg encoders |

**Explicit exclusions:** PDF conversion/rendering and Office formats (DOCX, XLSX, PPTX etc.) are **not supported** in v0.2. Animated/multipage images, unsupported audio/video streams and unavailable codecs are rejected rather than silently reduced or lost. MOV and AVI are input families, **not** output options; GIF is input-only. Some conversions are lossy; the original files are preserved by the engine's tested no-replace workflow.

## Stage 6 document support — v0.3.0 private candidate

The v0.2 57-route record above is **historical evidence**, not the complete current developer build. Stage6 adds genuine offline PDF page rendering and image→PDF/multipage merge. A PDF source may be exported as one image only when it has exactly one page; otherwise choose a full-page PNG ZIP or multipage TIFF. The app offers Office→PDF only when its strict DOCX/XLSX/PPTX/ODT/ODS/ODP content validator accepts the file **and** a locally installed/explicitly selected LibreOffice backend is present. No macros, external-resource links, formula-bearing spreadsheets, embedded OLE or silently dropped PDF pages are supported. The official LibreOffice MSI has been unpacked into an *isolated test workspace* for native QA; this is **not** a user-system LibreOffice installation and is **not shipped**.

Consult [STAGE6_FORMAT_MATRIX.md](STAGE6_FORMAT_MATRIX.md) and [STAGE6_PDF_OFFICE_QA.md](STAGE6_PDF_OFFICE_QA.md) for the specific actual passed conversions. Stage6 cannot be treated as Windows10, MSI/MSIX or commercial-license acceptance. A legal/publisher code signature and final product license/purchase process remain absent.

Evidence: [STAGE5_FORMAT_MATRIX.md](STAGE5_FORMAT_MATRIX.md) (57/57 tested routes), [STAGE5_FORMAT_MATRIX_RESULTS.json](STAGE5_FORMAT_MATRIX_RESULTS.json) and [STAGE5_RELEASE.md](STAGE5_RELEASE.md). The former [FORMAT_MATRIX.md](FORMAT_MATRIX.md) and `scripts/validate_stage4.py` describe the narrower **v0.1/Stage 4 historical acceptance**, not the current feature set.

## Install and use (Romanian interface)

1. Open `RightConvertSetup.exe` from the Windows release package. Click **Instalează**, wait for completion, and optionally **Deschide RightConvert**.
2. In Windows Explorer, select one or more files. On Windows 11 use **click dreapta → Show more options → Convert with RightConvert** to inspect/edit the batch in the GUI. **Stage 7 private candidate** also adds verified flat classic commands such as **Convert to webp**, **Convert to jpg**, **Convert to pdf**; they launch local conversion directly, without the GUI, and show a dialog on failure. The earlier nested quick-format cascade was removed after repeated Windows 11 rendering failures. The Stage 7 direct command was tested with an actual Explorer click and an original-preserving conversion; Windows 10 activation and the Windows 11 first-level modern menu remain unverified. The already-issued v0.3 ZIP may predate the separate Stage 7 candidate; do not assume it has these commands.
3. RightConvert inspects each file's actual contents and lists compatible targets. Select a target per row (double-click, F2/Enter) or a common format for the queue.
4. Keep **În folderul fișierului original** enabled to put results beside originals, or uncheck it and use **Alege folder…** to select an existing destination. The engine and saved configuration have passed independent Unicode custom-folder tests; the full choose-folder GUI path was not separately documented as a native click test.
5. Click **Convertește**. Monitor statuses and progress. **Anulează** stops ongoing work and removes the owned temporary output. Existing result names are protected: `name.webp`, `name (1).webp`, etc.
6. For audio/video provide separately installed FFmpeg **and** FFprobe, using **⚙ Motor FFmpeg** when needed. For Office→PDF, select your trusted local LibreOffice command in **Motor Office** if it is not found in PATH. PDF rendering needs no LibreOffice, while PDF→Word editable is intentionally unavailable. External FFmpeg and LibreOffice are not installed or downloaded by RightConvert itself.

## Uninstall

Use **Settings → Installed apps → RightConvert → Uninstall**, or run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\installer\uninstall.ps1"
```

The installer removes its own Explorer menu/COM registrations and its application directory. **Settings and technical logs are intentionally preserved by default**. To request their removal too:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\installer\uninstall.ps1" -PurgeUserData
```

When Explorer holds the classic DLL loaded, uninstall can schedule guarded cleanup at the next Windows sign-in without terminating Explorer. During reinstall, an old locked DLL may remain in a separately owner-marked backup for safe deferred *backup-only* cleanup; it must not trigger the removal of a newly installed app. The **specific historical unmarked Stage 3 two-DLL backup on the test host was independently identified and safely cleaned after its handles were released**; it is **not an outstanding blocker**. Do not delete arbitrary backup directories. See [STAGE5_RELEASE.md](STAGE5_RELEASE.md) for the current state and `installer/README.md` for recovery tooling.

## Rebuild from source (developer)

```powershell
python -m pip install -e . pytest pyinstaller
.\build.ps1
.\installer\build-classic.ps1 -Architecture x64 -PackageDir '.\dist\RightConvert'
.\installer\build-native.ps1 -Architecture x64 -PackageDir '.\dist\RightConvert'
python scripts\validate_stage6.py
python scripts\build_setup.py
python scripts\package_release.py
python scripts\check_release.py
```

Native Shell compilation additionally requires Visual Studio C++ Build Tools, Windows SDK and CMake. Signed Windows 11 MSIX registration needs a real signing certificate with trusted chain. See `installer/README.md` for exact prerequisites.

For current document candidate acceptance use [STAGE6_FORMAT_MATRIX.md](STAGE6_FORMAT_MATRIX.md) and [STAGE6_PDF_OFFICE_QA.md](STAGE6_PDF_OFFICE_QA.md). The 57-route [STAGE5_FORMAT_MATRIX.md](STAGE5_FORMAT_MATRIX.md) report is the validated **historical v0.2** media/image baseline; [FORMAT_MATRIX.md](FORMAT_MATRIX.md), [STAGE4_RELEASE.md](STAGE4_RELEASE.md) and `scripts/validate_stage4.py` preserve the still older **v0.1** acceptance.
