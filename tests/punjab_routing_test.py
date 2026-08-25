# -*- coding: utf-8 -*-
"""Punjab routes by its pinned state polygon and keeps every handoff fail-closed."""

from __future__ import annotations

import hashlib
import json
import sys

from playwright.sync_api import sync_playwright

from state_pack_utils import read_pack, read_payload, route_pattern


APP = "http://localhost:8765/"
PACK_ID = "in-pb-routing"
EXPECTED_GEOMETRY_SHA256 = (
    "e113eb774f4f353d3c7a9c98830f4b665f9bd4d166ed3b84e90855bdf38f5782"
)
INSIDE = [
    {"name": "Amritsar", "lat": 31.6340, "lng": 74.8723},
    {"name": "Ludhiana", "lat": 30.9010, "lng": 75.8573},
    # Deliberately not a city-centre fixture: statewide routing must not collapse to
    # the two Punjab cities that happen to appear in the Census top-50 list.
    {"name": "rural Punjab", "lat": 30.4720, "lng": 75.2010},
]
OUTSIDE = [
    {"name": "Chandigarh", "lat": 30.7333, "lng": 76.7794},
    {"name": "Panchkula", "lat": 30.6942, "lng": 76.8606},
    {"name": "Ambala", "lat": 30.3782, "lng": 76.7767},
]


