"""Live Stage6 document route acceptance, independent of document providers."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts import validate_stage6 as stage6


@pytest.fixture(scope="module")
def acceptance() -> dict:
    # Executes the same code path as `python scripts/validate_stage6.py` in a
    # disposable workspace. The proof is actual conversion and decoding.
    return stage6.matrix()


def test_document_routes_are_actually_decodable_and_keep_sources(acceptance: dict) -> None:
    assert acceptance["errors"] == [], acceptance["errors"]
    assert acceptance["summary"]["status"] == "PASS", acceptance
    assert acceptance["summary"]["legacy_routes_unchanged"] == 57
    assert acceptance["summary"]["new_routes_and_combines_pass"] == 13
    assert acceptance["summary"]["new_routes_and_combines_fail"] == 0

    routes = acceptance["routes"]
    assert len(routes) == 13
    assert {record["input_family"] for record in routes} == {
        "pdf-single", "pdf-multi", "image-bmp", "image-jpg", "image-png",
        "image-tiff", "image-webp", "image-group-3",
    }
    assert {record["target"] for record in routes if record["input_family"] == "pdf-single"} == {
        "png", "jpg", "webp", "tiff", "pngzip"}
    assert {record["target"] for record in routes if record["input_family"] == "pdf-multi"} == {
        "tiff", "pngzip"}
    for record in routes:
        assert record["status"] == "PASS", record
        assert record["source_untouched"] is True, record
        assert record["collision_preserved"] is True, record
        assert record["temp_clean"] is True, record
        decoded = record["independent_decode"]
        assert decoded["reader"]
        if record["target"] == "pdf":
            assert decoded["pages"] == record["pages"]
        if record["target"] == "pngzip":
            assert len(decoded["entries"]) == record["pages"]
        if record["target"] == "tiff":
            assert decoded["frames"] == record["pages"]


def test_pdf_fixtures_are_independently_readable(tmp_path: Path) -> None:
    pdfs = stage6.make_pdfs(tmp_path)
    assert stage6.pdf_pages_independent(pdfs["single"], [(96, 72)])["pages"] == 1
    assert stage6.pdf_pages_independent(
        pdfs["multi"], [(96, 72), (72, 96), (120, 84)])["pages"] == 3


def test_staging_cleanup_detector_rejects_leftover(tmp_path: Path) -> None:
    assert stage6.no_temporary_files(tmp_path)
    staging = tmp_path / ".rightconvert-stage-fixture"
    staging.mkdir()
    (staging / "part-unfinished.tmp.png").write_bytes(b"incomplete")
    assert not stage6.no_temporary_files(tmp_path)


def test_office_native_result_is_explicitly_unverified_without_libreoffice(acceptance: dict) -> None:
    assert {entry["source_format"] for entry in acceptance["office"]} == set(stage6.OFFICE_FORMATS)
    if not acceptance["libreoffice_executable_detected"]:
        assert all(entry["status"] == "UNVERIFIED_NO_LIBREOFFICE"
                   for entry in acceptance["office"])
    else:
        # A detected executable alone is insufficient evidence for a native
        # document format to be advertised as tested.
        assert all(entry["status"] == "UNVERIFIED_NO_TRUSTED_FIXTURE"
                   for entry in acceptance["office"])
    assert acceptance["summary"]["office_routes_native_verified"] == 0

