#!/usr/bin/env python3
"""Contract for bounded Room/filesystem keyframe ownership reconciliation."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
DAO = (ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/db/Daos.kt").read_text()
PLUGIN = (ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/plugin/DriveModePlugin.kt").read_text()
PAGING = (ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/plugin/NativeKeyframeOwnershipPaging.kt").read_text()
failures = []


def check(label, condition):
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        failures.append(label)


projection = DAO[DAO.index("data class DriveKeyframeOwnershipRef"):
                 DAO.index("data class PendingKeyframeSession")]
reconciliation = PLUGIN[PLUGIN.index("private suspend fun reconcileNativeState("):
                        PLUGIN.index("private suspend fun reconcileNativeStateOnce(")]
pending_api = PLUGIN[PLUGIN.index("fun listPendingKeyframeSessions("):
                     PLUGIN.index("fun listKeyframes(")]

check(
    "ownership projection contains only the row and path identity needed for cleanup",
    all(field in projection for field in ("val id: Long", "val sessionId: String", "val filePath: String"))
    and all(field not in projection for field in ("capturedAtMs", "lat:", "bytes:", "liveAnalyzed")),
)
check(
    "ownership row validation is a finite snapshot traversed in keyset pages",
    "ORDER BY id DESC LIMIT 1" in DAO
    and "WHERE id > :afterId AND id <= :throughId" in DAO
    and "ORDER BY id ASC LIMIT :limit" in DAO
    and "getNewestOwnershipId()" in reconciliation
    and "getOwnershipPage(" in reconciliation
    and "ROW_PAGE_SIZE = 128" in PAGING,
)
check(
    "orphan lookup is bounded by a session and a fixed candidate page",
    "WHERE sessionId = :sessionId AND filePath IN (:storedPaths)" in DAO
    and "ORDER BY id ASC LIMIT :limit" in DAO
    and "FILE_PAGE_SIZE = 128" in PAGING
    and "OWNER_QUERY_LIMIT = FILE_PAGE_SIZE * MAX_OWNER_CANDIDATES_PER_FILE" in PAGING
    and ".chunked(NativeKeyframeOwnershipPaging.FILE_PAGE_SIZE)" in reconciliation
    and "getOwnershipPageForSession(" in reconciliation,
)
check(
    "reconciliation no longer hydrates or flattens the full keyframe table",
    "driveKeyframeDao().getAll()" not in reconciliation
    and "suspend fun getAll(): List<DriveKeyframeEntity>" not in DAO
    and "indexedKeyframes" not in reconciliation
    and "indexedKeyframePaths" not in reconciliation
    and "validKeyframeFiles.values.flatten()" not in reconciliation
    and "unownedKeyframeFiles" not in reconciliation,
)
check(
    "active-session orphan deferral and verified inactive cleanup remain explicit",
    "wasActiveAtInventory" in reconciliation
    and "becameActiveDuringInventory" in reconciliation
    and "DriveForegroundService.status()" in reconciliation
    and "progress.markCleanupIncomplete()" in reconciliation
    and "progress.deleteFileVerified(canonical, footage = true)" in reconciliation
    and ".onFail { _, _ -> progress.markCleanupIncomplete() }" in reconciliation,
)
check(
    "automatic replay can page pending stopped sessions without loading drive history",
    "getPendingSessionPage(" in DAO
    and "sessions.status IN ('stopped', 'interrupted')" in DAO
    and "drive_keyframes.sessionId > :afterSessionId" in DAO
    and "ORDER BY drive_keyframes.sessionId ASC LIMIT :limit" in DAO
    and "getPendingSessionPage(afterSessionId, requested)" in pending_api
    and "reconcileNativeStateOnce(db)" in pending_api
    and "getDrives" not in pending_api
    and "getAllSegments" not in pending_api
    and "getAllSessions" not in pending_api,
)
check(
    "valid keyframes recover a missing parent session in bounded crash-recovery pages",
    "LEFT JOIN sessions ON sessions.id = drive_keyframes.sessionId" in DAO
    and "WHERE sessions.id IS NULL" in DAO
    and "MIN(drive_keyframes.capturedAtMs) AS firstCapturedAtMs" in DAO
    and "drive_keyframes.sessionId > :afterSessionId" in DAO
    and "getMissingSessionPage(" in reconciliation
    and "recoveredSessionState(" in reconciliation
    and "insertSessionIfMissing(" in reconciliation
    and "OnConflictStrategy.IGNORE" in DAO,
)

if failures:
    print("\nFAILED: " + "; ".join(failures))
    sys.exit(1)
print("native keyframe reconciliation paging tests passed")