SCENARIO = r"""
async ({inside, outside}) => {
  const P = StandaloneAPI.__pure;
  const checks = [];
  const eq = (name, got, want) => checks.push([
    name, JSON.stringify(got) === JSON.stringify(want), got, want,
  ]);
  const ok = (name, value, detail) => checks.push([
    name, !!value, detail === undefined ? value : detail, true,
  ]);
  const absent = (value, fields) => fields.every((field) =>
    !Object.prototype.hasOwnProperty.call(value || {}, field));

  const manifest = await P.getStatePackManifest();
  const resource = manifest && manifest.resources && manifest.resources["in-pb-routing"];
  const coverage = await P.punjabCoverage();
  const region = coverage && coverage.region;

  ok("pack: manifest entry exists", resource, manifest);
  eq("pack: adapter is statewide and data-only", resource && resource.adapter,
     "statewide-general-v1");
  eq("pack: state code is Punjab", resource && resource.state_code, "PB");
  eq("pack: full-state scope is explicit", resource && resource.statewide, true);
  ok("coverage: pinned payload loads", region, coverage);
  eq("coverage: stable region ID", region && region.id, "punjab-state");
  eq("coverage: stable authority ID", region && region.authority_id,
     "pb-statewide-unverified");
  eq("coverage: relation ID is pinned", region && region.osm_relation_id, 1942686);
  eq("coverage: geometry digest is pinned", region && region.geometry_sha256,
     P.PUNJAB_STATE_GEOMETRY_SHA256);
  ok("coverage: source attribution is explicit",
     /OpenStreetMap contributors/.test(region && region.source_name || "")
       && /ODbL/.test(region && region.source_license || ""), region);
  ok("coverage: scope excludes Chandigarh",
     /excludes Chandigarh Union Territory/i.test(region && region.scope || ""),
     region && region.scope);
  ok("coverage: limitations disclaim ownership and require user verification",
     (region && region.limitations || []).some((item) => /ownership is not inferred/i.test(item))
       && (region && region.limitations || []).some((item) => /user must select/i.test(item)),
     region && region.limitations);

  eq("registry: release contains statewide Andhra Pradesh routing",
     P.AUTHORITY_REGISTRY_VERSION, 14);
  eq("registry: stable Punjab authority is installed",
     P.PUNJAB_STATE_AUTHORITY.id, "pb-statewide-unverified");
  eq("registry: primary official handoff", P.PUNJAB_STATE_AUTHORITY.handoff_url,
     "https://connect.punjab.gov.in/");
  eq("registry: independent urban alternate",
     P.PUNJAB_STATE_AUTHORITY.alternate_handoff_url,
     "https://mseva.lgpunjab.gov.in/");
  eq("registry: state helpline is retained", P.PUNJAB_STATE_AUTHORITY.helpline, "1100");
  ok("registry: installed official routes pass structural validation",
     P.validateOfficialHandoffRegistry(P.OFFICIAL_AUTHORITIES), null);

  let saved = null;
  for (const fixture of inside) {
    const raw = await P.punjabRouteFromGeocode(null, fixture.lat, fixture.lng, 12);
    ok(`${fixture.name}: coordinates route without a geocoder`, raw && raw.routed, raw);
    eq(`${fixture.name}: neutral Punjab authority`, raw && raw.authority_id,
       "pb-statewide-unverified");
    eq(`${fixture.name}: exact state-boundary evidence`, raw && [
      raw.routing_source, raw.routing_match_field, raw.routing_match_value, raw.region,
    ], [
      "osm_punjab_state_boundary", "boundary",
      "Punjab (OpenStreetMap relation 1942686)", "punjab-state",
    ]);
    eq(`${fixture.name}: official handoff only`, raw && raw.delivery_channel,
       "official_handoff");
    eq(`${fixture.name}: no guessed recipient email`, raw && raw.officer_email, null);
    eq(`${fixture.name}: no ownership claim`, raw && raw.ownership_unverified, true);
    eq(`${fixture.name}: official reference remains mandatory`,
       raw && raw.requires_official_reference, true);
    eq(`${fixture.name}: no tender inference`, raw && raw.tender_eligible, false);
    eq(`${fixture.name}: full current pack provenance`, raw && [
      raw.routing_pack_id, raw.routing_pack_version, raw.routing_pack_sha256,
      raw.routing_pack_state_code,
    ], [
      "in-pb-routing", resource && resource.pack_version,
      resource && resource.sha256, "PB",
    ]);
    ok(`${fixture.name}: routing never claims submission`, absent(raw, [
      "official_grievance_id", "submitted_at", "sent_at", "handoff_opened_at",
    ]), raw);
    for (const issue of ["road_damage", "garbage", "open_manhole"]) {
      const typed = P.routeForIssue(raw, issue);
      eq(`${fixture.name}/${issue}: remains routable`, typed && typed.routed, true);
      eq(`${fixture.name}/${issue}: keeps Punjab authority`, typed && typed.authority_id,
         "pb-statewide-unverified");
      eq(`${fixture.name}/${issue}: issue is explicit`, typed && typed.issue_type, issue);
      eq(`${fixture.name}/${issue}: never becomes tender-eligible`,
         typed && typed.tender_eligible, false);
    }
    if (fixture.name === "Amritsar") saved = {...raw, ...fixture, gps_accuracy: 12,
      issue_type: "road_damage"};
  }

  const stalePunjab = {
    city: "Amritsar", state: "Punjab", country_code: "in",
    full: "stale Punjab address",
  };
  for (const fixture of outside) {
    const direct = await P.punjabRouteFromGeocode(
      stalePunjab, fixture.lat, fixture.lng, 12);
    eq(`${fixture.name}: stale Punjab label cannot cross the polygon`, direct, null);
    const orchestrated = await P.routeOfficer(
      stalePunjab, fixture.lat, fixture.lng, 12, null, null, "garbage");
    ok(`${fixture.name}: routeOfficer never assigns Punjab`,
       !orchestrated || orchestrated.authority_id !== "pb-statewide-unverified",
       orchestrated);
    eq(`${fixture.name}: outside-area result is explicit`,
       orchestrated && orchestrated.unrouted_reason, "outside_area");
  }

  const atLimit = await P.punjabRouteFromGeocode(null, 31.6340, 74.8723, 30);
  eq("accuracy: 30 metres is accepted away from the state edge",
     atLimit && atLimit.authority_id, "pb-statewide-unverified");
  for (const [label, accuracy] of [
    ["31 metres", 31], ["negative", -1], ["not finite", Number.NaN],
  ]) {
    const uncertain = await P.punjabRouteFromGeocode(
      null, 31.6340, 74.8723, accuracy);
    eq(`accuracy: ${label} fails closed`,
       uncertain && uncertain.unrouted_reason, "location_uncertain");
  }
  const edgePoint = region.geometry.type === "Polygon"
    ? region.geometry.coordinates[0][0]
    : region.geometry.coordinates[0][0][0];
  const edge = await P.punjabRouteFromGeocode(
    stalePunjab, edgePoint[1], edgePoint[0], 5);
  eq("boundary: a five-metre circle touching the state edge fails closed",
     edge && edge.unrouted_reason, "location_uncertain");

  // This checked-in highway coordinate lies inside Punjab. Road damage must route to
  // the national service before the state-neutral fallback; civic categories do not.
  const highway = await P.routeOfficer(
    {city: "Pathankot", state: "Punjab", country_code: "in"},
    31.994375, 75.612435, 5, null, null, "road_damage");
  eq("precedence: NH-44 beats the Punjab state handoff",
     highway && highway.authority_id, "in-national-highway");
  eq("precedence: mapped reference is retained", highway && highway.highway_ref, "NH-44");

  const pack = await P.loadStatePack("in-pb-routing");
  ok("saved binding: valid current Punjab record is accepted",
     saved && await P.savedOfficialRouteBinding(
       saved, "in-pb-routing", "pb-statewide-unverified", pack), saved);
  const rejected = async (name, changes) => {
    const candidate = {...saved, ...changes};
    const binding = await P.savedOfficialRouteBinding(
      candidate, "in-pb-routing", "pb-statewide-unverified", pack);
    eq(name, binding, null);
  };
  await rejected("saved binding: cross-pack ID is rejected",
    {routing_pack_id: "in-top50-routing"});
  await rejected("saved binding: cross-state provenance is rejected",
    {routing_pack_state_code: "IN"});
  await rejected("saved binding: forged digest is rejected",
    {routing_pack_sha256: "0".repeat(64)});
  await rejected("saved binding: cross-region binding is rejected", {region: "punjab"});
  await rejected("saved binding: outside coordinates are rejected",
    {lat: 30.7333, lng: 76.7794});
  await rejected("saved binding: over-30m accuracy is rejected", {gps_accuracy: 31});
  await rejected("saved binding: changed boundary evidence is rejected",
    {routing_match_value: "Punjab (OpenStreetMap relation 1)"});

  return checks;
}
"""


