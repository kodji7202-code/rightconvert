"""Stage 5 isolated offline and robustness probes.

Only disposable data below qa-fixtures/stage5 is written. No Windows adapter,
firewall, registry, service, installer, or global OS setting is changed.
"""

from __future__ import annotations

import errno
import hashlib
import os
import socket
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from PIL import Image

import rightconvert.engine as engine
from rightconvert.engine import ConversionCancelled, ConversionError, convert_file, get_targets
from rightconvert.formats import inspect_source, media_tools


STAGE5 = Path(__file__).resolve().parents[1] / "qa-fixtures" / "stage5"


@pytest.fixture
def sandbox():
    STAGE5.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="offline-qa-", dir=STAGE5) as folder:
        yield Path(folder)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tools() -> tuple[str, str]:
    result = media_tools()
    if result is None:
        pytest.skip("FFmpeg and FFprobe must be installed to test actual media calls")
    return result


def test_hls_http_loopback_never_connects_with_ffmpeg_protocol_whitelist(sandbox: Path):
    """Actual FFmpeg and FFprobe reject HTTP before connecting to a local listener.

    The listener is bound to 127.0.0.1 only. There is no remote server or
    network-adapter change. Check that no TCP handshake was queued.
    """
    ffmpeg, ffprobe = _tools()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(4)
        listener.settimeout(0.3)
        port = listener.getsockname()[1]
        playlist = sandbox / "crafted_network_playlist.m3u8"
        playlist.write_text(
            "#EXTM3U\n"
            "#EXT-X-TARGETDURATION:1\n"
            "#EXTINF:1.0,\n"
            f"http://127.0.0.1:{port}/should-never-be-requested.ts\n"
            "#EXT-X-ENDLIST\n",
            encoding="utf-8",
        )
        original = _sha(playlist)
        # RightConvert rejects playlists before launching FFprobe.
        assert inspect_source(playlist) is None
        assert get_targets(playlist) == []
        commands = [
            [ffprobe, "-v", "error", "-protocol_whitelist", "file,pipe",
             "-i", str(playlist), "-show_format"],
            [ffmpeg, "-nostdin", "-v", "error", "-protocol_whitelist",
             "file,pipe", "-i", str(playlist), "-f", "null", "-"],
        ]
        for command in commands:
            result = subprocess.run(
                command, capture_output=True, timeout=12,
                stdin=subprocess.DEVNULL, check=False,
            )
            assert result.returncode != 0
            assert b"not on whitelist" in result.stderr.lower(), result.stderr[:1000]
        with pytest.raises(socket.timeout):
            connection, _ = listener.accept()
            connection.close()
        assert _sha(playlist) == original
        assert sorted(path.name for path in sandbox.iterdir()) == [playlist.name]
        print("STAGE5_LOOPBACK: 0 accepted connections; FFprobe and FFmpeg refused HTTP")


def test_disk_full_during_write_does_not_publish_or_change_sources(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
):
    source = sandbox / "original.jpg"
    Image.new("RGB", (90, 64), "blue").save(source)
    source_sha = _sha(source)
    destination = sandbox / "converted"
    destination.mkdir()
    existing = destination / "original.png"
    existing.write_bytes(b"PRE-EXISTING-DO-NOT-OVERWRITE")
    existing_sha = _sha(existing)

    def disk_full(_source, _target, writable_handle, _cancel):
        writable_handle.write(b"PARTIAL OUTPUT")
        raise OSError(errno.ENOSPC, "stage5 simulated disk full during encoding")

    monkeypatch.setattr(engine, "_convert_image", disk_full)
    with pytest.raises(ConversionError, match="simulated disk full"):
        convert_file(source, "png", destination)
    assert _sha(source) == source_sha
    assert _sha(existing) == existing_sha
    assert sorted(path.name for path in destination.iterdir()) == [existing.name]


