"""UI state machine regressions with Tk-free test doubles (no desktop focus)."""

from __future__ import annotations

import queue
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from rightconvert import app as ui


class Var:
    def __init__(self, value=None):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class Widget:
    def __init__(self):
        self.options = {}

    def configure(self, **options):
        self.options.update(options)


class Tree:
    def __init__(self):
        self.rows = {}
        self.current = "1"
        self.column = "#3"

    def exists(self, iid):
        return iid in self.rows

    def item(self, iid, **options):
        self.rows[iid] = options

    def insert(self, parent, where, *, iid, **options):
        self.rows[iid] = options

    def delete(self, iid):
        self.rows.pop(iid, None)

    def selection(self):
        return [self.current]

    def focus(self):
        return self.current

    def identify_column(self, x):
        return self.column

    def identify_row(self, y):
        return self.current


class Root:
    def __init__(self):
        self.scheduled = []

    def after(self, delay, callback):
        self.scheduled.append((delay, callback))


@pytest.fixture
def bare_app(tmp_path: Path):
    app = ui.RightConvertApp.__new__(ui.RightConvertApp)
    app.root = Root()
    app.files = {}
    app.next_id = 1
    app.events = queue.Queue()
    app.import_events = queue.Queue(maxsize=64)
    app.import_cancel = threading.Event()
    app.cancel_signal = threading.Event()
    app.running = False
    app.importing = False
    app.close_requested = False
    app.preferred_target = None
    app.auto_start_pending = False
    app.auto_excluded = 0
    app.tree = Tree()
    app.settings = {"ffmpeg_path": "", "output_dir": str(tmp_path), "same_folder": False}
    app.colors = ui.LIGHT_COLORS.copy()
    app.log = SimpleNamespace(warning=lambda *args: None)
    app.same_folder_var = Var(False)
    app.folder_var = Var(str(tmp_path))
    app.folder_hint_var = Var("Folderul original")
    app.summary_var = Var("")
    app.progress_var = Var(0)
    app.bulk_var = Var("")
    app.preset_var = Var("Automat")
    for name in (
        "start_button", "cancel_button", "remove_button", "add_button", "add_folder_button",
        "ffmpeg_button", "choose_folder_btn", "bulk", "preset", "same_folder", "folder_entry",
    ):
        setattr(app, name, Widget())
    return app


def test_repeated_start_only_queues_unfinished_rows(bare_app, monkeypatch, tmp_path: Path):
    app = bare_app
    app.files = {
        "1": ui.SelectedFile(tmp_path / "first.jpg", "png", ["png", "webp"], "Finalizat", "jpg"),
        "2": ui.SelectedFile(tmp_path / "second.jpg", "png", ["png", "webp"], "Anulat", "jpg"),
        "3": ui.SelectedFile(tmp_path / "third.jpg", "png", ["png", "webp"], "Eroare · detalii…", "jpg"),
    }
    app.next_id = 4
    assert [row[0] for row in app._pending_jobs()] == ["2", "3"]
    app._set_row_target("1", "png")
    assert app.files["1"].status == "Finalizat"
    app._set_row_target("1", "webp")
    assert app.files["1"].status == "Pregătit"
    assert [row[0] for row in app._pending_jobs()] == ["1", "2", "3"]

    plans = []

    class DormantThread:
        def __init__(self, *, target, args, daemon):
            plans.append(args[0])

        def start(self):
            pass

    monkeypatch.setattr(ui.threading, "Thread", DormantThread)
    app._save_preferences = lambda: None
    app._start()
    assert [row[0] for row in plans[0]] == ["1", "2", "3"]
    assert all(row.status == "În așteptare" for row in app.files.values())
    assert app.add_button.options["state"] == "disabled"
    assert app.folder_entry.options["state"] == "disabled"
    assert app.ffmpeg_button.options["state"] == "disabled"
    assert app.bulk.options["state"] == "disabled"
    assert app.same_folder.options["state"] == "disabled"
    assert app.add_button.options["bg"] == ui.LIGHT_COLORS["surface_alt"]


