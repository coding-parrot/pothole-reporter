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

if failures:
    print(f"FAIL: system bars follow the phone theme in {', '.join(failures)}")
    sys.exit(1)
print("SYSTEM BARS STYLE TEST PASS")
