#!/usr/bin/env python3
"""Static release contract for native RTSP dashcam capture and phone-source isolation."""

from pathlib import Path
import sys
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
JAVA = ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter"
DRIVE = JAVA / "drive"
RTSP = (DRIVE / "NativeRtspFrameSource.kt").read_text()
SOURCE = (DRIVE / "NativeFrameSource.kt").read_text()
SERVICE = (DRIVE / "DriveForegroundService.kt").read_text()
INTERLOCK = (DRIVE / "NativeCaptureInterlock.kt").read_text()
NOTIFICATION = (DRIVE / "NotificationHelper.kt").read_text()
PLUGIN = (JAVA / "plugin/DriveModePlugin.kt").read_text()
ACTIVITY = (JAVA / "MainActivity.java").read_text()
WEB = (ROOT / "static/index.html").read_text()
GRADLE = (ROOT / "android-app/android/app/build.gradle").read_text()
MANIFEST_PATH = ROOT / "android-app/android/app/src/main/AndroidManifest.xml"
BACKUP_RULES = (ROOT / "android-app/android/app/src/main/res/xml/backup_rules.xml").read_text()
EXTRACTION_RULES = (
    ROOT / "android-app/android/app/src/main/res/xml/data_extraction_rules.xml"
).read_text()
failures = []


def check(label, condition):
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        failures.append(label)


android = "{http://schemas.android.com/apk/res/android}"
manifest = ET.parse(MANIFEST_PATH).getroot()
permissions = {node.get(android + "name") for node in manifest.findall("uses-permission")}
application = manifest.find("application")
drive_service = next(
    node for node in manifest.findall("./application/service")
    if node.get(android + "name") == ".drive.DriveForegroundService"
)

