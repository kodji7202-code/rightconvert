# Stage 6: PDFium / Office dependency and redistribution audit

Audit date: **2026-10-13**. Scope: proprietary RightConvert Windows distribution, the official **pypdfium2 Windows x64 wheel**, and external LibreOffice/FFmpeg. This is a license and packaging inspection, **not** a PDF/Office feature test, installed application release, or legal opinion. No wheel was installed, no installer or release archive was rebuilt, and no customer artifact was approved as part of this audit.

## What was inspected

Official [PyPI release 5.14.0](https://pypi.org/project/pypdfium2/5.14.0/) (published 2026-10-04), inspected by fetching the PyPI release JSON and reading the original wheel ZIP in memory:

| Property | Windows x64 wheel evidence |
| --- | --- |
| Wheel | `pypdfium2-5.14.0-py3-none-win_amd64.whl` |
| Wheel SHA-256 (PyPI metadata) | `149fd5c6397b8df8bf7911a93506eff0be874f877afe7ac936cf5d37d21a6a06` |
| Wheel download bytes / members | 3,947,280 bytes / 71 ZIP members |
| Bundled PDFium binary | `pypdfium2_raw/pdfium.dll`, 7,381,504 uncompressed bytes |
| Metadata `License` | `BSD-3-Clause, Apache-2.0, dependency licenses` |
| Dist-info license members | **18** (3 direct package licenses + 15 PDFium/build-provider/dependency notices) |
| Current local development host | `importlib.metadata.distribution("pypdfium2")` raised `PackageNotFoundError`; the examined `pyproject.toml` did not yet declare pypdfium2 |

The following is the **exact notice set observed in this wheel**, relative to `pypdfium2-5.14.0.dist-info/licenses/`:

| Direct Python-package texts | PDFium binary/build texts under `data/windows_x64/BUILD_LICENSES/` |
| --- | --- |
| `LICENSES/Apache-2.0.txt` | `abseil.txt`, `agg23.txt`, `fast_float.txt` |
| `LICENSES/BSD-3-Clause.txt` | `freetype.txt`, `icu.txt`, `lcms.txt` |
| `LICENSES/CC-BY-4.0.txt` | `libjpeg_turbo.ijg`, `libjpeg_turbo.md` |
| | `libopenjpeg.txt`, `libpng.txt`, `llvm-libc.txt` |
| | `pdfium-binaries.txt`, `pdfium.txt`, `simdutf.txt`, `zlib.txt` |

The pypdfium2 binding's **Apache-2.0 OR BSD-3-Clause** license is compatible in principle with separately licensed proprietary applications that comply with its conditions. The **CC-BY-4.0** file covers pypdfium2 documentation/examples; carrying the actual wheel's whole license set is the conservative distribution choice. PDFium itself uses a BSD-style license, and the supplied `pdfium.txt` also includes Apache-2.0 terms. The upstream binary provider has its own `pdfium-binaries.txt` notice. Compiled dependencies such as FreeType (license alternatives), ICU, JPEG libraries, OpenJPEG and zlib have distinct terms. The upstream authors explicitly instruct distributors to carry **PDFium and dependency notices**. Do not describe all of `pdfium.dll` as being covered by one generic BSD license.

Do not infer that a different PDFium build has the same list, or that macOS/Linux/Win32/ARM64 wheels require the Windows x64 list. A custom built DLL may differ in compiled codecs, toolchain runtimes or notices; pypdfium2 notes that some builds use **libgcc**, which has separate conditions. Verify the license manifest and DLL provenance for **each selected wheel, ABI and revision**. Primary upstream sources: [pypdfium2 licensing](https://github.com/pypdfium2-team/pypdfium2#licensing), [PDFium upstream license](https://pdfium.googlesource.com/pdfium/+/refs/heads/main/LICENSE), [pypdfium2 build-license directory](https://github.com/pypdfium2-team/pypdfium2/tree/main/BUILD_LICENSES), [wheel distribution](https://pypi.org/project/pypdfium2/5.14.0/).

## Collector behavior and gate

`scripts/collect_licenses.py` continues to copy the exact build-environment Pillow, NumPy, PyInstaller, CPython and **all three cryptography** notices, plus existing OpenSSL text. It scans the built `dist/RightConvert/` directory for `pdfium.dll`.

- If **no PDFium DLL** is present, it records that fact in `ThirdPartyNotices/README.txt`; PDFium notices are not an unconditional dependency.
- If **any PDFium DLL** is present, it requires local installed pypdfium2 distribution metadata, an installed wheel-recorded `pypdfium2_raw/pdfium.dll`, and an exact matching DLL **SHA-256**. It also verifies the installed DLL against the original wheel `RECORD` SHA-256. An unmatched arbitrary/system PDFium DLL is a packaging failure, requiring a separate build-specific license assessment.
- It copies **every** wheel member listed under `*.dist-info/licenses/` in the original wheel `RECORD`, verifying each file's recorded SHA-256 and retaining its wheel-relative path under `ThirdPartyNotices/pypdfium2/`. This includes the complete `LICENSES/` directory and `data/windows_x64/BUILD_LICENSES/` on the inspected wheel. The collector fails if files are missing, empty, changed, or if the four core files `Apache-2.0.txt`, `BSD-3-Clause.txt`, `pdfium.txt`, `pdfium-binaries.txt` are absent. Reading raw `RECORD` matters because higher-level `importlib.metadata` file listings may silently omit a deleted dependency notice. A version/notice-count and binary hash entry is added to `ThirdPartyNotices/README.txt`.
- The collector reads metadata from the **build host**. A public release must also record and verify the exact **installed** pypdfium2 version, wheel URL and SHA-256, native DLL SHA-256, PyInstaller contents and component-level attributions. Its hash matching is provenance validation, not a complete legal review of transitive native source code.

The collector alone cannot make a current old installer compliant. It operates on the loose PyInstaller app folder. The current `scripts/build_setup.py` and `scripts/package_release.py` recursively package this folder; archives rebuilt *after* collection inherit nested notices. However, **`scripts/package_customer_preview.py` currently uses a fixed list of top-level notice filenames**, so it would omit the nested `pypdfium2/` notices from its separate customer ZIP. This is an unresolved **release blocker** when PDFium is bundled. `scripts/check_release.py` also does not yet assert conditional PDFium notice coverage. These files are outside this audit worker's edit scope and need their packaging owner to update them.

## LibreOffice / FFmpeg boundary

**LibreOffice:** The current design relies on a user's **separately installed local LibreOffice**, referenced through an explicit local executable path. The Document Foundation identifies [MPL-2.0](https://www.libreoffice.org/licenses/) and numerous additional components/permissions. There is no selected, frozen LibreOffice build or provenance chain to redistribute, so **do not copy LibreOffice binaries, modules, install packages or runtimes** into RightConvert. If that policy changes, create a separate full bundle audit; MPL source-code and notice obligations for covered modified files and third-party licenses must be assessed on the actual built installer. Merely invoking a user's existing executable is different from redistributing its copies.

**FFmpeg/FFprobe:** Continue to require the user's separately installed local tools for media routes. The host's Gyan full build has GPLv3-or-later flags; **no FFmpeg/FFprobe executable is bundled**. If a future installer starts shipping, proxying or downloading FFmpeg, its exact build options and relevant LGPL/GPL/other component terms must be reviewed independently.

**Proprietary app:** These third-party notices do not grant RightConvert ownership of PDFium, LibreOffice or FFmpeg. RightConvert's original owned code may remain closed-source, subject to its own seller identification, EULA, consumer terms and any separate upstream obligations.

## Packaging steps for a future approved PDF build

1. Select and lock the exact **Windows x64** pypdfium2 wheel and publish an internal build manifest recording its filename, index URL and SHA-256. Inspect its `*.dist-info/licenses/`, binary identity and toolchain dependencies again, even if reusing 5.14.0. Add any pinned dependency to the appropriate build configuration only after the PDF renderer has its own validated functionality.
2. Run the normal tests and `./build.ps1` in a dedicated release environment. The build invokes `python scripts/collect_licenses.py` after PyInstaller. When the bundle contains PDFium, confirm **18 nested notices for the inspected wheel** and that the collector's PDFium version, hashes and count match the actual installed distribution. If no PDFium library is packaged, the collector should report that PDFium is absent.
3. Stage the native shell DLLs as usual, then build a **fresh** setup via `python scripts/build_setup.py`. Recreate any intended release/customer ZIPs only after the notice collection. Update the customer-preview packer to include nested PDFium notices, and the release checker to require them when `pdfium.dll` appears. Do **not** publish the private developer source ZIP; it is currently an internal QA artifact.
4. Inspect `build/setup/RightConvertPayload.zip` for `app/ThirdPartyNotices/pypdfium2/` entries and any PDFium DLL; inspect the final setup payload and every downloadable customer archive for the same complete content. Run `python scripts/check_release.py` once conditional checks have been implemented, plus `python scripts/generate_sbom.py --summary` and manually verify the PDFium license count and DLL hash until its rules are extended.
5. Confirm binary redistribution entitlements (including Microsoft runtime/DLL provenance), supplier attribution, finalized legal seller/EULA, signing and clean Windows installation/uninstallation, and **actual PDF/Office conversions** with source integrity/cancellation/offline acceptance before labeling these routes supported or offering commercial downloads.

**Local validation:** An isolated test downloaded the real official 5.14.0 Windows x64 wheel into a temporary folder, staged a matching PDFium DLL with a test-only placeholder application, and ran the collector. All **18** wheel notices were copied byte-for-byte and all three `cryptography` notices were retained. Negative tests confirmed rejection of a modified PDFium DLL and a deliberately deleted `freetype.txt` despite the package metadata API's missing-file omission. A bundle with no PDFium correctly skipped PDFium notice collection. No host package was installed and temporary files were removed.

**Status at this audit:** collector support and a reproducible upstream wheel inventory are available; **there is no evidence yet of an installed pypdfium2 wheel for RightConvert, successful PDF/Office conversion, a rebuilt PDF-enabled setup, or complete notices in the customer-preview ZIP**. Release sign-off remains pending the above gates.
