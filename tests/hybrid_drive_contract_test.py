#!/usr/bin/env python3
"""Offline contract for bounded native Drive capture and saved-frame replay."""

from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
CAMERA = (ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/drive/NativeDriveCameraManager.kt").read_text()
SERVICE = (ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/drive/DriveForegroundService.kt").read_text()
INFERENCE = (ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/drive/NativeInferenceEngine.kt").read_text()
POLICY = (ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/drive/DriveSessionLimitPolicy.kt").read_text()
PLUGIN = (ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/plugin/DriveModePlugin.kt").read_text()
DATABASE = (ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/db/PotholeDatabase.kt").read_text()
WEB = (ROOT / "static/index.html").read_text()
GRADLE = (ROOT / "android-app/android/app/build.gradle").read_text()
RELEASE = (ROOT / "tools/build-play-release.sh").read_text()
failures = []


def check(label, condition):
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        failures.append(label)


check("continuous video requests SD, never HD",
      "Quality.SD" in CAMERA and "Quality.HD" not in CAMERA)
check("only one raw burst may wait for inference",
      re.search(r"jobChannel\s*=\s*Channel\([\s\S]{0,500}?capacity\s*=\s*1\b", SERVICE))
check("native detection requires at least two real source frames",
      "MIN_DETECTION_SOURCE_FRAMES = 2" in CAMERA
      and "frames.size < MIN_DETECTION_SOURCE_FRAMES" in CAMERA
      and "burstFrames.size < NativeDriveCameraManager.MIN_DETECTION_SOURCE_FRAMES" in INFERENCE)
check("30-minute active-time limit is bounded to 15..90 minutes",
      all(value in POLICY for value in (
          "MIN_LIMIT_MINUTES = 15", "MAX_LIMIT_MINUTES = 90",
          "DEFAULT_LIMIT_MINUTES = 30", "fun pause(", "fun resume(",
          "fun remainingMs(", "fun expired("))
      and "SystemClock.elapsedRealtime()" in SERVICE
      and all(value in SERVICE for value in (
          "startSessionLimitLoop()", "pauseSessionLimit()", "resumeSessionLimit()")))
check("sparse keyframes are durable and unique per drive capture",
      "KEYFRAME_INTERVAL_MS = 2_000L" in SERVICE
      and "persistSparseKeyframe(baseItem)" in SERVICE
      and "if (!recordingEnabled" in SERVICE
      and "MIGRATION_3_4" in DATABASE
      and "index_drive_keyframes_sessionId_captureSeq" in DATABASE)
check("binary detector evidence has a non-destructive Room migration",
      "version = 5" in DATABASE and "MIGRATION_4_5" in DATABASE
      and "ADD COLUMN `looksLikeSpeedBreaker` INTEGER NOT NULL DEFAULT 1" in DATABASE
      and "ADD COLUMN `hasLocalizedCavity` INTEGER NOT NULL DEFAULT 0" in DATABASE)
check("native bridge exposes pending keyframe replay",
      all(f"fun {method}(" in PLUGIN for method in
          ("listKeyframes", "readKeyframe", "markKeyframeAnalyzed"))
      and 'getInt("maxDurationMinutes")' in PLUGIN
      and "EXTRA_MAX_DRIVE_MINUTES" in PLUGIN)

analysis = WEB[WEB.index("async function analyseNativeKeyframes"):]
analysis = analysis[:analysis.index("function gpsAt")]
check("UI wires timer and idempotent saved-frame analysis",
      "const DRIVE_LIMIT_OPTIONS = [15, 30, 60, 90]" in WEB
      and "maxDurationMinutes: driveLimitMinutes()" in WEB
      and 'data-analysenative="${driveId}"' in WEB
      and "pendingOnly: true" in analysis
      and "`live:${state.driveId}:${saved.captureSeq}`" in analysis
      and analysis.index('await api("/api/frame"')
          < analysis.index("await plugin.markKeyframeAnalyzed"))
check("hybrid status is explicit and all shipped web copies match",
      "RECORDING LOW-RES VIDEO" in WEB and '"Video: Low"' in WEB
      and "saved frame" in WEB and "min left" in WEB
      and (ROOT / "android-app/www/index.html").read_bytes()
          == (ROOT / "static/index.html").read_bytes()
      and (ROOT / "docs/index.html").read_bytes()
          == (ROOT / "static/index.html").read_bytes()
      and (ROOT / "android-app/android/app/src/main/assets/public/index.html").read_bytes()
          == (ROOT / "static/index.html").read_bytes())
check("Android release identity is 1.35.0 code 54 everywhere",
      re.search(r"versionCode\s+54\b", GRADLE)
      and re.search(r'versionName\s+"1\.35\.0"', GRADLE)
      and 'android:versionCode="54"' in RELEASE
      and 'android:versionName="1.35.0"' in RELEASE)

if failures:
    print(f"\nFAIL: {len(failures)} hybrid Drive contract check(s) failed")
    sys.exit(1)
print("\nHYBRID DRIVE CONTRACT TEST PASS")
