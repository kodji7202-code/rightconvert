"""Release gates use the installed wheel's exact PDFium DLL and 18 notice texts."""

from importlib import metadata
from pathlib import Path
import shutil
import zipfile

import pytest

from scripts.check_release import verify_embedded_setup, verify_pdfium_archive
from scripts.collect_licenses import pdfium_notices
from scripts.package_customer_preview import (
    OUTPUT, REQUIRED_NOTICES, allowed_customer_entries, build_preview,
)
from scripts.package_release import VERSION


@pytest.fixture
def pdfium_bundle(tmp_path: Path) -> tuple[Path, Path, Path, Path, dict[Path, Path]]:
    try:
        dist = metadata.distribution("pypdfium2")
    except metadata.PackageNotFoundError:
        pytest.skip("pypdfium2 Windows wheel is not installed")
    files = [item for item in (dist.files or [])
             if str(item).replace("\\", "/").endswith("pypdfium2_raw/pdfium.dll")]
    if len(files) != 1:
        pytest.skip("Expected exactly one installed wheel PDFium DLL")
    app = tmp_path / "app"
    dll = app / "_internal" / "pypdfium2_raw" / "pdfium.dll"
    dll.parent.mkdir(parents=True)
    shutil.copyfile(dist.locate_file(files[0]), dll)
    notices, provenance = pdfium_notices(app)
    assert len(notices) >= 4 and any("BUILD_LICENSES" in name.parts for name in notices)
    notice_dir = app / "ThirdPartyNotices"
    notice_dir.mkdir()
    for name in REQUIRED_NOTICES:
        (notice_dir / name).write_text("Test third-party license " + name, encoding="utf-8")
    (notice_dir / "README.txt").write_text("Build third-party notice inventory\n" + provenance,
                                           encoding="utf-8")
    for name, source in notices.items():
        target = notice_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    installer = tmp_path / "RightConvertSetup.exe"
    installer.write_bytes(b"MZ" + b"test setup" * 64)
    guide = tmp_path / "guide.txt"
    guide.write_text("UNSIGNED NOT FOR SALE", encoding="utf-8")
    return app, notice_dir, installer, guide, notices


def make_app_archive(app: Path, archive: Path, prefix: str = "app/",
                     replace: dict[str, bytes] | None = None,
                     skip: set[str] | None = None) -> Path:
    replacements = replace or {}
    omitted = skip or set()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as result:
        for file in app.rglob("*"):
            if not file.is_file():
                continue
            name = prefix + file.relative_to(app).as_posix()
            if name not in omitted:
                result.writestr(name, replacements.get(name, file.read_bytes()))
    return archive


def test_preview_includes_every_matching_pdfium_notice_and_excludes_private_material(
    pdfium_bundle, tmp_path: Path,
) -> None:
    app, notice_dir, installer, guide, notices = pdfium_bundle
    (tmp_path / "issuer-private.pem").write_text("private signing key")
    (tmp_path / "customer-orders.sqlite").write_text("private customer data")
    (tmp_path / "engine.py").write_text("proprietary source code")
    mapping = allowed_customer_entries(installer, notice_dir, guide)
    assert len([name for name in mapping if name.startswith("ThirdPartyNotices/pypdfium2/")]) == len(notices)
    assert all(f"ThirdPartyNotices/{name.as_posix()}" in mapping for name in notices)
    preview = build_preview(installer, notice_dir, guide, tmp_path / "preview.zip")
    with zipfile.ZipFile(preview) as archive:
        assert archive.testzip() is None
        assert set(archive.namelist()) == set(mapping)
        assert all("issuer-private" not in name and "customer-orders" not in name
                   and "engine.py" not in name for name in archive.namelist())
        for relative, source in notices.items():
            assert archive.read(f"ThirdPartyNotices/{relative.as_posix()}") == source.read_bytes()