def test_completed_batch_noop_and_bulk_requeue_only_changed_target(bare_app, monkeypatch, tmp_path: Path):
    app = bare_app
    app.files = {
        "1": ui.SelectedFile(tmp_path / "one.jpg", "png", ["png", "webp"], "Finalizat", "jpg"),
        "2": ui.SelectedFile(tmp_path / "two.jpg", "webp", ["png", "webp"], "Finalizat", "jpg"),
    }
    messages = []
    monkeypatch.setattr(ui.messagebox, "showinfo", lambda *args: messages.append(args))
    app._start()
    assert len(messages) == 1
    assert not app.running
    app.bulk_var.set("webp")
    app._apply_bulk(None)
    assert app.files["1"].status == "Pregătit"
    assert app.files["2"].status == "Finalizat"
    assert [job[0] for job in app._pending_jobs()] == ["1"]


def test_detected_source_format_and_safe_error_dialog(bare_app, monkeypatch, tmp_path: Path):
    app = bare_app
    row = ui.SelectedFile(tmp_path / "disguised.png", "webp", ["webp"], "Pregătit", "jpg")
    app.files["1"] = row
    app._update_row("1")
    assert app.tree.rows["1"]["values"][1] == "JPG"
    detail = "OSError: " + ("Long local diagnostic — " * 30)
    app.events.put(("error", "1", detail))
    app._consume_events()
    assert app.files["1"].error_detail == detail
    assert app.files["1"].status.startswith("Eroare")
    shown = []
    monkeypatch.setattr(ui.messagebox, "showerror", lambda *args, **kw: shown.append((args, kw)))
    assert app._show_focused_error() == "break"
    assert detail in shown[0][0][1]
    assert app.tree.rows["1"]["values"][3] == "Eroare · detalii…"


def test_worker_preserves_complete_error_and_only_logs_exception_type(
    bare_app, monkeypatch, tmp_path: Path
):
    app = bare_app
    source = tmp_path / "broken.wav"
    app.files["1"] = ui.SelectedFile(source, "mp3", ["mp3"], source_format="wav")
    detail = "diagnostic " * 80
    logged = []
    app.log = SimpleNamespace(warning=lambda *args: logged.append(args))

    def fail_conversion(*args, **kwargs):
        raise RuntimeError(detail)

    monkeypatch.setattr(ui, "convert_file", fail_conversion)
    app.running = True
    app._worker([("1", source, "mp3")], False, str(tmp_path))
    app._consume_events()
    assert app.files["1"].error_detail == f"RuntimeError: {detail}"
    assert len(app.files["1"].error_detail) > 130
    assert logged == [("Conversion failed: %s", "RuntimeError")]
    assert not app.running


def test_keyboard_editor_and_busy_guards(bare_app):
    app = bare_app
    app.files["1"] = ui.SelectedFile(Path("sample.jpg"), "png", ["png", "webp"], source_format="jpg")
    opened = []
    app._open_format_editor = lambda iid: opened.append(iid)
    assert app._edit_focused_format() == "break"
    app._edit_format(SimpleNamespace(x=1, y=1))
    assert opened == ["1", "1"]
    app.running = True
    assert app._edit_focused_format() == "break"
    app._edit_format(SimpleNamespace(x=1, y=1))
    app._remove_selected()
    assert opened == ["1", "1"]
    assert "1" in app.files
    app._set_busy_controls()
    assert all(
        getattr(app, name).options["state"] == "disabled"
        for name in ("start_button", "add_button", "remove_button", "ffmpeg_button", "bulk", "folder_entry")
    )


