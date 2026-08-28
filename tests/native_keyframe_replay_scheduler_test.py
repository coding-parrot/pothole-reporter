#!/usr/bin/env python3
"""Behavioral checks for bounded native saved-frame replay scheduling."""

from pathlib import Path
import subprocess
import sys
import textwrap


ROOT = Path(__file__).resolve().parents[1]
WEB = (ROOT / "static/index.html").read_text()
PLUGIN = (ROOT / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/plugin/DriveModePlugin.kt").read_text()
START = WEB.index("function localDataUrlBlob(value)")
END = WEB.index("function gpsAt(track, seconds)", START)
REPLAY_SOURCE = WEB[START:END]

HARNESS = r"""
const assert = require("node:assert/strict");

let currentPlugin = null;
let invalidations = 0;
let reportLoads = 0;
let apiCalls = 0;
let nativeHostAppActive = true;
let nativeWipeInProgress = false;
let nativeCaptureFinalizingSessionId = null;
let drive = null;
let driveStarting = false;
const document = { visibilityState: "visible", querySelectorAll: () => [] };
const navigator = { onLine: true };
const localStorage = { getItem: (key) => key === "openai_key" ? "test-key" : null };
const nativeDrivePlugin = () => currentPlugin;
const invalidateNativeDriveHistory = () => { invalidations++; };
const loadReports = async () => { reportLoads++; };
const show = () => {};
const controls = new Map();
const $ = (id) => {
  if (!controls.has(id)) controls.set(id, {
    classList: { add() {}, remove() {} }, disabled: false, onclick: null,
    textContent: "", src: ""
  });
  return controls.get(id);
};
let api = async () => {
  apiCalls++;
  return { analyzed: true, found: false, duplicate: false };
};

__REPLAY_SOURCE__

const productionReplayNativeKeyframeBatch = replayNativeKeyframeBatch;

async function settleScheduler() {
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(nativeKeyframeAutoPromise, null);
}

async function testFailedPreferredSessionRotatesThroughCursorPages() {
  nativeKeyframeAutoDeferredUntil.clear();
  nativeKeyframeAutoPreferredSession = null;
  const pending = new Set(["100", "200", "300"]);
  const discoveryCursors = [];
  currentPlugin = {
    listPendingKeyframeSessions: async ({ afterSessionId, limit }) => {
      discoveryCursors.push(afterSessionId);
      const rows = [...pending].sort().filter((id) => id > afterSessionId).slice(0, limit);
      return {
        sessions: rows.map((sessionId) => ({ sessionId, pending: 1 })),
        nextAfterSessionId: rows.length ? rows[rows.length - 1] : afterSessionId,
      };
    },
  };
  const replayed = [];
  replayNativeKeyframeBatch = async (sessionId) => {
    replayed.push(String(sessionId));
    if (String(sessionId) === "300") {
      return { checked: 0, found: 0, duplicates: 0, failed: 1,
        attempted: 1, cancelled: false, remaining: 1 };
    }
    pending.delete(String(sessionId));
    return { checked: 1, found: 0, duplicates: 0, failed: 0,
      attempted: 1, cancelled: false, remaining: 0 };
  };

  await scheduleNativeKeyframeReplay("300");
  await settleScheduler();
  assert.deepEqual(replayed, ["300", "100", "200"],
    "a failed preferred/oldest session must not starve other pending sessions");
  assert.deepEqual(discoveryCursors, ["", "300"],
    "session discovery must advance its native keyset cursor");
  assert.ok(nativeKeyframeAutoBackoffTimer,
    "a failed session must schedule its own retry when the backoff expires");
  if (typeof nativeKeyframeAutoBackoffTimer.hasRef === "function") {
    assert.equal(nativeKeyframeAutoBackoffTimer.hasRef(), false,
      "the scheduler backoff must not keep the Node contract process alive");
  }

  await scheduleNativeKeyframeReplay();
  await settleScheduler();
  assert.deepEqual(replayed, ["300", "100", "200"],
    "the failed session must observe its backoff on an immediate later trigger");
}

async function testNewDriveYieldsAfterReadBeforeBillableInference() {
  replayNativeKeyframeBatch = productionReplayNativeKeyframeBatch;
  nativeKeyframeReplay = null;
  apiCalls = 0;
  driveStarting = false;
  let markCalls = 0;
  currentPlugin = {
    listKeyframes: async () => ({ keyframes: [{ id: 8 }], pending: 1 }),
    readKeyframe: async () => {
      driveStarting = true;
      return {
        id: 8, captureSeq: 5, capturedAtMs: 2234, sourceOffsetMs: 44,
        photos: ["data:image/jpeg;base64,AA=="], primaryIndex: 0,
      };
    },
    markKeyframeAnalyzed: async () => { markCalls++; return { analyzed: 8 }; },
  };

  const result = await productionReplayNativeKeyframeBatch("drive-8", false);
  driveStarting = false;
  assert.equal(result.cancelled, true);
  assert.equal(apiCalls, 0,
    "a newly starting Drive must win before saved-frame model inference begins");
  assert.equal(markCalls, 0, "an unread frame has nothing to checkpoint");
}

async function testNativeStoppingRetriesTheExactBridgeOperations() {
  replayNativeKeyframeBatch = productionReplayNativeKeyframeBatch;
  nativeKeyframeReplay = null;
  apiCalls = 0;
  let listCalls = 0;
  let readCalls = 0;
  let markCalls = 0;
  const stopping = () => new Error(
    "Failed to checkpoint saved frame: Stop Drive before analysing saved frames"
  );
  currentPlugin = {
    listKeyframes: async () => {
      listCalls++;
      if (listCalls === 1) throw stopping();
      return { keyframes: [{ id: 7 }], pending: 1 };
    },
    readKeyframe: async () => {
      readCalls++;
      if (readCalls === 1) throw stopping();
      return {
        id: 7, captureSeq: 4, capturedAtMs: 1234, sourceOffsetMs: 34,
        photos: ["data:image/jpeg;base64,AA=="], primaryIndex: 0,
      };
    },
    markKeyframeAnalyzed: async () => {
      markCalls++;
      if (markCalls === 1) throw stopping();
      return { analyzed: 7 };
    },
  };

  const result = await productionReplayNativeKeyframeBatch("drive-7", false);
  assert.equal(result.checked, 1);
  assert.equal(result.failed, 0);
  assert.equal(listCalls, 2, "list must retry after the native teardown race");
  assert.equal(readCalls, 2, "read must retry after the native teardown race");
  assert.equal(markCalls, 2, "mark must retry in place without repeating inference");
  assert.equal(apiCalls, 1, "retrying the native checkpoint must not rebill inference");
}

(async () => {
  await testFailedPreferredSessionRotatesThroughCursorPages();
  await testNativeStoppingRetriesTheExactBridgeOperations();
  await testNewDriveYieldsAfterReadBeforeBillableInference();
  console.log("native keyframe replay scheduler tests passed");
})().catch((error) => {
  console.error(error && error.stack || error);
  process.exit(1);
});
"""


def main() -> int:
    read_block = PLUGIN[PLUGIN.index("fun readKeyframe("):
                          PLUGIN.index("fun markKeyframeAnalyzed(")]
    mark_block = PLUGIN[PLUGIN.index("fun markKeyframeAnalyzed("):
                          PLUGIN.index("fun shareFootage(")]
    checkpoint_guard = PLUGIN[PLUGIN.index("private fun requireDriveReplayCheckpointIsSafe("):
                              PLUGIN.index("private fun parseGpsTrack(")]
    if ("requireDriveReplayCheckpointIsSafe(frame.sessionId)" not in mark_block
            or "status.sessionId == sessionId" not in checkpoint_guard):
        print("FAILED: an old replay result cannot checkpoint safely during a newer Drive",
              file=sys.stderr)
        return 1
    if ('call.data.optLong("id", -1L)' not in read_block
            or 'call.data.optLong("id", -1L)' not in mark_block
            or 'call.getLong("id")' in read_block
            or 'call.getLong("id")' in mark_block):
        print("FAILED: keyframe bridge IDs must accept Capacitor Integer and Long values",
              file=sys.stderr)
        return 1
    script = textwrap.dedent(HARNESS).replace("__REPLAY_SOURCE__", REPLAY_SOURCE)
    completed = subprocess.run(
        ["node", "-"],
        input=script,
        text=True,
        capture_output=True,
        cwd=ROOT,
        timeout=15,
        check=False,
    )
    if completed.stdout:
        print(completed.stdout.rstrip())
    if completed.stderr:
        print(completed.stderr.rstrip(), file=sys.stderr)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
