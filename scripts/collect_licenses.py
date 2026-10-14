"""Bundle exact third-party license notices for the frozen Python application.

This does not determine which legal terms apply to external FFmpeg builds.
Audio/video tools are user-provided and never copied into our installer.
"""

from __future__ import annotations

import base64
import csv
import hashlib
from importlib import metadata
import io
from pathlib import Path
import shutil
import sys


ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "dist" / "RightConvert" / "ThirdPartyNotices"


def locate_license(distribution: str, tail: str) -> Path:
    dist = metadata.distribution(distribution)
    matches = [item for item in (dist.files or []) if str(item).replace("\\", "/").endswith(tail)]
    if len(matches) != 1:
        raise RuntimeError(f"Expected exactly one license for {distribution}: {tail}")
    path = Path(dist.locate_file(matches[0]))
    if not path.is_file() or path.stat().st_size < 10:
        raise RuntimeError(f"Missing installed {distribution} notice: {path}")
    return path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_wheel_member(path: Path, digest: str) -> None:
    """Validate an installed wheel member against its pip RECORD hash."""
    if not path.is_file() or path.stat().st_size < 10:
        raise RuntimeError(f"Missing or empty installed pypdfium2 wheel file: {path}")
    if not digest.startswith("sha256="):
        raise RuntimeError(f"Missing wheel SHA-256 for: {path.name}")
    try:
        encoded = digest.removeprefix("sha256=")
        expected = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True).hex()
    except (ValueError, base64.binascii.Error) as exc:
        raise RuntimeError(f"Invalid wheel digest for: {path.name}") from exc
    if sha256_file(path) != expected:
        raise RuntimeError(f"Installed pypdfium2 wheel file differs from its RECORD: {path.name}")


def pdfium_notices(bundle: Path) -> tuple[dict[Path, Path], str]:
    """Select all license texts from the exact installed pypdfium2 PDFium wheel.

    A PDFium DLL without corresponding wheel metadata is a release blocker: its
    native dependency licenses cannot be inferred from a different PDFium build.
    """
    binaries = sorted(path for path in bundle.rglob("*") if path.is_file() and path.name.lower() == "pdfium.dll")
    if not binaries:
        return {}, "PDFium is not included in this application bundle.\n"

    try:
        dist = metadata.distribution("pypdfium2")
    except metadata.PackageNotFoundError as exc:
        raise RuntimeError("Bundled pdfium.dll requires installed pypdfium2 wheel metadata and licenses") from exc

    # importlib.metadata.Distribution.files can drop missing installed files.
    # Read the original RECORD text so a deleted dependency notice fails closed.
    wheel_record = dist.read_text("RECORD")
    if not wheel_record:
        raise RuntimeError("pypdfium2 wheel RECORD is absent; cannot audit binary dependencies")
    wheel_files = [(row[0].replace("\\", "/"), row[1]) for row in csv.reader(io.StringIO(wheel_record))
                   if len(row) >= 2]
    if len({name for name, _ in wheel_files}) != len(wheel_files):
        raise RuntimeError("Duplicate pypdfium2 wheel RECORD entries")
    wheel_binary = [(name, digest) for name, digest in wheel_files if name.endswith("pypdfium2_raw/pdfium.dll")]
    if len(wheel_binary) != 1:
        raise RuntimeError("Expected exactly one pypdfium2 wheel PDFium DLL in package RECORD")
    wheel_path = Path(dist.locate_file(wheel_binary[0][0]))
    verify_wheel_member(wheel_path, wheel_binary[0][1])

    wheel_digest = sha256_file(wheel_path)
    for binary in binaries:
        if sha256_file(binary) != wheel_digest:
            raise RuntimeError(f"Bundled {binary.name} differs from installed pypdfium2 wheel; audit its own licenses")

    notices: dict[Path, Path] = {}
    for wheel_name, recorded_digest in wheel_files:
        marker = ".dist-info/licenses/"
        if marker not in wheel_name:
            continue
        relative = Path(wheel_name.split(marker, 1)[1])
        if not relative.parts or any(part in (".", "..") for part in relative.parts):
            raise RuntimeError(f"Unsafe pypdfium2 wheel license path: {wheel_name}")
        source = Path(dist.locate_file(wheel_name))
        verify_wheel_member(source, recorded_digest)
        notices[Path("pypdfium2") / relative] = source

    names = {path.name for path in notices}
    required = {"Apache-2.0.txt", "BSD-3-Clause.txt", "pdfium.txt", "pdfium-binaries.txt"}
    if not required.issubset(names) or not any("BUILD_LICENSES" in item.parts for item in notices):
        raise RuntimeError("pypdfium2 wheel is missing its PDFium/build dependency license set")

    details = (
        f"pypdfium2 {dist.version}: Apache-2.0 OR BSD-3-Clause for bindings; "
        "PDFium BSD-style and its native third-party licenses are copied verbatim\n"
        f"from installed wheel metadata ({len(notices)} notices, pdfium.dll SHA-256 {wheel_digest}).\n"
        "See pypdfium2/LICENSES/ and pypdfium2/data/*/BUILD_LICENSES/ in this folder.\n"
    )
    return notices, details


