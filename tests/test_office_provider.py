"""Office provider safety, mocked tests and optional real portable LibreOffice QA."""

from __future__ import annotations

import os
import hashlib
import shutil
import stat
import threading
import tempfile
import zipfile
from pathlib import Path

import pytest

from rightconvert.formats import ConversionCancelled, ConversionError, UnsupportedFormatError
import rightconvert.office_provider as office


_CT_URI = "http://schemas.openxmlformats.org/package/2006/content-types"
_REL_URI = "http://schemas.openxmlformats.org/package/2006/relationships"
_DOC_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
_KINDS = {
    "docx": ("word/document.xml", "wordprocessingml.document.main"),
    "xlsx": ("xl/workbook.xml", "spreadsheetml.sheet.main"),
    "pptx": ("ppt/presentation.xml", "presentationml.presentation.main"),
}
_ROOTS = {
    "docx": b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body/></w:document>',
    "xlsx": b'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheets/></workbook>',
    "pptx": b'<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>',
}
_ODF = {
    "odt": "application/vnd.oasis.opendocument.text",
    "ods": "application/vnd.oasis.opendocument.spreadsheet",
    "odp": "application/vnd.oasis.opendocument.presentation",
}


def _create_ooxml(path: Path, *, extra: dict[str, bytes] | None = None,
                  root_rel: str | None = None, declared: str | None = None):
    kind = path.suffix[1:]
    main, short_type = _KINDS.get(kind, _KINDS["docx"])
    parts = {
        "[Content_Types].xml": (
            f'<Types xmlns="{_CT_URI}"><Override PartName="/{main}" '
            f'ContentType="{declared or "application/vnd.openxmlformats-officedocument." + short_type + "+xml"}"/>'
            '</Types>'
        ).encode(),
        "_rels/.rels": (root_rel or (
            f'<Relationships xmlns="{_REL_URI}"><Relationship Id="rId1" '
            f'Type="{_DOC_REL}" Target="{main}"/></Relationships>'
        )).encode(),
        main: _ROOTS.get(kind, _ROOTS["docx"]),
    }
    parts.update(extra or {})
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in parts.items():
            archive.writestr(name, payload)
    return path


def _create_odf(path: Path, *, extra: dict[str, bytes] | None = None):
    mime = _ODF[path.suffix[1:]]
    entries = {
        "content.xml": (
            '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
            f'office:mimetype="{mime}"><office:body/></office:document-content>'
        ).encode(),
        "META-INF/manifest.xml": (
            '<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0">'
            f'<manifest:file-entry manifest:full-path="/" manifest:media-type="{mime}"/>'
            '</manifest:manifest>'
        ).encode(),
    }
    entries.update(extra or {})
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", mime, compress_type=zipfile.ZIP_STORED)
        for name, payload in entries.items():
            archive.writestr(name, payload, compress_type=zipfile.ZIP_DEFLATED)
    return path


@pytest.mark.parametrize("kind", ["docx", "xlsx", "pptx", "odt", "ods", "odp"])
def test_detects_all_six_zip_types_from_content(kind, tmp_path):
    path = tmp_path / f"Zi 東京.{kind}"
    (_create_ooxml if kind in _KINDS else _create_odf)(path)
    previous = path.read_bytes()
    assert office.inspect_office(path) == kind
    assert path.read_bytes() == previous
    renamed = path.with_suffix(".docx" if kind != "docx" else ".xlsx")
    path.rename(renamed)
    assert office.inspect_office(renamed) is None


def test_not_trusting_fake_extension_missing_parts_or_nonzip(tmp_path):
    assert office.inspect_office(tmp_path / "missing.docx") is None
    broken = tmp_path / "broken.docx"
    broken.write_bytes(b"This is not a real DOCX file")
    assert office.inspect_office(broken) is None
    bad = tmp_path / "no_doc.docx"
    with zipfile.ZipFile(bad, "w") as archive:
        archive.writestr("word/document.xml", "<document/>")
    assert office.inspect_office(bad) is None
    misleading = tmp_path / "mixed.docx"
    _create_ooxml(misleading, declared="application/vnd.ms-word.document.macroEnabled.main+xml")
    assert office.inspect_office(misleading) is None
    fake_structure = tmp_path / "fake_structure.docx"
    _create_ooxml(fake_structure, extra={"word/document.xml": b'<document xmlns="urn:fake"/>'})
    assert office.inspect_office(fake_structure) is None


