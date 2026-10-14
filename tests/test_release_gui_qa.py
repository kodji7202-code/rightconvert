"""Independent Stage 7 customer-facing GUI QA.

Runs real withdrawn Tcl/Tk widgets with an isolated test LOCALAPPDATA.
No production install, registry, Explorer menu, network or real FFmpeg/Office
process is used. Tests named 'observed' document current shortcomings rather
than silently treating missing functionality as accepted for paid customers.
"""

from __future__ import annotations

import hashlib
import time
import threading
import wave
import zipfile
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest
import tkinter as tk

from rightconvert import app as ui
from rightconvert import config, office_provider
from rightconvert.formats import ConversionCancelled


def _pump(app, done, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.root.update()
        if done():
            app.root.update()
            return
        time.sleep(.015)
    raise AssertionError(
        f"Tk event timed out: importing={app.importing} running={app.running} "
        f"status={app.summary_var.get()!r}"
    )


def _picture(path, mode="RGB"):
    Image.new(mode, (48, 30), (40, 115, 190) if mode == "RGB"
              else (40, 115, 190, 180)).save(path)
    return path


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _passive_docx(path):
    """Minimal standards-shaped OOXML input independently checked by provider."""
    main = "word/document.xml"
    parts = {
        "[Content_Types].xml": (
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.'
            'wordprocessingml.document.main+xml"/></Types>'
        ),
        "_rels/.rels": (
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/></Relationships>'
        ),
        main: (
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body/></w:document>'
        ),
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in parts.items():
            archive.writestr(name, payload)
    assert office_provider.inspect_office(path) == "docx"
    return path


@pytest.fixture(scope="module")
def hidden_host():
    try:
        master = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"No usable Tcl/Tk display: {exc}")
    master.withdraw()
    yield master
    master.destroy()


@pytest.fixture
def isolated_gui(monkeypatch, tmp_path, hidden_host):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-user-data"))
    collected = []
    logs = []

    def factory():
        window = tk.Toplevel(master=hidden_host)
        window.withdraw()
        return window

    monkeypatch.setattr(ui.tk, "Tk", factory)
    monkeypatch.setattr(ui.RightConvertApp, "_set_native_titlebar", lambda self: None)
    monkeypatch.setattr(ui.RightConvertApp, "_set_window_icon", lambda self: None)
    monkeypatch.setattr(
        ui, "technical_logger",
        lambda: SimpleNamespace(warning=lambda *parts: logs.append(parts)),
    )

    def create(*args, **kwargs):
        app = ui.RightConvertApp(*args, **kwargs)
        assert app.root.state() == "withdrawn"
        collected.append(app)
        return app

    yield create, logs
    for app in reversed(collected):
        app.import_cancel.set()
        app.cancel_signal.set()
        try:
            app.root.destroy()
        except tk.TclError:
            pass


def test_unactivated_preview_still_converts_a_real_image(isolated_gui, monkeypatch, tmp_path):
    """P1 evidence: all current functionality works without a paid entitlement."""
    create, _ = isolated_gui
    source = _picture(tmp_path / "unlicensed-original.png")
    original_hash = _digest(source)
    assert not (config.data_dir() / "license.rcl").exists()
    calls = []
    import rightconvert.licensing as licensing
    monkeypatch.setattr(
        licensing, "verify_license",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    app = create([source])
    _pump(app, lambda: not app.importing)
    assert app.files
    app.start_button.invoke()
    _pump(app, lambda: not app.running)
    assert (tmp_path / "unlicensed-original.webp").is_file()
    assert original_hash == _digest(source)
    assert calls == []  # The installed verifier has no GUI/CLI hook.
    assert "license" not in config.read_settings()


def test_no_license_status_or_activation_command_in_current_gui(isolated_gui):
    """Observed inventory, separate from any proposed Free/Pro launch contract."""
    create, _ = isolated_gui
    app = create()
    buttons = []

    def visit(widget):
        if isinstance(widget, tk.Button):
            buttons.append(widget.cget("text"))
        for child in widget.winfo_children():
            visit(child)

    visit(app.root)
    assert "Motor Office" in " ".join(buttons)
    assert "Motor FFmpeg" in " ".join(buttons)
    assert not any("licen" in value.casefold() or "activ" in value.casefold()
                   for value in buttons)


def test_valid_docx_first_run_missing_engine_gives_actionable_setup_hint(
    isolated_gui, monkeypatch, tmp_path
):
    """Regression: unsupported first-run DOCX explains missing LibreOffice."""
    create, _ = isolated_gui
    doc = _passive_docx(tmp_path / "customer-document.docx")
    before = _digest(doc)
    monkeypatch.setattr(office_provider, "find_libreoffice", lambda *_: None)
    app = create([doc])
    _pump(app, lambda: not app.importing)
    assert len(app.files) == 0
    assert "1 incompatibile" in app.summary_var.get()
    assert "LibreOffice" in app.summary_var.get()
    assert "Motor Office" in app.summary_var.get()
    assert "Office" in app.office_button.cget("text")
    assert _digest(doc) == before


def test_ffmpeg_picker_rejects_nonworking_binary_and_keeps_previous_setting(
    isolated_gui, monkeypatch, tmp_path
):
    """Regression: inert ffmpeg.exe must not be reported as configured."""
    create, _ = isolated_gui
    fake = tmp_path / "ffmpeg.exe"
    fake.write_bytes(b"this is not an executable or usable media converter")
    monkeypatch.setattr(ui.filedialog, "askopenfilename", lambda **kwargs: str(fake))
    errors = []
    monkeypatch.setattr(ui.messagebox, "showerror", lambda *args, **kwargs: errors.append(args))
    app = create()
    app.ffmpeg_button.invoke()
    assert config.read_settings()["ffmpeg_path"] != str(fake)
    assert errors and "Motor FFmpeg indisponibil" in errors[0][0]


def test_missing_media_backend_only_shows_incompatible_summary(
    isolated_gui, monkeypatch, tmp_path
):
    """Simulates a genuine media header with unavailable external tools."""
    create, _ = isolated_gui
    source = tmp_path / "music.wav"
    with wave.open(str(source), "wb") as sound:
        sound.setnchannels(1)
        sound.setsampwidth(2)
        sound.setframerate(8000)
        sound.writeframes(b"\0\0" * 80)
    before = _digest(source)
    # Confirm UI with an actual media-shaped recognition path and no encoders.
    from rightconvert import formats
    monkeypatch.setattr(formats, "media_tools", lambda *_: None)
    app = create([source])
    _pump(app, lambda: not app.importing)
    assert not app.files
    assert "incompatibile" in app.summary_var.get()
    assert "FFmpeg" not in app.summary_var.get()
    assert _digest(source) == before


def test_valid_ffmpeg_and_ffprobe_picker_saves_verified_path(
    isolated_gui, monkeypatch, tmp_path
):
    create, _ = isolated_gui
    ffmpeg = str(tmp_path / "ffmpeg.exe")
    ffprobe = str(tmp_path / "ffprobe.exe")
    monkeypatch.setattr(ui.filedialog, "askopenfilename", lambda **kwargs: ffmpeg)
    monkeypatch.setattr(ui, "media_tools", lambda _path: (ffmpeg, ffprobe))
    monkeypatch.setattr(ui, "available_encoders", lambda _path: frozenset({"libmp3lame"}))
    executed = []

    def fake_run(command, **kwargs):
        executed.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(ui.subprocess, "run", fake_run)
    app = create()
    app.ffmpeg_button.invoke()
    assert config.read_settings()["ffmpeg_path"] == ffmpeg
    assert executed == [[ffprobe, "-version"]]
    assert "configurat" in app.summary_var.get()


def test_invalid_custom_destination_shows_dialog_without_starting(
    isolated_gui, monkeypatch, tmp_path
):
    create, _ = isolated_gui
    app = create()
    source = _picture(tmp_path / "picture.png")
    app.add_files([source])
    app.same_folder_var.set(False)
    app.folder_var.set(str(tmp_path / "destination-does-not-exist"))
    app._folder_mode_changed()
    errors = []
    monkeypatch.setattr(ui.messagebox, "showerror", lambda *args, **kwargs: errors.append(args))
    before = _digest(source)
    app.start_button.invoke()
    assert not app.running
    assert errors and "Folder invalid" in errors[0][0]
    assert _digest(source) == before
    assert not list(tmp_path.glob("*.webp"))


def test_batch_common_target_and_per_row_compatibility(
    isolated_gui, tmp_path
):
    create, _ = isolated_gui
    app = create()
    app._add_ready_row(tmp_path / "photo.png", ["webp", "jpg", "pdf"], "png")
    app._add_ready_row(tmp_path / "illustration.jpg", ["webp", "pdf"], "jpg")
    app._add_ready_row(tmp_path / "book.docx", ["pdf"], "docx")
    app._refresh_formats()
    assert app.bulk["values"] == ("Format pentru toate…", "PDF")
    app.bulk_var.set("PDF")
    app._apply_bulk(None)
    assert all(row.target == "pdf" for row in app.files.values())
    app._add_ready_row(tmp_path / "sound.wav", ["mp3", "flac"], "wav")
    app._refresh_formats()
    assert app.bulk["values"] == ("Format pentru toate…",)
    # The preset is *only* applied where the engine advertises the target.
    app.preset_var.set("Audio → FLAC")
    app._apply_preset()
    assert app.files["4"].target == "flac"
    assert all(app.files[i].target == "pdf" for i in ("1", "2", "3"))


def test_failed_batch_conversion_exposes_details_and_does_not_lose_other_rows(
    isolated_gui, monkeypatch, tmp_path
):
    create, logs = isolated_gui
    first = _picture(tmp_path / "good.png")
    second = _picture(tmp_path / "bad.png")
    fingerprints = {p: _digest(p) for p in (first, second)}
    app = create()
    app.add_files([first, second])
    app._set_row_target("1", "webp")
    app._set_row_target("2", "webp")

    def convert(path, target, output, **kwargs):
        kwargs["progress"](.5)
        if path == second:
            raise RuntimeError("The destination disk is full")
        _picture(output / "good.webp")

    monkeypatch.setattr(ui, "convert_file", convert)
    shown = []
    monkeypatch.setattr(ui.messagebox, "showerror", lambda *args, **kwargs: shown.append(args))
    app.start_button.invoke()
    _pump(app, lambda: not app.running)
    assert app.files["1"].status == "Finalizat"
    assert app.files["2"].status.startswith("Eroare")
    assert "1 erori" in app.summary_var.get()
    app.tree.focus("2")
    assert app._show_focused_error() == "break"
    assert "The destination disk is full" in shown[0][1]
    assert logs == [("Conversion failed: %s", "RuntimeError")]
    assert {p: _digest(p) for p in fingerprints} == fingerprints
    assert (tmp_path / "good.webp").is_file()
    assert [iid for iid, *_ in app._pending_jobs()] == ["2"]


def test_batch_cancel_stops_later_jobs_without_mutating_originals(
    isolated_gui, monkeypatch, tmp_path
):
    create, _ = isolated_gui
    first = _picture(tmp_path / "first.png")
    second = _picture(tmp_path / "second.png")
    original = (_digest(first), _digest(second))
    app = create()
    app.add_files([first, second])
    entered = threading.Event()
    attempts = []

    def blocking_conversion(path, target, output, **kwargs):
        attempts.append(path)
        kwargs["progress"](.20)
        entered.set()
        assert kwargs["cancel"].wait(4), "QA cancellation was never forwarded"
        raise ConversionCancelled("cancelled in QA")

    monkeypatch.setattr(ui, "convert_file", blocking_conversion)
    app.start_button.invoke()
    assert entered.wait(2)
    assert app.start_button.cget("state") == "disabled"
    assert app.office_button.cget("state") == "disabled"
    app.cancel_button.invoke()
    _pump(app, lambda: not app.running)
    assert attempts == [first]
    assert all(row.status == "Anulat" for row in app.files.values())
    assert not list(tmp_path.glob("*.webp"))
    assert (_digest(first), _digest(second)) == original
    assert app.add_button.cget("state") == "normal"
    assert app.cancel_button.cget("state") == "disabled"


def test_office_picker_invalid_path_preserves_previous_configuration(
    isolated_gui, monkeypatch, tmp_path
):
    create, _ = isolated_gui
    app = create()
    app.settings["office_path"] = str(tmp_path / "previous" / "soffice.exe")
    bogus = tmp_path / "word.exe"
    bogus.write_bytes(b"invalid")
    monkeypatch.setattr(ui.filedialog, "askopenfilename", lambda **kwargs: str(bogus))
    monkeypatch.setattr(ui, "find_libreoffice", lambda *_: None)
    errors = []
    monkeypatch.setattr(ui.messagebox, "showerror", lambda *args: errors.append(args))
    app.office_button.invoke()
    assert errors and "Motor Office invalid" in errors[0][0]
    assert app.settings["office_path"].endswith("previous" + str(Path("/")) + "soffice.exe")
    assert not app.importing


@pytest.mark.parametrize("width,height", [(780, 600), (960, 640)])
def test_minimum_window_footer_and_progress_remain_visible(isolated_gui, width, height):
    """Map a fully transparent offscreen test window to measure real layout."""
    create, _ = isolated_gui
    app = create()
    assert app.root.minsize() == (780, 600)
    app.root.attributes("-alpha", 0.0)
    app.root.geometry(f"{width}x{height}+30000+30000")
    app.root.deiconify()
    try:
        app.root.update()
        app.root.update_idletasks()
        top = app.root.winfo_rooty()
        bottom = top + app.root.winfo_height()
        widgets = [app.status_label, app.start_button, app.cancel_button,
                   app.progress_bar, app.choose_folder_btn, app.office_button]
        measurements = [
            (type(widget).__name__, widget.winfo_rooty() - top,
             widget.winfo_rooty() + widget.winfo_height() - top,
             bool(widget.winfo_ismapped()))
            for widget in widgets
        ]
        horizontal_widgets = [app.bulk, app.preset, app.combine_button, app.add_button,
                              app.office_button, app.ffmpeg_button]
        horiz = [
            (widget.winfo_rootx() - app.root.winfo_rootx(),
             widget.winfo_rootx() + widget.winfo_width() - app.root.winfo_rootx())
            for widget in horizontal_widgets
        ]
        print(f"WINDOW={width}x{height} WIDGETS={measurements}")
        print(f"HORIZONTAL={horiz} ROOT_WIDTH={app.root.winfo_width()}")
        assert all(0 <= x0 < x1 <= app.root.winfo_width() for x0, x1 in horiz)
        assert all(item[3] and item[2] <= bottom - top for item in measurements)
    finally:
        app.root.withdraw()
