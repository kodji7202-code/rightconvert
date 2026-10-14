"""Stage 7 current-source media QA: real local FFmpeg, no installed-app mutation.

The existing Stage 5 script exercises all 57 historical routes separately.
These focused regressions cover capability fallbacks, real mixed streams,
24-bit PCM and offline protocol boundaries the ordinary matrix does not vary.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import struct
import subprocess
import wave
from pathlib import Path

import pytest
from PIL import Image

from rightconvert.engine import UnsupportedFormatError, convert_file, get_targets
from rightconvert.formats import available_encoders, inspect_source, media_tools
import rightconvert.formats as formats


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(command: list[str], *, timeout: int = 35) -> subprocess.CompletedProcess:
    completed = subprocess.run(
        command, stdin=subprocess.DEVNULL, capture_output=True,
        timeout=timeout, check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert completed.returncode == 0, (
        f"command returned {completed.returncode}: {completed.stderr[-1000:].decode('utf-8', 'replace')}")
    return completed


@pytest.fixture(scope="module")
def tools() -> tuple[str, str]:
    found = media_tools()
    if found is None:
        pytest.skip("FFmpeg and FFprobe unavailable: native media scenarios unverified")
    return found


def _wav(path: Path, *, depth: int = 16) -> Path:
    """Deterministic, nonzero PCM generated without an encoder/backend."""
    assert depth in (16, 24)
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setframerate(24000)
        stream.setsampwidth(depth // 8)
        if depth == 16:
            stream.writeframes(b"".join(struct.pack("<h", (i % 1000 - 500) * 20)
                                        for i in range(12000)))
        else:
            stream.writeframes(b"".join(int((i % 1000 - 500) * 7000).to_bytes(
                3, "little", signed=True) for i in range(12000)))
    return path


def _short_video(path: Path, ffmpeg: str) -> None:
    _run([
        ffmpeg, "-hide_banner", "-v", "error", "-nostdin", "-threads", "1",
        "-filter_threads", "1", "-f", "lavfi", "-i", "color=c=red:s=96x64:r=10:d=0.5",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=24000:duration=0.5",
        "-map", "0:v:0", "-map", "1:a:0", "-c:v", "mpeg4", "-c:a", "aac",
        "-shortest", "-f", "mp4", str(path),
    ])


def test_ffmpeg_unavailable_is_hidden_and_explicit_absolute_runtime_restores_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tools: tuple[str, str]
) -> None:
    source = _wav(tmp_path / "offline 音声.wav")
    digest = _sha(source)
    targets = set(get_targets(source, ffmpeg_path=tools[0]))
    assert targets, "Real FFmpeg binary should offer at least one WAV conversion"

    # The engine must never pick an arbitrary local ffmpeg.exe or download it.
    # Explicit configuration must still work with no PATH search available.
    monkeypatch.setenv("PATH", "")
    assert media_tools() is None
    assert inspect_source(source) is None
    assert get_targets(source) == []
    output_dir = tmp_path / "without-backend"
    with pytest.raises(UnsupportedFormatError):
        convert_file(source, sorted(targets)[0], output_dir)
    assert not output_dir.exists(), "No unpublished output directory should be created"
    assert set(get_targets(source, ffmpeg_path=tools[0])) == targets
    assert _sha(source) == digest


def test_media_capabilities_follow_available_audio_and_video_encoders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tools: tuple[str, str]
) -> None:
    encoders = available_encoders(tools[0])
    if not {"mpeg4", "aac", "libvpx-vp9", "libopus"}.issubset(encoders):
        pytest.skip("Actual FFmpeg lacks fixture/video/WebM encoders")
    video = tmp_path / "audio_present vidéo.mp4"
    _short_video(video, tools[0])
    info = inspect_source(video)
    assert info and info.kind == "video" and info.video_streams == 1
    assert info.audio_streams == 1 and info.has_audio
    baseline = _sha(video)

    # A bare video encoder cannot produce an advertised audio-bearing WebM.
    monkeypatch.setattr(formats, "available_encoders", lambda _ffmpeg: frozenset({"libvpx-vp9"}))
    assert "webm" not in get_targets(video)
    with pytest.raises(UnsupportedFormatError):
        convert_file(video, "webm", tmp_path / "blocked")

    # Advertising changes when the matching audio codec becomes available.
    monkeypatch.setattr(formats, "available_encoders", lambda _ffmpeg: frozenset({"libvpx-vp9", "libopus"}))
    assert "webm" in get_targets(video)
    assert "mkv" not in get_targets(video)
    result = convert_file(video, "webm", tmp_path / "allowed", ffmpeg_path=tools[0])
    metadata = json.loads(_run([
        tools[1], "-v", "error", "-show_entries", "stream=codec_type,codec_name",
        "-of", "json", "-i", str(result),
    ]).stdout)
    streams = metadata["streams"]
    assert any(s["codec_type"] == "video" for s in streams)
    assert any(s["codec_type"] == "audio" and s["codec_name"] == "opus" for s in streams)
    assert _sha(video) == baseline
    assert not list((tmp_path / "allowed").glob(".rightconvert-stage-*"))


def test_24bit_pcm_roundtrip_has_readable_finite_wav_and_original_sha(
    tmp_path: Path, tools: tuple[str, str]
) -> None:
    encoders = available_encoders(tools[0])
    if not {"flac", "pcm_s24le"}.issubset(encoders):
        pytest.skip("FFmpeg lacks the 24-bit PCM and FLAC encoders")
    source = _wav(tmp_path / "precision source.wav", depth=24)
    original_digest = _sha(source)
    flac = convert_file(source, "flac", tmp_path / "encoded")
    assert inspect_source(flac).format == "flac"
    output = convert_file(flac, "wav", tmp_path / "decoded")
    with wave.open(str(output), "rb") as stream:
        assert stream.getsampwidth() == 3
        assert stream.getnchannels() == 1
        assert stream.getnframes() == 12000
        payload = stream.readframes(12000)
    with wave.open(str(source), "rb") as stream:
        expected = stream.readframes(12000)
    assert payload == expected, "Lossless FLAC roundtrip altered 24-bit PCM"
    assert _sha(source) == original_digest
    assert not list((tmp_path / "decoded").glob(".rightconvert-stage-*"))


def test_embedded_mp3_cover_art_is_not_silently_dropped(
    tmp_path: Path, tools: tuple[str, str]
) -> None:
    if "libmp3lame" not in available_encoders(tools[0]):
        pytest.skip("No MP3 encoder available to generate a real cover-art source")
    source = _wav(tmp_path / "original.wav")
    mp3 = tmp_path / "original.mp3"
    _run([tools[0], "-hide_banner", "-v", "error", "-nostdin", "-i", str(source),
          "-c:a", "libmp3lame", "-f", "mp3", str(mp3)])
    cover = tmp_path / "cover.jpg"
    Image.new("RGB", (80, 60), "#aabbcc").save(cover)
    covered = tmp_path / "album art.mp3"
    _run([tools[0], "-hide_banner", "-v", "error", "-nostdin",
          "-i", str(mp3), "-i", str(cover), "-map", "0:a:0", "-map", "1:v:0",
          "-c", "copy", "-id3v2_version", "3", "-disposition:v:0", "attached_pic",
          "-f", "mp3", str(covered)])
    info = inspect_source(covered)
    assert info and info.kind == "audio" and info.format == "mp3"
    assert info.extra_streams or info.video_streams > 0
    baseline = _sha(covered)
    assert get_targets(covered) == [], "Do not advertise lossy conversion that drops cover art"
    destination = tmp_path / "blocked"
    with pytest.raises(UnsupportedFormatError):
        convert_file(covered, "ogg", destination)
    assert not destination.exists()
    assert _sha(covered) == baseline


def test_offline_network_manifest_cannot_connect_to_loopback(
    tmp_path: Path, tools: tuple[str, str]
) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(0.35)
        source = tmp_path / "offline-stream.m3u8"
        source.write_text(
            "#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXTINF:0.5,\n"
            f"http://127.0.0.1:{listener.getsockname()[1]}/should-not-connect.ts\n"
            "#EXT-X-ENDLIST\n", encoding="utf-8",
        )
        baseline = _sha(source)
        assert inspect_source(source) is None
        assert get_targets(source) == []
        for tool in tools:
            command = [tool, "-v", "error", "-protocol_whitelist", "file,pipe",
                       "-i", str(source)]
            command += (["-f", "null", "-"] if Path(tool).stem == "ffmpeg" else ["-show_format"])
            result = subprocess.run(command, stdin=subprocess.DEVNULL,
                                    capture_output=True, timeout=12, check=False)
            assert result.returncode != 0
            assert b"not on whitelist" in result.stderr.lower(), result.stderr[-500:]
        with pytest.raises(socket.timeout):
            connection, _ = listener.accept()
            connection.close()
        assert _sha(source) == baseline

