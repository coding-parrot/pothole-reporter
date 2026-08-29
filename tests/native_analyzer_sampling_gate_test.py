#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAMERA = (ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/drive/NativeDriveCameraManager.kt").read_text()
SERVICE = (ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/drive/DriveForegroundService.kt").read_text()

setter = CAMERA[
    CAMERA.index("fun setAnalyzerSamplingEnabled"):
    CAMERA.index("private fun processImageProxy")
]
process = CAMERA[
    CAMERA.index("private fun processImageProxy"):
    CAMERA.index("suspend fun captureBurst")
]

assert "analyzerSamplingEnabled = enabled" in setter
assert "analyzerSamplingGeneration.invalidate()" in setter
assert "rollingFrames.clear()" in setter
assert "lastRollingSourceTimestampNs = 0L" in setter
assert all(token not in setter for token in (
    "bindCameraUseCases(", "releaseCameraUseCases(", "unbindAll(",
    "pauseCameraSafely(", "stopActiveRecording("
))

gate = process.index("NativeAnalyzerSamplingPolicy.shouldConvert(")
conversion = process.index("imageProxy.toBitmap()")
assert gate < conversion
assert "enabled = analyzerSamplingEnabled" in process[:conversion]
assert "windowFull = rollingFrames.size >= NativeRollingBurstWindow.CAPACITY" in process[:conversion]
assert "deliveredFramesSinceLastSample = eligibleFrameCount" in process[:conversion]
assert "sourceFrameStride = NativeRollingBurstWindow.SOURCE_FRAME_STRIDE" in process[:conversion]
assert "minimumGapNs = NativeRollingBurstWindow.SAMPLE_SPACING_NS" in process[:conversion]
assert "maximumGapNs = NativeRollingBurstWindow.MAX_SAMPLE_GAP_NS" in process[:conversion]
assert "!analyzerSamplingGeneration.isCurrent(samplingGeneration)" in process
assert process.index("!analyzerSamplingGeneration.isCurrent(samplingGeneration)") < process.index("rollingFrames.addLast(frame)")
assert "removeFirst()" not in process
assert process.index("rollingFrames.addLast(frame)") < process.index(
    "lastRollingSourceTimestampNs = sourceTimestampNs")
capture = CAMERA[CAMERA.index("suspend fun captureBurst()"):
                 CAMERA.index("private fun Bitmap.recycleSafely()")]
assert "NativeRollingBurstWindow.Disposition.DISCARD" in capture
assert "discarded.forEach { it.bitmap.recycleSafely() }" in capture

sampling_refresh = SERVICE[
    SERVICE.index("private fun updateAnalyzerSampling"):
    SERVICE.index("private fun requestCaptureInterlockRefresh")
]
assert "decision.canCapture" in sampling_refresh
assert "inferenceSuspendedReason == null" in sampling_refresh
assert "setAnalyzerSamplingEnabled(enabled)" in sampling_refresh
interlock_refresh = SERVICE[
    SERVICE.index("private fun refreshCaptureInterlock"):
    SERVICE.index("private suspend fun persistSelectedBurst")
]
assert "updateAnalyzerSampling(decision)" in interlock_refresh
assert "cameraManager?.setAnalyzerSamplingEnabled(false)" in SERVICE[
    SERVICE.index("catch (error: NativeInferenceException)"):
    SERVICE.index("catch (error: Exception)", SERVICE.index("catch (error: NativeInferenceException)"))
]

print("NATIVE ANALYZER SAMPLING GATE PASS")
