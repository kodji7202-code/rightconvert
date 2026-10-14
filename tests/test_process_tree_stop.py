"""Cancellation must stop launcher shims *and* the real tool they spawn."""

from __future__ import annotations

import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from rightconvert import engine


def test_windows_stop_uses_tree_kill_first(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    class FakeProc:
        pid = 4242
        terminated = False

        def poll(self):
            return None

        def wait(self, timeout=None):
            return 0

        def terminate(self):
            self.terminated = True

        def kill(self):
            raise AssertionError("kill must not be needed")

    monkeypatch.setattr(engine.sys, "platform", "win32")
    monkeypatch.setattr(engine, "_kill_process_tree_windows", lambda pid: calls.append(pid) or True)
    proc = FakeProc()
    engine._stop_process(proc)  # type: ignore[arg-type]
    assert calls == [4242]
    assert proc.terminated is False


def test_windows_stop_falls_back_to_terminate(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeProc:
        pid = 7
        terminated = False

        def poll(self):
            return None

        def wait(self, timeout=None):
            return 0

        def terminate(self):
            self.terminated = True

        def kill(self):
            pass

    monkeypatch.setattr(engine.sys, "platform", "win32")
    monkeypatch.setattr(engine, "_kill_process_tree_windows", lambda pid: False)
    proc = FakeProc()
    engine._stop_process(proc)  # type: ignore[arg-type]
    assert proc.terminated is True


@pytest.mark.skipif(sys.platform != "win32", reason="Shim grandchild file locking is Windows-specific")
def test_stopping_a_shim_releases_the_grandchild_file_lock(tmp_path: Path) -> None:
    held = tmp_path / "part.tmp.webm"
    ready = tmp_path / "ready"
    child = textwrap.dedent(f"""
        import pathlib, time
        handle = open(r"{held}", "wb")
        pathlib.Path(r"{ready}").write_text("1")
        time.sleep(120)
    """)
    shim = textwrap.dedent(f"""
        import subprocess, sys
        sys.exit(subprocess.call([sys.executable, "-c", {child!r}]))
    """)
    proc = subprocess.Popen([sys.executable, "-c", shim], stdin=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 30
        while not ready.exists():
            assert time.monotonic() < deadline, "grandchild never started"
            time.sleep(0.05)
        engine._stop_process(proc)  # type: ignore[arg-type]
        deadline = time.monotonic() + 10
        while True:
            try:
                held.unlink()
                break
            except PermissionError:
                assert time.monotonic() < deadline, "grandchild still holds the partial file"
                time.sleep(0.1)
    finally:
        if proc.poll() is None:
            proc.kill()


def test_office_stop_never_runs_a_bare_taskkill(monkeypatch: pytest.MonkeyPatch) -> None:
    """LibreOffice cancel must use the trusted System32 tree kill, not PATH/CWD."""
    from rightconvert import office_provider

    tree_kills: list[int] = []
    launched: list[object] = []

    class FakeProc:
        pid = 5150
        killed = False

        def poll(self):
            return 0 if self.killed else None

        def wait(self, timeout=None):
            return 0

        def kill(self):
            self.killed = True

    def tree_kill(pid: int) -> bool:
        tree_kills.append(pid)
        proc.killed = True
        return True

    monkeypatch.setattr(office_provider.os, "name", "nt", raising=False)
    monkeypatch.setattr(engine, "_kill_process_tree_windows", tree_kill)
    monkeypatch.setattr(office_provider.subprocess, "run",
                        lambda *args, **kwargs: launched.append(args) or None)
    proc = FakeProc()
    office_provider._stop_process(proc)  # type: ignore[arg-type]
    assert tree_kills == [5150]
    assert launched == [], "No unqualified helper executable may be launched"


@pytest.mark.skipif(sys.platform != "win32", reason="Real Windows process tree kill")
def test_office_stop_kills_real_tree_with_absolute_system32_taskkill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Office cancel launches only System32\\taskkill.exe, even from a user folder."""
    from rightconvert import office_provider

    system_dir = engine._windows_system_directory()
    assert system_dir is not None
    monkeypatch.chdir(tmp_path)  # Explorer starts the app in the user's folder.
    launched: list[list[str]] = []
    real_run = subprocess.run

    def spy(args, *rest, **kwargs):
        launched.append([str(item) for item in args])
        return real_run(args, *rest, **kwargs)

    monkeypatch.setattr(subprocess, "run", spy)
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"],
                            stdin=subprocess.DEVNULL)
    try:
        office_provider._stop_process(proc)  # type: ignore[arg-type]
        assert proc.poll() is not None
    finally:
        if proc.poll() is None:
            proc.kill()
    assert launched, "the process tree kill was not attempted"
    for argv in launched:
        executable = Path(argv[0])
        assert executable.is_absolute(), argv
        assert executable.parent == system_dir, argv
        assert executable.name.lower() == "taskkill.exe", argv
