"""Actual local PDFium/Pillow round trips and safe staging boundaries."""

from __future__ import annotations

import hashlib
import io
import threading
from pathlib import Path

import pytest
from PIL import Image

from rightconvert.formats import ConversionCancelled, ConversionError, UnsupportedFormatError
from rightconvert import pdf_provider as provider


@pytest.fixture(scope="module", autouse=True)
def pdfium_available():
    pytest.importorskip("pypdfium2")


def _save_pdf(images: list[Path], source: Path) -> Path:
    with source.open("wb") as output:
        provider.images_to_pdf(images, output)
    assert source.read_bytes().startswith(b"%PDF-")
    return source


def _picture(path: Path, color: str, size=(70, 45), fmt="PNG") -> Path:
    Image.new("RGB", size, color).save(path, format=fmt)
    return path


def test_real_single_page_pdf_roundtrip_and_input_preservation(tmp_path: Path):
    source = _picture(tmp_path / "Fotografie 漢字.png", "#bd3171")
    expected_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    buffer = io.BytesIO()
    provider.image_to_pdf(source, buffer)
    assert not buffer.closed
    document = tmp_path / "fără extensie reală.xyz"
    document.write_bytes(buffer.getvalue())

    assert provider.inspect_pdf(document) == 1
    info = provider.inspect_pdf_info(document)
    assert info.page_count == 1
    assert info.pages[0].number == 1
    assert info.pages[0].width_pt == 70
    assert info.pages[0].height_pt == 45

    stage = tmp_path / "staging privat"
    stage.mkdir()
    outputs = provider.render_pdf_pages(document, stage, dpi=72)
    assert len(outputs) == 1
    assert outputs[0].parent == stage
    with Image.open(outputs[0]) as result:
        result.load()
        assert result.format == "PNG"
        assert result.size == (70, 45)
        assert result.getpixel((20, 20))[0] > 140
    assert hashlib.sha256(source.read_bytes()).hexdigest() == expected_hash
    assert document.read_bytes() == buffer.getvalue()


def test_multiple_inputs_preserve_page_order_and_formats(tmp_path: Path):
    first = _picture(tmp_path / "pagina întâi.jpeg", "#ff0000", (100, 30), "JPEG")
    second = _picture(tmp_path / "pagina doi.webp", "#0000ff", (40, 80), "WEBP")
    original_bytes = [path.read_bytes() for path in (first, second)]
    pdf = _save_pdf([first, second], tmp_path / "album.pdf")

    assert provider.inspect_pdf(pdf) == 2
    stage = tmp_path / "privat"
    stage.mkdir()
    progress = []
    pages = provider.render_pdf_pages(pdf, stage, "jpg", dpi=72, progress=progress.append)
    assert len(pages) == 2
    assert progress == [0.0, 0.5, 1.0]
    with Image.open(pages[0]) as rendered_first, Image.open(pages[1]) as rendered_second:
        assert rendered_first.format == "JPEG"
        assert rendered_second.format == "JPEG"
        assert rendered_first.size == (100, 30)
        assert rendered_second.size == (40, 80)
        assert rendered_first.getpixel((15, 15))[0] > 180
        assert rendered_second.getpixel((15, 15))[2] > 180
    assert [path.read_bytes() for path in (first, second)] == original_bytes


def test_animated_gif_generates_multipage_pdf(tmp_path: Path):
    animated = tmp_path / "animație.gif"
    frame0 = Image.new("RGB", (30, 24), "red")
    frame1 = Image.new("RGB", (30, 24), "green")
    frame0.save(animated, format="GIF", save_all=True, append_images=[frame1], duration=50, loop=0)
    original = animated.read_bytes()
    buffer = io.BytesIO()
    provider.image_to_pdf(animated, buffer)
    pdf = tmp_path / "animated.pdf"
    pdf.write_bytes(buffer.getvalue())
    assert provider.inspect_pdf(pdf) == 2
    assert animated.read_bytes() == original


