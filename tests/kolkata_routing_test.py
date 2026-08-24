# -*- coding: utf-8 -*-
"""West Bengal routing preserves exact KMC handling and fails closed at edges."""
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

  eq("registry: version includes statewide Telangana routing", P.AUTHORITY_REGISTRY_VERSION, 13);
  eq("registry: KMC uses a stable ID", P.KMC_AUTHORITY.id, "wb-kmc");
  eq("registry: statewide fallback uses a stable ID",
     P.WEST_BENGAL_STATE_AUTHORITY.id, "wb-statewide-unverified");
  ok("registry: both West Bengal authorities validate",
     P.validateAuthorityRegistry([P.KMC_AUTHORITY, P.WEST_BENGAL_STATE_AUTHORITY]), null);
  eq("registry: primary channel is KMC Grievance 2.0",
     P.KMC_AUTHORITY.handoff_name, "KMC Grievance 2.0");
  ok("registry: primary handoff is HTTPS and official",
     /^https:\/\/kmc\.wb\.gov\.in\//.test(P.KMC_AUTHORITY.handoff_url),
     P.KMC_AUTHORITY.handoff_url);
  eq("registry: installed official KMC app is launchable",
     P.KMC_AUTHORITY.handoff_package, "com.kmc.app");
  eq("registry: verified WhatsApp channel is retained",
     P.KMC_AUTHORITY.whatsapp_url, "https://wa.me/918335988888");
  eq("registry: statewide route uses the official PGRS",
     P.WEST_BENGAL_STATE_AUTHORITY.handoff_name, "West Bengal PGRS");
  eq("registry: statewide PGRS URL is pinned",
     P.WEST_BENGAL_STATE_AUTHORITY.handoff_url,
     "https://finance.wb.gov.in/pgrs/page/PGMS_Lodge_Greivance.aspx");

  ok("coverage: statewide asset loads", coverage && coverage.regions, coverage);
  eq("coverage: payload uses the statewide schema", coverage.version, 2);
  eq("coverage: ULB code is pinned", coverage.regions.kmc.ulb_code, "250299");
  eq("coverage: municipal ID is pinned", coverage.regions.kmc.mun_id, "250299_0000001");
  eq("coverage: KMC authority ID agrees with registry",
     coverage.regions.kmc.authority_id, "wb-kmc");
  ok("coverage: source names West Bengal UD&MA",
     /Urban Development.*Municipal Affairs.*West Bengal/i.test(coverage.regions.kmc.source_name),
     coverage.regions.kmc.source_name);
  ok("coverage: source URL is the official municipal WFS query",
     /^https:\/\/nagargispariseva\.wb\.gov\.in\//.test(coverage.regions.kmc.source_url)
       && /wb_municipal_boundary/.test(coverage.regions.kmc.source_url)
       && /250299/.test(coverage.regions.kmc.source_filter),
     coverage.regions.kmc.source_url);
  ok("coverage: no open-data licence is invented",
     /no explicit reuse licence/i.test(coverage.regions.kmc.source_access),
     coverage.regions.kmc.source_access);
  eq("coverage: state boundary relation is pinned",
     coverage.regions.west_bengal.source_relation_id, 1960177);
  eq("coverage: state boundary digest agrees with runtime",
     coverage.regions.west_bengal.geometry_sha256,
     P.WEST_BENGAL_STATE_GEOMETRY_SHA256);
  eq("coverage: state fallback authority agrees with registry",
     coverage.regions.west_bengal.authority_id, "wb-statewide-unverified");
  ok("coverage: state boundary keeps ODbL attribution",
     /ODbL/.test(coverage.regions.west_bengal.licence || ""),
     coverage.regions.west_bengal.licence);

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

  const statewide = [
    ["Howrah Maidan", 22.5815, 88.3285],
    ["Salt Lake Sector I", 22.5868, 88.4172],
    ["New Town Eco Park", 22.6035, 88.4677],
    ["Dum Dum north", 22.6500, 88.4200],
    ["Darjeeling", 27.0410, 88.2663],
    ["Siliguri", 26.7271, 88.3953],
    ["Durgapur", 23.5204, 87.3119],
    ["Asansol", 23.6739, 86.9524],
    ["Malda", 25.0108, 88.1411],
    ["Kharagpur", 22.3460, 87.2320],
    ["Cooch Behar", 26.3452, 89.4482],
  ];
  for (const [name, lat, lng] of statewide) {
    const got = await P.kolkataRouteFromGeocode(wb(), lat, lng, 12);
    eq(`statewide: ${name} uses the neutral state route`,
       got && got.authority_id, "wb-statewide-unverified");
    eq(`statewide: ${name} records the state region`,
       got && got.region, "west-bengal");
    eq(`statewide: ${name} uses polygon containment`,
       got && got.routing_source, "osm_west_bengal_state_boundary");
    eq(`statewide: ${name} opens West Bengal PGRS`, got && got.handoff_url,
       "https://finance.wb.gov.in/pgrs/page/PGMS_Lodge_Greivance.aspx");
    eq(`statewide: ${name} never claims an exact road owner`,
       got && got.ownership_unverified, true);
    eq(`statewide: ${name} never enables tender inference`,
       got && got.tender_eligible, false);
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
     falseName && falseName.authority_id, "wb-statewide-unverified");

  const noGeocodeState = await P.kolkataRouteFromGeocode(null, 23.5204, 87.3119, 12);
  eq("containment: statewide routing works when reverse geocoding fails",
     noGeocodeState && noGeocodeState.authority_id, "wb-statewide-unverified");

  const outside = [
    ["Jharkhand / Jamshedpur", 22.8046, 86.2029,
      {city: "Jamshedpur", state: "Jharkhand", country_code: "in"}],
    ["Sikkim / Gangtok", 27.3314, 88.6138,
      {city: "Gangtok", state: "Sikkim", country_code: "in"}],
    ["Bangladesh / Khulna", 22.8456, 89.5403,
      {city: "Khulna", state: "Khulna", country_code: "bd"}],
  ];
  for (const [name, lat, lng, geo] of outside) {
    const got = await P.kolkataRouteFromGeocode(geo, lat, lng, 12);
    eq(`outside: ${name} is refused by the state polygon`,
       got && got.unrouted_reason, "outside_area");
  }
  const falseState = await P.kolkataRouteFromGeocode(
    wb({city: "Jamshedpur", full: "mislabelled West Bengal"}), 22.8046, 86.2029, 12);
  eq("containment: a West Bengal geocoder label cannot override the state polygon",
     falseState && falseState.unrouted_reason, "outside_area");
  const staleWestBengalGeo = wb({city: "Chennai", full: "stale West Bengal geocoder label"});
  const staleWestBengalRoute = await P.kolkataRouteFromGeocode(
    staleWestBengalGeo, 13.0827, 80.2707, 12);
  eq("state isolation: a stale West Bengal label outside its routing envelope is ignored",
     staleWestBengalRoute, null);
  const chennaiWithStaleState = await P.routeOfficer(
    staleWestBengalGeo, 13.0827, 80.2707, 12, 0, 0, "garbage");
  eq("state isolation: Chennai containment wins over a stale West Bengal label",
     chennaiWithStaleState && chennaiWithStaleState.authority_id, "tn-gcc");

  const atLimit = await P.kolkataRouteFromGeocode(wb(), 22.5726, 88.3639, 30);
  eq("location: 30 m is accepted away from an edge", atLimit && atLimit.authority_id, "wb-kmc");
  for (const [name, accuracy] of [["31 m", 31], ["missing", Number.NaN], ["negative", -1]]) {
    const got = await P.kolkataRouteFromGeocode(wb(), 22.5726, 88.3639, accuracy);
    eq(`location: ${name} accuracy fails closed`, got && got.routed, false);
    eq(`location: ${name} identifies uncertainty`,
       got && got.unrouted_reason, "location_uncertain");
  }
  const firstBoundaryPoint = (geometry) => geometry.type === "Polygon"
    ? geometry.coordinates[0][0] : geometry.coordinates[0][0][0];
  const kmcEdgePoint = firstBoundaryPoint(coverage.regions.kmc.geometry);
  const edge = await P.kolkataRouteFromGeocode(
    wb(), kmcEdgePoint[1], kmcEdgePoint[0], 5);
  eq("location: an accuracy circle touching the KMC edge fails closed",
     edge && edge.unrouted_reason, "location_uncertain");
  const stateAtLimit = await P.kolkataRouteFromGeocode(wb(), 23.5204, 87.3119, 30);
  eq("location: a 30 m statewide fix is accepted away from edges",
     stateAtLimit && stateAtLimit.authority_id, "wb-statewide-unverified");
  const stateCoarse = await P.kolkataRouteFromGeocode(wb(), 23.5204, 87.3119, 31);
  eq("location: an over-30 m statewide fix fails closed",
     stateCoarse && stateCoarse.unrouted_reason, "location_uncertain");
  const stateEdgePoint = firstBoundaryPoint(coverage.regions.west_bengal.geometry);
  const stateEdge = await P.kolkataRouteFromGeocode(
    wb(), stateEdgePoint[1], stateEdgePoint[0], 5);
  eq("location: an accuracy circle touching the state edge fails closed",
     stateEdge && stateEdge.unrouted_reason, "location_uncertain");

  let kgisCalls = 0;
  const originalFetch = window.fetch;
  window.fetch = (url, ...args) => {
    if (String(url).includes("kgis.ksrsac.in")) kgisCalls++;
    return originalFetch(url, ...args);
  };
  const darjeeling = await P.routeOfficer(
    wb({city: "Darjeeling", full: "Darjeeling, West Bengal"}), 27.0410, 88.2663, 12);
  window.fetch = originalFetch;
  eq("state isolation: Darjeeling uses the statewide West Bengal route",
     darjeeling && darjeeling.authority_id, "wb-statewide-unverified");
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
    if data.get("version") != 2:
        failures.append("coverage payload version is not 2")
    if data["retrieved_at"] != "2026-08-24":
        failures.append("coverage retrieval date is not pinned")
    regions = data.get("regions", {})
    kmc = regions.get("kmc", {})
    state = regions.get("west_bengal", {})
    if not (199.0 < float(kmc.get("area_km2", 0)) < 201.0):
        failures.append("coverage area is outside the verified WFS range")
    kmc_geometry_bytes = json.dumps(
        kmc.get("geometry"), separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    kmc_geometry_digest = hashlib.sha256(kmc_geometry_bytes).hexdigest()
    expected_kmc_digest = "fa9e157d8cdc8d918dd934a77a5dcde375d3108598412cb8ca3e19ca2d916bf5"
    if (kmc_geometry_digest != expected_kmc_digest
            or kmc.get("geometry_sha256") != expected_kmc_digest):
        failures.append(f"KMC geometry digest is not pinned: {kmc_geometry_digest}")
    state_geometry_bytes = json.dumps(
        state.get("geometry"), separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    state_geometry_digest = hashlib.sha256(state_geometry_bytes).hexdigest()
    expected_state_digest = "aa4ab13c3064be2e168889f6eb02e87c59e01bc709d36b66bece534dfea23015"
    if (state_geometry_digest != expected_state_digest
            or state.get("geometry_sha256") != expected_state_digest):
        failures.append(f"state geometry digest is not pinned: {state_geometry_digest}")
    if state.get("source_relation_id") != 1960177:
        failures.append("West Bengal state relation ID is not pinned")

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
                body='{"format":"pothole-routing-pack","schema_version":1,'
                     '"pack_id":"in-wb-routing","pack_version":1,"state_code":"WB",'
                     '"adapter":"west-bengal-statewide-v1","generated_at":"2026-08-24",'
                     '"authorities":[],"payload":{"version":2,"regions":{}}}',
            )

        context, page = run_scenario(browser, malformed)
        malformed_reason = page.evaluate(
            "async () => (await StandaloneAPI.__pure.routeOfficer("
            "{state:'West Bengal',country_code:'in'},22.5726,88.3639,12)).unrouted_reason"
        )
        if malformed_reason != "jurisdiction_unavailable":
            failures.append(f"malformed coverage did not fail closed: {malformed_reason}")
        context.close()

        def wrong_digest(route):
            route.fulfill(
                status=200,
                content_type="application/json",
                body='{"format":"pothole-routing-pack","schema_version":1,'
                     '"pack_id":"in-wb-routing","pack_version":1,"state_code":"WB",'
                     '"adapter":"west-bengal-statewide-v1","generated_at":"2026-08-24",'
                     '"authorities":[{"id":"wb-kmc"},{"id":"wb-statewide-unverified"}],'
                     '"payload":{"version":2,"regions":{"kmc":{"authority_id":"wb-kmc",'
                     '"ulb_code":"250299","mun_id":"250299_0000001",'
                     '"geometry_sha256":"0000000000000000000000000000000000000000000000000000000000000000",'
                     '"geometry":{"type":"Polygon","coordinates":[[[88.30,22.55],'
                     '[88.35,22.55],[88.35,22.60],[88.30,22.60],[88.30,22.55]]]}},'
                     '"west_bengal":{"authority_id":"wb-statewide-unverified",'
                     '"source_relation_id":1960177,'
                     '"geometry_sha256":"0000000000000000000000000000000000000000000000000000000000000000",'
                     '"geometry":{"type":"Polygon","coordinates":[[[85.60,21.40],'
                     '[90.10,21.40],[90.10,27.40],[85.60,27.40],[85.60,21.40]]]}}}}}}',
            )

        context, page = run_scenario(browser, wrong_digest)
        wrong_reason = page.evaluate(
            "async () => (await StandaloneAPI.__pure.routeOfficer("
            "{state:'West Bengal',country_code:'in'},22.5815,88.3285,12)).unrouted_reason"
        )
        if wrong_reason != "jurisdiction_unavailable":
            failures.append(f"wrong-digest coverage did not fail closed: {wrong_reason}")
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
