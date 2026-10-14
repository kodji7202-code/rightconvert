"""Stage7 security acceptance against disposable files and in-process CLI.

Never register shell extensions, install packages or modify user settings.
The native smoke executable is intentionally excluded; its independent COM
tests already exercise Unicode argv without invoking installed Explorer.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import socket
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from rightconvert.cli import main
from rightconvert.engine import ConversionError, convert_file, get_targets
from rightconvert.office_provider import inspect_office


def _sha(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _image(path: Path, *, color: tuple[int, int, int] = (30, 100, 170)) -> Path:
    Image.new("RGB", (24, 20), color).save(path, format="PNG")
    return path


@pytest.fixture
def isolated_logger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Use only a temporary LOCALAPPDATA tree, including error logs."""
    folder = tmp_path / "private-appdata"
    monkeypatch.setenv("LOCALAPPDATA", str(folder))
    logger = logging.getLogger("rightconvert")
    before = list(logger.handlers)
    for handler in before:
        logger.removeHandler(handler)
    try:
        yield folder
    finally:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.flush()
            handler.close()
        for handler in before:
            logger.addHandler(handler)


def _no_owned_staging(folder: Path) -> None:
    assert not list(folder.rglob(".rightconvert-stage-*"))


def test_direct_unicode_shell_metacharacters_are_literal_and_source_unmodified(
    tmp_path: Path, isolated_logger: Path
) -> None:
    source = _image(tmp_path / "f & ^ (x) %VAR% !bang! '$' 東京 ședință.png")
    original = _sha(source)
    rc = main(["--files", str(source), "--target", "webp", "--direct"])
    assert rc == 0
    output = tmp_path / f"{source.stem}.webp"
    with Image.open(output) as decoded:
        decoded.verify()
        assert decoded.format == "WEBP"
    assert _sha(source) == original
    assert not (tmp_path / "VAR").exists()
    _no_owned_staging(tmp_path)


def test_direct_uses_fixed_target_and_never_executes_shell_text(
    tmp_path: Path, isolated_logger: Path, monkeypatch
) -> None:
    source = _image(tmp_path / "command $(touch innocent).png")
    sentinel = tmp_path / "must-not-run.txt"
    # Real conversion must not call shell=True or an executable from filename.
    original_popen = subprocess.Popen
    launched: list[object] = []

    def guarded(*args, **kwargs):
        assert kwargs.get("shell") is not True
        launched.append(args[0] if args else kwargs.get("args"))
        return original_popen(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", guarded)
    assert main(["--files", str(source), "--target", "webp", "--direct"]) == 0
    assert not sentinel.exists()
    assert not launched, "Image direct conversion should not spawn a subprocess"


@pytest.mark.parametrize("extra", [
    ["--notify"], ["--direct"], ["--auto-start"], ["--target", "webp"],
    ["--files", "file.png", "--target", "webp", "--notify"],
    ["--files", "file.png", "--target", "webp", "--direct", "--auto-start"],
    ["--files", "file.png", "--target", "webp;echo", "--direct"],
])
def test_cli_rejects_missing_or_invalid_switches(extra: list[str]) -> None:
    with pytest.raises(SystemExit) as error:
        main(extra)
    assert error.value.code == 2


def test_direct_preflight_rejects_poisoned_batch_without_partial_results(
    tmp_path: Path, isolated_logger: Path
) -> None:
    first = _image(tmp_path / "one.png")
    deceptive = tmp_path / "two.png"
    deceptive.write_bytes(b"malicious junk with false PNG suffix")
    assert main([
        "--files", str(first), str(deceptive), "--target", "webp", "--direct",
    ]) == 2
    assert not list(tmp_path.glob("*.webp"))
    assert _sha(first) == hashlib.sha256(first.read_bytes()).hexdigest()
    assert deceptive.read_bytes() == b"malicious junk with false PNG suffix"
    _no_owned_staging(tmp_path)


def test_direct_does_not_use_unrelated_gui_output_preference(
    tmp_path: Path, isolated_logger: Path
) -> None:
    """A quick Explorer action writes next to the selected file only."""
    source_dir = tmp_path / "selected"
    source_dir.mkdir()
    source = _image(source_dir / "quick.png")
    unrelated = tmp_path / "configured_gui_output"
    unrelated.mkdir()
    settings = isolated_logger / "RightConvert" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps({
        "output_dir": str(unrelated), "same_folder": False,
        "ffmpeg_path": "", "office_path": "",
    }), encoding="utf-8")
    assert main(["--files", str(source), "--target", "webp", "--direct"]) == 0
    assert (source_dir / "quick.webp").is_file()
    assert not list(unrelated.iterdir())


