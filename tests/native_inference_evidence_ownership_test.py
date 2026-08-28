#!/usr/bin/env python3
"""Static integration guard for exception-safe report/repair evidence ownership."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
DRIVE = ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/drive"
INFERENCE = (DRIVE / "NativeInferenceEngine.kt").read_text()
SERVICE = (DRIVE / "DriveForegroundService.kt").read_text()
REPORT_STORAGE = (DRIVE / "NativeReportEvidenceStorage.kt").read_text()
failures = []


def check(label, condition):
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        failures.append(label)


analysis = INFERENCE[INFERENCE.index("suspend fun analyzeBurst("):
                     INFERENCE.index("suspend fun verifyRepair(")]
repair = INFERENCE[INFERENCE.index("suspend fun verifyRepair("):
                   INFERENCE.index("private fun executeOaiStreaming(")]
worker = SERVICE[SERVICE.index("private fun startInferenceWorker()"):
                 SERVICE.index("private fun startSessionLimitLoop()")]
handoff = INFERENCE[INFERENCE.index("internal object NativeInferenceEvidenceOwnership"):
                    INFERENCE.index("class NativeInferenceEngine(")]

check(
    "report evidence is handed off immediately after commit and before fallible allocation",
    "onEvidenceSaved: (String) -> Unit" in analysis
    and analysis.index("saveEvidenceImage(")
        < analysis.index("NativeInferenceEvidenceOwnership.handOff(photoFile, onEvidenceSaved)")
        < analysis.index("bitmapToBoundedJpegBytes("),
)
check(
    "repair evidence is handed off before result construction",
    "onEvidenceSaved: (String) -> Unit" in repair
    and repair.index("saveRepairEvidenceImage(")
        < repair.index("NativeInferenceEvidenceOwnership.handOff(currentPhoto, onEvidenceSaved)")
        < repair.index("RepairVerificationResult("),
)
check(
    "the service records both paths inside its existing worker-level cleanup boundary",
    "onEvidenceSaved = { uncommittedReportPhoto = it }" in worker
    and "onEvidenceSaved = { uncommittedRepairPhoto = it }" in worker
    and "uncommittedReportPhoto?.let" in worker
    and "uncommittedRepairPhoto?.let" in worker
    and worker.index("var uncommittedReportPhoto")
        < worker.index("onEvidenceSaved = { uncommittedReportPhoto = it }")
        < worker.index("uncommittedReportPhoto?.let"),
)
check(
    "a receiver failure deletes the unclaimed file and preserves the original failure",
    "catch (error: Throwable)" in handoff
    and "NativeReportEvidenceStorage.deleteVerified(file)" in handoff
    and "NativeRetryableFileCleanup.deleteVerified(file)" in REPORT_STORAGE
    and "throw error" in handoff,
)

if failures:
    print("\nFAILED: " + "; ".join(failures))
    sys.exit(1)
print("native inference evidence ownership tests passed")
