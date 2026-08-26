#!/usr/bin/env python3
"""Guard the release-size settings that keep the Android app lean."""

from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
BUILD_GRADLE = (ROOT / "android-app/android/app/build.gradle").read_text()
GRADLE_PROPERTIES = (ROOT / "android-app/android/gradle.properties").read_text()
RELEASE_SCRIPT = (ROOT / "tools/build-play-release.sh").read_text()
failures = []


def check(label, condition):
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        failures.append(label)


release_match = re.search(r"buildTypes\s*\{[\s\S]*?release\s*\{([\s\S]*?)\n\s*}\n\s*}", BUILD_GRADLE)
release_block = release_match.group(1) if release_match else ""

check("release code shrinking is enabled",
      re.search(r"\bminifyEnabled\s+true\b", release_block)
      and not re.search(r"\bminifyEnabled\s+false\b", release_block))
check("release resource shrinking is enabled",
      re.search(r"\bshrinkResources\s+true\b", release_block))
check("release uses the optimizing Android rules",
      "getDefaultProguardFile('proguard-android-optimize.txt')" in release_block)
check("R8 uses code-aware resource shrinking",
      re.search(r"^android\.r8\.optimizedResourceShrinking=true$", GRADLE_PROPERTIES, re.MULTILINE))
check("Play release validation requires an R8 mapping",
      "R8 mapping is missing; release code shrinking is not active" in RELEASE_SCRIPT
      and "BUNDLE-METADATA/com.android.tools.build.obfuscation/proguard.map" in RELEASE_SCRIPT)

if failures:
    print(f"\nFAIL: {len(failures)} Android release optimization check(s) failed")
    sys.exit(1)
print("\nANDROID RELEASE OPTIMIZATION TEST PASS")