@pytest.mark.parametrize("part", [
    "word/vbaProject.bin", "word/embeddings/oleObject1.bin",
    "xl/externalLinks/externalLink1.xml", "ppt/activeX/activeX1.xml",
    "word/Scripts/shell.py", "word/_xmlsignatures/sig1.xml",
])
def test_rejects_macros_embeds_external_parts(part, tmp_path):
    path = _create_ooxml(tmp_path / "document.docx", extra={part: b"payload"})
    assert office.inspect_office(path) is None


@pytest.mark.parametrize("target,mode", [
    ("https://example.test/data", ' TargetMode="External"'),
    ("file:///c:/secret.txt", ""),
    ("../../private.txt", ""),
    ("missing.xml", ""),
    ("\\\\server\\share\\file", ""),
])
def test_rejects_external_and_broken_relationships(target, mode, tmp_path):
    rel = (f'<Relationships xmlns="{_REL_URI}"><Relationship Id="rId1" '
           f'Type="{_DOC_REL}" Target="{target}"{mode}/></Relationships>')
    path = _create_ooxml(tmp_path / "document.docx", root_rel=rel)
    assert office.inspect_office(path) is None


def test_rejects_internal_hyperlink_relationship_and_fields(tmp_path):
    link = (f'<Relationships xmlns="{_REL_URI}"><Relationship Id="rId1" '
            f'Type="{_DOC_REL}" Target="word/document.xml"/>'
            '<Relationship Id="rId2" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
            'Target="word/document.xml"/></Relationships>')
    assert office.inspect_office(_create_ooxml(tmp_path / "hyper.docx", root_rel=link)) is None
    payload = b'<w:document xmlns:w="urn:word"><w:instrText>INCLUDETEXT http://host/file</w:instrText></w:document>'
    assert office.inspect_office(_create_ooxml(tmp_path / "fields.docx", extra={"word/document.xml": payload})) is None


def test_rejects_spreadsheet_formula_text_that_can_call_network(tmp_path):
    excel = (b'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             b'<sheetData><row r="1"><c r="A1"><f>WEBSERVICE("http://127.0.0.1/data")</f>'
             b'</c></row></sheetData></worksheet>')
    path = _create_ooxml(tmp_path / "network.xlsx", extra={"xl/worksheets/sheet1.xml": excel})
    assert office.inspect_office(path) is None
    innocent = excel.replace(b'WEBSERVICE("http://127.0.0.1/data")', b'SUM(A1:A2)')
    assert office.inspect_office(_create_ooxml(tmp_path / "formula.xlsx", extra={"xl/worksheets/sheet1.xml": innocent})) is None
    defined = (b'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
               b'<definedNames><definedName name="data">WEBSERVICE("http://127.0.0.1")</definedName>'
               b'</definedNames></workbook>')
    assert office.inspect_office(_create_ooxml(tmp_path / "defined.xlsx", extra={"xl/workbook.xml": defined})) is None


def test_rejects_odf_formula_even_if_link_hidden_inside_cell_formula(tmp_path):
    content = (
        '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0">'
        '<table:table-cell table:formula="of:=WEBSERVICE(\"http://127.0.0.1/\")"/>'
        '</office:document-content>'
    ).encode()
    assert office.inspect_office(_create_odf(tmp_path / "unsafe.ods", extra={"content.xml": content})) is None


def test_rejects_odf_scripts_external_urls_and_embedded_objects(tmp_path):
    path = _create_odf(tmp_path / "macros.odt", extra={"Basic/Standard/Module1.xml": b"<script/>"})
    assert office.inspect_office(path) is None
    external = (
        '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
        'xmlns:xlink="http://www.w3.org/1999/xlink"><draw:image '
        'xlink:href="https://host.invalid/image.png"/></office:document-content>'
    ).encode()
    assert office.inspect_office(_create_odf(tmp_path / "links.odt", extra={"content.xml": external})) is None
    embedded = b'<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"><object/></office:document-content>'
    assert office.inspect_office(_create_odf(tmp_path / "embeds.odt", extra={"content.xml": embedded})) is None


