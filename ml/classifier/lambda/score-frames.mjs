#!/usr/bin/env node
// Score frames through the exact serving path (sharp letterbox + ONNX Runtime) so the
// threshold is chosen on the scores the Lambda will produce, not on PIL's resize.
//
//   node score-frames.mjs <model dir> <frames root> < paths.txt > scores.jsonl

import { readFileSync } from "node:fs";
import path from "node:path";

import { createScorer } from "./scorer.mjs";

const [modelDir, root] = process.argv.slice(2);
const scorer = await createScorer({ modelDir, threads: 3 });
const paths = readFileSync(0, "utf8").split("\n").filter(Boolean);
for (const relative of paths) {
  const result = await scorer.score(readFileSync(path.join(root, relative)));
  process.stdout.write(`${JSON.stringify({ path: relative, score: result.score,
    decode_ms: +result.decodeMs.toFixed(2), infer_ms: +result.inferMs.toFixed(2) })}\n`);
}
