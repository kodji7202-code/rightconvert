"""Optional, offline LibreOffice-to-PDF adapter.

This module deliberately does not advertise formats or publish user files.
The caller owns ``output_dir`` as a private scratch directory and must copy
the returned verified PDF into its separately guarded publication mechanism.
No LibreOffice installation is required for importing or sniffing documents.
"""

from __future__ import annotations

import os
import posixpath
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

from .formats import ConversionCancelled, ConversionError, UnsupportedFormatError

__all__ = [
    "inspect_office",
    "find_libreoffice",
    "convert_office_to_pdf",
    "cleanup_office_output",
]

_MAX_ZIP_SIZE = 128 * 1024 * 1024
_MAX_ENTRIES = 512
_MAX_ENTRY = 64 * 1024 * 1024
_MAX_UNCOMPRESSED = 256 * 1024 * 1024
_MAX_XML_SIZE = 12 * 1024 * 1024
_MAX_RATIO = 200
_MAX_PDF = 512 * 1024 * 1024
_MIMETYPES = {
    "application/vnd.oasis.opendocument.text": "odt",
    "application/vnd.oasis.opendocument.spreadsheet": "ods",
    "application/vnd.oasis.opendocument.presentation": "odp",
}
_OOXML = {
    "docx": ("word/document.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"),
    "xlsx": ("xl/workbook.xml", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"),
    "pptx": ("ppt/presentation.xml", "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"),
}
_ROOTS = {
    "docx": ("document", "wordprocessingml"),
    "xlsx": ("workbook", "spreadsheetml"),
    "pptx": ("presentation", "presentationml"),
}
_CT = "{http://schemas.openxmlformats.org/package/2006/content-types}"
_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_OFFICE = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"
_MANIFEST = "{urn:oasis:names:tc:opendocument:xmlns:manifest:1.0}"
_XLINK = "{http://www.w3.org/1999/xlink}"
_REL_PREFIXES = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/",
    "http://purl.oclc.org/ooxml/officeDocument/relationships/",
    "http://schemas.openxmlformats.org/package/2006/relationships/",
)
_BAD_NAME = re.compile(r"(^|/)(?:basic|scripts|macros|activex|embeddings|externallinks|connections|queries|querytables|pivotcache|webextensions|dde|oleobject|vba|_xmlsignatures)(?:/|\.|$)", re.I)
_BAD_COMPONENT = re.compile(r"(?:vba|macro|activex|oleobject|embedded|externallink|dde|rtd|script|hyperlink|attachedtemplate|altchunk|webextension|querytable)", re.I)
_BAD_FIELD = re.compile(r"\b(?:DDE|DDEAUTO|INCLUDETEXT|INCLUDEPICTURE|LINK|HYPERLINK|DATABASE|RD|RTD)\b", re.I)
_PDF_TAIL = re.compile(rb"startxref\s+(\d+)\s+%%EOF(?:\s*)$")


def _reject(message: str) -> None:
    raise UnsupportedFormatError(message)


