"""Stage 7 PDF release regression in disposable directories.

The production engine and frozen --direct program are exercised with locally
generated fixtures. Never execute the installer or alter the shell/registry.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from PIL import Image

from rightconvert import engine, pdf_provider
from rightconvert.engine import (
    ConversionCancelled,
    ConversionError,
    UnsupportedFormatError,
    combine_images_to_pdf,
    convert_file,
    get_targets,
)

pypdf = pytest.importorskip("pypdf", reason="Independent PDF parser needed for PDF release QA")
pytest.importorskip("pypdfium2")

FROZEN = (
    Path(__file__).resolve().parents[1]
    / "build" / "stage7" / "packed" / "RightConvert" / "RightConvert.exe"
)


def _png(path: Path, size=(64, 38), rgb=(180, 52, 72)) -> Path:
    Image.new("RGB", size, rgb).save(path)
    return path


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _no_stage(root: Path) -> None:
    assert not list(root.rglob(".rightconvert-stage-*"))


def _pdf(path: Path, *, sizes=((64, 44),), encrypted=False) -> Path:
    writer = pypdf.PdfWriter()
    for width, height in sizes:
        writer.add_blank_page(width=width, height=height)
    if encrypted:
        writer.encrypt("never supplied")
    with path.open("wb") as stream:
        writer.write(stream)
    return path


def _independent_pdf(path: Path, expected_count: int) -> None:
    reader = pypdf.PdfReader(str(path))
    assert not reader.is_encrypted
    assert len(reader.pages) == expected_count
    assert path.read_bytes().startswith(b"%PDF-")


def _inspect_png_zip(path: Path, sizes: list[tuple[int, int]]) -> None:
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        names = archive.namelist()
        assert names == [f"page-{i:04d}.png" for i in range(1, len(sizes) + 1)]
        for name, size in zip(names, sizes):
            from io import BytesIO
            with Image.open(BytesIO(archive.read(name))) as frame:
                frame.load()
                assert frame.format == "PNG"
                assert frame.size == (size[0] * 2, size[1] * 2)


def _frozen_direct(source: Path, target: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    if os.name != "nt" or not FROZEN.is_file():
        pytest.skip("Private Stage7 Windows frozen candidate is unavailable")
    state = tmp_path / "isolated-user"
    local = state / "local"
    roaming = state / "roaming"
    local.mkdir(parents=True, exist_ok=True)
    roaming.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["LOCALAPPDATA"] = str(local)
    env["APPDATA"] = str(roaming)
    env["TEMP"] = str(tmp_path)
    env["TMP"] = str(tmp_path)
    env["PYTHONNOUSERSITE"] = "1"
    return subprocess.run(
        [str(FROZEN), "--direct", "--files", str(source), "--target", target],
        cwd=str(tmp_path), env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, errors="replace", timeout=45, check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def test_source_direct_image_pdf_roundtrip_and_existing_target(tmp_path: Path):
    source = _png(tmp_path / "foto UTF-8 漢字.png")
    before = _digest(source)
    out = tmp_path / "converted"
    out.mkdir()
    marker = out / f"{source.stem}.pdf"
    marker.write_bytes(b"existing pdf is valuable")
    assert "pdf" in get_targets(source)
    first = convert_file(source, "pdf", out)
    second = convert_file(source, "pdf", out)
    assert first.name.endswith(" (1).pdf")
    assert second.name.endswith(" (2).pdf")
    assert first != second
    _independent_pdf(first, 1)
    _independent_pdf(second, 1)
    assert marker.read_bytes() == b"existing pdf is valuable"
    assert _digest(source) == before
    _no_stage(out)


def test_pdf_to_all_one_page_formats_and_no_overwrite(tmp_path: Path):
    pdf = _pdf(tmp_path / "scan 漢字.pdf", sizes=((88, 63),))
    before = _digest(pdf)
    out = tmp_path / "export"
    out.mkdir()
    assert {"png", "jpg", "webp", "tiff", "pngzip"} <= set(get_targets(pdf))
    for fmt, pil_name in (("png", "PNG"), ("jpg", "JPEG"), ("webp", "WEBP"), ("tiff", "TIFF")):
        output = convert_file(pdf, fmt, out)
        with Image.open(output) as image:
            image.load()
            assert image.format == pil_name
            assert image.size == (176, 126)
        assert _digest(pdf) == before
        _no_stage(out)
    archive = convert_file(pdf, "pngzip", out)
    assert archive.name == "scan 漢字-pages.zip"
    _inspect_png_zip(archive, [(88, 63)])
    assert _digest(pdf) == before
    _no_stage(out)


def test_multipage_zip_tiff_preserve_every_page_and_reject_loss(tmp_path: Path):
    sizes = [(40, 23), (52, 35), (67, 18)]
    source = _pdf(tmp_path / "batch.pdf", sizes=sizes)
    before = _digest(source)
    out = tmp_path / "folder"
    out.mkdir()
    sentinel = out / "batch-pages.zip"
    sentinel.write_bytes(b"keep unique user archive")
    assert set(get_targets(source)) == {"tiff", "pngzip"}
    for target in ("png", "jpg", "webp"):
        with pytest.raises(UnsupportedFormatError):
            convert_file(source, target, out)
    output = convert_file(source, "pngzip", out)
    assert output.name == "batch-pages (1).zip"
    _inspect_png_zip(output, sizes)
    assert sentinel.read_bytes() == b"keep unique user archive"
    tiff = convert_file(source, "tiff", out)
    with Image.open(tiff) as image:
        assert image.n_frames == len(sizes)
        for i, (width, height) in enumerate(sizes):
            image.seek(i)
            image.load()
            assert image.size == (width * 2, height * 2)
    assert _digest(source) == before
    _no_stage(out)


def test_combined_images_keep_order_and_existing_pdf(tmp_path: Path):
    sources = [
        _png(tmp_path / "Măsură 1.png", (22, 31), (255, 0, 0)),
        _png(tmp_path / "Măsură 2.png", (33, 41), (0, 0, 255)),
        _png(tmp_path / "Măsură 3.png", (44, 29), (0, 200, 0)),
    ]
    before = [_digest(path) for path in sources]
    destination = tmp_path / "group"
    destination.mkdir()
    sentinel = destination / f"{sources[0].stem}.pdf"
    sentinel.write_bytes(b"user data")
    output = combine_images_to_pdf(sources, destination)
    _independent_pdf(output, 3)
    pages = pypdf.PdfReader(str(output)).pages
    assert [(int(page.mediabox.width), int(page.mediabox.height)) for page in pages] == [
        (22, 31), (33, 41), (44, 29)
    ]
    assert sentinel.read_bytes() == b"user data"
    assert [_digest(path) for path in sources] == before
    _no_stage(destination)


def test_corrupt_and_encrypted_pdf_preserve_existing_output(tmp_path: Path):
    corrupt = tmp_path / "broken.pdf"
    corrupt.write_bytes(b"%PDF-1.7\n1 0 obj << incomplete endobj")
    locked = _pdf(tmp_path / "locked.pdf", encrypted=True)
    folder = tmp_path / "folder"
    folder.mkdir()
    sentinel = folder / "broken.png"
    sentinel.write_bytes(b"original existing output")
    for document in (corrupt, locked):
        original = document.read_bytes()
        assert get_targets(document) == []
        with pytest.raises((ConversionError, UnsupportedFormatError)):
            convert_file(document, "png", folder)
        assert document.read_bytes() == original
    assert sentinel.read_bytes() == b"original existing output"
    _no_stage(folder)


def test_page_and_pixel_limits_reject_without_user_outputs(tmp_path: Path):
    exceeded = _pdf(tmp_path / "pages.pdf", sizes=[(10, 10)] * 65)
    huge = _pdf(tmp_path / "oversize.pdf", sizes=[(20000, 20000)])
    out = tmp_path / "export"
    out.mkdir()
    for source in (exceeded, huge):
        before = _digest(source)
        assert get_targets(source) == []
        with pytest.raises((ConversionError, UnsupportedFormatError)):
            convert_file(source, "pngzip", out)
        assert _digest(source) == before
    assert list(out.iterdir()) == []


def test_cancellation_after_partial_multiframe_work_rolls_back(tmp_path: Path):
    document = _pdf(tmp_path / "three.pdf", sizes=[(70, 50)] * 3)
    before = _digest(document)
    folder = tmp_path / "export"
    folder.mkdir()
    sentinel = folder / "three-pages.zip"
    sentinel.write_bytes(b"keep contents")
    cancelled = threading.Event()
    reached = []

    def callback(fraction: float) -> None:
        reached.append(fraction)
        if fraction >= 1 / 3:
            cancelled.set()

    with pytest.raises(ConversionCancelled):
        convert_file(document, "pngzip", folder, cancel=cancelled, progress=callback)
    assert any(v >= 1 / 3 for v in reached)
    assert sentinel.read_bytes() == b"keep contents"
    assert _digest(document) == before
    _no_stage(folder)


def test_cancellation_before_image_pdf_or_combination_keeps_everything(tmp_path: Path):
    a = _png(tmp_path / "one.png")
    b = _png(tmp_path / "two.png")
    before = [_digest(a), _digest(b)]
    cancelled = threading.Event()
    cancelled.set()
    target = tmp_path / "out"
    with pytest.raises(ConversionCancelled):
        convert_file(a, "pdf", target, cancel=cancelled)
    with pytest.raises(ConversionCancelled):
        combine_images_to_pdf([a, b], target, cancel=cancelled)
    assert [_digest(a), _digest(b)] == before
    assert not target.exists()


def test_renderer_internal_error_does_not_mask_original_or_publish(tmp_path: Path, monkeypatch):
    source = _pdf(tmp_path / "unusual.pdf")
    before = _digest(source)
    folder = tmp_path / "out"
    folder.mkdir()
    expected = folder / "unusual.png"
    expected.write_bytes(b"existing")
    original_render = pdf_provider.render_pdf_pages

    def fail_after_valid_render(*args, **kwargs):
        result = original_render(*args, **kwargs)
        assert result
        raise ConversionError("injected renderer failure")

    monkeypatch.setattr(pdf_provider, "render_pdf_pages", fail_after_valid_render)
    with pytest.raises(ConversionError, match="injected renderer failure"):
        convert_file(source, "png", folder)
    assert _digest(source) == before
    assert expected.read_bytes() == b"existing"
    _no_stage(folder)


def test_adversarial_image_rejection_no_pdf_written(tmp_path: Path):
    rgba = tmp_path / "transparent.png"
    Image.new("RGBA", (12, 12), (255, 0, 0, 2)).save(rgba)
    before = _digest(rgba)
    dest = tmp_path / "out"
    assert "pdf" not in get_targets(rgba)
    with pytest.raises(UnsupportedFormatError):
        convert_file(rgba, "pdf", dest)
    assert _digest(rgba) == before
    assert not dest.exists()


def test_maximum_64_page_pdf_is_exported_without_truncation(tmp_path: Path):
    source = _pdf(tmp_path / "64 sheets.pdf", sizes=[(12, 9)] * 64)
    before = _digest(source)
    assert pdf_provider.inspect_pdf(source) == 64
    assert set(get_targets(source)) == {"tiff", "pngzip"}
    archive = convert_file(source, "pngzip", tmp_path / "out")
    _inspect_png_zip(archive, [(12, 9)] * 64)
    assert _digest(source) == before
    _no_stage(tmp_path)


def test_concurrent_same_basename_pdf_publications_are_unique(tmp_path: Path):
    image = _png(tmp_path / "photo.png", (34, 23))
    original = _digest(image)
    dest = tmp_path / "shared"
    dest.mkdir()
    sentinel = dest / "photo.pdf"
    sentinel.write_bytes(b"protected")
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: convert_file(image, "pdf", dest), range(8)))
    assert len(set(results)) == len(results)
    assert all(path.is_file() for path in results)
    for output in results:
        _independent_pdf(output, 1)
    assert sentinel.read_bytes() == b"protected"
    assert _digest(image) == original
    _no_stage(dest)


def test_misleading_pdf_extension_of_image_keeps_original(tmp_path: Path):
    # Extension is untrusted. An image called .pdf must not be overwritten by
    # a new output bearing the same extension.
    source = tmp_path / "not really a PDF.pdf"
    Image.new("RGB", (20, 10), "purple").save(source, format="PNG")
    before = source.read_bytes()
    assert "pdf" in get_targets(source)
    output = convert_file(source, "pdf", tmp_path)
    assert output.name == "not really a PDF (1).pdf"
    _independent_pdf(output, 1)
    assert source.read_bytes() == before
    _no_stage(tmp_path)


@pytest.mark.parametrize("direction", ["image_pdf", "pdf_png", "multipage_zip"])
def test_real_private_frozen_executable_direct_conversion(tmp_path: Path, direction: str):
    if direction == "image_pdf":
        source = _png(tmp_path / "frozen 日本.png", (56, 34))
        expected = tmp_path / "frozen 日本.pdf"
        target = "pdf"
    elif direction == "pdf_png":
        source = _pdf(tmp_path / "frozen 東京.pdf", sizes=[(46, 27)])
        expected = tmp_path / "frozen 東京.png"
        target = "png"
    else:
        source = _pdf(tmp_path / "frozen multipage.pdf", sizes=[(32, 19), (40, 25)])
        expected = tmp_path / "frozen multipage-pages.zip"
        target = "pngzip"
    initial = _digest(source)
    process = _frozen_direct(source, target, tmp_path)
    assert process.returncode == 0, (process.returncode, process.stdout[:500], process.stderr[:500])
    assert expected.is_file()
    if target == "pdf":
        _independent_pdf(expected, 1)
    elif target == "png":
        with Image.open(expected) as image:
            image.load()
            assert image.format == "PNG"
            assert image.size == (92, 54)
    else:
        _inspect_png_zip(expected, [(32, 19), (40, 25)])
    assert _digest(source) == initial
    _no_stage(tmp_path)


def test_real_frozen_executable_collision_and_corrupt_rejection(tmp_path: Path):
    source = _png(tmp_path / "frozen duplicate.png", (18, 14))
    original = _digest(source)
    existing = tmp_path / "frozen duplicate.pdf"
    existing.write_bytes(b"keep this")
    result = _frozen_direct(source, "pdf", tmp_path)
    assert result.returncode == 0
    assert existing.read_bytes() == b"keep this"
    _independent_pdf(tmp_path / "frozen duplicate (1).pdf", 1)

    corrupt = tmp_path / "invalid.pdf"
    corrupt.write_bytes(b"%PDF-1.7\ntruncated")
    before = corrupt.read_bytes()
    failure = _frozen_direct(corrupt, "png", tmp_path)
    assert failure.returncode != 0, "corrupt PDF unexpectedly converted"
    assert corrupt.read_bytes() == before
    assert not (tmp_path / "invalid.png").exists()
    assert _digest(source) == original
    _no_stage(tmp_path)
