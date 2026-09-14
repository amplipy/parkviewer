"""Generate the ParkViewer app icon (assets/icon.icns / icon.ico / icon.png).

Run once from the repo root:  python scripts/make_icon.py
Icons are deterministic (PIL drawing only) so CI stays reproducible.
"""

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "packaging" / "assets"
ASSETS.mkdir(parents=True, exist_ok=True)

# Rounded-square tile: white background, blue "AFM tip scanning a surface"
# mark: three scan lines under a stylised probe triangle.
BLUE = (28, 100, 242, 255)
BLUE_DARK = (10, 60, 160, 255)
SLATE = (90, 105, 130, 255)
WHITE = (255, 255, 255, 255)


def draw_icon(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # rounded-square tile
    m = size // 16  # margin
    r = size // 5  # corner radius
    d.rounded_rectangle([m, m, size - m, size - m], radius=r, fill=WHITE)

    # probe: inverted triangle (tip pointing down)
    cx = size // 2
    top = int(size * 0.30)
    half_w = int(size * 0.16)
    tip_y = int(size * 0.52)
    d.polygon(
        [(cx - half_w, top), (cx + half_w, top), (cx, tip_y)],
        fill=BLUE,
    )
    # probe body block above the triangle
    d.rounded_rectangle(
        [cx - half_w, int(size * 0.22), cx + half_w, top + 2],
        radius=int(size * 0.03),
        fill=BLUE_DARK,
    )

    # scan lines under the tip
    y0 = int(size * 0.62)
    x0, x1 = int(size * 0.28), int(size * 0.72)
    lw = max(2, size // 32)
    for i in range(3):
        y = y0 + i * int(size * 0.09)
        alpha = 255 - i * 70
        d.line(
            [(x0, y), (x1, y)],
            fill=(28, 100, 242, alpha),
            width=lw,
        )
    return img


def main() -> None:
    master = draw_icon(1024)
    master.save(ASSETS / "icon_1024.png")

    # PNG sizes used by PyInstaller (window icon) and Linux .desktop
    for s in (16, 32, 48, 64, 128, 256, 512):
        master.resize((s, s), Image.LANCZOS).save(ASSETS / f"icon_{s}.png")

    # Windows .ico with embedded multi-size frames
    master.save(
        ASSETS / "icon.ico",
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )

    # macOS .icns (Pillow >= 9.1 writes proper icns)
    master.save(ASSETS / "icon.icns", format="ICNS")

    print(f"icons written to {ASSETS}")


if __name__ == "__main__":
    main()