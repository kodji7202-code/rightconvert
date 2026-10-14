"""Verify that both privately packaged ZIPs match their SHA manifest.

Run after scripts/package_release.py; no files or registry entries are altered.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import zipfile

if __package__:
    from .package_release import VERSION
    from .package_customer_preview import REQUIRED_NOTICES, verified_pdfium_notices
else:
    from package_release import VERSION
    from package_customer_preview import REQUIRED_NOTICES, verified_pdfium_notices


ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "release"
APP_DIR = ROOT / "dist" / "RightConvert"
PAYLOAD = ROOT / "build" / "setup" / "RightConvertPayload.zip"


def verify_pdfium_archive(reader: zipfile.ZipFile, app_dir: Path, prefix: str) -> int:
    """Match archived PDFium binaries and every wheel notice to the staged app.

    This includes the installer staging payload, since a valid standalone
    third-party notices folder is insufficient if Setup omits the same files.
    """
    entries = reader.namelist()
    if len(entries) != len(set(entries)) or len(entries) != len({name.casefold() for name in entries}):
        raise AssertionError("Duplicate/case-colliding ZIP entry names")
    if any("\\" in name or name.startswith("/") or ".." in Path(name).parts for name in entries):
        raise AssertionError("Unsafe ZIP path")

    physical = {
        f"{prefix}{path.relative_to(app_dir).as_posix()}": path
        for path in app_dir.rglob("*")
        if path.is_file() and path.name.casefold() == "pdfium.dll"
    }
    archived = {
        name for name in entries
        if name.startswith(prefix) and name.rsplit("/", 1)[-1].casefold() == "pdfium.dll"
    }
    if archived != set(physical):
        raise AssertionError(f"Archived PDFium DLLs differ from staged bundle: "
                             f"missing={sorted(set(physical) - archived)}, "
                             f"extra={sorted(archived - set(physical))}")
    for name, disk_path in physical.items():
        if sha256_bytes(reader.read(name)) != sha256(disk_path):
            raise AssertionError(f"Archived PDFium DLL does not match installed wheel: {name}")

    notice_dir = app_dir / "ThirdPartyNotices"
    expected = verified_pdfium_notices(app_dir, notice_dir)
    notice_prefix = f"{prefix}ThirdPartyNotices/"
    archived_nested = {
        name[len(notice_prefix):] for name in entries
        if name.startswith(notice_prefix + "pypdfium2/") and not name.endswith("/")
    }
    if archived_nested != set(expected):
        raise AssertionError(f"Archived PDFium notices missing or unexpected: "
                             f"missing={sorted(set(expected) - archived_nested)}, "
                             f"extra={sorted(archived_nested - set(expected))}")
    for name, staged_path in expected.items():
        archive_name = notice_prefix + name
        if reader.read(archive_name) != staged_path.read_bytes():
            raise AssertionError(f"Incorrect archived pypdfium2/PDFium notice: {archive_name}")
    # Other licenses (notably cryptography) are always required and must match
    # the exact files staged into Setup and the Windows distribution.
    for name in REQUIRED_NOTICES:
        path = notice_dir / name
        archive_name = notice_prefix + name
        if not path.is_file() or archive_name not in entries:
            raise AssertionError(f"Required third-party notice missing: {archive_name}")
        if not path.stat().st_size or reader.read(archive_name) != path.read_bytes():
            raise AssertionError(f"Incorrect third-party notice: {archive_name}")
    return len(expected)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_embedded_setup(installer: Path, payload: Path, app_dir: Path) -> int:
    """Inspect the *actual* Setup EXE payload, not just its staging ZIP."""
    try:
        from PyInstaller.archive.readers import CArchiveReader
    except ImportError as exc:
        raise AssertionError("PyInstaller is required for inspecting the Setup binary") from exc
    frozen = CArchiveReader(str(installer))
    embedded = frozen.extract("RightConvertPayload.zip")
    digest = frozen.extract("RightConvertPayload.sha256").decode("ascii").strip()
    if sha256_bytes(embedded) != digest:
        raise AssertionError("Setup EXE embedded payload SHA-256 mismatch")
    if sha256_bytes(embedded) != sha256(payload):
        raise AssertionError("Setup EXE embedded payload differs from current staged payload")
    with zipfile.ZipFile(io.BytesIO(embedded)) as reader:
        if reader.testzip() is not None:
            raise AssertionError("Setup EXE embedded ZIP CRC mismatch")
        return verify_pdfium_archive(reader, app_dir, "app/")


def sha256(path: Path) -> str:
    with path.open("rb") as reader:
        return hashlib.file_digest(reader, "sha256").hexdigest()


def main() -> None:
    paths = {
        f"RightConvert-{VERSION}-win-x64.zip": (
            "dist/RightConvertSetup.exe",
            "dist/RightConvert/RightConvert.exe",
            "dist/RightConvert/RightConvertClassic.dll",
            "dist/RightConvert/RightConvertExplorer.dll",
            "dist/RightConvert/rightconvert-quick-cli-v1.marker",
            "dist/RightConvert/ThirdPartyNotices/Pillow-LICENSE.txt",
            "dist/RightConvert/ThirdPartyNotices/Python-LICENSE.txt",
            "dist/RightConvert/ThirdPartyNotices/PyInstaller-COPYING.txt",
            "dist/RightConvert/ThirdPartyNotices/cryptography-LICENSE.txt",
            "dist/RightConvert/ThirdPartyNotices/cryptography-APACHE.txt",
            "dist/RightConvert/ThirdPartyNotices/cryptography-BSD.txt",
            "docs/STAGE4_RELEASE.md",
            "docs/STAGE4_MATRIX_RESULTS.json",
            "docs/FORMAT_MATRIX.md",
            "docs/SYSTEM_REQUIREMENTS.md",
            "docs/STAGE5_FORMAT_MATRIX.md",
            "docs/STAGE5_FORMAT_MATRIX_RESULTS.json",
            "docs/STAGE5_QA.md",
            "docs/STAGE5_RELEASE.md",
            "docs/STAGE6_FORMAT_MATRIX.md",
            "docs/STAGE6_FORMAT_MATRIX_RESULTS.json",
            "docs/STAGE6_PDF_OFFICE_QA.md",
            "docs/STAGE6_RELEASE.md",
            "docs/LICENSE_KEYS.md",
            "docs/PRICING_AND_GO_TO_MARKET.md",
            "docs/COMMERCIAL_RELEASE_CHECKLIST.md",
            "docs/EULA_DRAFT.md",
            "docs/EVIDENCE.md",
            "docs/evidence/20261013-205036-v02-explorer-conversion-success.png",
        ),
        f"RightConvert-{VERSION}-source.zip": (
            "rightconvert/engine.py",
            "rightconvert/app.py",
            "rightconvert/formats.py",
            "rightconvert/licensing.py",
            "rightconvert/pdf_provider.py",
            "rightconvert/office_provider.py",
            "scripts/issue_license.py",
            "scripts/collect_licenses.py",
            "tests/test_licensing.py",
            "tests/test_stage6_document_adversarial.py",
            "tests/test_stage6_release_notices.py",
            "scripts/validate_stage4.py",
            "scripts/check_release.py",
            "scripts/package_release.py",
            "build.ps1",
            "pyproject.toml",
            "shell/classic/RightConvertClassic.cpp",
            "shell/windows11/RightConvertExplorer.cpp",
            "docs/STAGE4_MATRIX_RESULTS.json",
            "docs/STAGE4_RELEASE.md",
            "docs/STAGE5_FORMAT_MATRIX.md",
            "docs/STAGE5_FORMAT_MATRIX_RESULTS.json",
            "docs/STAGE5_QA.md",
            "docs/STAGE5_RELEASE.md",
            "docs/STAGE6_FORMAT_MATRIX.md",
            "docs/STAGE6_FORMAT_MATRIX_RESULTS.json",
            "docs/STAGE6_PDF_OFFICE_QA.md",
            "docs/STAGE6_RELEASE.md",
            "docs/LICENSE_KEYS.md",
            "docs/PRICING_AND_GO_TO_MARKET.md",
            "docs/COMMERCIAL_RELEASE_CHECKLIST.md",
            "docs/EULA_DRAFT.md",
            "docs/EVIDENCE.md",
            "docs/evidence/20261013-205036-v02-explorer-conversion-success.png",
        ),
    }
    checksums = {}
    for row in (RELEASE / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines():
        digest, separator, name = row.partition("  ")
        if separator != "  " or name not in paths or name in checksums:
            raise AssertionError("Unknown or duplicate SHA manifest row")
        checksums[name] = digest
    assert set(checksums) == set(paths), checksums

    result = {}
    for name, required in paths.items():
        archive = RELEASE / name
        computed = sha256(archive)
        assert computed == checksums[name], f"SHA-256 mismatch for {name}"
        with zipfile.ZipFile(archive) as reader:
            assert reader.testzip() is None, f"ZIP CRC mismatch for {name}"
            entries = set(reader.namelist())
            assert all(path in entries for path in required), f"Missing required entry in {name}"
            assert not any(Path(path).name.lower() in {"ffmpeg.exe", "ffprobe.exe"} for path in entries), (
                f"External FFmpeg unexpectedly bundled in {name}"
            )
            if name.endswith("win-x64.zip"):
                pdfium_count = verify_pdfium_archive(reader, APP_DIR, "dist/RightConvert/")
                if reader.read("dist/RightConvertSetup.exe") != (
                    ROOT / "dist" / "RightConvertSetup.exe"
                ).read_bytes():
                    raise AssertionError("Release ZIP contains outdated or different Setup EXE")
                forbidden_suffixes = (".py", ".pfx", ".pem", ".key", ".env", ".sql", ".sqlite", ".db")
                if any(entry.startswith("dist/") and entry.lower().endswith(forbidden_suffixes)
                       for entry in entries):
                    raise AssertionError("Source, private keys or customer records in binary ZIP")
                result[name] = {"pdfium_notices": pdfium_count}
            if name.endswith("source.zip"):
                actual = reader.read("rightconvert/formats.py")
                assert actual == (ROOT / "rightconvert" / "formats.py").read_bytes(), (
                    "Source ZIP no longer matches the working tree"
                )
            result[name] = {
                **result.get(name, {}),
                "bytes": archive.stat().st_size,
                "sha256": computed,
                "zip_crc": "PASS",
                "files": len(entries),
                "required_entries": len(required),
            }
    if not PAYLOAD.is_file():
        raise AssertionError(f"Missing staged Setup payload: {PAYLOAD}")
    with zipfile.ZipFile(PAYLOAD) as reader:
        if reader.testzip() is not None:
            raise AssertionError("Setup payload ZIP CRC mismatch")
        result["Setup payload"] = {
            "pdfium_notices": verify_pdfium_archive(reader, APP_DIR, "app/"),
        }
    result["Setup EXE embedded payload"] = {
        "pdfium_notices": verify_embedded_setup(
            ROOT / "dist" / "RightConvertSetup.exe", PAYLOAD, APP_DIR,
        ),
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print("PASS: ZIP CRC, SHA-256, complete exact PDFium/cryptography notices in release and Setup payload, no bundled FFmpeg.")


if __name__ == "__main__":
    main()