def test_bounded_import_worker_is_nonblocking_and_sniffs_content(bare_app, monkeypatch, tmp_path: Path):
    app = bare_app
    imported = [tmp_path / f"item-{index}.png" for index in range(35)]
    inspected = []

    def probe(path, ffmpeg_path):
        inspected.append(path)
        return ["png", "webp"], "jpg"  # extension lies

    monkeypatch.setattr(ui, "_inspect_candidate", probe)
    before = time.monotonic()
    app.queue_files(imported)
    assert time.monotonic() - before < 0.5
    assert app.importing
    assert app.import_events.maxsize == 64
    assert app.add_button.options["state"] == "disabled"
    limit = time.monotonic() + 3
    while app.import_events.qsize() < 36 and time.monotonic() < limit:
        time.sleep(0.01)
    assert app.import_events.qsize() == 36
    app._consume_import_events()
    assert app.importing
    assert len(app.files) == 24
    app._consume_import_events()
    assert not app.importing
    assert len(app.files) == 35
    assert len(inspected) == 35
    assert all(row.source_format == "jpg" for row in app.files.values())
    assert all(row.target == "webp" for row in app.files.values())
    assert app.start_button.options["state"] == "normal"


def test_single_file_explorer_constructor_does_not_wait_for_slow_probe(
    monkeypatch, tmp_path: Path
):
    """250 ms FFprobe-like delay must occur after Tk initialization, not in it."""
    source = tmp_path / "single-video.mp4"
    source.write_bytes(b"disposable fixture")
    probing = threading.Event()

    def slow_inspection(path, ffmpeg_path):
        assert path == source
        probing.set()
        time.sleep(0.25)
        return ["webm"], "mp4"

    class FakeTkRoot(Root):
        def title(self, value):
            pass

        def geometry(self, value):
            pass

        def minsize(self, x, y):
            pass

        def configure(self, **kwargs):
            pass

        def protocol(self, name, callback):
            pass

    def fake_build_ui(app):
        app.tree = Tree()
        for name in (
            "start_button", "cancel_button", "remove_button", "add_button", "add_folder_button",
            "ffmpeg_button", "choose_folder_btn", "bulk", "preset", "same_folder", "folder_entry",
        ):
            setattr(app, name, Widget())

    monkeypatch.setattr(ui.tk, "Tk", FakeTkRoot)
    monkeypatch.setattr(ui.tk, "BooleanVar", Var)
    monkeypatch.setattr(ui.tk, "StringVar", Var)
    monkeypatch.setattr(ui.tk, "DoubleVar", Var)
    monkeypatch.setattr(ui, "read_settings", lambda: {"same_folder": True, "output_dir": ""})
    monkeypatch.setattr(ui, "technical_logger", lambda: SimpleNamespace(warning=lambda *args: None))
    monkeypatch.setattr(ui.RightConvertApp, "_set_window_icon", lambda self: None)
    monkeypatch.setattr(ui.RightConvertApp, "_set_native_titlebar", lambda self: None)
    monkeypatch.setattr(ui.RightConvertApp, "_theme_style", lambda self: None)
    monkeypatch.setattr(ui.RightConvertApp, "_build_ui", fake_build_ui)
    monkeypatch.setattr(ui, "_inspect_candidate", slow_inspection)

    started = time.monotonic()
    app = ui.RightConvertApp([source])
    init_elapsed = time.monotonic() - started
    assert init_elapsed < 0.20, f"Tk initialization waited for FFprobe: {init_elapsed:.3f}s"
    assert app.importing
    assert not app.files
    assert app.import_events.maxsize == 64
    assert app.start_button.options["state"] == "disabled"
    assert app.summary_var.get().startswith("Se verifică fișierele… 0/1")
    assert probing.wait(timeout=1)

    deadline = time.monotonic() + 3
    while app.importing and time.monotonic() < deadline:
        app._consume_import_events()
        time.sleep(0.01)
    assert app.importing is False
    assert len(app.files) == 1
    row = next(iter(app.files.values()))
    assert row.source_format == "mp4"
    assert row.target == "webm"
    assert app.start_button.options["state"] == "normal"


def test_cancel_terminal_resets_partial_progress(bare_app):
    app = bare_app
    app.running = True
    app.files["1"] = ui.SelectedFile(Path("song.wav"), "mp3", ["mp3"], "În așteptare", "wav")
    app.progress_var.set(29)
    app.events.put(("progress", "1", 45))
    app.events.put(("finished", 0, 0, True))
    app._consume_events()
    assert not app.running
    assert app.progress_var.get() == 0
    assert app.files["1"].status == "Anulat"
    assert "anulată" in app.summary_var.get()
    assert app.folder_entry.options["state"] == "normal"


