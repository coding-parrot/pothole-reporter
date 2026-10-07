// Model loading and the one whole-frame preprocessing, the same as
// ml/classifier/models.py letterbox(): scale the complete frame so its long edge is the
// model's input size, pad the rest with the ImageNet mean. No crop, tile or mask.

import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";

export const PAD_RGB = Object.freeze({ r: 124, g: 116, b: 104 });
const MAX_DECODED_PIXELS = 12_000_000;

export function letterboxGeometry(width, height, size) {
  const scale = size / Math.max(width, height);
  const fit = (value) => Math.max(1, Math.min(size, Math.floor(value * scale + 0.5)));
  const w = fit(width);
  const h = fit(height);
  const left = Math.floor((size - w) / 2);
  const top = Math.floor((size - h) / 2);
  return { width: w, height: h, left, top, right: size - w - left, bottom: size - h - top };
}

// The head is far too sure of itself: raw scores pile up at 0 and 1 and the useful
// threshold sat near 0.0006. Dividing the logit by the temperature release.py fitted on
// validation spreads the scores out without changing their order, so a threshold and a
// logged screen_score are readable numbers. release.py applies the same formula.
export const LOGIT_LIMIT = 30;
export function soften(score, temperature = 1) {
  if (!(temperature > 0) || temperature === 1) return score;
  const logit = Math.max(-LOGIT_LIMIT, Math.min(LOGIT_LIMIT, Math.log(score / (1 - score))));
  return 1 / (1 + Math.exp(-logit / temperature));
}

export async function createScorer({ modelDir, threads = 2 } = {}) {
  const meta = JSON.parse(readFileSync(path.join(modelDir, "model.json"), "utf8"));
  const bytes = readFileSync(path.join(modelDir, "model.onnx"));
  // The threshold in model.json was chosen for exactly these bytes.
  if (createHash("sha256").update(bytes).digest("hex") !== meta.sha256) {
    throw new Error("model.onnx does not match the sha256 in model.json");
  }
  const ort = await import("onnxruntime-node");
  const sharp = (await import("sharp")).default;
  sharp.cache(false);
  sharp.concurrency(threads);
  const session = await ort.InferenceSession.create(bytes, {
    intraOpNumThreads: threads,
    interOpNumThreads: 1,
    executionMode: "sequential",
    graphOptimizationLevel: "all",
  });
  const size = meta.input_size;

  async function prepare(buffer) {
    try {
      // No shrink-on-load and no EXIF rotation: training decoded the full JPEG as stored.
      const image = sharp(buffer, { limitInputPixels: MAX_DECODED_PIXELS, failOn: "error" });
      const { width, height } = await image.metadata();
      const box = letterboxGeometry(width, height, size);
      const { data, info } = await image
        .removeAlpha()
        .toColourspace("srgb")
        .resize(box.width, box.height, { fit: "fill", kernel: "cubic", fastShrinkOnLoad: false })
        .extend({ top: box.top, bottom: box.bottom, left: box.left, right: box.right, background: PAD_RGB })
        .raw()
        .toBuffer({ resolveWithObject: true });
      if (info.width !== size || info.height !== size || info.channels !== 3) {
        throw new Error("unexpected decoded shape");
      }
      return data;
    } catch (error) {
      throw Object.assign(new Error("undecodable image", { cause: error }), { code: "bad_image" });
    }
  }

  async function run(pixels) {
    const frames = new ort.Tensor("uint8", pixels, [1, size, size, 3]);
    const output = await session.run({ frames });
    return soften(Number(output.score.data[0]), meta.temperature);
  }

  // The first run allocates the arena and the thread pool. Paying for it during init
  // keeps it out of the first request.
  await run(new Uint8Array(size * size * 3).fill(114));

  return {
    meta,
    prepare,
    async score(buffer) {
      const started = performance.now();
      const pixels = await prepare(buffer);
      const decoded = performance.now();
      const score = await run(pixels);
      return { score, decodeMs: decoded - started, inferMs: performance.now() - decoded };
    },
  };
}