def _check_cancel(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise ConversionCancelled("Office conversion cancelled")


def _zip_name(name: str) -> str:
    if (not name or "\\" in name or "\x00" in name or ":" in name
            or name.startswith("/") or any(part in ("", ".", "..") for part in name.strip("/").split("/"))):
        _reject("Office archive contains an unsafe entry path")
    return name.rstrip("/")


def _xml_tree(data: bytes) -> ET.Element:
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper() or b"\x00" in data:
        _reject("Office XML contains a forbidden declaration")
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise UnsupportedFormatError("Office archive contains invalid XML") from exc


def _safe_reference(target: str, base: str, members: set[str]) -> None:
    if (not target or "\\" in target or "\x00" in target or "?" in target
            or "#" in target or "%" in target or target.startswith("//")):
        _reject("Office document references an unsafe target")
    if urlsplit(target).scheme or ":" in target:
        _reject("Office document references an external resource")
    # OOXML internal pack-relative relationships may start at the OPC package
    # root (e.g. Target="/xl/workbook.xml" emitted by openpyxl). This is NOT a
    # filesystem absolute path. Match an actual ZIP member and reject any
    # noncanonical traversal, percent escaping, remote URI or missing member.
    if target.startswith("/"):
        parts = target[1:].split("/")
        if not parts or any(part in ("", ".", "..") for part in parts):
            _reject("Office document references an unsafe package part")
        member = target[1:]
    else:
        member = posixpath.normpath(posixpath.join(base, target))
    if member.startswith("../") or member in ("..", ".") or member not in members:
        _reject("Office document references content outside its package")


def _check_xml_elements(tree: ET.Element, *, odf: bool, members: set[str]) -> None:
    for node in tree.iter():
        local = node.tag.rsplit("}", 1)[-1].lower()
        namespace = node.tag.split("}", 1)[0].lstrip("{") if "}" in node.tag else ""
        # Spreadsheet formulas contain free-form expressions in element TEXT,
        # not in an attribute. Some (WEBSERVICE, DDE, external workbooks) may
        # connect to the network when opened. Reject even innocent formulas:
        # a safe allowlist across LO formula dialects is not currently proven.
        if local in ("f", "formula", "definedname", "calculatedcolumnformula", "totalsrowformula", "formula1", "formula2") and namespace in (
            "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
            "http://purl.oclc.org/ooxml/spreadsheetml/main",
        ):
            _reject("Spreadsheet formulas are disabled for safe offline conversion")
        if namespace == "http://www.w3.org/2001/XInclude" or _BAD_COMPONENT.search(local):
            _reject("Office document contains active, linked or embedded content")
        if odf and local in ("a", "object", "object-ole", "plugin", "applet", "binary-data", "scripts", "form", "event-listeners"):
            _reject("ODF links, embedded objects and scripts are not allowed")
        for key, value in node.attrib.items():
            key_local = key.rsplit("}", 1)[-1].lower()
            # DrawingML <a:font script="Jpan"> selects a writing system;
            # SpreadsheetML <cellStyle hidden="0"> is a harmless style flag.
            # The broad active-content keyword scanner previously rejected
            # both because "script" and the substring "dde" in "hidden"
            # coincidentally matched dangerous fields. Whitelist only these
            # exact documented metadata attributes in their safe namespaces.
            safe_font_script = (
                not odf and local == "font" and key_local == "script"
                and namespace in (
                    "http://schemas.openxmlformats.org/drawingml/2006/main",
                    "http://purl.oclc.org/ooxml/drawingml/main",
                )
                and re.fullmatch(r"[A-Za-z]{4}", value) is not None
            )
            safe_hidden_style = (
                not odf and local == "cellstyle" and key_local == "hidden"
                and namespace in (
                    "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
                    "http://purl.oclc.org/ooxml/spreadsheetml/main",
                )
                and value.casefold() in ("0", "1", "true", "false")
            )
            if (not (safe_font_script or safe_hidden_style)
                    and (_BAD_COMPONENT.search(key_local)
                         or key_local in ("src", "data-source"))):
                _reject("Office document has an active-content attribute")
            if key == _XLINK + "href":
                # Only inline raster images stored in the ODF ZIP are allowed.
                if not (odf and local in ("image", "fill-image", "background-image")
                        and value.startswith("Pictures/") and value in members
                        and Path(value).suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".bmp")):
                    _reject("Office document contains a forbidden hyperlink or external image")
            elif key_local in ("href", "url", "target", "src") and ("//" in value or ":" in value or value.startswith(("/", "\\"))):
                _reject("Office document contains an external reference")
            if key_local == "formula" or (odf and key_local in ("condition", "expression")):
                _reject("Spreadsheet formulas are disabled for safe offline conversion")
            if key_local == "instr" and _BAD_FIELD.search(value):
                _reject("Office document contains an unsafe field instruction")
        if local in ("instrtext", "fldsimple") and _BAD_FIELD.search("".join(node.itertext()) + " " + " ".join(node.attrib.values())):
            _reject("Office document contains an external field instruction")


