"""Save a tightly scoped screenshot of the foreground RightConvert QA window.

Run only when a RightConvert window showing disposable test fixtures is already
open in the foreground. This script fails closed rather than capturing other
applications or the user's full desktop.

    python scripts/capture_ui_evidence.py --label format-matrix
"""

from __future__ import annotations

import argparse
from datetime import datetime
import ctypes
from ctypes import wintypes
from pathlib import Path
import sys

from PIL import ImageGrab


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "evidence"


class Rect(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG), ("top", wintypes.LONG),
        ("right", wintypes.LONG), ("bottom", wintypes.LONG),
    ]


def main() -> int:
    if sys.platform != "win32":
        raise SystemExit("This screenshot verification tool requires Windows.")
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True, help="Short QA label without personal data")
    args = parser.parse_args()
    label = args.label
    if not label or len(label) > 48 or not all(ch.isascii() and (ch.isalnum() or ch in "-_") for ch in label):
        parser.error("--label must be ASCII letters, digits, hyphens or underscores (1-48 chars)")

    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        raise SystemExit("No foreground window.")
    title = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, title, len(title))
    if not title.value.startswith("RightConvert"):
        raise SystemExit("Foreground window is not RightConvert; refusing screen capture.")
    window_rect = Rect()
    if not user32.GetWindowRect(hwnd, ctypes.byref(window_rect)):
        raise SystemExit("Could not determine verified RightConvert window bounds.")
    bbox = (
        window_rect.left, window_rect.top,
        window_rect.right, window_rect.bottom,
    )
    width, height = bbox[2] - bbox[0], bbox[3] - bbox[1]
    if width < 350 or height < 300 or width > 4096 or height > 3000:
        raise SystemExit("Unexpected window dimensions; refusing capture.")

    OUTPUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    destination = OUTPUT / f"{stamp}-{label}.png"
    if destination.exists():
        raise SystemExit("Screenshot target already exists.")
    snapshot = ImageGrab.grab(bbox=bbox)
    snapshot.save(destination, format="PNG")
    print(f"Captured only foreground RightConvert window: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