def test_rejects_duplicate_slip_bombs_and_xml_doctype(tmp_path):
    dup = tmp_path / "duplicate.docx"
    _create_ooxml(dup, extra={"word/Picture.PNG": b"one", "word/picture.png": b"two"})
    assert office.inspect_office(dup) is None
    traversal = tmp_path / "traversal.docx"
    _create_ooxml(traversal, extra={"../../secret": b"bad"})
    assert office.inspect_office(traversal) is None
    bomb = tmp_path / "bomb.docx"
    _create_ooxml(bomb, extra={"word/media/image.png": b"x" * 300_000})
    assert office.inspect_office(bomb) is None
    xxe = tmp_path / "xxe.docx"
    _create_ooxml(xxe, extra={"word/document.xml": b'<!DOCTYPE doc [<!ENTITY x SYSTEM "file:///etc/passwd">]><doc>&x;</doc>'})
    assert office.inspect_office(xxe) is None
    active_svg = tmp_path / "active-svg.docx"
    _create_ooxml(active_svg, extra={"word/media/remote.svg": b'<svg xmlns="http://www.w3.org/2000/svg"><image href="https://example.test"/></svg>'})
    assert office.inspect_office(active_svg) is None


def _executable(tmp_path: Path) -> Path:
    exe = tmp_path / ("soffice.exe" if os.name == "nt" else "soffice")
    exe.write_bytes(b"not really LibreOffice; launch will be mocked")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    return exe


def test_binary_discovery_only_absolute_trusted_paths(tmp_path, monkeypatch):
    executable = _executable(tmp_path)
    assert office.find_libreoffice(str(executable)) == str(executable.resolve())
    assert office.find_libreoffice(str(tmp_path)) == str(executable.resolve())
    assert office.find_libreoffice(executable.name) is None
    assert office.find_libreoffice("./soffice.exe") is None
    monkeypatch.setenv("PATH", f".{os.pathsep}{tmp_path}")
    assert office.find_libreoffice() == str(executable.resolve())
    monkeypatch.chdir(tmp_path)
    assert office.find_libreoffice() is None, "CWD must not become a trusted PATH bin directory"
    monkeypatch.setenv("PATH", "")
    assert office.find_libreoffice() is None


def _pdf_bytes() -> bytes:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R >>",
        b"<< /Length 0 >>\nstream\n\nendstream",
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, body in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(pdf)
    pdf += f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode()
    for offset in offsets[1:]:
        pdf += f"{offset:010} 00000 n \n".encode()
    pdf += f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(pdf)


class _MockProcess:
    def __init__(self, *args, on_poll=None, returncode=0, **kwargs):
        self.argv = args[0]
        self.kwargs = kwargs
        self.returncode = returncode
        self.pid = 987654321
        self._on_poll = on_poll
        self.killed = False

    def poll(self):
        if self._on_poll:
            self._on_poll()
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


def test_mock_conversion_isolated_profile_pdf_verified_and_source_untouched(tmp_path, monkeypatch):
    source = _create_ooxml(tmp_path / "Original 東京.docx")
    original = source.read_bytes()
    exe = _executable(tmp_path)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    calls = []

    def fake_popen(command, **kwargs):
        calls.append((command, kwargs))
        assert command[0] == str(exe.resolve())
        assert "--headless" in command
        assert "--convert-to" in command
        assert "pdf:writer_pdf_Export" in command
        assert command[-1].endswith("input.docx")
        assert Path(command[-1]).read_bytes() == original
        assert Path(kwargs["cwd"]).is_relative_to(scratch)
        assert command[1].startswith("-env:UserInstallation=file:///")
        out = Path(command[command.index("--outdir") + 1])
        out.joinpath("input.pdf").write_bytes(_pdf_bytes())
        return _MockProcess(command, **kwargs)

    monkeypatch.setattr(office.subprocess, "Popen", fake_popen)
    progress = []
    result = office.convert_office_to_pdf(source, scratch, str(exe), progress=progress.append)
    assert len(calls) == 1
    assert calls[0][1]["shell"] is False
    assert result.parent.parent.parent == scratch
    assert result.read_bytes() == _pdf_bytes()
    assert source.read_bytes() == original
    assert progress == [0.1, 0.9]
    assert not (tmp_path / "Original 東京.pdf").exists()


