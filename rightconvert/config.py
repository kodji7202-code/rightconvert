"""Privacy-conscious, per-user application settings and technical logging."""

from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import tempfile


APP_NAME = "RightConvert"


def data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / APP_NAME
    return Path.home() / ".rightconvert"


def read_settings() -> dict:
    try:
        raw = json.loads((data_dir() / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {"output_dir": "", "same_folder": True, "ffmpeg_path": ""}
    if not isinstance(raw, dict):
        return {}
    settings = {
        "output_dir": raw.get("output_dir") if isinstance(raw.get("output_dir"), str) else "",
        "same_folder": raw.get("same_folder") if isinstance(raw.get("same_folder"), bool) else True,
        "ffmpeg_path": raw.get("ffmpeg_path") if isinstance(raw.get("ffmpeg_path"), str) else "",
    }
    if isinstance(raw.get("office_path"), str):
        settings["office_path"] = raw["office_path"]
    return settings


def save_settings(settings: dict) -> None:
    directory = data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    safe = {key: settings[key] for key in ("output_dir", "same_folder", "ffmpeg_path", "office_path") if key in settings}
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=directory, prefix="settings-", suffix=".tmp", encoding="utf-8", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(safe, handle, ensure_ascii=False, indent=2)
        os.replace(temporary, directory / "settings.json")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def technical_logger() -> logging.Logger:
    log = logging.getLogger("rightconvert")
    if log.handlers:
        return log
    log.setLevel(logging.INFO)
    try:
        folder = data_dir() / "logs"
        # User-writable logging must never follow an existing file or folder
        # symlink/junction into an unrelated location. Logging is optional:
        # fail closed to NullHandler without affecting conversion on such paths.
        def is_redirect(path: Path) -> bool:
            for node in (*reversed(path.parents), path):
                try:
                    details = node.lstat()
                except FileNotFoundError:
                    continue
                if (node.is_symlink() or
                        getattr(details, "st_file_attributes", 0) & 0x400 or
                        (hasattr(node, "is_junction") and node.is_junction())):
                    return True
            return False

        if is_redirect(folder):
            raise OSError("Technical log folder is redirected")
        folder.mkdir(parents=True, exist_ok=True)
        log_file = folder / "technical.log"
        if is_redirect(log_file):
            raise OSError("Technical log path is redirected")
        handler = RotatingFileHandler(log_file, maxBytes=524288, backupCount=2, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(handler)
    except OSError:
        log.addHandler(logging.NullHandler())
    return log
