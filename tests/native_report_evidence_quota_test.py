#!/usr/bin/env python3
"""Static integration guard for the persistent report/repair evidence quota."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
DRIVE = ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/drive"
STORAGE = (DRIVE / "NativeReportEvidenceStorage.kt").read_text()
INFERENCE = (DRIVE / "NativeInferenceEngine.kt").read_text()
EVIDENCE_STORE = (DRIVE / "NativeInferenceEvidenceStore.kt").read_text()
PROTOCOL = (DRIVE / "NativeInferenceProtocol.kt").read_text()
SERVICE = (DRIVE / "DriveForegroundService.kt").read_text()
PLUGIN = (ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/plugin/DriveModePlugin.kt").read_text()
DAO = (ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/db/Daos.kt").read_text()
failures = []


def check(label, condition):
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        failures.append(label)


save = STORAGE[STORAGE.index("suspend fun saveJpegAtomically("):
               STORAGE.index("fun deleteVerified(")]
reserve = STORAGE[STORAGE.index("suspend fun reserveInferenceCapacity("):
                  STORAGE.index("suspend fun saveJpegAtomically(")]
analyze = INFERENCE[INFERENCE.index("suspend fun analyzeBurst("):
                    INFERENCE.index("suspend fun verifyRepair(")]
repair = INFERENCE[INFERENCE.index("suspend fun verifyRepair("):
                   INFERENCE.index("private fun prepareDetectionImages(")]
pruning = STORAGE[STORAGE.index("private suspend fun pruningCandidatesLocked("):
                  STORAGE.index("private fun managedDescendant(")]
clear_data = PLUGIN[PLUGIN.index("fun clearNativeData("):
                    PLUGIN.index("fun getDrives(")]
reconcile = PLUGIN[PLUGIN.index("private suspend fun reconcileNativeState("):
                   PLUGIN.index("private suspend fun reconcileNativeStateOnce(")]

check(
    "report and repair evidence has a separate 512 MiB hard quota",
    "const val MAX_TOTAL_BYTES = 512L * 1024L * 1024L" in STORAGE
    and "private val quota = NativeMediaStorageQuota(" in STORAGE
    and "maxTotalBytes = MAX_TOTAL_BYTES" in STORAGE,
)
check(
    "startup and inference lease reconcile disk truth before reserving",
    "NativeReportEvidenceStorage.initialize(context)" in PLUGIN
    and "reconcileFromDiskLocked(context)" in STORAGE
    and reserve.index("if (!quota.isReconciled()) reconcileFromDiskLocked(context)")
        < reserve.index("reserveAfterPruningLocked("),
)
check(
    "JPEG persistence consumes the preflight lease and commits only the verified file",
    "NativeMediaFilesystemMutation.mutex.withLock" in save
    and "lease.reservation" in save
    and "reserveAfterPruningLocked(" not in save
    and save.index("out.write(jpeg)") < save.index("quota.commit(reservation, file.length())")
    and "releaseInferenceCapacity(lease)" in save
    and "quota.noteUnexpectedExistingFile(survivors)" in save,
)
check(
    "detection and repair reserve capacity before paid requests and always release it",
    analyze.index("reserveInferenceCapacity(appContext)") < analyze.index("transport.detect(")
    < analyze.index("evidenceStore.saveDetection(")
    and repair.index("reserveInferenceCapacity(appContext)") < repair.index("transport.verifyRepair(")
    < repair.index("evidenceStore.saveRepair(")
    and "releaseInferenceCapacity(evidenceLease)" in analyze
    and "releaseInferenceCapacity(evidenceLease)" in repair
    and "suspendInference = true" in reserve,
)
check(
    "pruning preserves every report and repair owner plus the active producer",
    "getAllReportMediaRefs()" in pruning
    and "getAllPhotoPaths()" in pruning
    and "status.isRunning || status.isStopping" in pruning
    and "oldestUnowned(" in pruning
    and "SELECT currentPhotoPath FROM repair_observations" in DAO,
)
check(
    "pruning is deterministic by modification time and canonical path",
    # Policy candidates are canonicalized before sorting, so `path` is already the
    # canonical tie-breaker without a second filesystem resolution.
    "sortedWith(compareBy<File>({ it.lastModified() }, { it.path }))" in STORAGE
    and "it.path !in referencedCanonicalPaths" in STORAGE
    and "canonicalProtected.none" in STORAGE,
)
check(
    "both inference saves and all uncommitted cleanup are quota-aware",
    "NativeReportEvidenceStorage.saveJpegAtomically(" in EVIDENCE_STORE
    and "NativeReportEvidenceStorage.deleteVerified(file)" in PROTOCOL
    and SERVICE.count("NativeReportEvidenceStorage.deleteVerified(File(it))") >= 2,
)
check(
    "report and repair acknowledgements credit the quota only after Room commits",
    PLUGIN.count("cleanup = { NativeReportEvidenceStorage.deleteAll(files) }") >= 2
    and "NativeReportEvidenceStorage.deleteAll(managed)" in reconcile
    and "NativeReportEvidenceStorage.deleteVerified(canonical)" in reconcile,
)
check(
    "reconciliation and full wipe always restore exact quota truth",
    "NativeReportEvidenceStorage.reconcileFromDiskLocked(context)" in clear_data
    and "NativeReportEvidenceStorage.reconcileFromDiskLocked(context)" in PLUGIN[
        PLUGIN.index("private suspend fun reconcileNativeStateOnce("):],
)

if failures:
    print("\nFAILED: " + "; ".join(failures))
    sys.exit(1)
print("native report evidence quota tests passed")
