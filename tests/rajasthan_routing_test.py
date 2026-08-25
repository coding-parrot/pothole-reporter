# -*- coding: utf-8 -*-
"""Rajasthan uses exact statewide containment and preserves highway priority."""

from __future__ import annotations

import hashlib
import json

from playwright.sync_api import sync_playwright

from state_pack_utils import read_pack, read_payload, resource_for, route_pattern


APP = "http://localhost:8765/"
PACK_ID = "in-rj-routing"
EXPECTED_GEOMETRY_SHA256 = (
    "dcde670675d0fc50e292c6b306b1f80d9d68a1323250c29d6eddc97992491a36"
)
INSIDE = [
    {"name": "Jaipur", "lat": 26.9154576, "lng": 75.8189817},
    {"name": "Jodhpur", "lat": 26.2967719, "lng": 73.0351433},
    {"name": "Kota", "lat": 25.2138, "lng": 75.8648},
    {"name": "Udaipur", "lat": 24.5854, "lng": 73.7125},
    {"name": "Bikaner", "lat": 28.0229, "lng": 73.3119},
    {"name": "Jaisalmer", "lat": 26.9157, "lng": 70.9083},
    {"name": "Bharatpur", "lat": 27.2152, "lng": 77.5030},
    {"name": "Banswara", "lat": 23.5461, "lng": 74.4349},
]
OUTSIDE = [
    {"name": "Ahmedabad", "lat": 23.0225, "lng": 72.5714},
    {"name": "Gwalior", "lat": 26.2037247, "lng": 78.1573628},
    {"name": "Agra", "lat": 27.1752554, "lng": 78.0098161},
    {"name": "Hisar", "lat": 29.1492, "lng": 75.7217},
    {"name": "Fazilka", "lat": 30.4036, "lng": 74.0280},
    {"name": "Bahawalpur", "lat": 29.3956, "lng": 71.6836},
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
  const resource = manifest && manifest.resources && manifest.resources["in-rj-routing"];
  const coverage = await P.rajasthanCoverage();
  const region = coverage && coverage.region;
  const pack = await P.loadStatePack("in-rj-routing");
  const legacyCities = await P.majorCityCoverage();

  eq("pack: v1.33 has eighteen independently pinned resources",
     Object.keys(manifest && manifest.resources || {}).length, 18);
  eq("pack: adapter is data-only", resource && resource.adapter, "statewide-general-v1");
  eq("pack: state code", resource && resource.state_code, "RJ");
  eq("pack: statewide", resource && resource.statewide, true);
  eq("pack: content-addressed path", resource && resource.path,
     resource && `packs/v1/states/rj/routing-${resource.sha256}.json`);
  eq("pack: immutable city compatibility inventory remains",
     legacyCities && legacyCities.regions && legacyCities.regions.length, 35);

  eq("coverage: stable region", region && region.id, "rajasthan-state");
  eq("coverage: stable authority", region && region.authority_id,
     "rj-statewide-unverified");
  eq("coverage: relation", region && region.osm_relation_id, 1942920);
  eq("coverage: geometry digest", region && region.geometry_sha256,
     P.RAJASTHAN_STATE_GEOMETRY_SHA256);
  ok("coverage: ownership and submission limitations are explicit",
     (region && region.limitations || []).some((item) => /ownership is not inferred/i.test(item))
       && (region && region.limitations || []).some((item) => /does not submit/i.test(item)),
     region && region.limitations);

  eq("registry: expansion is versioned", P.AUTHORITY_REGISTRY_VERSION, 16);
  eq("registry: state authority", P.RAJASTHAN_STATE_AUTHORITY.id,
     "rj-statewide-unverified");
  eq("registry: direct Sampark handoff", P.RAJASTHAN_STATE_AUTHORITY.handoff_url,
     "https://sampark.rajasthan.gov.in/grievanceForm");
  eq("registry: verified app package", P.RAJASTHAN_STATE_AUTHORITY.handoff_package,
     "com.rajsampark.versiontwo");
  eq("registry: helpline", P.RAJASTHAN_STATE_AUTHORITY.helpline, "181");
  ok("registry: installed contacts validate",
     P.validateOfficialHandoffRegistry(P.OFFICIAL_AUTHORITIES), null);

  let saved = null;
  for (const fixture of inside) {
    const raw = await P.rajasthanRouteFromGeocode(
      null, fixture.lat, fixture.lng, 12);
    ok(`${fixture.name}: coordinates route without geocoder`, raw && raw.routed, raw);
    eq(`${fixture.name}: exact state evidence`, raw && [
      raw.authority_id, raw.routing_source, raw.routing_match_field,
      raw.routing_match_value, raw.region,
    ], [
      "rj-statewide-unverified", "osm_rajasthan_state_boundary", "boundary",
      "Rajasthan (OpenStreetMap relation 1942920)", "rajasthan-state",
    ]);
    eq(`${fixture.name}: official handoff`, raw && raw.delivery_channel,
       "official_handoff");
    eq(`${fixture.name}: no guessed email`, raw && raw.officer_email, null);
    eq(`${fixture.name}: ownership unverified`, raw && raw.ownership_unverified, true);
    eq(`${fixture.name}: official reference required`,
       raw && raw.requires_official_reference, true);
    eq(`${fixture.name}: no tender inference`, raw && raw.tender_eligible, false);
    eq(`${fixture.name}: pack provenance`, raw && [
      raw.routing_pack_id, raw.routing_pack_version, raw.routing_pack_sha256,
      raw.routing_pack_state_code,
    ], ["in-rj-routing", resource.pack_version, resource.sha256, "RJ"]);
    ok(`${fixture.name}: never claims submission`, absent(raw, [
      "official_grievance_id", "submitted_at", "sent_at", "handoff_opened_at",
    ]), raw);
    for (const issue of ["road_damage", "garbage", "open_manhole"]) {
      const typed = P.routeForIssue(raw, issue);
      eq(`${fixture.name}/${issue}: remains statewide`, typed && [
        typed.routed, typed.authority_id, typed.issue_type, typed.tender_eligible,
      ], [true, "rj-statewide-unverified", issue, false]);
    }
    if (fixture.name === "Kota") {
      saved = {...raw, ...fixture, gps_accuracy: 12, issue_type: "road_damage"};
    }
  }

  const stale = {city:"Jaipur",state:"Rajasthan",country_code:"in"};
  for (const fixture of outside) {
    const route = await P.rajasthanRouteFromGeocode(
      stale, fixture.lat, fixture.lng, 12);
    eq(`${fixture.name}: stale Rajasthan label cannot cross polygon`, route, null);
  }

  eq("accuracy: 30m is accepted", (await P.rajasthanRouteFromGeocode(
    null, 25.2138, 75.8648, 30)).authority_id, "rj-statewide-unverified");
  for (const [label, accuracy] of [["31m",31],["negative",-1],["NaN",Number.NaN]]) {
    const route = await P.rajasthanRouteFromGeocode(null,25.2138,75.8648,accuracy);
    eq(`accuracy: ${label} fails closed`, route && route.unrouted_reason,
       "location_uncertain");
  }
  const vertex = region.geometry.type === "Polygon"
    ? region.geometry.coordinates[0][0] : region.geometry.coordinates[0][0][0];
  eq("boundary: accuracy circle touching edge fails closed",
     (await P.rajasthanRouteFromGeocode(null,vertex[1],vertex[0],5)).unrouted_reason,
     "location_uncertain");

  const highway = await P.routeOfficer(
    {state:"Rajasthan",country_code:"in"},26.92336,75.84563,5,null,null,"road_damage");
  eq("precedence: NH-248 beats statewide route",
     highway && [highway.authority_id,highway.highway_ref],
     ["in-national-highway","NH-248"]);
  const civic = await P.routeOfficer(
    {state:"Rajasthan",country_code:"in"},26.92336,75.84563,5,null,null,"garbage");
  eq("precedence: civic issue beside NH stays with Rajasthan",
     civic && [civic.authority_id,civic.routing_pack_id],
     ["rj-statewide-unverified","in-rj-routing"]);

  for (const [city,lat,lng] of [
    ["Jaipur",26.9154576,75.8189817],["Jodhpur",26.2967719,73.0351433],
  ]) {
    const route = await P.routeOfficer(
      {city,state:"Rajasthan",country_code:"in"},lat,lng,12,null,null,"garbage");
    eq(`supersession: ${city} uses exact statewide containment`, route && [
      route.authority_id,route.routing_pack_id,route.region,
    ], ["rj-statewide-unverified","in-rj-routing","rajasthan-state"]);
  }

  ok("saved binding: current Rajasthan record is accepted",
     saved && await P.savedOfficialRouteBinding(
       saved,"in-rj-routing","rj-statewide-unverified",pack), saved);
  for (const [name, changes] of [
    ["cross pack",{routing_pack_id:"in-top50-routing"}],
    ["forged digest",{routing_pack_sha256:"0".repeat(64)}],
    ["wrong state",{routing_pack_state_code:"GJ"}],
    ["wrong region",{region:"kota"}],
    ["outside coordinates",{lat:23.0225,lng:72.5714}],
    ["poor accuracy",{gps_accuracy:31}],
    ["changed source",{routing_source:"nominatim_structured_city"}],
  ]) {
    eq(`saved binding: rejects ${name}`, await P.savedOfficialRouteBinding(
      {...saved,...changes},"in-rj-routing","rj-statewide-unverified",pack), null);
  }
  return checks;
}
"""


def open_page(browser, override=None):
    context = browser.new_context(viewport={"width": 390, "height": 844})
    if override is not None:
        context.route(route_pattern(PACK_ID), override)
    page = context.new_page()
    page.goto(APP)
    page.wait_for_load_state("networkidle")
    page.wait_for_function(
        "() => window.StandaloneAPI && StandaloneAPI.__pure "
        "&& typeof StandaloneAPI.__pure.rajasthanRouteFromGeocode === 'function'",
        timeout=30_000,
    )
    return context, page


def main() -> None:
    failures: list[str] = []
    pack, raw = read_pack(PACK_ID)
    payload = read_payload(PACK_ID)
    region = payload["region"]
    encoded_geometry = json.dumps(
        region["geometry"], ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    if pack.get("adapter") != "statewide-general-v1":
        failures.append("Rajasthan adapter is not statewide-general-v1")
    if pack.get("state_code") != "RJ" or pack.get("pack_id") != PACK_ID:
        failures.append("Rajasthan hosted pack identity is invalid")
    if payload.get("retrieved_at") != "2026-08-25":
        failures.append("Rajasthan retrieval date is not pinned")
    if hashlib.sha256(encoded_geometry).hexdigest() != EXPECTED_GEOMETRY_SHA256:
        failures.append("Rajasthan geometry digest changed")
    if region.get("geometry_sha256") != EXPECTED_GEOMETRY_SHA256:
        failures.append("Rajasthan payload geometry pin differs")
    if hashlib.sha256(raw).hexdigest() != resource_for(PACK_ID).get("sha256"):
        failures.append("Rajasthan hosted pack digest differs")

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
            ("tampered", lambda route: route.fulfill(
                status=200, content_type="application/json",
                body=raw.replace(b"Rajasthan", b"Xajasthan", 1),
            )),
        ]:
            context, page = open_page(browser, responder)
            values = page.evaluate("""async () => {
              const P = StandaloneAPI.__pure;
              return {
                direct: await P.rajasthanRouteFromGeocode(null,26.9154576,75.8189817,12),
                jaipur: await P.routeOfficer(
                  {city:'Jaipur',state:'Rajasthan',country_code:'in'},
                  26.9154576,75.8189817,12,null,null,'garbage'),
                gwalior: await P.routeOfficer(
                  {city:'Gwalior',state:'Madhya Pradesh',country_code:'in'},
                  26.2037247,78.1573628,12,null,null,'garbage'),
              };
            }""")
            for key in ("direct", "jaipur"):
                route = values[key]
                if route.get("routed") is not False \
                        or route.get("unrouted_reason") != "jurisdiction_unavailable":
                    failures.append(f"{label} Rajasthan pack allowed {key} fallback: {route!r}")
            gwalior = values["gwalior"]
            if [gwalior.get("authority_id"), gwalior.get("routing_pack_id")] \
                    != ["in-mp-cm-helpline", "in-top50-routing"]:
                failures.append(f"{label} Rajasthan pack blocked Gwalior: {gwalior!r}")
            context.close()

        # Punjab's coarse pack-loading envelope overlaps northern Rajasthan. A failed
        # Punjab download must not pre-empt Rajasthan's independently verified polygon.
        context = browser.new_context(viewport={"width": 390, "height": 844})
        context.route(
            route_pattern("in-pb-routing"),
            lambda route: route.fulfill(status=404, body="missing"),
        )
        page = context.new_page()
        page.goto(APP)
        page.wait_for_load_state("networkidle")
        page.wait_for_function(
            "() => window.StandaloneAPI && StandaloneAPI.__pure "
            "&& typeof StandaloneAPI.__pure.routeOfficer === 'function'",
            timeout=30_000,
        )
        hanumangarh = page.evaluate("""async () => StandaloneAPI.__pure.routeOfficer(
          {city:'Hanumangarh',state:'Rajasthan',country_code:'in'},
          29.5815,74.3294,12,null,null,'garbage')""")
        if [hanumangarh.get("authority_id"), hanumangarh.get("routing_pack_id")] \
                != ["rj-statewide-unverified", "in-rj-routing"]:
            failures.append(
                f"missing overlapping Punjab pack blocked Hanumangarh: {hanumangarh!r}"
            )
        context.close()
        browser.close()

    if failures:
        print("RAJASTHAN ROUTING TEST FAIL")
        for failure in failures:
            print("  -", failure)
        raise SystemExit(1)
    print(f"RAJASTHAN ROUTING TEST PASS ({len(results) + 12} checks)")


if __name__ == "__main__":
    main()