check(
    "Media3 RTSP and connected-device foreground service are declared",
    'implementation "androidx.media3:media3-exoplayer:$media3Version"' in GRADLE
    and 'implementation "androidx.media3:media3-exoplayer-rtsp:$media3Version"' in GRADLE
    and drive_service.get(android + "foregroundServiceType") == "camera|connectedDevice|location"
    and "android.permission.FOREGROUND_SERVICE_CONNECTED_DEVICE" in permissions
    and "android.permission.ACCESS_NETWORK_STATE" in permissions
    and "android.permission.CHANGE_NETWORK_STATE" in permissions,
)
check(
    "locally stored RTSP credentials and evidence are excluded from backup and device transfer",
    application is not None
    and application.get(android + "allowBackup") == "false"
    and application.get(android + "dataExtractionRules") == "@xml/data_extraction_rules"
    and application.get(android + "fullBackupContent") == "@xml/backup_rules"
    and '<exclude domain="sharedpref" path="."' in BACKUP_RULES
    and '<exclude domain="database" path="."' in BACKUP_RULES
    and "<cloud-backup>" in EXTRACTION_RULES
    and "<device-transfer>" in EXTRACTION_RULES
    and EXTRACTION_RULES.count('<exclude domain="sharedpref" path="."') == 2
    and EXTRACTION_RULES.count('<exclude domain="database" path="."') == 2,
)
check(
    "phone remains the default and dashcam skips phone-camera permission",
    'PHONE_CAMERA("phone_camera", true)' in SOURCE
    and 'DASHCAM("dashcam", false)' in SOURCE
    and "null, \"\", PHONE_CAMERA.wireValue -> PHONE_CAMERA" in SOURCE
    and "if (sourceKind.requiresCameraPermission) add(\"camera\")" in PLUGIN,
)
check(
    "RTSP uses one stable full-frame H264 decoder surface with audio disabled",
    RTSP.count("setVideoSurface(reader.surface)") == 1
    and "setVideoScalingMode(C.VIDEO_SCALING_MODE_SCALE_TO_FIT)" in RTSP
    and "VIDEO_SCALING_MODE_SCALE_TO_FIT_WITH_CROPPING" not in RTSP
    and "Deliberately no surface mutation here" in RTSP
    and ".setTrackTypeDisabled(C.TRACK_TYPE_AUDIO, true)" in RTSP
    and "MimeTypes.VIDEO_H264" in RTSP,
)
check(
    "RTSP control and RTP media use Wi-Fi while external HTTPS keeps its default route",
    "preferredWifiSocketFactory()?.let(mediaSourceFactory::setSocketFactory)" in RTSP
    and ".setForceUseRtpTcp(true)" in RTSP
    and "NetworkCapabilities.TRANSPORT_WIFI" in RTSP
    and "bindProcessToNetwork" not in RTSP
    and "setProcessDefaultNetwork" not in RTSP
    and "setBufferDurationsMsForStreaming(" in RTSP
    and "MAX_BUFFER_MS = 1_500" in RTSP,
)
check(
    "GPS pauses only dashcam sampling and generation tokens reject ABA frames",
    "frameSource?.setSamplingEnabled(enabled)" in SERVICE
    and "releaseCamera = !dashcamKeepsStreaming" in INTERLOCK
    and "samplingToken = samplingGeneration.current()" in RTSP
    and "!samplingGeneration.isCurrent(samplingToken)" in RTSP,
)
check(
    "dashcam image OOM stops safely instead of crashing its decoder thread",
    "catch (_: OutOfMemoryError)" in RTSP
    and "stopForFatalMemoryError(generation)" in RTSP
    and "stopForFatalSourceError(" in RTSP
    and "onFatalError(reason)" in RTSP
    and "stopDriveSession(\"Stopped: $reason\")" in SERVICE,
)
check(
    "preview is smooth, backpressured, complete-frame, and detachable",
    "NativeYuv420FullFrameConverter.toPreviewBitmap(image)" in RTSP
    and "PREVIEW_MAX_WIDTH = 480" in RTSP
    and "PREVIEW_MAX_HEIGHT = 270" in RTSP
    and "publishOwnedPreview(bitmap" in RTSP
    and "previewDispatchPending" in RTSP
    and "if (previewDispatchPending) return@synchronized null" in RTSP
    and "private const val PREVIEW_INTERVAL_MS = 100L" in RTSP
    and "private val reusablePixels = ThreadLocal<IntArray>()" in RTSP
    and "sourceRow = row * (height - 1) / (outputHeight - 1)" in RTSP
    and "sourceColumn = column * (width - 1) / (outputWidth - 1)" in RTSP
    and "PreviewView.ScaleType.FIT_CENTER" in ACTIVITY
    and "ImageView.ScaleType.FIT_CENTER" in ACTIVITY
    and "border:2px dashed" not in WEB,
)
check(
    "partial decoder frames fail explicitly instead of silently detecting zero",
    "MAX_PARTIAL_FRAME_FAILURES = 8" in RTSP
    and "partialFrameFailures++" in RTSP
    and '"This device\'s dashcam decoder did not expose complete frames."' in RTSP,
)
check(
    "terminal format failures and prolonged reconnect failures stop cleanly",
    '"Dashcam stream must use H.264 video"' in RTSP
    and "sourceGeneration.invalidate()" in RTSP
    and "if (reconnectAttempt >= MAX_RECONNECT_ATTEMPT)" in RTSP
    and '"Dashcam stayed unavailable after $MAX_RECONNECT_ATTEMPT reconnect attempts."' in RTSP
    and "playerActive = player != null" in RTSP
    and "if (!playerActive) return false" in RTSP
    and "mainHandler.removeCallbacks(watchdogRunnable)" in RTSP
    and "connectingStartedElapsedMs = 0L" in RTSP
    and "Publish ownership before prepare()" in RTSP,
)
check(
    "GPS uses decoder delivery time and never assumes the producer timestamp clock",
    "capturedAtElapsedMs = nowElapsed" in RTSP
    and "producer-defined timebase" in RTSP
    and "totalBufferedDuration" not in RTSP
    and "estimatedCaptureElapsed" not in RTSP,
)
check(
    "uncalibrated dashcam timing cannot claim precise routing or a repaired revisit",
    "internal object NativeEvidenceLocationPolicy" in SOURCE
    and "if (source == NativeFrameSourceKind.PHONE_CAMERA) phoneGpsAccuracy else null" in SOURCE
    and SERVICE.count("NativeEvidenceLocationPolicy.gpsAccuracyForEvidence(") >= 2
    and "!NativeEvidenceLocationPolicy.canVerifyRepair(captureSourceKind)" in SERVICE
    and "evidenceGpsAccuracy, item.fix.speedMps, item.fix.heading" in SERVICE
    and "sourceOffStatus(\"finalizing saved data\")" in SERVICE
    and '"Dashcam could not resume" else "Camera could not resume"' in SERVICE,
)
check(
    "dashcam background disclosure never claims phone-camera recording",
    'captureSource: NativeFrameSourceKind = NativeFrameSourceKind.PHONE_CAMERA' in NOTIFICATION
    and '"Drive Mode · Dashcam Active"' in NOTIFICATION
    and '"Dashcam active · selected complete frames saved · audio off"' in NOTIFICATION
    and "captureSource = captureSourceKind" in SERVICE
    and "publicNativeSourceText" in WEB
    and "rtsp:\\/\\/\\S+" in WEB,
)
check(
    "pending-start restoration retains dashcam identity",
    "pendingCaptureSource()" in SERVICE
    and "pendingAdmittedCaptureSource()" in SERVICE
    and "DriveForegroundService.admitStart(pending.requestId, sourceConfig.kind)" in PLUGIN
    and '"Connecting to dashcam and GPS"' in SERVICE,
)
check(
    "all shipped UI copies are exact",
    (ROOT / "android-app/www/index.html").read_bytes() == (ROOT / "static/index.html").read_bytes()
    and (ROOT / "docs/index.html").read_bytes() == (ROOT / "static/index.html").read_bytes()
    and (ROOT / "android-app/android/app/src/main/assets/public/index.html").read_bytes() ==
        (ROOT / "static/index.html").read_bytes(),
)

if failures:
    print("\nFAILED: " + "; ".join(failures))
    sys.exit(1)
print("native dashcam contract tests passed")
