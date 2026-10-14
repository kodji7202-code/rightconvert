"""Generate the Store/MSIX visual assets from RightConvert's original artwork.

The artwork is the same vector-like drawing as scripts/make_icon.py (which
produces assets/RightConvert.ico), re-rendered at each exact pixel size with
4x supersampling so no asset is an upscaled bitmap. Output: <dest>/Assets/*.png

Usage: python msix-store/make_assets.py <layout-folder>
"""

from __future__ import annotations

from pathlib import Path
import sys

from PIL import Image, ImageDraw

BRAND = "#266FE0"


def emblem(size: int, *, plate: bool = True) -> Image.Image:
    """The RightConvert emblem (two arrows) at ``size`` px, 4x supersampled."""
    scale = 4
    big = size * scale
    k = big / 256.0
    icon = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    paint = ImageDraw.Draw(icon)
    if plate:
        paint.rounded_rectangle((8 * k, 8 * k, 248 * k, 248 * k), radius=60 * k, fill=BRAND)
    ink = "white" if plate else BRAND
    width = max(1, round(21 * k))
    for points in (
        [(55, 94), (198, 94)],
        [(165, 64), (202, 94), (165, 124)],
        [(200, 166), (57, 166)],
        [(90, 136), (54, 166), (90, 196)],
    ):
        paint.line([(x * k, y * k) for x, y in points], fill=ink, width=width, joint="curve")
    return icon.resize((size, size), Image.LANCZOS)


def tile(width: int, height: int, emblem_fraction: float) -> Image.Image:
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    side = round(min(width, height) * emblem_fraction)
    mark = emblem(side)
    canvas.alpha_composite(mark, ((width - side) // 2, (height - side) // 2))
    return canvas


def main(layout: Path) -> None:
    out = layout / "Assets"
    out.mkdir(parents=True, exist_ok=True)
    written: list[str] = []

    def save(image: Image.Image, name: str) -> None:
        image.save(out / name, optimize=True)
        written.append(name)

    # App list / taskbar icon (Square44x44Logo): full-bleed emblem.
    for qualifier, px in (("", 44), (".scale-100", 44), (".scale-125", 55), (".scale-150", 66),
                          (".scale-200", 88), (".scale-400", 176)):
        save(tile(px, px, 1.0), f"Square44x44Logo{qualifier}.png")
    for target in (16, 20, 24, 30, 32, 36, 40, 48, 60, 64, 72, 80, 96, 256):
        image = tile(target, target, 1.0)
        save(image, f"Square44x44Logo.targetsize-{target}.png")
        save(image, f"Square44x44Logo.targetsize-{target}_altform-unplated.png")
        save(image, f"Square44x44Logo.targetsize-{target}_altform-lightunplated.png")
    # Start tiles.
    for qualifier, factor in (("", 1), (".scale-100", 1), (".scale-125", 1.25), (".scale-150", 1.5),
                              (".scale-200", 2), (".scale-400", 4)):
        save(tile(round(150 * factor), round(150 * factor), 0.5), f"Square150x150Logo{qualifier}.png")
        save(tile(round(310 * factor), round(150 * factor), 0.5), f"Wide310x150Logo{qualifier}.png")
        save(tile(round(71 * factor), round(71 * factor), 0.62), f"Square71x71Logo{qualifier}.png")
        save(tile(round(310 * factor), round(310 * factor), 0.5), f"Square310x310Logo{qualifier}.png")
    # Package logo shown by the Store / App Installer.
    for qualifier, px in (("", 50), (".scale-100", 50), (".scale-125", 63), (".scale-150", 75),
                          (".scale-200", 100), (".scale-400", 200)):
        save(tile(px, px, 1.0), f"StoreLogo{qualifier}.png")
    print(f"Wrote {len(written)} assets to {out}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(Path(sys.argv[1]))
