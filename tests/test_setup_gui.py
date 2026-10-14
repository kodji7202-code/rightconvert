"""Installer state transitions without starting Tk, PowerShell, or installation."""

from __future__ import annotations

import queue
import os
from pathlib import Path

import pytest

from installer import setup_gui


class FakeWidget:
    def __init__(self, **initial):
        self.options = dict(initial)
        self.focus_calls = 0

    def configure(self, **options):
        self.options.update(options)

    def focus_set(self):
        self.focus_calls += 1


class FakeText(FakeWidget):
    def __init__(self):
        super().__init__()
        self.content = ""

    def insert(self, where, value):
        assert where == "end"
        self.content += value

    def delete(self, first, last):
        assert (first, last) == ("1.0", "end")
        self.content = ""

    def see(self, where):
        assert where == "end"


class FakeStatus:
    def __init__(self):
        self.value = ""

    def set(self, value):
        self.value = value


class FakeRoot:
    def __init__(self):
        self.after_calls = []
        self.clipboard = None
        self.clipboard_calls = 0

    def after(self, delay, callback):
        self.after_calls.append((delay, callback))

    def clipboard_clear(self):
        self.clipboard_calls += 1
        self.clipboard = ""

    def clipboard_append(self, value):
        self.clipboard_calls += 1
        self.clipboard = value


def make_installer_window():
    window = setup_gui.SetupWindow.__new__(setup_gui.SetupWindow)
    window.root = FakeRoot()
    window.events = queue.Queue()
    window.running = True
    window.success = False
    window.progress = FakeWidget(value=90)
    window.status = FakeStatus()
    window.log = FakeText()
    window.install_button = FakeWidget(state="disabled", text="Se instalează...")
    window.open_button = FakeWidget(state="disabled")
    window.media_status = FakeStatus()
    window.media_copy_button = FakeWidget(state="disabled")
    window.media_selection = None
    return window


def test_install_failure_resets_progress_and_focuses_retry(monkeypatch):
    window = make_installer_window()
    dialogs = []
    monkeypatch.setattr(setup_gui.messagebox, "showerror", lambda *args, **kwargs: dialogs.append((args, kwargs)))
    window.events.put(("progress", (90, "Instalez aplicația și meniul contextual...")))
    window.events.put(("error", "Eroare de test la instalare"))

    window.poll_events()

    assert window.running is False
    assert window.success is False
    assert window.progress.options["value"] == 0
    assert "eșuat" in window.status.value
    assert "Eroare de test la instalare" in window.log.content
    assert window.install_button.options["state"] == "normal"
    assert window.install_button.options["text"] == "Reîncearcă"
    assert window.install_button.focus_calls == 1
    assert window.open_button.options["state"] == "disabled"
    assert len(dialogs) == 1
    assert len(window.root.after_calls) == 1


def test_retry_clears_previous_attempt_and_starts_cleanly(monkeypatch):
    window = make_installer_window()
    monkeypatch.setattr(setup_gui.messagebox, "showerror", lambda *args, **kwargs: None)
    window.events.put(("progress", (90, "Instalare în curs")))
    window.events.put(("error", "Eroare veche"))
    window.poll_events()

    threads = []

    class FakeThread:
        def __init__(self, *, target, daemon):
            self.target = target
            self.daemon = daemon
            self.started = False
            threads.append(self)

        def start(self):
            self.started = True

    monkeypatch.setattr(setup_gui.threading, "Thread", FakeThread)
    window.start_install()

    assert window.running is True
    assert window.success is False
    assert window.progress.options["value"] == 0
    assert window.status.value == "Pregătesc instalarea locală..."
    assert window.log.content == "Pregătesc instalarea locală...\n"
    assert window.install_button.options["state"] == "disabled"
    assert window.install_button.options["text"] == "Se instalează..."
    assert window.open_button.options["state"] == "disabled"
    assert len(threads) == 1
    assert threads[0].started and threads[0].daemon
    assert threads[0].target == window.install_in_worker


