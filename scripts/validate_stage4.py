"""Deterministic local-format release acceptance with independent output probes.

Run from the project root: python scripts/validate_stage4.py
Creates only disposable fixtures under build/stage4 and writes a JSON report.
Audio/video checks need *separately installed* FFmpeg and FFprobe executables.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rightconvert.engine import convert_file
from rightconvert.formats import inspect_source, media_tools, supported_targets


REPORT = ROOT / "build" / "stage4" / "format-matrix.json"
PACKAGED_EVIDENCE = ROOT / "docs" / "STAGE4_MATRIX_RESULTS.json"


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def run(command: list[str], *, what: str) -> str:
    proc = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          check=False, timeout=60)
    if proc.returncode:
        raise AssertionError(f"{what} failed ({proc.returncode}): {proc.stderr[-400:]}")
    return proc.stdout


def probe_media(ffmpeg: str, probe: str, output: Path, kind: str) -> dict:
    content = run([probe, "-v", "error", "-show_entries",
                   "format=format_name,duration:stream=codec_type,codec_name,width,height,duration,duration_ts",
                   "-of", "json", "-i", str(output)], what=f"independent probe of {output.name}")
    info = json.loads(content)
    found = {s["codec_type"] for s in info.get("streams", [])}
    assert kind in found, (output.name, info)
    duration = float(info["format"].get("duration", 0) or 0)
    if duration <= 0:
        duration = max((float(stream.get("duration", 0) or 0) for stream in info["streams"]), default=0)
    if duration <= 0 and "audio" in found:
        # Streamed FFmpeg FLAC writes no final total-samples count to its
        # unseekable output, so FFprobe cannot infer a duration from metadata.
        # Independently decode PCM to prove that the output contains samples.
        decoded = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
             "-i", str(output), "-map", "0:a:0", "-ar", "22050", "-ac", "1",
             "-f", "s16le", "pipe:1"],
            capture_output=True, check=False, timeout=60,
        )
        assert decoded.returncode == 0 and len(decoded.stdout) > 1000, (output.name, decoded.stderr[-300:])
        duration = len(decoded.stdout) / (22050 * 2)
    assert duration > 0, (output.name, info)
    return {"streams": sorted(found), "duration": round(duration, 3)}


def main() -> None:
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    tools = media_tools()
    if tools is None:
        raise SystemExit("Stage4 audiovisual matrix BLOCKED: separately installed FFmpeg/FFprobe missing.")
    ffmpeg, ffprobe = tools
    results: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="stage4-fixtures-", dir=REPORT.parent) as dirname:
        root = Path(dirname)
        sources = root / "sources"
        outputs = root / "results"
        sources.mkdir()
        outputs.mkdir()
        image = Image.new("RGB", (79, 53), (70, 110, 215))
        for ext, name in (("png", "PNG"), ("jpg", "JPEG"), ("webp", "WEBP")):
            image.save(sources / f"imagine \u0219i \u6771\u4eac.{ext}", format=name)

        wav = sources / "c\u00e2ntec \u6771\u4eac.wav"
        with wave.open(str(wav), "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(22050)
            writer.writeframes(b"\x00\x00" * 22050)

        for target in ("mp3", "flac"):
            run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                 "-i", str(wav), str(sources / f"sound-{target}.{target}")],
                what=f"audio source fixture {target}")

        mp4 = sources / "sample video.mp4"
        run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
             "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=10",
             "-f", "lavfi", "-i", "sine=frequency=600:sample_rate=22050",
             "-t", "1.5", "-c:v", "mpeg4", "-q:v", "6", "-c:a", "aac",
             "-b:a", "64k", "-shortest", str(mp4)], what="video source fixture")
        webm = sources / "sample video.webm"
        run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
             "-i", str(mp4), "-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8",
             "-b:v", "350k", "-c:a", "libopus", str(webm)], what="WebM source fixture")

        all_cases: list[tuple[Path, str, str]] = []
        for ext in ("png", "jpg", "webp"):
            source = sources / f"imagine \u0219i \u6771\u4eac.{ext}"
            all_cases.extend((source, t, "image") for t in ("png", "jpg", "webp") if t != ext)
        for source in (wav, sources / "sound-mp3.mp3", sources / "sound-flac.flac"):
            fmt = source.suffix[1:]
            all_cases.extend((source, t, "audio") for t in ("mp3", "wav", "flac") if t != fmt)
        all_cases.extend(((mp4, "webm", "video"), (webm, "mp4", "video")))
        assert len(all_cases) == 14

        for source, target, kind in all_cases:
            before = sha256(source)
            info = inspect_source(source)
            assert info is not None, f"Source sniff failed: {source.name}"
            assert target in supported_targets(info), f"Route absent: {source.name} -> {target}"
            output = convert_file(source, target, outputs)
            assert output.is_file() and output.stat().st_size, output
            assert output.suffix.lower() == "." + target
            assert sha256(source) == before, f"Source bytes changed: {source.name}"
            if kind == "image":
                with Image.open(output) as converted:
                    converted.verify()
                with Image.open(output) as converted:
                    assert converted.size == (79, 53)
                details = {"dimensions": [79, 53]}
            else:
                details = probe_media(ffmpeg, ffprobe, output, kind)
            results.append({"source": source.name, "target": target, "kind": kind,
                            "status": "PASS", "input_sha256": before,
                            "result": output.name, "bytes": output.stat().st_size, **details})

        # Verify collision-safe no-replace semantics using an actual existing output.
        test_source = sources / "imagine \u0219i \u6771\u4eac.png"
        first = next(item for item in results if item["source"] == test_source.name and item["target"] == "webp")
        old_output = outputs / first["result"]
        old_hash = sha256(old_output)
        second = convert_file(test_source, "webp", outputs)
        assert old_output != second and old_hash == sha256(old_output)
        assert second.name.startswith(test_source.stem + " (") and second.suffix == ".webp", second.name
        assert not any(p.name.startswith(".rightconvert-stage-") for p in outputs.iterdir())

    document = {
        "test": "RightConvert Stage4 directed format matrix",
        "platform": "Windows 11 host; run on other OS explicitly to extend coverage",
        "ffmpeg": str(Path(ffmpeg).name),
        "ffprobe": str(Path(ffprobe).name),
        "cases_passed": len(results),
        "cases_expected": 14,
        "collision_passed": True,
        "original_hashes_checked": True,
        "probes": "Pillow decoder and external FFprobe",
        "results": results,
    }
    payload = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    REPORT.write_text(payload, encoding="utf-8")
    PACKAGED_EVIDENCE.write_text(payload, encoding="utf-8")
    print(f"PASS {len(results)}/14 directed routes, SHA-256 originals, independent decoders, output collisions.")
    print(f"Report: {REPORT}")
    print(f"Packaged evidence: {PACKAGED_EVIDENCE}")


if __name__ == "__main__":
    main()
