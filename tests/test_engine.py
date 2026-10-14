"""Black-box conversion tests; media cases skip when FFmpeg is unavailable."""

from __future__ import annotations

import subprocess
import threading
import wave
import os
import errno
import json
import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from rightconvert.engine import (
    ConversionCancelled,
    ConversionError,
    UnsupportedFormatError,
    convert_file,
    get_targets,
)
from rightconvert.formats import available_encoders, media_tools

PIL = pytest.importorskip("PIL.Image")
Image = PIL


def _image(path: Path, *, fmt: str = "JPEG") -> Path:
    Image.new("RGB", (12, 8), "#5a88ad").save(path, format=fmt)
    return path


def _wav(path: Path) -> Path:
    # Short silence is still valid audio and is deterministic/offline.
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(44100)
        stream.writeframes(b"\x00\x00" * 44100)
    return path


def _probe_streams(ffprobe: str, source: Path) -> list[dict]:
    output = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "stream=codec_type,codec_name,bits_per_raw_sample,bits_per_sample", "-of", "json", "-i", str(source)],
        capture_output=True,
        text=True,
        check=True,
        timeout=20,
    )
    return json.loads(output.stdout)["streams"]


def _attack_staged_temp(
    output_dir: Path,
    victim: Path,
) -> tuple[bool, Path]:
    """Try replacing staging path with a hardlink, as a same-user adversary.

    Windows may reject unlink while the original descriptor is held open,
    which itself prevents the injected pathname attack. POSIX permits it.
    """
    staged = list(output_dir.glob(".rightconvert-stage-*/part-*"))
    assert len(staged) == 1
    original_temp = staged[0]
    try:
        original_temp.unlink()
    except PermissionError:
        return False, original_temp
    os.link(victim, original_temp)
    return True, original_temp


@pytest.fixture
def ffmpeg_tools() -> tuple[str, str]:
    tools = media_tools()
    if tools is None:
        pytest.skip("FFmpeg and FFprobe are not installed")
    return tools


def test_content_sniff_targets_even_when_extension_lies(tmp_path: Path) -> None:
    source = _image(tmp_path / "unexpected_extension.data")
    targets = get_targets(source)
    assert "png" in targets
    assert "jpg" not in targets
    converted = convert_file(source, "PNG", tmp_path / "output")
    assert converted.name == "unexpected_extension.png"
    with Image.open(converted) as result:
        assert result.format == "PNG"
        assert result.size == (12, 8)
    assert source.read_bytes().startswith(b"\xff\xd8")


def test_collision_publication_does_not_overwrite_existing(tmp_path: Path) -> None:
    source = _image(tmp_path / "hello worlds.jpg")
    target_folder = tmp_path / "out"
    target_folder.mkdir()
    existing = target_folder / "hello worlds.png"
    existing.write_bytes(b"leave this alone")
    first = convert_file(source, "png", target_folder)
    second = convert_file(source, ".png", target_folder)
    assert first.name == "hello worlds (1).png"
    assert second.name == "hello worlds (2).png"
    assert first.read_bytes().startswith(b"\x89PNG")
    assert second.read_bytes().startswith(b"\x89PNG")
    assert existing.read_bytes() == b"leave this alone"
    assert not any("rightconvert-" in p.name for p in target_folder.iterdir())


def test_transparent_png_to_jpeg_has_white_background(tmp_path: Path) -> None:
    source = tmp_path / "opaque.png"
    Image.new("RGBA", (8, 8), (40, 50, 60, 0)).save(source)
    output = convert_file(source, "jpeg", tmp_path)
    with Image.open(output) as result:
        assert result.format == "JPEG"
        assert all(value >= 250 for value in result.getpixel((1, 1)))
    with Image.open(source) as original:
        assert original.mode == "RGBA"


def test_cmyk_jpeg_can_convert_to_png(tmp_path: Path) -> None:
    source = tmp_path / "cmyk.jpg"
    Image.new("CMYK", (9, 9), (20, 30, 40, 0)).save(source, format="JPEG")
    assert "png" in get_targets(source)
    result = convert_file(source, "png", tmp_path)
    with Image.open(result) as converted:
        assert converted.mode == "RGB"
        assert converted.size == (9, 9)


