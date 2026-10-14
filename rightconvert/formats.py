"""Format detection and locally available conversion capabilities.

Pillow is required for image support. Audio/video support additionally requires
FFmpeg and FFprobe executables (either on PATH, bundled beside the application,
or supplied to ``convert_file`` as ``ffmpeg_path``). Nothing is uploaded.

File extensions are deliberately not used to identify the input format.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import warnings
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

try:
    from PIL import Image
except ImportError:  # Allow media-only installations to import the module.
    Image = None  # type: ignore[assignment]


IMAGE_TARGETS = ("jpg", "png", "webp", "bmp", "tiff")
AUDIO_TARGETS = ("mp3", "wav", "flac", "ogg", "opus")
VIDEO_TARGETS = ("mp4", "webm", "mkv")
VIDEO_INPUTS = VIDEO_TARGETS + ("mov", "avi")
# PDF pages packed as a ZIP keep the existing single-output conversion API.
# A plain PNG/JPG/WebP output from a multi-page PDF would silently lose pages.
DOCUMENT_TARGETS = ("pdf", "pngzip")
ALL_TARGETS = IMAGE_TARGETS + AUDIO_TARGETS + VIDEO_TARGETS + DOCUMENT_TARGETS

_PIL_INPUTS = {
    "JPEG": "jpg", "PNG": "png", "WEBP": "webp", "BMP": "bmp",
    "TIFF": "tiff", "GIF": "gif",
}
_PIL_OUTPUTS = {"jpg": "JPEG", "png": "PNG", "webp": "WEBP", "bmp": "BMP", "tiff": "TIFF"}
MAX_IMAGE_PIXELS = 60_000_000
MAX_IMAGE_EDGE = 24_000


class ConversionError(RuntimeError):
    """An input, backend, or conversion error prevented producing a file."""


class UnsupportedFormatError(ConversionError, ValueError):
    """The source or requested output format is unsupported."""


class ConversionCancelled(ConversionError):
    """The conversion was cancelled; no output was committed."""


@dataclass(frozen=True)
class SourceInfo:
    kind: str  # image, audio, video, pdf, office
    format: str
    duration: float = 0.0
    has_audio: bool = False
    audio_streams: int = 0
    video_streams: int = 0
    extra_streams: bool = False
    audio_bit_depth: int = 0
    image_has_alpha: bool = False
    image_high_depth: bool = False
    image_mode: str = ""
    pages: int = 1
    image_width: int = 0
    image_height: int = 0


def normalize_target(target: str) -> str:
    if not isinstance(target, str):
        raise UnsupportedFormatError("Output format must be a string")
    normalized = target.strip().lower().lstrip(".")
    if normalized == "jpeg":
        normalized = "jpg"
    if normalized not in ALL_TARGETS:
        raise UnsupportedFormatError(f"Unsupported output format: {target!r}")
    return normalized


def _find_binary(name: str, ffmpeg_path: str | None = None) -> str | None:
    """Find FFmpeg tools without Windows' implicit current-directory search.

    Only an explicitly chosen *absolute* FFmpeg location, a frozen app bundle,
    and absolute PATH directories are eligible. Relative and empty PATH entries
    (including '.') never become implicit references to the current directory.
    """
    if name not in ("ffmpeg", "ffprobe"):
        return None
    extension = ".exe" if os.name == "nt" else ""
    filename = f"{name}{extension}"

    def executable_in(directory: Path) -> str | None:
        candidate = directory / filename
        try:
            if candidate.is_file():
                return str(candidate.resolve(strict=True))
        except OSError:
            pass
        return None

    if ffmpeg_path is not None:
        requested = Path(ffmpeg_path).expanduser()
        if not requested.is_absolute():
            return None
        if requested.is_dir():
            return executable_in(requested)
        if requested.name.casefold() == f"ffmpeg{extension}".casefold() and requested.is_file():
            if name == "ffmpeg":
                return str(requested.resolve(strict=True))
            return executable_in(requested.parent)
        return None

    # The executable directory counts as an app-owned bundle only when the
    # process is actually frozen. Python's interpreter directory is not one.
    if getattr(sys, "frozen", False):
        bundle_dirs = []
        internal_root = getattr(sys, "_MEIPASS", None)
        if internal_root:
            bundle_dirs.append(Path(internal_root))
        bundle_dirs.append(Path(sys.executable).parent)
        for root in bundle_dirs:
            if root.is_absolute():
                for directory in (root, root / "bin"):
                    selected = executable_in(directory)
                    if selected is not None:
                        return selected

    # Never use shutil.which() for an unqualified filename: Windows may
    # implicitly look in the process CWD before inspecting PATH.
    try:
        current_directory = Path.cwd().resolve(strict=True)
    except OSError:
        current_directory = None
    for value in os.environ.get("PATH", "").split(os.pathsep):
        entry = value.strip().strip('"')
        if not entry:
            continue
        directory = Path(entry)
        if not directory.is_absolute():
            continue
        try:
            directory = directory.resolve(strict=True)
            if current_directory is not None and directory == current_directory:
                continue
            selected = executable_in(directory)
            if selected is not None:
                if current_directory is None or Path(selected).parent != current_directory:
                    return selected
        except OSError:
            continue
    return None


def media_tools(ffmpeg_path: str | None = None) -> tuple[str, str] | None:
    ffmpeg = _find_binary("ffmpeg", ffmpeg_path)
    ffprobe = _find_binary("ffprobe", ffmpeg_path)
    if ffmpeg and ffprobe:
        return ffmpeg, ffprobe
    return None


def image_outputs() -> set[str]:
    if Image is None:
        return set()
    Image.init()
    return {
        extension
        for extension, pillow_format in _PIL_OUTPUTS.items()
        if pillow_format in Image.SAVE
    }


def _sniff_image(source: Path) -> SourceInfo | None:
    if Image is None:
        return None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(source) as img:
                detected = _PIL_INPUTS.get(img.format or "")
                if (
                    not detected
                    or not image_dimensions_safe(img.size)
                    or getattr(img, "n_frames", 1) != 1
                    or img.mode not in ("1", "L", "LA", "P", "RGB", "RGBA", "CMYK", "I;16")
                ):
                    # Animated inputs need a separate multi-frame pipeline;
                    # exporting only frame 1 would silently discard content.
                    return None
                img.verify()  # Reject truncated/corrupt headers as far as Pillow can.
                alpha = ("A" in img.getbands()) or ("transparency" in img.info)
                return SourceInfo(
                    "image", detected, image_has_alpha=alpha,
                    image_high_depth=img.mode == "I;16",
                    image_mode=img.mode,
                    image_width=img.width, image_height=img.height,
                )
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        return None


def image_dimensions_safe(size: tuple[int, int]) -> bool:
    width, height = size
    return (
        width > 0 and height > 0
        and width <= MAX_IMAGE_EDGE and height <= MAX_IMAGE_EDGE
        and width * height <= MAX_IMAGE_PIXELS
    )


def _sniff_media_magic(source: Path) -> str | None:
    try:
        with source.open("rb") as stream:
            head = stream.read(4096)
    except OSError:
        return None
    if head.startswith(b"fLaC"):
        return "flac"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[:4] == b"RIFF" and head[8:12] == b"AVI ":
        return "avi"
    if head.startswith(b"OggS") and len(head) >= 27 and head[4] == 0:
        return "ogg"
    if head.startswith(b"ID3") or (
        len(head) > 1
        and head[0] == 0xFF
        and (head[1] & 0xE0) == 0xE0  # MPEG audio frame sync
        and (head[1] & 0x18) != 0x08  # reserved version
        and (head[1] & 0x06) == 0x02  # Layer III
    ):
        return "mp3"
    if len(head) > 12 and head[4:8] == b"ftyp":
        return "mov" if head[8:12] == b"qt  " else "mp4"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        # Restrict EBML to known DocTypes, never guess from .mkv/.webm suffixes.
        if b"matroska" in head[:256]:
            return "mkv"
        if b"webm" in head[:256]:
            return "webm"
    return None


def _probe_media(source: Path, ffprobe: str, expected: str) -> SourceInfo | None:
    try:
        probe = subprocess.run(
            [
                ffprobe,
                "-v", "error",
                "-protocol_whitelist", "file,pipe",
                "-show_entries", "format=format_name,duration:stream=codec_type,codec_name,bits_per_raw_sample,bits_per_sample,sample_fmt:stream_disposition=attached_pic",
                "-of", "json",
                "-i", str(source),
            ],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=20,
            stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
        if probe.returncode != 0:
            return None
        result = json.loads(probe.stdout)
        names = set(result.get("format", {}).get("format_name", "").split(","))
        streams = result.get("streams", [])
        audio = [s for s in streams if s.get("codec_type") == "audio"]
        video = [s for s in streams if s.get("codec_type") == "video"]
        has_audio = bool(audio)
        has_video = bool(video)
        expected_demuxer = {"mkv": "matroska", "ogg": "ogg", "opus": "ogg"}.get(expected, expected)
        if expected_demuxer not in names or (expected in AUDIO_TARGETS and not has_audio):
            return None
        if expected in VIDEO_INPUTS and not has_video:
            return None
        try:
            duration = max(0.0, float(result.get("format", {}).get("duration", 0)))
        except (ValueError, TypeError):
            duration = 0.0
        if expected == "ogg":
            # Both Ogg/Vorbis and Ogg/Opus start with OggS. Use actual codec
            # metadata, not the filename, to distinguish the two formats.
            if len(audio) != 1 or video:
                return None
            if audio[0].get("codec_name") == "opus":
                expected = "opus"
            elif audio[0].get("codec_name") != "vorbis":
                return None
        kind = "video" if expected in VIDEO_INPUTS else "audio"
        depth = 0
        if audio:
            for key in ("bits_per_raw_sample", "bits_per_sample"):
                try:
                    depth = int(audio[0].get(key) or 0)
                except (ValueError, TypeError):
                    depth = 0
                if depth > 0:
                    break
        return SourceInfo(
            kind, expected, duration, has_audio,
            audio_streams=len(audio),
            video_streams=len(video),
            extra_streams=any(
                s.get("codec_type") not in ("audio", "video")
                or bool(s.get("disposition", {}).get("attached_pic"))
                for s in streams
            ),
            audio_bit_depth=depth,
        )
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError, KeyError):
        return None


def inspect_source(source: Path, ffmpeg_path: str | None = None) -> SourceInfo | None:
    """Inspect actual content, never trusting a filename extension."""
    if not source.is_file():
        return None
    image = _sniff_image(source)
    if image:
        return image
    try:
        with source.open("rb") as document_stream:
            header = document_stream.read(8)
    except OSError:
        return None
    if header.startswith(b"%PDF-"):
        try:
            from .pdf_provider import inspect_pdf
            pages = inspect_pdf(source)
            if pages is not None and pages > 0:
                return SourceInfo("pdf", "pdf", pages=pages)
        except (ImportError, OSError, ValueError, RuntimeError):
            pass
        return None
    if header.startswith(b"PK\x03\x04"):
        try:
            from .office_provider import inspect_office
            family = inspect_office(source)
            if family:
                return SourceInfo("office", family)
        except (ImportError, OSError, ValueError, RuntimeError):
            pass
        return None
    expected = _sniff_media_magic(source)
    if not expected:
        return None
    tools = media_tools(ffmpeg_path)
    if not tools:
        return None
    return _probe_media(source, tools[1], expected)


@lru_cache(maxsize=16)
def available_encoders(ffmpeg: str) -> frozenset[str]:
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=15,
            stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return frozenset()
    if result.returncode != 0:
        return frozenset()
    found = set()
    for line in result.stdout.splitlines():
        columns = line.split()
        if (
            len(columns) >= 2
            and len(columns[0]) == 6
            and columns[0][0] in "VAS"
            and all(flag == "." or flag.isupper() for flag in columns[0][1:])
        ):
            found.add(columns[1])
    return frozenset(found)


def chosen_codecs(info: SourceInfo, target: str, encoders: frozenset[str] | set[str]) -> tuple[str, str | None] | None:
    """Choose real encoders. Return (primary codec, optional audio codec)."""
    # Unsupported streams (subtitles, data, attachments) cannot be silently
    # discarded. Audio containers also cannot preserve multiple audio streams,
    # or attached picture/video streams with our supported output muxers.
    if info.extra_streams:
        return None
    if info.kind == "audio":
        if info.audio_streams != 1 or info.video_streams:
            return None
        wav_encoder = (
            "pcm_s32le" if info.audio_bit_depth > 24 else
            "pcm_s24le" if info.audio_bit_depth > 16 else
            "pcm_s16le"
        )
        audio_choices = {
            "mp3": ("libmp3lame", "libshine"),
            "wav": (wav_encoder,),
            "flac": ("flac",),
            "ogg": ("libvorbis",),
            "opus": ("libopus",),
        }
        return next(((c, None) for c in audio_choices.get(target, ()) if c in encoders), None)
    if info.kind != "video":
        return None
    if not info.video_streams:
        return None
    if target in ("mp4", "mkv"):
        video = next((c for c in ("libx264", "mpeg4") if c in encoders), None)
        audio = "aac" if "aac" in encoders else None
    elif target == "webm":
        video = next((c for c in ("libvpx-vp9", "libvpx") if c in encoders), None)
        audio = next((c for c in ("libopus", "libvorbis") if c in encoders), None)
    else:
        return None
    if not video or (info.has_audio and not audio):
        return None
    return video, audio if info.has_audio else None


def supported_targets(info: SourceInfo, ffmpeg_path: str | None = None,
                      office_path: str | None = None) -> list[str]:
    if info.kind == "image":
        # BMP cannot reliably retain alpha; avoid advertising destructive
        # alpha-flattening as a BMP conversion. 16-bit images only go to
        # targets that preserve their channel depth losslessly.
        targets = [
            target for target in IMAGE_TARGETS
            if target != info.format and target in image_outputs()
            and not (target == "bmp" and (
                info.image_has_alpha or info.image_high_depth
                or info.image_mode not in ("1", "L", "P", "RGB")
            ))
            and not (info.image_high_depth and target not in ("png", "tiff"))
        ]
        # Pillow's PDF writer is validated for opaque, conventional 8-bit
        # images only. Exclude alpha and high-depth to prevent silent loss.
        if (Image is not None and "PDF" in Image.registered_extensions().values()
                and not info.image_has_alpha and not info.image_high_depth
                and info.image_mode in ("1", "L", "RGB", "CMYK")
                and 0 < info.image_width <= 12000
                and 0 < info.image_height <= 12000
                and info.image_width * info.image_height <= 25_000_000):
            targets.append("pdf")
        return targets
    if info.kind == "pdf":
        # Only offer routes when the optional real PDFium rasterizer parsed
        # the input. One page may render directly; multipage must remain whole.
        if info.pages == 1:
            return ["png", "jpg", "webp", "tiff", "pngzip"]
        return ["tiff", "pngzip"]
    if info.kind == "office":
        try:
            from .office_provider import find_libreoffice
            return ["pdf"] if find_libreoffice(office_path) else []
        except (ImportError, OSError, ValueError, RuntimeError):
            return []
    tools = media_tools(ffmpeg_path)
    if not tools:
        return []
    encoders = available_encoders(tools[0])
    targets = AUDIO_TARGETS if info.kind == "audio" else VIDEO_TARGETS
    return [
        target for target in targets
        if target != info.format and chosen_codecs(info, target, encoders)
    ]
