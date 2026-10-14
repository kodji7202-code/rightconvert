"""Stage 6 document GUI acceptance through real hidden Tk widgets.

Only disposable test files, a temporary LOCALAPPDATA and withdrawn windows are
used. No live application profile, registry, Explorer or Office install touched.
"""

from __future__ import annotations

import hashlib
import threading
import time
from pathlib import Path

from PIL import Image
import pytest
import tkinter as tk

from rightconvert import app as gui
from rightconvert import config
from rightconvert.formats import ConversionCancelled


def _pump(app, predicate, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.root.update()
        if predicate():
            app.root.update()
            return
        time.sleep(0.015)
    raise AssertionError(f"Tk operation stalled: {app.summary_var.get()}")


@pytest.fixture(scope="module")
def tk_host():
    try:
        host = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk display unavailable: {exc}")
    host.withdraw()
    yield host
    host.destroy()


@pytest.fixture
def isolated_tk(monkeypatch, tmp_path, tk_host):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "user-profile"))
    apps = []

    def hidden_tk():
        window = tk.Toplevel(master=tk_host)
        window.withdraw()
        return window

    monkeypatch.setattr(gui.tk, "Tk", hidden_tk)
    monkeypatch.setattr(gui.RightConvertApp, "_set_native_titlebar", lambda self: None)
    monkeypatch.setattr(gui.RightConvertApp, "_set_window_icon", lambda self: None)

    def create(*args, **kwargs):
        app = gui.RightConvertApp(*args, **kwargs)
        apps.append(app)
        assert app.root.state() == "withdrawn"
        return app

    yield create
    for app in reversed(apps):
        app.cancel_signal.set()
        app.import_cancel.set()
        try:
            app.root.destroy()
        except tk.TclError:
            pass


def _photo(path: Path, *, alpha=False) -> Path:
    mode = "RGBA" if alpha else "RGB"
    color = (150, 60, 120, 140) if alpha else (150, 60, 120)
    Image.new(mode, (32, 24), color).save(path)
    return path


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_combine_visible_order_collision_custom_folder_and_opaque_only(isolated_tk, tmp_path):
    first = _photo(tmp_path / "first.png")
    middle = _photo(tmp_path / "middle.png")
    last = _photo(tmp_path / "last.png")
    transparent = _photo(tmp_path / "alpha.png", alpha=True)
    originals = {p: _hash(p) for p in (first, middle, last, transparent)}
    output = tmp_path / "output"
    output.mkdir()
    existing = output / "last.pdf"
    existing.write_bytes(b"third party existing PDF")
    old_hash = _hash(existing)

    app = isolated_tk()
    app.same_folder_var.set(False)
    app.folder_var.set(str(output))
    app._folder_mode_changed()
    app.queue_files([last, transparent, middle, first])
    _pump(app, lambda: not app.importing)
    assert len(app.files) == 4
    assert app.combine_button.cget("state") == "disabled"
    ids = app.tree.get_children()
    assert [app.files[i].file for i in ids] == [last, transparent, middle, first]
    assert "pdf" not in app.files[ids[1]].options

    app.tree.selection_set([ids[0], ids[1], ids[2]])
    app._refresh_combine_button()
    assert app.combine_button.cget("state") == "disabled"
    app.tree.selection_set([ids[3], ids[0], ids[2]])
    app._refresh_combine_button()
    assert app.combine_button.cget("state") == "normal"
    assert [path for _, path in app._selected_pdf_images()] == [last, middle, first]
    app.combine_button.invoke()
    assert app.running and app.start_button.cget("state") == "disabled"
    assert app.choose_folder_btn.cget("state") == "disabled"
    _pump(app, lambda: not app.running, timeout=15)
    assert app.progress_var.get() == 100
    assert "PDF creat:" in app.summary_var.get()
    combined = output / "last (1).pdf"
    assert combined.is_file()
    with combined.open("rb") as stream:
        assert stream.read(5) == b"%PDF-"
    assert _hash(existing) == old_hash
    assert {p: _hash(p) for p in originals} == originals
    assert all(app.files[i].status == "Combinat în PDF" for i in (ids[0], ids[2], ids[3]))
    assert app.files[ids[1]].status == "Pregătit"


def test_combine_cancel_progress_and_no_partial_output(isolated_tk, monkeypatch, tmp_path):
    first = _photo(tmp_path / "one.png")
    second = _photo(tmp_path / "two.png")
    hashes = (_hash(first), _hash(second))
    started = threading.Event()
    def slow_combine(sources, output_dir, *, cancel, progress):
        assert sources == [first, second]
        assert output_dir == first.parent
        progress(.36)
        started.set()
        assert cancel.wait(timeout=4)
        raise ConversionCancelled("Cancelled during write")

    monkeypatch.setattr(gui, "combine_images_to_pdf", slow_combine)
    app = isolated_tk()
    app.add_files([first, second])
    app.tree.selection_set(app.tree.get_children())
    app._refresh_combine_button()
    app.combine_button.invoke()
    assert started.wait(timeout=2)
    _pump(app, lambda: app.progress_var.get() > 0)
    app.cancel_button.invoke()
    _pump(app, lambda: not app.running)
    assert app.progress_var.get() == 0
    assert "anulată" in app.summary_var.get()
    assert all(row.status == "Anulat" for row in app.files.values())
    assert not list(tmp_path.glob("*.pdf"))
    assert (_hash(first), _hash(second)) == hashes
    assert app.start_button.cget("state") == "normal"


