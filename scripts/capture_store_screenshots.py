"""Capture Microsoft Store listing screenshots of the RightConvert main window.

CI only (``.github/workflows/store-screenshots.yml``) on a disposable Windows
runner. It creates synthetic, non-personal fixture images, launches the built
app, sizes the main window to a Store-friendly size, and captures **only the
RightConvert window** (the same fail-closed rule as capture_ui_evidence.py:
the foreground window title must be RightConvert's, no other windows/dialogs).

    python scripts/capture_store_screenshots.py --exe dist\\RightConvert\\RightConvert.exe --out store-screenshots

Outputs PNGs that are at least 1366x768 (Partner Center minimum):
  01-queue-ready.png        files queued, target format chosen
  02-converting.png         conversion in progress (best effort)
  03-conversion-done.png    all files converted
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from pathlib import Path
import subprocess
import sys
import time

from PIL import Image, ImageDraw, ImageGrab, ImageStat

MIN_W, MIN_H = 1366, 768
MAIN_TITLE = "RightConvert  ·  Conversii locale"

if sys.platform != "win32":
    raise SystemExit("Windows only")

user32 = ctypes.windll.user32
dwmapi = ctypes.windll.dwmapi
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except (OSError, AttributeError):
    user32.SetProcessDPIAware()


class RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def windows_of(pid: int) -> list[tuple[int, str, str]]:
    found: list[tuple[int, str, str]] = []

    def cb(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            title = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, title, 256)
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls, 256)
            found.append((hwnd, title.value, cls.value))
        return True

    user32.EnumWindows(EnumProc(cb), 0)
    return found


def frame(hwnd: int) -> RECT:
    rect = RECT()
    # DWMWA_EXTENDED_FRAME_BOUNDS = 9: the visible frame, without the invisible resize border.
    if dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect)) != 0:
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect


def wait_main_window(proc: subprocess.Popen, seconds: float = 60) -> int:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if proc.poll() is not None:
            raise SystemExit(f"RightConvert exited early with code {proc.returncode}")
        for hwnd, title, _ in windows_of(proc.pid):
            if title == MAIN_TITLE:
                return hwnd
        time.sleep(0.3)
    raise SystemExit("RightConvert main window did not appear")


def place(hwnd: int, width: int, height: int) -> None:
    HWND_TOPMOST, SWP_SHOWWINDOW = -1, 0x0040
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, width, height, SWP_SHOWWINDOW)
    time.sleep(0.4)
    # Compensate the invisible borders so the *visible* frame is exactly width x height.
    outer = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(outer))
    vis = frame(hwnd)
    extra_w = (outer.right - outer.left) - (vis.right - vis.left)
    extra_h = (outer.bottom - outer.top) - (vis.bottom - vis.top)
    user32.SetWindowPos(hwnd, HWND_TOPMOST, -(vis.left - outer.left), 0,
                        width + extra_w, height + extra_h, SWP_SHOWWINDOW)
    user32.SetForegroundWindow(hwnd)
    time.sleep(1.2)


def capture(proc: subprocess.Popen, hwnd: int, destination: Path, canvas: tuple[int, int] | None) -> None:
    others = [(t, c) for h, t, c in windows_of(proc.pid) if h != hwnd and c == "#32770"]
    if others:
        raise SystemExit(f"Refusing capture: a dialog is open: {others}")
    title = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, title, 256)
    if not title.value.startswith("RightConvert"):
        raise SystemExit("Window is not RightConvert; refusing capture")
    r = frame(hwnd)
    shot = ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom), all_screens=True).convert("RGB")
    stat = ImageStat.Stat(shot)
    if max(stat.stddev) < 4:
        raise SystemExit("Captured image is blank (desktop locked or window not drawn)")
    if canvas:
        # Screen too small for a Store-size window: centre the real window on a canvas.
        cw, ch = canvas
        board = Image.new("RGB", (cw, ch), (236, 241, 248))
        pen = ImageDraw.Draw(board)
        for y in range(ch):
            shade = int(236 - 26 * y / ch)
            pen.line([(0, y), (cw, y)], fill=(shade, shade + 5, 248))
        board.paste(shot, ((cw - shot.width) // 2, (ch - shot.height) // 2))
        shot = board
    if shot.width < MIN_W or shot.height < MIN_H:
        raise SystemExit(f"Screenshot {shot.size} is below {MIN_W}x{MIN_H}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shot.save(destination, format="PNG", optimize=True)
    print(f"Saved {destination} {shot.size} {destination.stat().st_size} bytes")


def make_fixtures(folder: Path) -> list[Path]:
    """Synthetic landscape-like pictures; large enough that converting takes a moment."""
    folder.mkdir(parents=True, exist_ok=True)
    specs = [
        ("apus-la-mare.png", (4200, 2800), (255, 140, 60), (40, 60, 140)),
        ("munti-iarna.png", (4200, 2800), (200, 225, 255), (40, 80, 120)),
        ("logo-produs.png", (2400, 2400), (38, 111, 224), (10, 30, 80)),
        ("padure-toamna.bmp", (3600, 2400), (230, 160, 40), (60, 40, 20)),
        ("scanare-document.tiff", (3000, 4000), (250, 250, 245), (180, 180, 170)),
        ("fotografie-oras.png", (4200, 2800), (120, 130, 160), (20, 20, 40)),
    ]
    paths = []
    for name, size, top, bottom in specs:
        w, h = size
        img = Image.new("RGB", size)
        pen = ImageDraw.Draw(img)
        for y in range(h):
            t = y / h
            pen.line([(0, y), (w, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)))
        for i in range(40):
            x, y = (i * 997) % w, (i * 613) % h
            pen.ellipse((x, y, x + w // 9, y + w // 9), fill=tuple((c + i * 23) % 256 for c in top))
        path = folder / name
        img.save(path)
        paths.append(path)
    return paths


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--fixtures", default=None)
    ap.add_argument("--target", default="webp")
    ap.add_argument("--width", type=int, default=1600)
    ap.add_argument("--height", type=int, default=900)
    args = ap.parse_args()
    out = Path(args.out)
    exe = str(Path(args.exe).resolve())

    screen_w, screen_h = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    print(f"Screen: {screen_w}x{screen_h}")
    width, height, canvas = args.width, args.height, None
    if screen_w < width or screen_h - 40 < height:
        width, height = max(780, min(width, screen_w - 24)), max(600, min(height, screen_h - 60))
        canvas = (max(MIN_W, width + 160), max(MIN_H, height + 120))
        print(f"Screen too small for {args.width}x{args.height}; window {width}x{height} on canvas {canvas}")

    fixtures = Path(args.fixtures or (Path.home() / "Pictures" / "RightConvert demo"))
    files = make_fixtures(fixtures)

    # 1) Queue ready: files added, target chosen, not started.
    proc = subprocess.Popen([exe, "--files", *map(str, files), "--target", args.target])
    try:
        hwnd = wait_main_window(proc)
        place(hwnd, width, height)
        time.sleep(4)  # inspection of the queued files
        capture(proc, hwnd, out / "01-queue-ready.png", canvas)
    finally:
        proc.kill()
        proc.wait()
    time.sleep(1)

    # 2) + 3) Auto-start the same batch: capture while running, then when done.
    outputs = [f.with_suffix("." + args.target) for f in files]
    for o in outputs:
        o.unlink(missing_ok=True)
    proc = subprocess.Popen([exe, "--files", *map(str, files), "--target", args.target, "--auto-start"])
    try:
        hwnd = wait_main_window(proc)
        place(hwnd, width, height)
        mid_done = False
        deadline = time.time() + 240
        while time.time() < deadline:
            finished = sum(o.exists() for o in outputs)
            if finished == len(outputs):
                break
            if not mid_done and finished >= 1:
                capture(proc, hwnd, out / "02-converting.png", canvas)
                mid_done = True
            time.sleep(0.25)
        else:
            raise SystemExit("Conversion did not finish in time")
        if not mid_done:
            print("WARNING: conversion finished before an in-progress frame could be captured")
        time.sleep(3)
        capture(proc, hwnd, out / "03-conversion-done.png", canvas)
    finally:
        proc.kill()
        proc.wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
