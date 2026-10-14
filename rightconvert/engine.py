"""Offline, original-preserving image/audio/video/document conversion engine.

API: ``get_targets(path)`` and ``convert_file(path, target, output_dir, ...)``.
Requires Pillow for images and FFmpeg + FFprobe for media (see formats.py).
Outputs are encoded through held file descriptors in private staging directories,
then published without replacing existing files. The backend never reopens its
output by a mutable pathname.
"""

from __future__ import annotations

import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import warnings
import zipfile
from pathlib import Path
from typing import Callable

from .formats import (
    ConversionCancelled,
    ConversionError,
    SourceInfo,
    UnsupportedFormatError,
    available_encoders,
    chosen_codecs,
    image_dimensions_safe,
    inspect_source,
    media_tools,
    normalize_target,
    supported_targets,
)

try:
    from PIL import Image
except ImportError:
    Image = None  # type: ignore[assignment]


__all__ = [
    "get_targets",
    "convert_file",
    "combine_images_to_pdf",
    "ConversionError",
    "ConversionCancelled",
    "UnsupportedFormatError",
]


def get_targets(source: Path, ffmpeg_path: str | None = None,
                office_path: str | None = None) -> list[str]:
    """Return only valid, installed conversions for the file's actual type.

    Does not trust filename extensions. Returns an empty list for corrupt,
    unsupported, or inaccessible files. Output names are lowercase extensions.
    """
    try:
        path = Path(source).expanduser().resolve()
        info = inspect_source(path, ffmpeg_path)
        return supported_targets(info, ffmpeg_path, office_path) if info else []
    except (OSError, ValueError, TypeError):
        return []


