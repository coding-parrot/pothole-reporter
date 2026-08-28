#!/usr/bin/env python3
"""Static guard for native inference memory, stream, and retry boundaries."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
DRIVE = ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/drive"
INFERENCE = (DRIVE / "NativeInferenceEngine.kt").read_text()
QUALITY = (DRIVE / "FrameQualityEvaluator.kt").read_text()
SERVICE = (DRIVE / "DriveForegroundService.kt").read_text()
failures = []


def check(label, condition):
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        failures.append(label)


analysis = INFERENCE[INFERENCE.index("suspend fun analyzeBurst("):
                     INFERENCE.index("suspend fun verifyRepair(")]
repair = INFERENCE[INFERENCE.index("suspend fun verifyRepair("):
                   INFERENCE.index("private fun executeOaiStreaming(")]
http_policy = INFERENCE[INFERENCE.index("internal object NativeInferenceHttpFailurePolicy"):
                        INFERENCE.index("internal object NativeInferenceEvidenceOwnership")]
detect_stream = INFERENCE[INFERENCE.index("private fun executeOaiStreaming("):
                          INFERENCE.index("private fun executeRepairStreaming(")]
repair_stream = INFERENCE[INFERENCE.index("private fun executeRepairStreaming("):
                          INFERENCE.index("private fun buildRequestBody(")]
scan = SERVICE[SERVICE.index("private fun startScanLoop()"):
               SERVICE.index("private fun validatedPostBurstFix(")]
worker = SERVICE[SERVICE.index("private fun startInferenceWorker()"):
                 SERVICE.index("private fun startSessionLimitLoop()")]
call_gate = INFERENCE[INFERENCE.index("private val activeCallLock"):
                      INFERENCE.index("private fun retryableFailure")]

check(
    "road-band preparation is downscale-only and capped at 1280",
    "MAX_PREPARED_ROAD_DIMENSION = 1280" in QUALITY
    and "NativePreparedImageScale.downscaleOnly(" in QUALITY
    and "ROAD_CROP_MAX_UPSCALE" not in QUALITY
    and "FrameQualityEvaluator.MAX_PREPARED_ROAD_DIMENSION" in analysis
    and "FrameQualityEvaluator.MAX_PREPARED_ROAD_DIMENSION" in repair
    and "1920" not in analysis
    and "1920" not in repair,
)
check(
    "burst transforms run sequentially and Base64 input lists are released before response wait",
    "burstFrames.forEach { frame ->" in analysis
    and "burstFrames.forEach { frame ->" in repair
    and "burstFrames.map { FrameQualityEvaluator.prepareRoadBandDataUrl" not in INFERENCE
    and "imageUrls.clear()" in detect_stream
    and "imageUrls.clear()" in repair_stream,
)
check(
    "raw inference queue is rendezvous-only and defers work from durable evidence",
    "capacity = Channel.RENDEZVOUS" in SERVICE
    and "BufferOverflow.DROP_OLDEST" not in SERVICE
    and "durable replay still owns the work" in SERVICE,
)
check(
    "only explicit transient HTTP statuses and transport receive retry delay",
    "TRANSIENT_HTTP_CODES = setOf(408, 409, 425, 429)" in http_policy
    and "code == 0 || code in TRANSIENT_HTTP_CODES || code in 500..599" in http_policy
    and "if (!isTransient(code)) return null" in http_policy
    and "shouldSuspendInference(code: Int)" in http_policy,
)
check(
    "deterministic failures do not advance retry backoff or replay oversized output forever",
    detect_stream.count("if (NativeInferenceHttpFailurePolicy.isTransient(code))") == 1
    and repair_stream.count("if (NativeInferenceHttpFailurePolicy.isTransient(code))") == 1
    and "OpenAI detection response exceeded the 64 KiB safety limit" in detect_stream
    and "suspendInference = true" in detect_stream
    and "OpenAI repair-check response exceeded the 64 KiB safety limit" in repair_stream
    and "suspendInference = true" in repair_stream,
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
    "const val MAX_UTF8_BYTES = 64 * 1024" in INFERENCE
    and detect_stream.count("textAccumulator.append(") >= 1
    and repair_stream.count("textAccumulator.append(") >= 1
    and "textAccumulator.snapshot()" in detect_stream
    and "textAccumulator.snapshot()" in repair_stream,
)
check(
    "structured Responses requests have small explicit output-token caps",
    "MAX_DETECTION_OUTPUT_TOKENS = 1_536" in INFERENCE
    and "MAX_REPAIR_OUTPUT_TOKENS = 768" in INFERENCE
    and 'req.put("max_output_tokens", MAX_DETECTION_OUTPUT_TOKENS)' in INFERENCE
    and 'put("max_output_tokens", MAX_REPAIR_OUTPUT_TOKENS)' in INFERENCE,
)
check(
    "Stop atomically blocks future HTTP calls and cancels every registered call",
    "private val activeCalls = mutableSetOf<Call>()" in call_gate
    and "@Volatile private var engineClosed = false" in call_gate
    and "synchronized(activeCallLock)" in call_gate
    and "if (engineClosed) throw IOException" in call_gate
    and "activeCalls.add(call)" in call_gate
    and "activeCalls.remove(call)" in call_gate
    and INFERENCE.count("okHttpClient.newCall(request)") == 1
    and INFERENCE.count("withTrackedCall(request)") == 3
    and "engineClosed = true" in INFERENCE[INFERENCE.index("fun close()") :]
    and "activeCalls.toList()" in INFERENCE[INFERENCE.index("fun close()") :]
    and "calls.forEach { call -> attempt(call::cancel) }" in INFERENCE,
)

if failures:
    print("\nFAILED: " + "; ".join(failures))
    sys.exit(1)
print("native inference resource contract tests passed")
