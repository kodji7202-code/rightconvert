"""Store (full MSIX) runtime behaviour that can be checked without a package."""

import json
import os
from pathlib import Path
import stat

import pytest

from rightconvert import cli, office_provider, runtime


def test_unpackaged_process_has_no_package_identity():
    # pytest itself never runs with MSIX identity.
    assert runtime.package_full_name() is None
    assert runtime.package_family_name() is None
    assert runtime.is_packaged() is False


def test_self_check_writes_report_without_gui(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "lad"))
    monkeypatch.setenv("PATH", "")
    import rightconvert.config as config
    _drop_handlers(config)
    report = tmp_path / "out" / "self-check.json"
    assert cli.main(["--self-check", str(report)]) == 0
    facts = json.loads(report.read_text(encoding="utf-8"))
    assert facts["ok"] is True and facts["packaged"] is False
    assert facts["package_full_name"] is None
    assert facts["libreoffice"] is None
    assert facts["data_dir"].endswith("RightConvert")
    assert facts["technical_log_written"] is True
    _drop_handlers(config)


def _drop_handlers(config):
    log = config.logging.getLogger("rightconvert")
    for handler in list(log.handlers):
        handler.close()
        log.removeHandler(handler)


@pytest.mark.skipif(os.name != "nt", reason="Program Files discovery is Windows-only")
def test_system_libreoffice_found_in_program_files(tmp_path, monkeypatch):
    program = tmp_path / "PF" / "LibreOffice" / "program"
    program.mkdir(parents=True)
    exe = program / "soffice.exe"
    exe.write_bytes(b"stub")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("RIGHTCONVERT_SYSTEM_OFFICE_LOOKUP", "1")
    monkeypatch.setenv("PATH", "")
    for variable in ("ProgramW6432", "ProgramFiles(x86)"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "PF"))
    import winreg

    def no_registry(*_args, **_kwargs):
        raise OSError("no key")

    monkeypatch.setattr(winreg, "OpenKey", no_registry)
    assert office_provider.find_libreoffice() == str(exe.resolve())
    # An explicit but missing configuration never silently falls back.
    assert office_provider.find_libreoffice(str(tmp_path / "missing" / "soffice.exe")) is None


def test_system_libreoffice_lookup_can_be_disabled(monkeypatch):
    monkeypatch.setenv("RIGHTCONVERT_SYSTEM_OFFICE_LOOKUP", "0")
    monkeypatch.setenv("PATH", "")
    assert office_provider.find_libreoffice() is None