def test_v02_cmyk_jpeg_png_webp_and_tiff_preserve_valid_pixel_data(tmp_path: Path) -> None:
    source = tmp_path / "CMYK țintă.jpg"
    Image.new("CMYK", (13, 11), (30, 90, 150, 20)).save(source, format="JPEG")
    original = source.read_bytes()
    assert set(get_targets(source)) == {"png", "webp", "tiff", "pdf"}
    # CMYK does not map to BMP's RGB palette without a color-model change,
    # so an apparently lossless BMP target must not be advertised.
    with pytest.raises(UnsupportedFormatError):
        convert_file(source, "bmp", tmp_path)
    for target, mode in (("png", "RGB"), ("webp", "RGB"), ("tiff", "CMYK")):
        result = convert_file(source, target, tmp_path)
        with Image.open(result) as output:
            assert output.format == {"png": "PNG", "webp": "WEBP", "tiff": "TIFF"}[target]
            assert output.mode == mode
            assert output.size == (13, 11)
            output.load()
    assert source.read_bytes() == original


def test_animated_webp_not_silently_reduced_to_one_frame(tmp_path: Path) -> None:
    source = tmp_path / "animated.webp"
    first = Image.new("RGB", (16, 16), "red")
    second = Image.new("RGB", (16, 16), "blue")
    first.save(source, format="WEBP", save_all=True, append_images=[second], duration=100, loop=0)
    assert get_targets(source) == []


def test_concurrent_conversion_reserves_unique_names(tmp_path: Path) -> None:
    source = _image(tmp_path / "same.jpg")
    with ThreadPoolExecutor(max_workers=4) as pool:
        paths = list(pool.map(lambda _: convert_file(source, "png", tmp_path), range(4)))
    assert {p.name for p in paths} == {
        "same.png", "same (1).png", "same (2).png", "same (3).png"
    }
    assert all(p.stat().st_size > 0 for p in paths)


def test_bad_sources_and_unknown_targets(tmp_path: Path) -> None:
    wrong = tmp_path / "bad.png"
    wrong.write_bytes(b"not a valid image or supported media format")
    assert get_targets(wrong) == []
    assert get_targets(tmp_path / "absent.png") == []
    with pytest.raises(UnsupportedFormatError):
        convert_file(wrong, "webp", tmp_path)
    with pytest.raises(UnsupportedFormatError):
        convert_file(wrong, "exe", tmp_path)
    actual = _image(tmp_path / "input.jpg")
    with pytest.raises(UnsupportedFormatError):
        convert_file(actual, "mp4", tmp_path)
    with pytest.raises(UnsupportedFormatError):
        convert_file(actual, "jpg", tmp_path)


def test_precancelled_image_does_not_leave_placeholder(tmp_path: Path) -> None:
    source = _image(tmp_path / "input.jpg")
    cancellation = threading.Event()
    cancellation.set()
    with pytest.raises(ConversionCancelled):
        convert_file(source, "png", tmp_path, cancel=cancellation)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["input.jpg"]


def test_cancel_from_image_progress_cleans_up(tmp_path: Path) -> None:
    source = _image(tmp_path / "input.jpg")
    cancellation = threading.Event()
    progress = []

    def on_progress(fraction: float) -> None:
        progress.append(fraction)
        if fraction == 0:
            cancellation.set()

    with pytest.raises(ConversionCancelled):
        convert_file(source, "png", tmp_path, cancel=cancellation, progress=on_progress)
    assert progress == [0]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["input.jpg"]


