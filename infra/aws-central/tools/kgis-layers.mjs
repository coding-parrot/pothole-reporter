// The KGIS layers Karnataka road ownership is read from, and the one polite way this
// repository talks to KGIS outside the request path: one request at a time, a pause
// between requests, retries with backoff, and a download that can be stopped and resumed.
//
// build-karnataka-geometry.mjs uses it to snapshot whole layers; verify-local-ownership.mjs
// uses it to put single points to the live register and compare them with the snapshot.
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { setTimeout as sleep } from "node:timers/promises";

const BASEMAP = "https://kgis.ksrsac.in/kgismaps/rest/services/State_Basemap/State_Basemap_Dynamic/MapServer";

// Each highway layer is land cover: polygons of carriageway, not centre lines. They carry
// no edit date, so a refresh is told apart from the last one by the hash of what it read.
export const KGIS_LAYERS = Object.freeze({
  national_highway: {
    key: "nh", layer_id: 289, url: `${BASEMAP}/289`,
    fields: "OBJECTID,Name,Remarks,LULC_Code", minimum: 50, page_cap: 10,
  },
  state_highway: {
    key: "sh", layer_id: 290, url: `${BASEMAP}/290`,
    fields: "OBJECTID,Name,Remarks,LULC_Code", minimum: 5_000, page_cap: 1_000,
  },
  district_highway: {
    key: "dh", layer_id: 291, url: `${BASEMAP}/291`,
    fields: "OBJECTID,Name,Remarks,LULC_Code", minimum: 10_000, page_cap: 1_000,
  },
  gram_panchayat: {
    key: "gp", layer_id: 0, url: "https://kgis.ksrsac.in/kgismaps/rest/services/Boundaries/GP_Boundary/MapServer/0",
    fields: "OBJECTID,KGISGPID,KGISGPName,KGISDistrictCode,KGISGP_DeptCode,last_edited_date",
    minimum: 5_000, page_cap: 250,
  },
});

export const KGIS_TOWN_LAYER_URL = "https://kgis.ksrsac.in/kgismaps/rest/services/Boundaries/Admin_Dynamic_New/MapServer/1";

const USER_AGENT = "PotholeReporter-snapshot/1 (+https://coding-parrot.github.io/pothole-reporter/; contact@aiengg.dev)";
const sha256File = (file) => new Promise((resolve, reject) => {
  const hash = crypto.createHash("sha256");
  fs.createReadStream(file).on("data", (chunk) => hash.update(chunk))
    .on("end", () => resolve(hash.digest("hex"))).on("error", reject);
});

// One request at a time, process-wide, with a pause after each: KGIS is a shared public
// service that stalls under load, and nothing here is in a hurry.
let queue = Promise.resolve();
export function politeJson(url, { timeoutMs = 180_000, attempts = 6, pauseMs = 400, log = () => {} } = {}) {
  const run = async () => {
    let failure = null;
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      if (attempt) await sleep(Math.min(60_000, 2_000 * 2 ** (attempt - 1)));
      try {
        const response = await fetch(url, {
          headers: { "user-agent": USER_AGENT },
          signal: AbortSignal.timeout(timeoutMs),
        });
        if (!response.ok) throw new Error(`KGIS answered ${response.status}`);
        const data = await response.json();
        if (!data || typeof data !== "object" || data.error) {
          throw new Error(`KGIS answered an error: ${JSON.stringify(data?.error || data).slice(0, 200)}`);
        }
        await sleep(pauseMs);
        return data;
      } catch (error) {
        failure = error;
        log(`attempt ${attempt + 1} of ${attempts} failed: ${String(error?.message || error).slice(0, 160)}`);
      }
    }
    throw failure;
  };
  const result = queue.then(run, run);
  queue = result.catch(() => {});
  return result;
}

export async function layerCount(layer, options) {
  const { count } = await politeJson(`${layer.url}/query?where=1%3D1&returnCountOnly=true&f=json`, options);
  if (!Number.isInteger(count)) throw new Error(`${layer.url} gave no count`);
  return count;
}

function pageUrl(layer, offset, size) {
  return `${layer.url}/query?where=1%3D1&outFields=${encodeURIComponent(layer.fields)}`
    + "&returnGeometry=true&outSR=4326&geometryPrecision=7&orderByFields=OBJECTID"
    + `&resultOffset=${offset}&resultRecordCount=${size}&f=json`;
}

const writeAtomically = (file, text) => {
  fs.writeFileSync(`${file}.tmp`, text);
  fs.renameSync(`${file}.tmp`, file);
};