def test_office_chooser_persists_and_reprobes_async(isolated_tk, monkeypatch, tmp_path):
    fake_office = tmp_path / "LibreOffice" / "program" / "soffice.exe"
    fake_office.parent.mkdir(parents=True)
    fake_office.write_bytes(b"dummy: no application is executed")
    source = _photo(tmp_path / "image.png")
    calls = []
    import_finished = threading.Event()

    def probe(path, ffmpeg, office_path=None):
        calls.append(office_path)
        import_finished.set()
        return (["pdf", "webp"], "png")

    monkeypatch.setattr(gui, "_inspect_candidate", probe)
    monkeypatch.setattr(gui, "find_libreoffice",
                        lambda path: str(fake_office) if path == str(fake_office) else None)
    monkeypatch.setattr(gui.filedialog, "askopenfilename", lambda **kwargs: str(fake_office))
    app = isolated_tk()
    app.add_files([source])
    assert calls == [None]
    app.office_button.invoke()
    assert app.importing
    assert app.office_button.cget("state") == "disabled"
    _pump(app, lambda: not app.importing)
    assert calls[-1] == str(fake_office)
    assert config.read_settings()["office_path"] == str(fake_office)
    assert app.office_button.cget("state") == "normal"
    assert app.files["1"].options == ["pdf", "webp"]
    assert import_finished.is_set()


def test_office_conversion_receives_saved_executable(isolated_tk, monkeypatch, tmp_path):
    app = isolated_tk()
    configured = str(tmp_path / "soffice.com")
    app.settings["office_path"] = configured
    office = tmp_path / "report.docx"
    office.write_bytes(b"dummy: conversion backend is stubbed")
    app._add_ready_row(office, ["pdf"], "docx")
    calls = []

    def fake_convert(path, target, output_dir, **kwargs):
        calls.append((path, target, output_dir, kwargs["office_path"]))

    monkeypatch.setattr(gui, "convert_file", fake_convert)
    app._worker([("1", office, "pdf")], False, str(tmp_path))
    app._consume_events()
    assert calls == [(office, "pdf", tmp_path, configured)]
    assert app.files["1"].status == "Finalizat"


def test_office_chooser_rejects_untrusted_binary(isolated_tk, monkeypatch, tmp_path):
    app = isolated_tk()
    spoofed = tmp_path / "malware.exe"
    spoofed.write_bytes(b"not soffice")
    monkeypatch.setattr(gui.filedialog, "askopenfilename", lambda **kwargs: str(spoofed))
    monkeypatch.setattr(gui, "find_libreoffice", lambda path: None)
    shown = []
    monkeypatch.setattr(gui.messagebox, "showerror", lambda *args: shown.append(args))
    app.office_button.invoke()
    assert len(shown) == 1
    assert "office_path" not in config.read_settings()
    assert not app.importing


def test_document_presets_and_pngzip_ui_labels(isolated_tk, tmp_path):
    app = isolated_tk()
    app._add_ready_row(tmp_path / "pages.pdf", ["tiff", "pngzip"], "pdf")
    app._add_ready_row(tmp_path / "sheet.xlsx", ["pdf"], "xlsx")
    app._add_ready_row(tmp_path / "scan.png", ["webp", "pdf"], "png")
    assert gui._target_label("pngzip") == "PNG ZIP (pagini)"
    assert gui._target_value("PNG ZIP (pagini)") == "pngzip"
    assert app.tree.item("1", "values")[2] == "PNG ZIP (pagini)"
    app.preset_var.set("PDF → TIFF")
    app._apply_preset()
    assert [row.target for row in app.files.values()] == ["tiff", "pdf", "webp"]
    app.preset_var.set("Imagini → PDF")
    app._apply_preset()
    assert app.files["3"].target == "pdf"
    assert app.files["2"].target == "pdf"
    app._refresh_formats()
    assert "PNG ZIP (pagini)" not in app.bulk["values"]  # Not shared across different kinds.
    app._set_row_target("1", "pngzip")
    assert app.tree.item("1", "values")[2] == "PNG ZIP (pagini)"


def test_pdf_selection_rejects_more_than_provider_limit(isolated_tk, monkeypatch, tmp_path):
    app = isolated_tk()
    for index in range(gui.MAX_COMBINED_PDF_IMAGES + 1):
        app._add_ready_row(tmp_path / f"{index:03}.png", ["pdf"], "png")
    app.tree.selection_set(app.tree.get_children())
    app._refresh_combine_button()
    assert app.combine_button.cget("state") == "disabled"
    assert app._selected_pdf_images() == []
    shown = []
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: shown.append(a))
    app._combine_selected()
    assert "64" in shown[0][1]
