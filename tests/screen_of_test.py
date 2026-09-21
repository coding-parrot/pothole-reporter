#!/usr/bin/env python3
"""The emulator smoke test names screens by colour; prove it still can on the current layout.

tools/harness/screen-of.py once probed single pixels. When the data notice's buttons became
a sticky row above the safe area and the Stop label moved over the old probe point, it
answered "unknown" for a notice and a live drive that were plainly on screen, and the smoke
test failed a working build. These screenshots come from the 1.39.4 debug build on the
1080x2400 test emulator.
"""
import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SHOTS = ROOT / "tests" / "fixtures" / "screens"

spec = importlib.util.spec_from_file_location("screen_of", ROOT / "tools" / "harness" / "screen-of.py")
screen_of = importlib.util.module_from_spec(spec)
spec.loader.exec_module(screen_of)

EXPECTED = {
    "home-1.39.4.png": "home",
    "notice-1.39.4.png": "dataConsent",
    "drive-1.39.4.png": "drive",
}

failures = []
for name, want in EXPECTED.items():
    got = screen_of.screen_of(SHOTS / name)
    print(f"{name}: {got}")
    if got != want:
        failures.append(f"{name}: expected {want}, got {got}")

if failures:
    print("FAIL")
    for failure in failures:
        print(f"  - {failure}")
    sys.exit(1)
print("PASS")
