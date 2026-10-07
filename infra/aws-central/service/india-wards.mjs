import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";

import { pointInRings, withinBox } from "./spatial.mjs";

// The ward a point outside Karnataka is in, from ward polygons copied from open sources
// (data/wards/<STATE>/<city>.json, built by tools/snapshot-india-wards.mjs). Karnataka's
// own wards come from the KGIS layer in geolocation.mjs and are not read here.
//
// Not every snapshot is used. data/wards/runtime.json lists the ones that are switched
// on, and tools/india-ward-runtime.mjs writes it from two records: the index's `use`
// (match by name, by number or by nothing) and data/wards/runtime-handread.json, in which
// a person read the ward and notice pairs this service would return for that body. A
// snapshot is on only with no pair read wrong and at most one in ten undecided. On
// 7 Oct 2026 that is two of 28 (Ahmedabad by name, Bhopal by number): the others are
// numbered for a delimitation their tenders no longer use, or their bodies have too few
// notices to read (data/wards/COVERAGE.md, sections 7 and 11).
//
// Same path relative to the service in the repo and in the Lambda package; deploy.sh
// stages runtime.json and only the files it lists (tools/stage-india-wards.mjs).
export const INDIA_WARDS_DIR = new URL("../../../data/wards/", import.meta.url);
export const INDIA_WARD_RUNTIME_FILE = "runtime.json";
export const INDIA_WARD_RUNTIME_FORMAT = "pothole-india-ward-runtime";
export const INDIA_WARD_SNAPSHOT_FORMAT = "pothole-india-ward-polygons";

const sha256Hex = (bytes) => createHash("sha256").update(bytes).digest("hex");

function validRuntime(runtime) {
  return runtime && runtime.format === INDIA_WARD_RUNTIME_FORMAT
    && Number.isFinite(runtime.coordinate_scale) && runtime.coordinate_scale > 0
    && Array.isArray(runtime.snapshots)
    && runtime.snapshots.every((entry) => entry && typeof entry.id === "string"
      && typeof entry.file === "string" && /^[A-Z]{2}$/.test(entry.state_code)
      && Array.isArray(entry.bbox) && entry.bbox.length === 4
      && ["name", "number"].includes(entry.by));
}

