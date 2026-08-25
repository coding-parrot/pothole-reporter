# -*- coding: utf-8 -*-
"""Karnataka has an exact statewide fallback without weakening KGIS or NH precedence."""

from __future__ import annotations

import hashlib
import json

from playwright.sync_api import sync_playwright

from state_pack_utils import read_pack, read_payload, resource_for, route_pattern


APP = "http://localhost:8765/"
PACK_ID = "in-ka-state-routing"
EXPECTED_GEOMETRY_SHA256 = (
    "9d7fe3f01a80cb41712c09139efcd43e0e11a644849d5f3bffe125cc0bc1c5ad"
)
INSIDE = [
    {"name": "Bengaluru", "lat": 12.9716, "lng": 77.5946},
    {"name": "Mysuru", "lat": 12.2958, "lng": 76.6394},
    {"name": "Mangaluru", "lat": 12.9141, "lng": 74.8560},
    {"name": "Kalaburagi", "lat": 17.3297, "lng": 76.8343},
    # A rural point proves the release is a state polygon, not an urban-body list.
    {"name": "rural Magadi", "lat": 13.0000, "lng": 77.2000},
]
OUTSIDE = [
    {"name": "Panaji", "lat": 15.4909, "lng": 73.8278},
    {"name": "Kasaragod", "lat": 12.4996, "lng": 74.9869},
    {"name": "Hosur", "lat": 12.7409, "lng": 77.8253},
    {"name": "Solapur", "lat": 17.6599, "lng": 75.9064},
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
  const resource = manifest && manifest.resources &&
    manifest.resources["in-ka-state-routing"];
  const legacyResource = manifest && manifest.resources &&
    manifest.resources["in-ka-routing"];
  const coverage = await P.karnatakaStateCoverage();
  const region = coverage && coverage.region;
  const pack = await P.loadStatePack("in-ka-state-routing");

  eq("pack: v1.30 has fifteen independently pinned resources",
     Object.keys(manifest && manifest.resources || {}).length, 15);
  ok("pack: statewide manifest entry exists", resource, manifest);
  eq("pack: statewide adapter is data-only", resource && resource.adapter,
     "statewide-general-v1");
  eq("pack: state code is Karnataka", resource && resource.state_code, "KA");
  eq("pack: full-state scope is explicit", resource && resource.statewide, true);
  ok("pack: content digest is pinned",
     resource && /^[0-9a-f]{64}$/.test(resource.sha256), resource);
  eq("pack: path is content-addressed", resource && resource.path,
     resource && `packs/v1/states/ka/routing-${resource.sha256}.json`);
  eq("pack: exact KGIS pack remains separate", legacyResource && legacyResource.adapter,
     "karnataka-kgis-v1");

  ok("coverage: pinned payload loads", region, coverage);
  eq("coverage: stable region ID", region && region.id, "karnataka-state");
  eq("coverage: stable authority ID", region && region.authority_id,
     "ka-statewide-unverified");
  eq("coverage: relation ID is pinned", region && region.osm_relation_id, 2019939);
  eq("coverage: geometry digest is pinned", region && region.geometry_sha256,
     P.KARNATAKA_STATE_GEOMETRY_SHA256);
  ok("coverage: source attribution is explicit",
     /OpenStreetMap contributors/.test(region && region.source_name || "")
       && /ODbL/.test(region && region.source_license || ""), region);
  ok("coverage: limitations disclaim ownership and automatic submission",
     (region && region.limitations || []).some((item) => /ownership is not inferred/i.test(item))
       && (region && region.limitations || []).some((item) => /does not submit/i.test(item)),
     region && region.limitations);

  eq("registry: statewide expansion is versioned", P.AUTHORITY_REGISTRY_VERSION, 14);
  eq("registry: stable statewide authority is installed",
     P.KARNATAKA_STATE_AUTHORITY.id, "ka-statewide-unverified");
  eq("registry: primary Janaspandana handoff",
     P.KARNATAKA_STATE_AUTHORITY.handoff_url, "https://ipgrs.karnataka.gov.in/");
  eq("registry: state helpline", P.KARNATAKA_STATE_AUTHORITY.helpline, "1902");
  ok("registry: installed official routes pass structural validation",
     P.validateOfficialHandoffRegistry(P.OFFICIAL_AUTHORITIES), null);

  let saved = null;
  for (const fixture of inside) {
    const raw = await P.karnatakaStateRouteFromGeocode(
      null, fixture.lat, fixture.lng, 12);
    ok(`${fixture.name}: coordinates route without a geocoder`, raw && raw.routed, raw);
    eq(`${fixture.name}: neutral Karnataka authority`, raw && raw.authority_id,
       "ka-statewide-unverified");
    eq(`${fixture.name}: exact state-boundary evidence`, raw && [
      raw.routing_source, raw.routing_match_field, raw.routing_match_value, raw.region,
    ], [
      "osm_karnataka_state_boundary", "boundary",
      "Karnataka (OpenStreetMap relation 2019939)", "karnataka-state",
    ]);
    eq(`${fixture.name}: official handoff only`, raw && raw.delivery_channel,
       "official_handoff");
    eq(`${fixture.name}: no guessed recipient email`, raw && raw.officer_email, null);
    eq(`${fixture.name}: no ownership claim`, raw && raw.ownership_unverified, true);
    eq(`${fixture.name}: official reference remains mandatory`,
       raw && raw.requires_official_reference, true);
    eq(`${fixture.name}: no tender inference`, raw && raw.tender_eligible, false);
    eq(`${fixture.name}: current pack provenance`, raw && [
      raw.routing_pack_id, raw.routing_pack_version, raw.routing_pack_sha256,
      raw.routing_pack_state_code,
    ], [
      "in-ka-state-routing", resource && resource.pack_version,
      resource && resource.sha256, "KA",
    ]);
    ok(`${fixture.name}: routing never claims submission`, absent(raw, [
      "official_grievance_id", "submitted_at", "sent_at", "handoff_opened_at",
    ]), raw);
    for (const issue of ["road_damage", "garbage", "open_manhole"]) {
      const typed = P.routeForIssue(raw, issue);
      eq(`${fixture.name}/${issue}: remains routable`, typed && typed.routed, true);
      eq(`${fixture.name}/${issue}: keeps statewide authority`,
         typed && typed.authority_id, "ka-statewide-unverified");
      eq(`${fixture.name}/${issue}: issue is explicit`, typed && typed.issue_type, issue);
      eq(`${fixture.name}/${issue}: never becomes tender-eligible`,
         typed && typed.tender_eligible, false);
    }
    if (fixture.name === "rural Magadi") {
      saved = {...raw, ...fixture, gps_accuracy: 12, issue_type: "road_damage"};
    }
  }

  const staleKarnataka = {
    city: "Bengaluru", state: "Karnataka", country_code: "in",
    full: "stale Karnataka address",
  };
  for (const fixture of outside) {
    const direct = await P.karnatakaStateRouteFromGeocode(
      staleKarnataka, fixture.lat, fixture.lng, 12);
    eq(`${fixture.name}: stale state label cannot cross the polygon`, direct, null);
  }

  const atLimit = await P.karnatakaStateRouteFromGeocode(
    null, 13.0, 77.2, 30);
  eq("accuracy: 30 metres is accepted away from the state edge",
     atLimit && atLimit.authority_id, "ka-statewide-unverified");
  for (const [label, accuracy] of [
    ["31 metres", 31], ["negative", -1], ["not finite", Number.NaN],
  ]) {
    const uncertain = await P.karnatakaStateRouteFromGeocode(
      null, 13.0, 77.2, accuracy);
    eq(`accuracy: ${label} fails closed`,
       uncertain && uncertain.unrouted_reason, "location_uncertain");
  }
  const edgePoint = region.geometry.type === "Polygon"
    ? region.geometry.coordinates[0][0]
    : region.geometry.coordinates[0][0][0];
  const edge = await P.karnatakaStateRouteFromGeocode(
    staleKarnataka, edgePoint[1], edgePoint[0], 5);
  eq("boundary: a five-metre circle touching the state edge fails closed",
     edge && edge.unrouted_reason, "location_uncertain");

  // Exact checked-in NH-44 line vertex in Bengaluru.
  const highway = await P.routeOfficer(
    {state: "Karnataka", country_code: "in"},
    13.00271, 77.58406, 5, null, null, "road_damage");
  eq("precedence: NH-44 beats the Karnataka statewide handoff",
     highway && [highway.authority_id, highway.highway_ref],
     ["in-national-highway", "NH-44"]);

  ok("saved binding: valid current Karnataka record is accepted",
     saved && await P.savedOfficialRouteBinding(
       saved, "in-ka-state-routing", "ka-statewide-unverified", pack), saved);
  const rejected = async (name, changes) => {
    const candidate = {...saved, ...changes};
    const binding = await P.savedOfficialRouteBinding(
      candidate, "in-ka-state-routing", "ka-statewide-unverified", pack);
    eq(name, binding, null);
  };
  await rejected("saved binding: cross-pack ID is rejected",
    {routing_pack_id: "in-ka-routing"});
  await rejected("saved binding: forged digest is rejected",
    {routing_pack_sha256: "0".repeat(64)});
  await rejected("saved binding: cross-region binding is rejected",
    {region: "karnataka"});
  await rejected("saved binding: outside coordinates are rejected",
    {lat: 12.4996, lng: 74.9869});
  await rejected("saved binding: over-30m accuracy is rejected", {gps_accuracy: 31});
  await rejected("saved binding: changed source is rejected",
    {routing_source: "kgis"});

  return checks;
}
"""


def open_page(
    browser,
    *,
    override=None,
    kgis_mode: str | None = None,
    maharashtra_override=None,
):
    context = browser.new_context(viewport={"width": 390, "height": 844})
    if override is not None:
        context.route(route_pattern(PACK_ID), override)
    if maharashtra_override is not None:
        context.route(route_pattern("in-mh-routing"), maharashtra_override)

    if kgis_mode:
        def town(route):
            features = []
            if kgis_mode == "town":
                features = [{"attributes": {
                    "KGISTownName": "Mysuru",
                    "Town_Type": "CC",
                    "KGISTownCode": 0,
                    "LGD_TownCode": 252045,
                }}]
            route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps({"features": features}),
            )

        def highway(route):
            route.fulfill(
                status=200, content_type="application/json",
                body='{"features":[]}',
            )

        def gram_panchayat(route):
            features = ([{"attributes": {"KGISGPName": "Kudur"}}]
                        if kgis_mode == "rural" else [])
            route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps({"features": features}),
            )

        context.route("**/Boundaries/Admin_Dynamic_New/MapServer/1/query*", town)
        context.route("**/State_Basemap/State_Basemap_Dynamic/MapServer/289/query*", highway)
        context.route("**/Boundaries/GP_Boundary/MapServer/0/query*", gram_panchayat)

    page = context.new_page()
    page.goto(APP)
    page.wait_for_load_state("networkidle")
    page.wait_for_function(
        "() => window.StandaloneAPI && StandaloneAPI.__pure "
        "&& typeof StandaloneAPI.__pure.karnatakaStateRouteFromGeocode === 'function'",
        timeout=30_000,
    )
    return context, page


def main() -> None:
    failures: list[str] = []
    raw = b""
    extra_checks = 0
    try:
        pack, raw = read_pack(PACK_ID)
        payload = read_payload(PACK_ID)
        region = payload["region"]
        encoded_geometry = json.dumps(
            region["geometry"], ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        actual_digest = hashlib.sha256(encoded_geometry).hexdigest()
        if pack.get("adapter") != "statewide-general-v1":
            failures.append(f"unexpected Karnataka adapter: {pack.get('adapter')!r}")
        if pack.get("state_code") != "KA" or pack.get("pack_id") != PACK_ID:
            failures.append("Karnataka hosted pack identity is invalid")
        if payload.get("retrieved_at") != "2026-08-25":
            failures.append("Karnataka retrieval date is not pinned")
        if actual_digest != EXPECTED_GEOMETRY_SHA256:
            failures.append(f"Karnataka geometry digest changed: {actual_digest}")
        if region.get("geometry_sha256") != EXPECTED_GEOMETRY_SHA256:
            failures.append("Karnataka payload does not record the pinned geometry digest")
        if hashlib.sha256(raw).hexdigest() != resource_for(PACK_ID).get("sha256"):
            failures.append("Karnataka hosted pack digest changed after validation")
    except (AssertionError, KeyError, OSError, TypeError, ValueError) as error:
        failures.append(f"Karnataka routing pack pin is invalid: {error}")

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

        # The exact KGIS urban-body response must keep the verified email route and its
        # original pack, rather than being replaced by the neutral statewide handoff.
        context, page = open_page(browser, kgis_mode="town")
        exact = page.evaluate("""async () => StandaloneAPI.__pure.routeOfficer(
          {city:'Mysuru',state:'Karnataka',country_code:'in'},
          12.2958,76.6394,12,null,null,'road_damage')""")
        if [exact.get("authority_id"), exact.get("routing_pack_id"), exact.get("delivery_channel")] \
                != ["ka-lgd-252045", "in-ka-routing", "email"]:
            failures.append(f"mocked exact KGIS route lost precedence: {exact!r}")
        extra_checks += 1
        context.close()

        # A KGIS-confirmed rural point has no municipal recipient. Exact state containment
        # may still offer Janaspandana without guessing that the municipality owns the road.
        context, page = open_page(browser, kgis_mode="rural")
        rural = page.evaluate("""async () => StandaloneAPI.__pure.routeOfficer(
          {state:'Karnataka',country_code:'in'},
          13.0,77.2,12,null,null,'road_damage')""")
        if [rural.get("authority_id"), rural.get("routing_pack_id"), rural.get("tender_eligible")] \
                != ["ka-statewide-unverified", "in-ka-state-routing", False]:
            failures.append(f"mocked rural KGIS point did not fall back statewide: {rural!r}")
        extra_checks += 1
        context.close()

        # Maharashtra's download envelope reaches northern Karnataka. Failure of that
        # unrelated pack must not block an independently verified Karnataka polygon.
        context, page = open_page(
            browser,
            kgis_mode="rural",
            maharashtra_override=lambda route: route.fulfill(status=404, body="missing"),
        )
        northern = page.evaluate("""async () => StandaloneAPI.__pure.routeOfficer(
          {city:'Kalaburagi',state:'Karnataka',country_code:'in'},
          17.3297,76.8343,12,null,null,'road_damage')""")
        if [northern.get("authority_id"), northern.get("routing_pack_id")] \
                != ["ka-statewide-unverified", "in-ka-state-routing"]:
            failures.append(
                f"missing Maharashtra pack blocked northern Karnataka: {northern!r}"
            )
        extra_checks += 1
        context.close()

        for label, responder in [
            ("missing", lambda route: route.fulfill(status=404, body="missing")),
            (
                "tampered",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=raw.replace(b"Karnataka", b"Xarnataka", 1),
                ),
            ),
        ]:
            context, page = open_page(browser, override=responder)
            failed = page.evaluate("""async () =>
              StandaloneAPI.__pure.karnatakaStateRouteFromGeocode(
                null,13.0,77.2,12)""")
            if not isinstance(failed, dict) or failed.get("routed") is not False \
                    or failed.get("unrouted_reason") != "jurisdiction_unavailable":
                failures.append(f"{label} Karnataka state pack failed open: {failed!r}")
            extra_checks += 1
            context.close()
        browser.close()

    if failures:
        print("FAIL")
        for failure in failures:
            print("  -", failure)
        raise SystemExit(1)
    print(f"KARNATAKA STATEWIDE ROUTING TEST PASS ({len(results) + extra_checks + 5} checks)")


if __name__ == "__main__":
    main()