def main() -> None:
    bundle = ROOT / "dist" / "RightConvert"
    if not (bundle / "RightConvert.exe").is_file():
        raise SystemExit("Build RightConvert first")
    sources = {
        "Pillow-LICENSE.txt": locate_license("pillow", ".dist-info/licenses/LICENSE"),
        "PyInstaller-COPYING.txt": locate_license("pyinstaller", ".dist-info/licenses/COPYING.txt"),
        "Python-LICENSE.txt": Path(sys.base_prefix) / "LICENSE.txt",
        "cryptography-LICENSE.txt": locate_license("cryptography", ".dist-info/licenses/LICENSE"),
        "cryptography-APACHE.txt": locate_license("cryptography", ".dist-info/licenses/LICENSE.APACHE"),
        "cryptography-BSD.txt": locate_license("cryptography", ".dist-info/licenses/LICENSE.BSD"),
    }
    # NumPy is not a dependency. Ship its notice exactly when PyInstaller froze
    # it from the build environment; a bundled copy without metadata fails.
    if any((base / "numpy").is_dir() for base in (bundle, bundle / "_internal")):
        sources["NumPy-LICENSE.txt"] = locate_license("numpy", ".dist-info/LICENSE.txt")
    pdfium_sources, pdfium_details = pdfium_notices(bundle)
    for name, path in pdfium_sources.items():
        sources[name] = path
    DEST.mkdir(parents=True, exist_ok=True)
    for name, path in sources.items():
        if not path.is_file():
            raise RuntimeError(f"Missing license: {name}")
        output = DEST / name
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, output)
    (DEST / "README.txt").write_text(
        "RightConvert distribution: third-party notices copied from the exact build environment.\n"
        "Pillow: MIT-CMU;"
        + (" NumPy: BSD and bundled subcomponent notices, see license text;"
           if "NumPy-LICENSE.txt" in sources else "")
        + "\n"
        "Python/Tcl/Tk: PSF/Tcl/Tk; PyInstaller: GPL with bootloader exception.\n"
        "cryptography: Apache-2.0 OR BSD-3-Clause; both terms included in notices.\n"
        + pdfium_details
        + "OpenSSL 3.0.16: bundled libcrypto-3.dll and libssl-3.dll from the Python Windows runtime;\n"
        "OpenSSL project authors, Apache License 2.0 (full Apache 2.0 terms are reproduced\n"
        "in Python-LICENSE.txt). Official terms: https://openssl-library.org/source/license/\n"
        "FFmpeg and FFprobe are not included; check the license of your own binaries.\n"
        "No redistribution rights for components beyond their actual license terms are implied.\n",
        encoding="utf-8",
    )
    for item in sorted(path for path in DEST.rglob("*") if path.is_file()):
        print(f"Notice: {item.relative_to(DEST)} ({item.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
