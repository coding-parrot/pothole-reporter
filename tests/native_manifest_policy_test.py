#!/usr/bin/env python3
"""What the wired manifest promises Android, checked against what the page can deliver.

Two promises broke in the field. The activity listed itself in every video share sheet
after the page lost the only element that showed an import, so a shared clip was copied
into the cache (up to 512 MB) and the tester saw a plain Home. And the activity took any
orientation, so a phone mounted sideways pushed the Drive count and status below a
364 px fold that a rider cannot scroll mid-ride.
"""

from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = (ROOT / "android-app/android/app/src/main/AndroidManifest.xml").read_text()
BRIDGE = ROOT / "android-app/android/app/src/main/java/com/gauravsen/potholereporter/bridge"
PLUGIN = (BRIDGE / "VideoImportPlugin.kt").read_text()
POLICY = (BRIDGE / "VideoImportPolicy.kt").read_text()
WEB = (ROOT / "static/index.html").read_text()
failures = []


def check(label, condition):
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        failures.append(label)


activity = MANIFEST[MANIFEST.index("<activity"):MANIFEST.index("</activity>")]
share_filters = re.findall(
    r"<intent-filter>(?:(?!</intent-filter>).)*android\.intent\.action\.SEND(?:_MULTIPLE)?\""
    r"(?:(?!</intent-filter>).)*</intent-filter>",
    activity, re.S)
import_host = 'id="nativeVideoImports"' in WEB
accept = PLUGIN[PLUGIN.index("fun acceptIngressIntent("):PLUGIN.index("override fun handleOnDestroy")]

check(
    "the activity is offered as a video share target only while the page can show the import",
    import_host or not share_filters,
)
check(
    "an explicit SEND intent is not copied into the cache while the import UI is gone",
    import_host or ("if (!SHARE_INGRESS_ENABLED) return" in accept
                    and "internal const val SHARE_INGRESS_ENABLED = false" in POLICY),
)
check(
    "MainActivity is portrait, the only orientation the Drive HUD fits",
    'android:screenOrientation="portrait"' in activity,
)

if failures:
    print(f"FAIL {len(failures)} manifest policy check(s)")
    sys.exit(1)
print("manifest policy: share entry and orientation match the page")