def test_invalid_pdf_is_rejected_and_private_artifacts_removed(tmp_path, monkeypatch):
    source = _create_ooxml(tmp_path / "test.docx")
    exe = _executable(tmp_path)
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    def fake_popen(command, **kwargs):
        Path(command[command.index("--outdir") + 1], "input.pdf").write_bytes(b"%PDF-1.7\nnot a real PDF")
        return _MockProcess(command, **kwargs)

    monkeypatch.setattr(office.subprocess, "Popen", fake_popen)
    with pytest.raises(ConversionError, match="PDF"):
        office.convert_office_to_pdf(source, scratch, str(exe))
    assert list(scratch.iterdir()) == []


def test_cancel_in_progress_kills_process_and_cleans_files(tmp_path, monkeypatch):
    source = _create_ooxml(tmp_path / "test.docx")
    exe = _executable(tmp_path)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    cancel = threading.Event()
    holder = []

    def fake_popen(command, **kwargs):
        proc = _MockProcess(command, returncode=None, on_poll=cancel.set, **kwargs)
        holder.append(proc)
        return proc

    monkeypatch.setattr(office.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(office.subprocess, "run", lambda *args, **kwargs: None)
    with pytest.raises(ConversionCancelled):
        office.convert_office_to_pdf(source, scratch, str(exe), cancel=cancel)
    assert holder[0].killed
    assert list(scratch.iterdir()) == []


def test_timeout_kills_process_and_cleans_files(tmp_path, monkeypatch):
    source = _create_odf(tmp_path / "test.odt")
    exe = _executable(tmp_path)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    holder = []

    def fake_popen(command, **kwargs):
        proc = _MockProcess(command, returncode=None, **kwargs)
        holder.append(proc)
        return proc

    monkeypatch.setattr(office.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(office.subprocess, "run", lambda *args, **kwargs: None)
    with pytest.raises(ConversionError, match="timed out"):
        office.convert_office_to_pdf(source, scratch, str(exe), timeout=0.01)
    assert holder[0].killed
    assert list(scratch.iterdir()) == []


def test_no_process_spawn_for_rejected_document(tmp_path, monkeypatch):
    exe = _executable(tmp_path)
    bad = tmp_path / "bad.docx"
    bad.write_text("fake")
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    def should_never_start(*args, **kwargs):
        pytest.fail("LibreOffice launched for an unsafe document")

    monkeypatch.setattr(office.subprocess, "Popen", should_never_start)
    with pytest.raises(UnsupportedFormatError):
        office.convert_office_to_pdf(bad, scratch, str(exe))
    assert list(scratch.iterdir()) == []


# The native acceptance fixtures below intentionally include visible content,
# realistic OOXML relationships and ODF bodies. They are self-contained and
# created only within the disposable QA directory, never installed globally.
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _native_source(path: Path) -> None:
    """Create an offline, passive, renderable fixture for one Office family."""
    kind = path.suffix[1:]
    if kind == "docx":
        contents = (f'<w:document xmlns:w="{_W}"><w:body><w:p><w:r><w:t>'
                    'RightConvert native DOCX verification 2026'
                    '</w:t></w:r></w:p><w:sectPr/></w:body></w:document>').encode()
        _create_ooxml(path, extra={"word/document.xml": contents})
        return
    if kind == "xlsx":
        workbook = (f'<workbook xmlns="{_S}" xmlns:r="{_R}"><sheets>'
                    '<sheet name="Checks" sheetId="1" r:id="rId1"/>'
                    '</sheets></workbook>').encode()
        # Explicitly widen column A: PDF preserves Calc's printable cell
        # geometry, and Excel's default narrow column clips long cell text.
        worksheet = (f'<worksheet xmlns="{_S}"><cols>'
                     '<col min="1" max="1" width="52" customWidth="1"/>'
                     '</cols><sheetData>'
                     '<row r="1"><c r="A1" t="inlineStr"><is><t>'
                     'RightConvert native XLSX verification'                     '</t></is></c><c r="B1"><v>2026</v></c></row>'
                     '</sheetData></worksheet>').encode()
        rels = (f'<Relationships xmlns="{_REL_URI}"><Relationship Id="rId1" '
                f'Type="{_R}/worksheet" Target="worksheets/sheet1.xml"/>'
                '</Relationships>').encode()
        _create_ooxml(path, extra={
            "xl/workbook.xml": workbook,
            "xl/_rels/workbook.xml.rels": rels,
            "xl/worksheets/sheet1.xml": worksheet,
        })
        return
    if kind == "pptx":
        presentation = (f'<p:presentation xmlns:p="{_P}" xmlns:r="{_R}">'
                        '<p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst>'
                        '<p:sldSz cx="9144000" cy="6858000" type="screen4x3"/>'
                        '<p:notesSz cx="6858000" cy="9144000"/></p:presentation>').encode()
        rels = (f'<Relationships xmlns="{_REL_URI}"><Relationship Id="rId1" '
                f'Type="{_R}/slide" Target="slides/slide1.xml"/>'
                '</Relationships>').encode()
        slide = (f'<p:sld xmlns:p="{_P}" xmlns:a="{_A}"><p:cSld><p:spTree>'
                 '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
                 '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
                 '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
                 '<p:sp><p:nvSpPr><p:cNvPr id="2" name="QA title"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>'
                 '<p:spPr><a:xfrm><a:off x="400000" y="500000"/>'
                 '<a:ext cx="7600000" cy="1000000"/></a:xfrm><a:prstGeom prst="rect">'
                 '<a:avLst/></a:prstGeom></p:spPr><p:txBody><a:bodyPr/><a:lstStyle/>'
                 '<a:p><a:r><a:rPr lang="en-US"/><a:t>RightConvert native PPTX verification</a:t>'
                 '</a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld>'
                 '</p:sld>').encode()
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", (
                f'<Types xmlns="{_CT_URI}"><Override PartName="/ppt/presentation.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
                '<Override PartName="/ppt/slides/slide1.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
                '</Types>'))
            archive.writestr("_rels/.rels", (
                f'<Relationships xmlns="{_REL_URI}"><Relationship Id="rId1" '
                f'Type="{_DOC_REL}" Target="ppt/presentation.xml"/></Relationships>'))
            archive.writestr("ppt/presentation.xml", presentation)
            archive.writestr("ppt/_rels/presentation.xml.rels", rels)
            archive.writestr("ppt/slides/slide1.xml", slide)
        return
    if kind in _ODF:
        ns = 'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        ns += 'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
        ns += 'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
        ns += 'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
        ns += 'xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0"'
        inner = {
            "odt": '<office:text><text:p>RightConvert native ODT verification</text:p></office:text>',
            "ods": '<office:spreadsheet><table:table table:name="Checks"><table:table-row>'
                   '<table:table-cell office:value-type="string"><text:p>'
                   'RightConvert native ODS verification</text:p></table:table-cell>'
                   '</table:table-row></table:table></office:spreadsheet>',
            "odp": '<office:presentation><draw:page draw:name="Slide 1">'
                   '<draw:frame svg:x="1cm" svg:y="1cm" svg:width="20cm" svg:height="3cm">'
                   '<draw:text-box><text:p>RightConvert native ODP verification</text:p>'
                   '</draw:text-box></draw:frame></draw:page></office:presentation>',
        }[kind]
        content = (f'<office:document-content {ns} office:mimetype="{_ODF[kind]}">'
                   f'<office:body>{inner}</office:body></office:document-content>').encode()
        _create_odf(path, extra={"content.xml": content})
        return
    raise AssertionError(kind)


