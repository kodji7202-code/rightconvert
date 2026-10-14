"""Stage 6 live document-format acceptance, with disposable local fixtures.

Run from the repository root: ``python scripts/validate_stage6.py``.
``--report PATH`` optionally writes machine-readable evidence to an explicit
destination; the default run does not modify project files. This validator
exercises real engine.convert_file() and combine_images_to_pdf() calls, verifies
published results with independent PDF/image readers, and checks source hashes,
collision protection and private staging cleanup. Office is reported as
unverified when LibreOffice cannot actually be executed locally.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import sys
import tempfile
import time
import zipfile
from collections import Counter
from datetime import datetime, timezone
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402
from rightconvert.engine import combine_images_to_pdf, convert_file, get_targets  # noqa: E402
from rightconvert.formats import (  # noqa: E402
    AUDIO_TARGETS, IMAGE_TARGETS, VIDEO_INPUTS, inspect_source,
    media_tools, supported_targets,
)
from rightconvert.office_provider import find_libreoffice  # noqa: E402
from scripts.validate_stage5 import make_audio, make_images, make_video  # noqa: E402

OFFICE_FORMATS = ("docx", "xlsx", "pptx", "odt", "ods", "odp")
STAGE5_EVIDENCE = ROOT / "docs" / "STAGE5_FORMAT_MATRIX_RESULTS.json"
STAGE5_LEGACY_TOTAL = 57
IMAGE_PIL_NAMES = {"jpg": "JPEG", "png": "PNG", "webp": "WEBP", "tiff": "TIFF"}


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def no_temporary_files(folder: Path) -> bool:
    return not any(p.name.startswith((".rightconvert-stage-", "part-")) or
                   p.suffix.startswith(".tmp.") for p in folder.rglob("*"))


def make_pdfs(directory: Path) -> dict[str, Path]:
    """Construct PDFs with pypdf, independently of RightConvert/PDFium.

    Vary the page dimensions so independent raster readers can check order,
    completeness and that the engine has not exported only the first page.
    """
    from pypdf import PdfWriter

    paths = {}
    for label, dimensions in {
        "single": [(96, 72)],
        "multi": [(96, 72), (72, 96), (120, 84)],
    }.items():
        result = directory / f"document ședință 東京 {label}.pdf"
        writer = PdfWriter()
        for width, height in dimensions:
            writer.add_blank_page(width=width, height=height)
        with result.open("wb") as stream:
            writer.write(stream)
        paths[label] = result
    return paths


def make_safe_office_fixtures(directory: Path) -> dict[str, Path]:
    """Reuse the independently authored, renderable six-family QA generator.

    No LibreOffice process participates in fixture construction. Tests of the
    Office provider own this generator; keeping one source of passive OOXML/ODF
    fixtures prevents two test suites from silently diverging.
    """
    spec = spec_from_file_location("stage6_office_fixture_helpers",
                                   ROOT / "tests" / "test_office_provider.py")
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    fixtures = {}
    for extension in OFFICE_FORMATS:
        source = directory / f"stage6.{extension}"
        module._native_source(source)
        info = inspect_source(source)
        assert info is not None and info.kind == "office" and info.format == extension
        fixtures[extension] = source
    return fixtures


def pdf_pages_independent(path: Path, expected_sizes: list[tuple[int, int]] | None = None) -> dict:
    """Read page structure via pypdf and independently rasterize with PyMuPDF.

    Neither reader participates in the application PDF renderer (PDFium) or
    Pillow's PDF writer. A second raster decoder also checks pages are usable.
    """
    from pypdf import PdfReader
    import pymupdf

    reader = PdfReader(str(path), strict=True)
    if reader.is_encrypted:
        raise AssertionError("Unexpected encrypted PDF output")
    pages = list(reader.pages)
    assert pages, "PDF has no pages"
    sizes = [(round(float(p.mediabox.width)), round(float(p.mediabox.height))) for p in pages]
    if expected_sizes is not None:
        assert sizes == expected_sizes, f"PDF page sizes/order: {sizes} != {expected_sizes}"
    with pymupdf.open(str(path)) as rendered:
        assert len(rendered) == len(pages), "Independent PDF decoders disagree on page count"
        for index, page in enumerate(rendered):
            raster = page.get_pixmap(matrix=pymupdf.Matrix(1, 1), alpha=False)
            assert raster.width > 0 and raster.height > 0 and len(raster.samples) > 0
            assert abs(raster.width - sizes[index][0]) <= 2
            assert abs(raster.height - sizes[index][1]) <= 2
    return {"reader": "pypdf.PdfReader + PyMuPDF raster", "pages": len(pages),
            "sizes_pt": [list(item) for item in sizes]}


def inspect_raster(data: bytes | Path, fmt: str,
                   source_sizes: list[tuple[int, int]]) -> dict:
    """Decode actual pixels with Pillow, including all TIFF frames."""
    if isinstance(data, bytes):
        source = io.BytesIO(data)
    else:
        source = data
    with Image.open(source) as raster:
        assert raster.format == IMAGE_PIL_NAMES[fmt], f"Unexpected {raster.format} instead of {fmt}"
        frames = getattr(raster, "n_frames", 1)
        assert frames == len(source_sizes), f"{fmt}: lost or added pages ({frames})"
        sizes = []
        for index, source_size in enumerate(source_sizes):
            raster.seek(index)
            raster.load()
            assert raster.width > 0 and raster.height > 0
            # PDF rasterizes at 144 dpi; the extra margin is at most 2 pixels.
            assert abs(raster.width - source_size[0] * 2) <= 2
            assert abs(raster.height - source_size[1] * 2) <= 2
            sample = raster.getpixel((raster.width // 2, raster.height // 2))
            assert bool(sample) if isinstance(sample, tuple) else sample >= 0
            sizes.append([raster.width, raster.height])
    return {"reader": "Pillow.Image.load", "frames": frames, "size_px": sizes}


def inspect_document_output(output: Path, target: str, pages: int,
                            source_sizes: list[tuple[int, int]] | None = None,
                            image_size: tuple[int, int] | None = None) -> dict:
    assert output.is_file() and 30 < output.stat().st_size < 32 * 1024 * 1024
    if target == "pdf":
        return pdf_pages_independent(output,
                                     [image_size] if image_size else source_sizes)
    assert source_sizes is not None
    if target == "pngzip":
        with zipfile.ZipFile(output) as zipped:
            expected = [f"page-{page:04d}.png" for page in range(1, pages + 1)]
            assert zipped.namelist() == expected, "ZIP lost/reordered/added PDF pages"
            assert zipped.testzip() is None, "ZIP checksum failure"
            decoded = [inspect_raster(zipped.read(name), "png", [size])
                       for name, size in zip(expected, source_sizes)]
        return {"reader": "zipfile CRC + Pillow.Image.load", "pages": pages,
                "entries": expected, "decoded": decoded}
    return inspect_raster(output, target, source_sizes)


def attempt_conversion(source: Path, target: str, root: Path,
                       *, pages: int = 1,
                       source_sizes: list[tuple[int, int]] | None = None,
                       image_size: tuple[int, int] | None = None,
                       combine: list[Path] | None = None,
                       office_path: str | None = None) -> dict:
    label = "combine" if combine else "convert"
    record = {"operation": label, "source": source.name, "target": target,
              "pages": pages, "status": "FAIL", "source_untouched": False,
              "collision_preserved": False, "temp_clean": False}
    route_dir = root / f"route-{len(list(root.iterdir())):03d}"
    route_dir.mkdir()
    sources = combine or [source]
    before = {str(path): digest(path) for path in sources}
    existing_name = (f"{source.stem}-pages.zip" if target == "pngzip"
                     else f"{source.stem}.{target}")
    output_name = (f"{source.stem}-pages (1).zip" if target == "pngzip"
                   else f"{source.stem} (1).{target}")
    existing = route_dir / existing_name
    existing.write_bytes(b"Stage6 collision marker: preserve exactly these bytes")
    marker_hash = digest(existing)
    started = time.monotonic()
    try:
        output = (combine_images_to_pdf(combine, route_dir) if combine else
                  convert_file(source, target, route_dir, office_path=office_path))
        assert output.parent == route_dir.resolve(), "Result escaped output folder"
        assert output != existing, "An existing destination was overwritten"
        assert output.name == output_name, output.name
        assert output.suffix.lower() == (".zip" if target == "pngzip" else f".{target}")
        record["independent_decode"] = inspect_document_output(
            output, target, pages, source_sizes, image_size)
        if office_path is not None and target == "pdf":
            record["pages"] = record["independent_decode"]["pages"]
        record["output_bytes"] = output.stat().st_size
        record["status"] = "PASS"
    except Exception as exc:
        record["error"] = f"{type(exc).__name__}: {str(exc)[-1000:]}"
    finally:
        record["elapsed_s"] = round(time.monotonic() - started, 3)
        record["source_untouched"] = all(digest(p) == before[str(p)] for p in sources)
        record["collision_preserved"] = existing.is_file() and digest(existing) == marker_hash
        record["temp_clean"] = no_temporary_files(route_dir)
        if not all(record[name] for name in ("source_untouched", "collision_preserved", "temp_clean")):
            record["status"] = "FAIL"
            record["error"] = record.get("error", "") + " Source/collision/staging integrity check failed"
    return record


def previous_media_matrix(home: Path, result: dict) -> dict[str, dict[str, Path]]:
    """Dynamically reconstruct all 16 legacy source-family advertisements.

    Compare the current live registry with the 57 already-executed Stage5
    routes. Stage6 doesn't re-run the established Stage5 conversions.
    """
    previous = json.loads(STAGE5_EVIDENCE.read_text(encoding="utf-8"))
    assert previous["summary"]["status"] == "PASS"
    assert len(previous["routes"]) == STAGE5_LEGACY_TOTAL
    assert all(route["status"] == "PASS" for route in previous["routes"])
    historical = {(route["family"], route["source"], route["target"])
                  for route in previous["routes"]}
    assert len(historical) == STAGE5_LEGACY_TOTAL

    sources_dir = home / "legacy-inputs"
    sources_dir.mkdir()
    tools = media_tools()
    assert tools is not None, "FFmpeg and FFprobe required to reproduce all 57 legacy advertisements"
    ffmpeg, _ = tools
    fixtures = {"image": make_images(sources_dir), "audio": make_audio(sources_dir, ffmpeg),
                "video": make_video(sources_dir, ffmpeg)}
    observed = set()
    for family, sources in fixtures.items():
        expected_sources = (set(IMAGE_TARGETS) | {"gif"} if family == "image" else
                            set(AUDIO_TARGETS) if family == "audio" else set(VIDEO_INPUTS))
        assert set(sources) == expected_sources, f"Missing fixture family {family}: {sources}"
        for source_fmt, path in sources.items():
            info = inspect_source(path)
            assert info is not None and info.kind == family and info.format == source_fmt
            targets = get_targets(path)
            assert set(targets) == set(supported_targets(info)), (family, source_fmt)
            for target in targets:
                if target in ("pdf", "pngzip"):
                    continue
                observed.add((family, source_fmt, target))
    assert historical == observed, ("Existing media/image routes changed",
                                    sorted(observed - historical), sorted(historical - observed))
    result["legacy_stage5"] = {
        "status": "UNCHANGED", "previous_evidence": str(STAGE5_EVIDENCE.relative_to(ROOT)),
        "previously_passed_routes": len(historical), "currently_advertised_legacy_routes": len(observed),
        "live_fixtures": sum(map(len, fixtures.values())),
        "note": "Stage5 conversions were previously executed; Stage6 rechecks their live advertisements"}
    return fixtures


def matrix(*, office_fixtures_dir: Path | None = None,
           office_path: str | None = None) -> dict:
    result = {"schema_version": 1, "generated_utc": datetime.now(timezone.utc).isoformat(),
              "platform": platform.platform(), "python": platform.python_version(),
              "documents": [], "routes": [], "office": [], "errors": []}
    # Use a short, temporary directory in the project root: LibreOffice creates
    # several nested private profile directories and Windows components may
    # otherwise hit legacy MAX_PATH when the sandbox is under user AppData.
    # TemporaryDirectory must remove the entire owned tree after acceptance.
    with tempfile.TemporaryDirectory(prefix=".rc-stage6-", dir=ROOT) as tmp:
        home = Path(tmp)
        try:
            fixtures = previous_media_matrix(home, result)
            documents = home / "pdf-fixtures"
            documents.mkdir()
            pdfs = make_pdfs(documents)

            # Construct all proposed combinations from *live* introspection,
            # instead of hardcoding new advertised route counts.
            expected_doc_targets = {
                "single": {"png", "jpg", "webp", "tiff", "pngzip"},
                "multi": {"tiff", "pngzip"},
            }
            for kind, path in pdfs.items():
                sizes = [(96, 72)] if kind == "single" else [(96, 72), (72, 96), (120, 84)]
                source_record = {"source": kind, "kind": "pdf", "path": path.name,
                                 "status": "FAIL", "advertised": []}
                result["documents"].append(source_record)
                try:
                    source_record["independent_decode"] = pdf_pages_independent(path, sizes)
                    info = inspect_source(path)
                    assert info is not None and info.kind == "pdf" and info.pages == len(sizes)
                    targets = get_targets(path)
                    assert set(targets) == set(supported_targets(info))
                    assert set(targets) == expected_doc_targets[kind], targets
                    source_record["advertised"] = targets
                    source_record["status"] = "PASS"
                    for target in targets:
                        record = attempt_conversion(path, target, home, pages=len(sizes),
                                                    source_sizes=sizes)
                        record["input_family"] = f"pdf-{kind}"
                        result["routes"].append(record)
                except Exception as exc:
                    source_record["error"] = f"{type(exc).__name__}: {exc}"

            for fmt, path in fixtures["image"].items():
                info = inspect_source(path)
                assert info is not None and info.kind == "image"
                targets = get_targets(path)
                assert set(targets) == set(supported_targets(info))
                if "pdf" not in targets:
                    continue
                with Image.open(path) as img:
                    size = img.size
                record = attempt_conversion(path, "pdf", home, image_size=size)
                record["input_family"] = f"image-{fmt}"
                result["routes"].append(record)

            # Combining is a separate operation; test ordered pages and output
            # collision with source SHA verification for every input.
            image_paths = []
            for index, dims in enumerate(((70, 50), (80, 60), (90, 70))):
                path = documents / f"combine {index} ședință 東京.png"
                with Image.new("RGB", dims, (70 + 35 * index, 100, 120)) as image:
                    image.save(path, "PNG")
                image_paths.append(path)
            result["routes"].append({
                **attempt_conversion(image_paths[0], "pdf", home, pages=3,
                                     source_sizes=[(70, 50), (80, 60), (90, 70)],
                                     combine=image_paths),
                "input_family": "image-group-3"})

            executable = find_libreoffice(office_path)
            if executable and office_fixtures_dir is None:
                office_fixtures_dir = home / "office-fixtures"
                office_fixtures_dir.mkdir()
                try:
                    generated = make_safe_office_fixtures(office_fixtures_dir)
                    result["office_fixture_generation"] = {
                        "status": "PASS", "families": sorted(generated),
                        "method": "Independent passive OOXML/ODF ZIP generator from tests/test_office_provider.py"}
                except Exception as exc:
                    result["office_fixture_generation"] = {
                        "status": "FAIL", "error": f"{type(exc).__name__}: {exc}"}
            for extension in OFFICE_FORMATS:
                entry = {"source_format": extension, "target": "pdf", "status": "UNVERIFIED"}
                result["office"].append(entry)
                if not executable:
                    entry.update(status="UNVERIFIED_NO_LIBREOFFICE",
                                 reason="No locally detected real LibreOffice executable; conversion not attempted")
                    continue
                if office_fixtures_dir is None:
                    entry.update(status="UNVERIFIED_NO_TRUSTED_FIXTURE",
                                 reason="Supply --office-fixtures with real native documents to validate")
                    continue
                source = office_fixtures_dir / f"stage6.{extension}"
                if not source.is_file():
                    entry.update(status="UNVERIFIED_MISSING_FIXTURE",
                                 reason=f"No native fixture at {source}")
                    continue
                try:
                    info = inspect_source(source)
                    assert info is not None and info.kind == "office" and info.format == extension, (
                        "The document failed strict native-content inspection")
                    assert "pdf" in supported_targets(info, office_path=executable)
                    assert "pdf" in get_targets(source, office_path=executable)
                    route = attempt_conversion(source, "pdf", home, office_path=executable)
                    route["input_family"] = f"office-{extension}"
                    result["routes"].append(route)
                    entry.update(status=route["status"],
                                 independent_decode=route.get("independent_decode"),
                                 source_untouched=route["source_untouched"],
                                 collision_preserved=route["collision_preserved"],
                                 temp_clean=route["temp_clean"])
                    if route["status"] != "PASS":
                        entry["reason"] = route.get("error", "Native conversion failed")
                except Exception as exc:
                    entry.update(status="UNVERIFIED_INVALID_FIXTURE",
                                 reason=f"{type(exc).__name__}: {exc}")
            result["libreoffice_executable_detected"] = bool(executable)
        except Exception as exc:
            result["errors"].append(f"{type(exc).__name__}: {exc}")

    counts = Counter(item["status"] for item in result["routes"])
    broken_sources = sum(item["status"] != "PASS" for item in result["documents"])
    all_checked = all(item["source_untouched"] and item["collision_preserved"]
                      and item["temp_clean"] for item in result["routes"])
    # On the PDFium/Pillow host we expect 5 image->PDF, 7 PDF->image, and
    # 1 image-group->PDF; a changed advertisement is reflected as a failure.
    office_pass = sum(item["status"] == "PASS" for item in result["office"])
    expected_count = 13 + office_pass
    office_required_but_incomplete = (
        (result.get("libreoffice_executable_detected", False)
         and office_pass != len(OFFICE_FORMATS))
        or (office_path is not None and not result.get("libreoffice_executable_detected", False))
    )
    success = (not result["errors"] and broken_sources == 0 and all_checked
               and counts["FAIL"] == 0 and counts["PASS"] == expected_count
               and not office_required_but_incomplete
               and result.get("legacy_stage5", {}).get("status") == "UNCHANGED")
    result["summary"] = {
        "status": "PASS" if success else "FAIL",
        "legacy_routes_unchanged": result.get("legacy_stage5", {}).get("currently_advertised_legacy_routes", 0),
        "new_routes_and_combines_advertised": len(result["routes"]),
        "new_routes_and_combines_pass": counts["PASS"],
        "new_routes_and_combines_fail": counts["FAIL"],
        "pdf_sources_verified": len(result["documents"]) - broken_sources,
        "office_routes_native_verified": office_pass,
        "office_routes_unverified": sum(item["status"].startswith("UNVERIFIED")
                                         for item in result["office"]),
        "errors": len(result["errors"]),
    }
    return result


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, help="Optional JSON evidence destination")
    parser.add_argument("--office-fixtures", type=Path,
                        help="Folder with genuine stage6.docx/xlsx/pptx/odt/ods/odp test documents")
    parser.add_argument("--libreoffice", type=str,
                        help="Absolute path to a real locally installed soffice executable or program folder")
    args = parser.parse_args()
    evidence = matrix(office_fixtures_dir=args.office_fixtures,
                      office_path=args.libreoffice)
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + "\n",
                               encoding="utf-8")
    summary = evidence["summary"]
    print(f"Stage6 {summary['status']}: previous media/image routes "
          f"{summary['legacy_routes_unchanged']}/57 unchanged; document routes and "
          f"combine {summary['new_routes_and_combines_pass']}/{summary['new_routes_and_combines_advertised']} "
          f"PASS; Office native verified {summary['office_routes_native_verified']}/6 "
          f"({summary['office_routes_unverified']} unverified)")
    for item in evidence["errors"]:
        print("FAIL general:", item)
    for item in (*evidence["documents"], *evidence["routes"]):
        if item["status"] != "PASS":
            print("FAIL source/route:", item.get("source"), item.get("target", ""),
                  item.get("error", "unknown"))
    return int(summary["status"] != "PASS")


if __name__ == "__main__":
    raise SystemExit(main())
