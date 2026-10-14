"""Stage 5 GUI acceptance with real, withdrawn Tcl/Tk widgets.

Everything here operates on disposable pytest files and an isolated LOCALAPPDATA.
These tests call actual Tk buttons/methods; they do not perform native mouse
clicks or interact with RightConvert's installed Explorer integration.
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
from rightconvert.formats import ConversionCancelled


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _jpeg(path: Path, size=(36, 24)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (38, 102, 177)).save(path, format="JPEG")
    return path


def _webp(path: Path, size=(36, 24)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (82, 134, 55)).save(path, format="WEBP")
    return path


def _until(app: gui.RightConvertApp, condition, *, timeout=8.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.root.update()
        if condition():
            app.root.update()
            return
        time.sleep(0.015)
    raise AssertionError(
        f"Tk event condition not reached in {timeout}s; "
        f"importing={app.importing}, running={app.running}, "
        f"rows={len(app.files)}, summary={app.summary_var.get()!r}"
    )


@pytest.fixture(scope="module")
def tk_host():
    """Keep one withdrawn Tcl interpreter alive across tests on Windows.

    Repeated Tk() creation/destruction triggers occasional Windows Tcl
    `invalid command name tcl_findLibrary` errors unrelated to RightConvert.
    Each app instead owns a fresh, real Tk Toplevel under this hidden host.
    """
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk interpreter/display unavailable: {exc}")
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def isolated_tk(monkeypatch, tmp_path: Path, tk_host):
    """Create real Tk widgets in isolated, withdrawn Toplevel windows."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-localappdata"))
    apps: list[gui.RightConvertApp] = []

    def hidden_tk():
        root = gui.tk.Toplevel(master=tk_host)
        root.withdraw()
        return root

    monkeypatch.setattr(gui.tk, "Tk", hidden_tk)
    # Do not touch global native titlebar or Taskbar/Explorer UI at all.
    monkeypatch.setattr(gui.RightConvertApp, "_set_native_titlebar", lambda self: None)
    monkeypatch.setattr(gui.RightConvertApp, "_set_window_icon", lambda self: None)

    def create(*args, **kwargs) -> gui.RightConvertApp:
        # A TclError during the application's actual widget construction is a
        # real test failure, not an unavailable-desktop SKIP.
        app = gui.RightConvertApp(*args, **kwargs)
        assert app.root.state() == "withdrawn"
        apps.append(app)
        return app

    yield create
    for app in reversed(apps):
        app.import_cancel.set()
        app.cancel_signal.set()
        try:
            # Do not call _on_close(): it saves settings. The pytest profile
            # is isolated, but teardown should not write anything anyway.
            app.root.destroy()
        except gui.tk.TclError:
            pass


def test_real_tk_custom_output_unicode_collision_preset_and_conversion(
    isolated_tk, tmp_path: Path
):
    original = _jpeg(tmp_path / "intrare_î_東京.jpg")
    before = _sha256(original)
    destination = tmp_path / "rezultate separate"
    destination.mkdir()
    preexisting = destination / "intrare_î_東京.png"
    preexisting.write_bytes(b"third-party data, never overwrite")
    preexisting_hash = _sha256(preexisting)

    app = isolated_tk()
    app.same_folder_var.set(False)
    app.folder_var.set(str(destination))
    app._folder_mode_changed()
    assert app.folder_entry.cget("state") == "normal"

    app.queue_files([original])
    assert app.importing and app.start_button.cget("state") == "disabled"
    _until(app, lambda: not app.importing)
    assert len(app.files) == 1
    row = next(iter(app.files.values()))
    assert row.source_format == "jpg"
    assert app.tree.item(next(iter(app.files)), "values")[1] == "JPG"
    app.preset_var.set("Imagini → PNG")
    app._apply_preset()  # Real ttk.Combobox/StringVar, called without mouse.
    assert row.target == "png"
    assert app.preset.get() == "Imagini → PNG"

    app.start_button.invoke()  # Actual Tk button command, no native click.
    _until(app, lambda: not app.running)
    assert row.status == "Finalizat"
    assert app.progress_var.get() == 100
    converted = destination / "intrare_î_東京 (1).png"
    assert converted.is_file()
    with Image.open(converted) as image:
        assert image.format == "PNG"
        assert image.size == (36, 24)
        image.verify()
    assert _sha256(original) == before
    assert _sha256(preexisting) == preexisting_hash
    assert not list(destination.glob(".rightconvert-stage-*"))
    assert not (original.parent / converted.name).exists()


