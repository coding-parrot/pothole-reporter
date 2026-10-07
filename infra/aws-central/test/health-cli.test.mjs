import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { BROKEN_WINDOW, HEALTHY_WINDOW, fakeApi, fakeAwsCli, serve } from "./health-support.mjs";

// tools/production-health.mjs run as the process deploy.sh and the workflow run, on a
// fixed input: a stand-in `aws` on PATH answers its Logs Insights queries and a local
// server answers the canary. The lines and exit codes below were recorded from the
// script as it stood on 7 Oct 2026, before its rules moved into service/health and five
// of its nine queries became two, and must not change: deploy.sh fails a deploy on the
// exit code and people read the lines. Two lines were added after them the same day and
// none was altered: whether shadow mode has shown enough to let the screen answer, and
// the rules for the screen once it does (one line while no frame is screened live).

const SCRIPT = fileURLToPath(new URL("../tools/production-health.mjs", import.meta.url));

function run(args, { window = HEALTHY_WINDOW, api = fakeApi(), env = {}, key = null } = {}) {
  return serve(api).then((server) => new Promise((resolve) => {
    const aws = fakeAwsCli(window, { key });
    execFile(process.execPath, [SCRIPT, ...args], {
      env: { ...process.env, PATH: `${aws.directory}:${process.env.PATH}`, API_URL: server.url, AWS_REGION: "ap-south-1",
        LOG_GROUP: "/aws/lambda/pothole-reporter-central", CANARY_CATALOGUE_IS_THIS_CHECKOUT: "", ...env },
    }, (error, stdout, stderr) => {
      server.close().then(() => resolve({
        code: error ? error.code : 0,
        stderr,
        stdout,
        asked: aws.asked(),
        // Only what a clock or a fresh key decides is blanked.
        lines: stdout.replace(/ in \d+ ms/g, " in N ms").replace(/(install registers: )[0-9a-f]{8}/, "$1########")
          .replace(/(Canary against )\S+/, "$1API").split("\n"),
      }));
    });
  }));
}