@pytest.fixture(scope="module")
def portable_soffice():
    """Explicit opt-in to QA-only extraction; no system LibreOffice probing."""
    path = (Path(__file__).resolve().parents[1] / "build" / "stage6"
            / "libreoffice-isolated" / "portable-root" / "program" / "soffice.com")
    if not path.is_file():
        pytest.skip("Official portable LibreOffice 26.8.1.1 QA extraction unavailable")
    chosen = office.find_libreoffice(str(path))
    assert chosen == str(path.resolve(strict=True))
    return chosen


@pytest.mark.parametrize("kind", ["docx", "xlsx", "pptx", "odt", "ods", "odp"])
def test_real_portable_libreoffice_to_pdf(kind, portable_soffice, tmp_path, monkeypatch):
    """Actual Windows soffice.com subprocess and validator, no GUI/registry."""
    scratch = tmp_path / "private"
    scratch.mkdir()
    temp_root = scratch / "isolated-temp"
    temp_root.mkdir()
    monkeypatch.setenv("TMP", str(temp_root))
    monkeypatch.setenv("TEMP", str(temp_root))
    source = tmp_path / f"Raport-東京-{kind}.{kind}"
    _native_source(source)
    assert office.inspect_office(source) == kind
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    progress: list[float] = []
    result = office.convert_office_to_pdf(
        source, scratch, portable_soffice, progress=progress.append, timeout=90,
    )
    assert result.is_file()
    assert result.parent.name == "out"
    assert result.parent.parent.parent == scratch
    assert result.name == "input.pdf"
    assert result.stat().st_size > 300
    assert result.read_bytes().startswith(b"%PDF-")
    assert b"%%EOF" in result.read_bytes()[-256:]
    try:
        import pypdfium2
    except ImportError:
        pass
    else:
        with pypdfium2.PdfDocument(str(result)) as document:
            assert len(document) >= 1
            text = "\n".join(
                document[page_index].get_textpage().get_text_range()
                for page_index in range(len(document))
            )
            assert f"RightConvert native {kind.upper()} verification" in text
    assert progress == [0.1, 0.9]
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert not list(tmp_path.glob("*.pdf"))
    run_dir = result.parent.parent
    assert (run_dir / "profile").is_dir()
    assert (run_dir / f"input.{kind}").is_file()
    print(f"NATIVE_LIBREOFFICE_PASS {kind.upper()} PDF={result.stat().st_size} bytes")
    office.cleanup_office_output(result, scratch)
    assert sorted(p.name for p in scratch.iterdir()) == ["isolated-temp"]


