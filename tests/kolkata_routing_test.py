# -*- coding: utf-8 -*-
"""KMC routing must use the official West Bengal polygon and fail closed."""
import json
import hashlib
import sys

from playwright.sync_api import sync_playwright
from state_pack_utils import read_pack, read_payload, route_pattern


APP = "http://localhost:8765/"

SCENARIO = r"""
async () => {
  const P = StandaloneAPI.__pure;
  const checks = [];
  const eq = (name, got, want) => checks.push([
    name, JSON.stringify(got) === JSON.stringify(want), got, want,
  ]);
  const ok = (name, value, detail) => checks.push([
    name, !!value, detail === undefined ? value : detail, true,
  ]);
  const wb = (overrides = {}) => ({
    city: "Kolkata", state: "West Bengal", country_code: "in",
    full: "Kolkata, West Bengal, India", ...overrides,
  });

  // The authority registry is populated only by a checksum-verified state pack.
  const coverage = await P.kolkataCoverage();

  eq("registry: version includes National Highway routing", P.AUTHORITY_REGISTRY_VERSION, 6);
  eq("registry: KMC uses a stable ID", P.KMC_AUTHORITY.id, "wb-kmc");
  ok("registry: KMC validates", P.validateAuthorityRegistry([P.KMC_AUTHORITY]), null);
  eq("registry: primary channel is KMC Grievance 2.0",
     P.KMC_AUTHORITY.handoff_name, "KMC Grievance 2.0");
  ok("registry: primary handoff is HTTPS and official",
     /^https:\/\/kmc\.wb\.gov\.in\//.test(P.KMC_AUTHORITY.handoff_url),
     P.KMC_AUTHORITY.handoff_url);
  eq("registry: installed official KMC app is launchable",
     P.KMC_AUTHORITY.handoff_package, "com.kmc.app");
  eq("registry: verified WhatsApp channel is retained",
     P.KMC_AUTHORITY.whatsapp_url, "https://wa.me/918335988888");

  ok("coverage: official asset loads", coverage && coverage.region, coverage);
  eq("coverage: ULB code is pinned", coverage.region.ulb_code, "250299");
  eq("coverage: municipal ID is pinned", coverage.region.mun_id, "250299_0000001");
  eq("coverage: authority ID agrees with registry", coverage.region.authority_id, "wb-kmc");
  ok("coverage: source names West Bengal UD&MA",
     /Urban Development.*Municipal Affairs.*West Bengal/i.test(coverage.region.source_name),
     coverage.region.source_name);
  ok("coverage: source URL is the official municipal WFS query",
     /^https:\/\/nagargispariseva\.wb\.gov\.in\//.test(coverage.region.source_url)
       && /wb_municipal_boundary/.test(coverage.region.source_url)
       && /250299/.test(coverage.region.source_filter),
     coverage.region.source_url);
  ok("coverage: no open-data licence is invented",
     /no explicit reuse licence/i.test(coverage.region.source_access),
     coverage.region.source_access);

  const inside = [
    ["central", 22.5726, 88.3639],
    ["Shyambazar north", 22.6011, 88.3730],
    ["Science City east", 22.5390, 88.3958],
    ["Joka southern addition", 22.4550, 88.3000],
  ];
  for (const [name, lat, lng] of inside) {
    const got = await P.kolkataRouteFromGeocode(null, lat, lng, 12);
    eq(`inside: ${name} routes to KMC`, got && got.authority_id, "wb-kmc");
    eq(`inside: ${name} records Kolkata region`, got && got.region, "kolkata");
    eq(`inside: ${name} uses official GIS containment`,
       got && got.routing_source, "wb_udma_official_gis");
    eq(`inside: ${name} records boundary match`, got && got.routing_match_field, "boundary");
    eq(`inside: ${name} never enables contract inference`, got && got.tender_eligible, false);
    eq(`inside: ${name} warns road ownership is unverified`,
       got && got.ownership_unverified, true);
    eq(`inside: ${name} requires official reference before submitted`,
       got && got.requires_official_reference, true);
  }

  const outside = [
    ["Howrah Maidan", 22.5815, 88.3285],
    ["Salt Lake Sector I", 22.5868, 88.4172],
    ["New Town Eco Park", 22.6035, 88.4677],
    ["Dum Dum north", 22.6500, 88.4200],
  ];
  for (const [name, lat, lng] of outside) {
    const got = await P.kolkataRouteFromGeocode(wb(), lat, lng, 12);
    eq(`outside: ${name} is not routed`, got && got.routed, false);
    eq(`outside: ${name} is explicitly outside KMC`,
       got && got.unrouted_reason, "outside_area");
  }

  const conflicting = await P.kolkataRouteFromGeocode({
    city: "Mumbai", state: "Maharashtra", country_code: "in", full: "Mumbai",
  }, 22.5726, 88.3639, 12);
  eq("containment: KMC polygon beats a conflicting geocoder city/state",
     conflicting && conflicting.authority_id, "wb-kmc");
  const noGeocode = await P.kolkataRouteFromGeocode(null, 22.5726, 88.3639, 12);
  eq("containment: KMC works when reverse geocoding fails",
     noGeocode && noGeocode.authority_id, "wb-kmc");
  const falseName = await P.kolkataRouteFromGeocode(
    wb({city: "Kolkata", full: "mislabelled Kolkata"}), 22.5815, 88.3285, 12);
  eq("containment: a Kolkata place name outside the polygon never selects KMC",
     falseName && falseName.unrouted_reason, "outside_area");

  const atLimit = await P.kolkataRouteFromGeocode(wb(), 22.5726, 88.3639, 30);
  eq("location: 30 m is accepted away from an edge", atLimit && atLimit.authority_id, "wb-kmc");
  for (const [name, accuracy] of [["31 m", 31], ["missing", Number.NaN], ["negative", -1]]) {
    const got = await P.kolkataRouteFromGeocode(wb(), 22.5726, 88.3639, accuracy);
    eq(`location: ${name} accuracy fails closed`, got && got.routed, false);
    eq(`location: ${name} identifies uncertainty`,
       got && got.unrouted_reason, "location_uncertain");
  }
  const edgePoint = coverage.region.geometry.type === "Polygon"
    ? coverage.region.geometry.coordinates[0][0]
    : coverage.region.geometry.coordinates[0][0][0];
  const edge = await P.kolkataRouteFromGeocode(
    wb(), edgePoint[1], edgePoint[0], 5);
  eq("location: an accuracy circle touching the KMC edge fails closed",
     edge && edge.unrouted_reason, "location_uncertain");

  let kgisCalls = 0;
  const originalFetch = window.fetch;
  window.fetch = (url, ...args) => {
    if (String(url).includes("kgis.ksrsac.in")) kgisCalls++;
    return originalFetch(url, ...args);
  };
  const darjeeling = await P.routeOfficer(
    wb({city: "Darjeeling", full: "Darjeeling, West Bengal"}), 27.0410, 88.2663, 12);
  window.fetch = originalFetch;
  eq("state isolation: West Bengal outside KMC stays outside",
     darjeeling && darjeeling.unrouted_reason, "outside_area");
  eq("state isolation: West Bengal never calls Karnataka GIS", kgisCalls, 0);

  return checks;
}
"""