def test_external_writer_takes_target_during_conversion(tmp_path: Path) -> None:
    """Another process's file must survive the atomic publish name collision."""
    source = _image(tmp_path / "picture.jpg")
    original = source.read_bytes()
    target = tmp_path / "picture.png"
    third_party_data = b"unrelated writer's file--never overwrite"

    def progress(fraction: float) -> None:
        if fraction == 0.0:
            # The destination must not be reserved by an empty placeholder.
            assert not target.exists()
            target.write_bytes(third_party_data)

    result = convert_file(source, "png", tmp_path, progress=progress)
    assert result.name == "picture (1).png"
    assert target.read_bytes() == third_party_data
    assert source.read_bytes() == original
    assert result.read_bytes().startswith(b"\x89PNG")
    assert not list(tmp_path.glob(".*.rightconvert-*"))


def test_external_writer_file_survives_conversion_cancellation(tmp_path: Path) -> None:
    """Cancellation must only clean our random temp, never another writer's file."""
    source = _image(tmp_path / "picture.jpg")
    original = source.read_bytes()
    target = tmp_path / "picture.png"
    third_party_data = b"external document survives cancellation"
    cancellation = threading.Event()

    def progress(fraction: float) -> None:
        if fraction == 0.0:
            assert not target.exists()
            target.write_bytes(third_party_data)
            cancellation.set()

    with pytest.raises(ConversionCancelled):
        convert_file(source, "png", tmp_path, cancel=cancellation, progress=progress)
    assert target.read_bytes() == third_party_data
    assert source.read_bytes() == original
    assert sorted(p.name for p in tmp_path.iterdir()) == ["picture.jpg", "picture.png"]


@pytest.mark.parametrize("victim_is_source", [True, False])
def test_image_hardlink_temp_swap_does_not_corrupt_any_victim(
    tmp_path: Path, victim_is_source: bool
) -> None:
    source = _image(tmp_path / "source.jpg")
    victim = source if victim_is_source else tmp_path / "unrelated-document.bin"
    if victim is not source:
        victim.write_bytes(b"third party document: irreversible if clobbered")
    original_source = hashlib.sha256(source.read_bytes()).digest()
    original_victim = hashlib.sha256(victim.read_bytes()).digest()
    attack: list[tuple[bool, Path]] = []

    def on_progress(fraction: float) -> None:
        if fraction == 0.0:
            attack.append(_attack_staged_temp(tmp_path, victim))

    try:
        converted = convert_file(source, "png", tmp_path, progress=on_progress)
    except ConversionError as exc:
        assert attack and attack[0][0], f"Unexpected conversion failure: {exc}"
    else:
        assert attack and not attack[0][0], "A swapped temp must not publish"
        assert converted.read_bytes().startswith(b"\x89PNG")
    assert hashlib.sha256(source.read_bytes()).digest() == original_source
    assert hashlib.sha256(victim.read_bytes()).digest() == original_victim
    if attack[0][0]:
        # Do not delete another writer's hardlink during cleanup.
        assert attack[0][1].exists()
        assert os.path.samefile(attack[0][1], victim)


def test_cancel_after_temp_replacement_does_not_delete_foreign_hardlink(tmp_path: Path) -> None:
    source = _image(tmp_path / "source.jpg")
    victim = tmp_path / "victim.txt"
    victim.write_bytes(b"keep-this-file")
    before_source = source.read_bytes()
    before_victim = victim.read_bytes()
    signal = threading.Event()
    attack: list[tuple[bool, Path]] = []

    def on_progress(fraction: float) -> None:
        if fraction == 0.0:
            attack.append(_attack_staged_temp(tmp_path, victim))
            signal.set()

    with pytest.raises(ConversionCancelled):
        convert_file(source, "png", tmp_path, cancel=signal, progress=on_progress)
    assert source.read_bytes() == before_source
    assert victim.read_bytes() == before_victim
    assert not (tmp_path / "source.png").exists()
    assert attack
    if attack[0][0]:
        assert attack[0][1].exists()
        assert os.path.samefile(attack[0][1], victim)


