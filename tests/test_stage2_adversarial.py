"""Stage 2 adversarial integration probes on disposable test files only."""

from __future__ import annotations

import errno
import csv
import hashlib
import os
from pathlib import Path
import shutil
import struct
import subprocess
import threading
import wave
import zlib

import pytest
from PIL import Image

from rightconvert.engine import ConversionCancelled, ConversionError, convert_file, get_targets
from rightconvert.formats import media_tools


def image(path: Path):
    Image.new("RGB", (18, 16), "#277bdd").save(path, format="JPEG")
    return path


def test_spoofed_extension_collision_never_changes_original(tmp_path: Path):
    original = image(tmp_path / "încărcare_東京_🚀.png")  # JPEG data, false PNG extension
    before = original.read_bytes()
    assert "png" in get_targets(original)
    result = convert_file(original, "png", tmp_path)
    assert result.name == "încărcare_東京_🚀 (1).png"
    assert original.read_bytes() == before
    with Image.open(result) as converted:
        assert converted.format == "PNG" and converted.size == (18, 16)


def test_unsupported_large_dimensions_rejected_without_allocation(tmp_path: Path):
    """A tiny crafted PNG claims 25000² pixels; no huge bitmap is allocated."""
    path = tmp_path / "monster.png"
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", 25_000, 25_000, 8, 2, 0, 0, 0)
    chunk = b"IHDR" + ihdr
    path.write_bytes(signature + struct.pack(">I", len(ihdr)) + chunk + struct.pack(">I", zlib.crc32(chunk)))
    assert get_targets(path) == []
    with pytest.raises(ConversionError):
        convert_file(path, "webp", tmp_path)
    assert sorted(x.name for x in tmp_path.iterdir()) == ["monster.png"]


def test_real_large_36_megapixel_image_converts_and_preserves_original(tmp_path: Path):
    """Exercise decoding/encoding a genuine large raster below the 60 MP limit."""
    source = tmp_path / "large_6000x6000.png"
    with Image.new("L", (6000, 6000), 114) as img:
        img.save(source, format="PNG")
    before = source.read_bytes()
    assert "jpg" in get_targets(source)
    output = convert_file(source, "jpg", tmp_path)
    with Image.open(output) as img:
        assert img.size == (6000, 6000)
        img.verify()
    assert source.read_bytes() == before


def test_batch_of_100_unique_outputs_preserves_source(tmp_path: Path):
    source = image(tmp_path / "batch file.jpg")
    original = source.read_bytes()
    out = tmp_path / "results"
    outputs = [convert_file(source, "png", out) for _ in range(100)]
    assert len(set(outputs)) == 100
    assert outputs[0].name == "batch file.png"
    assert outputs[-1].name == "batch file (99).png"
    assert source.read_bytes() == original
    assert all(p.read_bytes().startswith(b"\x89PNG") for p in outputs)
    assert len(list(out.iterdir())) == 100


def test_denied_output_directory_reports_error_preserves_input(tmp_path: Path, monkeypatch):
    import rightconvert.engine as engine

    source = image(tmp_path / "input.jpg")
    original = source.read_bytes()

    def refuse(*_args, **_kwargs):
        raise PermissionError(errno.EACCES, "Permission denied")

    if os.name == "nt":
        monkeypatch.setattr(engine, "_win_stage_file", refuse)
    else:
        monkeypatch.setattr(engine.tempfile, "mkstemp", refuse)
    with pytest.raises(ConversionError, match="Permission denied"):
        convert_file(source, "png", tmp_path / "denied")
    assert source.read_bytes() == original
    assert not list((tmp_path / "denied").iterdir())


@pytest.mark.skipif(os.name != "nt", reason="Real Windows ACL test")
def test_real_windows_acl_denies_output_without_changing_original(tmp_path: Path):
    """Deny file creation in an owned disposable child directory, then restore ACL."""
    if not (shutil.which("icacls") and shutil.which("whoami")):
        pytest.skip("Windows security utilities unavailable")
    identity = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"],
                              capture_output=True, text=True, errors="replace", check=True)
    sid = next(csv.reader(identity.stdout.splitlines()))[-1].strip()
    if not sid.startswith("S-1-"):
        pytest.skip("Cannot identify Windows user SID for local ACL test")
    source = image(tmp_path / "readable original.jpg")
    original = source.read_bytes()
    restricted = tmp_path / "acl restricted"
    restricted.mkdir()
    marker = restricted / "existing.txt"
    marker.write_text("not a converted image", encoding="utf-8")
    deny = subprocess.run(["icacls", str(restricted), "/deny", f"*{sid}:(WD,AD)"],
                          capture_output=True, text=True, errors="replace")
    if deny.returncode:
        pytest.skip("Cannot set disposable Windows ACL: " + deny.stderr[:150])
    try:
        with pytest.raises(ConversionError):
            convert_file(source, "png", restricted)
    finally:
        restore = subprocess.run(["icacls", str(restricted), "/remove:d", f"*{sid}"],
                                 capture_output=True, text=True, errors="replace")
        if restore.returncode:
            raise RuntimeError("Failed to restore disposable test directory ACL: " + restore.stderr[:200])
    assert source.read_bytes() == original
    assert marker.read_text(encoding="utf-8") == "not a converted image"
    assert sorted(p.name for p in restricted.iterdir()) == ["existing.txt"]