def _archive_contents(archive: zipfile.ZipFile) -> tuple[dict[str, bytes], set[str]]:
    entries = archive.infolist()
    if not entries or len(entries) > _MAX_ENTRIES:
        _reject("Office archive has an unsafe entry count")
    members: set[str] = set()
    folded: set[str] = set()
    sizes = 0
    compressed = 0
    xml_data: dict[str, bytes] = {}
    for info in entries:
        name = _zip_name(info.filename)
        if name.casefold() in folded:
            _reject("Office archive contains duplicate entry names")
        folded.add(name.casefold())
        members.add(name)
        if info.flag_bits & 1 or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            _reject("Office archive uses unsupported compression or encryption")
        if (info.file_size > _MAX_ENTRY or info.compress_size > _MAX_ZIP_SIZE
                or info.file_size > max(2048, info.compress_size * _MAX_RATIO)):
            _reject("Office archive entry exceeds safety limits")
        sizes += info.file_size
        compressed += info.compress_size
        if sizes > _MAX_UNCOMPRESSED or compressed > _MAX_ZIP_SIZE:
            _reject("Office archive exceeds size limits")
        if stat.S_IFMT(info.external_attr >> 16) == stat.S_IFLNK:
            _reject("Office archive contains a symbolic link")
        if _BAD_NAME.search(name) or _BAD_COMPONENT.search(name):
            _reject("Office archive contains macro or embedded content")
        # SVG/VML/HTML can carry active resources yet are not .xml members.
        # Reject those file types even if an OOXML/ODF relationship is missing.
        if name.lower().endswith((".bin", ".exe", ".dll", ".jar", ".js", ".svg", ".svgz", ".vml", ".html", ".htm", ".xhtml", ".mht", ".mhtml")):
            _reject("Office archive contains unsupported active media")
        is_xml = name.lower().endswith((".xml", ".rels"))
        if is_xml and info.file_size > _MAX_XML_SIZE:
            _reject("Office XML exceeds safety limits")
        if name == "mimetype" and info.file_size > 256:
            _reject("ODF MIME declaration is too long")
        if info.is_dir():
            continue
        # Stream and CRC-verify *all* members, including binaries not parsed.
        data = bytearray() if is_xml or name in ("mimetype", "[Content_Types].xml") else None
        actual = 0
        with archive.open(info) as stream:
            while chunk := stream.read(128 * 1024):
                actual += len(chunk)
                if actual > info.file_size or actual > _MAX_ENTRY:
                    _reject("Office archive has a deceptive entry length")
                if data is not None:
                    data.extend(chunk)
        if actual != info.file_size:
            _reject("Office archive entry has a mismatched length")
        if data is not None:
            xml_data[name] = bytes(data)
    return xml_data, members


def _sniff_ooxml(data: dict[str, bytes], members: set[str]) -> str | None:
    if "[Content_Types].xml" not in data or "_rels/.rels" not in data:
        return None
    types = _xml_tree(data["[Content_Types].xml"])
    if types.tag != _CT + "Types":
        return None
    declared: dict[str, str] = {}
    for node in types:
        if node.tag not in (_CT + "Default", _CT + "Override"):
            _reject("Office content-type manifest has unsupported entries")
        content_type = node.get("ContentType", "")
        if not content_type or _BAD_COMPONENT.search(content_type):
            _reject("Office manifest contains unsafe content")
        if node.tag == _CT + "Override":
            part = node.get("PartName", "")
            if not part.startswith("/") or part[1:] not in members:
                _reject("Office manifest names a missing part")
            declared[part[1:]] = content_type
    variants = [kind for kind, (path, mime) in _OOXML.items() if declared.get(path) == mime]
    if len(variants) != 1:
        return None
    kind = variants[0]
    expected = _OOXML[kind][0]
    root = _xml_tree(data[expected])
    expected_local, family = _ROOTS[kind]
    if root.tag not in (
        f"{{http://schemas.openxmlformats.org/{family}/2006/main}}{expected_local}",
        f"{{http://purl.oclc.org/ooxml/{family}/main}}{expected_local}",
    ):
        return None
    if any(path in members for key, (path, _) in _OOXML.items() if key != kind):
        _reject("Office archive mixes incompatible document types")
    matched_root = False
    for name, payload in data.items():
        if name.endswith(".rels"):
            tree = _xml_tree(payload)
            if tree.tag != _REL + "Relationships":
                _reject("Office document contains invalid relationships")
            if name == "_rels/.rels":
                base = ""
            else:
                parent, marker, filename = name.rpartition("/_rels/")
                if not marker or not filename.endswith(".rels"):
                    _reject("Office relationship location is invalid")
                base = parent + "/"
            for relation in tree:
                if relation.tag != _REL + "Relationship":
                    _reject("Office relationship entry is invalid")
                rel_type = relation.get("Type", "")
                if (not rel_type.startswith(_REL_PREFIXES) or _BAD_COMPONENT.search(rel_type)
                        or relation.get("TargetMode", "Internal").lower() != "internal"):
                    _reject("Office document contains external or forbidden relationships")
                target = relation.get("Target", "")
                _safe_reference(target, base, members)
                if name == "_rels/.rels" and rel_type.endswith("/officeDocument") and target == expected:
                    matched_root = True
        elif name.lower().endswith(".xml") and name != "[Content_Types].xml":
            _check_xml_elements(_xml_tree(payload), odf=False, members=members)
    return kind if matched_root else None


