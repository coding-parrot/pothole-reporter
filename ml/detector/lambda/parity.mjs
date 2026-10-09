// Compare the Lambda's locator (sharp + ONNX Runtime) with the scores the training
// instance computed in PyTorch for the same held-out pictures.
//
//   node ml/detector/lambda/parity.mjs <dir with images/, index.json, python-<model>.json> <python file>
//
// A picture agrees when both give it a best score within 0.05 of each other and, where
// both mark it, the best boxes overlap (IoU at least 0.7).
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { createLocator } from "./locator.mjs";

const [dir, reference] = process.argv.slice(2);
const here = path.dirname(fileURLToPath(import.meta.url));
const locator = await createLocator({ modelDir: path.join(here, "model") });
const expected = JSON.parse(readFileSync(path.join(dir, reference), "utf8"));
const sizes = new Map(JSON.parse(readFileSync(path.join(dir, "index.json"), "utf8")).map((row) => [row.name, row]));
const iou = (a, b) => {
  const w = Math.min(a[2], b[2]) - Math.max(a[0], b[0]), h = Math.min(a[3], b[3]) - Math.max(a[1], b[1]);
  if (w <= 0 || h <= 0) return 0;
  return w * h / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - w * h);
};
let worst = 0, scoreMisses = 0, boxMisses = 0, compared = 0, times = [];
for (const row of expected) {
  const { width, height } = sizes.get(row.name);
  const got = await locator.score(readFileSync(path.join(dir, "images", row.name)));
  times.push(got.decodeMs + got.inferMs);
  const difference = Math.abs(got.score - row.score);
  worst = Math.max(worst, difference);
  if (difference > 0.05) scoreMisses += 1;
  if (got.boxes.length && row.boxes.length && Math.min(got.score, row.score) >= 0.15) {
    compared += 1;
    const top = [...row.boxes].sort((a, b) => b[4] - a[4])[0];
    const mine = got.boxes[0];
    const mineBox = [mine.x * width, mine.y * height, (mine.x + mine.w) * width, (mine.y + mine.h) * height];
    if (iou(mineBox, top) < 0.7) boxMisses += 1;
  }
}
times.sort((a, b) => a - b);
console.log(JSON.stringify({ model: locator.meta.model_version, pictures: expected.length,
  largest_score_difference: Number(worst.toFixed(4)), scores_apart_by_over_0_05: scoreMisses,
  best_boxes_compared: compared, best_boxes_that_differ: boxMisses,
  median_ms_on_this_machine: Math.round(times[Math.floor(times.length / 2)]) }));
if (scoreMisses > expected.length * 0.05 || boxMisses > Math.max(1, compared * 0.05)) process.exit(1);
