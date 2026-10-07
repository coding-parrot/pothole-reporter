import { readFileSync } from "node:fs";

// The photograph the canary sends for detection: docs/example-pothole.jpg, a real
// pothole the detector must call damaged. deploy.sh copies it into the Lambda package at
// the same path relative to this file as in the repo, and tools/check-package.mjs reads
// it through this module before anything is uploaded.
export const EXAMPLE_IMAGE = new URL("../../../../docs/example-pothole.jpg", import.meta.url);

export function readExampleImage() {
  const bytes = readFileSync(EXAMPLE_IMAGE);
  // JPEG files start FF D8 FF. An empty or truncated copy would be sent to the detector
  // and come back as a rejected image, which reads like a detector fault.
  if (bytes.length < 1024 || bytes[0] !== 0xff || bytes[1] !== 0xd8 || bytes[2] !== 0xff) {
    throw new Error(`${EXAMPLE_IMAGE.pathname} is not a JPEG photograph (${bytes.length} bytes)`);
  }
  return bytes;
}
