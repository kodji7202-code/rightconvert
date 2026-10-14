# RightConvert Stage 6 — independent adversarial PDF/Office QA

**Follow-up after this independent snapshot, same date:** The six native Office→PDF routes were subsequently exercised with a verified official LibreOffice 26.8.1.1 MSI administratively extracted into `build/stage6/libreoffice-isolated` (no global installation). The Office provider's native tests passed **6/6**, and the independent Stage6 engine matrix passed **6/6 Office routes plus 12/12 PDF/image routes and 1/1 image merge**, preserving hashes, collision sentinels and temporary cleanup. See [STAGE6_FORMAT_MATRIX.md](STAGE6_FORMAT_MATRIX.md), [STAGE6_FORMAT_MATRIX_RESULTS.json](STAGE6_FORMAT_MATRIX_RESULTS.json) and [STAGE6_RELEASE.md](STAGE6_RELEASE.md). **The two skips below describe this earlier adversarial test module's environment; they do not override the separate later native acceptance.** Neither run proves a disconnected-VM network packet trace, native Win10 support or a signed commercial installer.

**Date:** 2026-10-13  
**Scope:** proprietary optional document backends, hostile PDF and Office files, publication and cancellation safety. The independent QA worker added only `tests/test_stage6_document_adversarial.py` and this report. It did not modify conversion engines, formats, provider code, registry, installer, firewall or network settings.

## Evidence and verdict

On the tested Windows 11 build 26200 (Python 3.12.10, Pillow 11.2.1, `pypdfium2` 5.14.0), the latest isolated Stage 6 run completed:

```powershell
cd D:\fuckpdf
python -B -m pytest -q -rs -p no:cacheprovider tests/test_stage6_document_adversarial.py
```

**Result: 33 passed, 2 skipped, 0 failed in 2.37 s.** These are actual tests of the current Python source. The available `pypdfium2` binding executes real local PDF rendering; Pillow reopens and verifies its PNG, JPG, WEBP and multipage TIFF results. The one PDF password-encryption fixture uses installed PyMuPDF (`fitz`) as a **test-only** producer. That library is not a RightConvert document backend or an app dependency, and its individual test skips if PyMuPDF is absent.

`find_libreoffice()` returned **`None`** and `Get-Command soffice,soffice.com,libreoffice` found no executable on the test machine. The **two skips** are the genuine LibreOffice-dependent DOCX-to-PDF round-trip and the Office job cancellation at the publication boundary. No real native LibreOffice export was demonstrated. Passing Office ZIP validation tests must not be presented as passing Office document conversion.

This result is **conditional PDF conversion security acceptance in the source environment**, with native Office export and release-package assurance outstanding. All generated fixtures live under pytest disposable temporary folders; they do not use personal documents or modify Windows shell registration.

## Real PDF rendering and output semantics

| Requirement | How tested | Result |
| --- | --- | --- |
| Real multipage PDF to PNG pages | Generate a two-page, classic-xref PDF with distinct page contents. Call `convert_file(pdf, "pngzip", ...)`. Independently inspect ZIP entries and decode each PNG with Pillow; ensure two unique frames in page order. | **PASS**. Public `pngzip` route emits **`<source>-pages.zip`** (including `page-0001.png`, `page-0002.png`). A collision emits `<source>-pages (1).zip`. `pngzip` is an internal target name, **not** the output filename extension. |
| Multipage PDF single-image loss | Inspect a two-page PDF through `get_targets()`. | **PASS**. Individual `png`/`jpg`/`webp` targets are absent; `pngzip` is present. |
| Multipage PDF to TIFF | Convert a two-page PDF to TIFF and use Pillow to read both frames. | **PASS**, 2 frames preserved. |
| Single-page raster | Convert a one-page synthetic PDF to `png`, `jpg` and `webp`. Independently decode each result, checking actual encoded format. | **PASS**, all three routes. |
| Encrypted PDF | Generate an AES-256-encrypted PDF using PyMuPDF; attempt advertised routes and conversion. | **PASS**, rejected without publication. Test-only PyMuPDF is optional. |
| Invalid/oversized PDF | Fake or truncated PDF; PDF with **65 pages**; one page with **15,000 pt** edges. | **PASS**, rejected before rendering/output; tests exercise page/geometry bounds without allocating giant raster buffers. |
| Cancellation mid-render | Set cancellation after the first progress event of a three-page PDF. | **PASS**, `ConversionCancelled` and no partial ZIP or staging files. |
| Cancellation just before commit | Set cancellation inside a wrapper of the guarded `_publish_output` for two-page PDF ZIP. | **PASS**, canceled without a published file. |
| Atomic output and Unicode | Source `Счёт ședință 中文.pdf`; existing ZIP sentinel in destination; convert twice and inject `OSError(ENOSPC)` into `_publish_output` on the second conversion. | **PASS**, first new output uses collision suffix; original and existing files keep their SHA-256; injected failure publishes nothing and leaves no owned staging directory. This is simulated disk-full **at publish**, not actual storage exhaustion. |
| PDF action-based external URL | Embed URI link annotation pointing to an ephemeral `127.0.0.1` listening port; render PDF; attempt `accept()` afterward. | **PASS**, no TCP connection observed to this precise test endpoint. |