def test_direct_rejects_1001_selection_without_outputs(
    tmp_path: Path, isolated_logger: Path
) -> None:
    source = _image(tmp_path / "one.png")
    assert main(["--files", *([str(source)] * 1001),
                 "--target", "webp", "--direct"]) == 2
    assert not list(tmp_path.glob("*.webp"))


def test_direct_colliding_destination_symlink_does_not_modify_victim(
    tmp_path: Path, isolated_logger: Path
) -> None:
    source = _image(tmp_path / "alpha.png")
    victim = tmp_path / "protected-foreign.txt"
    victim.write_bytes(b"NEVER-CHANGE-THIS")
    symlink = tmp_path / "alpha.webp"
    try:
        symlink.symlink_to(victim)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"File symlink creation unavailable: {exc}")
    victim_sha = _sha(victim)
    assert main(["--files", str(source), "--target", "webp", "--direct"]) == 0
    assert symlink.is_symlink()
    assert _sha(victim) == victim_sha
    fresh = tmp_path / "alpha (1).webp"
    with Image.open(fresh) as image:
        image.verify()
    _no_owned_staging(tmp_path)


def test_direct_existing_hardlinked_destination_does_not_write_through(
    tmp_path: Path, isolated_logger: Path
) -> None:
    source = _image(tmp_path / "alpha.png")
    victim = tmp_path / "hardlink-protected.bin"
    victim.write_bytes(b"UNCHANGED ORIGINAL VICTIM")
    os.link(victim, tmp_path / "alpha.webp")
    victim_sha = _sha(victim)
    assert main(["--files", str(source), "--target", "webp", "--direct"]) == 0
    assert _sha(victim) == victim_sha
    assert (tmp_path / "alpha (1).webp").is_file()
    _no_owned_staging(tmp_path)


def test_direct_source_file_symlink_keeps_real_source_untouched(
    tmp_path: Path, isolated_logger: Path
) -> None:
    """Characterize CLI link handling without reading unrelated host files."""
    real_dir = tmp_path / "real-source"
    real_dir.mkdir()
    real_source = _image(real_dir / "original.png")
    visible_dir = tmp_path / "selected-links"
    visible_dir.mkdir()
    selected = visible_dir / "alias.png"
    try:
        selected.symlink_to(real_source)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"File symlink creation unavailable: {exc}")
    original_hash = _sha(real_source)
    assert main(["--files", str(selected), "--target", "webp", "--direct"]) == 0
    assert _sha(real_source) == original_hash
    assert selected.is_symlink()
    # Engine resolves the selected link before naming its output. This is the
    # *target* basename, a privacy/UX consideration documented in the audit.
    with Image.open(visible_dir / "original.webp") as preview:
        preview.verify()
    # Current CLI follows a file symlink deliberately. That behavior is
    # reported separately so callers can decide whether to disallow it.


