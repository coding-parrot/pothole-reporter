#!/usr/bin/env python3
"""The app is dark in every phone theme, so the system bars must use light icons.

Capacitor's SystemBars plugin follows the phone's theme by default. On real phones set
to light mode (Pixel 8a, Redmi Note 13, Galaxy A15 on AWS Device Farm, 7 Oct 2026) that
drew the clock and status icons dark on the app's dark background, nearly invisible, and
a light grey navigation bar under the dark app on phones with three buttons. The style is
pinned to DARK (light icons) in both copies of the Capacitor configuration.
"""

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIGS = [
    ROOT / "android-app" / "capacitor.config.json",
    ROOT / "android-app" / "android" / "app" / "src" / "main" / "assets" / "capacitor.config.json",
]

failures = []
for path in CONFIGS:
    config = json.loads(path.read_text(encoding="utf-8"))
    style = config.get("plugins", {}).get("SystemBars", {}).get("style")
    ok = style == "DARK"
    print(f"  {'ok  ' if ok else 'FAIL'} {path.relative_to(ROOT)} SystemBars.style is {style!r}")
    if not ok:
        failures.append(str(path.relative_to(ROOT)))

# Light icons need a dark bar behind them. With the first fix alone a Redmi Note 13
# (Android 15, three navigation buttons) drew WHITE buttons on a light grey bar: the bar
# is a translucent scrim over the activity window, and the window was the light theme's
# white. The window behind the WebView, and the bar colours older Android still reads,
# are the page's own background colour.
import re

page = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
page_background = re.search(r"--bg:\s*(#[0-9a-fA-F]{6})", page).group(1).lower()
values = ROOT / "android-app" / "android" / "app" / "src" / "main" / "res" / "values"
styles = (values / "styles.xml").read_text(encoding="utf-8")
colors_path = values / "colors.xml"
colors = colors_path.read_text(encoding="utf-8") if colors_path.exists() else ""
declared = re.search(r'<color name="appBackground">(#[0-9a-fA-F]{6})</color>', colors)
theme = re.search(r'<style name="AppTheme\.NoActionBar".*?</style>', styles, re.S).group(0)
checks = [
    ("colors.xml declares appBackground as the page background " + page_background,
     bool(declared) and declared.group(1).lower() == page_background),
    ("the window behind the WebView is that colour",
     '<item name="android:windowBackground">@color/appBackground</item>' in theme),
    ("the navigation bar colour is that colour on Android that still reads it",
     '<item name="android:navigationBarColor">@color/appBackground</item>' in theme),
    ("the status bar colour is that colour on Android that still reads it",
     '<item name="android:statusBarColor">@color/appBackground</item>' in theme),
    ("the theme itself asks for light icons, before the plugin loads",
     '<item name="android:windowLightStatusBar">false</item>' in theme
     and '<item name="android:windowLightNavigationBar">false</item>' in theme),
]
for name, ok in checks:
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    if not ok:
        failures.append(name)

if failures:
    print(f"FAIL: {'; '.join(failures)}")
    sys.exit(1)
print("SYSTEM BARS STYLE TEST PASS")