def test_media_hardlink_temp_swap_does_not_corrupt_victim(
    tmp_path: Path, ffmpeg_tools: tuple[str, str]
) -> None:
    source = _wav(tmp_path / "sound.wav")
    if "mp3" not in get_targets(source):
        pytest.skip("MP3 encoder unavailable")
    victim = tmp_path / "unrelated.bin"
    victim.write_bytes(b"independent victim must remain byte-for-byte unchanged")
    original_source = hashlib.sha256(source.read_bytes()).digest()
    original_victim = hashlib.sha256(victim.read_bytes()).digest()
    attack: list[tuple[bool, Path]] = []

    def on_progress(fraction: float) -> None:
        if fraction == 0.0:
            attack.append(_attack_staged_temp(tmp_path, victim))

    try:
        converted = convert_file(source, "mp3", tmp_path, progress=on_progress)
    except ConversionError as exc:
        assert attack and attack[0][0], f"Unexpected conversion failure: {exc}"
    else:
        assert attack and not attack[0][0], "A swapped media temp must not publish"
        assert converted.stat().st_size > 0
    assert hashlib.sha256(source.read_bytes()).digest() == original_source
    assert hashlib.sha256(victim.read_bytes()).digest() == original_victim
    if attack[0][0]:
        assert attack[0][1].exists()
        assert os.path.samefile(attack[0][1], victim)


