"""Read-only RightConvert release inventory.

Usage (Windows/Python 3.12):
    python scripts/generate_sbom.py --summary
    python scripts/generate_sbom.py --json > sbom-local.json

No files, binaries, registry entries, or certificates are modified. Package
metadata below describes the *build host*, not necessarily a future build.
Review license provenance manually; file names alone do not prove licensing.
"""

from __future__ import annotations

import argparse
import hashlib
from importlib import metadata
import json
from pathlib import Path
import sys
import zipfile
from package_release import VERSION


PROJECT = Path(__file__).resolve().parents[1]
RUNTIME = PROJECT / "dist" / "RightConvert"
RELEASE = PROJECT / "release"
WINDOWS_ARCHIVE = f"RightConvert-{VERSION}-win-x64.zip"
SOURCE_ARCHIVE = f"RightConvert-{VERSION}-source.zip"
NOTICES = (
    "Pillow-LICENSE.txt",
    "NumPy-LICENSE.txt",
    "Python-LICENSE.txt",
    "PyInstaller-COPYING.txt",
    "README.txt",
    "cryptography-LICENSE.txt",
    "cryptography-APACHE.txt",
    "cryptography-BSD.txt",
)
BUILD_PACKAGES = ("Pillow", "numpy", "cryptography", "PyInstaller", "pyinstaller-hooks-contrib", "pip", "setuptools")
NATIVE_SUFFIXES = frozenset((".dll", ".exe", ".pyd"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def tag_binary(relative: str) -> str:
    path = relative.lower().replace("\\", "/")
    name = path.rsplit("/", 1)[-1]
    if name in {"rightconvert.exe", "rightconvertclassic.dll", "rightconvertexplorer.dll"}:
        return "RightConvert-owned (product license: not selected)"
    if name == "python312.dll" or path.startswith("_internal/python"):
        return "CPython runtime (PSF)"
    if name in {"tcl86t.dll", "tk86t.dll"}:
        return "Tcl/Tk (license.terms)"
    if name in {"libcrypto-3.dll", "libssl-3.dll"}:
        return "OpenSSL 3 (Apache-2.0)"
    if name == "libffi-8.dll":
        return "libffi (MIT-style; exact source provenance to confirm)"
    if name == "zlib1.dll":
        return "zlib (zlib license; exact source provenance to confirm)"
    if name.startswith("vcruntime") or name.startswith("msvcp") or name == "ucrtbase.dll" or name.startswith("api-ms-win-"):
        return "Microsoft runtime/API-set (redistribution rights to confirm)"
    if path.startswith("_internal/numpy") or "/numpy." in path or "openblas" in name:
        return "NumPy / vendored OpenBLAS (see NumPy-LICENSE)"
    if path.startswith("_internal/pil/"):
        return "Pillow (MIT-CMU + compiled codecs; review wheel provenance)"
    if path.startswith("_internal/"):
        return "CPython or transitive dependency (verify origin/license)"
    return "Unclassified native file (review required)"


def collect_release_archive(path: Path, expected_notices: tuple[str, ...]) -> dict:
    if not path.is_file():
        return {"present": False}
    data = {"present": True, "bytes": path.stat().st_size, "sha256": sha256(path)}
    with zipfile.ZipFile(path) as archive:
        members = archive.namelist()
        data["entries"] = len(members)
        data["ffmpeg_binaries"] = sorted(
            name for name in members if name.lower().endswith(("/ffmpeg.exe", "/ffprobe.exe"))
        )
        data["notice_files"] = sorted(
            name for name in members if "/thirdpartynotices/" in f"/{name.lower()}/"
        )
        data["missing_notices"] = [
            name for name in expected_notices
            if not any(member.endswith("/ThirdPartyNotices/" + name) for member in members)
        ] if "-win-" in path.name else []
    return data


def build_inventory() -> dict:
    result: dict = {
        "schema": "rightconvert-inventory-v1",
        "scope": "Local files currently present; not SPDX/CycloneDX certification",
        "release_root": "dist/RightConvert",
        "python_build_host": sys.version.split()[0],
        "build_host_packages": {},
        "runtime": {"present": RUNTIME.is_dir()},
        "release": {},
    }
    for package in BUILD_PACKAGES:
        try:
            result["build_host_packages"][package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            result["build_host_packages"][package] = None
    if RUNTIME.is_dir():
        entries = []
        for entry in sorted(RUNTIME.rglob("*"), key=lambda file: file.as_posix().lower()):
            if not entry.is_file():
                continue
            relative = entry.relative_to(RUNTIME).as_posix()
            record = {
                "path": relative,
                "bytes": entry.stat().st_size,
                "sha256": sha256(entry),
            }
            if entry.suffix.lower() in NATIVE_SUFFIXES:
                record["license_review_group"] = tag_binary(relative)
            entries.append(record)
        result["runtime"]["file_count"] = len(entries)
        result["runtime"]["native_count"] = sum("license_review_group" in file for file in entries)
        result["runtime"]["native_files"] = [
            file for file in entries if "license_review_group" in file
        ]
        result["runtime"]["notices"] = {
            name: next(
                ({"sha256": file["sha256"], "bytes": file["bytes"]}
                 for file in entries if file["path"] == "ThirdPartyNotices/" + name),
                None,
            )
            for name in NOTICES
        }
        result["runtime"]["tcl_notice_present"] = any(
            file["path"] == "_internal/_tk_data/license.terms" for file in entries
        )
        result["runtime"]["ffmpeg_binaries"] = sorted(
            file["path"] for file in entries if file["path"].lower().endswith(
                ("ffmpeg.exe", "ffprobe.exe")
            )
        )
        result["runtime"]["all_file_hashes"] = {
            file["path"]: file["sha256"] for file in entries
        }
    for prefix in (WINDOWS_ARCHIVE, SOURCE_ARCHIVE):
        result["release"][prefix] = collect_release_archive(RELEASE / prefix, NOTICES)
    for name in ("RightConvertSetup.exe",):
        path = PROJECT / "dist" / name
        result["release"][name] = (
            {"present": True, "bytes": path.stat().st_size, "sha256": sha256(path)}
            if path.is_file() else {"present": False}
        )
    return result


def summary(data: dict) -> str:
    runtime = data["runtime"]
    binary = data["release"][WINDOWS_ARCHIVE]
    source = data["release"][SOURCE_ARCHIVE]
    lines = [
        "RightConvert SBOM (read-only local snapshot)",
        "Build-host Python: " + data["python_build_host"],
        "Build-host packages: " + ", ".join(
            f"{name}={version or 'MISSING'}" for name, version in data["build_host_packages"].items()
        ),
        f"Frozen bundle: {runtime.get('file_count', 0)} files, {runtime.get('native_count', 0)} native",
        "Notice files: " + ", ".join(
            f"{name}={'yes' if info else 'MISSING'}"
            for name, info in runtime.get("notices", {}).items()
        ),
        "Tcl/Tk notice: " + ("yes" if runtime.get("tcl_notice_present") else "MISSING"),
        "Bundled FFmpeg/FFprobe: " + (", ".join(runtime.get("ffmpeg_binaries", [])) or "none"),
        f"Binary ZIP: {binary.get('entries', 0)} entries; missing notices: {binary.get('missing_notices', [])}",
        f"Source ZIP: {source.get('entries', 0)} entries",
        "Signature state and native component origins require Windows verification; "
        "see docs/SBOM.md. Hashes/paths: use --json.",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--summary", action="store_true", help="Print brief human-readable inventory")
    group.add_argument("--json", action="store_true", help="Print complete sorted JSON with SHA-256 hashes")
    args = parser.parse_args()
    report = build_inventory()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    else:
        print(summary(report))


if __name__ == "__main__":
    main()