@pytest.mark.parametrize("fmt", ["png", "jpeg", "webp"])
def test_pdf_to_requested_raster_format(tmp_path: Path, fmt: str):
    picture = _picture(tmp_path / "p.png", "#325481", (18, 14))
    pdf = _save_pdf([picture], tmp_path / "test.pdf")
    stage = tmp_path / "stage"
    stage.mkdir()
    outputs = provider.render_pdf_pages(pdf, stage, fmt, dpi=72)
    assert len(outputs) == 1
    expected = "JPEG" if fmt == "jpeg" else fmt.upper()
    with Image.open(outputs[0]) as image:
        image.load()
        assert image.format == expected
        assert image.size == (18, 14)


def test_signature_requires_pdf_at_offset_zero_and_real_file(tmp_path: Path):
    forged = tmp_path / "wrong.pdf"
    forged.write_bytes(b"not actually a PDF")
    assert provider.inspect_pdf(forged) is None
    with pytest.raises(UnsupportedFormatError, match="signature"):
        provider.inspect_pdf_info(forged)

    shifted = tmp_path / "shifted.pdf"
    shifted.write_bytes(b"comment\n%PDF-1.7\n%%EOF")
    assert provider.inspect_pdf(shifted) is None
    empty = tmp_path / "empty.pdf"
    empty.write_bytes(b"")
    assert provider.inspect_pdf(empty) is None


def test_pdfium_rejects_corrupt_pdf_with_valid_magic(tmp_path: Path):
    corrupt = tmp_path / "damaged.pdf"
    corrupt.write_bytes(b"%PDF-1.7\n1 0 obj << /Broken\n%%EOF")
    assert provider.inspect_pdf(corrupt) is None
    stage = tmp_path / "stage"
    stage.mkdir()
    with pytest.raises((ConversionError, UnsupportedFormatError)):
        provider.render_pdf_pages(corrupt, stage)
    assert list(stage.iterdir()) == []


def test_encrypted_pdf_never_opens_without_password(tmp_path: Path):
    pypdf = pytest.importorskip("pypdf")
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=80, height=60)
    writer.encrypt("not exposed password")
    locked = tmp_path / "confidențial.pdf"
    with locked.open("wb") as f:
        writer.write(f)
    assert provider.inspect_pdf(locked) is None
    with pytest.raises(UnsupportedFormatError, match="encrypted|corrupt|opened"):
        provider.inspect_pdf_info(locked)


def test_too_many_pages_rejected_before_render(tmp_path: Path, monkeypatch):
    pypdf = pytest.importorskip("pypdf")
    writer = pypdf.PdfWriter()
    for _ in range(3):
        writer.add_blank_page(width=72, height=72)
    many = tmp_path / "large-page-count.pdf"
    with many.open("wb") as f:
        writer.write(f)
    monkeypatch.setattr(provider, "MAX_PDF_PAGES", 2)
    assert provider.inspect_pdf(many) is None
    stage = tmp_path / "stage"
    stage.mkdir()
    with pytest.raises(UnsupportedFormatError, match="pages"):
        provider.render_pdf_pages(many, stage)
    assert not list(stage.iterdir())


def test_large_pdf_page_rejected_from_metadata_before_raster_allocation(tmp_path: Path):
    pypdf = pytest.importorskip("pypdf")
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=15000, height=15000)
    huge = tmp_path / "massive.pdf"
    with huge.open("wb") as f:
        writer.write(f)
    assert provider.inspect_pdf(huge) is None
    stage = tmp_path / "stage"
    stage.mkdir()
    with pytest.raises(UnsupportedFormatError, match="dimensions"):
        provider.render_pdf_pages(huge, stage)
    assert not list(stage.iterdir())


def test_total_pixels_rejected_before_any_render(tmp_path: Path, monkeypatch):
    first = _picture(tmp_path / "one.png", "red")
    second = _picture(tmp_path / "two.png", "blue")
    pdf = _save_pdf([first, second], tmp_path / "two.pdf")
    monkeypatch.setattr(provider, "MAX_TOTAL_PIXELS", 5000)
    stage = tmp_path / "stage"
    stage.mkdir()
    with pytest.raises(UnsupportedFormatError, match="total"):
        provider.render_pdf_pages(pdf, stage, dpi=72)
    assert not list(stage.iterdir())