const HEALTHY_WINDOW_LINES = [
  "",
  "Log window: last 6 h of /aws/lambda/pothole-reporter-central",
  "  ok   no internal errors: 0",
  "  ok   reports never rejected on their receipt: 0",
  "  ok   cap never reached (daily_vision_limit): 0",
  "  ok   cap never reached (shared_rate_limit): 0",
  "  ok   cap never reached (shared_daily_budget_reached): 0",
  "  ok   cap never reached (shared_budget_reached): 0",
  "  ok   road ownership answered: 0 of 203 unclassified",
  "  ok   street address resolved: 0 of 183 without a street",
  "  ok   reports land: 0 of 20 retried on the lock",
  "  ok   tenders match: 19 matched of 183 lookups",
  "  ok   tenders match somewhere in India: 19 matched of 176 lookups with a street",
  "  ok   road ownership layers are in the package: 0 lookups without the road ownership layers",
  "  ok   ward snapshot is in the package: 0 lookups without the ward snapshot",
  "  ok   wards find their tenders: 172 of 175 lookups with a ward (98.3%) answered a ward tender or a street tender",
  "  ok   ward snapshots outside Karnataka are in the package: 0 lookups without a switched-on ward snapshot; 1 placed in a ward (GJ/ahmedabad 1)",
  "  ok   detection is fast: p50 1906 ms, p90 3100 ms over 68 detections",
  "  ok   shadow screen (report only): the screen flagged 9 of 9 frames gpt-5-mini judged damaged (live recall 100.0%); cleared 5 of 16 it judged undamaged (31.3%); 3 frames had no screen answer",
  "  ok   shadow screen threshold for 98% live recall (report only): 9 scored frames judged damaged; 100 are needed before a threshold can be read off",
  "  ok   shadow screen ready to switch on (report only): NOT READY to switch on: 3 of 5 conditions met. Met: the screen flagged 9 of 9 damaged frames, live recall 100.0% (98% needed); cleared 5 of 16 frames gpt-5-mini judged undamaged, 31.3% (30% needed); 0 of 28 frames took the screen over 300 ms, 0.0% (p90 is under 300 ms up to 10%). Not met: 9 frames gpt-5-mini judged damaged (300 needed); 3 of 28 frames had no screen answer, 10.7% (under 1% needed). Not checked: that the damaged frames come from more than one phone (the request log carries no install marker)",
  "  ok   live screen: no drive frames were screened live in the window; nothing to judge",
  "  ok   service overhead is small (/v1/vision/detect): p90 158 ms, database p90 149 ms over 68 requests",
  "  ok   service overhead is small (/v1/tenders/resolve): p90 240 ms, database p90 12 ms over 183 requests",
  "  ok   known answers are instant (/v1/tenders/resolve): p50 8 ms, p90 9 ms over 77 requests",
  "  ok   known answers are instant (/v1/map): p50 9 ms, p90 179 ms over 100 requests",
  "  ok   no runtime crashes: 0",
];
const HEALTHY_CANARY_LINES = [
  "",
  "Canary against API",
  "  ok   health: 200, openai_with_shadow_screen",
  "  ok   /v1/map: 200 in N ms",
  "  ok   /v1/impact: 200 in N ms",
  "  ok   map is compressed: 6023 bytes of JSON sent as 410, gzip",
  "  ok   install registers: ######## (a new install: the stored canary key could not be read)",
  "  ok   shared detection finds the example pothole: pothole_cavity medium via openai in N ms",
  "  ok   Bengaluru street is classified municipal: LGD 305851 GBA - Central via the packaged state GIS layers; tender none (no_location_match) in N ms",
  "  ok   road class needs no state GIS call: lookup.local municipal_polygon",
  "  ok   ward is named from the packaged snapshot: Cox Town, KGIS ward 10",
  "  ok   the ward's tenders are answered: 2, first: Construction of Cement concrete roads and other improvement works at Doddigunta ",
  "  ok   Ahmedabad point is answered with its ward by name: SHAHIBAG (ward 16, GJ/ahmedabad); 1 ward tenders, first: In the area of Shahibaug ward of the Central zone, resurfacing of Bhogilal Chali road in N ms",
  "  ok   server finds the street itself, with no geocoder call: MM Road, Doddigunta, Cox Town, Bengaluru, 560005 in N ms",
];
const BROKEN_WINDOW_LINES = [
  "",
  "Log window: last 24 h of /aws/lambda/pothole-reporter-central",
  "  FAIL no internal errors: 4 requests crashed",
  "  FAIL reports never rejected on their receipt: 2 rejected; this is a bug in the receipt key",
  "  FAIL cap never reached (daily_vision_limit): 7 detections refused; raise the cap in template.yaml and test/caps.test.mjs",
  "  FAIL cap never reached (shared_rate_limit): 1 detections refused; raise the cap in template.yaml and test/caps.test.mjs",
  "  FAIL cap never reached (shared_daily_budget_reached): 1 detections refused; raise the cap in template.yaml and test/caps.test.mjs",
  "  FAIL cap never reached (shared_budget_reached): 1 detections refused; raise the cap in template.yaml and test/caps.test.mjs",
  "  FAIL road ownership answered: 26 of 122 lookups (21.3%) could not classify the road; the state GIS is down and the local fallback did not cover them",
  "  FAIL street address resolved: 24 of 110 lookups (21.8%) had no usable street; the reverse geocoder is failing",
  "  FAIL reports land: 7 of 12 reports (58.3%) were told to retry on the location lock",
  "  FAIL tenders match: 60 lookups reached matching and none matched; the tender table is empty or matching is broken",
  "  FAIL tenders match somewhere in India: 60 lookups had a street and none matched any catalogue; check the packed national catalogues and their review dates",
  "  FAIL road ownership layers are in the package: 26 lookups could not read the road ownership layers; data/karnataka-ownership.bin is missing from the package",
  "  FAIL ward snapshot is in the package: 5 municipal lookups could not read the ward snapshot; data/karnataka-ward-geometry.json is missing from the package",
  "  FAIL wards find their tenders: 5 of 55 lookups with a ward (9.1%) answered a ward tender or a street tender; the rule is 20%. Check the packaged ward snapshot, the tender table and ward-tenders.mjs",
  "  FAIL ward snapshots outside Karnataka are in the package: 6 lookups outside Karnataka could not read a ward snapshot the package should hold: GJ/ahmedabad (4), data/wards/runtime.json (2). Check that deploy.sh ran tools/stage-india-wards.mjs",
  "  FAIL detection is fast: p50 2600 ms, p90 4200 ms over 30 detections; rule is p50 under 2.5 s and p90 under 4 s",
  "  ok   shadow screen (report only): no drive frames were shadow screened in the window",
  "  ok   shadow screen threshold for 98% live recall (report only): 0 scored frames judged damaged; 100 are needed before a threshold can be read off",
  "  ok   shadow screen ready to switch on (report only): no drive frames were shadow screened in the window; nothing to say about switching on",
  "  ok   live screen: no drive frames were screened live in the window; nothing to judge",
  "  FAIL service overhead is small (/v1/tenders/resolve): p90 401 ms outside the detector and the geolocator over 110 requests; budget 400 ms",
  "  FAIL known answers are instant (/v1/map): p50 60 ms, p90 140 ms over 40 requests; budget p50 15 ms",
  "  FAIL no runtime crashes: 2 timeouts or runtime exits",
];

