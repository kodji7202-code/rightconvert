from pathlib import Path
import zipfile

import pytest

from scripts.package_customer_preview import (
    REQUIRED_NOTICES, allowed_customer_entries, build_preview,
)


def fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    installer = tmp_path / "RightConvertSetup.exe"
    installer.write_bytes(b"MZ" + b"fixture" * 100)
    notices = tmp_path / "notices"
    notices.mkdir()
    for name in REQUIRED_NOTICES:
        (notices / name).write_text(f"legal notice fixture {name}", encoding="utf-8")
    guide = tmp_path / "preview.txt"
    guide.write_text("NOT FOR SALE; signing and customer EULA pending", encoding="utf-8")
    return installer, notices, guide


def test_customer_preview_excludes_source_secrets_and_internal_files(tmp_path: Path):
    installer, notices, guide = fixture(tmp_path)
    # Placing plausible source and private-key files next to the fixture cannot
    # accidentally sweep them into the whitelisted customer payload.
    (tmp_path / "engine.py").write_text("private commercial source")
    (tmp_path / "issuer-private.pem").write_text("private test fixture")
    (tmp_path / "RightConvert-0.2.0-source.zip").write_bytes(b"private archive")
    archive = build_preview(installer, notices, guide, tmp_path / "customer-preview.zip")
    with zipfile.ZipFile(archive) as payload:
        assert payload.testzip() is None
        assert set(payload.namelist()) == set(allowed_customer_entries(installer, notices, guide))
        assert b"NOT FOR SALE" in payload.read("NOT-FOR-SALE-README.txt")
        assert all(not name.endswith((".py", ".pem", ".key", ".sql", "source.zip"))
                   for name in payload.namelist())


def test_customer_preview_refuses_missing_notice_or_link(tmp_path: Path):
    installer, notices, guide = fixture(tmp_path)
    (notices / REQUIRED_NOTICES[2]).unlink()
    with pytest.raises(ValueError, match="Expected regular"):
        allowed_customer_entries(installer, notices, guide)
    (notices / REQUIRED_NOTICES[2]).write_text("restored")
    guide.unlink()
    try:
        guide.symlink_to(installer)
    except (OSError, NotImplementedError):
        pytest.skip("Windows developer symlink permission unavailable")
    with pytest.raises(ValueError, match="Expected regular"):
        allowed_customer_entries(installer, notices, guide)


def test_numpy_notice_required_exactly_when_numpy_is_bundled(tmp_path: Path):
    """NumPy is not a dependency: a clean build must not demand its notice,
    but a bundle that froze numpy must never ship without it."""
    from scripts.package_customer_preview import required_notices

    installer, notices, guide = fixture(tmp_path)
    assert "NumPy-LICENSE.txt" not in required_notices(tmp_path)
    allowed_customer_entries(installer, notices, guide)  # no NumPy notice needed

    (tmp_path / "_internal" / "numpy").mkdir(parents=True)
    assert "NumPy-LICENSE.txt" in required_notices(tmp_path)
    with pytest.raises(ValueError, match="NumPy-LICENSE"):
        allowed_customer_entries(installer, notices, guide)
    (notices / "NumPy-LICENSE.txt").write_text("numpy notice fixture", encoding="utf-8")
    assert "ThirdPartyNotices/NumPy-LICENSE.txt" in allowed_customer_entries(installer, notices, guide)
