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


def screen_of(path):
    image = Image.open(path).convert("RGB")
    if orange(px(image, 540, 455)):
        return "home"
    if green(px(image, 780, 2322)) and not green(px(image, 300, 2322)):
        return "dataConsent"
    if green(px(image, 540, 2148)):
        return "settings"
    # Android's runtime permission sheet is centred and pale; both the camera and the
    # location variants cover these two points, and no app screen is light there.
    if light(px(image, 540, 1226)) and light(px(image, 540, 1500)):
        return "permission"
    # The live drive's Stop button. Dimmed under a permission sheet, so checked last.
    if red(px(image, 877, 469)):
        return "drive"
    return "unknown"


if __name__ == "__main__":
    print(screen_of(sys.argv[1]))