def _check_cancel(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise ConversionCancelled("Conversion cancelled")


def _report(progress: Callable[[float], None] | None, fraction: float) -> None:
    if progress is not None:
        progress(min(1.0, max(0.0, fraction)))


def _same_file_identity(
    path: Path, identity: os.stat_result, *, require_single_link: bool = True
) -> bool:
    """Reject a substituted temporary pathname or unexpected new hard links."""
    try:
        actual = path.lstat()
    except OSError:
        return False
    return (
        actual.st_dev == identity.st_dev
        and actual.st_ino == identity.st_ino
        and (not require_single_link or actual.st_nlink == 1)
        and not path.is_symlink()
    )


def _create_private_staging(output_dir: Path) -> Path:
    """Create user-only staging; retry collisions, fail immediately on ACL denial.

    CPython 3.12 tempfile.mkdtemp retries PermissionError on Windows for an
    ACL-denied parent due to a misleading os.access() result (long CPU hang).
    """
    from uuid import uuid4

    # A chosen output folder can be renamed/replaced after the initial
    # preflight (including from a concurrent local process). Do not create
    # even a temporary file in a newly substituted symlink/junction target.
    if _safe_output_directory(output_dir) != output_dir:
        raise ConversionError("Output directory changed before staging")

    for _ in range(10):
        directory = output_dir / f".rightconvert-stage-{uuid4().hex}"
        try:
            os.mkdir(directory, 0o700)
        except FileExistsError:
            continue
        return directory
    raise ConversionError("Could not allocate a private staging directory")


def _safe_output_directory(folder: Path) -> Path:
    """Reject redirections through symlinks, junctions or other reparse points.

    Resolve only *after* checking the caller-requested directory and its
    ancestors, because Path.resolve silently follows a redirect and would
    otherwise publish outside the destination the user actually chose.
    """
    try:
        candidate = Path(os.path.abspath(os.path.expanduser(os.fspath(folder))))
        for node in (*reversed(candidate.parents), candidate):
            try:
                attrs = node.lstat()
            except FileNotFoundError:
                continue
            if node.is_symlink() or (getattr(attrs, "st_file_attributes", 0) & 0x400):
                raise ConversionError("Output folder cannot contain a shortcut, junction or reparse point")
            if hasattr(node, "is_junction") and node.is_junction():
                raise ConversionError("Output folder cannot contain a junction")
        candidate.mkdir(parents=True, exist_ok=True)
        actual = candidate.resolve(strict=True)
        if os.path.normcase(str(actual)) != os.path.normcase(str(candidate)):
            raise ConversionError("Output folder was redirected while preparing the conversion")
        # Recheck after mkdir to reject a reparse point created during setup.
        for node in (*reversed(candidate.parents), candidate):
            attrs = node.lstat()
            if node.is_symlink() or (getattr(attrs, "st_file_attributes", 0) & 0x400):
                raise ConversionError("Output folder was redirected while preparing the conversion")
        return actual
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ConversionError(f"Output folder is inaccessible: {exc}") from exc


def _win_stage_file(staging: Path, suffix: str) -> tuple[int, str]:
    """Create a staged file with a held DELETE-capable, write-exclusive handle."""
    import ctypes
    import msvcrt
    from ctypes import wintypes
    from uuid import uuid4

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel32.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    invalid = ctypes.c_void_p(-1).value
    # Explicit share-read only: other processes cannot open this file for
    # WRITE or DELETE while we hold it. Renaming uses this same handle.
    read_write_delete = 0x80000000 | 0x40000000 | 0x00010000
    share_read = 0x00000001
    create_new = 1
    normal = 0x00000080
    for _ in range(10):
        path = staging / f"part-{uuid4().hex}{suffix}"
        raw_handle = create(str(path), read_write_delete, share_read, None, create_new, normal, None)
        if raw_handle == invalid:
            code = ctypes.get_last_error()
            if code in (80, 183):
                continue
            raise OSError(code, ctypes.FormatError(code), str(path))
        try:
            fd = msvcrt.open_osfhandle(raw_handle, os.O_RDWR | os.O_BINARY)
        except Exception:
            kernel32.CloseHandle(raw_handle)
            raise
        return fd, str(path)
    raise ConversionError("Could not create a secure staging file")


def _win_set_info(fd: int, information_class: int, payload) -> None:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    operation = kernel32.SetFileInformationByHandle
    operation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    operation.restype = wintypes.BOOL
    if not operation(msvcrt.get_osfhandle(fd), information_class, ctypes.byref(payload), ctypes.sizeof(payload)):
        code = ctypes.get_last_error()
        if code in (80, 183):
            raise FileExistsError(code, ctypes.FormatError(code))
        raise OSError(code, ctypes.FormatError(code))


def _win_rename_by_handle(fd: int, target: Path) -> None:
    """Windows FileRenameInfo: move held source handle, never replace target."""
    import ctypes
    from ctypes import wintypes

    class RenameInfo(ctypes.Structure):
        _fields_ = [
            ("ReplaceIfExists", wintypes.BOOL),
            ("RootDirectory", wintypes.HANDLE),
            ("FileNameLength", wintypes.DWORD),
            ("FileName", wintypes.WCHAR * 1),
        ]

    name_bytes = str(target).encode("utf-16-le")
    # Ensure enough room after the actual FileName offset for full UTF-16.
    payload = ctypes.create_string_buffer(RenameInfo.FileName.offset + len(name_bytes) + 2)
    info = ctypes.cast(payload, ctypes.POINTER(RenameInfo)).contents
    info.ReplaceIfExists = 0
    info.RootDirectory = None
    info.FileNameLength = len(name_bytes)
    ctypes.memmove(ctypes.addressof(payload) + RenameInfo.FileName.offset, name_bytes, len(name_bytes))
    _win_set_info(fd, 3, payload)  # FileRenameInfo, supported by Windows 10/11


def _win_delete_by_handle(fd: int) -> None:
    import ctypes
    from ctypes import wintypes

    class DispositionInfo(ctypes.Structure):
        _fields_ = [("DeleteFile", wintypes.BOOL)]

    data = DispositionInfo(1)
    _win_set_info(fd, 4, data)  # FileDispositionInfo: delete held file at close.


def _link_by_handle(fd: int, target: Path) -> None:
    """POSIX Linux handle-based no-replace publication, without source path."""
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    linkat = libc.linkat
    linkat.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
    linkat.restype = ctypes.c_int
    # AT_EMPTY_PATH links the held original file, not a swapped pathname.
    # No fallback to pathname-based rename/link if unsupported: fail closed.
    if linkat(fd, b"", -100, os.fsencode(target), 0x1000) != 0:
        code = ctypes.get_errno()
        if code == 17:
            raise FileExistsError(code, os.strerror(code), str(target))
        raise OSError(code, os.strerror(code), str(target))


def _publish_output(
    fd: int,
    temporary: Path,
    source: Path,
    output_dir: Path,
    target: str,
    cancel: threading.Event | None,
    identity: os.stat_result,
    destination_identity: os.stat_result | None = None,
) -> Path:
    """Atomically publish the held source inode, never by a mutable filepath."""
    base = source.stem
    for count in range(100000):
        _check_cancel(cancel)
        # Revalidate before each OS-level no-replace commit. In particular,
        # never follow an output-directory symlink inserted mid-conversion.
        if _safe_output_directory(output_dir) != output_dir:
            raise ConversionError("Output directory changed during conversion")
        if destination_identity is not None:
            current_dir = output_dir.lstat()
            if (current_dir.st_dev != destination_identity.st_dev
                    or current_dir.st_ino != destination_identity.st_ino):
                raise ConversionError("Output directory identity changed during conversion")
        # Any substituted original pathname is an attack; the OS move/link
        # below targets our held FD, regardless of subsequent path changes.
        if not _same_file_identity(temporary, identity):
            raise ConversionError("Temporary output was replaced during conversion")
        suffix = "" if count == 0 else f" ({count})"
        if target == "pngzip":
            candidate = output_dir / f"{base}-pages{suffix}.zip"
        else:
            candidate = output_dir / f"{base}{suffix}.{target}"
        try:
            if os.name == "nt":
                _win_rename_by_handle(fd, candidate)
            else:
                _link_by_handle(fd, candidate)
        except FileExistsError:
            continue
        else:
            return candidate
    raise ConversionError("No free output name could be allocated")


def _convert_image(source: Path, target: str, temporary, cancel: threading.Event | None) -> None:
    if Image is None:
        raise ConversionError("Pillow is required for image conversions")
    _check_cancel(cancel)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(source) as original:
                if not image_dimensions_safe(original.size):
                    raise UnsupportedFormatError("Image dimensions exceed safe processing limits")
                # EXIF transpose gives expected orientation and avoids copying
                # stale orientation tags after pixels have been rotated.
                from PIL import ImageOps

                image = ImageOps.exif_transpose(original)
                _check_cancel(cancel)
                if target == "jpg":
                    if image.mode != "RGB":
                        if image.mode in ("RGBA", "LA") or "transparency" in image.info:
                            rgba = image.convert("RGBA")
                            canvas = Image.new("RGB", rgba.size, (255, 255, 255))
                            canvas.paste(rgba, mask=rgba.getchannel("A"))
                            image = canvas
                        else:
                            image = image.convert("RGB")
                    image.save(temporary, format="JPEG", quality=92, optimize=True)
                elif target == "png":
                    if image.mode not in ("1", "L", "LA", "P", "RGB", "RGBA", "I", "I;16"):
                        image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
                    image.save(temporary, format="PNG")
                elif target == "webp":
                    if image.mode not in ("RGB", "RGBA"):
                        image = image.convert("RGBA" if "A" in image.getbands() or "transparency" in image.info else "RGB")
                    image.save(temporary, format="WEBP", quality=90, method=4)
                elif target == "bmp":
                    # BMP alpha channels are inconsistently interpreted by
                    # Windows applications. Do not silently discard one.
                    if "A" in image.getbands() or "transparency" in image.info:
                        raise UnsupportedFormatError("BMP output cannot preserve image transparency")
                    if image.mode not in ("1", "L", "P", "RGB"):
                        raise UnsupportedFormatError("BMP output cannot preserve this image bit depth")
                    image.save(temporary, format="BMP")
                elif target == "tiff":
                    # Single-page, lossless TIFF. Preserve an embedded color
                    # profile where Pillow exposes one.
                    if image.mode not in ("1", "L", "LA", "P", "RGB", "RGBA", "CMYK", "I;16"):
                        raise UnsupportedFormatError("Unsupported TIFF pixel mode")
                    if image.mode == "P" and "transparency" in image.info:
                        image = image.convert("RGBA")
                    params = {"compression": "raw"}
                    if original.info.get("icc_profile"):
                        params["icc_profile"] = original.info["icc_profile"]
                    image.save(temporary, format="TIFF", **params)
                else:
                    raise UnsupportedFormatError(f"Unsupported image output: {target}")
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ConversionError(f"Image conversion failed: {exc}") from exc
    _check_cancel(cancel)


_MAX_DOCUMENT_RESULT = 512 * 1024 * 1024


def _copy_document_result(path: Path, private_root: Path, output_stream,
                          cancel: threading.Event | None) -> None:
    """Copy a verified, job-private result into our still-held output FD.

    An external Office application cannot be given the destination handle as
    an --outdir. Instead it writes ONLY inside a separate private workspace;
    no path produced there is ever treated as a publishable user destination.
    """
    try:
        child = path.resolve(strict=True)
        root = private_root.resolve(strict=True)
        if root not in child.parents or path.is_symlink():
            raise ConversionError("Document provider returned an unsafe output path")
        before = path.lstat()
        if before.st_nlink != 1 or not child.is_file():
            raise ConversionError("Document provider output is not a private regular file")
        with child.open("rb") as source_stream:
            opened = os.fstat(source_stream.fileno())
            if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                raise ConversionError("Document provider output changed before reading")
            total = 0
            while True:
                _check_cancel(cancel)
                block = source_stream.read(1024 * 1024)
                if not block:
                    break
                total += len(block)
                if total > _MAX_DOCUMENT_RESULT:
                    raise ConversionError("Document output exceeds safe size limit")
                output_stream.write(block)
            if total == 0:
                raise ConversionError("Document provider returned an empty file")
    except OSError as exc:
        raise ConversionError(f"Could not copy a private document result: {exc}") from exc


def _convert_document(source: Path, target: str, output_stream,
                      info: SourceInfo, staging: Path,
                      cancel: threading.Event | None,
                      progress: Callable[[float], None] | None,
                      office_path: str | None) -> None:
    if info.kind == "image" and target == "pdf":
        from .pdf_provider import image_to_pdf
        image_to_pdf(source, output_stream, cancel=cancel)
        return

    # LibreOffice's command/profile path handling on Windows is not uniformly
    # long-path aware. A nested output staging directory under a long user
    # destination can make it exit without a PDF and stall for 120 seconds.
    # Put ONLY its isolated disposable job files in a short private TEMP folder;
    # publication still copies into the held staging FD at the user destination.
    if info.kind == "office":
        scratch_parent = _safe_output_directory(Path(tempfile.gettempdir()))
        workspace = Path(tempfile.mkdtemp(prefix="rc-", dir=scratch_parent))
        if os.name == "nt" and len(str(workspace)) > 140:
            workspace.rmdir()
            raise ConversionError("LibreOffice temporary path is too long; configure a shorter TEMP directory")
    else:
        workspace = _create_private_staging(staging)
    identity = workspace.lstat()
    try:
        if info.kind == "office" and target == "pdf":
            from .office_provider import (
                cleanup_office_output, convert_office_to_pdf, find_libreoffice,
            )
            executable = find_libreoffice(office_path)
            if not executable:
                raise UnsupportedFormatError("LibreOffice is not installed or configured")
            result = convert_office_to_pdf(
                source, workspace, executable, cancel=cancel, progress=progress,
            )
            _check_cancel(cancel)
            _copy_document_result(result, workspace, output_stream, cancel)
            _check_cancel(cancel)
            # LibreOffice writes a nested private user/profile tree. Remove
            # it using the provider's identity-scoped Windows long-path
            # cleanup while our held output stream is still unpublished.
            # A failure here fails closed and never publishes half a job.
            cleanup_office_output(result, workspace)
            return

        if info.kind != "pdf":
            raise UnsupportedFormatError("Unsupported document conversion")
        from .pdf_provider import render_pdf_pages
        rendered_format = target if target in ("jpg", "png", "webp") else "png"
        pages = render_pdf_pages(
            source, workspace, fmt=rendered_format, cancel=cancel, progress=progress,
        )
        if len(pages) != info.pages:
            raise ConversionError("PDF page count changed during conversion")
        if target == "pngzip":
            # The archive is one protected output; interrupted multi-page jobs
            # cannot leave half a dozen already-published result images behind.
            with zipfile.ZipFile(output_stream, mode="w", compression=zipfile.ZIP_STORED,
                                 allowZip64=True) as archive:
                for index, page in enumerate(pages, 1):
                    _check_cancel(cancel)
                    # Recheck membership before accepting each page's bytes.
                    with archive.open(f"page-{index:04d}.png", "w") as zip_stream:
                        _copy_document_result(page, workspace, zip_stream, cancel)
            return
        if target == "tiff":
            if Image is None:
                raise ConversionError("Pillow is required for TIFF output")
            opened = []
            try:
                for page in pages:
                    _check_cancel(cancel)
                    resolved = page.resolve(strict=True)
                    if workspace.resolve(strict=True) not in resolved.parents or page.is_symlink():
                        raise ConversionError("Unexpected renderer page path")
                    with Image.open(page) as img:
                        opened.append(img.copy())
                _check_cancel(cancel)
                opened[0].save(
                    output_stream, format="TIFF", save_all=True,
                    append_images=opened[1:], compression="tiff_deflate",
                )
            finally:
                for img in opened:
                    img.close()
            return
        if len(pages) != 1:
            raise UnsupportedFormatError("Select PNG pages ZIP or multipage TIFF to preserve all PDF pages")
        _copy_document_result(pages[0], workspace, output_stream, cancel)
    finally:
        # Remove only a directory created inside OUR held-output staging
        # directory, after confirming its original identity. This directory is
        # not a user-selected output path or any arbitrary external location.
        try:
            if _same_file_identity(workspace, identity, require_single_link=False):
                shutil.rmtree(workspace)
        except OSError:
            # Fail closed on unexpected interference; never remove a renamed
            # directory outside the owned staging hierarchy.
            pass


def _media_args(info: SourceInfo, target: str, encoders: frozenset[str] | set[str]) -> list[str]:
    codecs = chosen_codecs(info, target, encoders)
    if codecs is None:
        raise UnsupportedFormatError(f"No installed encoder supports {target.upper()} output")
    primary, audio = codecs

    if info.kind == "audio":
        args = ["-map", "0:a:0", "-vn", "-sn", "-dn", "-c:a", primary]
        if target == "mp3":
            args += ["-q:a", "2"] if primary == "libmp3lame" else ["-b:a", "192k"]
        elif target == "flac":
            args += ["-compression_level", "5"]
        return args

    # Preserve every video and audio track across video containers. Only
    # supported streams are offered by formats.chosen_codecs().
    args = ["-map", "0:v"]
    if info.has_audio:
        args += ["-map", "0:a?"]
    args += ["-sn", "-dn", "-c:v", primary]
    if target in ("mp4", "mkv"):
        if primary == "libx264":
            args += ["-preset", "medium", "-crf", "23", "-pix_fmt", "yuv420p"]
        else:
            args += ["-q:v", "4", "-pix_fmt", "yuv420p"]
        # h264 / mpeg4 require even dimensions in common pixel formats.
        args += ["-vf", "scale=ceil(iw/2)*2:ceil(ih/2)*2"]
    else:
        if primary == "libvpx-vp9":
            args += ["-deadline", "good", "-cpu-used", "4", "-crf", "32", "-b:v", "0"]
        else:
            args += ["-deadline", "good", "-cpu-used", "4", "-b:v", "1M"]
    if audio:
        args += ["-c:a", audio, "-b:a", "160k" if target == "mp4" else "128k"]
    else:
        args += ["-an"]
    return args


_TIME_RE = re.compile(r"^(\d+):(\d+):(\d+(?:\.\d+)?)$")


def _progress_seconds(line: str) -> float | None:
    key, sep, value = line.partition("=")
    if not sep:
        return None
    if key in ("out_time_us", "out_time_ms"):
        try:
            return max(0.0, int(value) / 1_000_000)
        except ValueError:
            return None
    if key == "out_time":
        match = _TIME_RE.fullmatch(value.strip())
        if match:
            return int(match[1]) * 3600 + int(match[2]) * 60 + float(match[3])
    return None


def _stop_process(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=3)


def _finalize_streamed_wav(temporary) -> None:
    """Repair FFmpeg's unknown RIFF/data lengths on our held seekable output.

    FFmpeg writes 0xffffffff for both lengths when sending WAV to stdout.
    Its payload is fine, but strict RIFF decoders treat the result as corrupt.
    Rewrite only the bytes on the SAME open staging file handle, after FFmpeg
    exits and before the guarded no-replace publication. WAV >4 GiB requires
    RF64, which we explicitly do not advertise or silently truncate.
    """
    temporary.flush()
    temporary.seek(0, os.SEEK_END)
    total = temporary.tell()
    if total < 44 or total - 8 >= 0xFFFFFFFF:
        raise ConversionError("WAV output size is invalid or exceeds the RIFF 4 GiB limit")
    temporary.seek(0)
    header = temporary.read(12)
    if header[:4] != b"RIFF" or header[8:12] != b"WAVE":
        raise ConversionError("FFmpeg produced an invalid WAV RIFF header")

    pos = 12
    data_position = None
    block_align = None
    fact_position = None
    while pos < min(total - 8, 1024 * 1024):
        temporary.seek(pos)
        chunk = temporary.read(8)
        if len(chunk) != 8:
            raise ConversionError("WAV output has a truncated chunk header")
        kind = chunk[:4]
        length = int.from_bytes(chunk[4:], "little")
        if kind == b"data":
            data_position = pos + 8
            break
        if length == 0xFFFFFFFF or pos + 8 + length > total:
            raise ConversionError("WAV output has malformed metadata chunks")
        if kind == b"fmt ":
            if length < 16:
                raise ConversionError("WAV output has no usable PCM format")
            temporary.seek(pos + 8)
            fmt = temporary.read(16)
            block_align = int.from_bytes(fmt[12:14], "little")
            if int.from_bytes(fmt[0:2], "little") not in (1, 0xFFFE) or block_align < 1:
                raise ConversionError("WAV output does not contain supported PCM")
        elif kind == b"fact" and length >= 4:
            fact_position = pos + 8
        pos += 8 + length + (length % 2)
    if data_position is None or block_align is None:
        raise ConversionError("WAV output lacks its PCM format or data chunk")

    audio_bytes = total - data_position
    if audio_bytes <= 0 or audio_bytes >= 0xFFFFFFFF or audio_bytes % block_align:
        raise ConversionError("WAV output has incomplete PCM frames")
    temporary.seek(4)
    temporary.write((total - 8).to_bytes(4, "little"))
    temporary.seek(data_position - 4)
    temporary.write(audio_bytes.to_bytes(4, "little"))
    if fact_position is not None:
        frames = audio_bytes // block_align
        if frames > 0xFFFFFFFF:
            raise ConversionError("WAV sample count exceeds its header limit")
        temporary.seek(fact_position)
        temporary.write(frames.to_bytes(4, "little"))
    temporary.seek(0, os.SEEK_END)
    temporary.flush()


def _convert_media(
    source: Path,
    target: str,
    temporary,
    info: SourceInfo,
    cancel: threading.Event | None,
    progress: Callable[[float], None] | None,
    ffmpeg: str,
) -> None:
    _check_cancel(cancel)
    args = _media_args(info, target, available_encoders(ffmpeg))
    # Encode to the already-open destination handle. FFmpeg never receives
    # the staging filename, so a hostile rename or hardlink cannot redirect
    # its writes to an unrelated file.
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-progress", "pipe:2", "-nostats", "-protocol_whitelist", "file,pipe", "-i", str(source),
        *args,
    ]
    if target == "mp4":
        # MP4 normally requires a seekable muxer. A fragmented MP4 is valid
        # from FFmpeg's non-seekable pipe protocol.
        command += ["-movflags", "+frag_keyframe+empty_moov"]
    muxer = {"mkv": "matroska"}.get(target, target)
    command += ["-f", muxer, "pipe:1"]
    try:
        proc = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=temporary,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as exc:
        raise ConversionError(f"Could not launch FFmpeg: {exc}") from exc

    output_lines: queue.Queue[str] = queue.Queue()

    def read_progress() -> None:
        assert proc.stderr is not None
        try:
            for line in proc.stderr:
                output_lines.put(line.strip())
        finally:
            proc.stderr.close()

    reader = threading.Thread(target=read_progress, daemon=True)
    reader.start()
    errors: list[str] = []
    try:
        while proc.poll() is None or not output_lines.empty() or reader.is_alive():
            _check_cancel(cancel)
            try:
                line = output_lines.get(timeout=0.1)
            except queue.Empty:
                continue
            seconds = _progress_seconds(line)
            if seconds is not None and info.duration > 0:
                _report(progress, min(0.99, seconds / info.duration))
            elif "=" not in line and line and line != "progress=end":
                errors.append(line)
        exit_code = proc.wait()
        _check_cancel(cancel)
        if exit_code != 0:
            raise ConversionError(f"FFmpeg conversion failed ({exit_code}): {' '.join(errors)[-2000:] or 'unknown error'}")
    finally:
        _stop_process(proc)
        reader.join(timeout=3)