@pytest.mark.parametrize("change", ["missing", "replaced", "extra", "provenance", "dll_changed"])
def test_preview_fails_closed_on_missing_or_wrong_pdfium_notice(
    pdfium_bundle, change: str, tmp_path: Path,
) -> None:
    app, notice_dir, installer, guide, notices = pdfium_bundle
    key = next(name for name in notices if name.as_posix().endswith("pdfium.txt"))
    if change == "missing":
        (notice_dir / key).unlink()
    elif change == "replaced":
        (notice_dir / key).write_bytes(b"M" * (notice_dir / key).stat().st_size)
    elif change == "extra":
        (notice_dir / "pypdfium2" / "private-issuer.key").write_bytes(b"hidden")
    elif change == "provenance":
        (notice_dir / "README.txt").write_text("Unverifiable PDFium information")
    else:
        (app / "_internal" / "pypdfium2_raw" / "pdfium.dll").write_bytes(b"fake native binary")
    with pytest.raises((ValueError, RuntimeError), match="PDFium|pypdfium2|wheel"):
        build_preview(installer, notice_dir, guide, tmp_path / "bad-preview.zip")
    assert not (tmp_path / "bad-preview.zip").exists()


def test_release_zip_and_setup_payload_verify_complete_pdfium_set(pdfium_bundle, tmp_path: Path):
    app, _, _, _, notices = pdfium_bundle
    for prefix in ("app/", "dist/RightConvert/"):
        archive = make_app_archive(app, tmp_path / ("setup.zip" if prefix == "app/" else "win.zip"), prefix)
        with zipfile.ZipFile(archive) as reader:
            assert verify_pdfium_archive(reader, app, prefix) == len(notices)


@pytest.mark.parametrize("change", ["remove_notice", "tamper_notice", "tamper_dll", "remove_dll"])
def test_release_verifier_rejects_matching_hash_archives_with_bad_pdfium_content(
    pdfium_bundle, tmp_path: Path, change: str,
):
    app, _, _, _, notices = pdfium_bundle
    prefix = "dist/RightConvert/"
    license_name = next(name for name in notices if name.as_posix().endswith("pdfium.txt"))
    selected = prefix + "ThirdPartyNotices/" + license_name.as_posix()
    binary = prefix + "_internal/pypdfium2_raw/pdfium.dll"
    replacements = {selected: b"Wrong PDFium LICENSE"} if change == "tamper_notice" else (
        {binary: b"Wrong PDFium native binary"} if change == "tamper_dll" else {}
    )
    omitted = {selected} if change == "remove_notice" else (
        {binary} if change == "remove_dll" else set()
    )
    archive = make_app_archive(app, tmp_path / "changed.zip", prefix, replacements, omitted)
    with zipfile.ZipFile(archive) as reader:
        assert reader.testzip() is None  # A new valid CRC/SHA manifest must not bypass notices.
        with pytest.raises(AssertionError, match="PDFium|pypdfium2"):
            verify_pdfium_archive(reader, app, prefix)


def test_release_verifier_without_pdfium_still_requires_crypto_notices(tmp_path: Path) -> None:
    app = tmp_path / "app"
    notice_dir = app / "ThirdPartyNotices"
    notice_dir.mkdir(parents=True)
    for name in REQUIRED_NOTICES:
        (notice_dir / name).write_text("Test license " + name)
    archive = make_app_archive(app, tmp_path / "without-pdfium.zip")
    with zipfile.ZipFile(archive) as reader:
        assert verify_pdfium_archive(reader, app, "app/") == 0
    (notice_dir / "cryptography-BSD.txt").unlink()
    with zipfile.ZipFile(archive) as reader:
        with pytest.raises(AssertionError, match="cryptography-BSD"):
            verify_pdfium_archive(reader, app, "app/")


def test_customer_preview_tracks_live_release_version() -> None:
    assert OUTPUT.name == f"RightConvert-{VERSION}-NOT-FOR-SALE.zip"


def test_release_verifier_reads_embedded_setup_not_just_staging(tmp_path: Path) -> None:
    from scripts.check_release import APP_DIR, PAYLOAD, ROOT

    exe = ROOT / "dist" / "RightConvertSetup.exe"
    if not exe.is_file() or not PAYLOAD.is_file():
        pytest.skip("An already built internal Setup EXE and staged ZIP are required")
    assert verify_embedded_setup(exe, PAYLOAD, APP_DIR) >= 0

    # An old, replaced staging ZIP must never be accepted just because it
    # contains a matching list of notice names.
    wrong_payload = tmp_path / "RightConvertPayload.zip"
    wrong_payload.write_bytes(b"PK\x03\x04intentionally stale payload")
    with pytest.raises(AssertionError, match="differs from current staged payload"):
        verify_embedded_setup(exe, wrong_payload, APP_DIR)