def _luminance(rgb: str) -> float:
    values = [int(rgb[1 + 2*i:3 + 2*i], 16) / 255 for i in range(3)]
    linear = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in values]
    return sum(weight * channel for weight, channel in zip((0.2126, 0.7152, 0.0722), linear))


@pytest.mark.parametrize("palette", [ui.DARK_COLORS, ui.LIGHT_COLORS])
def test_muted_text_meets_wcag_aa_on_all_panels(palette):
    foreground = _luminance(palette["muted"])
    for field in ("bg", "surface", "surface_alt"):
        background = _luminance(palette[field])
        ratio = (max(foreground, background) + 0.05) / (min(foreground, background) + 0.05)
        assert ratio >= 4.5, (field, ratio)


def test_folder_walker_recurses_and_never_follows_symlink(tmp_path: Path):
    root = tmp_path / "chosen"
    deep = root / "nested" / "fișiere"
    deep.mkdir(parents=True)
    inside = deep / "imagine.jpg"
    inside.write_bytes(b"fixture")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.jpg").write_bytes(b"must not import")
    linked = False
    try:
        (root / "outside-link").symlink_to(outside, target_is_directory=True)
        linked = True
    except (OSError, NotImplementedError):
        pass  # Windows without Developer Mode cannot create symlinks.
    stats = ui.FolderScanStats()
    found = list(ui._walk_folder_files(root, threading.Event(), stats))
    assert found == [inside]
    assert stats.files == 1
    assert stats.skipped_links == int(linked)
    assert not stats.truncated
    if linked:
        linked_stats = ui.FolderScanStats()
        with pytest.raises(ValueError, match="link"):
            list(ui._walk_folder_files(root / "outside-link", threading.Event(), linked_stats))


def test_folder_walker_is_bounded_by_file_entry_and_depth_limits(tmp_path: Path):
    root = tmp_path / "many"
    deep = root / "one" / "two" / "three"
    deep.mkdir(parents=True)
    for index in range(5):
        (root / f"{index}.jpg").write_bytes(b"x")
    (deep / "deep.jpg").write_bytes(b"x")

    files_limit = ui.FolderScanStats()
    assert len(list(ui._walk_folder_files(root, threading.Event(), files_limit, max_files=2))) == 2
    assert files_limit.truncated
    entries_limit = ui.FolderScanStats()
    assert len(list(ui._walk_folder_files(root, threading.Event(), entries_limit, max_entries=3))) <= 3
    assert entries_limit.entries == 3
    assert entries_limit.truncated
    depth_limit = ui.FolderScanStats()
    found = list(ui._walk_folder_files(root, threading.Event(), depth_limit, max_depth=1))
    assert all(path.parent == root for path in found)
    assert depth_limit.truncated
    cancellation = threading.Event()
    cancellation.set()
    assert list(ui._walk_folder_files(root, cancellation, ui.FolderScanStats())) == []


def test_folder_gui_import_is_async_bounded_and_cancelable(
    bare_app, monkeypatch, tmp_path: Path
):
    app = bare_app
    root = tmp_path / "scan"
    root.mkdir()
    for index in range(40):
        (root / f"track-{index}.wav").write_bytes(b"fixture")
    started = threading.Event()

    def slow_inspect(path, ffmpeg):
        started.set()
        time.sleep(0.015)
        return ["mp3", "flac"], "wav"

    monkeypatch.setattr(ui, "_inspect_candidate", slow_inspect)
    began = time.monotonic()
    app.queue_folder(root)
    assert time.monotonic() - began < 0.20
    assert app.importing
    assert "0/?" in app.summary_var.get()
    assert app.import_events.maxsize == 64
    assert app.add_folder_button.options["state"] == "disabled"
    assert app.cancel_button.options["state"] == "normal"
    assert started.wait(timeout=1)
    app._cancel()
    assert app.import_cancel.is_set()
    deadline = time.monotonic() + 3
    while app.importing and time.monotonic() < deadline:
        app._consume_import_events()
        time.sleep(0.01)
    assert not app.importing
    assert "import anulat" in app.summary_var.get()
    assert len(app.files) < 40
    assert app.add_folder_button.options["state"] == "normal"


