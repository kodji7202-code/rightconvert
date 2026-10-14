"""Stage 6 PDF/Office hostile-input and document-output acceptance tests.

All fixtures are generated within pytest's disposable tmp_path. Tests using
real PDFium/LibreOffice conversion explicitly SKIP until the engine advertises
the corresponding route; a skip is never counted as backend validation.
"""

from __future__ import annotations

import errno
import hashlib
import io
import socket
import threading
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import pytest
from PIL import Image

import rightconvert.engine as engine
from rightconvert.engine import (
    ConversionCancelled,
    ConversionError,
    convert_file,
    get_targets,
)
from rightconvert.office_provider import inspect_office


_CONTENT_TYPES = "http://schemas.openxmlformats.org/package/2006/content-types"
_PKG_RELS = "http://schemas.openxmlformats.org/package/2006/relationships"
_DOC_RELS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_WORD = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _assert_no_stage(folder: Path) -> None:
    assert not list(folder.glob(".rightconvert-stage-*"))


def _make_pdf(
    path: Path, *, pages: int = 1, uri: str | None = None, media_size: int = 180
) -> Path:
    """Build a self-contained classic-xref PDF with distinct page text.

    This intentionally uses no PDF library, avoiding a second PDF renderer
    that might interfere with the engine integration being tested.
    """
    assert 1 <= pages <= 70
    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    kids = " ".join(f"{4 + idx * 2} 0 R" for idx in range(pages))
    objects[2] = f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>".encode("ascii")
    next_id = 4 + pages * 2
    for i in range(pages):
        number = 4 + i * 2
        content = f"BT /F1 15 Tf 18 65 Td (RightConvert page {i + 1}) Tj ET".encode("ascii")
        annot = ""
        if uri is not None:
            annotation = (
                f"<< /Type /Annot /Subtype /Link /Rect [0 0 100 60] "
                f"/Border [0 0 0] /A << /S /URI /URI ({uri}) >> >>"
            )
            objects[next_id] = annotation.encode("ascii")
            annot = f" /Annots [{next_id} 0 R]"
            next_id += 1
        objects[number] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {media_size} {media_size}] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {number + 1} 0 R{annot} >>"
        ).encode("ascii")
        objects[number + 1] = (
            f"<< /Length {len(content)} >>\nstream\n".encode("ascii")
            + content
            + b"\nendstream"
        )
    buffer = io.BytesIO()
    buffer.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = {0: 0}
    for identifier in sorted(objects):
        offsets[identifier] = buffer.tell()
        buffer.write(f"{identifier} 0 obj\n".encode("ascii"))
        buffer.write(objects[identifier] + b"\nendobj\n")
    xref_offset = buffer.tell()
    maximum = max(objects)
    buffer.write(f"xref\n0 {maximum + 1}\n0000000000 65535 f \n".encode("ascii"))
    for identifier in range(1, maximum + 1):
        offset = offsets.get(identifier)
        buffer.write(
            f"{offset:010d} 00000 n \n".encode("ascii")
            if offset is not None else b"0000000000 65535 f \n"
        )
    buffer.write(
        f"trailer\n<< /Root 1 0 R /Size {maximum + 1} >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    path.write_bytes(buffer.getvalue())
    return path


def _make_docx(
    path: Path, *, macro: bool = False, external_url: str | None = None,
    extra: dict[str, bytes] | None = None,
) -> Path:
    main_type = (
        "application/vnd.ms-word.document.macroEnabled.main+xml" if macro else
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
    )
    content_types = (
        f'<Types xmlns="{_CONTENT_TYPES}">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        f'<Override PartName="/word/document.xml" ContentType="{main_type}"/>'
        + (
            '<Override PartName="/word/vbaProject.bin" '
            'ContentType="application/vnd.ms-office.vbaProject"/>' if macro else ""
        ) + "</Types>"
    )
    package_rels = (
        f'<Relationships xmlns="{_PKG_RELS}">'
        f'<Relationship Id="rId1" Type="{_DOC_RELS}/officeDocument" '
        'Target="word/document.xml"/></Relationships>'
    )
    hyperlink = '<w:hyperlink r:id="rIdLink"><w:r><w:t>Linked</w:t></w:r></w:hyperlink>' if external_url else ""
    word_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<w:document xmlns:w="{_WORD}" xmlns:r="{_DOC_RELS}">'
        '<w:body><w:p><w:r><w:t>Stage6 safe document</w:t></w:r>'
        f'{hyperlink}</w:p><w:sectPr/></w:body></w:document>'
    )
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("[Content_Types].xml", content_types)
        bundle.writestr("_rels/.rels", package_rels)
        bundle.writestr("word/document.xml", word_xml)
        if external_url:
            bundle.writestr(
                "word/_rels/document.xml.rels",
                f'<Relationships xmlns="{_PKG_RELS}"><Relationship Id="rIdLink" '
                f'Type="{_DOC_RELS}/hyperlink" Target="{escape(external_url)}" '
                'TargetMode="External"/></Relationships>',
            )
        if macro:
            bundle.writestr("word/vbaProject.bin", b"VBA project binary sentinel")
        for name, contents in (extra or {}).items():
            bundle.writestr(name, contents)
    return path


