#!/usr/bin/env python3
"""The promises the APK makes outside its code: its user agent and its permissions.

The 1.39.x builds announced themselves as PotholeReporter/1.38.0 on every OSM tile
request and in every drive analysis log, because the suffix in capacitor.config.json
was never bumped with versionName. And the manifest kept CHANGE_NETWORK_STATE and
POST_NOTIFICATIONS after the dashcam and foreground Drive code that used them was
unwired, so Play listed them and the Data safety review asked about them.

check-native-contracts.mjs guards both. This suite runs it on the repo, then plants
each defect in a copy and watches it fail, because a gate that never fires is not a gate.
"""

from pathlib import Path
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "tools/harness/check-native-contracts.mjs"
GRADLE = "android-app/android/app/build.gradle"
CONFIG = "android-app/capacitor.config.json"
PACKAGED = "android-app/android/app/src/main/assets/capacitor.config.json"
MANIFEST = "android-app/android/app/src/main/AndroidManifest.xml"
WIRED = "android-app/android/app/src/main/java/com/gauravsen"
UNWIRED_CONTRACT = "android-app/android/app/src/main/java/dev/aiengg/potholereporter/drive/NativeRepairContract.kt"
failures = []


def check(label, condition, detail=""):
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        failures.append(label)
        if detail:
            print("       " + detail.strip().replace("\n", "\n       "))


def run(root=None):
    env = dict(os.environ)
    if root:
        env["NATIVE_CONTRACTS_ROOT"] = str(root)
    result = subprocess.run(["node", str(CHECK)], capture_output=True, text=True, env=env)
    return result.returncode, result.stdout + result.stderr


def copy_repo(dest):
    for rel in ["static/standalone.js", "static/index.html", GRADLE, CONFIG, PACKAGED,
                MANIFEST, UNWIRED_CONTRACT]:
        if (ROOT / rel).exists():
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(ROOT / rel, dest / rel)
    shutil.copytree(ROOT / WIRED, dest / WIRED)


def planted(label, mutate, expect):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        copy_repo(root)
        mutate(root)
        code, output = run(root)
    check(label, code != 0 and expect in output, output)


def edit(root, rel, pattern, replacement):
    path = root / rel
    text = path.read_text()
    changed = re.sub(pattern, replacement, text, count=1)
    assert changed != text, f"{rel}: {pattern} did not match"
    path.write_text(changed)


code, output = run()
check("the repo passes check-native-contracts", code == 0, output)

version = re.search(r'versionName "([^"]+)"', (ROOT / GRADLE).read_text()).group(1)
user_agent = json.loads((ROOT / CONFIG).read_text())["appendUserAgent"]
check(f"the WebView user agent names the build it ships in ({version})",
      user_agent.startswith(f"PotholeReporter/{version} "), user_agent)

manifest = (ROOT / MANIFEST).read_text()
for permission in ["CHANGE_NETWORK_STATE", "POST_NOTIFICATIONS"]:
    check(f"the manifest no longer asks for {permission}",
          f"android.permission.{permission}" not in manifest)

planted("a versionName bump without the user agent fails the check",
        lambda root: edit(root, GRADLE, r'versionName "[^"]+"', 'versionName "9.9.9"'),
        "user agent: capacitor.config.json says")
if (ROOT / PACKAGED).exists():
    planted("a packaged config left behind by the source fails the check",
            lambda root: edit(root, PACKAGED, r"PotholeReporter/[0-9.]+", "PotholeReporter/1.0.0"),
            f"user agent: {PACKAGED} differs")
planted("a permission with no wired user fails the check",
        lambda root: edit(root, MANIFEST, r'(<uses-permission android:name="android.permission.WAKE_LOCK" />)',
                          r'\1\n    <uses-permission android:name="android.permission.POST_NOTIFICATIONS" />'),
        "permission POST_NOTIFICATIONS: declared in the manifest")
planted("a permission named only in a comment still fails the check",
        lambda root: edit(root, MANIFEST, r'(<uses-permission android:name="android.permission.WAKE_LOCK" />)',
                          r'\1\n    <uses-permission android:name="android.permission.CHANGE_NETWORK_STATE" />')
        or (root / WIRED / "potholereporter/Note.kt").write_text(
            "package com.gauravsen.potholereporter\n// CHANGE_NETWORK_STATE once served the dashcam.\n"),
        "permission CHANGE_NETWORK_STATE: declared in the manifest")
planted("a library permission whose user is removed fails the check",
        lambda root: edit(root, WIRED + "/potholereporter/drivemode/UploadWorker.kt",
                          r"NetworkType\.CONNECTED", "NetworkType.NOT_REQUIRED"),
        "permission ACCESS_NETWORK_STATE: declared for WorkManager")

if failures:
    print(f"FAIL {len(failures)} native manifest contract check(s)")
    sys.exit(1)
print("native manifest contracts: user agent matches versionName, every permission has a user")
