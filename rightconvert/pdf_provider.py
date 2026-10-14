"""Bounded, offline PDF rendering and image-to-PDF staging helpers.

The caller owns the private staging directory and the final no-replace publish.
Nothing here selects a user-facing destination or writes to an input file.

Optional renderer: pypdfium2 (BSD-3-Clause/Apache-2.0 distribution, including
PDFium and bundled third-party license notices). The commercial packager must
ship the matching upstream license/third-party notice files when bundling it.
Pillow is required for image-to-PDF and encoding rendered image pages.
"""

from __future__ import annotations

import math
import os
import re
import tempfile
import threading
import warnings
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable, Sequence

from .formats import ConversionCancelled, ConversionError, UnsupportedFormatError

try:
    from PIL import Image, ImageOps
except ImportError:
    Image = None  # type: ignore[assignment]
    ImageOps = None  # type: ignore[assignment]

try:
    import pypdfium2 as pdfium
    import pypdfium2.raw as pdfium_raw
except ImportError:
    pdfium = None  # type: ignore[assignment]
    pdfium_raw = None  # type: ignore[assignment]


MAX_PDF_PAGES = 64
MAX_PAGE_PIXELS = 25_000_000
MAX_TOTAL_PIXELS = 90_000_000
MAX_PAGE_EDGE = 12_000
MAX_SOURCE_BYTES = 256 * 1024 * 1024
DEFAULT_DPI = 144
_PDF_HEADER = re.compile(rb"%PDF-(?:1\.[0-7]|2\.0)\r?(?:\n|\r|\s|$)")
_IMAGE_FORMATS = frozenset({"JPEG", "PNG", "WEBP", "BMP", "TIFF", "GIF"})
_RENDER_OUTPUTS = {"png": "PNG", "jpg": "JPEG", "webp": "WEBP"}


@dataclass(frozen=True)
class PdfPageInfo:
    number: int
    width_pt: float
    height_pt: float
    width_px: int
    height_px: int


@dataclass(frozen=True)
class PdfInfo:
    pages: tuple[PdfPageInfo, ...]
    dpi: float

    @property
    def page_count(self) -> int:
        return len(self.pages)