def _sniff_odf(data: dict[str, bytes], members: set[str], first: zipfile.ZipInfo) -> str | None:
    mime = data.get("mimetype")
    if mime is None or first.filename != "mimetype" or first.compress_type != zipfile.ZIP_STORED:
        return None
    kind = _MIMETYPES.get(mime.decode("ascii", "replace"))
    if kind is None or "content.xml" not in data or "META-INF/manifest.xml" not in data:
        return None
    content = _xml_tree(data["content.xml"])
    if content.tag != _OFFICE + "document-content":
        return None
    if content.get(_OFFICE + "mimetype", mime.decode("ascii", "replace")) != mime.decode("ascii", "replace"):
        _reject("ODF content declares a conflicting document type")
    manifest = _xml_tree(data["META-INF/manifest.xml"])
    if manifest.tag != _MANIFEST + "manifest":
        return None
    found_root = False
    for item in manifest:
        if item.tag != _MANIFEST + "file-entry":
            _reject("ODF archive has unsupported manifest entries")
        path = item.get(_MANIFEST + "full-path", "")
        if path == "/":
            found_root = item.get(_MANIFEST + "media-type", "") == mime.decode("ascii")
        elif path.rstrip("/") not in members:
            _reject("ODF manifest references a missing file")
        if _BAD_NAME.search(path) or _BAD_COMPONENT.search(path):
            _reject("ODF manifest contains embedded or active content")
    if not found_root:
        return None
    for name, payload in data.items():
        if name.lower().endswith(".xml"):
            _check_xml_elements(_xml_tree(payload), odf=True, members=members)
    return kind


def inspect_office(source: Path) -> str | None:
    """Return docx/xlsx/pptx/odt/ods/odp only for valid, passive ZIP packages.

    Content *and* filename suffix must agree; a fake .docx is never accepted.
    Invalid, active, oversized, encrypted or linked documents return ``None``.
    """
    try:
        source = Path(source)
        if source.suffix.lower().lstrip(".") not in (*_OOXML, "odt", "ods", "odp"):
            return None
        if not source.is_file() or source.stat().st_size > _MAX_ZIP_SIZE:
            return None
        with source.open("rb") as f:
            if f.read(4) != b"PK\x03\x04":
                return None
        with zipfile.ZipFile(source) as archive:
            data, members = _archive_contents(archive)
            kind = (_sniff_ooxml(data, members) if "[Content_Types].xml" in members
                    else _sniff_odf(data, members, archive.infolist()[0]))
            return kind if kind == source.suffix.lower().lstrip(".") else None
    except (OSError, ValueError, OverflowError, RuntimeError, zipfile.BadZipFile,
            zipfile.LargeZipFile, UnicodeError, EOFError, ET.ParseError):
        return None


def _executable(candidate: Path) -> str | None:
    """Check an absolute, canonical LibreOffice executable; never use shell lookup."""
    names = ("soffice.exe", "soffice.com") if os.name == "nt" else ("soffice",)
    try:
        if not candidate.is_absolute() or candidate.name.casefold() not in names:
            return None
        if candidate.is_symlink() or not candidate.is_file():
            return None
        canonical = candidate.resolve(strict=True)
        if not canonical.is_file() or not os.access(canonical, os.X_OK):
            return None
        return str(canonical)
    except (OSError, ValueError):
        return None


