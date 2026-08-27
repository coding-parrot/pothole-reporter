"""Native image sync must page every row without retry loops on failed acknowledgements."""
import json
import pathlib
import subprocess
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
COPIES = [
    ROOT / "static" / "index.html",
    ROOT / "docs" / "index.html",
    ROOT / "android-app" / "www" / "index.html",
]
SCRIPT_COPIES = [
    ROOT / "static" / "standalone.js",
    ROOT / "docs" / "standalone.js",
    ROOT / "android-app" / "www" / "standalone.js",
]

failures = []
sync_sources = []
for path in COPIES:
    html = path.read_text()
    start = html.index("const NATIVE_IMAGE_BATCH_SIZE = 2;")
    end = html.index("async function finishNativeDrive", start)
    sync_sources.append(html[start:end])
if len(set(sync_sources)) != 1:
    failures.append("native paging logic differs across static, docs, and Android WebView copies")
sync_source = sync_sources[0]
script_sources = [path.read_text() for path in SCRIPT_COPIES]
if len(set(script_sources)) != 1:
    failures.append("repair-target API logic differs across static, docs, and Android WebView copies")
if "targets.slice(-2000).map" in script_sources[0]:
    failures.append("repair-target API still materializes every target photo in one call")
if 'if (path === "/api/repair-targets" && method === "POST")' not in script_sources[0]:
    failures.append("repair-target API has no bounded photo page endpoint")

harness = r"""
let nativeRepairSyncPromise = null;
let nativeWipeInProgress = false;
let activePlugin;
function nativeDrivePlugin() { return activePlugin; }
async function getNativeDriveHistory() { return {drives: []}; }
async function api(path, options) {
  if (path === "/api/native-report") activePlugin.reportPosts++;
  else if (path === "/api/native-repair") activePlugin.repairPosts++;
  else if (path === "/api/repair-targets" && (!options || !options.method)) {
    return {target_ids: [201, 202, 203, 204, 205]};
  } else if (path === "/api/repair-targets" && options.method === "POST") {
    const ids = JSON.parse(options.body).ids;
    activePlugin.targetPageSizes.push(ids.length);
    if (activePlugin.failTargetPage && activePlugin.targetPageSizes.length === 2) {
      return {targets: []};
    }
    return {targets: ids.map((id) => ({id, photo_data_url: "data:image/jpeg;base64,YQ=="}))};
  }
  return {};
}

function pluginWithRows(count, acknowledge) {
  const reportRows = Array.from({length: count}, (_, index) => ({id: index + 1}));
  const repairRows = Array.from({length: count}, (_, index) => ({id: index + 101}));
  const reportAck = new Set();
  const repairAck = new Set();
  return {
    reportLimits: [], repairLimits: [], reportPosts: 0, repairPosts: 0,
    targetPageSizes: [], bridgeBatchSizes: [], targetCommits: 0, targetAborts: 0,
    failTargetPage: false,
    syncReports: async ({limit}) => {
      const pending = reportRows.filter((row) => !reportAck.has(row.id));
      activePlugin.reportLimits.push(limit);
      return {reports: pending.slice(0, limit), remaining: pending.length};
    },
    acknowledgeReports: async ({ids}) => {
      if (acknowledge) ids.forEach((id) => reportAck.add(id));
      return {acknowledged: acknowledge ? ids.length : 0};
    },
    syncRepairObservations: async ({limit}) => {
      const pending = repairRows.filter((row) => !repairAck.has(row.id));
      activePlugin.repairLimits.push(limit);
      return {observations: pending.slice(0, limit), remaining: pending.length};
    },
    acknowledgeRepairObservations: async ({ids}) => {
      if (acknowledge) ids.forEach((id) => repairAck.add(id));
      return {acknowledged: acknowledge ? ids.length : 0};
    },
    beginRepairTargetSync: async ({ids}) => {
      activePlugin.targetManifest = ids.slice();
      return {token: "stage-token", expected: ids.length};
    },
    appendRepairTargetBatch: async ({token, offset, targets}) => {
      if (token !== "stage-token") throw new Error("wrong token");
      activePlugin.bridgeBatchSizes.push(targets.length);
      return {received: offset + targets.length};
    },
    commitRepairTargetSync: async ({token}) => {
      if (token !== "stage-token") throw new Error("wrong token");
      activePlugin.targetCommits++;
      return {replaced: activePlugin.targetManifest.length};
    },
    abortRepairTargetSync: async () => {
      activePlugin.targetAborts++;
      return {aborted: true};
    },
  };
}

(async () => {
  activePlugin = pluginWithRows(5, true);
  const complete = await performNativeDataSync();
  const completeProbe = {
    result: complete,
    reportLimits: activePlugin.reportLimits,
    repairLimits: activePlugin.repairLimits,
    reportPosts: activePlugin.reportPosts,
    repairPosts: activePlugin.repairPosts,
    targetPageSizes: activePlugin.targetPageSizes,
    bridgeBatchSizes: activePlugin.bridgeBatchSizes,
    targetCommits: activePlugin.targetCommits,
    targetAborts: activePlugin.targetAborts,
  };

  activePlugin = pluginWithRows(5, false);
  const blocked = await performNativeDataSync();
  const blockedProbe = {
    result: blocked,
    reportCalls: activePlugin.reportLimits.length,
    repairCalls: activePlugin.repairLimits.length,
    reportPosts: activePlugin.reportPosts,
    repairPosts: activePlugin.repairPosts,
    targetPageSizes: activePlugin.targetPageSizes,
    bridgeBatchSizes: activePlugin.bridgeBatchSizes,
    targetCommits: activePlugin.targetCommits,
  };

  activePlugin = pluginWithRows(0, true);
  activePlugin.failTargetPage = true;
  let stagingFailure = null;
  try { await syncNativeRepairData(activePlugin); }
  catch (error) { stagingFailure = String(error && error.message || error); }
  const failedStageProbe = {
    stagingFailure,
    targetPageSizes: activePlugin.targetPageSizes,
    bridgeBatchSizes: activePlugin.bridgeBatchSizes,
    targetCommits: activePlugin.targetCommits,
    targetAborts: activePlugin.targetAborts,
  };
  process.stdout.write(JSON.stringify({completeProbe, blockedProbe, failedStageProbe}));
})().catch((error) => {
  process.stderr.write(String(error && error.stack || error));
  process.exit(1);
});
"""