def run_scenario(browser, route_override=None):
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    if route_override is not None:
        page.route(route_pattern("in-wb-routing"), route_override)
    page.goto(APP)
    page.wait_for_load_state("networkidle")
    page.wait_for_function(
        "typeof StandaloneAPI !== 'undefined' && StandaloneAPI.__pure", timeout=30000
    )
    return context, page


def main():
    failures = []
    try:
        read_pack("in-wb-routing")
        data = read_payload("in-wb-routing")
    except (AssertionError, KeyError, OSError, ValueError) as error:
        failures.append(f"West Bengal routing pack pin is invalid: {error}")
        data = {}
    if not data:
        print("FAIL")
        for failure in failures:
            print("  -", failure)
        sys.exit(1)
    if data["retrieved_at"] != "2026-08-21":
        failures.append("coverage retrieval date is not pinned")
    if not (199.0 < float(data["region"]["area_km2"]) < 201.0):
        failures.append("coverage area is outside the verified WFS range")
    geometry_bytes = json.dumps(
        data["region"]["geometry"], separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    geometry_digest = hashlib.sha256(geometry_bytes).hexdigest()
    expected_digest = "fa9e157d8cdc8d918dd934a77a5dcde375d3108598412cb8ca3e19ca2d916bf5"
    if geometry_digest != expected_digest or data["region"].get("geometry_sha256") != expected_digest:
        failures.append(f"coverage geometry digest is not pinned: {geometry_digest}")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=["--disable-web-security"])
        context, page = run_scenario(browser)
        results = page.evaluate(SCENARIO)
        context.close()
        for name, passed, got, want in results:
            if not passed:
                failures.append(name)
                print(f"  FAIL {name}\n         got  {got}\n         want {want}")

        def malformed(route):
            route.fulfill(
                status=200,
                content_type="application/json",
                body='{"version":1,"region":{"authority_id":"wb-kmc",'
                     '"ulb_code":"250299","mun_id":"250299_0000001",'
                     '"geometry":{"type":"Polygon","coordinates":[]}}}',
            )

        context, page = run_scenario(browser, malformed)
        malformed_reason = page.evaluate(
            "async () => (await StandaloneAPI.__pure.routeOfficer("
            "{state:'West Bengal',country_code:'in'},22.5726,88.3639,12)).unrouted_reason"
        )
        if malformed_reason != "jurisdiction_unavailable":
            failures.append(f"malformed coverage did not fail closed: {malformed_reason}")
        context.close()

        def wrong_but_valid(route):
            route.fulfill(
                status=200,
                content_type="application/json",
                body='{"version":1,"region":{"authority_id":"wb-kmc",'
                     '"ulb_code":"250299","mun_id":"250299_0000001",'
                     '"geometry":{"type":"Polygon","coordinates":[[[88.30,22.55],'
                     '[88.35,22.55],[88.35,22.60],[88.30,22.60],[88.30,22.55]]]}}}',
            )

        context, page = run_scenario(browser, wrong_but_valid)
        wrong_reason = page.evaluate(
            "async () => (await StandaloneAPI.__pure.routeOfficer("
            "{state:'West Bengal',country_code:'in'},22.5815,88.3285,12)).unrouted_reason"
        )
        if wrong_reason != "jurisdiction_unavailable":
            failures.append(f"valid-shaped wrong coverage did not fail closed: {wrong_reason}")
        context.close()

        context, page = run_scenario(
            browser, lambda route: route.fulfill(status=404, body="missing")
        )
        missing_reason = page.evaluate(
            "async () => (await StandaloneAPI.__pure.routeOfficer("
            "{state:'West Bengal',country_code:'in'},22.5726,88.3639,12)).unrouted_reason"
        )
        if missing_reason != "jurisdiction_unavailable":
            failures.append(f"missing coverage did not fail closed: {missing_reason}")
        context.close()
        browser.close()

    if failures:
        print("FAIL")
        for failure in failures:
            print("  -", failure)
        sys.exit(1)
    print(f"KOLKATA ROUTING TEST PASS ({len(results) + 6} checks)")


main()