def convert_file(
    source: Path,
    target: str,
    output_dir: Path,
    *,
    cancel: threading.Event | None = None,
    progress: Callable[[float], None] | None = None,
    ffmpeg_path: str | None = None,
    office_path: str | None = None,
) -> Path:
    """Convert a file without overwriting the input or an existing output.

    Raises UnsupportedFormatError for invalid/unavailable conversions,
    ConversionCancelled on cancellation, and ConversionError on failures.
    Progress callbacks receive values between 0.0 and 1.0; 1.0 means the
    output was successfully committed. Cancellation of Pillow's synchronous
    save is observed as soon as the save returns, before any output is kept.
    """
    _check_cancel(cancel)
    normalized = normalize_target(target)
    try:
        input_path = Path(source).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ConversionError(f"Input file is inaccessible: {exc}") from exc
    if not input_path.is_file():
        raise UnsupportedFormatError("Input must be a regular file")
    info = inspect_source(input_path, ffmpeg_path)
    if info is None:
        raise UnsupportedFormatError("Unrecognized, corrupt, or unsupported input format")
    available = supported_targets(info, ffmpeg_path, office_path)
    if normalized not in available:
        raise UnsupportedFormatError(f"Cannot convert {info.format.upper()} to {normalized.upper()} with installed tools")

    tools = media_tools(ffmpeg_path) if info.kind in ("audio", "video") else None
    if info.kind in ("audio", "video") and tools is None:
        raise ConversionError("FFmpeg and FFprobe are required for audio/video conversions")

    try:
        destination = _safe_output_directory(output_dir)
        destination_identity = destination.lstat()
    except (OSError, ValueError) as exc:
        raise ConversionError(f"Output directory is inaccessible: {exc}") from exc

    _check_cancel(cancel)
    staging: Path | None = None
    staging_identity: os.stat_result | None = None
    temporary: Path | None = None
    identity: os.stat_result | None = None
    published = False
    try:
        # Python 3.12 handles mode 0700 with a user-only Windows ACL. A custom
        # bounded mkdir avoids tempfile.mkdtemp's Windows ACL denial retry hang.
        staging = _create_private_staging(destination)
        staging_identity = staging.lstat()
        if os.name == "nt":
            fd, temp_name = _win_stage_file(staging, f".tmp.{normalized}")
        else:
            fd, temp_name = tempfile.mkstemp(
                prefix="part-", suffix=f".tmp.{normalized}", dir=staging
            )
        temporary = Path(temp_name)
        with os.fdopen(fd, "w+b") as output_stream:
            try:
                identity = os.fstat(output_stream.fileno())
                _report(progress, 0.0)
                if info.kind == "image" and normalized != "pdf":
                    _convert_image(input_path, normalized, output_stream, cancel)
                elif info.kind in ("image", "pdf", "office"):
                    _convert_document(
                        input_path, normalized, output_stream, info, staging,
                        cancel, progress, office_path,
                    )
                elif info.kind in ("audio", "video"):
                    assert tools is not None
                    _convert_media(input_path, normalized, output_stream, info, cancel, progress, tools[0])
                    if normalized == "wav":
                        _check_cancel(cancel)
                        _finalize_streamed_wav(output_stream)
                else:
                    raise UnsupportedFormatError("No encoder for source type")
                output_stream.flush()
                _check_cancel(cancel)
                if os.fstat(output_stream.fileno()).st_size == 0:
                    raise ConversionError("Conversion produced an empty output")
                output = _publish_output(
                    output_stream.fileno(), temporary, input_path,
                    destination, normalized, cancel, identity, destination_identity,
                )
                published = True
            finally:
                if not published and os.name == "nt":
                    # Windows marks our *held* file for deletion at close;
                    # never unlink the path, which could have been replaced.
                    try:
                        _win_delete_by_handle(output_stream.fileno())
                    except OSError as cleanup_error:
                        if sys.exc_info()[0] is None:
                            raise ConversionError("Could not safely discard temporary output") from cleanup_error
                        # Preserve the original failure; a leaked private
                        # staging file is safer than deleting an unknown path.
        _report(progress, 1.0)
        return output
    except (OSError, ValueError) as exc:
        raise ConversionError(f"Could not write converted file: {exc}") from exc
    finally:
        # Cleanup should never replace the real cancellation or FFmpeg error.
        unwinding = sys.exc_info()[0] is not None
        cleanup_errors: list[OSError] = []
        if os.name != "nt" and temporary is not None and identity is not None and _same_file_identity(
            temporary, identity, require_single_link=False
        ):
            try:
                temporary.unlink(missing_ok=True)
            except OSError as exc:
                cleanup_errors.append(exc)
        if staging is not None and staging_identity is not None:
            try:
                current = staging.lstat()
                if (
                    current.st_dev == staging_identity.st_dev
                    and current.st_ino == staging_identity.st_ino
                    and not staging.is_symlink()
                ):
                    # Never recursively remove attacker-created files.
                    staging.rmdir()
            except OSError:
                # A substituted or nonempty staging directory is left intact
                # to avoid deleting another writer's data.
                pass
        if cleanup_errors and not unwinding:
            raise ConversionError("Could not clean temporary conversion files") from cleanup_errors[0]