test("a healthy window and canary print every rule as ok and exit 0", async () => {
  const result = await run(["--window", "6h", "--canary"]);
  assert.deepEqual(result.lines, [...HEALTHY_WINDOW_LINES, ...HEALTHY_CANARY_LINES, "", "HEALTHY (771 requests in 6 h)", ""]);
  assert.equal(result.code, 0);
  assert.equal(result.stderr, "");
  // Every query is asked of the function's log group, over the window given. Six of
  // them: each one is charged for every byte of the window.
  assert.equal(result.asked.length, 6);
  for (const query of result.asked) {
    assert.deepEqual([query.group, query.seconds, query.region], ["/aws/lambda/pothole-reporter-central", 6 * 3600, "ap-south-1"]);
  }
});

test("the window alone and the canary alone print their own half", async () => {
  const window = await run(["--window", "6h"]);
  assert.deepEqual(window.lines, [...HEALTHY_WINDOW_LINES, "", "HEALTHY (771 requests in 6 h)", ""]);
  assert.equal(window.code, 0);
  const canary = await run(["--canary"]);
  assert.deepEqual(canary.lines, [...HEALTHY_CANARY_LINES, "", "HEALTHY", ""]);
  assert.equal(canary.code, 0);
  assert.deepEqual(canary.asked, [], "the canary needs no AWS access");
});

test("a broken window prints every broken rule and exits 1", async () => {
  const result = await run(["--window", "24h"], { window: BROKEN_WINDOW });
  assert.deepEqual(result.lines, [...BROKEN_WINDOW_LINES, "", "UNHEALTHY: 19 rule(s) broken (166 requests in 24 h)", ""]);
  assert.equal(result.code, 1);
  assert.equal(result.asked[0].seconds, 24 * 3600);
});

// Logs Insights charges by the bytes a query scans, and only a query's last answer says
// how many. A week is the window the readiness report wants, so the run says what its
// six queries cost. (The stand-in aws reports no statistics unless told to, which is why
// the lines above end without it.)
test("a window says how much log its queries scanned, and takes days", async () => {
  const result = await run(["--window", "7d"], { window: HEALTHY_WINDOW.map((entry) => ({ ...entry, scanned: 1_250_000 })) });
  assert.equal(result.lines.at(-2), "HEALTHY (771 requests in 168 h; 7.5 MB of log scanned by 6 queries)");
  assert.equal(result.code, 0);
  assert.ok(result.asked.every((query) => query.seconds === 7 * 24 * 3600));
});