PDF source content and both pre-existing destination and newly converted outputs are checked separately. ZIP archive members are non-traversing names, have the expected number of pages, decode as PNG and have distinct digests for PDFs whose visible content differs. The public `convert_file() -> Path` contract remains single-output; `pngzip` is a ZIP containing all rendered page images, avoiding partial sets of independently committed page files.

## Office archive and active-content rejection

The Office-specific safety tests exercise the real `rightconvert.office_provider.inspect_office()` scanner **directly**, whether or not the optional LibreOffice executable exists. For malicious fixtures, they also verify that `get_targets()` does not advertise `pdf`, `convert_file(..., "pdf", ...)` raises `ConversionError`, source SHA-256 and a pre-existing output sentinel stay unchanged, and no final/staging files appear.

| Attack / property | Synthetic fixture | Result |
| --- | --- | --- |
| Valid passive Office identification | Structurally inspectable DOCX and ODT ZIP packages with content manifests and local parts | **PASS**: detected as `docx` and `odt` respectively. This does **not** prove LibreOffice can render them. |
| Wrong signatures / corrupt ZIP | Fake PK header and truncated OOXML archive | **PASS**, rejected. |
| ZIP archive traversal | `../outside.xml`, `/absolute.xml`, `word/../../outside.xml` | **PASS**, rejected; no path extracted or written outside temporary directory. |
| ZIP symlink | UNIX symlink entry in OOXML archive | **PASS**, rejected. |
| Case-insensitive duplicate | Two entries targeting `word/document.xml` and `WORD/document.xml` | **PASS**, rejected. |
| Too many ZIP members | 520 added inert entries, exceeding 512-member guard | **PASS**, rejected. |
| Compression-expansion attack | A highly compressible **128 MiB uncompressed** XML member in a ZIP smaller than 1 MiB | **PASS**, rejected by metadata limits without expanding to a directory or running LibreOffice. |
| XML DTD/entities | An OOXML member containing DOCTYPE/ENTITY declaration | **PASS**, rejected. |
| Disguised macros | Macro-enabled content type and VBA payload hidden under `.docx` filename | **PASS**, rejected. |
| Embedded executable/object content | OOXML entries in `word/embeddings/` or `xl/vbaProject.bin` | **PASS**, rejected. |
| External Word relationship | DOCX hyperlink relationship with `TargetMode="External"` and a loopback HTTP URL | **PASS**, rejected before conversion; **zero listener connections** during the check. |
| External ODF link | Passive ODT structure carrying an `xlink:href` HTTP link | **PASS**, rejected. |
| Spreadsheet external formula | XLSX worksheet containing `<f>WEBSERVICE("http://127.0.0.1:16666/private")</f>` | **PASS after fix**, rejected by scanner. |

### Confirmed defect and remediation

During the independent run, the XLSX `WEBSERVICE(...)` fixture **actually passed** `inspect_office()`, returning `"xlsx"`. The first regression run reported **1 failed, 20 passed, 2 skipped**. The root cause was checking XML **attributes** for dangerous formulas while ordinary OOXML worksheet formulas reside as **text of `<f>` elements**. This could have exposed a future LibreOffice-converted spreadsheet to external-resource evaluation.

