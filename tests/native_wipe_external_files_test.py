#!/usr/bin/env python3
"""Delete all data must reach the app-specific external folders, not just Room.

Capacitor Camera has the camera app write each full-resolution original (often with
EXIF location) through the my_images FileProvider path into
Android/data/<pkg>/files/Pictures, and capacitor-email-composer materialises the last
evidence attachment in Android/data/<pkg>/cache/email_composer. The web wipe only
removes Documents/pothole-frames and the share cache, and the native clearAllData only
touched Room and the Keystore identity, so both folders outlived Delete all.
"""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = (ROOT / "android-app/android/app/src/main/java/com/gauravsen/potholereporter/bridge/DriveModePlugin.kt").read_text()
PATHS = (ROOT / "android-app/android/app/src/main/res/xml/file_paths.xml").read_text()
failures = []


def check(label, condition):
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        failures.append(label)


clear = PLUGIN[PLUGIN.index("fun clearAllData(call: PluginCall)"):PLUGIN.index("fun listDriveSessions(")]

check("the camera originals folder is still where FileProvider points",
      '<external-files-path name="my_images" path="." />' in PATHS)
check("the email attachment folder is still where FileProvider points",
      'path="email_composer/"' in PATHS)
check("clearAllData removes the camera originals under Pictures",
      "getExternalFilesDir(android.os.Environment.DIRECTORY_PICTURES)" in clear)
check("clearAllData removes the email_composer attachment cache",
      'File(it, "email_composer")' in clear)
check("a folder that survives the delete fails the wipe instead of reporting ok",
      "deleteRecursively()" in clear
      and clear.index("deleteRecursively()") < clear.index('put("ok", true)'))

if failures:
    print(f"FAIL {len(failures)} external-files wipe check(s)")
    sys.exit(1)
print("clearAllData reaches every app-specific external folder")