def test_nested_folder_import_displays_relative_path_and_keeps_original(
    bare_app, monkeypatch, tmp_path: Path
):
    root = tmp_path / "selected"
    nested = root / "album" / "disc1"
    nested.mkdir(parents=True)
    source = nested / "music.wav"
    source.write_bytes(b"unchanged original")
    before = source.read_bytes()
    app = bare_app
    monkeypatch.setattr(ui, "_inspect_candidate", lambda *args: (["mp3"], "wav"))
    app.queue_folder(root)
    deadline = time.monotonic() + 3
    while app.importing and time.monotonic() < deadline:
        app._consume_import_events()
        time.sleep(0.01)
    assert not app.importing
    assert len(app.files) == 1
    row = next(iter(app.files.values()))
    assert row.file == source
    assert row.display_name == str(Path("album") / "disc1" / "music.wav")
    assert app.tree.rows["1"]["values"][0] == row.display_name
    assert source.read_bytes() == before


def test_presets_apply_only_to_compatible_sources(bare_app, tmp_path: Path):
    app = bare_app
    image = tmp_path / "x.png"
    audio = tmp_path / "x.wav"
    video = tmp_path / "x.mp4"
    app.preset_var.set("Imagini → WebP")
    app._add_ready_row(image, ["jpg", "webp"], "png")
    app._add_ready_row(audio, ["mp3", "flac"], "wav")
    app._add_ready_row(video, ["webm"], "mp4")
    assert [row.target for row in app.files.values()] == ["webp", "mp3", "webm"]
    app.preset_var.set("Audio → FLAC")
    app._apply_preset()
    assert [row.target for row in app.files.values()] == ["webp", "flac", "webm"]
    app.preset_var.set("Imagini → PNG")
    app._apply_preset()
    assert [row.target for row in app.files.values()] == ["webp", "flac", "webm"]
    app.preset_var.set("Video → MP4")  # unsupported for existing MP4 source
    app._apply_preset()
    assert app.files["3"].target == "webm"


def test_auto_start_with_target_excludes_nonmatching_sources(bare_app, monkeypatch, tmp_path: Path):
    app = bare_app
    app.preferred_target = "webm"
    app.auto_start_pending = True
    app.auto_excluded = 0
    img = tmp_path / "photo.jpg"
    video = tmp_path / "movie.mp4"
    started = []
    app._start = lambda: started.append(app._pending_jobs())

    def inspect(path, ffmpeg):
        return (["png", "webp"], "jpg") if path == img else (["webm"], "mp4")

    monkeypatch.setattr(ui, "_inspect_candidate", inspect)
    app.importing = True
    app._import_worker([(None, img), (None, video)], set(), None)
    app._consume_import_events()
    assert app.auto_start_pending is False
    assert len(started) == 1
    assert len(started[0]) == 1
    assert started[0][0][1] == video
    assert started[0][0][2] == "webm"
    assert app.auto_excluded == 1
    assert "excluse" in app.summary_var.get()
    assert all(row.file != img for row in app.files.values())


def test_auto_start_no_matching_files_never_converts_fallback(bare_app, monkeypatch, tmp_path: Path):
    app = bare_app
    app.preferred_target = "flac"
    app.auto_start_pending = True
    app.auto_excluded = 0
    started = []
    app._start = lambda: started.append(True)
    monkeypatch.setattr(ui, "_inspect_candidate", lambda *args: (["webp", "png"], "jpg"))
    app.importing = True
    app._import_worker([(None, tmp_path / "picture.jpg")], set(), None)
    app._consume_import_events()
    assert not started
    assert not app.files
    assert "niciun fișier compatibil" in app.summary_var.get()
    assert app.auto_excluded == 1
