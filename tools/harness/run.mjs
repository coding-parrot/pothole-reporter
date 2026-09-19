#!/usr/bin/env node
// One command that runs every check this repo has, in parallel, and tells you exactly
// what is broken.
//
//   node tools/harness/run.mjs                 static gates + flow tests (the fast set)
//   node tools/harness/run.mjs --all           everything, including the slow suites
//   node tools/harness/run.mjs --only flow     one group: static, flow, server, python,
//                                             browsers (firefox/webkit), emulator,
//                                             devices (AWS Device Farm, opt-in)
//   node tools/harness/run.mjs --loop          re-run until no regressions remain
//   node tools/harness/run.mjs --until-green   re-run until EVERY check passes
//   node tools/harness/run.mjs --baseline      write baseline.json instead of judging
//
// It serves docs/ (the shipped web app) once for every browser test, and runs
// them across CPU workers, because a suite nobody waits for is a suite nobody runs.

import { execFile, spawn } from "node:child_process";
import { existsSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { availableParallelism } from "node:os";
import { resolve } from "node:path";
import { createServer } from "node:http";
import { promisify } from "node:util";

const run = promisify(execFile);
const repoRoot = resolve(import.meta.dirname, "../..");
const args = process.argv.slice(2);
const flag = (name) => args.includes(`--${name}`);
const value = (name, fallback) => {
  const index = args.indexOf(`--${name}`);
  return index >= 0 && args[index + 1] ? args[index + 1] : fallback;
};

const python = `${repoRoot}/.venv/bin/python`;
const PORT = Number(process.env.HARNESS_PORT || 8765);
const workers = Number(value("workers", Math.max(2, Math.min(6, availableParallelism() - 2))));
const timeoutMs = Number(value("timeout", 240)) * 1000;

// Suites that take minutes or need a model, a network fetch or a device. They are not
// part of the fast loop, but --all runs them so nothing rots unnoticed.
const SLOW = new Set([
  "exhaustive_video_eval_test.py",
  "video_eval_test.py",
  "state_pack_validation_test.py",
  "full_frame_invariant_test.py",
]);

async function refuseIfPortBusy(port) {
  const inUse = await new Promise((done) => {
    const probe = createServer();
    probe.once("error", () => done(true));
    probe.once("listening", () => probe.close(() => done(false)));
    probe.listen(port);
  });
  if (inUse) {
    console.error(`Port ${port} is already serving something else. Stop it first: `
      + `the suites would load that document root instead of docs/.`);
    process.exit(2);
  }
}

function staticServer(root, port) {
  const types = {
    ".html": "text/html", ".js": "text/javascript", ".json": "application/json",
    ".png": "image/png", ".jpg": "image/jpeg", ".svg": "image/svg+xml",
    ".webp": "image/webp", ".pt": "application/octet-stream", ".mp4": "video/mp4",
    ".webm": "video/webm", ".gpx": "application/gpx+xml", ".css": "text/css",
  };
  const server = createServer((request, response) => {
    let path = decodeURIComponent(new URL(request.url, "http://x").pathname);
    // The hosted site exposes the app under /web-app/ as well as at the root; five
    // suites request it that way.
    if (path === "/web-app" || path.startsWith("/web-app/")) {
      path = path.slice("/web-app".length) || "/";
    }
    const file = resolve(root, `.${path === "/" ? "/index.html" : path}`);
    if (!file.startsWith(root) || !existsSync(file)) {
      response.writeHead(404).end("not found");
      return;
    }
    const extension = file.slice(file.lastIndexOf("."));
    response.writeHead(200, { "content-type": types[extension] || "application/octet-stream" });
    response.end(readFileSync(file));
  });
  return new Promise((done) => server.listen(port, () => done(server)));
}

async function runOne(task) {
  const startedAt = Date.now();
  try {
    const { stdout, stderr } = await run(task.command[0], task.command.slice(1), {
      cwd: task.cwd || repoRoot,
      timeout: timeoutMs,
      maxBuffer: 32 * 1024 * 1024,
      env: { ...process.env, ...task.env },
    });
    return { ...task, ok: true, ms: Date.now() - startedAt, output: stdout + stderr };
  } catch (error) {
    const output = `${error.stdout || ""}${error.stderr || ""}` || String(error.message);
    return {
      ...task,
      ok: false,
      ms: Date.now() - startedAt,
      timedOut: error.killed === true || /timed? ?out/i.test(String(error.message)),
      output,
    };
  }
}

async function pool(tasks, limit) {
  const results = [];
  let next = 0;
  await Promise.all(Array.from({ length: Math.min(limit, tasks.length) }, async () => {
    while (next < tasks.length) {
      const task = tasks[next++];
      const result = await runOne(task);
      results.push(result);
      process.stdout.write(result.ok
        ? `  ok   ${result.name} (${(result.ms / 1000).toFixed(1)}s)\n`
        : `  FAIL ${result.name} (${(result.ms / 1000).toFixed(1)}s)${result.timedOut ? " [timeout]" : ""}\n`);
    }
  }));
  return results;
}

function tasks(group) {
  const all = [];
  if (!group || group === "static") {
    all.push({
      group: "static",
      name: "lint-web (no undefined identifiers)",
      command: ["node", "tools/harness/lint-web.mjs"],
    });
    all.push({
      group: "static",
      name: "__pure exports every helper",
      command: ["node", "tools/harness/sync-pure-exports.mjs", "--check"],
    });
    all.push({
      group: "static",
      name: "llm contract is current",
      command: ["node", "llm/generate.mjs", "--check"],
    });
    all.push({
      group: "static",
      name: "web and native agree on shared contracts",
      command: ["node", "tools/harness/check-native-contracts.mjs"],
    });
    all.push({
      group: "static",
      name: "every language defines every key",
      command: ["node", "tools/harness/check-i18n.mjs"],
    });
    all.push({
      group: "static",
      name: "data notice version tracks its wording",
      command: ["python3", "tools/snapshot-data-notice.py"],
    });
    all.push({
      group: "static",
      name: "web asset mirrors match",
      command: ["python3", "tools/verify-release-assets.py", "--static", "static",
        "--www", "android-app/www", "--docs", "docs",
        "--packaged", "android-app/android/app/src/main/assets/public"],
    });
  }
  if (!group || group === "server") {
    all.push({
      group: "server",
      name: "central service unit tests",
      command: ["npm", "test", "--silent"],
      cwd: `${repoRoot}/infra/aws-central`,
    });
  }
  // Real phones in AWS Device Farm. Opt-in: it needs AWS credentials and spends
  // device-minutes, so it never runs as part of --all.
  if (group === "devices") {
    all.push({
      group: "devices",
      name: "aws device farm (Top Devices pool)",
      command: ["bash", "tools/harness/devicefarm-run.sh"],
    });
  }
  // The packaged app on a real Android WebView: the only check that would have caught
  // a bundle whose script died at load. Skips itself when no device is attached.
  if (group === "emulator" || flag("all")) {
    all.push({
      group: "emulator",
      name: "android emulator smoke (fresh install reaches Home)",
      command: ["bash", "tools/harness/emulator-smoke.sh"],
    });
  }
  // The flow suites also run on Firefox and WebKit: a tester's WebView is not Chromium,
  // and an engine-specific break in signup, drive or reporting must fail here.
  if (group === "browsers" || flag("all")) {
    for (const engine of ["firefox", "webkit"]) {
      for (const name of readdirSync(`${repoRoot}/tests`).sort()) {
        if (!name.startsWith("flow_") || !name.endsWith("_test.py")) continue;
        all.push({
          group: "browsers",
          name: `${name} [${engine}]`,
          command: [python, `tests/${name}`],
          env: { POTHOLE_TEST_APP: `http://localhost:${PORT}/`, POTHOLE_TEST_BROWSER: engine },
        });
      }
    }
  }
  if (!group || group === "flow" || group === "python") {
    const wanted = group === "flow"
      ? (name) => name.startsWith("flow_")
      : () => true;
    for (const name of readdirSync(`${repoRoot}/tests`).sort()) {
      if (!name.endsWith("_test.py") || !wanted(name)) continue;
      if (SLOW.has(name) && !flag("all")) continue;
      all.push({
        group: name.startsWith("flow_") ? "flow" : "python",
        name,
        command: [python, `tests/${name}`],
        env: { POTHOLE_TEST_APP: `http://localhost:${PORT}/` },
      });
    }
  }
  return all;
}

async function once() {
  const group = value("only", null);
  const list = tasks(group);
  // docs/ is the shipped web app: the same index.html and standalone.js as static/,
  // plus the data packs the routing suites need. Serving static/ made every pack fetch
  // 404 and looked like a routing bug.
  await refuseIfPortBusy(PORT);
  const server = await staticServer(`${repoRoot}/docs`, PORT);
  const startedAt = Date.now();
  console.log(`Running ${list.length} checks with ${workers} workers on port ${PORT}\n`);
  let results;
  try {
    results = await pool(list, workers);
  } finally {
    server.close();
  }
  const failed = results.filter((result) => !result.ok);
  const seconds = ((Date.now() - startedAt) / 1000).toFixed(1);
  console.log(`\n${results.length - failed.length}/${results.length} passed in ${seconds}s`);
  if (failed.length) {
    console.log("\nFailures:");
    for (const result of failed) {
      console.log(`\n=== ${result.name} ===`);
      console.log(result.output.split("\n").slice(-25).join("\n").trim());
    }
  }
  return { results, failed };
}

if (flag("baseline")) {
  const { results } = await once();
  const baseline = Object.fromEntries(results.map((result) => [result.name, result.ok]));
  writeFileSync(`${repoRoot}/tools/harness/baseline.json`, `${JSON.stringify(baseline, null, 2)}\n`);
  console.log(`\nBaseline written: ${results.filter((r) => r.ok).length} passing, ${results.filter((r) => !r.ok).length} failing.`);
  process.exit(0);
}

const baselinePath = `${repoRoot}/tools/harness/baseline.json`;
const baseline = existsSync(baselinePath) ? JSON.parse(readFileSync(baselinePath, "utf8")) : {};

async function judge() {
  const { results, failed } = await once();
  // A suite that was already broken before this work does not get to hide a new break,
  // and does not get to fail the run either. Regressions are what matter.
  const regressions = failed.filter((result) => baseline[result.name] !== false);
  const known = failed.filter((result) => baseline[result.name] === false);
  if (known.length) {
    console.log(`\nKnown-failing before this work (not regressions): ${known.map((r) => r.name).join(", ")}`);
  }
  if (regressions.length) {
    console.log(`\nREGRESSIONS: ${regressions.map((r) => r.name).join(", ")}`);
    return false;
  }
  console.log("\nNo regressions.");
  return true;
}

// --until-green ignores the baseline: nothing is "known broken" any more, the run
// repeats until every check in the repo passes. This is the mode to leave running
// while the pre-existing failures are worked through.
if (flag("until-green")) {
  const waitSeconds = Number(value("wait", 60));
  for (let attempt = 1; ; attempt += 1) {
    console.log(`\n===== until-green, attempt ${attempt} =====`);
    const { results, failed } = await once();
    if (!failed.length) {
      console.log(`Everything green: ${results.length} checks passing.`);
      break;
    }
    console.log(`${failed.length} still failing: ${failed.map((r) => r.name).join(", ")}`);
    console.log(`Re-running in ${waitSeconds}s.`);
    await new Promise((done) => setTimeout(done, waitSeconds * 1000));
  }
} else if (flag("loop")) {
  const waitSeconds = Number(value("wait", 30));
  for (let attempt = 1; ; attempt += 1) {
    console.log(`\n===== harness loop, attempt ${attempt} =====`);
    if (await judge()) {
      console.log("Everything green.");
      break;
    }
    console.log(`Re-running in ${waitSeconds}s. Fix the failures above; the loop keeps checking.`);
    await new Promise((done) => setTimeout(done, waitSeconds * 1000));
  }
} else {
  process.exit(await judge() ? 0 : 1);
}
