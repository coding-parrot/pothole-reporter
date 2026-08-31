#!/usr/bin/env python3
"""Static release guard for bounded, concurrent live inference admission."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
DRIVE = ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/drive"
SERVICE = (DRIVE / "DriveForegroundService.kt").read_text()
POLICY = (DRIVE / "NativeLiveInferencePolicy.kt").read_text()
TRANSPORT = (DRIVE / "NativeInferenceTransport.kt").read_text()
failures = []


def check(label, condition):
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        failures.append(label)


scan = SERVICE[SERVICE.index("private fun startScanLoop()"):
               SERVICE.index("private fun validatedPostBurstFix(")]
workers = SERVICE[SERVICE.index("private fun startInferenceWorker()"):
                  SERVICE.index("private fun startSessionLimitLoop()")]

check(
    "every candidate is durable before live admission",
    scan.index("persistSelectedBurst(baseItem)") < scan.index("jobChannel?.trySend(item)")
    and "durable replay still owns the work" in scan,
)
check(
    "raw bursts are never buffered behind active inference",
    "capacity = Channel.RENDEZVOUS" in SERVICE
    and "BufferOverflow" not in SERVICE,
)
check(
    "exactly two live consumers reduce starvation with an explicit memory bound",
    "const val MAX_CONCURRENT_BURSTS = 2" in POLICY
    and "repeat(NativeLiveInferencePolicy.MAX_CONCURRENT_BURSTS)" in workers
    and "launch { consumeInferenceJobs(channel) }" in workers,
)
check(
    "failed handoffs release only raw memory and retain durable replay",
    "workLedger.deferLive()" in scan
    and "recycle(item)" in scan[scan.index("jobChannel?.trySend(item)"):],
)
check(
    "concurrent report and duplicate totals cannot lose increments",
    "private val foundCounter = AtomicInteger(0)" in SERVICE
    and "foundCounter.incrementAndGet()" in workers
    and "private val alreadyCount: Int get() = duplicateIds.size" in SERVICE,
)
check(
    "shared transport backoff is atomic and Stop tracks all active calls",
    "private val consecutiveRetryableFailures = AtomicInteger(0)" in TRANSPORT
    and "consecutiveRetryableFailures.getAndIncrement()" in TRANSPORT
    and "consecutiveRetryableFailures.set(0)" in TRANSPORT
    and "private val activeCalls = mutableSetOf<Call>()" in TRANSPORT
    and "calls.forEach { call -> attempt(call::cancel) }" in TRANSPORT,
)

if failures:
    print("\nFAILED: " + "; ".join(failures))
    sys.exit(1)
print("native live-inference backpressure tests passed")
