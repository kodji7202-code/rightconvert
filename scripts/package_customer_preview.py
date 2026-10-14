"""Make a binary-only, explicitly UNSIGNED/NOT-FOR-SALE customer ZIP preview.

The private developer/source archives remain separate.  A real commercial
artifact requires the publisher's finalized EULA, signature and approval.
"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import zipfile

if __package__:
    from .collect_licenses import pdfium_notices
    from .package_release import VERSION
else:
    from collect_licenses import pdfium_notices
    from package_release import VERSION


ROOT = Path(__file__).resolve().parents[1]
NOTICE_DIR = ROOT / "dist" / "RightConvert" / "ThirdPartyNotices"
INSTALLER = ROOT / "dist" / "RightConvertSetup.exe"
GUIDE = ROOT / "docs" / "CUSTOMER_PACKAGE_PREVIEW.txt"
OUTPUT = ROOT / "release" / "internal-preview" / f"RightConvert-{VERSION}-NOT-FOR-SALE.zip"

REQUIRED_NOTICES = (
    "Pillow-LICENSE.txt",
    "Python-LICENSE.txt",
    "PyInstaller-COPYING.txt",
    "cryptography-LICENSE.txt",
    "cryptography-APACHE.txt",
    "cryptography-BSD.txt",
    "README.txt",
)

# Notices required only when the frozen bundle actually ships the package.
# NumPy is not a RightConvert dependency; PyInstaller bundles it only when it
# happens to be importable in the build environment (an optional import of
# Pillow/pypdfium2). Its notice must then be present, and is otherwise absent.
CONDITIONAL_NOTICES = {"NumPy-LICENSE.txt": "numpy"}


def bundled_packages(bundle: Path) -> set[str]:
    """Top-level Python packages frozen into a PyInstaller one-dir bundle."""
    found = set()
    for base in (bundle, bundle / "_internal"):
        for package in CONDITIONAL_NOTICES.values():
            if (base / package).is_dir():
                found.add(package)
    return found


def required_notices(bundle: Path) -> tuple[str, ...]:
    present = bundled_packages(bundle)
    return REQUIRED_NOTICES + tuple(
        name for name, package in CONDITIONAL_NOTICES.items() if package in present
    )


def verified_pdfium_notices(bundle: Path, notice_dir: Path) -> dict[str, Path]:
    """Require every exact wheel notice if the frozen bundle contains PDFium.

    The collector matches bundled pdfium.dll to the installed wheel by SHA-256.
    Reading the expected files directly from that same wheel also detects
    replaced or stale license texts, rather than just checking their names.
    """
    expected, provenance = pdfium_notices(bundle)
    nested = notice_dir / "pypdfium2"
    for directory in (notice_dir, nested):
        if directory.is_symlink() or (hasattr(directory, "is_junction") and directory.is_junction()):
            raise ValueError(f"PDFium notice directory must be local and regular: {directory.name}")
    actual = {
        path.relative_to(notice_dir).as_posix()
        for path in nested.rglob("*") if path.is_file() or path.is_symlink()
    } if nested.exists() else set()
    wanted = {path.as_posix() for path in expected}
    if actual != wanted:
        raise ValueError(f"pypdfium2/PDFium notices missing or unexpected: "
                         f"missing={sorted(wanted - actual)}, extra={sorted(actual - wanted)}")
    if not expected:
        return {}

    readme = notice_dir / "README.txt"
    if not readme.is_file() or provenance not in readme.read_text(encoding="utf-8"):
        raise ValueError("pypdfium2/PDFium version, DLL hash or license-count provenance missing")

    for relative, wheel_source in expected.items():
        actual_path = notice_dir / relative
        for depth in range(1, len(relative.parts)):
            ancestor = notice_dir.joinpath(*relative.parts[:depth])
            if ancestor.is_symlink() or (hasattr(ancestor, "is_junction") and ancestor.is_junction()):
                raise ValueError(f"Linked pypdfium2 notice directory: {relative.as_posix()}")
        if (not actual_path.is_file() or actual_path.is_symlink()
                or (hasattr(actual_path, "is_junction") and actual_path.is_junction())):
            raise ValueError(f"Missing regular pypdfium2 notice: {relative.as_posix()}")
        if sha256(actual_path.read_bytes()).digest() != sha256(wheel_source.read_bytes()).digest():
            raise ValueError(f"Incorrect pypdfium2/PDFium notice content: {relative.as_posix()}")
    return {relative.as_posix(): notice_dir / relative for relative in expected}


def allowed_customer_entries(installer: Path, notice_dir: Path, guide: Path) -> dict[str, Path]:
    """Permit only an audited exact payload, never the source ZIP or key files."""
    mapping = {
        "RightConvertSetup.exe": installer,
        "NOT-FOR-SALE-README.txt": guide,
    }
    for name in required_notices(notice_dir.parent):
        mapping[f"ThirdPartyNotices/{name}"] = notice_dir / name
    for name, path in verified_pdfium_notices(notice_dir.parent, notice_dir).items():
        mapping[f"ThirdPartyNotices/{name}"] = path
    for name, path in mapping.items():
        if not path.is_file() or path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ValueError(f"Expected regular customer payload file: {name}")
        if not path.stat().st_size:
            raise ValueError(f"Empty customer payload file: {name}")
    return mapping


def build_preview(
    installer: Path = INSTALLER,
    notice_dir: Path = NOTICE_DIR,
    guide: Path = GUIDE,
    output: Path = OUTPUT,
) -> Path:
    mapping = allowed_customer_entries(installer, notice_dir, guide)
    # A manually invoked customer-preview build must validate the *embedded*
    # installer, too. The staged notices alone could otherwise be newer than
    # the actual installer a customer receives.
    if installer == INSTALLER and notice_dir == NOTICE_DIR:
        if __package__:
            from .check_release import verify_embedded_setup
        else:
            from check_release import verify_embedded_setup
        verify_embedded_setup(
            installer, ROOT / "build" / "setup" / "RightConvertPayload.zip",
            notice_dir.parent,
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.is_symlink():
        raise ValueError("Preview archive target must not be a symbolic link")
    # Caller-supplied output paths are only used from unit tests; routine
    # execution uses the fixed private preview path above.
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=8) as bundle:
        for name, path in mapping.items():
            bundle.write(path, name)
    with zipfile.ZipFile(output) as bundle:
        if bundle.testzip() is not None or set(bundle.namelist()) != set(mapping):
            raise ValueError("Invalid or unexpected customer preview contents")
        if any(n.lower().endswith((".py", ".pfx", ".pem", ".key", ".env", ".sql", ".db"))
               for n in bundle.namelist()):
            raise ValueError("Source, private keys or customer data in customer preview")
    print(f"PRIVATE PREVIEW ONLY — {output} ({output.stat().st_size:,} bytes)")
    print(f"SHA256: {sha256(output.read_bytes()).hexdigest()}")
    return output


if __name__ == "__main__":
    build_preview()
