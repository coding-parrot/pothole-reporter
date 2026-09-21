#!/usr/bin/env python3
"""Name the app screen shown in an emulator screenshot.

    python3 tools/harness/screen-of.py shot.png    -> home | settings | dataConsent |
                                                      permission | drive | unknown

The packaged WebView exposes almost nothing to accessibility and drops console output
in a release build, so the emulator smoke test cannot ask the page where it is. It can
look. Each screen has one element with a colour nothing else on that screen shares, at
a fixed position on the 1080x2400 test device: Drive's orange, the green Continue at
the foot of first-run Settings, the green Continue plus grey Not now on the camera and
location notice, the white system permission sheet, and the red Stop on the live drive.

Buttons are found by the share of a region they fill, not by one pixel: a single probe
once landed on the Stop label's white text, and on the dark page after the notice's
buttons became a sticky row above the safe area, and a working build failed the smoke
test. `screen-of.py --continue shot.png` prints the centre of the notice's green
Continue so the smoke test taps where the button is.
"""
import sys

from PIL import Image


def px(image, x, y):
    return image.getpixel((x, y))


def green(p):
    r, g, b = p
    return g > 150 and r < 110 and b < 150


def orange(p):
    r, g, b = p
    return r > 220 and 90 < g < 160 and b < 80


def red(p):
    r, g, b = p
    return r > 200 and g < 110 and b < 110


def light(p):
    return all(channel > 200 for channel in p)


def share(image, box, test, step=6):
    """Fraction of sampled pixels in box (x0, y0, x1, y1) for which test holds."""
    x0, y0, x1, y1 = box
    hits = total = 0
    for y in range(y0, y1, step):
        for x in range(x0, x1, step):
            total += 1
            hits += test(image.getpixel((x, y)))
    return hits / total if total else 0.0


# The notice's button row sits at the foot of the screen; how far up depends on the
# safe-area inset, so search the whole band. Continue is the right half, Not now the left.
CONTINUE_BAND = (560, 2000, 1020, 2360)
NOT_NOW_BAND = (60, 2000, 520, 2360)
# The live drive's Stop button, top right of the camera card.
STOP_BOX = (760, 420, 1000, 530)


def continue_centre(image):
    """Centre (x, y) of the green Continue button, or None when it is not on screen."""
    x0, y0, x1, y1 = CONTINUE_BAND
    rows = [y for y in range(y0, y1, 4) if share(image, (x0, y, x1, y + 1), green) > 0.5]
    if not rows:
        return None
    return (x0 + x1) // 2, (rows[0] + rows[-1]) // 2


def screen_of(path):
    image = Image.open(path).convert("RGB")
    if orange(px(image, 540, 455)):
        return "home"
    if continue_centre(image) and share(image, NOT_NOW_BAND, green) < 0.05:
        return "dataConsent"
    if green(px(image, 540, 2148)):
        return "settings"
    # Android's runtime permission sheet is centred and pale; both the camera and the
    # location variants cover these two points, and no app screen is light there.
    if light(px(image, 540, 1226)) and light(px(image, 540, 1500)):
        return "permission"
    # The live drive's Stop button. Dimmed under a permission sheet, so checked last.
    if share(image, STOP_BOX, red) > 0.3:
        return "drive"
    return "unknown"


if __name__ == "__main__":
    if sys.argv[1] == "--continue":
        centre = continue_centre(Image.open(sys.argv[2]).convert("RGB"))
        if not centre:
            sys.exit(1)
        print(*centre)
    else:
        print(screen_of(sys.argv[1]))
