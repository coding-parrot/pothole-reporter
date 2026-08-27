#!/usr/bin/env python3
"""Native duplicate evidence hand-off and acknowledgement race contract."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
DRIVE = ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/drive"
PLUGIN = ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/plugin"
DEDUPE = (DRIVE / "NativeDeduplicationEngine.kt").read_text()
SERVICE = (DRIVE / "DriveForegroundService.kt").read_text()
BRIDGE = (PLUGIN / "DriveModePlugin.kt").read_text()
failures = []


def check(label, condition):
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        failures.append(label)


dedupe_start = DEDUPE.index("suspend fun checkAndCommitReport(")
dedupe_end = DEDUPE.index("private suspend fun matchRoadEvent", dedupe_start)
dedupe_transaction = DEDUPE[dedupe_start:dedupe_end]
ack_start = BRIDGE.index("fun acknowledgeReports(")
ack_end = BRIDGE.index("fun beginRepairTargetSync(", ack_start)
acknowledgement = BRIDGE[ack_start:ack_end]

check(
    "dedupe and acknowledgement share one media mutation lock",
    "NativeMediaFilesystemMutation.mutex.withLock" in dedupe_transaction
    and "NativeMediaFilesystemMutation.mutex.withLock" in acknowledgement
    and dedupe_transaction.index("NativeMediaFilesystemMutation.mutex.withLock")
    < dedupe_transaction.index("database.withTransaction"),
)
check(
    "a scrubbed or partial canonical row adopts complete candidate evidence",
    "priorEvidenceIncomplete" in DEDUPE
    and "candidateFullPath" in DEDUPE
    and "candidateThumbnail" in DEDUPE
    and "candidateEvidenceAdopted = merged.candidateEvidenceAdopted" in DEDUPE,
)
check(
    "displaced legacy evidence is left for reconciliation after ownership changes",
    "priorEvidenceDisplaced" in DEDUPE
    and "NativeMediaReconciliationEpoch.invalidate()" in dedupe_transaction
    and "intentionally not deleted here" in dedupe_transaction,
)
check(
    "service transfers file cleanup ownership only after a durable adoption",
    "!it.isDuplicate || it.candidateEvidenceAdopted" in SERVICE
    and "uncommittedReportPhoto = null" in SERVICE
    and "uncommittedReportPhoto?.let" in SERVICE
    and "NativeRetryableFileCleanup.deleteVerified(File(it))" in SERVICE,
)
check(
    "the bridge still requires a thumbnail and full-resolution evidence",
    "report.photoDataUrlChars" in BRIDGE
    and "val requiredFullPath = report.photoFullPath" in BRIDGE
    and "?: report.photoPath" in BRIDGE,
)

if failures:
    print("\nFAILED: " + "; ".join(failures))
    sys.exit(1)
print("native duplicate revisit contract tests passed")
