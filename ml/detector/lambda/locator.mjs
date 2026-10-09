// The pothole detector as the Lambda runs it: one whole frame in, the boxes it found
// out. Preprocessing is yolox.data.ValTransform's, which is what the model was trained
// and scored with: the complete frame scaled to fit 640 x 640 from the top-left corner,
// the rest filled with grey 114, pixels in BGR order as 0..255 floats. No crop, tile or
// mask. The model's output is decoded in its own graph (export_onnx.py): one row per
// candidate, (cx, cy, w, h, objectness, pothole score) in input pixels.

import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";

export const PAD = 114;
const MAX_DECODED_PIXELS = 12_000_000;

// Where the frame lands in the model's square, exactly as ValTransform computes it.
export function fitGeometry(width, height, size) {
  const ratio = Math.min(size / height, size / width);
  return { ratio, width: Math.max(1, Math.trunc(width * ratio)), height: Math.max(1, Math.trunc(height * ratio)) };
}

const overlap = (a, b) => {
  const w = Math.min(a.x2, b.x2) - Math.max(a.x1, b.x1);
  const h = Math.min(a.y2, b.y2) - Math.max(a.y1, b.y1);
  if (w <= 0 || h <= 0) return 0;
  const inter = w * h;
  return inter / ((a.x2 - a.x1) * (a.y2 - a.y1) + (b.x2 - b.x1) * (b.y2 - b.y1) - inter);
};

// rows: Float32Array of n x 6. Returns boxes in the ORIGINAL frame, as fractions of its
// width and height, best first, after non-maximum suppression.
export function decode(rows, { ratio, frameWidth, frameHeight, minScore = 0.05, nms = 0.65, keep = 20 }) {
  const found = [];
  for (let i = 0; i + 5 < rows.length; i += 6) {
    const score = rows[i + 4] * rows[i + 5];
    if (!(score >= minScore)) continue;
    const [cx, cy, w, h] = [rows[i], rows[i + 1], rows[i + 2], rows[i + 3]];
    found.push({ x1: (cx - w / 2) / ratio, y1: (cy - h / 2) / ratio,
      x2: (cx + w / 2) / ratio, y2: (cy + h / 2) / ratio, score });
  }
  found.sort((a, b) => b.score - a.score);
  const kept = [];
  for (const box of found) {
    if (kept.some((other) => overlap(box, other) > nms)) continue;
    kept.push(box);
    if (kept.length >= keep) break;
  }
  const clamp = (value) => Math.min(1, Math.max(0, value));
  const round = (value) => Math.round(value * 10000) / 10000;
  return kept.map((box) => {
    const x = clamp(box.x1 / frameWidth), y = clamp(box.y1 / frameHeight);
    return { x: round(x), y: round(y),
      w: round(clamp(box.x2 / frameWidth) - x), h: round(clamp(box.y2 / frameHeight) - y),
      score: round(box.score) };
  }).filter((box) => box.w > 0 && box.h > 0);
}

export async function createLocator({ modelDir, threads = 2 } = {}) {
  const meta = JSON.parse(readFileSync(path.join(modelDir, "model.json"), "utf8"));
  const bytes = readFileSync(path.join(modelDir, "model.onnx"));
  if (createHash("sha256").update(bytes).digest("hex") !== meta.sha256) {
    throw new Error("model.onnx does not match the sha256 in model.json");
  }
  const ort = await import("onnxruntime-node");
  const sharp = (await import("sharp")).default;
  sharp.cache(false);
  sharp.concurrency(threads);
  const session = await ort.InferenceSession.create(bytes, {
    intraOpNumThreads: threads, interOpNumThreads: 1,
    executionMode: "sequential", graphOptimizationLevel: "all",
  });
  const size = meta.input_size;
  const plane = size * size;

  async function prepare(buffer) {
    try {
      // No shrink-on-load and no EXIF rotation: training decoded the JPEG as stored.
      const image = sharp(buffer, { limitInputPixels: MAX_DECODED_PIXELS, failOn: "error" });
      const { width, height } = await image.metadata();
      const fit = fitGeometry(width, height, size);
      const { data, info } = await image
        .removeAlpha()
        .toColourspace("srgb")
        .resize(fit.width, fit.height, { fit: "fill", kernel: "linear", fastShrinkOnLoad: false })
        .extend({ top: 0, left: 0, right: size - fit.width, bottom: size - fit.height,
          background: { r: PAD, g: PAD, b: PAD } })
        .raw()
        .toBuffer({ resolveWithObject: true });
      if (info.width !== size || info.height !== size || info.channels !== 3) {
        throw new Error("unexpected decoded shape");
      }
      // Interleaved RGB bytes to planar BGR floats.
      const pixels = new Float32Array(3 * plane);
      for (let i = 0, p = 0; i < plane; i += 1, p += 3) {
        pixels[i] = data[p + 2];
        pixels[plane + i] = data[p + 1];
        pixels[2 * plane + i] = data[p];
      }
      return { pixels, width, height, ratio: fit.ratio };
    } catch (error) {
      throw Object.assign(new Error("undecodable image", { cause: error }), { code: "bad_image" });
    }
  }

  async function run(pixels) {
    const output = await session.run({ images: new ort.Tensor("float32", pixels, [1, 3, size, size]) });
    return output.output.data;
  }

  // The first run allocates the arena and the thread pool, during init and not in a request.
  await run(new Float32Array(3 * plane).fill(PAD));

  return {
    meta,
    async score(buffer) {
      const started = performance.now();
      const frame = await prepare(buffer);
      const decoded = performance.now();
      const rows = await run(frame.pixels);
      const boxes = decode(rows, { ratio: frame.ratio, frameWidth: frame.width, frameHeight: frame.height,
        minScore: meta.min_box_score ?? 0.05, nms: meta.nms ?? 0.65 });
      // The frame's score is its best box's: "is there a pothole in this picture".
      return { score: boxes.length ? boxes[0].score : 0, boxes,
        decodeMs: decoded - started, inferMs: performance.now() - decoded };
    },
  };
}
