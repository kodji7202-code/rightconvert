"""8.3 short paths (e.g. %TEMP% = C:\\Users\\RUNNER~1\\...) are not redirections."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from rightconvert import engine
from rightconvert.formats import ConversionError


def _short_path(path: Path) -> Path:
    import ctypes
    from ctypes import wintypes

    get_short = ctypes.WinDLL("kernel32", use_last_error=True).GetShortPathNameW
    get_short.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    get_short.restype = wintypes.DWORD
    buffer = ctypes.create_unicode_buffer(32768)
    length = get_short(str(path), buffer, len(buffer))
    assert 0 < length < len(buffer)
    return Path(buffer.value)


@pytest.mark.skipif(os.name != "nt", reason="8.3 short names are a Windows file-system feature")
def test_short_name_output_folder_is_accepted_and_normalized(tmp_path: Path) -> None:
    folder = tmp_path / "Long Folder Name For Short Alias"
    folder.mkdir()
    short = _short_path(folder)
    if os.path.normcase(str(short)) == os.path.normcase(str(folder)):
        pytest.skip("8.3 short names are disabled on this volume")
    assert engine._safe_output_directory(short) == folder.resolve(strict=True)
    # Staging below the normalized folder must also be accepted.
    staging = engine._create_private_staging(engine._safe_output_directory(short))
    assert staging.parent == folder.resolve(strict=True)
    staging.rmdir()


@pytest.mark.skipif(os.name != "nt", reason="Windows junctions")
def test_long_path_normalization_still_rejects_a_junction(tmp_path: Path) -> None:
    import subprocess

    target = tmp_path / "real target"
    target.mkdir()
    link = tmp_path / "Junction Folder With Long Name"
    system_dir = engine._windows_system_directory()
    assert system_dir is not None
    result = subprocess.run([str(system_dir / "cmd.exe"), "/c", "mklink", "/J", str(link), str(target)],
                            capture_output=True, text=True, check=False)
    if result.returncode != 0:
        pytest.skip("Could not create a junction: " + result.stderr)
    with pytest.raises(ConversionError):
        engine._safe_output_directory(link / "out")
    with pytest.raises(ConversionError):
        engine._safe_output_directory(_short_path(link) / "out")


def test_long_path_is_identity_off_windows(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("POSIX identity check")
    assert engine._windows_long_path(tmp_path) == tmp_path
