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