def test_real_tk_directory_picker_recursive_import_source_sniff_and_presets(
    isolated_tk, monkeypatch, tmp_path: Path
):
    folder = tmp_path / "albume"
    nested = _webp(folder / "amestec" / "東京" / "copertă.webp")
    top = _jpeg(folder / "imagine-rădăcină.jpg")
    unsupported = folder / "amestec" / "notes.txt"
    unsupported.write_text("not a media file", encoding="utf-8")
    hashes = {p: _sha256(p) for p in (nested, top, unsupported)}
    external = _jpeg(tmp_path / "private" / "other.jpg")
    link_created = False
    try:
        (folder / "shortcut").symlink_to(external.parent, target_is_directory=True)
        link_created = True
    except (OSError, NotImplementedError):
        pass

    app = isolated_tk()
    destination = tmp_path / "converted-folder-results"
    destination.mkdir()
    app.same_folder_var.set(False)
    app.folder_var.set(str(destination))
    app._folder_mode_changed()
    app.preset_var.set("Imagini → PNG")
    monkeypatch.setattr(gui.filedialog, "askdirectory", lambda **_kw: str(folder))
    app.add_folder_button.invoke()  # Real Tcl/Tk button invokes the dialog flow.
    assert app.importing and app.cancel_button.cget("state") == "normal"
    _until(app, lambda: not app.importing)
    assert {row.file for row in app.files.values()} == {nested, top}
    assert all(row.target == "png" for row in app.files.values())
    rows = {row.file: row for row in app.files.values()}
    assert rows[nested].display_name == str(nested.relative_to(folder))
    assert rows[top].source_format == "jpg"
    assert rows[nested].source_format == "webp"
    if link_created:
        assert "linkuri ignorate" in app.summary_var.get()
    assert all(_sha256(path) == hash_before for path, hash_before in hashes.items())
    assert not any(p.suffix == ".png" for p in folder.rglob("*.png"))
    assert external.exists()
    app.start_button.invoke()
    _until(app, lambda: not app.running)
    assert all(row.status == "Finalizat" for row in app.files.values())
    assert {p.name for p in destination.iterdir()} == {"copertă.png", "imagine-rădăcină.png"}
    with Image.open(destination / "copertă.png") as image:
        assert image.format == "PNG"
        image.verify()
    assert all(_sha256(path) == hash_before for path, hash_before in hashes.items())
    assert not list(destination.glob(".rightconvert-stage-*"))


def test_real_tk_import_cancel_preserves_files_and_reenables_controls(
    isolated_tk, monkeypatch, tmp_path: Path
):
    folder = tmp_path / "huge-list"
    folder.mkdir()
    files = [_jpeg(folder / f"{index:03}.jpg") for index in range(30)]
    originals = {path: _sha256(path) for path in files}
    original_inspect = gui._inspect_candidate
    started = threading.Event()

    def deliberately_slow_probe(path, ffmpeg_path):
        started.set()
        time.sleep(0.04)
        return original_inspect(path, ffmpeg_path)

    monkeypatch.setattr(gui, "_inspect_candidate", deliberately_slow_probe)
    app = isolated_tk()
    app.queue_folder(folder)
    assert started.wait(1.5)
    app.cancel_button.invoke()
    assert app.import_cancel.is_set()
    _until(app, lambda: not app.importing)
    assert "import anulat" in app.summary_var.get()
    assert len(app.files) < len(files)
    assert app.add_folder_button.cget("state") == "normal"
    assert app.start_button.cget("state") == "normal"
    assert all(_sha256(path) == digest for path, digest in originals.items())


def test_real_tk_folder_symlink_outside_root_is_ignored_when_os_supports_it(
    isolated_tk, tmp_path: Path
):
    chosen = tmp_path / "chosen"
    chosen.mkdir()
    inside = _jpeg(chosen / "inside.jpg")
    outside = _jpeg(tmp_path / "other-place" / "private.jpg")
    shortcut = chosen / "redirect"
    try:
        shortcut.symlink_to(outside.parent, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Creating a real directory symlink is unavailable: {type(exc).__name__}")

    app = isolated_tk()
    app.queue_folder(chosen)
    _until(app, lambda: not app.importing)
    assert {row.file for row in app.files.values()} == {inside}
    assert "linkuri ignorate" in app.summary_var.get()
    assert outside.is_file()


def test_real_tk_conversion_cancel_updates_status_without_artifact(
    isolated_tk, monkeypatch, tmp_path: Path
):
    source = _jpeg(tmp_path / "input.jpg")
    before = _sha256(source)
    destination = tmp_path / "cancelled-outputs"
    destination.mkdir()
    processing = threading.Event()

    def simulated_slow_backend(path, fmt, outdir, *, cancel, progress, ffmpeg_path, office_path=None):
        assert path == source and outdir == destination
        progress(0.31)
        processing.set()
        if not cancel.wait(timeout=3):
            raise AssertionError("Test backend did not receive cancellation")
        raise ConversionCancelled("simulated mid-conversion cancellation")

    monkeypatch.setattr(gui, "convert_file", simulated_slow_backend)
    app = isolated_tk()
    app.same_folder_var.set(False)
    app.folder_var.set(str(destination))
    app._folder_mode_changed()
    app.queue_files([source])
    _until(app, lambda: not app.importing)
    app.start_button.invoke()
    assert processing.wait(2)
    assert app.cancel_button.cget("state") == "normal"
    app.cancel_button.invoke()
    _until(app, lambda: not app.running)
    assert all(row.status == "Anulat" for row in app.files.values())
    assert app.progress_var.get() == 0
    assert "anulată" in app.summary_var.get()
    assert app.start_button.cget("state") == "normal"
    assert _sha256(source) == before
    assert list(destination.iterdir()) == []


def test_real_tk_output_settings_persist_only_in_isolated_profile(
    isolated_tk, monkeypatch, tmp_path: Path
):
    destination = tmp_path / "settings-destination"
    destination.mkdir()
    monkeypatch.setattr(gui.filedialog, "askdirectory", lambda **_kw: str(destination))
    first = isolated_tk()
    first.same_folder_var.set(False)
    first._folder_mode_changed()
    assert first.choose_folder_btn.cget("state") == "normal"
    first.choose_folder_btn.invoke()
    assert first.folder_var.get() == str(destination)
    assert first.same_folder_var.get() is False
    first.root.destroy()

    reopened = isolated_tk()
    assert reopened.same_folder_var.get() is False
    assert reopened.folder_var.get() == str(destination)
    assert reopened.folder_entry.cget("state") == "normal"
    assert (tmp_path / "isolated-localappdata" / "RightConvert" / "settings.json").is_file()
