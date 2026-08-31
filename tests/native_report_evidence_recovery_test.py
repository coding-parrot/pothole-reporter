#!/usr/bin/env python3
"""Native accepted-report evidence must either bridge completely or replay its exact keyframe."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
DRIVE = ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/drive"
PLUGIN = ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/plugin"
DB = (ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/db/Daos.kt").read_text()
ENGINE = (DRIVE / "NativeInferenceEngine.kt").read_text()
FACTORY = (DRIVE / "NativeInferenceReportFactory.kt").read_text()
DEDUPE = (DRIVE / "NativeDeduplicationEngine.kt").read_text()
BRIDGE = (PLUGIN / "DriveModePlugin.kt").read_text()
POLICY = (PLUGIN / "NativeReportEvidenceRecoveryPolicy.kt").read_text()
failures = []


def check(label, condition):
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        failures.append(label)


accepted = ENGINE[ENGINE.index("val photoFile = evidenceStore.saveDetection("):
                  ENGINE.index("} finally {", ENGINE.index("val photoFile = evidenceStore.saveDetection("))]
commit = DEDUPE[DEDUPE.index("suspend fun checkAndCommitReport("):
                DEDUPE.index("private suspend fun matchRoadEvent")]
reconcile = BRIDGE[BRIDGE.index("private suspend fun reconcileNativeState("):
                   BRIDGE.index("private suspend fun reconcileNativeStateOnce")]
recovery = BRIDGE[BRIDGE.index("private suspend fun recoverBrokenUnsyncedReport("):
                  BRIDGE.index("private suspend fun reconcileNativeStateOnce")]

check(
    "thumbnail failure occurs before report construction and leaves file cleanup owned",
    accepted.index("publishEvidence(photoFile, onEvidenceSaved)")
    < accepted.index("val thumbnailDataUrl")
    < accepted.index("createInferenceOutcome(")
    and "?: throw NativeInferenceException(" in accepted
    and "val thumbnailDataUrl: String," in FACTORY
    and "input.thumbnailDataUrl.isNotBlank()" in FACTORY,
)
check(
    "dedupe rejects partial candidates before taking its mutation lock",
    "requireCompleteNativeReportEvidence(candidate)" in commit
    and "val managedFullPath = candidate.photoFullPath" in DEDUPE
    and commit.index("requireCompleteNativeReportEvidence(candidate)")
    < commit.index("NativeMediaFilesystemMutation.mutex.withLock"),
)
check(
    "report recovery scans a frozen bounded keyset instead of hydrating all rows",
    "getNewestReportMediaId()" in reconcile
    and "getReportMediaRefPage(" in reconcile
    and "NativeReportEvidenceRecoveryPolicy.REPORT_PAGE_SIZE" in reconcile
    and "getAllReportMediaRefs()" not in reconcile,
)
check(
    "only unsynced unbridgeable rows enter recovery while synced scrubbed rows are preserved",
    "if (report.syncedToWeb)" in reconcile
    and "reportEvidenceIsBridgeable(report, reportDao, reportsRoot)" in reconcile
    and "recoverBrokenUnsyncedReport(db, report)" in reconcile
    and "if (current.syncedToWeb) return@withTransaction" in recovery,
)
check(
    "recovery uses exact source identity and a complete bounded keyframe",
    'val prefix = "live:$sessionId:"' in POLICY
    and "sourceEventKey != \"$prefix$captureSeq\"" in POLICY
    and "getBySessionAndCaptureSeq" in recovery
    and "stored.isComplete && stored.hasTemporalContext" in recovery
    and "NativeBridgeJpeg.readExact(" in recovery,
)
check(
    "broken row removal and exact replay checkpoint are one Room transaction",
    "db.withTransaction" in recovery
    and "deleteSightingsForReport(report.id)" in recovery
    and "deleteReport(report.id)" in recovery
    and "markPending(it)" in recovery
    and recovery.index("deleteReport(report.id)") < recovery.index("markPending(it)"),
)
check(
    "DAO exposes bounded report pages and exact keyframe recovery mutations",
    "getReportMediaRefPage(" in DB
    and "getBySessionAndCaptureSeq(" in DB
    and "UPDATE drive_keyframes SET liveAnalyzed = 0" in DB
    and "DELETE FROM reports WHERE id = :id" in DB,
)

if failures:
    print("\nFAILED: " + "; ".join(failures))
    sys.exit(1)
print("native report evidence recovery tests passed")