completed = subprocess.run(
    ["node", "-e", sync_source + "\n" + harness],
    cwd=ROOT,
    text=True,
    capture_output=True,
    check=False,
)
if completed.returncode:
    failures.append(f"sync harness failed: {completed.stderr.strip()}")
else:
    result = json.loads(completed.stdout)
    complete = result["completeProbe"]
    if complete["result"] != {"reports": 5, "repairs": 5, "drives": 0}:
        failures.append(f"five-row sync did not finish: {complete}")
    if complete["reportLimits"] != [2, 2, 2] or complete["repairLimits"] != [2, 2, 2]:
        failures.append(f"sync did not use bounded multi-page requests: {complete}")
    if complete["reportPosts"] != 5 or complete["repairPosts"] != 5:
        failures.append(f"sync skipped native rows: {complete}")
    if complete["targetPageSizes"] != [2, 2, 1] or complete["bridgeBatchSizes"] != [2, 2, 1]:
        failures.append(f"repair photos were not fetched and bridged in two-item pages: {complete}")
    if complete["targetCommits"] != 1 or complete["targetAborts"] != 0:
        failures.append(f"complete repair staging did not commit exactly once: {complete}")

    blocked = result["blockedProbe"]
    if blocked["result"] != {"reports": 0, "repairs": 0, "drives": 0}:
        failures.append(f"unacknowledged rows were counted as durable: {blocked}")
    if blocked["reportCalls"] != 1 or blocked["repairCalls"] != 1:
        failures.append(f"sync retried an unacknowledged page: {blocked}")
    if blocked["targetPageSizes"] != [2, 2, 1] or blocked["bridgeBatchSizes"] != [2, 2, 1]:
        failures.append(f"blocked outbox path violated repair photo paging: {blocked}")
    if blocked["targetCommits"] != 1:
        failures.append(f"blocked outbox path did not finish transactional repair staging: {blocked}")

    failed_stage = result["failedStageProbe"]
    if "history changed" not in (failed_stage["stagingFailure"] or ""):
        failures.append(f"malformed staged page did not fail closed: {failed_stage}")
    if failed_stage["targetPageSizes"] != [2, 2] \
            or failed_stage["bridgeBatchSizes"] != [2]:
        failures.append(f"failed stage crossed an unexpected image batch: {failed_stage}")
    if failed_stage["targetCommits"] != 0 or failed_stage["targetAborts"] != 1:
        failures.append(f"incomplete repair generation was not aborted: {failed_stage}")

if failures:
    print("FAIL")
    for failure in failures:
        print("  -", failure)
    sys.exit(1)
print("NATIVE BRIDGE PAGING TEST PASS")
