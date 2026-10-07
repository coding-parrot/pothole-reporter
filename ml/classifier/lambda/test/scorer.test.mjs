import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import test from "node:test";

import { PAD_RGB, createScorer, letterboxGeometry, soften } from "../scorer.mjs";

const modelDir = new URL("../model/", import.meta.url).pathname;
const hasModel = existsSync(`${modelDir}model.onnx`);

test("the whole frame is scaled to the long edge and centred, as models.py letterbox does", () => {
  // (width, height, size) -> what ml/classifier/models.py computes for the same frame.
  assert.deepEqual(letterboxGeometry(480, 720, 448),
    { width: 299, height: 448, left: 74, top: 0, right: 75, bottom: 0 });
  assert.deepEqual(letterboxGeometry(720, 1280, 448),
    { width: 252, height: 448, left: 98, top: 0, right: 98, bottom: 0 });
  assert.deepEqual(letterboxGeometry(1280, 720, 224),
    { width: 224, height: 126, left: 0, top: 49, right: 0, bottom: 49 });
  assert.deepEqual(letterboxGeometry(720, 720, 224),
    { width: 224, height: 224, left: 0, top: 0, right: 0, bottom: 0 });
  // A half rounds up, as Kotlin roundToInt and Python floor(x + 0.5) do.
  assert.equal(letterboxGeometry(3, 8, 4).width, 2);
  assert.equal(letterboxGeometry(1, 4000, 224).width, 1);
  for (const [w, h, size] of [[480, 720, 448], [1279, 853, 448], [333, 1280, 224]]) {
    const box = letterboxGeometry(w, h, size);
    assert.equal(box.left + box.width + box.right, size);
    assert.equal(box.top + box.height + box.bottom, size);
  }
  assert.deepEqual(PAD_RGB, { r: 124, g: 116, b: 104 });
});

test("the packaged model scores the example pothole above a clean road frame", { skip: !hasModel }, async () => {
  const scorer = await createScorer({ modelDir });
  const size = scorer.meta.input_size;
  const pothole = readFileSync(new URL("../../../../docs/example-pothole.jpg", import.meta.url));
  const pixels = await scorer.prepare(pothole);
  assert.equal(pixels.length, size * size * 3, "the frame fills the model input exactly");
  const result = await scorer.score(pothole);
  assert.ok(result.score >= 0 && result.score <= 1);
  assert.ok(result.score >= scorer.meta.threshold,
    `the example pothole scored ${result.score}, under the threshold ${scorer.meta.threshold}`);
  await assert.rejects(scorer.score(Buffer.from("not an image")), (error) => error.code === "bad_image");
});

test("the temperature spreads scores out and never reorders them", () => {
  assert.equal(soften(0.3, 1), 0.3);
  assert.equal(soften(0.3, undefined), 0.3);
  assert.ok(Math.abs(soften(0.5, 8) - 0.5) < 1e-12);
  // logit(0.0006) is about -7.42; at temperature 8 that is sigmoid(-0.927).
  assert.ok(Math.abs(soften(0.0006, 8) - 1 / (1 + Math.exp(Math.log(0.9994 / 0.0006) / 8))) < 1e-12);
  const raw = [0, 1e-30, 1e-9, 0.0006, 0.2, 0.5, 0.97, 1 - 1e-7, 1];
  const soft = raw.map((value) => soften(value, 8));
  for (let index = 1; index < soft.length; index += 1) assert.ok(soft[index] >= soft[index - 1]);
  // A saturated float32 sigmoid (exactly 0 or 1) stays finite and inside (0, 1).
  assert.ok(soft[0] > 0 && soft.at(-1) < 1);
  assert.ok(Math.abs(soft[0] - 1 / (1 + Math.exp(30 / 8))) < 1e-12);
});
