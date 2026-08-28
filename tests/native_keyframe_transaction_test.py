#!/usr/bin/env python3
"""Static service contract for video-independent, atomic three-view keyframes."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SERVICE = (
    ROOT
    / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/drive/DriveForegroundService.kt"
).read_text()

start = SERVICE.index("private suspend fun persistSelectedBurst(")
end = SERVICE.index("private fun startInferenceWorker()", start)
transaction = SERVICE[start:end]
failures = []


def check(label, condition):
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        failures.append(label)


check(
    "saved-frame persistence is independent of the continuous-video toggle",
    "recordingEnabled" not in transaction,
)
check(
    "one capture writes and commits one primary plus two temporal context frames",
    "val sourceIndexes = listOf(primaryIndex) + contextIndexes" in transaction
    and "item.burstFrames.map(BurstFrame::capturedAtElapsedMs)" in transaction
    and "encodedBySourceIndex.size != 3" in transaction
    and "listOf(plan.primarySourceIndex) + plan.contextSourceIndexes" in transaction
    and "temporaryFiles.zip(encodedFrames).forEach" in transaction
    and "temporaryFiles.drop(1).zip(plan.files.drop(1))" in transaction
    and "commitKeyframeFile(temporaryFiles.first(), plan.primaryFile)" in transaction,
)
check(
    "the three committed files are represented by one Room row with aggregate bytes",
    transaction.count("DriveKeyframeEntity(") == 1
    and transaction.count("insertKeyframe(") == 1
    and "val actualBytes = plan.files.sumOf(File::length)" in transaction
    and "filePath = plan.primaryFile.absolutePath" in transaction
    and "bytes = actualBytes" in transaction,
)
check(
    "every transactional failure path owns destinations plus temps and reconciles quota",
    transaction.count("cleanFailedKeyframeWrite(") == 3
    and "val temporaryFiles = plan.files.map" in transaction
    and "NativeKeyframeFailureCleanup.cleanup(" in transaction
    and "storage.noteDeletedMediaBytes(cleanup.removedAccountedBytes)" in transaction
    and "storage.releaseMediaBytes(reservation)" in transaction
    and "storage.noteUnexpectedMediaBytes(cleanup.remainingUnaccountedBytes)" in transaction
    and "NativeRetryableFileCleanup.deleteVerified(temporary) && destination.isFile" in transaction,
)

if failures:
    print("\nFAILED: " + "; ".join(failures))
    sys.exit(1)
print("native keyframe transaction tests passed")