@pytest.mark.parametrize("stop", ["cancel", "timeout"])
def test_real_portable_libreoffice_stop_cleans_private_tree(
    stop, portable_soffice, tmp_path, monkeypatch,
):
    """Kill a real spawned LibreOffice job, verify no PDF or profile survives."""
    source = tmp_path / "cancel-me.docx"
    _native_source(source)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    scratch = tmp_path / "private"
    scratch.mkdir()
    temporary = scratch / "isolated-temp"
    temporary.mkdir()
    monkeypatch.setenv("TEMP", str(temporary))
    monkeypatch.setenv("TMP", str(temporary))
    signal = threading.Event()
    if stop == "cancel":
        actual_popen = office.subprocess.Popen
        spawned = []

        def cancel_when_started(*args, **kwargs):
            process = actual_popen(*args, **kwargs)
            if args and args[0] and args[0][0] == portable_soffice:
                spawned.append(process.pid)
                signal.set()
            return process

        monkeypatch.setattr(office.subprocess, "Popen", cancel_when_started)
        with pytest.raises(ConversionCancelled):
            office.convert_office_to_pdf(
                source, scratch, portable_soffice, cancel=signal, timeout=40,
            )
        assert spawned, "Real soffice.com process must launch before cancellation"
    else:
        with pytest.raises(ConversionError, match="timed out"):
            office.convert_office_to_pdf(source, scratch, portable_soffice, timeout=0.01)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
    assert sorted(p.name for p in scratch.iterdir()) == ["isolated-temp"]
    assert not list(tmp_path.glob("*.pdf"))
