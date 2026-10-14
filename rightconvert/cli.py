"""Entry points shared by the interactive UI and Windows Explorer."""

from __future__ import annotations

import argparse
from pathlib import Path


def parser() -> argparse.ArgumentParser:
    app = argparse.ArgumentParser(prog="RightConvert", description="Offline local file conversion")
    app.add_argument("--files", metavar="PATH", nargs="+", default=[], help="Files chosen in File Explorer")
    app.add_argument("--target", metavar="FORMAT", default=None,
                     help="Preselect this supported target in the graphical file queue")
    app.add_argument("--auto-start", action="store_true",
                     help="Start supported selected files after inspection; requires --target and --files")
    app.add_argument("--direct", action="store_true",
                     help="Convert selected files directly without opening the GUI; requires --target and --files")
    app.add_argument("--notify", action="store_true",
                     help="Show an error dialog for a failed direct Explorer conversion")
    app.add_argument("--self-check", metavar="REPORT", default=None,
                     help="Write a JSON diagnostic report (package identity, data folder, "
                          "LibreOffice discovery) and exit without opening the GUI")
    return app


def _self_check(report: str) -> int:
    """GUI-free diagnostics used by the Store MSIX CI and for support.

    Contains no file contents and no user documents; only runtime facts.
    """
    import json
    import sys
    from . import __version__
    from .config import data_dir, technical_logger
    from .office_provider import find_libreoffice
    from .runtime import package_family_name, package_full_name

    log = technical_logger()
    log.info("Self-check requested")
    for handler in log.handlers:
        handler.flush()
    folder = data_dir()
    facts = {
        "ok": True,
        "version": __version__,
        "frozen": bool(getattr(sys, "frozen", False)),
        "executable": sys.executable,
        "packaged": package_full_name() is not None,
        "package_full_name": package_full_name(),
        "package_family_name": package_family_name(),
        "data_dir": str(folder),
        "technical_log_written": (folder / "logs" / "technical.log").is_file(),
        "libreoffice": find_libreoffice(),
    }
    target = Path(report)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


def _show_direct_error(message: str) -> None:
    import os
    if os.name == "nt":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, "RightConvert", 0x10)


def _convert_direct(files: list[str], target: str, *, notify: bool = False) -> int:
    """Explorer's deliberate, GUI-free quick action with all-or-none preflight.

    Unsupported selections never cause partial batches. If an encoder fails
    mid-batch, the existing no-replace engine preserves every original and
    already completed output, and the failure is recorded in a local log.
    """
    from .config import read_settings, technical_logger
    from .engine import convert_file, get_targets

    log = technical_logger()
    if not 1 <= len(files) <= 1000:
        log.warning("Quick conversion rejected an empty or oversized selection")
        if notify:
            _show_direct_error("Selectează între 1 și 1000 de fișiere.")
        return 2
    preferences = read_settings()
    ffmpeg = preferences.get("ffmpeg_path") or None
    office = preferences.get("office_path") or None
    paths = [Path(item) for item in files]
    try:
        for path in paths:
            if target not in get_targets(path, ffmpeg_path=ffmpeg, office_path=office):
                log.warning("Quick conversion incompatible source: %s -> %s", path.suffix, target)
                if notify:
                    _show_direct_error("Unele fișiere nu pot fi convertite în formatul ales. Verifică formatul și motoarele locale instalate.")
                return 2
        for path in paths:
            convert_file(path, target, path.parent, ffmpeg_path=ffmpeg, office_path=office)
    except Exception as exc:
        # The frozen app is windowed and has no console. Do not log file
        # contents, credentials or full user paths in a generic error.
        log.error("Quick conversion failed: %s", type(exc).__name__)
        if notify:
            _show_direct_error("Conversia nu s-a putut finaliza. Originalele au fost păstrate. Consultă jurnalul RightConvert.")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    cli = parser()
    args = cli.parse_args(argv)
    if args.self_check is not None:
        return _self_check(args.self_check)
    if args.auto_start and args.direct:
        cli.error("--auto-start and --direct cannot be used together")
    if args.notify and not args.direct:
        cli.error("--notify requires --direct")
    if (args.auto_start or args.direct) and (not args.files or args.target is None):
        cli.error("--auto-start/--direct requires both --files and --target")
    preferred = None
    if args.target is not None:
        if not args.files:
            cli.error("--target requires --files")
        from .formats import normalize_target, UnsupportedFormatError
        try:
            preferred = normalize_target(args.target)
        except UnsupportedFormatError as exc:
            cli.error(str(exc))
    if args.direct:
        assert preferred is not None
        return _convert_direct(args.files, preferred, notify=args.notify)
    from .app import run

    if preferred is None and not args.auto_start:
        # Preserve the original call contract for existing automation and tests.
        run([Path(path) for path in args.files])
    else:
        run([Path(path) for path in args.files], preferred_target=preferred, auto_start=args.auto_start)
    return 0