def test_output_directory_must_be_supplied_and_existing(tmp_path: Path):
    picture = _picture(tmp_path / "src.png", "red")
    pdf = _save_pdf([picture], tmp_path / "source.pdf")
    with pytest.raises(ConversionError, match="staging"):
        provider.render_pdf_pages(pdf, tmp_path / "not-created")
    assert not (tmp_path / "not-created").exists()


def test_progress_cancellation_removes_all_partial_pages(tmp_path: Path):
    images = [_picture(tmp_path / f"{i}.png", "red") for i in range(3)]
    pdf = _save_pdf(images, tmp_path / "group.pdf")
    original = pdf.read_bytes()
    stage = tmp_path / "stage"
    stage.mkdir()
    already_there = stage / "other-data.bin"
    already_there.write_bytes(b"untouched")
    cancel = threading.Event()

    def stop_after_first(progress: float):
        if progress >= 1 / 3:
            cancel.set()

    with pytest.raises(ConversionCancelled):
        provider.render_pdf_pages(pdf, stage, cancel=cancel, progress=stop_after_first)
    assert list(stage.iterdir()) == [already_there]
    assert already_there.read_bytes() == b"untouched"
    assert pdf.read_bytes() == original


def test_pre_cancelled_image_to_pdf_writes_nothing(tmp_path: Path):
    source = _picture(tmp_path / "a.png", "red")
    cancel = threading.Event()
    cancel.set()
    output = io.BytesIO()
    with pytest.raises(ConversionCancelled):
        provider.image_to_pdf(source, output, cancel=cancel)
    assert output.getvalue() == b""


def test_single_multi_image_pdf_rejects_unsafe_image_size(tmp_path: Path, monkeypatch):
    picture = _picture(tmp_path / "small.png", "green", (200, 200))
    monkeypatch.setattr(provider, "MAX_PAGE_PIXELS", 1000)
    with pytest.raises(UnsupportedFormatError, match="limits"):
        provider.image_to_pdf(picture, io.BytesIO())


def test_pdfium_optional_inspection_returns_none_when_absent(tmp_path: Path, monkeypatch):
    picture = _picture(tmp_path / "small.png", "green")
    pdf = _save_pdf([picture], tmp_path / "small.pdf")
    monkeypatch.setattr(provider, "pdfium", None)
    assert provider.inspect_pdf(pdf) is None
    with pytest.raises(UnsupportedFormatError, match="pypdfium2"):
        provider.inspect_pdf_info(pdf)


@pytest.mark.parametrize("dpi", [0, -1, 1e200, float("nan"), 500])
def test_invalid_dpi_rejected(tmp_path: Path, dpi):
    picture = _picture(tmp_path / "small.png", "green")
    pdf = _save_pdf([picture], tmp_path / "small.pdf")
    with pytest.raises(UnsupportedFormatError, match="DPI"):
        provider.inspect_pdf_info(pdf, dpi=dpi)


def test_disallowed_pdf_image_source_is_not_transcoded(tmp_path: Path):
    picture = tmp_path / "misleading.png"
    picture.write_text("<svg xmlns='http://www.w3.org/2000/svg'></svg>", encoding="utf-8")
    with pytest.raises(ConversionError):
        provider.image_to_pdf(picture, io.BytesIO())


def test_render_declines_unknown_image_encoder(tmp_path: Path):
    picture = _picture(tmp_path / "p.png", "blue")
    pdf = _save_pdf([picture], tmp_path / "s.pdf")
    stage = tmp_path / "stage"
    stage.mkdir()
    with pytest.raises(UnsupportedFormatError, match="PNG, JPG or WEBP"):
        provider.render_pdf_pages(pdf, stage, "bmp")
    assert list(stage.iterdir()) == []