def _make_xlsx_with_formula(path: Path, formula: str) -> Path:
    """Minimal OOXML workbook with a real worksheet formula text element."""
    spreadsheet_ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    workbook_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
    worksheet_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"
    types = (
        f'<Types xmlns="{_CONTENT_TYPES}">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        f'<Override PartName="/xl/workbook.xml" ContentType="{workbook_type}"/>'
        f'<Override PartName="/xl/worksheets/sheet1.xml" ContentType="{worksheet_type}"/>'
        '</Types>'
    )
    root_rels = (
        f'<Relationships xmlns="{_PKG_RELS}">'
        f'<Relationship Id="rId1" Type="{_DOC_RELS}/officeDocument" '
        'Target="xl/workbook.xml"/></Relationships>'
    )
    workbook = (
        f'<workbook xmlns="{spreadsheet_ns}" xmlns:r="{_DOC_RELS}">'
        '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    sheet = (
        f'<worksheet xmlns="{spreadsheet_ns}"><sheetData>'
        '<row r="1"><c r="A1"><f>' + escape(formula)
        + '</f><v>0</v></c></row></sheetData></worksheet>'
    )
    sheet_rels = (
        f'<Relationships xmlns="{_PKG_RELS}">'
        f'<Relationship Id="rId1" Type="{_DOC_RELS}/worksheet" '
        'Target="worksheets/sheet1.xml"/></Relationships>'
    )
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", sheet_rels)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return path


