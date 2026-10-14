"""Build reproducible source and Windows ZIP artifacts without bundled FFmpeg."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.3.0"
OUTPUT = ROOT / "release"


def paths_for_source():
    for name in ("README.md", "pyproject.toml", "launch.py", "build.ps1", ".gitignore"):
        yield ROOT / name
    icon = ROOT / "assets" / "RightConvert.ico"
    if icon.is_file():
        yield icon
    for directory in ("rightconvert", "scripts", "tests", "docs", "installer", "shell/classic", "shell/windows11"):
        for path in sorted((ROOT / directory).rglob("*")):
            if not path.is_file() or any(part in {"__pycache__", "out", "build", "dist", "release", ".pytest_cache"} for part in path.relative_to(ROOT).parts):
                continue
            if path.suffix.lower() in {".py", ".cpp", ".h", ".md", ".ps1", ".def", ".txt", ".xml", ".nsi"} or path.name in {"CMakeLists.txt", ".gitignore", "STAGE4_MATRIX_RESULTS.json", "STAGE5_FORMAT_MATRIX_RESULTS.json", "STAGE6_FORMAT_MATRIX_RESULTS.json"} or (path.parent.name == "evidence" and path.suffix.lower() == ".png"):
                yield path


def write_zip(destination: Path, files: list[Path]) -> None:
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED, compresslevel=8) as archive:
        for path in files:
            archive.write(path, path.relative_to(ROOT).as_posix())
    with zipfile.ZipFile(destination) as archive:
        bad = archive.testzip()
        if bad:
            raise RuntimeError(f"Corrupt ZIP entry: {bad}")


def main() -> None:
    app = ROOT / "dist" / "RightConvert" / "RightConvert.exe"
    if not app.is_file():
        raise SystemExit("Build application with build.ps1 before packaging")
    OUTPUT.mkdir(exist_ok=True)
    source = OUTPUT / f"RightConvert-{VERSION}-source.zip"
    write_zip(source, list(dict.fromkeys(paths_for_source())))

    contents = sorted(path for path in (ROOT / "dist" / "RightConvert").rglob("*") if path.is_file())
    setup_exe = ROOT / "dist" / "RightConvertSetup.exe"
    if setup_exe.is_file():
        contents.append(setup_exe)
    contents += [ROOT / "README.md", ROOT / "pyproject.toml"]
    contents += sorted(path for path in (ROOT / "docs").glob("*.md"))
    contents += sorted(path for path in (ROOT / "docs" / "evidence").glob("*.png") if path.is_file())
    evidence = ROOT / "docs" / "STAGE4_MATRIX_RESULTS.json"
    if evidence.is_file():
        contents.append(evidence)
    stage5_evidence = ROOT / "docs" / "STAGE5_FORMAT_MATRIX_RESULTS.json"
    if stage5_evidence.is_file():
        contents.append(stage5_evidence)
    stage6_evidence = ROOT / "docs" / "STAGE6_FORMAT_MATRIX_RESULTS.json"
    if stage6_evidence.is_file():
        contents.append(stage6_evidence)
    contents += sorted(path for path in (ROOT / "installer").glob("*.ps1"))
    contents += sorted(path for path in (ROOT / "installer").glob("*.md"))
    modern_dll = ROOT / "shell" / "windows11" / "out" / "x64" / "Release" / "RightConvertExplorer.dll"
    if modern_dll.is_file():
        contents += [modern_dll, ROOT / "shell" / "windows11" / "AppxManifest.xml"]
    windows = OUTPUT / f"RightConvert-{VERSION}-win-x64.zip"
    write_zip(windows, contents)
    manifest = OUTPUT / "SHA256SUMS.txt"
    manifest.write_text("".join(f"{sha256(path.read_bytes()).hexdigest()}  {path.name}\n" for path in (source, windows)), encoding="utf-8")
    for name in (source, windows, manifest):
        print(f"{name.name}: {name.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
