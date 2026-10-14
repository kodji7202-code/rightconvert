"""Independent, actual v0.2 directed-format acceptance on tiny local files.

Run from the repository root: ``python scripts/validate_stage5.py``.
The input families come from the current format registry. This is a historical
Stage5 IMAGE/AUDIO/VIDEO matrix only: newer Stage6 image->PDF routes are
verified by validate_stage6.py (using independent PDF decoders). Current
targets still come from supported_targets and GUI-facing get_targets; this
validator tests the original legacy subset rather than misdecoding PDFs with
Pillow's image reader.

The evidence JSON is written even when individual fixtures/routes fail. Exit 1
means a coverage, fixture, conversion, decoding, or integrity failure.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import struct
import subprocess
import sys
import tempfile
import threading
import time
import wave
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rightconvert.engine import convert_file, get_targets  # noqa: E402
from rightconvert.formats import (  # noqa: E402
    _PIL_INPUTS, AUDIO_TARGETS, IMAGE_TARGETS, VIDEO_INPUTS, VIDEO_TARGETS,
    inspect_source, media_tools, supported_targets,
)

DEFAULT_REPORT = ROOT / "docs" / "STAGE5_FORMAT_MATRIX_RESULTS.json"
IMAGE_TYPES = {"jpg": "JPEG", "png": "PNG", "webp": "WEBP", "bmp": "BMP", "tiff": "TIFF", "gif": "GIF"}
VIDEO_SETTINGS = {
    "mp4": ("libx264", "aac", "mp4"),
    "webm": ("libvpx-vp9", "libopus", "webm"),
    "mov": ("mpeg4", "aac", "mov"),
    "avi": ("mpeg4", "libmp3lame", "avi"),
    "mkv": ("mpeg4", "aac", "matroska"),
}
AUDIO_SETTINGS = {
    "mp3": ("libmp3lame", "mp3"),
    "flac": ("flac", "flac"),
    "ogg": ("libvorbis", "ogg"),
    "opus": ("libopus", "opus"),
}


def sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def invoke(command: list[str], *, timeout: int = 25) -> subprocess.CompletedProcess:
    return subprocess.run(
        command, check=False, timeout=timeout, capture_output=True,
        stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def run_ok(command: list[str], label: str, *, timeout: int = 25) -> bytes:
    done = invoke(command, timeout=timeout)
    if done.returncode:
        error = done.stderr.decode("utf-8", "replace")[-1200:]
        raise AssertionError(f"{label}: exit {done.returncode}: {error}")
    return done.stdout


def make_images(directory: Path) -> dict[str, Path]:
    result = {}
    for extension, pillow_name in IMAGE_TYPES.items():
        dest = directory / f"image spații 東京.{extension}"
        with Image.new("RGB", (100, 60), (55, 120, 210)) as picture:
            picture.save(dest, format=pillow_name)
        result[extension] = dest
    return result


def make_audio(directory: Path, ffmpeg: str) -> dict[str, Path]:
    wav = directory / "audio café 東京.wav"
    frames = [int(10000 * math.sin(2 * math.pi * 440 * i / 24000)) for i in range(14400)]
    with wave.open(str(wav), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        handle.writeframes(struct.pack("<" + "h" * len(frames), *frames))
    result = {"wav": wav}
    for extension in sorted(set(AUDIO_TARGETS) - {"wav"}):
        codec, muxer = AUDIO_SETTINGS[extension]
        target = directory / f"audio café 東京.{extension}"
        run_ok([
            ffmpeg, "-hide_banner", "-v", "error", "-nostdin", "-threads", "1",
            "-protocol_whitelist", "file,pipe", "-i", str(wav),
            "-map", "0:a:0", "-c:a", codec, "-f", muxer, str(target),
        ], f"generate {extension}")
        result[extension] = target
    return result


def make_video(directory: Path, ffmpeg: str) -> dict[str, Path]:
    result = {}
    for extension in VIDEO_INPUTS:
        codec, audio_codec, muxer = VIDEO_SETTINGS[extension]
        target = directory / f"video café 東京.{extension}"
        command = [
            ffmpeg, "-hide_banner", "-v", "error", "-nostdin", "-threads", "1",
            "-filter_threads", "1", "-filter_complex_threads", "1",
            "-f", "lavfi", "-i", "color=c=blue:s=160x90:r=10:d=0.6",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=24000:duration=0.6",
            "-map", "0:v:0", "-map", "1:a:0", "-c:v", codec, "-threads:v", "1",
            "-pix_fmt", "yuv420p", "-c:a", audio_codec, "-shortest",
        ]
        if extension == "webm":
            command += ["-deadline", "realtime", "-cpu-used", "7", "-b:v", "150k"]
        elif extension == "mp4":
            command += ["-preset", "ultrafast"]
        run_ok(command + ["-f", muxer, str(target)], f"generate {extension}")
        result[extension] = target
    return result


def assert_media_magic(output: Path, target: str) -> None:
    with output.open("rb") as handle:
        first = handle.read(512)
    if target == "mp3":
        good = first.startswith(b"ID3") or (len(first) >= 2 and first[0] == 255 and (first[1] & 224) == 224)
    elif target == "wav":
        good = first.startswith(b"RIFF") and first[8:12] == b"WAVE"
    elif target == "flac":
        good = first.startswith(b"fLaC")
    elif target in ("ogg", "opus"):
        good = first.startswith(b"OggS")
    elif target == "mp4":
        good = first[4:8] == b"ftyp"
    elif target == "mov":
        good = first[4:8] == b"ftyp" and first[8:12] == b"qt  "
    elif target == "avi":
        good = first.startswith(b"RIFF") and first[8:12] == b"AVI "
    elif target in ("webm", "mkv"):
        doc = b"webm" if target == "webm" else b"matroska"
        good = first.startswith(b"\x1a\x45\xdf\xa3") and doc in first[:256]
    else:
        raise AssertionError(f"No independent magic check for target {target}")
    assert good, f"Unexpected {target} header: {first[:24].hex()}"


def independent_wav_read(output: Path) -> dict:
    """Cross-check PCM with stdlib wave, even if FFmpeg rejects its RIFF size."""
    with output.open("rb") as stream:
        header = stream.read(44)
    with wave.open(str(output), "rb") as wav:
        assert wav.getnchannels() in (1, 2), "Unexpected WAV channel count"
        assert wav.getsampwidth() in (2, 3, 4), "Unexpected WAV sample depth"
        pcm = wav.readframes(24000 * 3)
        assert len(pcm) >= 2000, "No meaningful WAV PCM frames"
        return {"decoder": "wave.readframes", "channels": wav.getnchannels(),
                "rate": wav.getframerate(), "sample_width": wav.getsampwidth(),
                "decoded_bytes": len(pcm), "header_riff_size": int.from_bytes(header[4:8], "little"),
                "metadata_frames": wav.getnframes()}


def verify_output(output: Path, target: str, family: str, ffmpeg: str, ffprobe: str) -> dict:
    assert output.is_file() and 0 < output.stat().st_size < 32 * 1024 * 1024, "Missing, empty, or oversize output"
    if family == "image":
        with Image.open(output) as image:
            assert image.format == IMAGE_TYPES[target], (target, image.format)
            assert image.size == (100, 60), (target, image.size)
            image.load()  # Decode pixels independently of RightConvert's sniff.
            return {"decoder": "Pillow/Image.load", "format": image.format, "size": list(image.size)}

    assert_media_magic(output, target)
    raw = run_ok([
        ffprobe, "-v", "error", "-protocol_whitelist", "file,pipe",
        "-show_entries", "format=format_name:stream=codec_name,codec_type,width,height",
        "-of", "json", "-i", str(output),
    ], f"independent FFprobe for {output.name}")
    metadata = json.loads(raw)
    streams = metadata.get("streams", [])
    kinds = [stream.get("codec_type") for stream in streams]
    expected = "audio" if family == "audio" else "video"
    assert expected in kinds, f"Missing {expected} stream: {streams}"
    if family == "video":
        assert "audio" in kinds, f"Audio stream discarded: {streams}"
        assert any(s.get("codec_type") == "video" and int(s.get("width", 0)) == 160
                   and int(s.get("height", 0)) == 90 for s in streams), streams
    if target in ("ogg", "opus"):
        expected_codec = "opus" if target == "opus" else "vorbis"
        assert any(s.get("codec_name") == expected_codec for s in streams), streams

    # Probe success alone does not prove frames/samples are decodable. Decode to
    # null using an independent FFmpeg invocation (local file/pipe only).
    run_ok([
        ffmpeg, "-hide_banner", "-v", "error", "-xerror", "-nostdin",
        "-threads", "1", "-protocol_whitelist", "file,pipe",
        "-i", str(output), "-map", "0:a:0" if family == "audio" else "0:v:0",
        "-f", "null", "-",
    ], f"independent decode of {output.name}")
    if family == "video":
        run_ok([
            ffmpeg, "-hide_banner", "-v", "error", "-xerror", "-nostdin",
            "-threads", "1", "-protocol_whitelist", "file,pipe",
            "-i", str(output), "-map", "0:a:0", "-f", "null", "-",
        ], f"independent audio stream decode of {output.name}")
    return {"decoder": "FFprobe + FFmpeg decode-to-null", "container": metadata.get("format", {}).get("format_name"),
            "codecs": [s.get("codec_name") for s in streams], "streams": kinds}


def run_with_limit(source: Path, target: str, output: Path, *, seconds: float = 40) -> Path:
    completed: dict[str, object] = {}
    cancelled = threading.Event()

    def worker() -> None:
        try:
            completed["path"] = convert_file(source, target, output, cancel=cancelled)
        except BaseException as exc:
            completed["error"] = exc

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        cancelled.set()
        thread.join(5)
        raise TimeoutError(f"Conversion took longer than {seconds}s (cancel_requested=True, stopped={not thread.is_alive()})")
    if "error" in completed:
        raise completed["error"]
    return completed["path"]  # type: ignore[return-value]


def matrix(report_dir: Path | None = None) -> dict:
    result: dict = {
        "schema_version": 1,
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "app_version": "v0.2 source tree (not published package)",
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "fixture": {"image_px": [100, 60], "video_px": [160, 90], "seconds": 0.6,
                    "video_fps": 10, "audio_hz": 24000, "contains_audio_in_video": True},
        "routes": [], "inputs": [], "errors": [],
    }
    tools = media_tools()
    if tools is None:
        result["errors"].append("Missing separately installed FFmpeg and FFprobe")
        result["summary"] = {"status": "FAIL", "pass": 0, "fail": 1, "routes_advertised": 0}
        return result
    ffmpeg, ffprobe = tools
    try:
        result["ffmpeg"] = run_ok([ffmpeg, "-version"], "FFmpeg version").decode("utf-8", "replace").splitlines()[0]
        result["ffprobe"] = run_ok([ffprobe, "-version"], "FFprobe version").decode("utf-8", "replace").splitlines()[0]
    except Exception as exc:
        result["errors"].append(f"Backend verification: {type(exc).__name__}: {exc}")
        result["summary"] = {"status": "FAIL", "pass": 0, "fail": 1, "routes_advertised": 0}
        return result

    # No real documents, network paths, registry operations, or GUI calls; all
    # fixtures/results reside in a TemporaryDirectory and are removed on exit.
    with tempfile.TemporaryDirectory(prefix="rightconvert-stage5-") as tmp:
        home = Path(tmp)
        sources_dir = home / "sources"
        sources_dir.mkdir()
        kinds: dict[str, dict[str, Path]] = {"image": {}, "audio": {}, "video": {}}
        generators = (("image", lambda: make_images(sources_dir)),
                      ("audio", lambda: make_audio(sources_dir, ffmpeg)),
                      ("video", lambda: make_video(sources_dir, ffmpeg)))
        for family, generator in generators:
            try:
                kinds[family] = generator()
            except Exception as exc:
                result["errors"].append(f"Fixture generation for {family}: {type(exc).__name__}: {exc}")

        expected: dict[str, set[str]] = {
            "image": set(_PIL_INPUTS.values()), "audio": set(AUDIO_TARGETS),
            "video": set(VIDEO_INPUTS),
        }
        for family in ("image", "audio", "video"):
            for format_name in sorted(expected[family]):
                item: dict = {"family": family, "source_format": format_name, "fixture_created": False,
                              "detected_format": None, "advertised_targets": [], "status": "FAIL"}
                result["inputs"].append(item)
                source = kinds[family].get(format_name)
                if source is None:
                    item["error"] = "No synthetic fixture for this declared input family"
                    continue
                item["fixture_created"] = True
                baseline = sha256(source)
                item["source_sha256"] = baseline
                try:
                    # Prove fixture integrity with an independent decoder
                    # before treating its advertised conversion set as evidence.
                    item["fixture_verification"] = verify_output(
                        source, format_name, family, ffmpeg, ffprobe)
                    info = inspect_source(source)
                    assert info is not None, "Source not recognized by content inspection"
                    item["detected_format"] = info.format
                    assert info.kind == family and info.format == format_name, (
                        f"Detected {info.kind}/{info.format}, expected {family}/{format_name}")
                    proposed = supported_targets(info)
                    advertised = get_targets(source)
                    item["current_advertised_targets"] = list(advertised)
                    assert len(set(advertised)) == len(advertised), "Duplicate advertised targets"
                    assert set(proposed) == set(advertised), (
                        f"supported_targets={proposed}, get_targets={advertised}")
                    assert advertised, "Input detected but no conversion targets advertised"
                    legacy_targets = [target for target in advertised if not
                                      (family == "image" and target == "pdf")]
                    item["advertised_targets"] = list(legacy_targets)
                    assert legacy_targets, "No legacy targets remained available"
                    item["status"] = "PASS"
                    for target in legacy_targets:
                        record: dict = {"family": family, "source": format_name, "target": target,
                                        "status": "FAIL", "source_untouched": False, "temp_clean": False}
                        result["routes"].append(record)
                        route_dir = home / f"out-{family}-{format_name}-{target}"
                        route_dir.mkdir()
                        started = time.monotonic()
                        try:
                            created = run_with_limit(source, target, route_dir)
                            assert created.parent == route_dir, "Unexpected destination folder"
                            assert created.suffix.lower() == "." + target, "Wrong output suffix"
                            if target == "wav":
                                # Preserve independent facts for failure analysis
                                # even when the stricter FFmpeg decoder rejects.
                                record["wav_pcm_check"] = independent_wav_read(created)
                            record["verification"] = verify_output(created, target, family, ffmpeg, ffprobe)
                            record["output_bytes"] = created.stat().st_size
                            record["status"] = "PASS"
                        except Exception as exc:
                            record["error"] = f"{type(exc).__name__}: {str(exc)[-1200:]}"
                        finally:
                            record["seconds"] = round(time.monotonic() - started, 3)
                            record["source_untouched"] = sha256(source) == baseline
                            record["temp_clean"] = not any(p.name.startswith(".rightconvert-stage-")
                                                           for p in route_dir.iterdir())
                            if not record["source_untouched"] or not record["temp_clean"]:
                                record["status"] = "FAIL"
                                record["error"] = record.get("error", "") + " Source changed or temporary staging leaked"
                except Exception as exc:
                    item["error"] = f"{type(exc).__name__}: {exc}"
                finally:
                    item["source_untouched"] = sha256(source) == baseline
                    if not item["source_untouched"]:
                        item["status"] = "FAIL"
                        item["error"] = item.get("error", "") + " Source file was modified"
    counts = Counter(row["status"] for row in result["routes"])
    broken_inputs = sum(row["status"] != "PASS" for row in result["inputs"])
    total_failures = counts["FAIL"] + broken_inputs + len(result["errors"])
    result["summary"] = {"status": "PASS" if total_failures == 0 else "FAIL",
                         "input_families": len(result["inputs"]), "inputs_pass": len(result["inputs"]) - broken_inputs,
                         "input_fail": broken_inputs, "routes_advertised": len(result["routes"]),
                         "pass": counts["PASS"], "fail": counts["FAIL"],
                         "general_errors": len(result["errors"]), "total_failures": total_failures}
    return result


def main() -> int:
    # Default Windows consoles may be cp1252 even with Unicode fixture names.
    # An encoding failure must not suppress acceptance FAIL evidence.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT,
                        help="Machine-readable JSON destination (default: docs/STAGE5_FORMAT_MATRIX_RESULTS.json)")
    args = parser.parse_args()
    evidence = matrix()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    summary = evidence["summary"]
    print(f"Stage5 {summary['status']}: inputs {summary.get('inputs_pass', 0)}/{summary.get('input_families', 0)}, "
          f"advertised routes {summary['pass']}/{summary['routes_advertised']} PASS; "
          f"failures {summary.get('total_failures', summary['fail'])}")
    print(f"Evidence: {args.report.resolve()}")
    for issue in evidence["errors"]:
        print("FAIL general:", issue)
    for item in evidence["inputs"]:
        if item["status"] != "PASS":
            print(f"FAIL input {item['family']}/{item['source_format']}: {item.get('error', 'unknown')}")
    for route in evidence["routes"]:
        if route["status"] != "PASS":
            print(f"FAIL route {route['source']}->{route['target']}: {route.get('error', 'unknown')}")
    return int(summary["status"] != "PASS")


if __name__ == "__main__":
    raise SystemExit(main())
