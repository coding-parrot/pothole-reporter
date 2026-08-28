#!/usr/bin/env python3
"""Contract for lossless bounded capture admission and automatic pending replay."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SERVICE = (ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/drive/DriveForegroundService.kt").read_text()
DAO = (ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/db/Daos.kt").read_text()
PLUGIN = (ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/plugin/DriveModePlugin.kt").read_text()
WEB = (ROOT / "android-app/www/index.html").read_text()
failures = []


def check(label, condition):
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        failures.append(label)


scan = SERVICE[SERVICE.index("private fun startScanLoop()"):
               SERVICE.index("private fun validatedPostBurstFix(")]
worker = SERVICE[SERVICE.index("private fun startInferenceWorker()"):
                 SERVICE.index("private fun startSessionLimitLoop()")]
replay = WEB[WEB.index("function nativeAutomaticReplayAllowed"):
             WEB.index("function gpsAt")]
finish = WEB[WEB.index("async function finishNativeDrive"):
             WEB.index("async function ensureNativeDriveListeners")]
quality = WEB[WEB.index("function roadFrameQuality(video)"):
              WEB.index("function bestBurstIndex", WEB.index("function roadFrameQuality(video)"))]

check(
    "every selected burst is indexed before it can enter the live network queue",
    "persistSelectedBurst(baseItem)" in scan
    and "if (keyframeId == null)" in scan
    and scan.index("persistSelectedBurst(baseItem)")
        < scan.index("jobChannel?.trySend(item)"),
)
check(
    "a persistence failure stops capture explicitly instead of inferring without evidence",
    "saved-frame storage is unavailable" in scan
    and scan.index("if (keyframeId == null)") < scan.index("stopDriveSession("),
)
check(
    "slow-network overflow remains memory bounded and describes durable deferral",
    "capacity = 1" in SERVICE
    and "BufferOverflow.DROP_OLDEST" in SERVICE
    and "every selected burst has already been saved" in SERVICE,
)
check(
    "native completion is checkpointed only after a complete detector verdict",
    "analysisCompleted = outcome?.analyzed == true" in worker
    and worker.index("analysisCompleted = outcome?.analyzed == true")
        < worker.index("markAnalyzed(keyframeId)"),
)
check(
    "bridge pages pending metadata and never loads an unbounded frame inventory",
    "ORDER BY captureSeq ASC LIMIT :limit" in DAO
    and "countPendingForSession" in DAO
    and "getSummaries()" in DAO
    and 'coerceIn(1, 100)' in PLUGIN
    and 'put("remaining"' in PLUGIN,
)
check(
    "saved frames automatically replay after stop and on a later safe foreground",
    "scheduleNativeKeyframeReplay(sessionId)" in WEB
    and "await scheduleNativeKeyframeReplay(sessionId)" not in WEB
    and "finally {" in finish[finish.index("await syncNativeData({ force: true })"):]
    and finish.index("await syncNativeData({ force: true })")
        < finish.index("scheduleNativeKeyframeReplay(sessionId)")
    and 'document.visibilityState === "visible"' in replay
    and "nativeHostAppActive" in replay
    and 'window.addEventListener("online"' in WEB
    and "scheduleNativeKeyframeReplay();" in WEB,
)
settings_save = WEB[WEB.index('$("setSave").onclick = async () => {'):
                    WEB.index("function missingFilesystemEntry")]
check(
    "saving an API key immediately resumes durable pending-frame replay",
    "await loadReports();" in settings_save
    and "scheduleNativeKeyframeReplay();" in settings_save
    and settings_save.index("await loadReports();")
        < settings_save.index("scheduleNativeKeyframeReplay();"),
)
check(
    "deferred native frames retain live-revisit repair semantics",
    'fd.append("capture_source", "drive_live")' in replay
    and 'fd.append("capture_source", "drive_vod")' not in replay,
)
check(
    "automatic replay preserves failed work and bounds bridge/image memory",
    "NATIVE_KEYFRAME_BATCH_SIZE = 25" in WEB
    and "NATIVE_AUTO_KEYFRAME_ATTEMPTS_PER_RUN = 100" in WEB
    and "result.analyzed !== true" in replay
    and replay.index('await api("/api/frame"')
        < replay.index("() => plugin.markKeyframeAnalyzed")
    and "if (result.failed)" in replay
    and "deferNativeReplaySession(activeSessionId)" in replay
    and "if (result.cancelled || result.checked === 0) break" in replay,
)
check(
    "automatic replay uses lightweight session discovery and stays on one paged session",
    "plugin.listPendingKeyframeSessions" in replay
    and "plugin.getDrives()" not in replay
    and "let activeSessionId = nativeKeyframeAutoPreferredSession" in replay
    and "activeSessionId = null" in replay,
)
check(
    "delete-all cancels and awaits replay before deleting native and Web data",
    "state.done = new Promise" in WEB
    and "if (nativeWipeInProgress)" in replay
    and "nativeWipeInProgress || state.cancelled" not in replay
    and "if (replayState) replayState.cancelled = true" in WEB
    and "replayState && replayState.done" in WEB,
)
check(
    "browser burst selection uses the same production orientation-aware road region",
    "StandaloneAPI.__pure.selectRoadRegion" in quality
    and "region.x, region.y, region.width, region.height" in quality
    and "video.videoHeight - sourceH" not in quality,
)

if failures:
    print("\nFAILED: " + "; ".join(failures))
    sys.exit(1)
print("native durable replay tests passed")
