#!/usr/bin/env node
// Invokes the deployed health function once and says whether it works.
//
//   node infra/aws-central/tools/prove-health-function.mjs <function name>
//
// deploy.sh runs this after every deploy. The function has its own role and takes its
// AWS SDK from the Lambda runtime, so no test and no package check can show that it
// loads and may query the log: every unit test passed while GET /v1/map returned 500 for
// ten days on a grant only the real stack could exercise. A broken checker would
// otherwise be found two hours later by the alarm for a checker that is not running.
//
// What counts as working: the function returned its own answer and could run both
// halves. What it found is not judged here. A broken rule is production's news; the
// function printing it is the function working.

import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

// The window and the reads canary: Logs Insights and the public API, and nothing a
// person would notice. No install is registered and no detection is paid for. (The first
// full canary, within half an hour, is what first reads and writes the key store.)
export const PROOF_EVENT = { window: "1h", canary: "reads" };

export function judgeProof({ functionError = null, payload }) {
  const lines = [];
  if (functionError) {
    lines.push(`health function crashed: ${payload?.errorType || functionError}: ${payload?.errorMessage || JSON.stringify(payload)}`);
    return { ok: false, lines };
  }
  if (!payload || typeof payload.healthy !== "boolean" || !Array.isArray(payload.could_not_run)) {
    lines.push(`health function did not answer as itself: ${JSON.stringify(payload)?.slice(0, 300)}`);
    return { ok: false, lines };
  }
  lines.push(String(payload.report || ""));
  if (payload.could_not_run.length) {
    lines.push(`health function could not run: ${payload.could_not_run.join("; ")}`);
    return { ok: false, lines };
  }
  lines.push(`health function ran: ${payload.broken_rules} broken log rules, canary ${payload.canary_failed ? "failed" : "ok"}`);
  return { ok: true, lines };
}

// The function runs one at a time. If a scheduled run holds the slot, Lambda refuses
// this one; a run takes seconds, so it is asked again after 20 s, four times in all.
export async function proveHealthFunction({ invoke, sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)), attempts = 4 }) {
  for (let attempt = 1; ; attempt += 1) {
    try {
      return judgeProof(await invoke());
    } catch (error) {
      const said = String(error.stderr || error.message).trim();
      const busy = /TooManyRequestsException|Rate Exceeded/i.test(said);
      if (!busy || attempt >= attempts) return { ok: false, lines: [`health function could not be invoked: ${said.slice(0, 500)}`] };
      await sleep(20_000);
    }
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const functionName = process.argv[2];
  if (!functionName) {
    console.error("usage: prove-health-function.mjs <function name>");
    process.exit(2);
  }
  const directory = mkdtempSync(path.join(os.tmpdir(), "health-proof-"));
  const eventFile = path.join(directory, "event.json");
  const answerFile = path.join(directory, "answer.json");
  writeFileSync(eventFile, JSON.stringify(PROOF_EVENT));
  const proof = await proveHealthFunction({
    invoke: () => {
      // fileb:// sends the bytes as they are in both aws CLI versions. The read timeout
      // is above the function's own, so a slow run is the function's to report.
      const status = JSON.parse(execFileSync("aws", ["lambda", "invoke", "--function-name", functionName,
        "--region", process.env.AWS_REGION || "ap-south-1", "--payload", `fileb://${eventFile}`,
        "--cli-read-timeout", "300", "--output", "json", answerFile], { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] }));
      return { functionError: status.FunctionError || null, payload: JSON.parse(readFileSync(answerFile, "utf8")) };
    },
  });
  for (const line of proof.lines) console.log(line);
  process.exit(proof.ok ? 0 : 1);
}
