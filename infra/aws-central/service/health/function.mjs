// The scheduled health run: the rules of window.mjs and canary.mjs, run inside the stack
// by EventBridge instead of by a GitHub schedule (which fired every 4 to 5 hours when
// asked for every hour, and needed AWS keys stored in GitHub).
//
// The event says what to run, and each schedule rule in template.yaml sends its own:
//   {"window": "6h"}                     the log window rules over the last six hours
//   {"canary": "reads"}                  health, the map and the impact figures
//   {"canary": "full"}                   also an install, one detection, three lookups
//   {"window": "6h", "canary": "reads"}  both, the window first
//
// How the news travels. An unhealthy run is not an error: the function returns, and says
// what it found in two places. The whole report goes to the log as one entry. One JSON
// line follows it, which is a CloudWatch embedded metric line whenever something is
// broken: HealthBrokenRules (the number of broken log rules) and HealthCanaryFailed (1).
// The stack's alarms are on those two metrics. A healthy run writes the same line with
// no metric in it: a custom metric is charged for every hour it receives a value, USD
// 0.30 a month each if written every run, and the alarms read no data as healthy. A run
// that does not finish (a crash, a timeout, a function that was never invoked) writes
// nothing at all, and the third alarm, on the function's own Invocations and Errors,
// reads that silence as a failure.

import { runCanary } from "./canary.mjs";
import { createStoredIdentity } from "./canary-key.mjs";
import { createInsights } from "./insights.mjs";
import { createReport } from "./report.mjs";
import { judgeWindow, parseWindow } from "./window.mjs";

export const BROKEN_RULES_METRIC = "HealthBrokenRules";
export const CANARY_FAILED_METRIC = "HealthCanaryFailed";

function planFrom(event) {
  const window = event?.window ?? null;
  const canary = event?.canary ?? null;
  if (canary !== null && canary !== "reads" && canary !== "full") {
    throw new Error(`canary is "reads" or "full", not ${JSON.stringify(canary)}`);
  }
  if (!window && !canary) {
    throw new Error('nothing to run: the event names a window ({"window":"6h"}), a canary ({"canary":"reads"} or {"canary":"full"}) or both');
  }
  return { window, canary };
}

// One line of JSON for the log. Metrics at zero are left out, and with them the `_aws`
// block that makes CloudWatch read the line as metrics. No dimensions: a dimension set
// is a separate metric, and a separate charge.
export function metricLine({ namespace, timestamp, metrics, fields }) {
  const present = Object.entries(metrics).filter(([, value]) => value > 0);
  return JSON.stringify({
    ...(present.length ? { _aws: { Timestamp: timestamp, CloudWatchMetrics: [{
      Namespace: namespace, Dimensions: [[]], Metrics: present.map(([Name]) => ({ Name, Unit: "Count" })),
    }] } } : {}),
    ...fields,
    ...Object.fromEntries(present),
  });
}

export function createHealthFunction({ apiUrl, logGroup, namespace, keyParameter, logs, parameters, fetch, readImage, log, emit,
  now = Date.now, sleep }) {
  // Kept for the life of the function instance, so a warm run reads the key store once.
  const identity = createStoredIdentity({ parameters, name: keyParameter });

  return async function health(event) {
    const plan = planFrom(event);
    const startedAt = now();
    const report = createReport();
    let scannedBytes = 0;
    let installId = null;
    // The two halves fail apart: a log that cannot be queried must not stop the canary.
    const attempt = async (part, run) => {
      try { await run(); } catch (error) { report.crashed(error, part); }
    };
    if (plan.window) {
      await attempt("window", async () => judgeWindow({
        // Asked twice a second and given 45 s: a six hour window answered in 2 to 4 s
        // on 7 Oct 2026.
        query: createInsights({ logs, logGroupName: logGroup, pollMs: 500, patienceMs: 45_000, now, sleep,
          onScanned: (bytes) => { scannedBytes += bytes; } }),
        hours: parseWindow(plan.window), logGroup, report,
      }));
    }
    if (plan.canary) {
      await attempt("canary", async () => {
        const ran = await runCanary({ apiUrl, fetch, identity, readImage, report, depth: plan.canary, now });
        installId = ran.installId || null;
      });
    }
    const result = report.conclude();
    const broken = (part) => result.failures.filter((rule) => rule.part === part).length;
    const brokenRules = plan.window ? broken("window") : null;
    const canaryFailed = plan.canary ? Number(broken("canary") > 0) : null;
    const text = result.lines.join("\n");
    log(text);
    emit(metricLine({
      namespace,
      timestamp: startedAt,
      metrics: { [BROKEN_RULES_METRIC]: brokenRules || 0, [CANARY_FAILED_METRIC]: canaryFailed || 0 },
      fields: {
        event: "health_run",
        healthy: result.healthy,
        window: plan.window,
        canary: plan.canary,
        broken_rules: brokenRules,
        canary_failed: canaryFailed,
        failures: result.failures.slice(0, 30).map((rule) => `${rule.name}: ${rule.detail}`.slice(0, 300)),
        // What this run cost: Logs Insights charges by the bytes scanned.
        scanned_bytes: scannedBytes,
        duration_ms: now() - startedAt,
        canary_install_id: installId,
      },
    }));
    return {
      healthy: result.healthy,
      broken_rules: brokenRules,
      canary_failed: canaryFailed,
      // Halves that ended on an error instead of a verdict: a denied query, an API that
      // did not answer, a key store that could not be read.
      could_not_run: result.failures.filter((rule) => rule.crashed).map((rule) => `${rule.part}: ${rule.detail}`),
      report: text,
    };
  };
}