def find_libreoffice(configured: str | None = None) -> str | None:
    """Find an explicitly configured binary or a canonical absolute PATH entry.

    Never invokes shell, ``shutil.which``, implicit CWD or relative PATH entries.
    """
    if configured is not None:
        supplied = Path(configured).expanduser()
        if not supplied.is_absolute():
            return None
        if supplied.is_dir():
            for name in (("soffice.exe", "soffice.com") if os.name == "nt" else ("soffice",)):
                found = _executable(supplied / name)
                if found:
                    return found
            return None
        return _executable(supplied)
    try:
        cwd = Path.cwd().resolve(strict=True)
    except OSError:
        cwd = None
    for item in os.environ.get("PATH", "").split(os.pathsep):
        value = item.strip().strip('"')
        if not value:
            continue
        directory = Path(value)
        if not directory.is_absolute() or directory.is_symlink():
            continue
        try:
            directory = directory.resolve(strict=True)
        except OSError:
            continue
        if directory == cwd:
            continue
        for name in (("soffice.exe", "soffice.com") if os.name == "nt" else ("soffice",)):
            found = _executable(directory / name)
            if found is not None:
                return found
    return None


def _stop_process(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        # soffice.exe can spawn soffice.bin. Kill descendants as well, using
        # the trusted System32 taskkill.exe only. A bare "taskkill" would be
        # resolved by CreateProcess through the application directory, the
        # current directory (a user's folder in Explorer) and PATH first.
        from .engine import _kill_process_tree_windows
        _kill_process_tree_windows(proc.pid)
    else:
        import signal
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
    if proc.poll() is None:
        proc.kill()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        pass


class _WindowsOfficeJob:
    """Windows kill-on-close Job Object for just this invocation's process tree.

    LibreOffice's .com wrapper starts soffice.bin. Waiting only for the wrapper
    can report success before the real converter has written its PDF.
    """

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
            )]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimits),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.CreateJobObjectW(None, None)
        if not handle:
            raise ConversionError("Could not isolate LibreOffice in a Windows Job Object")
        self._kernel = kernel
        self._handle = handle
        try:
            limits = ExtendedLimits()
            limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise ConversionError("Could not configure LibreOffice process isolation")
        except BaseException:
            self.close()
            raise

    def attach(self, process: subprocess.Popen[bytes]) -> None:
        if not self._kernel.AssignProcessToJobObject(self._handle, process._handle):
            raise ConversionError("Could not attach LibreOffice to its private process job")

    def close(self) -> None:
        if self._handle:
            self._kernel.CloseHandle(self._handle)
            self._handle = None


def _remove_private_tree(run_dir: Path) -> None:
    """Remove our run tree, including deep LibreOffice Windows profile files.

    Windows' classic 260-character path limit can make shutil.rmtree fail on
    LibreOffice's nested bundled-extension registry despite a successful PDF.
    Use the Win32 extended-length form for the already validated private root.
    """
    if run_dir.is_symlink() or not run_dir.name.startswith("rightconvert-office-"):
        raise ConversionError("Refusing to remove an unrecognized Office workspace")
    if not run_dir.exists():
        return
    absolute = run_dir.resolve(strict=True)
    original_identity = absolute.lstat()
    if os.name == "nt":
        name = str(absolute)
        if name.startswith("\\\\"):
            name = "\\\\?\\UNC\\" + name[2:]
        else:
            name = "\\\\?\\" + name
    else:
        name = str(absolute)
    # The child can be exiting while its LibreOffice profile registry removes
    # transient files. Retry the private directory only, on a bounded clock.
    for attempt in range(25):
        try:
            current = absolute.lstat()
        except FileNotFoundError:
            return
        if (current.st_ino != original_identity.st_ino
                or current.st_dev != original_identity.st_dev
                or absolute.is_symlink()):
            raise ConversionError("Office workspace was replaced during cleanup")
        try:
            shutil.rmtree(name)
            return
        except (OSError, FileNotFoundError):
            if attempt == 24:
                raise
            time.sleep(0.1)


