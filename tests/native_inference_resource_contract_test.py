#!/usr/bin/env python3
"""Static guard for native inference memory, stream, and retry boundaries."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
DRIVE = ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/drive"
ENGINE = (DRIVE / "NativeInferenceEngine.kt").read_text()
TRANSPORT = (DRIVE / "NativeInferenceTransport.kt").read_text()
REQUEST = (DRIVE / "NativeInferenceRequest.kt").read_text()
DETECTION_CONTRACT = (DRIVE / "NativeDetectionContract.kt").read_text()
RETRY_POLICY = (DRIVE / "NativeDetectionRetryPolicy.kt").read_text()
REPAIR_CONTRACT = (DRIVE / "NativeRepairContract.kt").read_text()
INFERENCE = "\n".join((ENGINE, TRANSPORT, REQUEST, DETECTION_CONTRACT,
                       RETRY_POLICY, REPAIR_CONTRACT))
QUALITY = (DRIVE / "FrameQualityEvaluator.kt").read_text()
SERVICE = (DRIVE / "DriveForegroundService.kt").read_text()
failures = []


def check(label, condition):
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        failures.append(label)


analysis = ENGINE[ENGINE.index("suspend fun analyzeBurst("):
                  ENGINE.index("suspend fun verifyRepair(")]
repair = ENGINE[ENGINE.index("suspend fun verifyRepair("):
                ENGINE.index("private fun prepareDetectionImages(")]
detect_stream = TRANSPORT[TRANSPORT.index("fun detect("):
                          TRANSPORT.index("fun verifyRepair(")]
repair_stream = TRANSPORT[TRANSPORT.index("fun verifyRepair("):
                          TRANSPORT.index("private fun authorizedRequest(")]
http_failure = TRANSPORT[TRANSPORT.index("private fun httpFailure("):
                         TRANSPORT.index("private fun retryableFailure(")]
http_policy = TRANSPORT[TRANSPORT.index("internal fun isTransientInferenceFailure"):]
scan = SERVICE[SERVICE.index("private fun startScanLoop()"):
               SERVICE.index("private fun validatedPostBurstFix(")]
worker = SERVICE[SERVICE.index("private fun startInferenceWorker()"):
                 SERVICE.index("private fun startSessionLimitLoop()")]
call_gate = TRANSPORT[TRANSPORT.index("private val activeCalls"):
                      TRANSPORT.index("fun detect(")]

check(
    "complete-frame preparation is downscale-only and capped at 1280",
    "MAX_PREPARED_FRAME_DIMENSION = 1280" in QUALITY
    and "NativePreparedImageScale.downscaleOnly(" in QUALITY
    and "MAX_PREPARED_ROAD_DIMENSION" not in QUALITY
    and "ROAD_CROP_MAX_UPSCALE" not in QUALITY
    and "RoadRegionSelector" not in QUALITY
    and QUALITY.count("Bitmap.createBitmap(") == 1
    and "Bitmap.createBitmap(src.width, src.height" in QUALITY
    and ENGINE.count("FrameQualityEvaluator.MAX_PREPARED_FRAME_DIMENSION") >= 2
    and "1920" not in analysis
    and "1920" not in repair,
)
check(
    "burst transforms run sequentially and Base64 input lists are released before response wait",
    ENGINE.count("burstFrames.forEach { frame ->") >= 2
    and ENGINE.count("FrameQualityEvaluator.prepareDetectionFrameDataUrl(") >= 2
    and "prepareRoadBandDataUrl" not in INFERENCE
    and "imageUrls.clear()" in detect_stream
    and "imageUrls.clear()" in repair_stream,
)
check(
    "v19 detection and v2 repair contracts require complete uncropped camera frames",
    'PROMPT_VERSION = "pothole-binary-v19"' in DETECTION_CONTRACT
    and 'PROMPT_VERSION = "road-repair-v2"' in REPAIR_CONTRACT
    and "complete camera frames" in DETECTION_CONTRACT
    and "complete current camera" in REPAIR_CONTRACT
    and "No image is cropped, tiled, masked, or limited to a region of interest." in DETECTION_CONTRACT
    and "No current image is cropped, tiled, masked, or limited" in REPAIR_CONTRACT,
)
check(
    "temporary-surface decisions use a bounded complete-frame majority",
    "const val MAX_ATTEMPTS = 3" in RETRY_POLICY
    and "isVoteEligible" in RETRY_POLICY
    and "runBoundedDetectionAttempts {" in analysis
    and "prepareDetectionImages(burstFrames, primaryFrame)" in analysis
    and "allowEarlyReject = allowEarlyReject" in analysis
    and "allowEarlyReject = repairCandidate == null" in SERVICE
    and "NativeDetectionRetryPolicy.shouldRetry(attempts)" in RETRY_POLICY
    and "NativeDetectionRetryPolicy.acceptedByMajority(attempts)" in RETRY_POLICY
    and "asUnconfirmedTemporarySurface()" in RETRY_POLICY
    and "while (true)" not in analysis
    and "while (true)" not in RETRY_POLICY,
)
check(
    "raw inference is rendezvous-only, bounded to two consumers, and durably deferred",
    "capacity = Channel.RENDEZVOUS" in SERVICE
    and "BufferOverflow.DROP_OLDEST" not in SERVICE
    and "repeat(NativeLiveInferencePolicy.MAX_CONCURRENT_BURSTS)" in SERVICE
    and "const val MAX_CONCURRENT_BURSTS = 2" in
        (DRIVE / "NativeLiveInferencePolicy.kt").read_text()
    and "durable replay still owns the work" in SERVICE,
)
check(
    "only explicit transient HTTP statuses and transport receive retry delay",
    "code == 0 || code in setOf(408, 409, 425, 429) || code in 500..599" in http_policy
    and "if (!isTransientInferenceFailure(code)) return null" in http_policy,
)
check(
    "deterministic failures do not advance retry backoff or replay oversized output forever",
    detect_stream.count("httpFailure(") == 1
    and repair_stream.count("httpFailure(") == 1
    and http_failure.count("if (isTransientInferenceFailure(code))") == 1
    and 'readSse(call, responseBody, "OpenAI detection response")' in detect_stream
    and 'readSse(call, responseBody, "OpenAI repair-check response")' in repair_stream
    and '"$responseName exceeded the 64 KiB safety limit"' in TRANSPORT
    and "suspendInference = true" in TRANSPORT[TRANSPORT.index("private fun readSse("):],
)
check(
    "deterministic request failures suspend inference without stopping camera or filling pending bursts",
    "inferenceSuspendedReason" in SERVICE
    and "if (inferenceSuspendedReason != null) continue" in scan
    and "if (inferenceSuspendedReason != null) continue" in worker
    and "if (error.suspendInference)" in worker
    and "camera and local video continue" in SERVICE
    and "analysisCompleted = true" not in worker[worker.index("if (error.suspendInference)"):],
)
check(
    "both SSE outputs are bounded to 64 KiB",
    "const val MAX_UTF8_BYTES = 64 * 1024" in TRANSPORT
    and "if (!output.append(" in TRANSPORT
    and TRANSPORT.count("readSse(call, responseBody") == 2
    and "output.snapshot()" in TRANSPORT,
)
check(
    "structured Responses requests have small explicit output-token caps",
    "MAX_OUTPUT_TOKENS = 1_536" in DETECTION_CONTRACT
    and "MAX_OUTPUT_TOKENS = 768" in REPAIR_CONTRACT
    and "maxOutputTokens = NativeDetectionContract.MAX_OUTPUT_TOKENS" in REQUEST
    and "maxOutputTokens = NativeRepairContract.MAX_OUTPUT_TOKENS" in REQUEST
    and 'put("max_output_tokens", maxOutputTokens)' in REQUEST,
)
check(
    "Stop atomically blocks future HTTP calls and cancels every registered call",
    "private val activeCalls = mutableSetOf<Call>()" in TRANSPORT
    and "@Volatile private var closed = false" in call_gate
    and "synchronized(activeCallsLock)" in TRANSPORT
    and "if (closed) throw IOException" in TRANSPORT
    and "activeCalls.add(call)" in TRANSPORT
    and "activeCalls.remove(call)" in TRANSPORT
    and TRANSPORT.count("okHttpClient.newCall(request)") == 1
    and TRANSPORT.count("withTrackedCall(") == 3
    and "closed = true" in TRANSPORT[TRANSPORT.index("fun close()") :]
    and "activeCalls.toList()" in TRANSPORT[TRANSPORT.index("fun close()") :]
    and "calls.forEach { call -> attempt(call::cancel) }" in TRANSPORT,
)

if failures:
    print("\nFAILED: " + "; ".join(failures))
    sys.exit(1)
print("native inference resource contract tests passed")