def test_error_in_converter_removes_reserved_and_temporary_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import rightconvert.engine as engine

    source = _image(tmp_path / "input.jpg")

    def fail(*args: object, **kwargs: object) -> None:
        raise ConversionError("simulated converter failure")

    monkeypatch.setattr(engine, "_convert_image", fail)
    with pytest.raises(ConversionError, match="simulated converter failure"):
        convert_file(source, "png", tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["input.jpg"]


def test_failed_commit_disk_full_preserves_original(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import rightconvert.engine as engine

    source = _image(tmp_path / "input.jpg")
    original = source.read_bytes()

    def out_of_disk(*args: object, **kwargs: object) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(engine, "_win_rename_by_handle" if os.name == "nt" else "_link_by_handle", out_of_disk)
    with pytest.raises(ConversionError, match="No space left on device"):
        convert_file(source, "png", tmp_path)
    assert source.read_bytes() == original
    assert sorted(p.name for p in tmp_path.iterdir()) == ["input.jpg"]


def test_staging_acl_denial_fails_without_retry_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import rightconvert.engine as engine

    source = _image(tmp_path / "source.jpg")
    original = source.read_bytes()
    attempts = 0
    real_mkdir = engine.os.mkdir

    def deny_staging(path, mode=0o777, *args, **kwargs):
        nonlocal attempts
        if str(path).find(".rightconvert-stage-") != -1:
            attempts += 1
            raise PermissionError(errno.EACCES, "ACL denies folder creation")
        return real_mkdir(path, mode, *args, **kwargs)

    monkeypatch.setattr(engine.os, "mkdir", deny_staging)
    with pytest.raises(ConversionError, match="ACL denies folder creation"):
        convert_file(source, "png", tmp_path)
    assert attempts == 1
    assert source.read_bytes() == original
    assert sorted(p.name for p in tmp_path.iterdir()) == ["source.jpg"]


def test_temp_swap_at_publish_does_not_publish_source_hardlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Attempt a source-hardlink swap after validation but before OS commit."""
    import rightconvert.engine as engine

    source = _image(tmp_path / "victim.jpg")
    original = source.read_bytes()
    attempts: list[bool] = []
    method = "_win_rename_by_handle" if os.name == "nt" else "_link_by_handle"
    actual_publish = getattr(engine, method)

    def hostile_publish(fd: int, candidate: Path) -> None:
        current = next(tmp_path.glob(".rightconvert-stage-*/part-*"))
        try:
            current.unlink()
        except PermissionError:
            attempts.append(False)
        else:
            os.link(source, current)
            attempts.append(True)
        actual_publish(fd, candidate)

    monkeypatch.setattr(engine, method, hostile_publish)
    try:
        output = convert_file(source, "png", tmp_path)
    except ConversionError:
        assert attempts and attempts[0]
    else:
        assert attempts
        assert output.read_bytes().startswith(b"\x89PNG")
        assert not os.path.samefile(output, source)
    assert source.read_bytes() == original


def test_audio_conversion_targets_and_unicode_filename(tmp_path: Path, ffmpeg_tools: tuple[str, str]) -> None:
    source = _wav(tmp_path / "muzică și spații.wav")
    if "mp3" not in get_targets(source):
        pytest.skip("This FFmpeg lacks an MP3 encoder")
    seen: list[float] = []
    converted = convert_file(source, "mp3", tmp_path, progress=seen.append)
    assert converted.name == "muzică și spații.mp3"
    assert converted.stat().st_size > 0
    assert "wav" in get_targets(converted)
    assert seen[0] == 0.0 and seen[-1] == 1.0
    assert all(0.0 <= value <= 1.0 for value in seen)
    assert source.stat().st_size > converted.stat().st_size


@pytest.mark.parametrize("source_target", ("mp3", "flac", "ogg", "opus"))
def test_streamed_wav_header_has_final_finite_lengths(
    tmp_path: Path, ffmpeg_tools: tuple[str, str], source_target: str
) -> None:
    """WAV output must decode strictly; FFmpeg pipe emits unknown RIFF lengths."""
    input_wav = _wav(tmp_path / "voice.wav")
    if source_target not in get_targets(input_wav):
        pytest.skip(f"Encoder for {source_target} unavailable")
    source = convert_file(input_wav, source_target, tmp_path)
    if "wav" not in get_targets(source):
        pytest.skip(f"WAV target unavailable from {source_target}")
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    result = convert_file(source, "wav", tmp_path)
    header = result.read_bytes()[:12]
    assert header.startswith(b"RIFF") and header[8:] == b"WAVE"
    assert int.from_bytes(header[4:8], "little") == result.stat().st_size - 8
    with wave.open(str(result), "rb") as reader:
        assert reader.getnframes() > 0
        assert reader.getnframes() < reader.getframerate() * 3
        assert reader.readframes(reader.getnframes())
    checked = subprocess.run(
        [ffmpeg_tools[0], "-hide_banner", "-v", "error", "-xerror", "-nostdin",
         "-i", str(result), "-map", "0:a:0", "-f", "null", "-"],
        capture_output=True, timeout=25, check=False,
    )
    assert checked.returncode == 0, checked.stderr.decode(errors="replace")
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_sha


def test_cancel_during_media_conversion(tmp_path: Path, ffmpeg_tools: tuple[str, str]) -> None:
    source = _wav(tmp_path / "to_cancel.wav")
    if "flac" not in get_targets(source):
        pytest.skip("This FFmpeg lacks a FLAC encoder")
    cancellation = threading.Event()

    def progress(fraction: float) -> None:
        if fraction > 0.0:
            cancellation.set()

    with pytest.raises(ConversionCancelled):
        convert_file(source, "flac", tmp_path, cancel=cancellation, progress=progress)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["to_cancel.wav"]


def test_video_webm_to_mp4_with_ffprobe_sniff(tmp_path: Path, ffmpeg_tools: tuple[str, str]) -> None:
    encoders = available_encoders(ffmpeg_tools[0])
    if "libvpx-vp9" not in encoders or not ({"libx264", "mpeg4"} & encoders):
        pytest.skip("Video encoders unavailable")
    source = tmp_path / "clip with spaces.webm"
    result = subprocess.run(
        [
            ffmpeg_tools[0], "-nostdin", "-v", "error", "-y", "-f", "lavfi",
            "-i", "color=c=blue:s=32x24:r=10:d=0.4", "-an", "-c:v", "libvpx-vp9",
            "-f", "webm", str(source),
        ],
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert get_targets(source) == ["mp4", "mkv"]
    converted = convert_file(source, "mp4", tmp_path)
    assert converted.read_bytes()[4:8] == b"ftyp"
    assert source.exists()
    assert "webm" in get_targets(converted)


def test_video_mp4_to_webm(tmp_path: Path, ffmpeg_tools: tuple[str, str]) -> None:
    encoders = available_encoders(ffmpeg_tools[0])
    if "libvpx-vp9" not in encoders or "libx264" not in encoders:
        pytest.skip("Video encoders unavailable")
    source = tmp_path / "movie.mp4"
    result = subprocess.run(
        [
            ffmpeg_tools[0], "-nostdin", "-v", "error", "-y", "-f", "lavfi",
            "-i", "color=c=green:s=32x24:r=10:d=0.4", "-an", "-c:v", "libx264",
            "-pix_fmt", "yuv420p", "-f", "mp4", str(source),
        ],
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert get_targets(source) == ["webm", "mkv"]
    converted = convert_file(source, "webm", tmp_path)
    assert converted.read_bytes()[:4] == b"\x1a\x45\xdf\xa3"
    assert "mp4" in get_targets(converted)


@pytest.mark.parametrize("target", ["webm", "mkv"])
def test_video_conversion_preserves_multiple_audio_tracks(
    tmp_path: Path, ffmpeg_tools: tuple[str, str], target: str
) -> None:
    encoders = available_encoders(ffmpeg_tools[0])
    if not {"libx264", "libvpx-vp9", "aac", "libopus"}.issubset(encoders):
        pytest.skip("Required video/audio encoders unavailable")
    original = tmp_path / "two audio tracks.mp4"
    process = subprocess.run(
        [
            ffmpeg_tools[0], "-nostdin", "-v", "error", "-y",
            "-f", "lavfi", "-i", "color=c=red:s=32x24:r=10:d=0.6",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=0.6",
            "-f", "lavfi", "-i", "sine=frequency=880:duration=0.6",
            "-map", "0:v:0", "-map", "1:a:0", "-map", "2:a:0",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            "-f", "mp4", str(original),
        ], capture_output=True, timeout=40,
    )
    assert process.returncode == 0, process.stderr.decode(errors="replace")
    assert sum(s["codec_type"] == "audio" for s in _probe_streams(ffmpeg_tools[1], original)) == 2
    assert target in get_targets(original)
    converted = convert_file(original, target, tmp_path)
    converted_streams = _probe_streams(ffmpeg_tools[1], converted)
    assert sum(s["codec_type"] == "audio" for s in converted_streams) == 2
    assert sum(s["codec_type"] == "video" for s in converted_streams) == 1
    assert original.is_file()


def test_subtitle_track_rejects_video_conversion(tmp_path: Path, ffmpeg_tools: tuple[str, str]) -> None:
    subtitles = tmp_path / "caption.srt"
    subtitles.write_text("1\n00:00:00,000 --> 00:00:00,500\nTest caption\n", encoding="utf-8")
    source = tmp_path / "subtitled.mp4"
    process = subprocess.run(
        [
            ffmpeg_tools[0], "-nostdin", "-v", "error", "-y",
            "-f", "lavfi", "-i", "color=c=blue:s=32x24:r=10:d=0.6",
            "-i", str(subtitles), "-map", "0:v:0", "-map", "1:s:0",
            "-c:v", "mpeg4", "-c:s", "mov_text", "-f", "mp4", str(source),
        ], capture_output=True, timeout=40,
    )
    assert process.returncode == 0, process.stderr.decode(errors="replace")
    assert any(s["codec_type"] == "subtitle" for s in _probe_streams(ffmpeg_tools[1], source))
    assert get_targets(source) == []
    with pytest.raises(UnsupportedFormatError):
        convert_file(source, "webm", tmp_path)


def test_attached_cover_art_is_not_reencoded_as_extra_video_track(
    tmp_path: Path, ffmpeg_tools: tuple[str, str]
) -> None:
    cover = _image(tmp_path / "cover.jpg")
    source = tmp_path / "movie-with-cover.mp4"
    result = subprocess.run(
        [
            ffmpeg_tools[0], "-nostdin", "-v", "error", "-y",
            "-f", "lavfi", "-i", "color=c=blue:s=32x24:r=10:d=0.5",
            "-i", str(cover), "-map", "0:v:0", "-map", "1:v:0",
            "-c:v:0", "mpeg4", "-c:v:1", "mjpeg",
            "-disposition:v:1", "attached_pic", "-f", "mp4", str(source),
        ], capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    probe = subprocess.run(
        [ffmpeg_tools[1], "-v", "error", "-show_entries", "stream=codec_type:stream_disposition=attached_pic",
         "-of", "json", "-i", str(source)],
        capture_output=True, text=True, check=True, timeout=20,
    )
    video = [s for s in json.loads(probe.stdout)["streams"] if s["codec_type"] == "video"]
    assert len(video) == 2
    assert any(s.get("disposition", {}).get("attached_pic") for s in video)
    assert get_targets(source) == []
    with pytest.raises(UnsupportedFormatError):
        convert_file(source, "webm", tmp_path)


def test_24bit_flac_to_wav_retains_sample_precision(
    tmp_path: Path, ffmpeg_tools: tuple[str, str]
) -> None:
    if not {"flac", "pcm_s24le"}.issubset(available_encoders(ffmpeg_tools[0])):
        pytest.skip("Required lossless encoders unavailable")
    wav24 = tmp_path / "source24.wav"
    with wave.open(str(wav24), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(3)
        stream.setframerate(44100)
        stream.writeframes(b"\x01\x02\x03" * 4096)
    flac = tmp_path / "source24.flac"
    process = subprocess.run(
        [ffmpeg_tools[0], "-nostdin", "-v", "error", "-y", "-i", str(wav24), "-c:a", "flac", str(flac)],
        capture_output=True, timeout=30,
    )
    assert process.returncode == 0, process.stderr.decode(errors="replace")
    source_streams = _probe_streams(ffmpeg_tools[1], flac)
    assert int(source_streams[0].get("bits_per_raw_sample") or 0) == 24
    assert "wav" in get_targets(flac)
    result = convert_file(flac, "wav", tmp_path)
    converted_streams = _probe_streams(ffmpeg_tools[1], result)
    assert converted_streams[0]["codec_name"] == "pcm_s24le"
    assert int(converted_streams[0].get("bits_per_sample") or 0) == 24
    assert flac.is_file()


def test_media_requires_both_executables(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import rightconvert.formats as formats

    source = _wav(tmp_path / "audio.wav")
    monkeypatch.setattr(formats, "media_tools", lambda ffmpeg_path=None: None)
    assert get_targets(source) == []


def test_media_custom_binary_path(tmp_path: Path, ffmpeg_tools: tuple[str, str]) -> None:
    source = _wav(tmp_path / "audio.wav")
    assert get_targets(source, ffmpeg_path=ffmpeg_tools[0]) == get_targets(source)


def test_binary_lookup_never_uses_current_directory_or_relative_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import rightconvert.formats as formats

    extension = ".exe" if os.name == "nt" else ""
    cwd = tmp_path / "untrusted"
    cwd.mkdir()
    fake = cwd / f"ffmpeg{extension}"
    fake.write_bytes(b"malicious executable")
    (cwd / f"ffprobe{extension}").write_bytes(b"malicious executable")
    trusted = tmp_path / "trusted path"
    trusted.mkdir()
    trusted_ffmpeg = trusted / f"ffmpeg{extension}"
    trusted_ffmpeg.write_bytes(b"trusted placeholder")
    (trusted / f"ffprobe{extension}").write_bytes(b"trusted placeholder")
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(formats.sys, "frozen", False, raising=False)
    monkeypatch.setenv("PATH", f".{os.pathsep}{cwd}{os.pathsep}")
    # Both an empty PATH element and the absolute current directory are unsafe.
    assert formats._find_binary("ffmpeg") is None
    assert formats.media_tools() is None
    assert formats._find_binary("ffmpeg", f".{os.sep}ffmpeg{extension}") is None
    # An absolute trusted PATH directory is still allowed, even with spaces.
    monkeypatch.setenv("PATH", f".{os.pathsep}{cwd}{os.pathsep}{trusted}")
    tools = formats.media_tools()
    assert tools == (str(trusted_ffmpeg.resolve()), str((trusted / f"ffprobe{extension}").resolve()))
    # The user may also explicitly choose an absolute executable directory.
    assert formats.media_tools(str(trusted_ffmpeg)) == tools


def test_no_media_probe_for_unrecognized_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import rightconvert.formats as formats

    source = tmp_path / "unknown.data"
    source.write_bytes(b"unsupported")

    def explode(*args: object, **kwargs: object) -> object:
        raise AssertionError("FFprobe should not run for unknown signatures")

    monkeypatch.setattr(formats, "_probe_media", explode)
    assert get_targets(source) == []


def test_v02_static_images_reject_animation_and_multipage_tiff(tmp_path: Path) -> None:
    first = Image.new("RGB", (12, 8), "red")
    second = Image.new("RGB", (12, 8), "blue")
    for name, image_format in (("animated.gif", "GIF"), ("pages.tiff", "TIFF")):
        source = tmp_path / name
        first.save(source, format=image_format, save_all=True, append_images=[second], duration=120)
        assert get_targets(source) == [], name
        with pytest.raises(UnsupportedFormatError):
            convert_file(source, "png", tmp_path)


def test_v02_transparent_gif_never_offers_bmp_and_tiff_preserves_alpha(tmp_path: Path) -> None:
    picture = Image.new("P", (14, 9), 1)
    picture.putpalette([10, 20, 30, 90, 120, 180] + [0, 0, 0] * 254)
    picture.putpixel((0, 0), 0)
    source = tmp_path / "transparent-static.gif"
    picture.save(source, format="GIF", transparency=0)
    original = source.read_bytes()
    targets = get_targets(source)
    assert "bmp" not in targets
    assert "tiff" in targets and "png" in targets
    with pytest.raises(UnsupportedFormatError):
        convert_file(source, "bmp", tmp_path)
    dest = convert_file(source, "tiff", tmp_path)
    with Image.open(dest) as result:
        assert result.format == "TIFF"
        assert result.mode == "RGBA"
        assert result.getpixel((0, 0))[3] == 0
        assert result.getpixel((1, 1))[3] == 255
    assert source.read_bytes() == original


def test_v02_16bit_tiff_does_not_offer_8bit_or_lossy_output(tmp_path: Path) -> None:
    source = tmp_path / "16bit-unicode-東京.tiff"
    picture = Image.new("I;16", (12, 9))
    picture.putpixel((2, 3), 50001)
    picture.save(source, format="TIFF")
    original = source.read_bytes()
    assert get_targets(source) == ["png"]
    result = convert_file(source, "png", tmp_path)
    with Image.open(result) as output:
        assert output.format == "PNG"
        assert output.getpixel((2, 3)) == 50001
    assert source.read_bytes() == original


def test_v02_media_encoders_not_advertised_if_absent(tmp_path: Path, ffmpeg_tools, monkeypatch) -> None:
    import rightconvert.formats as formats

    source = _wav(tmp_path / "test-lossless.wav")
    if "libopus" in available_encoders(ffmpeg_tools[0]):
        assert "opus" in get_targets(source)
    monkeypatch.setattr(formats, "available_encoders", lambda _ffmpeg: frozenset())
    assert get_targets(source) == []
    with pytest.raises(UnsupportedFormatError):
        convert_file(source, "opus", tmp_path)


def test_v02_matroska_subtitle_is_not_silently_discarded(tmp_path: Path, ffmpeg_tools) -> None:
    source = tmp_path / "subtitled_matroska.mkv"
    subtitles = tmp_path / "captions.srt"
    subtitles.write_text("1\n00:00:00,000 --> 00:00:00,300\nDo not drop\n", encoding="utf-8")
    proc = subprocess.run(
        [ffmpeg_tools[0], "-nostdin", "-v", "error", "-y",
         "-f", "lavfi", "-i", "color=c=blue:s=32x24:r=10:d=0.4",
         "-i", str(subtitles), "-map", "0:v:0", "-map", "1:s:0",
         "-c:v", "mpeg4", "-c:s", "srt", "-f", "matroska", str(source)],
        capture_output=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stderr.decode(errors="replace")
    assert get_targets(source) == []
    with pytest.raises(UnsupportedFormatError):
        convert_file(source, "mp4", tmp_path)