def cleanup_office_output(pdf: Path, staging_root: Path) -> None:
    """Remove a returned provider run after caller securely publishes the PDF.

    The caller supplies the exact staging root it passed to conversion. Reject
    unexpected paths and symlinked roots rather than recursively deleting any
    unrelated location.
    """
    pdf, staging_root = Path(pdf), Path(staging_root)
    if (pdf.name != "input.pdf" or pdf.parent.name != "out"
            or pdf.parent.parent.parent != staging_root
            or not staging_root.is_absolute() or staging_root.is_symlink()
            or not staging_root.is_dir()):
        raise ConversionError("Office cleanup path is outside the private staging root")
    _remove_private_tree(pdf.parent.parent)


def _verify_pdf(path: Path, private_output: Path) -> None:
    try:
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or path.parent.resolve(strict=True) != private_output.resolve(strict=True)
                or not (50 <= info.st_size <= _MAX_PDF)):
            raise ConversionError("LibreOffice produced an unsafe PDF output")
        with path.open("rb") as stream:
            header = stream.read(8)
            if not re.fullmatch(rb"%PDF-[12]\.[0-9]\s?", header):
                raise ConversionError("LibreOffice produced an invalid PDF header")
            stream.seek(max(0, info.st_size - 4096))
            tail = stream.read()
            match = _PDF_TAIL.search(tail)
            if match is None:
                raise ConversionError("LibreOffice produced an invalid PDF trailer")
            xref_at = int(match[1])
            if xref_at >= info.st_size:
                raise ConversionError("LibreOffice produced an invalid PDF xref pointer")
            stream.seek(xref_at)
            if not re.match(rb"(?:xref\b|\d+\s+\d+\s+obj\b)", stream.read(64)):
                raise ConversionError("LibreOffice produced an invalid PDF cross-reference")
        try:
            import pypdfium2  # type: ignore[import-not-found]
        except ImportError:
            pass  # Optional deeper verification; no PDF parser dependency in v0.2.
        else:
            with pypdfium2.PdfDocument(str(path)) as doc:
                if len(doc) < 1:
                    raise ConversionError("LibreOffice produced a PDF with no pages")
    except (OSError, ValueError, RuntimeError) as exc:
        raise ConversionError("Could not validate LibreOffice PDF output") from exc


