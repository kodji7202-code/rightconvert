"""Generate original RightConvert artwork locally; no external image assets."""

from pathlib import Path

from PIL import Image, ImageDraw


def main() -> None:
    size = 256
    icon = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    painter = ImageDraw.Draw(icon)
    painter.rounded_rectangle((8, 8, 248, 248), radius=60, fill="#266FE0")
    painter.line([(55, 94), (198, 94)], fill="white", width=21, joint="curve")
    painter.line([(165, 64), (202, 94), (165, 124)], fill="white", width=21, joint="curve")
    painter.line([(200, 166), (57, 166)], fill="white", width=21, joint="curve")
    painter.line([(90, 136), (54, 166), (90, 196)], fill="white", width=21, joint="curve")
    target = Path(__file__).resolve().parents[1] / "assets" / "RightConvert.ico"
    target.parent.mkdir(parents=True, exist_ok=True)
    icon.save(target, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(target)


if __name__ == "__main__":
    main()