export function createIndiaWards({ dir = INDIA_WARDS_DIR, logger = console } = {}) {
  const base = dir instanceof URL ? dir : new URL(`${String(dir).replace(/\/?$/, "/")}`, "file://");
  const note = (event, detail) => logger.error(JSON.stringify({ event, ...detail }));
  let runtime = null;
  const states = new Map();

  // One read per process. A list that is missing or malformed is logged once and every
  // lookup then says "unavailable": the package was built without it.
  function loadRuntime() {
    if (!runtime) {
      const path = new URL(INDIA_WARD_RUNTIME_FILE, base);
      runtime = readFile(path, "utf8").then(JSON.parse).then((value) => {
        if (!validRuntime(value)) throw new Error(`not a ${INDIA_WARD_RUNTIME_FORMAT}`);
        return value;
      }).catch((error) => {
        note("india_ward_runtime_unavailable", {
          path: path.pathname, error_message: String(error?.message || error).slice(0, 300),
        });
        return null;
      });
    }
    return runtime;
  }

  // Every switched-on snapshot of one State, read on the first point that falls in one of
  // their boxes and kept for the life of the process. A file that is missing or is not
  // the bytes the list pinned is logged once and its wards answer "unavailable".
  function loadState(list, stateCode) {
    if (!states.has(stateCode)) {
      states.set(stateCode, Promise.all(list.snapshots
        .filter((entry) => entry.state_code === stateCode)
        .map(async (entry) => {
          const path = new URL(entry.file, base);
          try {
            const bytes = await readFile(path);
            if (entry.sha256 && sha256Hex(bytes) !== entry.sha256) throw new Error("file bytes do not match the runtime list");
            const snapshot = JSON.parse(bytes.toString("utf8"));
            if (snapshot?.format !== INDIA_WARD_SNAPSHOT_FORMAT || !Array.isArray(snapshot.wards)
                || snapshot.coordinate_scale !== list.coordinate_scale) {
              throw new Error(`not a ${INDIA_WARD_SNAPSHOT_FORMAT}`);
            }
            return { ...entry, wards: snapshot.wards };
          } catch (error) {
            note("india_ward_snapshot_unavailable", {
              snapshot: entry.id, path: path.pathname,
              error_message: String(error?.message || error).slice(0, 300),
            });
            return { ...entry, wards: null };
          }
        })));
    }
    return states.get(stateCode);
  }

  return {
    dir: base,
    // The loaded snapshot with this id, or null. Ward tender matching reads its wards.
    async snapshot(id) {
      const list = await loadRuntime();
      const entry = list?.snapshots.find((item) => item.id === id);
      if (!entry) return null;
      const loaded = (await loadState(list, entry.state_code)).find((item) => item.id === id);
      return loaded?.wards ? loaded : null;
    },
    // Where a point is among the switched-on snapshots.
    //   out_of_scope      no snapshot's box holds it (nothing is read from disk)
    //   resolved          in a ward that has a name; resolved_unnamed where it has only a number
    //   between_wards     two wards of one snapshot hold it: the drawings overlap there (up
    //                     to 7.4% of a ward in the committed files) and neither is said
    //   no_ward           inside a snapshot's box and in none of its wards
    //   unavailable       the list, or the file of a snapshot whose box holds it, could not
    //                     be read
    // The list is in order of preference, so where two snapshots of one body both hold the
    // point (Mumbai and Pune each have two) the first is answered and the other is named in
    // `passed_over`.
    async locate(lat, lng) {
      const list = await loadRuntime();
      if (!list) return { status: "unavailable", snapshot: null, ward: null, passed_over: [] };
      const x = lng * list.coordinate_scale;
      const y = lat * list.coordinate_scale;
      const boxed = list.snapshots.filter((entry) => withinBox(x, y, entry.bbox));
      if (!boxed.length) return { status: "out_of_scope", snapshot: null, ward: null, passed_over: [] };
      let answer = null;
      let missing = null;
      let overlapped = null;
      const passedOver = [];
      for (const stateCode of new Set(boxed.map((entry) => entry.state_code))) {
        for (const snapshot of await loadState(list, stateCode)) {
          if (!withinBox(x, y, snapshot.bbox)) continue;
          if (!snapshot.wards) {
            missing = missing || snapshot;
            continue;
          }
          const holders = snapshot.wards.filter((ward) => withinBox(x, y, ward.bbox) && pointInRings(x, y, ward.rings));
          if (holders.length > 1) overlapped = overlapped || snapshot;
          if (holders.length !== 1) continue;
          if (answer) passedOver.push(snapshot.id);
          else answer = { snapshot, ward: holders[0] };
        }
      }
      if (answer) {
        return {
          status: answer.ward.name ? "resolved" : "resolved_unnamed",
          snapshot: answer.snapshot, ward: answer.ward, passed_over: passedOver,
        };
      }
      if (missing) return { status: "unavailable", snapshot: missing, ward: null, passed_over: [] };
      if (overlapped) return { status: "between_wards", snapshot: overlapped, ward: null, passed_over: [] };
      return { status: "no_ward", snapshot: null, ward: null, passed_over: [] };
    },
    // A jurisdiction outside Karnataka with its ward, in the fields a Karnataka ward has,
    // and in `lookup` which snapshot said so and how old it is. Any other jurisdiction is
    // returned as it came. The ward is worked out from the packaged polygons every time
    // and never trusted from a stored answer (geo-cache.mjs keeps a location for a week):
    // a snapshot switched off by a deploy must stop answering with that deploy.
    async place(jurisdiction) {
      if (!jurisdiction || jurisdiction.road_ownership !== "outside_state"
          || !Number.isFinite(jurisdiction.lat) || !Number.isFinite(jurisdiction.lng)) return jurisdiction;
      const found = await this.locate(jurisdiction.lat, jurisdiction.lng);
      const { ward_snapshot: _snapshot, ward_snapshot_dated: _dated, ward_vintage: _vintage,
        ward_snapshot_over: _over, ...lookup } = jurisdiction.lookup || {};
      const snapshot = found.snapshot;
      return {
        ...jurisdiction,
        // The source's own name for the ward (Bhopal's are in Devanagari) and its number
        // in the snapshot's delimitation. ward_numbering says whether tenders use that
        // number: snapshot_current, snapshot_untested, snapshot_wrong or snapshot_none.
        ward_name: found.ward?.name || null,
        ward_no: found.ward?.no || null,
        ward_code: found.ward?.code || null,
        ward_numbering: found.ward ? `snapshot_${snapshot.numbers}` : null,
        lookup: {
          ...lookup,
          ward: found.status,
          ...(snapshot ? {
            ward_snapshot: snapshot.id,
            ward_snapshot_dated: snapshot.source_last_edited || null,
            ward_vintage: snapshot.vintage || null,
          } : {}),
          ...(found.passed_over.length ? { ward_snapshot_over: found.passed_over } : {}),
        },
      };
    },
  };
}
