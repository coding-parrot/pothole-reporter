#!/usr/bin/env python3
"""The page needs a way to Android's permission page once a denial is permanent.

After 'Don't allow' twice, Android marks CAMERA and location USER_FIXED and never shows
the system sheet again. Nothing in the app could open the app's own settings page, so
Drive stayed dead behind a raw alert. DriveModePlugin.openAppSettings is that exit.
"""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = (ROOT / "android-app/android/app/src/main/java/com/gauravsen/potholereporter/bridge/DriveModePlugin.kt").read_text()
failures = []


def check(label, condition):
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        failures.append(label)


has = "fun openAppSettings(call: PluginCall)" in PLUGIN
body = PLUGIN[PLUGIN.index("fun openAppSettings("):] if has else ""
body = body[:body.find("\n    @PluginMethod") if "\n    @PluginMethod" in body else len(body)]
check("DriveModePlugin exposes openAppSettings", has
      and PLUGIN[:PLUGIN.index("fun openAppSettings(")].rstrip().endswith("@PluginMethod"))
check("it opens this package's details page, not a generic settings screen",
      "Settings.ACTION_APPLICATION_DETAILS_SETTINGS" in body
      and 'Uri.fromParts("package",' in body)
check("it resolves only after the activity was started", body.find("startActivity(") != -1
      and body.find("startActivity(") < body.find("call.resolve("))

if failures:
    print(f"FAIL {len(failures)} app-settings check(s)")
    sys.exit(1)
print("DriveModePlugin can open the app's Android settings page")
