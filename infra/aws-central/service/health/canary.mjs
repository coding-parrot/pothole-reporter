// The canary: the live API exercised the way a phone uses it, through its public URL.
//
// Two depths. "reads" asks health, the map and the impact figures and nothing else: no
// install, no signature, no model call. "full" then registers an install, sends one real
// detection of the example photograph (one model call, USD 0.0005) and three signed
// tender lookups. It never reports a pothole, so the public map is not touched.
//
// Everything it needs is passed in: `fetch`, the clock, the API's URL, `identity` (the
// install's P-256 key pair, a new one or a kept one) and `readImage` (the photograph's
// bytes). Returns the install id a full canary ran as.

import { createHash, randomUUID, sign } from "node:crypto";

import { INDIA_WARD_CANARY, judgeIndiaWardCanary } from "./rules.mjs";

// A real Bengaluru street inside GBA Central, used by 49 real reports. The hint is what
// the phone would send; the second lookup sends none to prove the server's own geocoder.
const CANARY_POINT = { lat: 12.99657, lng: 77.62034, hint: "Cambridge Road, Halasuru, Bengaluru" };

const sha = (value) => createHash("sha256").update(value).digest("hex");

export async function runCanary({ apiUrl, fetch, identity, readImage, report, depth = "full", now = Date.now,
  expectedOpenNotices = async () => null }) {
  const { ok, fail } = report;
  report.begin("canary", `Canary against ${apiUrl}`);

  async function timed(label, promise, limitMs) {
    const started = now();
    const result = await promise;
    const took = now() - started;
    if (took > limitMs) fail(`${label} within ${limitMs} ms`, `${took} ms`);
    return { ...result, took };
  }

  async function publicGet(route) {
    const response = await fetch(apiUrl + route, { signal: AbortSignal.timeout(20_000) });
    const text = await response.text();
    let body = null;
    try { body = JSON.parse(text); } catch { body = null; }
    return { status: response.status, body };
  }

  const health = await timed("health", publicGet("/v1/health"), 5000);
  health.status === 200 && health.body?.ok === true && health.body?.shared_vision_primary_configured === true
    ? ok("health", `200, ${health.body.shared_vision_provider}`)
    : fail("health", `${health.status} ${JSON.stringify(health.body).slice(0, 200)}`);
  for (const route of ["/v1/map", "/v1/impact"]) {
    const result = await timed(route, publicGet(route), 10_000);
    result.status === 200 ? ok(route, `200 in ${result.took} ms`) : fail(route, `${result.status}`);
  }
  // The map is the largest thing the app downloads from the service. It went over the
  // air uncompressed (52.8 KB) until 6 Oct 2026; the wire size is asked for raw here,
  // because fetch would quietly decompress and hide a regression.
  const wire = await fetch(`${apiUrl}/v1/map`, { headers: { "accept-encoding": "gzip" },
    signal: AbortSignal.timeout(20_000) });
  const encoding = wire.headers.get("content-encoding");
  const plainBytes = Buffer.byteLength(await wire.text());
  const sentBytes = Number(wire.headers.get("content-length")) || null;
  plainBytes < 1024 || encoding === "gzip"
    ? ok("map is compressed", `${plainBytes} bytes of JSON${sentBytes ? ` sent as ${sentBytes}` : ""}, ${encoding || "small enough to send plain"}`)
    : fail("map is compressed", `${plainBytes} bytes sent with content-encoding ${encoding}`);
  if (depth === "reads") return {};

  // Registering a key the service already holds changes nothing there and answers the
  // same install id (it is the hash of the public key), so a kept key is one install for
  // as long as it is kept.
  const { publicKey, privateKey } = await identity();
  const registration = await fetch(`${apiUrl}/v1/installations`, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ public_key: publicKey.export({ type: "spki", format: "der" }).toString("base64") }),
    signal: AbortSignal.timeout(20_000),
  });
  const install = await registration.json();
  if (registration.status !== 201 || !install.install_id) {
    fail("install registers", `${registration.status} ${JSON.stringify(install).slice(0, 200)}`);
    return {};
  }
  ok("install registers", install.install_id.slice(0, 8));

  async function signedPost(route, payload, timeoutMs = 30_000) {
    const body = JSON.stringify(payload);
    const timestamp = String(now());
    const idempotencyKey = randomUUID();
    const canonical = ["POST", route, timestamp, idempotencyKey, sha(body)].join("\n");
    const signature = sign("sha256", Buffer.from(canonical), privateKey).toString("base64");
    const started = now();
    const response = await fetch(apiUrl + route, {
      method: "POST", body, signal: AbortSignal.timeout(timeoutMs),
      headers: { "content-type": "application/json", "x-install-id": install.install_id, "x-timestamp": timestamp,
        "x-signature": signature, "idempotency-key": idempotencyKey },
    });
    const text = await response.text();
    let parsed = null;
    try { parsed = JSON.parse(text); } catch { parsed = { raw: text.slice(0, 200) }; }
    return { status: response.status, body: parsed, took: now() - started };
  }

  const image = readImage();
  const detect = await signedPost("/v1/vision/detect", {
    prompt_version: "road-damage-v5", capture_mode: "manual",
    images: [`data:image/jpeg;base64,${image.toString("base64")}`],
  });
  if (detect.status === 200 && detect.body.assessment === "damaged") {
    ok("shared detection finds the example pothole", `${detect.body.damage_type} ${detect.body.size} via ${detect.body.detector?.backend_provider} in ${detect.took} ms`);
    if (detect.took > 6000) fail("detection within 6000 ms", `${detect.took} ms`);
  } else {
    fail("shared detection finds the example pothole", `${detect.status} ${JSON.stringify(detect.body).slice(0, 300)}`);
  }

  const withHint = await signedPost("/v1/tenders/resolve", { ...CANARY_POINT, address_hint: CANARY_POINT.hint });
  const jurisdiction = withHint.body?.jurisdiction;
  if (withHint.status === 200 && jurisdiction?.road_ownership === "municipal" && jurisdiction?.lgd) {
    ok("Bengaluru street is classified municipal", `LGD ${jurisdiction.lgd} ${jurisdiction.town} via ${{ available: "the live state GIS", snapshot: "the packaged state GIS layers" }[jurisdiction.lookup?.kgis] || "the outage fallback"}; tender ${withHint.body.tender ? withHint.body.tender.tender_number : `none (${withHint.body.reason})`} in ${withHint.took} ms`);
    if (withHint.body.reason === "address_unresolved") fail("hinted address is used for matching", "address_unresolved with a hint present");
    if (withHint.body.reason === "no_tenders_for_jurisdiction") fail("tender table has rows for Bengaluru", "no_tenders_for_jurisdiction; seed the table");
    // The road class is read from the packaged layers; a request that waits on the state
    // GIS again is the 20 s stall coming back.
    jurisdiction.lookup?.kgis === "snapshot" && jurisdiction.lookup?.local === "municipal_polygon"
      ? ok("road class needs no state GIS call", `lookup.local ${jurisdiction.lookup.local}`)
      : fail("road class needs no state GIS call", `lookup.kgis ${jurisdiction.lookup?.kgis}, lookup.local ${jurisdiction.lookup?.local}`);
    // The canary point is in KGIS ward 10, Cox Town, whose tenders the index names by the
    // old BBMP ward 108. A service from before the ward release answers no lookup.ward at
    // all and is not judged here.
    if (jurisdiction.lookup?.ward !== undefined) {
      jurisdiction.lookup.ward === "resolved" && jurisdiction.ward_name === "Cox Town"
        ? ok("ward is named from the packaged snapshot", `${jurisdiction.ward_name}, KGIS ward ${jurisdiction.ward_no}`)
        : fail("ward is named from the packaged snapshot", `lookup.ward ${jurisdiction.lookup.ward}, ward_name ${jurisdiction.ward_name}`);
      Array.isArray(withHint.body.ward_tenders) && withHint.body.ward_tenders.length
        ? ok("the ward's tenders are answered", `${withHint.body.ward_tenders.length}, first: ${withHint.body.ward_tenders[0].title.slice(0, 80)}`)
        : fail("the ward's tenders are answered", `ward_tenders ${JSON.stringify(withHint.body.ward_tenders)?.slice(0, 120)}`);
    }
  } else {
    fail("Bengaluru street is classified municipal", `${withHint.status} ${JSON.stringify(withHint.body).slice(0, 300)}`);
  }

  // One real point outside Karnataka, 760 m inside Shahibag ward of Ahmedabad: the ward
  // must come back by name from the packaged snapshot, and every ward tender must say
  // that ward in its title. Notices close every week, so an empty list is judged only
  // where the caller knows which catalogue is deployed (deploy.sh does, and passes how
  // many open notices it holds for the ward); otherwise it is printed as skipped, with
  // the reason.
  const ahmedabad = await signedPost("/v1/tenders/resolve",
    { lat: INDIA_WARD_CANARY.lat, lng: INDIA_WARD_CANARY.lng, address_hint: INDIA_WARD_CANARY.hint });
  const expectedOpen = await expectedOpenNotices();
  const wardCanary = judgeIndiaWardCanary({ status: ahmedabad.status, body: ahmedabad.body, expectedOpen });
  if (wardCanary.state === "fail") fail("Ahmedabad point is answered with its ward by name", wardCanary.detail);
  else if (wardCanary.state === "skip") report.skip("Ahmedabad point is answered with its ward by name", wardCanary.detail);
  else ok("Ahmedabad point is answered with its ward by name", `${wardCanary.detail} in ${ahmedabad.took} ms`);

  const withoutHint = await signedPost("/v1/tenders/resolve", { lat: CANARY_POINT.lat + 0.0006, lng: CANARY_POINT.lng + 0.0006 });
  const source = withoutHint.body?.jurisdiction?.address_source;
  // Bengaluru is in the packaged street index, so the name must come from it. The public
  // geocoder answering here means data/streets is missing from the package, and every
  // first lookup of a place is waiting 200 to 1,050 ms on an outside call again.
  withoutHint.status === 200 && source === "packaged_streets"
    ? ok("server finds the street itself, with no geocoder call", `${withoutHint.body.jurisdiction.address} in ${withoutHint.took} ms`)
    : fail("server finds the street itself, with no geocoder call", `${withoutHint.status} address_source ${source}, lookup.streets ${withoutHint.body?.jurisdiction?.lookup?.streets}; reason ${withoutHint.body?.reason || withoutHint.body?.error}`);
  return { installId: install.install_id };
}
