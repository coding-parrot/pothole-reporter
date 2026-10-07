// Prompt A against prompt B on N labelled frames. Runs inside CodeBuild (see
// ../buildspec-eval.yml and ../eval-prompt.sh); nothing here is used by the app.
//
// A is the production detection prompt, B the candidate. Both arms send the production
// drive request, field for field, as infra/aws-central/service/detectors.mjs builds it:
// the image first, then the prompt (base + captureLayouts.drive), the strict schema,
// verbosity, reasoning effort and store, all read from llm/generated/contract.mjs at
// this commit. Only the base prompt text differs between the arms.
//
// Every frame is judged once by each arm (a pair). The order within a pair is random, so
// neither arm always pays for the cold cache. Reported per arm: damaged kept (labelled
// damaged and flagged), undamaged kept (labelled undamaged and not flagged), output
// tokens, and OpenAI's own processing time from the openai-processing-ms response
// header. Wall time is never reported: it measures this machine's network. Between the
// arms: the discordant pairs and an exact two-sided binomial (McNemar) test on them.
//
// Spend is added up from each response's usage block. A call is only started while
// (spent + what the calls in flight could cost + one more) fits the budget, so the
// budget cannot be overrun; a run that stops early says so and exits 3.
import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { appendFileSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import https from "node:https";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

import {
  DETECT_PROMPT, DETECT_PROMPT_VERSION, DETECT_SCHEMA, LLM_CONTRACT, MODEL_CONFIG, RUNTIME_CONFIG,
} from "../../../llm/generated/contract.mjs";

const run = promisify(execFile);
const HERE = dirname(fileURLToPath(import.meta.url));
const env = (name, fallback) => (process.env[name] === undefined || process.env[name] === ""
  ? fallback : process.env[name]);

const BUCKET = env("EVAL_BUCKET", "pothole-reporter-ml-695656921622-ap-south-1");
const DATA_PREFIX = env("EVAL_DATA_PREFIX", "v1-work");
const FRAMES = Number(env("EVAL_FRAMES", "60"));
const BUDGET_USD = Number(env("EVAL_BUDGET_USD", "0.10"));
const SEED = Number(env("EVAL_SEED", "20261007"));
const CONCURRENCY = Number(env("EVAL_CONCURRENCY", "4"));
const CANDIDATE = env("EVAL_CANDIDATE_FILE", "");
const OUT = env("EVAL_OUT", join(HERE, "out"));
const SELF_TEST = process.argv.includes("--self-test");

// List price, USD per million tokens. A model without a price here cannot have a
// spend stop, so the run refuses it.
const USD_PER_M = { "gpt-5-mini": { input: 0.25, cached_input: 0.025, output: 2.0 } };
// What one call is assumed to cost before its usage is known (about four times the
// measured USD 0.0005), raised to the dearest call seen.
let reservePerCall = 0.002;

const promptConfig = LLM_CONTRACT.prompts.detection;
const MODEL = MODEL_CONFIG.defaultModel;
const DETAIL = MODEL_CONFIG.defaultImageDetail;
const EFFORT = MODEL_CONFIG.reasoningEffortByModel[MODEL] || MODEL_CONFIG.defaultReasoningEffort;

export function costUsd(usage, model = MODEL) {
  const price = USD_PER_M[model];
  const cached = usage?.input_tokens_details?.cached_tokens || 0;
  const fresh = (usage?.input_tokens || 0) - cached;
  return (fresh * price.input + cached * price.cached_input
    + (usage?.output_tokens || 0) * price.output) / 1e6;
}

// Exact two-sided binomial test of b against c with p = 1/2 (McNemar's exact test).
export function exactTwoSided(b, c) {
  const n = b + c;
  if (n === 0) return 1;
  const k = Math.min(b, c);
  let term = 0.5 ** n; // C(n, 0) / 2^n
  let tail = term;
  for (let i = 1; i <= k; i += 1) {
    term = term * (n - i + 1) / i;
    tail += term;
  }
  return Math.min(1, 2 * tail);
}

export function mulberry32(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6D2B79F5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// Half labelled damaged, half undamaged (as far as the manifest allows), drawn from the
// held-out test split first, in an order fixed by the seed.
export function chooseFrames(rows, count, seed) {
  const random = mulberry32(seed);
  const shuffled = (items) => {
    const copy = [...items];
    for (let i = copy.length - 1; i > 0; i -= 1) {
      const j = Math.floor(random() * (i + 1));
      [copy[i], copy[j]] = [copy[j], copy[i]];
    }
    return copy;
  };
  const usable = rows.filter((row) => typeof row.damaged === "boolean" && row.path && row.sha256);
  const ordered = (damaged) => [
    ...shuffled(usable.filter((row) => row.damaged === damaged && row.split === "test")),
    ...shuffled(usable.filter((row) => row.damaged === damaged && row.split !== "test")),
  ];
  const damaged = ordered(true);
  const undamaged = ordered(false);
  const half = Math.min(Math.ceil(count / 2), damaged.length);
  const chosen = [...damaged.slice(0, half), ...undamaged.slice(0, count - half)];
  return shuffled(chosen);
}

export function buildRequest(basePrompt, dataUrl) {
  return {
    model: MODEL,
    input: [{
      role: promptConfig.role,
      content: [
        { type: "input_image", image_url: dataUrl, detail: DETAIL },
        { type: "input_text", text: basePrompt + promptConfig.captureLayouts.drive },
      ],
    }],
    text: {
      format: {
        type: "json_schema", name: promptConfig.schemaName, schema: DETECT_SCHEMA,
        strict: RUNTIME_CONFIG.strictStructuredOutputs,
      },
      verbosity: RUNTIME_CONFIG.textVerbosity,
    },
    reasoning: { effort: EFFORT },
    store: false,
  };
}

const readOutputText = (data) => {
  if (typeof data?.output_text === "string") return data.output_text;
  const message = (data?.output || []).find((item) => item.type === "message");
  return (message?.content || []).find((item) => item.type === "output_text")?.text;
};

// A frame counts as flagged when the model can judge it and calls it damaged: the rule
// the app uses to keep a frame.
export const flagged = (verdict) => Boolean(verdict)
  && verdict.image_quality === "acceptable" && verdict.assessment === "damaged";

const quantile = (values, q) => {
  if (!values.length) return null;
  const sorted = [...values].sort((x, y) => x - y);
  return sorted[Math.min(sorted.length - 1, Math.floor(q * sorted.length))];
};
const mean = (values) => (values.length ? values.reduce((s, v) => s + v, 0) / values.length : null);

export function summarise(pairs, extra = {}) {
  const arm = (name) => {
    const rows = pairs.map((pair) => ({ label: pair.damaged, ...pair[name] }));
    const damaged = rows.filter((row) => row.label);
    const undamaged = rows.filter((row) => !row.label);
    const times = rows.map((row) => row.processing_ms).filter((value) => Number.isFinite(value));
    return {
      damaged_kept: damaged.filter((row) => row.flagged).length, damaged_total: damaged.length,
      undamaged_kept: undamaged.filter((row) => !row.flagged).length, undamaged_total: undamaged.length,
      output_tokens_mean: mean(rows.map((row) => row.output_tokens)),
      input_tokens_mean: mean(rows.map((row) => row.input_tokens)),
      cached_calls: rows.filter((row) => row.cached_tokens > 0).length,
      processing_ms_median: quantile(times, 0.5), processing_ms_p90: quantile(times, 0.9),
      processing_ms_missing: rows.length - times.length,
      usd: rows.reduce((s, row) => s + row.usd, 0),
    };
  };
  const aOnly = pairs.filter((pair) => pair.a.flagged && !pair.b.flagged);
  const bOnly = pairs.filter((pair) => !pair.a.flagged && pair.b.flagged);
  return {
    pairs: pairs.length, a: arm("a"), b: arm("b"),
    discordant: {
      a_flagged_b_not: aOnly.length, b_flagged_a_not: bOnly.length,
      on_damaged: { a_only: aOnly.filter((p) => p.damaged).length, b_only: bOnly.filter((p) => p.damaged).length },
      on_undamaged: { a_only: aOnly.filter((p) => !p.damaged).length, b_only: bOnly.filter((p) => !p.damaged).length },
      exact_two_sided_p: exactTwoSided(aOnly.length, bOnly.length),
    },
    ...extra,
  };
}

export function report(summary) {
  const pct = (kept, total) => (total ? `${kept}/${total} (${(100 * kept / total).toFixed(1)}%)` : "0/0");
  const row = (name, s) => `${name.padEnd(13)} ${pct(s.damaged_kept, s.damaged_total).padEnd(16)} `
    + `${pct(s.undamaged_kept, s.undamaged_total).padEnd(16)} ${String(s.output_tokens_mean?.toFixed(1)).padEnd(11)} `
    + `${String(s.processing_ms_median).padEnd(10)} ${String(s.processing_ms_p90).padEnd(8)} ${s.usd.toFixed(4)}`;
  const d = summary.discordant;
  return [
    `Prompt eval ${summary.run_id || ""}: ${summary.pairs} of ${summary.frames_requested ?? summary.pairs} frames judged by both arms`
      + ` (model ${summary.model}, detail ${summary.detail}, effort ${summary.reasoning_effort})`,
    `A = production prompt ${summary.production_prompt_version}; B = ${summary.candidate_name}`
      + (summary.candidate_is_production ? " (identical text: this run measures the noise floor)" : ""),
    "",
    `${"arm".padEnd(13)} ${"damaged kept".padEnd(16)} ${"undamaged kept".padEnd(16)} ${"out tokens".padEnd(11)} ${"p50 ms".padEnd(10)} ${"p90 ms".padEnd(8)} USD`,
    row("A production", summary.a),
    row("B candidate", summary.b),
    "(ms is OpenAI's openai-processing-ms header, not wall time)",
    "",
    `discordant pairs: ${d.a_flagged_b_not + d.b_flagged_a_not} of ${summary.pairs}`
      + ` (A flagged, B not: ${d.a_flagged_b_not}; B flagged, A not: ${d.b_flagged_a_not})`,
    `  on damaged frames: A only ${d.on_damaged.a_only}, B only ${d.on_damaged.b_only};`
      + ` on undamaged frames: A only ${d.on_undamaged.a_only}, B only ${d.on_undamaged.b_only}`,
    `exact two-sided test on the discordant pairs: p = ${d.exact_two_sided_p.toFixed(4)}`
      + (d.exact_two_sided_p >= 0.05 ? " (the arms are not distinguishable at 0.05)" : " (the arms differ at 0.05)"),
    `spend: USD ${summary.spent_usd?.toFixed(4)} of a USD ${summary.budget_usd} budget`
      + `, ${summary.calls_ok} calls answered, ${summary.calls_failed} failed`
      + (summary.stopped_on_budget ? "; STOPPED EARLY: the budget would not cover another call" : ""),
  ].join("\n");
}

// ---------- the run ----------

// The detector secret is JSON with openai_api_key (or OPENAI_API_KEY), or the bare key:
// the shapes infra/aws-central/service/detectors.mjs accepts.
export function openaiKey(raw = process.env.DETECTOR_SECRET || "") {
  if (!raw.trim()) return (process.env.OPENAI_API_KEY || "").trim();
  let value;
  try { value = JSON.parse(raw); } catch { value = { openai_api_key: raw }; }
  return String(value.openai_api_key || value.OPENAI_API_KEY || "").trim();
}

const AGENT = new https.Agent({ keepAlive: true, maxSockets: CONCURRENCY });
const TARGET = new URL(RUNTIME_CONFIG.responsesUrl);

function callOnce(body, key) {
  return new Promise((resolve, reject) => {
    const request = https.request({
      host: TARGET.hostname, port: 443, path: TARGET.pathname, method: "POST", agent: AGENT,
      headers: { authorization: `Bearer ${key}`, "content-type": "application/json",
                 "content-length": Buffer.byteLength(body) },
      timeout: 60_000,
    }, (response) => {
      const chunks = [];
      response.on("data", (chunk) => chunks.push(chunk));
      response.on("error", reject);
      response.on("end", () => resolve({
        status: response.statusCode, text: Buffer.concat(chunks).toString("utf8"),
        httpVersion: response.httpVersion, reusedSocket: request.reusedSocket,
        requestId: response.headers["x-request-id"] || null,
        processingMs: Number(response.headers["openai-processing-ms"]),
      }));
    });
    request.on("timeout", () => request.destroy(new Error("timeout")));
    request.on("error", reject);
    request.end(body);
  });
}

async function main() {
  if (!USD_PER_M[MODEL]) throw new Error(`no price is recorded for ${MODEL}; refusing to run without a spend stop`);
  const key = openaiKey();
  if (!key) throw new Error("no OpenAI key: the buildspec reads DETECTOR_SECRET from Secrets Manager");
  mkdirSync(join(OUT, "frames"), { recursive: true });
  const candidateIsProduction = !CANDIDATE || CANDIDATE === "production";
  const candidateText = candidateIsProduction ? DETECT_PROMPT : readFileSync(CANDIDATE, "utf8").replace(/\s+$/, "");
  if (!candidateText.trim()) throw new Error("the candidate prompt is empty");
  const prompts = { a: DETECT_PROMPT, b: candidateText };

  const s3 = (...args) => run("aws", ["s3", ...args, "--only-show-errors"], { maxBuffer: 64 << 20 });
  await s3("cp", `s3://${BUCKET}/${DATA_PREFIX}/manifest.jsonl`, join(OUT, "manifest.jsonl"));
  const rows = readFileSync(join(OUT, "manifest.jsonl"), "utf8").trim().split("\n").map((line) => JSON.parse(line));
  const frames = chooseFrames(rows, FRAMES, SEED);
  console.log(`manifest: ${rows.length} rows; chosen ${frames.length} frames`
    + ` (${frames.filter((f) => f.damaged).length} labelled damaged), seed ${SEED}`);

  const random = mulberry32(SEED ^ 0x9E3779B9);
  const state = { spent: 0, inFlight: 0, ok: 0, failed: 0, stop: false, http: new Set(), reused: 0 };
  const pairs = [];
  const callsPath = join(OUT, "calls.jsonl");
  writeFileSync(callsPath, "");

  async function judge(frame, armName, dataUrl) {
    const body = JSON.stringify(buildRequest(prompts[armName], dataUrl));
    for (let attempt = 1; attempt <= 4; attempt += 1) {
      // The spend stop: never start a call the budget might not cover.
      if (state.spent + (state.inFlight + 1) * reservePerCall > BUDGET_USD) {
        state.stop = true;
        return null;
      }
      state.inFlight += 1;
      let result = null;
      let failure = null;
      try { result = await callOnce(body, key); } catch (error) { failure = String(error?.code || error?.message || error).slice(0, 120); }
      state.inFlight -= 1;
      if (result && result.status === 200) {
        const data = JSON.parse(result.text);
        let verdict = null;
        try { verdict = JSON.parse(readOutputText(data)); } catch { /* kept as an unparsed verdict */ }
        const usd = costUsd(data.usage);
        state.spent += usd;
        state.ok += 1;
        state.http.add(result.httpVersion);
        if (result.reusedSocket) state.reused += 1;
        reservePerCall = Math.max(reservePerCall, usd * 1.5);
        const row = {
          path: frame.path, arm: armName, attempts: attempt, flagged: flagged(verdict), verdict,
          processing_ms: Number.isFinite(result.processingMs) ? result.processingMs : null,
          input_tokens: data.usage?.input_tokens || 0, output_tokens: data.usage?.output_tokens || 0,
          cached_tokens: data.usage?.input_tokens_details?.cached_tokens || 0,
          usd, request_id: result.requestId, http: result.httpVersion, reused_socket: result.reusedSocket,
          status: data.status, at: new Date().toISOString(),
        };
        appendFileSync(callsPath, `${JSON.stringify(row)}\n`);
        return row;
      }
      failure = failure || `${result.status}: ${result.text.slice(0, 160)}`;
      const retryable = !result || [408, 409, 429].includes(result.status) || result.status >= 500;
      if (result && (result.status === 401 || result.status === 403)) {
        state.stop = true;
        throw new Error(`OpenAI rejected the key (${result.status}); stopping`);
      }
      if (!retryable || attempt === 4) { console.log(`failed ${frame.path} ${armName}: ${failure}`); break; }
      await new Promise((resolve) => setTimeout(resolve, 1000 * 2 ** attempt));
    }
    state.failed += 1;
    return null;
  }

  let next = 0;
  await Promise.all(Array.from({ length: Math.max(1, Math.floor(CONCURRENCY / 2)) }, async () => {
    while (next < frames.length && !state.stop) {
      const frame = frames[next];
      next += 1;
      const local = join(OUT, "frames", frame.sha256 + ".jpg");
      await s3("cp", `s3://${BUCKET}/${DATA_PREFIX}/frames/${frame.path}`, local);
      const bytes = readFileSync(local);
      if (createHash("sha256").update(bytes).digest("hex") !== frame.sha256) {
        throw new Error(`${frame.path} does not match the manifest's sha256`);
      }
      const dataUrl = `data:image/jpeg;base64,${bytes.toString("base64")}`;
      // Both arms of a pair at once, in a random order, on kept-alive HTTP/1.1 sockets.
      const order = random() < 0.5 ? ["a", "b"] : ["b", "a"];
      const first = judge(frame, order[0], dataUrl);
      await new Promise((resolve) => setTimeout(resolve, 150));
      const second = judge(frame, order[1], dataUrl);
      const [x, y] = await Promise.all([first, second]);
      if (x && y) pairs.push({ path: frame.path, damaged: frame.damaged, first: order[0], [order[0]]: x, [order[1]]: y });
    }
  }));

  const summary = summarise(pairs, {
    run_id: env("EVAL_RUN_ID", ""), commit: env("CODEBUILD_RESOLVED_SOURCE_VERSION", ""),
    model: MODEL, detail: DETAIL, reasoning_effort: EFFORT,
    production_prompt_version: DETECT_PROMPT_VERSION,
    production_prompt_sha256: createHash("sha256").update(DETECT_PROMPT).digest("hex"),
    candidate_name: candidateIsProduction ? "the production prompt again" : env("EVAL_CANDIDATE_NAME", CANDIDATE),
    candidate_sha256: createHash("sha256").update(candidateText).digest("hex"),
    candidate_is_production: candidateText === DETECT_PROMPT,
    frames_requested: FRAMES, frames_chosen: frames.length, seed: SEED,
    budget_usd: BUDGET_USD, spent_usd: state.spent, calls_ok: state.ok, calls_failed: state.failed,
    stopped_on_budget: state.stop, http_versions: [...state.http], reused_socket_calls: state.reused,
    data: `s3://${BUCKET}/${DATA_PREFIX}/`,
  });
  writeFileSync(join(OUT, "summary.json"), `${JSON.stringify(summary, null, 2)}\n`);
  writeFileSync(join(OUT, "pairs.jsonl"), pairs.map((pair) => JSON.stringify({
    path: pair.path, damaged: pair.damaged, first: pair.first, a: pair.a.flagged, b: pair.b.flagged,
  })).join("\n") + "\n");
  const text = report(summary);
  writeFileSync(join(OUT, "summary.txt"), `${text}\n`);
  console.log(`\n${text}`);
  AGENT.destroy();
  if (state.stop) process.exitCode = 3;
  else if (pairs.length < frames.length) process.exitCode = 4;
}

function selfTest() {
  const near = (x, y) => Math.abs(x - y) < 1e-9;
  const checks = [
    ["exact test, no discordant pairs", exactTwoSided(0, 0) === 1],
    ["exact test, 5 against 5", exactTwoSided(5, 5) === 1],
    ["exact test, 0 against 6 is 2/64", near(exactTwoSided(0, 6), 2 / 64)],
    ["exact test, 1 against 9 is 22/1024", near(exactTwoSided(1, 9), 22 / 1024)],
    ["exact test is symmetric", near(exactTwoSided(2, 7), exactTwoSided(7, 2))],
    ["cost counts cached input at its own price", near(costUsd({ input_tokens: 1000,
      input_tokens_details: { cached_tokens: 400 }, output_tokens: 100 }, "gpt-5-mini"),
      (600 * 0.25 + 400 * 0.025 + 100 * 2) / 1e6)],
    ["a rejected image is never flagged", !flagged({ image_quality: "rejected", assessment: "damaged" })],
    ["an acceptable damaged image is flagged", flagged({ image_quality: "acceptable", assessment: "damaged" })],
  ];
  checks.push(["the key is read from the secret's JSON", openaiKey('{"openai_api_key":" k1 ","yolo_api_key":"y"}') === "k1"
    && openaiKey('{"OPENAI_API_KEY":"k2"}') === "k2" && openaiKey("k3\n") === "k3"]);
  const rows = Array.from({ length: 40 }, (_, i) => ({ path: `p${i}`, sha256: `s${i}`, damaged: i % 4 === 0,
    split: i % 2 ? "test" : "train" }));
  const chosen = chooseFrames(rows, 12, 7);
  checks.push(["the sample is half damaged", chosen.filter((r) => r.damaged).length === 6 && chosen.length === 12]);
  checks.push(["the sample is repeatable", JSON.stringify(chosen) === JSON.stringify(chooseFrames(rows, 12, 7))]);
  const request = buildRequest("PROMPT", "data:image/jpeg;base64,AAAA");
  checks.push(["the request carries one image before the text",
    request.input[0].content.length === 2 && request.input[0].content[0].type === "input_image"
    && request.input[0].content[1].text.startsWith("PROMPT") && request.store === false]);
  const call = (isFlagged, ms) => ({ flagged: isFlagged, processing_ms: ms, output_tokens: 80, input_tokens: 1200, cached_tokens: 0, usd: 0.0005 });
  const summary = summarise([
    { damaged: true, a: call(true, 900), b: call(true, 800) },
    { damaged: true, a: call(true, 950), b: call(false, 700) },
    { damaged: false, a: call(false, 1000), b: call(true, 750) },
    { damaged: false, a: call(false, 1100), b: call(false, 650) },
  ]);
  checks.push(["summary counts kept frames per arm", summary.a.damaged_kept === 2 && summary.b.damaged_kept === 1
    && summary.a.undamaged_kept === 2 && summary.b.undamaged_kept === 1]);
  checks.push(["summary counts discordant pairs both ways", summary.discordant.a_flagged_b_not === 1
    && summary.discordant.b_flagged_a_not === 1 && summary.discordant.exact_two_sided_p === 1]);
  let failed = 0;
  for (const [name, ok] of checks) { console.log(`${ok ? "ok  " : "FAIL"} ${name}`); if (!ok) failed += 1; }
  process.exit(failed ? 1 : 0);
}

if (SELF_TEST) selfTest();
else main().catch((error) => { console.error(`FAIL ${error.message}`); process.exit(1); });
