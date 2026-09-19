# -*- coding: utf-8 -*-
"""Which Android source tree the shipped app actually wires.

The repository carries two Android implementations:

  com/gauravsen/potholereporter   the one AndroidManifest.xml and MainActivity wire
  dev/aiengg/potholereporter      native Drive Mode with durable keyframe replay, RTSP
                                  dashcam sources, repair verification and its own
                                  detection contract

Only the first is reachable at runtime: the manifest declares .drivemode.DriveModeService
and MainActivity registers com.gauravsen.potholereporter.bridge.DriveModePlugin. Nothing
names a class in dev/aiengg, so that code is compiled and never called. Suites written
against it cannot pass, and passing them would prove nothing about the shipped app.

Call require_wired_tree() at the top of such a suite so it fails with that sentence
instead of a stack trace about a file whose contents no longer matter.
"""

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "android-app/android/app/src/main/AndroidManifest.xml"
ACTIVITY = ROOT / "android-app/android/app/src/main/java/com/gauravsen/potholereporter/MainActivity.kt"
ALTERNATE_TREE = "dev/aiengg/potholereporter"


def wired_packages() -> set:
    """Package prefixes the manifest and MainActivity actually name."""
    sources = MANIFEST.read_text(encoding="utf-8")
    if ACTIVITY.is_file():
        sources += ACTIVITY.read_text(encoding="utf-8")
    return set(re.findall(r"\b((?:com|dev)(?:\.[a-z0-9_]+){2,})", sources))


def alternate_tree_is_wired() -> bool:
    return any(name.startswith("dev.aiengg") for name in wired_packages())


def require_wired_tree(what: str) -> None:
    if alternate_tree_is_wired():
        return
    print(f"FAIL: {what} lives in {ALTERNATE_TREE}, which the shipped app does not wire.")
    print("      AndroidManifest.xml declares .drivemode.DriveModeService and MainActivity")
    print("      registers com.gauravsen.potholereporter.bridge.DriveModePlugin, so nothing")
    print("      in that tree runs. Decide whether to wire it or remove it; this suite")
    print("      cannot say anything about the app until then.")
    sys.exit(1)
