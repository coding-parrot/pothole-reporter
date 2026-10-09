// Lambda entry point (handler.handler) for the pothole locator. The request and response
// are the screen's (ml/classifier/lambda/service.mjs, copied in by build.sh): the same
// contract the central service already speaks, with `boxes` beside the score. The model
// loads during init; one that cannot load answers 503, which the central service reads
// as "no marks for this frame".

import path from "node:path";
import { fileURLToPath } from "node:url";

import { createLocator } from "./locator.mjs";
import { createHandler } from "./service.mjs";

const modelDir = process.env.MODEL_DIR
  || path.join(path.dirname(fileURLToPath(import.meta.url)), "model");
const loading = createLocator({ modelDir, threads: Number(process.env.SCREEN_THREADS || 2) });
loading.then(
  (locator) => console.log(JSON.stringify({ event: "locator_model_loaded",
    model_version: locator.meta.model_version, input_size: locator.meta.input_size,
    threshold: locator.meta.threshold })),
  (error) => console.log(JSON.stringify({ event: "locator_model_failed",
    error_type: String(error?.name || "Error"), error_message: String(error?.message).slice(0, 200) })),
);
await loading.catch(() => {});

export const handler = createHandler({ scorer: () => loading });