def _make_odt(path: Path, *, external_url: str | None = None) -> Path:
    mime = "application/vnd.oasis.opendocument.text"
    office_ns = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
    manifest_ns = "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"
    text_ns = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
    link = (
        f'<text:a xlink:href="{escape(external_url)}">Remote file</text:a>'
        if external_url else ""
    )
    content = (
        f'<office:document-content xmlns:office="{office_ns}" '
        f'xmlns:text="{text_ns}" xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'office:mimetype="{mime}">'
        f'<office:body><office:text><text:p>Stage 6 ODT {link}</text:p>'
        '</office:text></office:body></office:document-content>'
    )
    manifest = (
        f'<manifest:manifest xmlns:manifest="{manifest_ns}">'
        f'<manifest:file-entry manifest:full-path="/" manifest:media-type="{mime}"/>'
        '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>'
        '</manifest:manifest>'
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", mime, compress_type=zipfile.ZIP_STORED)
        archive.writestr("content.xml", content, compress_type=zipfile.ZIP_DEFLATED)
        archive.writestr("META-INF/manifest.xml", manifest, compress_type=zipfile.ZIP_DEFLATED)
    return path


def _rejected_without_side_effects(source: Path, target: str, root: Path) -> None:
    destination = root / "output"
    destination.mkdir(exist_ok=True)
    existing = destination / f"{source.stem}.{target}"
    existing.write_bytes(b"DO NOT OVERWRITE USER DATA")
    before = (_digest(source), _digest(existing))
    if source.suffix.lower() in (".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp"):
        assert inspect_office(source) is None, "Unsafe Office package passed passive-only validation"
    assert target not in get_targets(source), "Malicious input advertised a conversion route"
    with pytest.raises(ConversionError):
        convert_file(source, target, destination)
    assert (_digest(source), _digest(existing)) == before
    assert sorted(p.name for p in destination.iterdir()) == [existing.name]
    _assert_no_stage(destination)


def _needs_route(source: Path, target: str, reason: str) -> None:
    if target not in get_targets(source):
        pytest.skip(f"{reason} is not configured/advertised: {target}")


def test_generated_document_fixtures_are_structurally_inspectable(tmp_path: Path) -> None:
    one = _make_pdf(tmp_path / "one.pdf")
    two = _make_pdf(tmp_path / "two.pdf", pages=2)
    assert one.read_bytes().startswith(b"%PDF-1.4")
    assert two.read_bytes().count(b"/Type /Page ") == 2
    assert two.read_bytes().rstrip().endswith(b"%%EOF")
    office = _make_docx(tmp_path / "sample.docx")
    with zipfile.ZipFile(office) as archive:
        assert archive.testzip() is None
        assert b"Stage6 safe document" in archive.read("word/document.xml")
    passive_odt = _make_odt(tmp_path / "safe.odt")
    assert inspect_office(passive_odt) == "odt"
    assert inspect_office(office) == "docx"


@pytest.mark.parametrize("case", ["truncated-pdf", "fake-pdf", "fake-office", "truncated-office"])
def test_corrupt_document_signatures_and_archives_fail_closed(tmp_path: Path, case: str) -> None:
    if case == "truncated-pdf":
        good = _make_pdf(tmp_path / "valid.pdf")
        source = tmp_path / "broken.pdf"
        source.write_bytes(good.read_bytes()[:50])
        good.unlink()
        target = "pngzip"
    elif case == "fake-pdf":
        source = tmp_path / "fake.pdf"
        source.write_bytes(b"%PDF-1.7\nthis is not a document\n")
        target = "pngzip"
    elif case == "fake-office":
        source = tmp_path / "fake.docx"
        source.write_bytes(b"PK\x03\x04truncated local file header")
        target = "pdf"
    else:
        good = _make_docx(tmp_path / "valid.docx")
        source = tmp_path / "broken.docx"
        source.write_bytes(good.read_bytes()[:-30])
        good.unlink()
        target = "pdf"
    _rejected_without_side_effects(source, target, tmp_path)


@pytest.mark.parametrize("member", ["../outside.xml", "/absolute.xml", "word/../../outside.xml"])
def test_ooxml_traversal_members_rejected(tmp_path: Path, member: str) -> None:
    source = _make_docx(tmp_path / "traversal.docx", extra={member: b"<evil/>"})
    _rejected_without_side_effects(source, "pdf", tmp_path)
    assert not (tmp_path / "outside.xml").exists()


def test_macro_enabled_docx_disguised_as_docx_is_rejected(tmp_path: Path) -> None:
    source = _make_docx(tmp_path / "macro-hidden-in-docx.docx", macro=True)
    _rejected_without_side_effects(source, "pdf", tmp_path)


@pytest.mark.parametrize("injected_part", [
    "word/embeddings/oleObject1.bin",
    "xl/vbaProject.bin",
])
def test_embedded_active_content_rejected(tmp_path: Path, injected_part: str) -> None:
    source = _make_docx(
        tmp_path / "active-content.docx", extra={injected_part: b"Untrusted embedded object"}
    )
    _rejected_without_side_effects(source, "pdf", tmp_path)


def test_zip_symlink_member_rejected(tmp_path: Path) -> None:
    source = _make_docx(tmp_path / "symlink-member.docx")
    member = zipfile.ZipInfo("word/linked.xml")
    member.create_system = 3
    member.external_attr = (0o120777 << 16)  # A UNIX symlink, not a regular file.
    with zipfile.ZipFile(source, "a") as archive:
        archive.writestr(member, "../../outside.xml")
    _rejected_without_side_effects(source, "pdf", tmp_path)


def test_ooxml_casefold_duplicate_members_rejected(tmp_path: Path) -> None:
    source = _make_docx(tmp_path / "duplicate-members.docx")
    with zipfile.ZipFile(source, "a") as archive:
        archive.writestr("WORD/document.xml", b"<injected/>")
    _rejected_without_side_effects(source, "pdf", tmp_path)


def test_ooxml_dtd_entity_rejected(tmp_path: Path) -> None:
    payload = b'<!DOCTYPE x [<!ENTITY exploit "surprise">]><x>&exploit;</x>'
    source = _make_docx(tmp_path / "entity-expansion.docx", extra={"customXml/item1.xml": payload})
    _rejected_without_side_effects(source, "pdf", tmp_path)


def test_office_archive_member_count_limit_rejected(tmp_path: Path) -> None:
    extra = {f"word/unused/part-{i:03}.bin": b"x" for i in range(520)}
    source = _make_docx(tmp_path / "too-many-parts.docx", extra=extra)
    with zipfile.ZipFile(source) as archive:
        assert len(archive.infolist()) > 512
    _rejected_without_side_effects(source, "pdf", tmp_path)


def test_external_hyperlink_docx_rejected_without_tcp_request(tmp_path: Path) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(2)
        listener.settimeout(0.35)
        endpoint = f"http://127.0.0.1:{listener.getsockname()[1]}/stage6-external-link"
        source = _make_docx(tmp_path / "external.docx", external_url=endpoint)
        _rejected_without_side_effects(source, "pdf", tmp_path)
        with pytest.raises(socket.timeout):
            connected, _ = listener.accept()
            connected.close()


def test_odf_external_hyperlink_rejected(tmp_path: Path) -> None:
    source = _make_odt(tmp_path / "external.odt", external_url="http://127.0.0.1:16667/external")
    _rejected_without_side_effects(source, "pdf", tmp_path)


def test_xlsx_formula_with_external_webservice_is_rejected(tmp_path: Path) -> None:
    source = _make_xlsx_with_formula(
        tmp_path / "formula.xlsx", 'WEBSERVICE("http://127.0.0.1:16666/private")'
    )
    _rejected_without_side_effects(source, "pdf", tmp_path)


def test_compression_bomb_rejected_without_expanding_archive(tmp_path: Path) -> None:
    source = _make_docx(tmp_path / "expansion-bomb.docx")
    # 128 MiB of highly compressible repetitive XML with modest CPU/RAM.
    # The archive is valid; its uncompressed size is deliberately enormous
    # relative to its on-disk footprint. No extraction to disk occurs here.
    with zipfile.ZipFile(source, "a", compression=zipfile.ZIP_DEFLATED) as archive:
        with archive.open("word/bomb.xml", "w", force_zip64=True) as stream:
            chunk = b"<p>" + b"X" * (1024 * 1024 - 7) + b"</p>"
            for _ in range(128):
                stream.write(chunk)
    with zipfile.ZipFile(source) as archive:
        expanded = sum(i.file_size for i in archive.infolist())
    assert expanded >= 128 * 1024 * 1024 - 1024
    assert source.stat().st_size < 1024 * 1024
    _rejected_without_side_effects(source, "pdf", tmp_path)


@pytest.mark.parametrize("pages,media_size", [(65, 180), (1, 15000)])
def test_pdf_page_count_and_geometry_limits(tmp_path: Path, pages: int, media_size: int) -> None:
    source = _make_pdf(tmp_path / "over-limit.pdf", pages=pages, media_size=media_size)
    _rejected_without_side_effects(source, "pngzip", tmp_path)


def test_encrypted_pdf_cannot_be_advertised_or_opened(tmp_path: Path) -> None:
    # PyMuPDF is a TEST-ONLY optional fixture generator, never an application
    # backend or a runtime dependency required to pass the rest of the suite.
    fitz = pytest.importorskip("fitz")
    source = tmp_path / "encrypted.pdf"
    document = fitz.open()
    try:
        document.new_page(width=180, height=180)
        document.save(
            str(source), encryption=fitz.PDF_ENCRYPT_AES_256,
            user_pw="secret-to-open", owner_pw="owner-secret",
        )
    finally:
        document.close()
    assert source.read_bytes().startswith(b"%PDF-")
    _rejected_without_side_effects(source, "pngzip", tmp_path)


@pytest.mark.parametrize("source_kind,target", [("pdf", "pngzip"), ("office", "pdf")])
def test_precancelled_document_job_never_publishes(
    tmp_path: Path, source_kind: str, target: str
) -> None:
    source = (
        _make_pdf(tmp_path / "αναφορά 中文.pdf") if source_kind == "pdf" else
        _make_docx(tmp_path / "raport ședință 中文.docx")
    )
    before = _digest(source)
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(ConversionCancelled):
        convert_file(source, target, tmp_path / "no-output", cancel=cancelled)
    assert _digest(source) == before
    assert not (tmp_path / "no-output").exists()


def test_pdf_multipage_is_never_silently_flattened(tmp_path: Path) -> None:
    source = _make_pdf(tmp_path / "multi page.pdf", pages=2)
    targets = get_targets(source)
    if not any(name in targets for name in ("pngzip", "png", "jpg", "webp")):
        pytest.skip("A real PDF rendering provider has not advertised PDF output routes")
    for single_page_target in ("png", "jpg", "webp"):
        assert single_page_target not in targets, (
            f"PDF with two pages advertises lossy one-image {single_page_target} output"
        )
    assert "pngzip" in targets, "Multipage PDF needs an explicit all-pages export route"
    source_before = _digest(source)
    output = convert_file(source, "pngzip", tmp_path / "out")
    assert output.name == f"{source.stem}-pages.zip"
    with zipfile.ZipFile(output) as archive:
        members = archive.namelist()
        assert len(members) == 2
        assert len(set(members)) == 2
        assert members == sorted(members), "PDF pages should be emitted in a predictable order"
        assert all(name.lower().endswith(".png") for name in members)
        assert all("/" not in name and "\\" not in name and ".." not in name for name in members)
        page_digests = []
        for name in members:
            pixels = archive.read(name)
            page_digests.append(hashlib.sha256(pixels).hexdigest())
            with Image.open(io.BytesIO(pixels)) as page:
                page.verify()
                assert page.format == "PNG"
        assert page_digests[0] != page_digests[1], "Different PDF pages rendered identically"
    assert _digest(source) == source_before
    _assert_no_stage(output.parent)


@pytest.mark.parametrize("target,pillow_type", [
    ("png", "PNG"), ("jpg", "JPEG"), ("webp", "WEBP"),
])
def test_pdf_single_page_real_decodable_output(
    tmp_path: Path, target: str, pillow_type: str
) -> None:
    source = _make_pdf(tmp_path / "one page.pdf")
    _needs_route(source, target, "PDF renderer")
    original_hash = _digest(source)
    output = convert_file(source, target, tmp_path / "output")
    with Image.open(output) as rendered:
        rendered.verify()
        assert rendered.format == pillow_type
    assert _digest(source) == original_hash
    _assert_no_stage(output.parent)


def test_pdf_multipage_tiff_preserves_page_count(tmp_path: Path) -> None:
    source = _make_pdf(tmp_path / "two page.pdf", pages=2)
    _needs_route(source, "tiff", "PDF renderer")
    source_hash = _digest(source)
    output = convert_file(source, "tiff", tmp_path / "out")
    with Image.open(output) as raster:
        assert raster.format == "TIFF"
        assert raster.n_frames == 2
        for index in range(2):
            raster.seek(index)
            raster.load()
            assert raster.width > 0 and raster.height > 0
    assert _digest(source) == source_hash
    _assert_no_stage(output.parent)


def test_pdf_midrender_cancel_keeps_no_partial_zip(tmp_path: Path) -> None:
    source = _make_pdf(tmp_path / "three pages.pdf", pages=3)
    _needs_route(source, "pngzip", "PDF renderer")
    original_hash = _digest(source)
    destination = tmp_path / "output"
    cancellation = threading.Event()

    def stop_after_first_page(value: float) -> None:
        if value >= 0.3:
            cancellation.set()

    with pytest.raises(ConversionCancelled):
        convert_file(source, "pngzip", destination, cancel=cancellation,
                     progress=stop_after_first_page)
    assert cancellation.is_set(), "Renderer did not report first-page progress"
    assert _digest(source) == original_hash
    assert not list(destination.iterdir())
    _assert_no_stage(destination)


def test_pdf_unicode_collision_and_injected_publish_failure(tmp_path: Path, monkeypatch) -> None:
    source = _make_pdf(tmp_path / "Счёт ședință 中文.pdf", pages=2)
    _needs_route(source, "pngzip", "PDF renderer")
    destination = tmp_path / "publish"
    destination.mkdir()
    previous = destination / f"{source.stem}-pages.zip"
    previous.write_bytes(b"An existing user's output must survive")
    previous_hash, source_hash = _digest(previous), _digest(source)
    converted = convert_file(source, "pngzip", destination)
    assert converted.name == f"{source.stem}-pages (1).zip"
    assert _digest(previous) == previous_hash
    assert _digest(source) == source_hash
    converted_hash = _digest(converted)

    original_publish = engine._publish_output
    publication_calls = []

    def fail_publish(*args, **kwargs):
        publication_calls.append(True)
        raise OSError(errno.ENOSPC, "stage6 injected disk full at publish")

    monkeypatch.setattr(engine, "_publish_output", fail_publish)
    with pytest.raises(ConversionError, match="disk full|write converted|space"):
        convert_file(source, "pngzip", destination)
    assert publication_calls, "Document conversion bypassed safe held-handle publisher"
    assert _digest(previous) == previous_hash
    assert _digest(converted) == converted_hash
    assert _digest(source) == source_hash
    assert sorted(p.name for p in destination.iterdir()) == sorted([previous.name, converted.name])
    _assert_no_stage(destination)
    monkeypatch.setattr(engine, "_publish_output", original_publish)


@pytest.mark.parametrize("kind,target", [("pdf", "pngzip"), ("office", "pdf")])
def test_document_cancelled_at_publication_boundary(tmp_path: Path, monkeypatch, kind: str, target: str):
    source = (
        _make_pdf(tmp_path / "pages.pdf", pages=2) if kind == "pdf"
        else _make_docx(tmp_path / "report.docx")
    )
    _needs_route(source, target, "Document provider")
    original_hash = _digest(source)
    output_dir = tmp_path / "out"
    cancellation = threading.Event()
    publisher = engine._publish_output
    reached = []

    def cancel_in_publisher(*args, **kwargs):
        reached.append(True)
        cancellation.set()
        return publisher(*args, **kwargs)

    monkeypatch.setattr(engine, "_publish_output", cancel_in_publisher)
    with pytest.raises(ConversionCancelled):
        convert_file(source, target, output_dir, cancel=cancellation)
    assert reached, "Document conversion did not use the guarded held-handle publication"
    assert _digest(source) == original_hash
    assert not list(output_dir.glob("*.zip"))
    assert not list(output_dir.glob("*.pdf"))
    _assert_no_stage(output_dir)


def test_office_to_pdf_real_provider_utf8_and_collision(tmp_path: Path) -> None:
    source = _make_docx(tmp_path / "office raport 中文 ședință.docx")
    _needs_route(source, "pdf", "Installed LibreOffice provider")
    destination = tmp_path / "converted"
    destination.mkdir()
    previous = destination / f"{source.stem}.pdf"
    previous.write_bytes(b"Existing destination sentinel")
    old_hash, original_hash = _digest(previous), _digest(source)
    output = convert_file(source, "pdf", destination)
    assert output != previous and output.name == f"{source.stem} (1).pdf"
    assert output.stat().st_size >= 128
    assert output.read_bytes().startswith(b"%PDF-")
    assert b"%%EOF" in output.read_bytes()[-256:]
    assert _digest(previous) == old_hash
    assert _digest(source) == original_hash
    _assert_no_stage(destination)


def test_pdf_with_uri_annotation_does_not_trigger_network(tmp_path: Path) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(2)
        listener.settimeout(0.35)
        url = f"http://127.0.0.1:{listener.getsockname()[1]}/pdf-annotation"
        source = _make_pdf(tmp_path / "annotated.pdf", uri=url)
        supported = get_targets(source)
        target = next((name for name in ("png", "pngzip") if name in supported), None)
        if target is None:
            pytest.skip("PDF renderer is not configured/advertised for one-page output")
        hash_before = _digest(source)
        output = convert_file(source, target, tmp_path / "out")
        assert output.is_file()
        if target == "pngzip":
            with zipfile.ZipFile(output) as archive:
                assert len(archive.namelist()) == 1
        else:
            with Image.open(output) as picture:
                picture.verify()
                assert picture.format == "PNG"
        with pytest.raises(socket.timeout):
            connected, _ = listener.accept()
            connected.close()
        assert _digest(source) == hash_before