def convert_office_to_pdf(
    source: Path,
    output_dir: Path,
    soffice: str,
    *,
    cancel: threading.Event | None = None,
    progress: Callable[[float], None] | None = None,
    timeout: float = 120.0,
) -> Path:
    """Convert a passive Office ZIP to PDF inside a caller-owned private workspace.

    ``output_dir`` must exist and be a private scratch directory. The provider
    adds a unique private run directory containing the input copy, LibreOffice
    profile and PDF; caller cleans it after securely publishing the PDF.
    No output is ever written to the user's final destination by this module.
    """
    _check_cancel(cancel)
    if not (isinstance(timeout, (int, float)) and 0 < timeout <= 1800):
        raise ConversionError("LibreOffice timeout must be between 0 and 1800 seconds")
    executable = _executable(Path(soffice))
    if executable is None:
        raise ConversionError("A trusted absolute LibreOffice executable is required")
    source = Path(source)
    kind = inspect_office(source)
    if kind is None:
        raise UnsupportedFormatError("Invalid, linked or unsupported Office input")
    base = Path(output_dir)
    if not base.is_absolute() or base.is_symlink() or not base.is_dir():
        raise ConversionError("Office staging directory must be an existing absolute directory")
    base = base.resolve(strict=True)
    filters = {"docx": "writer_pdf_Export", "odt": "writer_pdf_Export",
               "xlsx": "calc_pdf_Export", "ods": "calc_pdf_Export",
               "pptx": "impress_pdf_Export", "odp": "impress_pdf_Export"}
    run_dir: Path | None = None
    complete = False
    try:
        run_dir = Path(tempfile.mkdtemp(prefix="rightconvert-office-", dir=base))
        profile = run_dir / "profile"
        output = run_dir / "out"
        profile.mkdir(mode=0o700)
        output.mkdir(mode=0o700)
        copied = run_dir / f"input.{kind}"
        # Re-inspect the exact bytes LibreOffice will see, closing a source
        # substitution race between the initial sniff and conversion.
        with source.open("rb") as reader, copied.open("xb") as writer:
            remaining = _MAX_ZIP_SIZE + 1
            while remaining:
                _check_cancel(cancel)
                block = reader.read(min(128 * 1024, remaining))
                if not block:
                    break
                writer.write(block)
                remaining -= len(block)
            if remaining == 0:
                raise UnsupportedFormatError("Office input exceeds safety limits")
        if inspect_office(copied) != kind:
            raise UnsupportedFormatError("Office input changed during validation")
        _check_cancel(cancel)
        if progress:
            progress(0.1)
        command = [
            executable,
            "-env:UserInstallation=" + profile.as_uri(),
            "--headless", "--invisible", "--nodefault", "--nologo", "--norestore",
            "--convert-to", f"pdf:{filters[kind]}", "--outdir", str(output), str(copied),
        ]
        log = run_dir / "conversion.log"
        expected = output / "input.pdf"
        job = None
        try:
            with log.open("wb") as stream:
                job = _WindowsOfficeJob() if os.name == "nt" else None
                proc = subprocess.Popen(
                    command, shell=False, cwd=str(run_dir), stdin=subprocess.DEVNULL,
                    stdout=stream, stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    start_new_session=(os.name != "nt"),
                )
                try:
                    if job is not None and hasattr(proc, "_handle"):
                        job.attach(proc)
                    deadline = time.monotonic() + timeout
                    while proc.poll() is None:
                        _check_cancel(cancel)
                        if time.monotonic() >= deadline:
                            raise ConversionError("LibreOffice conversion timed out")
                        time.sleep(0.05)
                    _check_cancel(cancel)
                    if proc.returncode != 0:
                        raise ConversionError(f"LibreOffice conversion failed (exit {proc.returncode})")
                    # soffice.com can exit before soffice.bin publishes the PDF,
                    # and LibreOffice may leave its bin process idle afterwards.
                    # Wait for the exact PDF to validate and stabilize. Closing
                    # this private job will then stop *only our* leftover child.
                    stable_size = -1
                    stable_polls = 0
                    invalid_size = -1
                    invalid_polls = 0
                    while True:
                        _check_cancel(cancel)
                        if time.monotonic() >= deadline:
                            raise ConversionError("LibreOffice PDF output timed out or was invalid")
                        current_files = sorted(output.iterdir())
                        if current_files == [expected]:
                            try:
                                _verify_pdf(expected, output)
                            except ConversionError:
                                stable_polls = 0
                                stable_size = -1
                                current_size = expected.stat().st_size
                                invalid_polls = invalid_polls + 1 if current_size == invalid_size else 1
                                invalid_size = current_size
                                if invalid_polls >= 10:
                                    raise ConversionError("LibreOffice produced an invalid PDF output")
                            else:
                                invalid_polls = 0
                                current_size = expected.stat().st_size
                                stable_polls = stable_polls + 1 if current_size == stable_size else 1
                                stable_size = current_size
                                if stable_polls >= 4:
                                    break
                        else:
                            stable_polls = 0
                            stable_size = -1
                            invalid_polls = 0
                            invalid_size = -1
                        time.sleep(0.1)
                finally:
                    _stop_process(proc)
                    if job is not None:
                        job.close()
        except OSError as exc:
            raise ConversionError("Could not launch LibreOffice") from exc
        finally:
            # Also close the job if Popen fails before the inner finally exists.
            if job is not None:
                job.close()
        if sorted(output.iterdir()) != [expected]:
            raise ConversionError("LibreOffice produced missing or unexpected output files")
        _verify_pdf(expected, output)
        _check_cancel(cancel)
        if progress:
            progress(0.9)
        complete = True
        return expected
    finally:
        if not complete and run_dir is not None and run_dir.is_dir() and not run_dir.is_symlink():
            try:
                _remove_private_tree(run_dir)
            except OSError:
                # Preserve cancellation/conversion error; caller can clean its
                # private scratch directory if Windows still holds profile files.
                pass
