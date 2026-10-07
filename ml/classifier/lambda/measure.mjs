#!/usr/bin/env node
// Invoke the deployed screen with the central service's own request shape and report
// what Lambda itself measured: init (cold start), duration per call, memory.
//
//   AWS_PROFILE=pothole node measure.mjs <pothole.jpg> <clean-road.jpg> [calls per image]
//
// A cold start is forced first by changing an environment variable (MEASURED_AT), which
// makes Lambda start a new execution environment for the next invoke.

import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const FUNCTION = process.env.FUNCTION || "pothole-reporter-central-screen";
const REGION = process.env.AWS_REGION || "ap-south-1";
const KEY_FILE = process.env.SCREEN_API_KEY_FILE || path.join(here, "../work/screen-api-key");
const [potholePath, cleanPath, countArg] = process.argv.slice(2);
const calls = Number(countArg || 30);
const key = readFileSync(KEY_FILE, "utf8").trim();
const scratch = mkdtempSync(path.join(tmpdir(), "screen-measure-"));

const aws = (...args) => execFileSync("aws", [...args, "--region", REGION, "--output", "json"],
  { encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });

function invoke(imagePath, index) {
  const requestId = `measure-${Date.now()}-${index}`;
  const payload = path.join(scratch, "payload.json");
  writeFileSync(payload, JSON.stringify({
    version: "2.0", routeKey: "POST /v1/detect", rawPath: "/v1/detect",
    headers: { "x-request-id": requestId, "x-yolo-api-key": key, "content-type": "application/json" },
    body: JSON.stringify({
      version: 1, task: "road_damage_detection", request_id: requestId, model: "pothole-yolo",
      capture_mode: "drive", language: "en", prompt_version: "road-damage-v5", schema_version: 4,
      images: [{ data_url: `data:image/jpeg;base64,${readFileSync(imagePath).toString("base64")}` }],
    }),
    isBase64Encoded: false, requestContext: { requestId },
  }));
  const out = path.join(scratch, "out.json");
  const started = performance.now();
  const meta = JSON.parse(aws("lambda", "invoke", "--function-name", FUNCTION, "--log-type", "Tail",
    "--cli-binary-format", "raw-in-base64-out", "--payload", `fileb://${payload}`, out));
  const clientMs = performance.now() - started;
  const tail = Buffer.from(meta.LogResult, "base64").toString("utf8");
  const report = tail.split("\n").find((line) => line.startsWith("REPORT")) || "";
  const number = (label) => Number((new RegExp(`${label}: ([0-9.]+)`).exec(report) || [])[1]);
  const own = tail.split("\n").map((line) => line.slice(line.indexOf("{")))
    .map((text) => { try { return JSON.parse(text); } catch { return null; } })
    .find((line) => line?.event === "screen_request_complete") || {};
  const envelope = JSON.parse(readFileSync(out, "utf8"));
  const body = JSON.parse(envelope.body || "{}");
  return {
    status: envelope.statusCode, assessment: body.verdict?.assessment, score: body.score,
    durationMs: number("Duration"), initMs: number("Init Duration") || null,
    memoryMb: number("Max Memory Used"), decodeMs: own.decode_ms, inferMs: own.infer_ms,
    handlerMs: own.latency_ms, clientMs, functionError: meta.FunctionError || null,
  };
}

const percentile = (values, p) => {
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.min(sorted.length - 1, Math.ceil((p / 100) * sorted.length) - 1)];
};

const config = JSON.parse(aws("lambda", "get-function-configuration", "--function-name", FUNCTION));
const variables = { ...(config.Environment?.Variables || {}), MEASURED_AT: new Date().toISOString() };
aws("lambda", "update-function-configuration", "--function-name", FUNCTION,
  "--environment", JSON.stringify({ Variables: variables }));
execFileSync("aws", ["lambda", "wait", "function-updated-v2", "--function-name", FUNCTION, "--region", REGION]);

const cold = invoke(potholePath, 0);
console.log(JSON.stringify({ cold_start: { init_ms: cold.initMs, first_duration_ms: cold.durationMs,
  status: cold.status, memory_mb: cold.memoryMb } }));
const result = { function: FUNCTION, memory_configured_mb: config.MemorySize, cold_start: cold, images: {} };
for (const [label, imagePath] of [["pothole", potholePath], ["clean_road", cleanPath]]) {
  const runs = [];
  for (let index = 1; index <= calls; index += 1) runs.push(invoke(imagePath, index));
  const bad = runs.filter((run) => run.status !== 200 || run.functionError || run.initMs);
  const summary = {
    calls: runs.length, not_warm_or_failed: bad.length,
    assessment: [...new Set(runs.map((run) => run.assessment))].join(","),
    score: runs[0].score,
    duration_ms: { p50: percentile(runs.map((r) => r.durationMs), 50), p90: percentile(runs.map((r) => r.durationMs), 90),
      max: Math.max(...runs.map((r) => r.durationMs)) },
    infer_ms: { p50: percentile(runs.map((r) => r.inferMs), 50), p90: percentile(runs.map((r) => r.inferMs), 90) },
    decode_ms: { p50: percentile(runs.map((r) => r.decodeMs), 50), p90: percentile(runs.map((r) => r.decodeMs), 90) },
    client_ms_from_this_machine: { p50: Math.round(percentile(runs.map((r) => r.clientMs), 50)) },
    max_memory_used_mb: Math.max(...runs.map((r) => r.memoryMb)),
  };
  result.images[label] = summary;
  console.log(JSON.stringify({ [label]: summary }));
}
writeFileSync(path.join(here, "../work/lambda-measurement.json"), `${JSON.stringify(result, null, 1)}\n`);