def open_page(browser, override=None):
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    if override is not None:
        page.route(route_pattern(PACK_ID), override)
    page.goto(APP)
    page.wait_for_load_state("networkidle")
    page.wait_for_function(
        "() => window.StandaloneAPI && StandaloneAPI.__pure "
        "&& typeof StandaloneAPI.__pure.punjabRouteFromGeocode === 'function'",
        timeout=30_000,
    )
    return context, page


def main() -> None:
    failures: list[str] = []
    raw = b""
    try:
        pack, raw = read_pack(PACK_ID)
        payload = read_payload(PACK_ID)
        region = payload["region"]
        encoded_geometry = json.dumps(
            region["geometry"], ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        actual_digest = hashlib.sha256(encoded_geometry).hexdigest()
        if pack.get("adapter") != "statewide-general-v1":
            failures.append(f"unexpected Punjab adapter: {pack.get('adapter')!r}")
        if payload.get("retrieved_at") != "2026-08-24":
            failures.append("Punjab retrieval date is not pinned")
        if actual_digest != EXPECTED_GEOMETRY_SHA256:
            failures.append(f"Punjab geometry digest changed: {actual_digest}")
        if region.get("geometry_sha256") != EXPECTED_GEOMETRY_SHA256:
            failures.append("Punjab payload does not record the pinned geometry digest")
    except (AssertionError, KeyError, OSError, TypeError, ValueError) as error:
        failures.append(f"Punjab routing pack pin is invalid: {error}")

    if not raw:
        print("FAIL")
        for failure in failures:
            print("  -", failure)
        raise SystemExit(1)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--disable-web-security"])
        context, page = open_page(browser)
        results = page.evaluate(SCENARIO, {"inside": INSIDE, "outside": OUTSIDE})
        context.close()
        for name, passed, got, want in results:
            if not passed:
                failures.append(name)
                print(f"  FAIL {name}\n         got  {got!r}\n         want {want!r}")

        for label, responder in [
            ("missing", lambda route: route.fulfill(status=404, body="missing")),
            (
                "tampered",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=raw.replace(b"Punjab", b"Punxab", 1),
                ),
            ),
        ]:
            context, page = open_page(browser, responder)
            result = page.evaluate(
                """async () => StandaloneAPI.__pure.punjabRouteFromGeocode(
                  null,30.4720,75.2010,12)"""
            )
            if result.get("routed") is not False or result.get("unrouted_reason") != "jurisdiction_unavailable":
                failures.append(f"{label} Punjab pack did not fail closed: {result!r}")
            context.close()
        browser.close()

    if failures:
        print("FAIL")
        for failure in failures:
            print("  -", failure)
        raise SystemExit(1)
    print(f"PUNJAB ROUTING TEST PASS ({len(results) + 2} checks)")


if __name__ == "__main__":
    main()
