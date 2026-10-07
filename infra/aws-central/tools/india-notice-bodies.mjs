// Reads the State/UT road notice packs the app ships (static/road-notice-manifest-*.json)
// for the offline ward tools (india-ward-coverage.mjs, india-ward-runtime.mjs). Which
// urban body a notice belongs to is decided by service/notice-bodies.mjs, which the
// service reads too; it is passed on from here so the tools keep one import.
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { noticeForBody, urbanBodyOf } from "../service/notice-bodies.mjs";

export { urbanBodyOf };

export const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");

export function newestManifest(prefix) {
  const dir = path.join(root, "static");
  const versioned = fs.readdirSync(dir)
    .map((name) => name.match(new RegExp(`^${prefix}-v(\\d+)\\.(\\d+)\\.json$`)))
    .filter(Boolean)
    .sort((left, right) => Number(left[1]) - Number(right[1]) || Number(left[2]) - Number(right[2]));
  if (!versioned.length) throw new Error(`No versioned ${prefix} in static/`);
  return path.join(dir, versioned[versioned.length - 1][0]);
}

// Every notice of every State/UT pack, hash-checked against the manifest.
export function loadRoadNotices() {
  const manifestPath = newestManifest("road-notice-manifest");
  const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
  const notices = [];
  const states = [];
  for (const resource of Object.values(manifest.resources)) {
    const bytes = fs.readFileSync(path.join(root, "docs", resource.path));
    if (crypto.createHash("sha256").update(bytes).digest("hex") !== resource.sha256) {
      throw new Error(`Road notice pack ${resource.pack_id} does not match its manifest hash`);
    }
    const pack = JSON.parse(bytes);
    states.push({ state: resource.state_code, notices: pack.notices.length, retrieved_at: resource.source_retrieved_at });
    for (const notice of pack.notices) {
      notices.push({ ...noticeForBody(resource.state_code, notice), tender_id: notice.tender_id, closing_at: notice.closing_at || null });
    }
  }
  return { manifest: path.basename(manifestPath), generated_at: manifest.generated_at, states, notices };
}
