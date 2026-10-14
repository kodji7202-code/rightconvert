"""Acceptance tests on the actual frozen Stage7 EXE shipped to customers.

These are independent of engine.convert_file source-code tests and never use
the installed user app/registry. Every input, output and LOCALAPPDATA setting
lives exclusively in pytest's disposable tmp_path.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import zipfile

from PIL import Image
from pypdf import PdfReader, PdfWriter
import pytest


ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = Path(os.environ.get(
    "RIGHTCONVERT_FROZEN_CANDIDATE",
    str(ROOT / "build" / "stage7" / "packed" / "RightConvert" / "RightConvert.exe"),
))


@pytest.fixture(scope="module")
def application() -> Path:
    if os.name != "nt" or not CANDIDATE.is_file():
        pytest.skip("Windows Stage7 frozen binary not available")
    return CANDIDATE


def run_direct(application: Path, local_data: Path, paths: list[Path], target: str) -> int:
    env = os.environ.copy()
    env["LOCALAPPDATA"] = str(local_data)
    env.pop("PYTHONPATH", None)
    proc = subprocess.run(
        [str(application), "--files", *[str(p) for p in paths], "--target", target, "--direct"],
        cwd=str(application.parent), env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return proc.returncode


def digest(source: Path) -> str:
    return hashlib.sha256(source.read_bytes()).hexdigest()


def assert_no_stage_files(directory: Path) -> None:
    assert not [p for p in directory.iterdir() if p.name.startswith(".rightconvert-stage-")]


def test_frozen_opaque_image_to_webp_jpg_pdf(application: Path, tmp_path: Path):
    file = tmp_path / "Ședință 東京 test.png"
    Image.new("RGB", (96, 45), (10, 40, 180)).save(file)
    original = digest(file)
    for target in ("webp", "jpg", "pdf"):
        assert run_direct(application, tmp_path / "isolated-settings", [file], target) == 0
        result = file.with_suffix("." + target)
        assert result.is_file() and result.stat().st_size > 0
        if target == "pdf":
            assert len(PdfReader(result).pages) == 1
        else:
            with Image.open(result) as reader:
                assert reader.format == {"webp": "WEBP", "jpg": "JPEG"}[target]
                reader.load()
        assert digest(file) == original
        assert_no_stage_files(tmp_path)


def test_frozen_repeat_preserves_first_output_and_source(application: Path, tmp_path: Path):
    source = tmp_path / "collision.png"
    Image.new("RGB", (32, 36), (90, 30, 40)).save(source)
    original = digest(source)
    assert run_direct(application, tmp_path / "private-settings", [source], "webp") == 0
    output = tmp_path / "collision.webp"
    first = digest(output)
    assert run_direct(application, tmp_path / "private-settings", [source], "webp") == 0
    assert (tmp_path / "collision (1).webp").is_file()
    assert digest(output) == first
    assert digest(source) == original
    assert_no_stage_files(tmp_path)


def test_frozen_pdf_all_pages_tiff_and_zip(application: Path, tmp_path: Path):
    pdf = tmp_path / "multi Șâ 日本語.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=92, height=63)
    writer.add_blank_page(width=72, height=94)
    writer.add_blank_page(width=100, height=77)
    with pdf.open("wb") as stream:
        writer.write(stream)
    original = digest(pdf)
    assert run_direct(application, tmp_path / "settings-private", [pdf], "pngzip") == 0
    with zipfile.ZipFile(tmp_path / "multi Șâ 日本語-pages.zip") as result:
        assert result.testzip() is None
        assert result.namelist() == [f"page-{n:04d}.png" for n in (1, 2, 3)]
        for item in result.namelist():
            with result.open(item) as payload:
                with Image.open(payload) as image:
                    assert image.format == "PNG"
    assert run_direct(application, tmp_path / "settings-private", [pdf], "tiff") == 0
    with Image.open(tmp_path / "multi Șâ 日本語.tiff") as image:
        assert image.format == "TIFF"
        assert image.n_frames == 3
        for n in range(3):
            image.seek(n)
            image.load()
    assert digest(pdf) == original
    assert_no_stage_files(tmp_path)


def test_frozen_batch_preflight_never_partially_converts(application: Path, tmp_path: Path):
    normal = tmp_path / "valid.png"
    corrupt = tmp_path / "bad.jpeg"
    Image.new("RGB", (34, 41), (44, 2, 18)).save(normal)
    corrupt.write_bytes(b"malformed file")
    input_digests = [digest(p) for p in (normal, corrupt)]
    assert run_direct(application, tmp_path / "settings-private", [normal, corrupt], "webp") == 2
    assert not (tmp_path / "valid.webp").exists()
    assert not (tmp_path / "bad.webp").exists()
    assert [digest(p) for p in (normal, corrupt)] == input_digests
    assert_no_stage_files(tmp_path)