// Reads a whole layer into <workDir>/<key>.ndjson, one feature a line as KGIS gave it
// (WGS84, seven decimals), paging by resultOffset in OBJECTID order at the layer's own
// maxRecordCount. <key>.state.json is rewritten after every page, so a run that is
// stopped (or that KGIS stops) resumes at the next page; a page that keeps failing is
// asked for again at half the size. When the last page lands the count is checked against
// a fresh returnCountOnly and the file is hashed into <key>.done.json.
export async function downloadLayer(name, { workDir, log = console.log, restart = false } = {}) {
  const layer = KGIS_LAYERS[name];
  if (!layer) throw new Error(`Unknown KGIS layer ${name}`);
  fs.mkdirSync(workDir, { recursive: true });
  const dataFile = path.join(workDir, `${layer.key}.ndjson`);
  const stateFile = path.join(workDir, `${layer.key}.state.json`);
  const doneFile = path.join(workDir, `${layer.key}.done.json`);
  if (restart) for (const file of [dataFile, stateFile, doneFile]) fs.rmSync(file, { force: true });
  if (fs.existsSync(doneFile)) {
    const done = JSON.parse(fs.readFileSync(doneFile, "utf8"));
    log(`${layer.key}: already complete (${done.count} features, retrieved ${done.retrieved_at})`);
    return done;
  }
  const quiet = { log: (line) => log(`${layer.key}: ${line}`) };
  const meta = await politeJson(`${layer.url}?f=json`, quiet);
  const maxRecordCount = Number(meta.maxRecordCount) || 1_000;
  const expected = await layerCount(layer, quiet);
  if (expected < layer.minimum) throw new Error(`${layer.url} holds ${expected} features, fewer than ${layer.minimum}`);
  let state = fs.existsSync(stateFile) ? JSON.parse(fs.readFileSync(stateFile, "utf8")) : null;
  if (state && (state.source !== layer.url || state.expected !== expected || !fs.existsSync(dataFile)
      || fs.statSync(dataFile).size < state.bytes)) {
    log(`${layer.key}: the partial download no longer matches the layer (count ${state.expected} then, ${expected} now); starting again`);
    state = null;
  }
  if (!state) {
    state = {
      source: layer.url, layer_id: layer.layer_id, fields: layer.fields, expected,
      max_record_count: maxRecordCount, page_size: Math.min(maxRecordCount, layer.page_cap),
      next_offset: 0, bytes: 0, last_object_id: 0, source_last_edited_ms: 0,
      started_at: new Date().toISOString(),
    };
    fs.writeFileSync(dataFile, "");
  } else {
    // Drop whatever a stopped run wrote past its last recorded page.
    fs.truncateSync(dataFile, state.bytes);
    log(`${layer.key}: resuming at ${state.next_offset} of ${expected}`);
  }
  while (state.next_offset < expected) {
    let page = null;
    while (!page) {
      try {
        page = await politeJson(pageUrl(layer, state.next_offset, state.page_size), { ...quiet, attempts: 4 });
        if (!Array.isArray(page.features) || !page.features.length) throw new Error("an empty page");
        if (page.spatialReference?.wkid !== 4326) throw new Error("KGIS did not answer in WGS84");
      } catch (error) {
        page = null;
        if (state.page_size === 1) throw error;
        state.page_size = Math.max(1, Math.floor(state.page_size / 2));
        log(`${layer.key}: page at ${state.next_offset} kept failing; page size now ${state.page_size}`);
      }
    }
    let text = "";
    for (const feature of page.features) {
      const id = feature?.attributes?.OBJECTID;
      if (!Number.isInteger(id) || id <= state.last_object_id) {
        throw new Error(`${layer.key}: OBJECTID ${id} after ${state.last_object_id}; the layer changed under the download`);
      }
      if (!Array.isArray(feature.geometry?.rings)) throw new Error(`${layer.key}: OBJECTID ${id} has no rings`);
      state.last_object_id = id;
      state.source_last_edited_ms = Math.max(state.source_last_edited_ms, Number(feature.attributes.last_edited_date) || 0);
      text += `${JSON.stringify({ attributes: feature.attributes, rings: feature.geometry.rings })}\n`;
    }
    fs.appendFileSync(dataFile, text);
    state.bytes += Buffer.byteLength(text);
    state.next_offset += page.features.length;
    writeAtomically(stateFile, JSON.stringify(state));
    log(`${layer.key}: ${state.next_offset} of ${expected}`);
  }
  const after = await layerCount(layer, quiet);
  if (after !== expected || state.next_offset !== expected) {
    throw new Error(`${layer.key}: read ${state.next_offset} features, KGIS counted ${expected} before and ${after} after`);
  }
  const done = {
    layer: name, key: layer.key, source: layer.url, layer_id: layer.layer_id, source_fields: layer.fields,
    count: state.next_offset, kgis_count: after, max_record_count: maxRecordCount,
    source_last_edited: state.source_last_edited_ms
      ? new Date(state.source_last_edited_ms).toISOString().slice(0, 10) : null,
    retrieved_at: new Date().toISOString().slice(0, 10),
    raw_bytes: state.bytes, raw_sha256: await sha256File(dataFile),
  };
  writeAtomically(doneFile, JSON.stringify(done, null, 2));
  log(`${layer.key}: complete, ${done.count} features, ${done.raw_bytes} bytes, sha256 ${done.raw_sha256}`);
  return done;
}
