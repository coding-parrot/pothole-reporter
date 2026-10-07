// Lambda entry point (handler.handler). The model loads during init, where Lambda gives
// the function its full CPU, so no request pays for it. A model that cannot load does
// not crash the function: every request then gets a 503 the central service reads as
// "screen unavailable" and falls through to gpt-5-mini.

import path from "node:path";
import { fileURLToPath } from "node:url";

import { createScorer } from "./scorer.mjs";
import { createHandler } from "./service.mjs";

const modelDir = process.env.MODEL_DIR
  || path.join(path.dirname(fileURLToPath(import.meta.url)), "model");
const loading = createScorer({ modelDir, threads: Number(process.env.SCREEN_THREADS || 2) });
loading.then(
  (scorer) => console.log(JSON.stringify({ event: "screen_model_loaded",
    model_version: scorer.meta.model_version, input_size: scorer.meta.input_size,
    threshold: scorer.meta.threshold })),
  (error) => console.log(JSON.stringify({ event: "screen_model_failed",
    error_type: String(error?.name || "Error"), error_message: String(error?.message).slice(0, 200) })),
);
await loading.catch(() => {});

export const handler = createHandler({ scorer: () => loading });