def test_disk_full_during_publish_preserves_collision(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
):
    source = sandbox / "source.jpg"
    Image.new("RGB", (96, 96), "green").save(source)
    source_sha = _sha(source)
    destination = sandbox / "converted"
    destination.mkdir()
    existing = destination / "source.png"
    existing.write_bytes(b"EXISTING ORIGINAL DESTINATION")
    existing_sha = _sha(existing)

    def disk_full(*_args, **_kwargs):
        raise OSError(errno.ENOSPC, "stage5 simulated commit disk full")

    function = "_win_rename_by_handle" if os.name == "nt" else "_link_by_handle"
    monkeypatch.setattr(engine, function, disk_full)
    with pytest.raises(ConversionError, match="stage5 simulated commit disk full"):
        convert_file(source, "png", destination)
    assert _sha(source) == source_sha
    assert _sha(existing) == existing_sha
    assert sorted(path.name for path in destination.iterdir()) == [existing.name]


def test_parallel_collision_and_source_sha_with_unicode(sandbox: Path):
    source = sandbox / "încearcă 東京 & more.jpg"
    Image.new("RGB", (128, 96), "#204060").save(source)
    source_sha = _sha(source)
    destination = sandbox / "converted"
    destination.mkdir()
    existing = destination / "încearcă 東京 & more.png"
    existing.write_bytes(b"EXISTING-COLLISION")
    before_existing = _sha(existing)
    with ThreadPoolExecutor(max_workers=5) as pool:
        outputs = list(pool.map(lambda _: convert_file(source, "png", destination), range(5)))
    assert len(set(outputs)) == 5
    assert all(file.exists() for file in outputs)
    assert all(file.read_bytes().startswith(b"\x89PNG") for file in outputs)
    assert _sha(source) == source_sha
    assert _sha(existing) == before_existing
    assert not list(destination.glob(".rightconvert-stage-*"))


@pytest.fixture
def video_source():
    """A real 1280x720, 30-second video; bounded temporary disk use (<120 MiB)."""
    ffmpeg, ffprobe = _tools()
    STAGE5.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="video-qa-", dir=STAGE5) as folder:
        root = Path(folder)
        source = root / "720p_30sec_video.mp4"
        started = time.monotonic()
        command = [
            ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=18",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
            "-t", "30", "-c:v", "mpeg4", "-q:v", "5",
            "-c:a", "aac", "-b:a", "96k",
            "-shortest", "-f", "mp4", "-y", str(source),
        ]
        result = subprocess.run(
            command, capture_output=True, stdin=subprocess.DEVNULL,
            timeout=120, check=False,
        )
        assert result.returncode == 0, result.stderr[-1800:]
        assert 1_000_000 < source.stat().st_size < 120_000_000
        print(
            f"STAGE5_SOURCE: 1280x720, 30s, {source.stat().st_size} bytes; "
            f"generation {time.monotonic() - started:.2f}s, sha256={_sha(source)}"
        )
        yield source, ffprobe


def test_actual_720p_video_cancel_cleans_partial_and_preserves_sha(video_source):
    source, ffprobe = video_source
    original_sha = _sha(source)
    destination = source.parent / "converted"
    destination.mkdir()
    progress = []
    signal = threading.Event()

    def cancel_on_progress(value: float) -> None:
        progress.append(value)
        if value > 0:
            signal.set()

    assert "webm" in get_targets(source)
    started = time.monotonic()
    with pytest.raises(ConversionCancelled):
        convert_file(source, "webm", destination, cancel=signal, progress=cancel_on_progress)
    assert signal.is_set(), "Cancellation must occur during FFmpeg, not before launch"
    assert any(value > 0 for value in progress)
    assert _sha(source) == original_sha
    assert list(destination.iterdir()) == [], "No partial WebM or staging should be left"
    probe = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "stream=codec_type,width,height",
         "-of", "json", str(source)], capture_output=True, timeout=15, check=False,
    )
    assert probe.returncode == 0
    assert b"1280" in probe.stdout and b"720" in probe.stdout
    print(
        f"STAGE5_CANCEL: elapsed {time.monotonic() - started:.2f}s, "
        f"first-positive-progress {next(value for value in progress if value > 0):.4f}; "
        f"source-sha256={original_sha}; partial outputs=0"
    )
