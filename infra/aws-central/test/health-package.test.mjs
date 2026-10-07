import assert from "node:assert/strict";
import { cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { EXAMPLE_IMAGE } from "../service/health/example-image.mjs";
import { PROOF_EVENT, judgeProof, proveHealthFunction } from "../tools/prove-health-function.mjs";
import { checkStagedHealth } from "../tools/staged-health.mjs";

// The health function runs from the central function's zip, so what it needs has to be
// in that zip: its modules (they are under service/, which deploy.sh copies whole) and
// the photograph its canary sends, which lives in docs/ and is copied by name. A zip
// without the photograph would deploy, and every full canary would then fail on a
// missing file. tools/check-package.mjs refuses it before anything is uploaded.

const root = fileURLToPath(new URL("../../../", import.meta.url));
const deploy = readFileSync(path.join(root, "infra/aws-central/deploy.sh"), "utf8");

function layout({ photograph = true } = {}) {
  const pkg = mkdtempSync(path.join(os.tmpdir(), "package-"));
  cpSync(path.join(root, "infra/aws-central/service/health"), path.join(pkg, "infra/aws-central/service/health"), { recursive: true });
  cpSync(path.join(root, "infra/aws-central/package.json"), path.join(pkg, "infra/aws-central/package.json"));
  if (photograph) {
    mkdirSync(path.join(pkg, "docs"));
    cpSync(path.join(root, "docs/example-pothole.jpg"), path.join(pkg, "docs/example-pothole.jpg"));
  }
  return pkg;
}

test("the deploy copies the photograph to where the function reads it, and zips and hashes it", () => {
  // The module reads it four directories up from service/health: the package root.
  assert.equal(path.relative(root, fileURLToPath(EXAMPLE_IMAGE)), "docs/example-pothole.jpg");
  assert.match(deploy, /mkdir -p [^\n]*"\$TMP_DIR\/package\/docs"/);
  assert.match(deploy, /cp docs\/example-pothole\.jpg "\$TMP_DIR\/package\/docs\/"/);
  assert.match(deploy, /zip -q -r "\$TMP_DIR\/central-lambda\.zip" infra llm data docs\)/, "a directory not named here is not in the zip");
  // The code key is a hash of the package's files: a changed photograph is a new key.
  assert.match(deploy, /find infra llm data docs -type f/);
  // Copied before the check that looks for it, which runs before the upload.
  const copied = deploy.indexOf("cp docs/example-pothole.jpg");
  const checked = deploy.indexOf("tools/check-package.mjs");
  assert.ok(copied !== -1 && copied < checked && checked < deploy.indexOf("aws s3 cp"));
});

test("the pre-upload check passes a package with the health modules and the photograph", async () => {
  const whole = await checkStagedHealth(layout());
  assert.deepEqual(whole.problems, []);
  assert.equal(whole.photographBytes, readFileSync(path.join(root, "docs/example-pothole.jpg")).length);
  const check = readFileSync(path.join(root, "infra/aws-central/tools/check-package.mjs"), "utf8");
  assert.match(check, /checkStagedHealth\(packageDirectory\)/, "check-package.mjs runs it");
});

test("a package without the photograph, or with a broken copy of it, is refused", async () => {
  const missing = await checkStagedHealth(layout({ photograph: false }));
  assert.equal(missing.problems.length, 1);
  assert.match(missing.problems[0], /docs\/example-pothole\.jpg.*deploy\.sh/);
  const empty = layout();
  writeFileSync(path.join(empty, "docs/example-pothole.jpg"), "");
  assert.match((await checkStagedHealth(empty)).problems[0], /not a JPEG photograph \(0 bytes\)/);
  const text = layout();
  writeFileSync(path.join(text, "docs/example-pothole.jpg"), "x".repeat(5000));
  assert.match((await checkStagedHealth(text)).problems[0], /not a JPEG photograph/);
});

test("a package without the health function's modules is refused", async () => {
  const noEntry = layout();
  rmSync(path.join(noEntry, "infra/aws-central/service/health/handler.mjs"));
  assert.match((await checkStagedHealth(noEntry)).problems[0], /service\/health\/handler\.mjs is not in the package/);
  // A module the function imports, lost by a copy step.
  const half = layout();
  rmSync(path.join(half, "infra/aws-central/service/health/canary.mjs"));
  assert.match((await checkStagedHealth(half)).problems[0], /health function cannot be loaded from the package.*canary\.mjs/);
  const bare = mkdtempSync(path.join(os.tmpdir(), "package-"));
  assert.match((await checkStagedHealth(bare)).problems[0], /handler\.mjs is not in the package/);
});

// ------------------------------------------------------------------ after the deploy
// The function has its own role, and its AWS SDK is the Lambda runtime's: only the real
// stack can show that it loads and may query the log. deploy.sh invokes it once.
test("the deploy invokes the health function once the stack is live, by the name the stack gives", () => {
  assert.match(deploy, /OutputKey=='HealthFunctionName'/);
  const proved = deploy.indexOf("tools/prove-health-function.mjs");
  assert.ok(proved > deploy.indexOf("aws cloudformation deploy"), "after the stack is deployed");
  assert.ok(proved > deploy.indexOf("production-health.mjs --canary"), "after the canary that gates the deploy");
});

test("the proof run reads and writes nothing a person would notice", () => {
  // The window and the reads canary: no install, no detection, no lookup. A full canary
  // here would put three more unmatched lookups in the log on every deploy.
  assert.deepEqual(PROOF_EVENT, { window: "1h", canary: "reads" });
});

