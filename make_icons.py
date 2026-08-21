"""Generate the Pothole Reporter web, Android and Play listing artwork."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
WWW = ROOT / "android-app" / "www"
RES = ROOT / "android-app" / "android" / "app" / "src" / "main" / "res"
STORE = ROOT / "store-assets"
BG = (17, 18, 20, 255)
ROAD = (58, 59, 64, 255)
MUTED = (180, 180, 186, 255)
ORANGE = (255, 122, 26, 255)


def draw_mark(img: Image.Image, box: tuple[float, float, float, float]) -> None:
    """Draw the established road-and-pothole mark inside box."""
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    d.polygon(
        [(x0 + w * 0.18, y0 + h * 0.90), (x0 + w * 0.82, y0 + h * 0.90),
         (x0 + w * 0.63, y0 + h * 0.10), (x0 + w * 0.37, y0 + h * 0.10)],
        fill=ROAD,
    )
    for top, bottom in ((0.18, 0.30), (0.38, 0.50), (0.58, 0.70)):
        d.polygon(
            [(x0 + w * 0.47, y0 + h * bottom), (x0 + w * 0.53, y0 + h * bottom),
             (x0 + w * 0.52, y0 + h * top), (x0 + w * 0.48, y0 + h * top)],
            fill=MUTED,
        )
    cx, cy = x0 + w * 0.5, y0 + h * 0.73
    rx, ry = w * 0.22, h * 0.10
    d.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=ORANGE)
    d.ellipse([cx - rx * 0.64, cy - ry * 0.58, cx + rx * 0.64, cy + ry * 0.64],
              fill=(20, 21, 24, 255))


def make_icon(size: int, path: Path) -> None:
    # Full-bleed square source. Android and Play apply their own masks.
    img = Image.new("RGBA", (size, size), BG)
    draw_mark(img, (size * 0.20, size * 0.12, size * 0.80, size * 0.88))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)


def make_foreground(size: int, path: Path) -> None:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    # Keep the complete mark inside the adaptive-icon safe zone.
    draw_mark(img, (size * 0.31, size * 0.25, size * 0.69, size * 0.75))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)


def make_splash(width: int, height: int, path: Path) -> None:
    img = Image.new("RGBA", (width, height), BG)
    size = max(120, int(min(width, height) * 0.30))
    mark = Image.new("RGBA", (size, size), BG)
    draw_mark(mark, (size * 0.20, size * 0.12, size * 0.80, size * 0.88))
    img.alpha_composite(mark, ((width - size) // 2, (height - size) // 2))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)


def font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    choices = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold
        else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/SFNS.ttf",
    ]
    for candidate in choices:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            pass
    return ImageFont.load_default()


def make_feature(path: Path) -> None:
    img = Image.new("RGBA", (1024, 500), BG)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((48, 48, 404, 452), radius=42, fill=(28, 29, 33, 255),
                        outline=(42, 43, 48, 255), width=3)
    draw_mark(img, (125, 82, 327, 418))
    d.text((458, 128), "Pothole", font=font(72, True), fill=(242, 242, 244, 255))
    d.text((458, 205), "Reporter", font=font(72, True), fill=ORANGE)
    d.text((462, 310), "Detect road damage.", font=font(30), fill=(207, 207, 212, 255))
    d.text((462, 352), "Prepare a complaint draft.", font=font(30), fill=(207, 207, 212, 255))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(path, quality=94)


for size, name in ((192, "icon-192.png"), (512, "icon-512.png"),
                   (180, "apple-touch-icon.png")):
    make_icon(size, STATIC / name)
    make_icon(size, WWW / name)

STORE.mkdir(parents=True, exist_ok=True)
make_icon(512, STORE / "play-icon-512.png")
make_feature(STORE / "feature-graphic-1024x500.png")

densities = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}
for density, size in densities.items():
    target = RES / f"mipmap-{density}"
    make_icon(size, target / "ic_launcher.png")
    make_icon(size, target / "ic_launcher_round.png")
    make_foreground(round(size * 2.25), target / "ic_launcher_foreground.png")

splash_sizes = {
    "drawable": (480, 320),
    "drawable-land-mdpi": (480, 320), "drawable-land-hdpi": (800, 480),
    "drawable-land-xhdpi": (1280, 720), "drawable-land-xxhdpi": (1600, 960),
    "drawable-land-xxxhdpi": (1920, 1280),
    "drawable-port-mdpi": (320, 480), "drawable-port-hdpi": (480, 800),
    "drawable-port-xhdpi": (720, 1280), "drawable-port-xxhdpi": (960, 1600),
    "drawable-port-xxxhdpi": (1280, 1920),
}
for folder, dims in splash_sizes.items():
    make_splash(*dims, RES / folder / "splash.png")

print("web, Android and Play artwork written")