def _check_cancel(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise ConversionCancelled("PDF conversion cancelled")


def _check_dpi(dpi: float) -> float:
    try:
        value = float(dpi)
    except (ValueError, TypeError, OverflowError) as exc:
        raise UnsupportedFormatError("PDF DPI must be between 36 and 300") from exc
    if not math.isfinite(value) or not 36 <= value <= 300:
        raise UnsupportedFormatError("PDF DPI must be between 36 and 300")
    return value


def _check_source(path: Path, *, pdf: bool) -> Path:
    try:
        source = Path(path)
        size = source.stat().st_size
        if not source.is_file() or not 0 < size <= MAX_SOURCE_BYTES:
            raise UnsupportedFormatError("Input is not a supported local file size")
        if pdf:
            with source.open("rb") as stream:
                head = stream.read(16)
            if _PDF_HEADER.match(head) is None:
                raise UnsupportedFormatError("PDF is missing a valid leading %PDF signature")
        return source
    except UnsupportedFormatError:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise ConversionError("Cannot read local input file") from exc


def _require_pdfium() -> None:
    if pdfium is None or pdfium_raw is None:
        raise UnsupportedFormatError("pypdfium2 is required for PDF reading and rendering")


def _require_pillow() -> None:
    if Image is None or ImageOps is None:
        raise UnsupportedFormatError("Pillow is required for PDF image encoding")


def _open_pdf(source: Path):
    _require_pdfium()
    try:
        document = pdfium.PdfDocument(str(source))
    except (OSError, ValueError, pdfium.PdfiumError) as exc:
        # A password-protected file is never decrypted or supplied a password.
        raise UnsupportedFormatError("PDF cannot be opened (corrupt or encrypted)") from exc
    try:
        # PDFium can open some encrypted files without a password. Exclude
        # those too, rather than rendering them under restricted permissions.
        if pdfium_raw.FPDF_GetSecurityHandlerRevision(document.raw) != -1:
            raise UnsupportedFormatError("Encrypted PDF is unsupported")
        return document
    except Exception:
        document.close()
        raise


def _scan_document(document, dpi: float, cancel: threading.Event | None) -> PdfInfo:
    _check_cancel(cancel)
    try:
        count = len(document)
    except (ValueError, pdfium.PdfiumError) as exc:
        raise ConversionError("PDF page count cannot be read") from exc
    if not 1 <= count <= MAX_PDF_PAGES:
        raise UnsupportedFormatError(f"PDF must contain 1 to {MAX_PDF_PAGES} pages")
    pages: list[PdfPageInfo] = []
    total_pixels = 0
    for index in range(count):
        _check_cancel(cancel)
        try:
            with closing(document.get_page(index)) as page:
                width_pt, height_pt = page.get_size()
        except (ValueError, pdfium.PdfiumError) as exc:
            raise ConversionError("PDF page dimensions cannot be read") from exc
        if not all(math.isfinite(v) and v > 0 for v in (width_pt, height_pt)):
            raise UnsupportedFormatError("PDF page dimensions are invalid")
        # PDFium can round the raster up by one pixel; the extra pixel
        # reserves that margin before any native renderer is invoked.
        width_px = math.ceil(width_pt * dpi / 72) + 1
        height_px = math.ceil(height_pt * dpi / 72) + 1
        pixels = width_px * height_px
        if width_px > MAX_PAGE_EDGE or height_px > MAX_PAGE_EDGE or pixels > MAX_PAGE_PIXELS:
            raise UnsupportedFormatError("PDF page exceeds safe render dimensions")
        total_pixels += pixels
        if total_pixels > MAX_TOTAL_PIXELS:
            raise UnsupportedFormatError("PDF exceeds safe total render pixels")
        pages.append(PdfPageInfo(index + 1, width_pt, height_pt, width_px, height_px))
    _check_cancel(cancel)
    return PdfInfo(tuple(pages), dpi)


def inspect_pdf_info(
    path: Path, *, dpi: float = DEFAULT_DPI, cancel: threading.Event | None = None
) -> PdfInfo:
    """Validate strict PDF magic, encryption and all pages before rendering.

    Raises ConversionError / UnsupportedFormatError / ConversionCancelled.
    Dimensions are conservative raster allocations for the requested DPI.
    """
    _check_cancel(cancel)
    dpi = _check_dpi(dpi)
    source = _check_source(path, pdf=True)
    with _open_pdf(source) as document:
        return _scan_document(document, dpi, cancel)


def inspect_pdf(path: Path) -> int | None:
    """Return safe PDF page count, or None for invalid/encrypted/oversized PDF.

    The provider is optional: return None when pypdfium2 is unavailable.
    """
    try:
        return inspect_pdf_info(path).page_count
    except (ConversionError, OSError, ValueError, OverflowError):
        return None


def _stage_file(directory: Path, *, prefix: str, suffix: str) -> tuple[Path, BinaryIO]:
    try:
        folder = Path(directory)
        if not folder.is_dir() or folder.is_symlink():
            raise ConversionError("PDF output requires an existing private staging directory")
        fd, name = tempfile.mkstemp(dir=folder, prefix=prefix, suffix=suffix)
        try:
            return Path(name), os.fdopen(fd, "wb")
        except Exception:
            os.close(fd)
            raise
    except (OSError, ValueError, TypeError) as exc:
        raise ConversionError("Could not create a private PDF staging file") from exc


def _cleanup_staged(paths: list[tuple[Path, os.stat_result]]) -> None:
    for path, original in paths:
        try:
            current = path.lstat()
            if (not path.is_symlink() and current.st_ino == original.st_ino
                    and current.st_dev == original.st_dev and current.st_nlink == 1):
                path.unlink()
        except OSError:
            pass


def render_pdf_pages(
    path: Path,
    output_dir: Path,
    fmt: str = "png",
    *,
    cancel: threading.Event | None = None,
    progress: Callable[[float], None] | None = None,
    dpi: float = DEFAULT_DPI,
) -> list[Path]:
    """Render every page to unique files in a caller-owned PRIVATE staging dir.

    No final/user destination is accepted; the caller may package or publish
    these files after verification. On error/cancellation all our files are
    removed. PNG is lossless; JPG flattens alpha onto white.
    """
    _check_cancel(cancel)
    _require_pillow()
    normalized = str(fmt).lower().lstrip(".")
    if normalized == "jpeg":
        normalized = "jpg"
    if normalized not in _RENDER_OUTPUTS:
        raise UnsupportedFormatError("PDF page output must be PNG, JPG or WEBP")
    dpi = _check_dpi(dpi)
    source = _check_source(path, pdf=True)
    try:
        stage = Path(output_dir)
        if not stage.is_dir() or stage.is_symlink():
            raise ConversionError("PDF output requires an existing private staging directory")
    except (TypeError, OSError) as exc:
        raise ConversionError("PDF output requires an existing private staging directory") from exc
    staged: list[tuple[Path, os.stat_result]] = []
    results: list[Path] = []
    try:
        with _open_pdf(source) as document:
            info = _scan_document(document, dpi, cancel)
            if progress is not None:
                progress(0.0)
            for index, expected in enumerate(info.pages):
                _check_cancel(cancel)
                with closing(document.get_page(index)) as page:
                    with closing(page.render(scale=dpi / 72)) as bitmap:
                        with bitmap.to_pil() as raster:
                            if (raster.width > expected.width_px
                                    or raster.height > expected.height_px
                                    or raster.width * raster.height > MAX_PAGE_PIXELS):
                                raise ConversionError("Rendered PDF page exceeds safe dimensions")
                            output, handle = _stage_file(
                                stage, prefix=f"page-{index + 1:04d}-", suffix=f".{normalized}"
                            )
                            staged.append((output, os.fstat(handle.fileno())))
                            with handle:
                                if normalized == "jpg":
                                    with raster.convert("RGB") as opaque:
                                        opaque.save(handle, format="JPEG", quality=92)
                                elif normalized == "webp":
                                    raster.save(handle, format="WEBP", quality=92, method=4)
                                else:
                                    raster.save(handle, format="PNG")
                results.append(output)
                _check_cancel(cancel)
                if progress is not None:
                    progress((index + 1) / info.page_count)
        return results
    except Exception as exc:
        _cleanup_staged(staged)
        if pdfium is not None and isinstance(exc, pdfium.PdfiumError):
            raise ConversionError("PDF page rendering failed") from exc
        if Image is not None and isinstance(exc, (Image.DecompressionBombError, Image.DecompressionBombWarning)):
            raise ConversionError("PDF render exceeds safe raster limit") from exc
        raise


def _read_image_pages(sources: Sequence[Path], cancel: threading.Event | None):
    _require_pillow()
    if not 1 <= len(sources) <= MAX_PDF_PAGES:
        raise UnsupportedFormatError("PDF needs 1 to 64 image source files")
    pages = []
    pixels = 0
    success = False
    try:
        for source in sources:
            _check_cancel(cancel)
            path = _check_source(source, pdf=False)
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(path) as original:
                    if original.format not in _IMAGE_FORMATS:
                        raise UnsupportedFormatError("Unsupported image source for PDF")
                    count = getattr(original, "n_frames", 1)
                    if len(pages) + count > MAX_PDF_PAGES:
                        raise UnsupportedFormatError("Too many image frames for PDF")
                    for frame_index in range(count):
                        _check_cancel(cancel)
                        original.seek(frame_index)
                        width, height = original.size
                        if (not 0 < width <= MAX_PAGE_EDGE or not 0 < height <= MAX_PAGE_EDGE
                                or width * height > MAX_PAGE_PIXELS):
                            raise UnsupportedFormatError("Image exceeds PDF page limits")
                        pixels += width * height
                        if pixels > MAX_TOTAL_PIXELS:
                            raise UnsupportedFormatError("Images exceed PDF total pixel limit")
                        frame = ImageOps.exif_transpose(original.copy())
                        try:
                            # PDF has an opaque page background; composite alpha
                            # deliberately on white, preserving visible colors.
                            if "A" in frame.getbands() or "transparency" in frame.info:
                                rgba = frame.convert("RGBA")
                                page = Image.new("RGB", rgba.size, "white")
                                page.paste(rgba, mask=rgba.getchannel("A"))
                                rgba.close()
                            else:
                                page = frame.convert("RGB")
                            pages.append(page)
                        finally:
                            frame.close()
        success = True
        return pages
    except UnsupportedFormatError:
        raise
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ConversionError("Cannot decode image pages for PDF") from exc
    finally:
        if not success:
            for page in pages:
                page.close()


def images_to_pdf(
    sources: Sequence[Path], output_stream: BinaryIO, *, cancel: threading.Event | None = None
) -> None:
    """Write source image(s)/frames to an already-open caller-owned stage stream.

    This function never opens, truncates, names or publishes its output path.
    Image source files are read only. On failure the caller must discard its
    private staged stream because partial PDF bytes may already exist.
    """
    _check_cancel(cancel)
    pages = _read_image_pages(sources, cancel)
    try:
        _check_cancel(cancel)
        if not pages:
            raise UnsupportedFormatError("PDF needs at least one image page")
        try:
            pages[0].save(
                output_stream, format="PDF", save_all=True,
                append_images=pages[1:], resolution=72.0,
            )
        except (OSError, ValueError, TypeError) as exc:
            raise ConversionError("Pillow could not encode PDF") from exc
        _check_cancel(cancel)
    finally:
        for page in pages:
            page.close()


def image_to_pdf(
    source: Path, output_stream: BinaryIO, *, cancel: threading.Event | None = None
) -> None:
    """Write a one-file image or multi-frame image PDF to an OPEN stage stream."""
    images_to_pdf([source], output_stream, cancel=cancel)


__all__ = [
    "PdfInfo", "PdfPageInfo", "inspect_pdf", "inspect_pdf_info", "render_pdf_pages",
    "image_to_pdf", "images_to_pdf", "MAX_PDF_PAGES", "MAX_PAGE_PIXELS",
    "MAX_TOTAL_PIXELS", "DEFAULT_DPI",
]