test("a run that returned is proof, whatever it found; a run that could not, or crashed, is not", () => {
  const ran = judgeProof({ payload: { healthy: false, broken_rules: 3, canary_failed: 0, could_not_run: [], report: "\nLog window: last 1 h of g\n  FAIL x: y" } });
  assert.equal(ran.ok, true, "broken rules are production's news, carried by the alarms; the function works");
  assert.match(ran.lines.join("\n"), /FAIL x: y/);
  assert.match(ran.lines.at(-1), /health function ran: 3 broken log rules, canary ok/);

  const denied = judgeProof({ payload: { healthy: false, broken_rules: 1, canary_failed: 0, report: "r",
    could_not_run: ["window: User is not authorized to perform: logs:StartQuery"] } });
  assert.equal(denied.ok, false);
  assert.match(denied.lines.at(-1), /could not run: window: User is not authorized to perform: logs:StartQuery/);

  const crashed = judgeProof({ functionError: "Unhandled", payload: { errorType: "Runtime.ImportModuleError", errorMessage: "Cannot find package '@aws-sdk/client-ssm'" } });
  assert.equal(crashed.ok, false);
  assert.match(crashed.lines.at(-1), /crashed: Runtime\.ImportModuleError: Cannot find package '@aws-sdk\/client-ssm'/);

  assert.equal(judgeProof({ payload: null }).ok, false);
  assert.equal(judgeProof({ payload: { message: "Internal server error" } }).ok, false, "not the function's own answer");
});

test("a scheduled run holding the function's one slot is waited for, a few times", async () => {
  const throttled = () => Object.assign(new Error("Command failed"), { stderr: "An error occurred (TooManyRequestsException) when calling the Invoke operation: Rate Exceeded." });
  const answer = { payload: { healthy: true, broken_rules: 0, canary_failed: 0, could_not_run: [], report: "ok" } };
  const waits = [];
  let calls = 0;
  const proof = await proveHealthFunction({ sleep: async (ms) => { waits.push(ms); },
    invoke: () => { calls += 1; if (calls < 3) throw throttled(); return answer; } });
  assert.equal(proof.ok, true);
  assert.deepEqual(waits, [20_000, 20_000]);

  // Never free: the last refusal is the answer, and the loop ends.
  calls = 0;
  const never = await proveHealthFunction({ sleep: async () => {}, invoke: () => { calls += 1; throw throttled(); } });
  assert.equal(never.ok, false);
  assert.equal(calls, 4);
  assert.match(never.lines.at(-1), /TooManyRequestsException/);

  // Any other failure to invoke is not waited on.
  calls = 0;
  const gone = await proveHealthFunction({ sleep: async () => {}, invoke: () => { calls += 1; throw Object.assign(new Error("Command failed"), { stderr: "ResourceNotFoundException: Function not found" }); } });
  assert.equal(gone.ok, false);
  assert.equal(calls, 1);
  assert.match(gone.lines.at(-1), /Function not found/);
});

// The tool as deploy.sh runs it, with a stand-in `aws` on PATH that answers `lambda
// invoke` by writing the function's answer to the file it is given.
test("run as a process, the proof invokes the named function with the proof event and exits on what it says", async () => {
  const { execFile } = await import("node:child_process");
  const { chmodSync } = await import("node:fs");
  const run = (answer, status = { StatusCode: 200 }) => new Promise((resolve) => {
    const bin = mkdtempSync(path.join(os.tmpdir(), "fake-aws-"));
    const seen = path.join(bin, "seen.json");
    writeFileSync(path.join(bin, "aws"), `#!/usr/bin/env node
const { readFileSync, writeFileSync } = require("node:fs");
const args = process.argv.slice(2);
const option = (name) => args[args.indexOf(name) + 1];
writeFileSync(${JSON.stringify(seen)}, JSON.stringify({ command: args.slice(0, 2), name: option("--function-name"), region: option("--region"),
  event: JSON.parse(readFileSync(option("--payload").replace(/^fileb:\\/\\//, ""), "utf8")), timeout: option("--cli-read-timeout") }));
writeFileSync(args.at(-1), ${JSON.stringify(JSON.stringify(answer))});
console.log(${JSON.stringify(JSON.stringify(status))});
`);
    chmodSync(path.join(bin, "aws"), 0o755);
    execFile(process.execPath, [path.join(root, "infra/aws-central/tools/prove-health-function.mjs"), "pothole-reporter-central-health"],
      { env: { ...process.env, PATH: `${bin}:${process.env.PATH}`, AWS_REGION: "ap-south-1" } },
      (error, stdout) => resolve({ code: error ? error.code : 0, stdout, seen: JSON.parse(readFileSync(seen, "utf8")) }));
  });
  const worked = await run({ healthy: true, broken_rules: 0, canary_failed: 0, could_not_run: [], report: "\nLog window: last 1 h of g\n  ok   no internal errors: 0\n\nHEALTHY" });
  assert.equal(worked.code, 0);
  assert.deepEqual(worked.seen, { command: ["lambda", "invoke"], name: "pothole-reporter-central-health", region: "ap-south-1", event: PROOF_EVENT, timeout: "300" });
  assert.match(worked.stdout, /ok {3}no internal errors: 0\n\nHEALTHY\nhealth function ran: 0 broken log rules, canary ok\n$/);
  const crashed = await run({ errorType: "Runtime.ImportModuleError", errorMessage: "Cannot find package" }, { StatusCode: 200, FunctionError: "Unhandled" });
  assert.equal(crashed.code, 1);
  assert.match(crashed.stdout, /health function crashed: Runtime\.ImportModuleError: Cannot find package/);
});