The finding was escalated to the main implementation agent. It updated `rightconvert/office_provider.py` so that OOXML spreadsheet `<f>` elements and formula attributes are rejected, including benign formulas for now because a safe formula allowlist has not been proven. Our unchanged malicious XLSX case subsequently passed; the expanded Stage 6 suite returned **33 passed, 2 skipped**. This is a deliberate conservative limitation: **spreadsheets containing formulas are presently unsupported by the passive-only Office policy**, and the product must explain that limitation to buyers rather than silently flattening or recalculating them.

## Security review of the current architecture

1. **Input inspection:** PDF header, PDFium page count, page geometry and encryption checks are performed before rendering. Existing provider limits at review include a maximum **64 PDF pages**, **25 million pixels/page**, **90 million pixels total** and a **256 MiB PDF source**. The deterministic large-page test confirms that the pre-render limit rejects an unsafe PDF. ZIP inspections verify package members, names, per-entry size/ratio, total unpacked size, CRC and XML safety; no `extractall()` of hostile archives is used.
2. **Offline controls:** Office scanner refuses hyperlinks, external relationships, embedded active objects and formulas. LibreOffice invocation, when installed, uses an absolute executable, a private job profile, argument-array execution with `shell=False`, explicit PDF export filters, time limits and a private output directory. **Static rejection is not equivalent to observing a native LibreOffice process with network tracing.** The provided loopback checks exercise specific URLs only, not all possible DNS, HTTP or system calls.
3. **Publication:** PDF/Office backends prepare private results, copy them to the application's already-open staging descriptor, then publish via `_publish_output()` without replacing an existing destination. Verified tests exercise collisions, cancellation and injected publication failure on the real PDF path. Office's equivalent path cannot be tested end-to-end until a real LibreOffice build is available.
4. **Native parser isolation:** PDFium renders untrusted PDFs inside the RightConvert process. Byte, page and pixel limits reduce resource exposure, but they are not a process sandbox for a native PDF library crash or a previously unknown vulnerability. Isolation in a child process with resource limits and crash containment is a recommended hardening step before broad untrusted-document distribution.
5. **Dependency provenance:** The tested source environment has `pypdfium2` 5.14.0, Pillow 11.2.1 and optional test-only PyMuPDF. This confirms functionality in this environment only. The exact frozen Windows installer and every PDFium/Pillow native DLL still require packaging/version/hash/license/SBOM checks before retail release.

## Remaining release blockers and exact checks

- **Real LibreOffice on Windows:** install or point to a versioned, trusted `soffice.exe`/`.com`; run DOCX, XLSX, PPTX and ODT/ODS/ODP conversion acceptance on legitimate passive documents. Verify actual resulting pages, Unicode, source SHA, collision handling, cancel, timeout, process descendants, profile cleanup, font/layout differences and missing-feature messages. The two current test skips must become real passes. Other Office family fixtures currently need integration acceptance beyond DOCX/ODT sniff tests.
- **Network observation for Office:** use a controlled Windows VM with process/network capture to open explicitly linked/macro-enabled samples and permitted documents. Confirm LibreOffice process and children issue no DNS/HTTP/TCP requests under its isolated profile; the current loopback-only negative tests do not establish a universal offline guarantee.
- **PDF parser hardening:** include malformed cross-reference, signatures, form/XFA and image-heavy PDFs, high page counts, metadata, rotation, real-world PDFs, cancellation under expensive renders and bounded parallel jobs. Run memory/CPU monitoring and PDFium crash containment checks in an isolated VM. This suite verifies small, deterministic synthetic PDFs and selected rejection limits; it does not establish arbitrary-payload fuzzing coverage.
- **Shipped binary:** repeat the matrix on the actual rebuilt `RightConvert.exe` and clean Windows 10/11 x64 VMs, including machines without optional providers, and verify right-click menu discovery, installed DLLs, third-party notices and no outbound traffic. None of these packaging/UI/native VM checks was performed by this QA worker.

No native LibreOffice export, independent full packet capture, Windows 10 VM, commercial package installation, Office layout fidelity or adversarial native PDFium fuzzing is claimed in this report.