def test_direct_rejects_reparse_or_symlink_output_directory(
    tmp_path: Path, isolated_logger: Path
) -> None:
    """Regression: a selected output root must not silently redirect writes."""
    outer = tmp_path / "visible"
    outer.mkdir()
    real = tmp_path / "separate-private-target"
    real.mkdir()
    alias = outer / "linked-output"
    try:
        alias.symlink_to(real, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Directory symlink creation unavailable: {exc}")
    source = _image(tmp_path / "picture.png")
    original_sha = _sha(source)
    # The target is a deliberately untrusted directory alias. Running this
    # test is safe because both its endpoints are within tmp_path.
    try:
        convert_file(source, "webp", alias)
    except ConversionError:
        pass  # Expected fail-closed policy
    else:
        pytest.fail("Reparse-point output root redirected publication into a different directory")
    assert _sha(source) == original_sha
    assert not list(real.iterdir())


def test_direct_rejects_reparse_parent_of_output_directory(
    tmp_path: Path, isolated_logger: Path
) -> None:
    source = _image(tmp_path / "sample.png")
    actual_root = tmp_path / "target"
    actual_root.mkdir()
    shortcut = tmp_path / "redirected-parent"
    try:
        shortcut.symlink_to(actual_root, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Directory symlink creation unavailable: {exc}")
    with pytest.raises(ConversionError, match="reparse|junction|redirect"):
        convert_file(source, "webp", shortcut / "child")
    assert not list(actual_root.iterdir())


def test_direct_reparse_swap_after_preflight_must_not_publish_outside_destination(
    tmp_path: Path, isolated_logger: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deterministic adversarial timing: swap parent after validation.

    Both directories are private pytest fixtures. This injects a mutation at
    the first staging creation, just after _safe_output_directory returns.
    """
    import rightconvert.engine as engine

    source = _image(tmp_path / "normal-input.png")
    selected = tmp_path / "selected-destination"
    selected.mkdir()
    victim = tmp_path / "unrelated-destination"
    victim.mkdir()
    source_hash = _sha(source)
    original_staging = engine._create_private_staging
    performed = []

    def substitute_output_root(path: Path) -> Path:
        if Path(path) == selected and not performed:
            original_dir = tmp_path / "selected-original-directory"
            selected.rename(original_dir)
            selected.symlink_to(victim, target_is_directory=True)
            performed.append(True)
        return original_staging(path)

    monkeypatch.setattr(engine, "_create_private_staging", substitute_output_root)
    try:
        convert_file(source, "webp", selected)
    except ConversionError:
        pass
    assert performed, "Test did not execute the post-validation substitution"
    assert _sha(source) == source_hash
    assert not list(victim.iterdir()), "Post-check symlink swap redirected a committed output"


def test_direct_preserves_existing_colliding_output_and_unicode_source(
    tmp_path: Path, isolated_logger: Path
) -> None:
    original = _image(tmp_path / "фото 文件 ș.png")
    destination = tmp_path / f"{original.stem}.webp"
    destination.write_bytes(b"existing unrelated data")
    digest = _sha(original), _sha(destination)
    assert main(["--files", str(original), "--target", "webp", "--direct"]) == 0
    assert (_sha(original), _sha(destination)) == digest
    with Image.open(tmp_path / f"{original.stem} (1).webp") as decoded:
        decoded.verify()


def test_direct_error_log_never_records_full_private_path(
    tmp_path: Path, isolated_logger: Path
) -> None:
    secret = tmp_path / "TOP-SECRET-CUSTOMER-NAME" / "sensitive-legal-file.png"
    secret.parent.mkdir()
    secret.write_bytes(b"invalid fake PNG")
    assert main(["--files", str(secret), "--target", "webp", "--direct"]) == 2
    logfile = isolated_logger / "RightConvert" / "logs" / "technical.log"
    assert logfile.is_file()
    content = logfile.read_text(encoding="utf-8")
    assert "Quick conversion incompatible source:" in content
    assert "TOP-SECRET-CUSTOMER-NAME" not in content
    assert "sensitive-legal-file" not in content
    assert str(tmp_path) not in content
    assert "Traceback" not in content


def test_direct_internal_error_log_sanitizes_raw_exception(
    tmp_path: Path, isolated_logger: Path, monkeypatch
) -> None:
    import rightconvert.engine as engine

    source = _image(tmp_path / "secretcase.png")
    exact_secret = "API-KEY-DO-NOT-LEAK"

    def dangerous_error(*args, **kwargs):
        raise RuntimeError(f"Private token={exact_secret}; path={source}")

    monkeypatch.setattr(engine, "convert_file", dangerous_error)
    assert main(["--files", str(source), "--target", "webp", "--direct"]) == 1
    log = (isolated_logger / "RightConvert" / "logs" / "technical.log").read_text(encoding="utf-8")
    assert "RuntimeError" in log
    assert exact_secret not in log and str(source) not in log
    assert not (tmp_path / "secretcase.webp").exists()


def test_technical_log_must_not_follow_untrusted_file_symlink(
    tmp_path: Path, isolated_logger: Path
) -> None:
    """A log symlink may redirect otherwise local app logs to another target."""
    bad = tmp_path / "invalid.png"
    bad.write_bytes(b"not an image")
    external = tmp_path / "other-owned-folder"
    external.mkdir()
    victim = external / "unrelated.log"
    victim.write_text("UNCHANGED\n", encoding="utf-8")
    log_dir = isolated_logger / "RightConvert" / "logs"
    log_dir.mkdir(parents=True)
    try:
        (log_dir / "technical.log").symlink_to(victim)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Symlink creation unavailable: {exc}")
    assert main(["--files", str(bad), "--target", "webp", "--direct"]) == 2
    assert victim.read_text(encoding="utf-8") == "UNCHANGED\n", (
        "Direct conversion log escaped its private log directory via a symlink"
    )


def test_direct_rejects_fake_office_and_corrupt_pdf_preflight(
    tmp_path: Path, isolated_logger: Path
) -> None:
    good = _image(tmp_path / "valid.png")
    fake_office = tmp_path / "fake.docx"
    fake_office.write_bytes(b"PK\x03\x04truncated zip payload")
    fake_pdf = tmp_path / "invalid.pdf"
    fake_pdf.write_bytes(b"%PDF-1.7\nnot real page objects\n")
    for malicious, target in ((fake_office, "pdf"), (fake_pdf, "pngzip")):
        snapshot = _sha(good), _sha(malicious)
        assert get_targets(malicious) == []
        assert main(["--files", str(malicious), "--target", target, "--direct"]) == 2
        assert (_sha(good), _sha(malicious)) == snapshot
    assert not list(tmp_path.glob("*.zip"))
    assert not list(tmp_path.glob("*.webp"))
    _no_owned_staging(tmp_path)


def test_direct_passive_office_blocks_relationship_to_loopback(
    tmp_path: Path, isolated_logger: Path
) -> None:
    """Actual minimal DOCX with relationship to local listener: no packet."""
    ns = "http://schemas.openxmlformats.org"
    source = tmp_path / "external-link.docx"
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(0.25)
        remote = f"http://127.0.0.1:{listener.getsockname()[1]}/secret"
        with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as doc:
            doc.writestr("[Content_Types].xml",
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Override PartName="/word/document.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                '</Types>')
            doc.writestr("_rels/.rels",
                f'<Relationships xmlns="{ns}/package/2006/relationships">'
                f'<Relationship Id="rId1" Type="{ns}/officeDocument/2006/relationships/officeDocument" '
                'Target="word/document.xml"/></Relationships>')
            doc.writestr("word/document.xml",
                f'<w:document xmlns:w="{ns}/wordprocessingml/2006/main">'
                '<w:body><w:p><w:r><w:t>hello</w:t></w:r></w:p></w:body></w:document>')
            doc.writestr("word/_rels/document.xml.rels",
                f'<Relationships xmlns="{ns}/package/2006/relationships">'
                f'<Relationship Id="rId2" Type="{ns}/officeDocument/2006/relationships/hyperlink" '
                f'Target="{remote}" TargetMode="External"/></Relationships>')
        assert inspect_office(source) is None
        assert main(["--files", str(source), "--target", "pdf", "--direct"]) == 2
        with pytest.raises(socket.timeout):
            listener.accept()
    assert not list(tmp_path.glob("*.pdf"))


def test_direct_rejects_spreadsheet_external_formula(
    tmp_path: Path, isolated_logger: Path
) -> None:
    """A minimal OOXML workbook with WEBSERVICE must remain unsupported."""
    spreadsheet = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    package = "http://schemas.openxmlformats.org/package/2006"
    office_rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    source = tmp_path / "malicious.xlsx"
    with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '</Types>')
        archive.writestr("_rels/.rels",
            f'<Relationships xmlns="{package}/relationships"><Relationship Id="rId1" '
            f'Type="{office_rel}/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr("xl/workbook.xml",
            f'<workbook xmlns="{spreadsheet}" xmlns:r="{office_rel}">'
            '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels",
            f'<Relationships xmlns="{package}/relationships"><Relationship Id="rId1" '
            f'Type="{office_rel}/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        archive.writestr("xl/worksheets/sheet1.xml",
            f'<worksheet xmlns="{spreadsheet}"><sheetData><row r="1"><c r="A1">'
            '<f>WEBSERVICE("http://127.0.0.1:12345/a")</f>'
            '</c></row></sheetData></worksheet>')
    assert inspect_office(source) is None
    assert main(["--files", str(source), "--target", "pdf", "--direct"]) == 2
    assert not (tmp_path / "malicious.pdf").exists()


def test_direct_rejects_macro_object_office_archive(
    tmp_path: Path, isolated_logger: Path
) -> None:
    source = tmp_path / "vba-in-word.docx"
    with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '</Types>')
        archive.writestr("word/vbaProject.bin", b"malicious binary in ZIP")
        archive.writestr("word/document.xml", b"<document/>")
    assert inspect_office(source) is None
    assert main(["--files", str(source), "--target", "pdf", "--direct"]) == 2
    assert not (tmp_path / "vba-in-word.pdf").exists()


def test_direct_pdf_link_does_not_request_loopback(
    tmp_path: Path, isolated_logger: Path
) -> None:
    fitz = pytest.importorskip("fitz", reason="Optional PDF fixture generator absent")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(2)
        listener.settimeout(0.25)
        pdf_path = tmp_path / "untrusted-annotation.pdf"
        document = fitz.open()
        try:
            page = document.new_page(width=180, height=180)
            page.insert_link({
                "kind": fitz.LINK_URI,
                "from": fitz.Rect(15, 15, 130, 60),
                "uri": f"http://127.0.0.1:{listener.getsockname()[1]}/fetch",
            })
            document.save(str(pdf_path))
        finally:
            document.close()
        before = _sha(pdf_path)
        if "pngzip" not in get_targets(pdf_path):
            pytest.skip("PDFium PNG ZIP renderer not available")
        assert main(["--files", str(pdf_path), "--target", "pngzip", "--direct"]) == 0
        with pytest.raises(socket.timeout):
            listener.accept()
        assert _sha(pdf_path) == before
        rendered = tmp_path / "untrusted-annotation-pages.zip"
        assert rendered.is_file()
        with zipfile.ZipFile(rendered) as archive:
            assert len(archive.namelist()) == 1


@pytest.mark.skipif(os.name != "nt", reason="Native Windows COM smoke required")
def test_native_classic_com_argument_smoke_in_disposable_folder() -> None:
    """The compiled COM smoke tests 11 fixed target CLSIDs and Unicode argv."""
    root = Path(__file__).resolve().parents[1]
    built = root / "shell" / "classic" / "out" / "x64" / "Release"
    exe = built / "RightConvertClassicSmoke.exe"
    dll = built / "RightConvertClassic.dll"
    if not exe.is_file() or not dll.is_file():
        pytest.skip("Stage7 compiled native smoke artifacts are absent")
    result = subprocess.run(
        [str(exe), str(dll)], capture_output=True, text=True, timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "11 fixed direct targets; 21 Unicode paths per launch" in result.stdout


@pytest.mark.skipif(os.name != "nt", reason="Windows CRT subprocess argument test")
def test_windows_native_commandline_args_handle_unicode_and_spaces(
    tmp_path: Path, isolated_logger: Path
) -> None:
    """Subprocess list uses CreateProcess quoting; no cmd.exe shell involved."""
    source = _image(tmp_path / "one ^ & % Ä😀.png")
    report = tmp_path / "argv.txt"
    script = (
        "import sys,pathlib;"
        "pathlib.Path(sys.argv[1]).write_text('\\n'.join(sys.argv[2:]),encoding='utf-8')"
    )
    result = subprocess.run([
        sys.executable, "-c", script, str(report), "--files", str(source),
        "--target", "webp", "--direct",
    ], capture_output=True, text=True, timeout=10, check=False)
    assert result.returncode == 0, result.stderr
    assert report.read_text(encoding="utf-8").splitlines() == [
        "--files", str(source), "--target", "webp", "--direct",
    ]