def combine_images_to_pdf(
    sources: list[Path], output_dir: Path, *,
    cancel: threading.Event | None = None,
    progress: Callable[[float], None] | None = None,
) -> Path:
    """Combine an ordered group of source images into one multipage PDF.

    This deliberately uses the same held-FD Windows publication and collision
    protection as convert_file; the first input supplies the output basename.
    The order is exactly the caller's order. No existing output is replaced.
    """
    if not isinstance(sources, (list, tuple)) or not 2 <= len(sources) <= 64:
        raise UnsupportedFormatError("Select between 2 and 64 images for a multipage PDF")
    _check_cancel(cancel)
    inputs: list[Path] = []
    identities: set[str] = set()
    for source in sources:
        try:
            path = Path(source).expanduser().resolve(strict=True)
        except (OSError, ValueError, RuntimeError) as exc:
            raise ConversionError(f"Cannot read source image: {exc}") from exc
        key = os.path.normcase(str(path))
        if key in identities or not path.is_file():
            raise UnsupportedFormatError("PDF sources must be distinct regular files")
        info = inspect_source(path)
        if not info or info.kind != "image" or "pdf" not in supported_targets(info):
            raise UnsupportedFormatError("PDF merging requires compatible opaque images")
        inputs.append(path)
        identities.add(key)
    try:
        destination = _safe_output_directory(output_dir)
        destination_identity = destination.lstat()
    except (OSError, ValueError) as exc:
        raise ConversionError(f"Output directory is inaccessible: {exc}") from exc
    staging: Path | None = None
    temporary: Path | None = None
    identity: os.stat_result | None = None
    staging_identity: os.stat_result | None = None
    published = False
    try:
        staging = _create_private_staging(destination)
        staging_identity = staging.lstat()
        if os.name == "nt":
            fd, name = _win_stage_file(staging, ".tmp.pdf")
        else:
            fd, name = tempfile.mkstemp(prefix="part-", suffix=".tmp.pdf", dir=staging)
        temporary = Path(name)
        with os.fdopen(fd, "w+b") as output_stream:
            try:
                identity = os.fstat(output_stream.fileno())
                _report(progress, 0)
                from .pdf_provider import images_to_pdf
                images_to_pdf(inputs, output_stream, cancel=cancel)
                _check_cancel(cancel)
                output_stream.flush()
                if os.fstat(output_stream.fileno()).st_size <= 0:
                    raise ConversionError("Combined PDF was empty")
                published_file = _publish_output(
                    output_stream.fileno(), temporary, inputs[0],
                    destination, "pdf", cancel, identity, destination_identity,
                )
                published = True
            finally:
                if not published and os.name == "nt":
                    try:
                        _win_delete_by_handle(output_stream.fileno())
                    except OSError:
                        if sys.exc_info()[0] is None:
                            raise ConversionError("Unable to discard unfinished PDF")
        _report(progress, 1)
        return published_file
    except (OSError, ValueError) as exc:
        raise ConversionError(f"Could not create combined PDF: {exc}") from exc
    finally:
        if (os.name != "nt" and temporary is not None and identity is not None
                and _same_file_identity(temporary, identity, require_single_link=False)):
            temporary.unlink(missing_ok=True)
        if staging is not None and staging_identity is not None:
            try:
                if _same_file_identity(staging, staging_identity, require_single_link=False):
                    staging.rmdir()
            except OSError:
                pass