test("an install that cannot register ends the canary there and exits 1", async () => {
  const result = await run(["--canary"], { api: fakeApi({ "POST /v1/installations": { status: 503, body: { message: "Service Unavailable" } } }) });
  assert.deepEqual(result.lines, [
    ...HEALTHY_CANARY_LINES.slice(0, 6),
    "  FAIL install registers: 503 {\"message\":\"Service Unavailable\"}",
    "",
    "UNHEALTHY: 1 rule(s) broken",
    "",
  ]);
  assert.equal(result.code, 1);
});

test("a query Logs Insights fails is one broken rule, and the canary is not run", async () => {
  const failed = HEALTHY_WINDOW.map((entry) => (entry.match === "by road_ownership, local_lookup" ? { ...entry, status: "Failed" } : entry));
  const result = await run(["--window", "6h", "--canary"], { window: failed });
  assert.deepEqual(result.lines, [
    ...HEALTHY_WINDOW_LINES.slice(0, 13),
    "  FAIL health check ran: Logs Insights Failed",
    "",
    "UNHEALTHY: 1 rule(s) broken (771 requests in 6 h)",
    "",
  ]);
  assert.equal(result.code, 1);
});

test("no flag is a usage error, exit 2", async () => {
  const result = await run([]);
  assert.equal(result.code, 2);
  assert.equal(result.stderr, "usage: production-health.mjs [--window 24h] [--canary]\n");
  assert.deepEqual(result.lines, [""]);
});

test("a window that is not hours or days is one broken rule", async () => {
  const result = await run(["--window", "soon"]);
  assert.deepEqual(result.lines, ["  FAIL health check ran: --window takes hours or days, like 24h or 7d, not soon", "", "UNHEALTHY: 1 rule(s) broken", ""]);
  assert.equal(result.code, 1);
});

// Every deploy runs the canary, and until 7 Oct 2026 every run registered a new install:
// one more "active installation" in the public figures per deploy. The script now asks
// the parameter store for the scheduled canary's key and runs as that install, which the
// service leaves out of the figures; a caller who may not read the key (the GitHub
// workflow's user) gets a new install, and the output says which it was.
test("the canary runs as the scheduled canary's stored install when the caller can read its key", async () => {
  const { generateKeyPairSync } = await import("node:crypto");
  const { installationPublicKey } = await import("../service/auth.mjs");
  const kept = generateKeyPairSync("ec", { namedCurve: "prime256v1" });
  const pem = kept.privateKey.export({ type: "pkcs8", format: "pem" });
  const id = installationPublicKey(kept.publicKey.export({ type: "spki", format: "der" }).toString("base64")).installId;
  const api = fakeApi();
  const first = await run(["--canary"], { api, key: pem });
  const second = await run(["--canary"], { api, key: pem });
  for (const result of [first, second]) {
    assert.equal(result.code, 0);
    assert.ok(result.stdout.includes(`  ok   install registers: ${id.slice(0, 8)} (the scheduled canary's stored install)\n`));
    assert.ok(!result.stdout.includes("PRIVATE KEY") && !result.stderr.includes("PRIVATE KEY"));
    assert.equal(result.stderr, "");
  }
  assert.deepEqual([...api.installs.keys()], [id], "two runs, one install");
  // It names itself on its reads, as the scheduled canary does.
  assert.ok(api.calls.filter((call) => call.name.startsWith("GET ")).every((call) => call.headers["x-install-id"] === id));
});

test("a caller who cannot read the key runs as a new install, says so, and prints no error", async () => {
  const api = fakeApi();
  const result = await run(["--canary"], { api });
  assert.equal(result.code, 0);
  assert.match(result.stdout, /install registers: [0-9a-f]{8} \(a new install: the stored canary key could not be read\)\n/);
  assert.equal(result.stderr, "", "the refusal is not printed as an error: the canary ran");
  assert.equal(api.installs.size, 1);
});
