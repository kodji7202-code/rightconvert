"""Build the standalone PyInstaller RightConvertSetup.exe for Windows.

Run after building the onedir application:
    python scripts/build_setup.py

The setup EXE embeds a SHA-256 checked ZIP with every application file plus
the three existing PowerShell installer scripts. No network or elevation.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import zipfile


ROOT = Path(__file__).resolve().parent.parent
SETUP_DIR = ROOT / "build" / "setup"
PAYLOAD = SETUP_DIR / "RightConvertPayload.zip"
HASH_FILE = SETUP_DIR / "RightConvertPayload.sha256"
DIST = ROOT / "dist"
APP_DIR = DIST / "RightConvert"
SCRIPTS = ("install.ps1", "common.ps1", "uninstall.ps1")
CHUNK = 1024 * 1024


def check_no_links(path: Path) -> None:
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        raise ValueError(f"Cannot bundle symbolic links or junctions: {path}")


def list_payload_files() -> list[tuple[Path, str]]:
    if os.name != "nt":
        raise RuntimeError("The standalone Windows setup must be built on Windows.")
    if not (APP_DIR / "RightConvert.exe").is_file():
        raise FileNotFoundError(f"Missing built application: {APP_DIR / 'RightConvert.exe'}")
    check_no_links(APP_DIR)
    files: list[tuple[Path, str]] = []
    for parent, folders, filenames in os.walk(APP_DIR, followlinks=False):
        base = Path(parent)
        check_no_links(base)
        for folder in folders:
            check_no_links(base / folder)
        for filename in filenames:
            source = base / filename
            check_no_links(source)
            relative = source.relative_to(APP_DIR)
            archive_name = (Path("app") / relative).as_posix()
            files.append((source, archive_name))
    for name in SCRIPTS:
        source = ROOT / "installer" / name
        if not source.is_file():
            raise FileNotFoundError(f"Missing installer script: {source}")
        check_no_links(source)
        files.append((source, f"installer/{name}"))
    return sorted(files, key=lambda pair: pair[1].casefold())


def create_payload() -> tuple[int, int]:
    SETUP_DIR.mkdir(parents=True, exist_ok=True)
    files = list_payload_files()
    if not files:
        raise RuntimeError("No files to package.")
    # Rebuild, do not append: a previous archive must never supply stale files.
    with zipfile.ZipFile(
        PAYLOAD, mode="w", compression=zipfile.ZIP_DEFLATED,
        compresslevel=6, allowZip64=True,
    ) as archive:
        for source, archive_name in files:
            stat_before = source.stat()
            archive.write(source, arcname=archive_name)
            stat_after = source.stat()
            if (
                stat_before.st_size != stat_after.st_size
                or stat_before.st_mtime_ns != stat_after.st_mtime_ns
            ):
                raise RuntimeError(f"Source changed while packaging: {source}")
    with zipfile.ZipFile(PAYLOAD) as archive:
        invalid = archive.testzip()
        if invalid is not None:
            raise RuntimeError(f"Damaged payload entry: {invalid}")
    digest = hashlib.sha256()
    with PAYLOAD.open("rb") as stream:
        for block in iter(lambda: stream.read(CHUNK), b""):
            digest.update(block)
    HASH_FILE.write_text(digest.hexdigest() + "\n", encoding="ascii")
    return len(files), PAYLOAD.stat().st_size


def build_executable() -> Path:
    # Process argument list avoids quoting bugs on Unicode/spaced Windows paths.
    command = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", "RightConvertSetup",
        "--distpath", str(DIST),
        "--workpath", str(SETUP_DIR / "pyinstaller"),
        "--specpath", str(SETUP_DIR),
        "--add-data", f"{PAYLOAD}{os.pathsep}.",
        "--add-data", f"{HASH_FILE}{os.pathsep}.",
    ]
    icon = ROOT / "assets" / "RightConvert.ico"
    if icon.is_file():
        command += ["--icon", str(icon)]
    command += [str(ROOT / "installer" / "setup_gui.py")]
    subprocess.run(command, cwd=ROOT, check=True)
    result = DIST / "RightConvertSetup.exe"
    if not result.is_file() or result.stat().st_size == 0:
        raise RuntimeError(f"Missing PyInstaller setup executable: {result}")
    return result


def main(argv: list[str] | None = None) -> int:
    global APP_DIR, DIST, SETUP_DIR, PAYLOAD, HASH_FILE
    parser = argparse.ArgumentParser(description="Build bundled RightConvertSetup.exe")
    parser.add_argument(
        "--archive-only", action="store_true",
        help="Create and verify the embedded archive without running PyInstaller",
    )
    parser.add_argument("--app-dir", type=Path,
                        help="Optional isolated app folder (defaults to dist/RightConvert)")
    parser.add_argument("--dist-dir", type=Path,
                        help="Optional isolated output folder for Setup.exe (defaults to dist)")
    parser.add_argument("--setup-dir", type=Path,
                        help="Optional isolated intermediate folder (defaults to build/setup)")
    args = parser.parse_args(argv)
    if args.app_dir:
        APP_DIR = args.app_dir.expanduser().resolve(strict=True)
    if args.dist_dir:
        DIST = args.dist_dir.expanduser().resolve()
    if args.setup_dir:
        SETUP_DIR = args.setup_dir.expanduser().resolve()
        PAYLOAD = SETUP_DIR / "RightConvertPayload.zip"
        HASH_FILE = SETUP_DIR / "RightConvertPayload.sha256"
    if args.app_dir and (not APP_DIR.is_dir() or APP_DIR.is_symlink()):
        raise ValueError("Isolated app must be a real directory")
    if args.dist_dir and str(DIST).casefold() == str(ROOT / "dist").casefold():
        raise ValueError("Explicit isolated output must not target the normal distribution folder")
    files, size = create_payload()
    print(f"Built payload: {PAYLOAD} ({size:,} bytes; {files} files)", flush=True)
    print(f"Payload SHA-256: {HASH_FILE.read_text(encoding='ascii').strip()}", flush=True)
    if not args.archive_only:
        result = build_executable()
        digest = hashlib.sha256(result.read_bytes()).hexdigest()
        print(f"Built installer: {result} ({result.stat().st_size:,} bytes)", flush=True)
        print(f"Installer SHA-256: {digest}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