def test_corrupt_and_truncated_inputs_never_create_outputs(tmp_path: Path):
    valid = image(tmp_path / "reference.jpg")
    truncated = tmp_path / "truncated.jpg"
    truncated.write_bytes(valid.read_bytes()[:35])
    fake = tmp_path / "fake.mp4"
    fake.write_bytes(b"\0\0\0\x14ftypisom" + b"not a valid movie")
    empty = tmp_path / "empty.webp"
    empty.write_bytes(b"")
    for file in (truncated, fake, empty):
        assert get_targets(file) == []
        with pytest.raises(ConversionError):
            convert_file(file, "png", tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_external_encoder_launch_failure_is_recoverable(tmp_path: Path, monkeypatch):
    import rightconvert.engine as engine

    tools = media_tools()
    if not tools:
        pytest.skip("Local FFmpeg/FFprobe unavailable")
    source = tmp_path / "original.wav"
    with wave.open(str(source), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(22050)
        wav.writeframes(b"\0\0" * 22050)
    assert "mp3" in get_targets(source)
    before = source.read_bytes()

    original_popen = subprocess.Popen

    def fail_launch(*args, **kwargs):
        command = args[0] if args else kwargs.get("args", [])
        if "-progress" in command:
            raise FileNotFoundError(errno.ENOENT, "simulated FFmpeg absent")
        return original_popen(*args, **kwargs)

    monkeypatch.setattr(engine.subprocess, "Popen", fail_launch)
    with pytest.raises(ConversionError, match="simulated FFmpeg absent"):
        convert_file(source, "mp3", tmp_path)
    assert source.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["original.wav"]


def test_external_encoder_nonzero_exit_is_recoverable(tmp_path: Path, monkeypatch):
    import rightconvert.engine as engine

    if not media_tools():
        pytest.skip("FFmpeg unavailable")
    source = tmp_path / "audio.wav"
    with wave.open(str(source), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(22050)
        wav.writeframes(b"\0\0" * 22050)
    assert "flac" in get_targets(source)
    before = source.read_bytes()
    monkeypatch.setattr(engine, "_media_args", lambda *_: ["-c:a", "nonexistent_fake_encoder"])
    with pytest.raises(ConversionError, match="FFmpeg conversion failed"):
        convert_file(source, "flac", tmp_path)
    assert source.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["audio.wav"]


def test_large_sparse_audio_cancel_preserves_original(tmp_path: Path):
    """Real 96-MiB local WAV with sparse content, cancelled during FFmpeg transcode."""
    if not media_tools():
        pytest.skip("FFmpeg unavailable")
    source = tmp_path / "mare spațiu_音.wav"
    total = 96 * 1024 * 1024
    with wave.open(str(source), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(44100)
        wav.writeframes(b"\0\0" * 2048)
    with source.open("r+b") as file:
        file.truncate(total + 44)
        file.seek(4)
        file.write(struct.pack("<I", total + 36))
        file.seek(40)
        file.write(struct.pack("<I", total))
    assert source.stat().st_size >= total
    if "mp3" not in get_targets(source):
        pytest.skip("MP3 conversion unavailable")
    with source.open("rb") as stream:
        original_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
    event = threading.Event()

    def progress(value: float):
        if value > 0:
            event.set()

    with pytest.raises(ConversionCancelled):
        convert_file(source, "mp3", tmp_path, cancel=event, progress=progress)
    assert event.is_set()
    assert source.stat().st_size == total + 44
    with source.open("rb") as stream:
        assert hashlib.file_digest(stream, "sha256").hexdigest() == original_sha256
    assert sorted(p.name for p in tmp_path.iterdir()) == [source.name]
