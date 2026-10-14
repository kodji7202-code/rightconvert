"""Executable proof of prior v0.2 routes plus new PDF image targets."""

from __future__ import annotations

import subprocess
import wave
from pathlib import Path

from PIL import Image
import pytest

from rightconvert.engine import convert_file, get_targets
from rightconvert.formats import IMAGE_TARGETS, AUDIO_TARGETS, VIDEO_TARGETS, available_encoders, inspect_source, media_tools


@pytest.fixture(scope="module")
def media_bin():
    tools = media_tools()
    if tools is None:
        pytest.skip("Local FFmpeg/FFprobe are required for the media matrix")
    return tools


@pytest.mark.parametrize("source_format", ["png", "jpg", "webp"])
def test_all_image_output_routes(source_format: str, tmp_path: Path):
    path = tmp_path / f"test spații 漢字.{source_format}"
    Image.new("RGB", (100, 60), (32, 100, 160)).save(path)
    original = path.read_bytes()
    expected = (set(IMAGE_TARGETS) - {source_format}) | {"pdf"}
    available = set(get_targets(path))
    assert available == expected
    for target in sorted(expected):
        out = convert_file(path, target, tmp_path / "out")
        if target == "pdf":
            from rightconvert.pdf_provider import inspect_pdf
            assert out.read_bytes().startswith(b"%PDF-")
            assert inspect_pdf(out) == 1
            continue
        with Image.open(out) as result:
            assert result.format == {"jpg": "JPEG", "png": "PNG", "webp": "WEBP", "bmp": "BMP", "tiff": "TIFF"}[target]
            assert result.size == (100, 60)
    assert path.read_bytes() == original


@pytest.mark.parametrize("source_format,pillow_format", [
    ("bmp", "BMP"), ("tiff", "TIFF"), ("gif", "GIF"),
])
def test_v02_static_image_inputs_route_to_actual_supported_outputs(
    source_format: str, pillow_format: str, tmp_path: Path
):
    # A GIF remains an input only: quantizing arbitrary RGB into 256 GIF
    # colors would otherwise silently destroy source colors.
    source = tmp_path / f"static 東京 {source_format}.fake"
    Image.new("RGB", (41, 27), (80, 120, 190)).save(source, format=pillow_format)
    original = source.read_bytes()
    targets = set(get_targets(source))
    # PDF encoding is intentionally limited to opaque conventional modes.
    # GIF's palette mode is not silently expanded for this route.
    expected = set(IMAGE_TARGETS) - {source_format}
    if source_format != "gif":
        expected.add("pdf")
    assert targets == expected
    for target in sorted(targets):
        result = convert_file(source, target, tmp_path / "out")
        if target == "pdf":
            from rightconvert.pdf_provider import inspect_pdf
            assert result.read_bytes().startswith(b"%PDF-")
            assert inspect_pdf(result) == 1
            continue
        with Image.open(result) as image:
            assert image.format == {"jpg": "JPEG", "png": "PNG", "webp": "WEBP", "bmp": "BMP", "tiff": "TIFF"}[target]
            assert image.size == (41, 27)
    assert source.read_bytes() == original


@pytest.fixture
def audio_samples(tmp_path: Path, media_bin):
    ffmpeg, _ = media_bin
    path = tmp_path / "muzică model.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(22050)
        handle.writeframes(b"\x00\x00" * 11025)
    files = {"wav": path}
    encoders = available_encoders(ffmpeg)
    for target in ("mp3", "flac", "ogg", "opus"):
        out = tmp_path / f"muzică model.{target}"
        codecs = {"mp3": "libmp3lame", "flac": "flac", "ogg": "libvorbis", "opus": "libopus"}
        if codecs[target] not in encoders:
            continue
        conversion = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(path),
             "-c:a", codecs[target], str(out)], capture_output=True, timeout=30
        )
        if conversion.returncode != 0:
            pytest.skip(f"Local FFmpeg cannot generate {target} fixture")
        files[target] = out
    return files


@pytest.mark.parametrize("source_format", ["wav", "mp3", "flac", "ogg", "opus"])
def test_all_audio_output_routes(source_format: str, audio_samples, tmp_path: Path, media_bin):
    if source_format not in audio_samples:
        pytest.skip("The requested source encoder is not installed")
    path = audio_samples[source_format]
    original = path.read_bytes()
    encoders = available_encoders(media_bin[0])
    required = {"mp3": ("libmp3lame", "libshine"), "wav": ("pcm_s16le",),
                "flac": ("flac",), "ogg": ("libvorbis",), "opus": ("libopus",)}
    expected = {
        target for target in AUDIO_TARGETS
        if target != source_format and any(codec in encoders for codec in required[target])
    }
    assert set(get_targets(path)) == expected
    for target in sorted(expected):
        out = convert_file(path, target, tmp_path / "out")
        assert out.stat().st_size > 0
        info = inspect_source(out)
        assert info is not None
        assert info.format == target
    assert path.read_bytes() == original


@pytest.fixture
def video_samples(tmp_path: Path, media_bin):
    ffmpeg, _ = media_bin
    files = {}
    for target, video_codec, audio_codec, muxer in [
        ("mp4", "libx264", "aac", "mp4"),
        ("webm", "libvpx-vp9", "libopus", "webm"),
        ("mov", "mpeg4", "aac", "mov"),
        ("mkv", "mpeg4", "aac", "matroska"),
        ("avi", "mpeg4", "libmp3lame", "avi"),
    ]:
        output = tmp_path / f"cadru video 漢字.{target}"
        command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                   "-f", "lavfi", "-i", "color=c=blue:size=64x64:rate=10:duration=0.4",
                   "-f", "lavfi", "-i", "sine=frequency=400:duration=0.4",
                   "-c:v", video_codec, "-c:a", audio_codec, "-shortest", "-f", muxer, str(output)]
        proc = subprocess.run(command, capture_output=True, timeout=45)
        if proc.returncode != 0:
            pytest.skip(f"Local FFmpeg cannot generate {target} fixture")
        files[target] = output
    return files


@pytest.mark.parametrize("source_format", ["mp4", "webm", "mov", "mkv", "avi"])
@pytest.mark.parametrize("target", VIDEO_TARGETS)
def test_all_video_output_routes(source_format: str, target: str, video_samples, tmp_path: Path):
    if source_format == target:
        pytest.skip("Output is already in that container")
    input_path = video_samples[source_format]
    original = input_path.read_bytes()
    assert set(get_targets(input_path)) == set(VIDEO_TARGETS) - {source_format}
    output = convert_file(input_path, target, tmp_path / "out")
    info = inspect_source(output)
    assert info is not None and info.kind == "video" and info.format == target
    assert info.has_audio
    assert input_path.read_bytes() == original