def make_local_pair(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    ffmpeg = directory / "ffmpeg.exe"
    ffprobe = directory / "ffprobe.exe"
    ffmpeg.write_bytes(b"not executed in this test")
    ffprobe.write_bytes(b"not executed in this test")
    return ffmpeg, ffprobe


def test_offline_path_finds_both_tools_without_running_them(tmp_path, monkeypatch):
    ffmpeg, ffprobe = make_local_pair(tmp_path / "Bin cu spații și ș")
    monkeypatch.setattr(
        setup_gui.subprocess, "run",
        lambda *args, **kwargs: pytest.fail("Setup must not execute external FFmpeg"),
    )
    path = f"{tmp_path / 'missing'}{os.pathsep}{ffmpeg.parent}"
    status = setup_gui.detect_local_media_tools(path)
    assert status.complete
    assert status.ffmpeg == ffmpeg.resolve()
    assert status.ffprobe == ffprobe.resolve()
    assert status.source == "PATH"
    assert "neverificată" in setup_gui.media_status_text(status)


def test_offline_path_missing_both_or_one(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert not setup_gui.detect_local_media_tools(str(empty)).complete
    incomplete = tmp_path / "partial"
    incomplete.mkdir()
    (incomplete / "ffmpeg.exe").write_bytes(b"fake")
    result = setup_gui.detect_local_media_tools(str(incomplete))
    assert result.ffmpeg is not None and result.ffprobe is None
    assert not result.complete
    assert "FFprobe" in setup_gui.media_status_text(result)


def test_offline_path_refuses_relative_current_directory(tmp_path, monkeypatch):
    make_local_pair(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = setup_gui.detect_local_media_tools(f".{os.pathsep}{tmp_path}")
    assert result.ffmpeg is None
    assert result.ffprobe is None


def test_select_valid_pair_and_reject_wrong_or_missing_pair(tmp_path):
    ffmpeg, ffprobe = make_local_pair(tmp_path / "Media 🎬")
    result = setup_gui.check_selected_ffmpeg(str(ffmpeg))
    assert result.complete and result.source == "selectat"
    assert result.ffprobe == ffprobe.resolve()
    with pytest.raises(ValueError, match="ffmpeg.exe"):
        setup_gui.check_selected_ffmpeg(str(ffprobe))
    ffprobe.unlink()
    with pytest.raises(ValueError, match="ffprobe.exe"):
        setup_gui.check_selected_ffmpeg(str(ffmpeg))
    with pytest.raises(ValueError, match="absolută"):
        setup_gui.check_selected_ffmpeg("ffmpeg.exe")


def test_gui_refresh_and_user_choice_never_persists_config(tmp_path, monkeypatch):
    window = make_installer_window()
    ffmpeg, _ = make_local_pair(tmp_path / "Kit local")
    monkeypatch.setattr(setup_gui, "detect_local_media_tools",
                        lambda: setup_gui.LocalMediaTools())
    warnings = []
    monkeypatch.setattr(setup_gui.messagebox, "showwarning",
                        lambda *args, **kwargs: warnings.append(args))
    path_choices = iter(("", str(ffmpeg.parent / "not_ffmpeg.exe"), str(ffmpeg)))
    monkeypatch.setattr(setup_gui.filedialog, "askopenfilename",
                        lambda **kwargs: next(path_choices))
    monkeypatch.setattr(setup_gui, "powershell_install",
                        lambda *args: pytest.fail("The media assistant must not install"))
    window.refresh_media_status()
    assert window.media_selection is None
    assert window.media_copy_button.options["state"] == "disabled"
    assert "Lipsește" in window.media_status.value
    window.choose_media_file()  # cancelled
    assert window.media_copy_button.options["state"] == "disabled"
    window.choose_media_file()  # invalid name
    assert warnings and window.media_copy_button.options["state"] == "disabled"
    window.choose_media_file()  # complete local kit
    assert window.media_selection == ffmpeg.resolve()
    assert window.media_copy_button.options["state"] == "normal"
    assert window.root.clipboard_calls == 0  # no implicit side effects
    window.copy_media_path()
    assert window.root.clipboard == str(ffmpeg.resolve())
    assert "Motor FFmpeg" in window.media_status.value
    window.refresh_media_status()  # explicit PATH-only reset
    assert window.media_selection is None
    assert window.media_copy_button.options["state"] == "disabled"


def test_gui_rejects_incomplete_local_kit_without_copy_or_install(tmp_path, monkeypatch):
    window = make_installer_window()
    folder = tmp_path / "kit incomplet"
    folder.mkdir()
    candidate = folder / "ffmpeg.exe"
    candidate.write_bytes(b"not executed")
    warnings = []
    monkeypatch.setattr(setup_gui.filedialog, "askopenfilename",
                        lambda **kwargs: str(candidate))
    monkeypatch.setattr(setup_gui.messagebox, "showwarning",
                        lambda *args, **kwargs: warnings.append(args))
    monkeypatch.setattr(setup_gui.subprocess, "run",
                        lambda *args, **kwargs: pytest.fail("FFmpeg must never run during Setup"))
    window.choose_media_file()
    assert window.media_selection is None
    assert window.media_copy_button.options["state"] == "disabled"
    assert warnings and "ffprobe.exe" in str(warnings[0])
    assert window.root.clipboard_calls == 0
