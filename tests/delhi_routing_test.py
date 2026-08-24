# -*- coding: utf-8 -*-
"""Delhi routing must cover the full NCT, exclude NCR neighbours, and fail closed."""
import hashlib
import json
import sys

from playwright.sync_api import sync_playwright
from state_pack_utils import read_pack, read_payload, route_pattern


APP = "http://localhost:8765/"
EXPECTED_DIGEST = "3462ba68bdbbc1fdebc99403aa9e1f9db5e0b78e30ca138b2d25df7463506ab3"

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
  const rejects = (name, fn) => {
    try { fn(); checks.push([name, false, "accepted", "rejected"]); }
    catch (error) { checks.push([name, true, String(error && error.message || error), "rejected"]); }
  };

  // Official contacts are installed only after the state pack passes its full-byte and
  // envelope checks. This is the same lazy path production routing takes.
  const coverage = await P.delhiCoverage();

  eq("registry: version includes statewide Tamil Nadu routing", P.AUTHORITY_REGISTRY_VERSION, 11);
  eq("registry: Delhi route has a stable ID", P.DELHI_PWD_AUTHORITY.id, "dl-pwd-sewa");
  eq("registry: primary route is PWD Sewa", P.DELHI_PWD_AUTHORITY.handoff_name, "PWD Sewa");
  ok("registry: primary complaint route is official HTTPS",
     /^https:\/\/www\.pwddelhi\.gov\.in\/sewa\/complaint/.test(P.DELHI_PWD_AUTHORITY.handoff_url),
     P.DELHI_PWD_AUTHORITY.handoff_url);
  eq("registry: official PWD Sewa app package is launchable",
     P.DELHI_PWD_AUTHORITY.handoff_package, "com.sis.pwdsewaapp");
  eq("registry: Delhi PGMS is the independent fallback",
     P.DELHI_PWD_AUTHORITY.alternate_handoff_url, "https://pgms.delhi.gov.in/");
  eq("registry: PWD Sewa WhatsApp is retained",
     P.DELHI_PWD_AUTHORITY.whatsapp_url, "https://wa.me/918130188222");
  eq("registry: PWD Sewa helpline is retained", P.DELHI_PWD_AUTHORITY.helpline, "1908");
  ok("registry: every official route passes structural validation",
     P.validateOfficialHandoffRegistry(P.OFFICIAL_AUTHORITIES), null);
  rejects("registry: duplicate IDs are rejected", () =>
    P.validateOfficialHandoffRegistry([P.DELHI_PWD_AUTHORITY, {...P.DELHI_PWD_AUTHORITY}]));
  rejects("registry: non-HTTPS primary routes are rejected", () =>
    P.validateOfficialHandoffRegistry([{...P.DELHI_PWD_AUTHORITY, handoff_url: "http://invalid"}]));
  rejects("registry: invalid Android packages are rejected", () =>
    P.validateOfficialHandoffRegistry([{...P.DELHI_PWD_AUTHORITY, handoff_package: "bad package"}]));
  rejects("registry: invalid WhatsApp routes are rejected", () =>
    P.validateOfficialHandoffRegistry([{...P.DELHI_PWD_AUTHORITY, whatsapp_url: "https://example.invalid"}]));

  ok("coverage: Delhi asset loads", coverage && coverage.region, coverage);
  eq("coverage: NCT ID is pinned", coverage.region.id, "delhi-nct");
  eq("coverage: OSM relation is pinned", coverage.region.osm_relation_id, 1942586);
  eq("coverage: authority agrees with registry", coverage.region.authority_id, "dl-pwd-sewa");
  eq("coverage: runtime digest is pinned", P.DELHI_GEOMETRY_SHA256,
     "3462ba68bdbbc1fdebc99403aa9e1f9db5e0b78e30ca138b2d25df7463506ab3");
  ok("coverage: scope explicitly excludes wider NCR",
     /excludes.*National Capital Region/i.test(coverage.region.scope), coverage.region.scope);
  ok("coverage: ODbL attribution is explicit",
     /OpenStreetMap contributors/.test(coverage.region.source_name)
       && /ODbL/.test(coverage.region.source_license), coverage.region.source_license);
  ok("coverage: metadata says this is not an ownership map",
     /does not identify a road owner/i.test(coverage.region.routing_note), coverage.region.routing_note);
  const bbox = coverage.region.bbox;
  ok("coverage: every boundary extent remains reachable inside the Delhi envelope",
     bbox && P.inDelhiEnvelope(bbox.min_lat, bbox.min_lng)
       && P.inDelhiEnvelope(bbox.max_lat, bbox.max_lng), bbox);

  const inside = [
    ["India Gate", 28.6129, 77.2295],
    ["Connaught Place", 28.6315, 77.2167],
    ["Rohini", 28.7041, 77.1025],
    ["Dwarka", 28.5921, 77.0460],
    ["Najafgarh", 28.6090, 76.9855],
    ["Narela", 28.8527, 77.0929],
    ["Delhi Cantonment", 28.5960, 77.1290],
    ["Shahdara", 28.6733, 77.2890],
    ["Karawal Nagar", 28.7283, 77.2764],
    ["Badarpur", 28.5020, 77.3022],
    ["Aya Nagar", 28.4709, 77.1325],
  ];
  for (const [name, lat, lng] of inside) {
    const got = await P.delhiRouteFromGeocode(null, lat, lng, 12);
    eq(`inside: ${name} routes to Delhi coordination`, got && got.authority_id, "dl-pwd-sewa");
    eq(`inside: ${name} records Delhi region`, got && got.region, "delhi");
    eq(`inside: ${name} records NCT boundary source`,
       got && got.routing_source, "osm_delhi_nct_boundary");
    eq(`inside: ${name} records relation containment`,
       got && got.routing_match_value, "OpenStreetMap relation 1942586");
    eq(`inside: ${name} never infers a contract`, got && got.tender_eligible, false);
    eq(`inside: ${name} does not claim road ownership`, got && got.ownership_unverified, true);
    eq(`inside: ${name} requires an official reference`,
       got && got.requires_official_reference, true);
  }

  const outside = [
    ["Gurugram", 28.4595, 77.0266],
    ["Noida", 28.5355, 77.3910],
    ["Ghaziabad", 28.6692, 77.4538],
    ["Faridabad", 28.4089, 77.3178],
    ["Bahadurgarh", 28.6929, 76.9355],
  ];
  for (const [name, lat, lng] of outside) {
    const got = await P.delhiRouteFromGeocode(null, lat, lng, 12);
    eq(`outside: ${name} is not routed`, got && got.routed, false);
    eq(`outside: ${name} is explicitly outside Delhi NCT`,
       got && got.unrouted_reason, "outside_area");
  }

  const conflicting = await P.delhiRouteFromGeocode({
    city: "Mumbai", state: "Maharashtra", country_code: "in", full: "Mumbai",
  }, 28.6129, 77.2295, 12);
  eq("containment: Delhi polygon beats a conflicting geocoder",
     conflicting && conflicting.authority_id, "dl-pwd-sewa");
  const falseName = await P.delhiRouteFromGeocode({
    city: "Delhi", state: "Delhi", country_code: "in", full: "mislabelled Delhi",
  }, 28.5355, 77.3910, 12);
  eq("containment: a Delhi name outside NCT never routes",
     falseName && falseName.unrouted_reason, "outside_area");

  const atLimit = await P.delhiRouteFromGeocode(null, 28.6129, 77.2295, 30);
  eq("location: 30 m is accepted away from the edge", atLimit && atLimit.authority_id,
     "dl-pwd-sewa");
  for (const [name, accuracy] of [["31 m", 31], ["not a number", Number.NaN], ["negative", -1]]) {
    const got = await P.delhiRouteFromGeocode(null, 28.6129, 77.2295, accuracy);
    eq(`location: ${name} accuracy fails closed`, got && got.routed, false);
    eq(`location: ${name} identifies uncertainty`,
       got && got.unrouted_reason, "location_uncertain");
  }
  const edgePoint = coverage.region.geometry.type === "Polygon"
    ? coverage.region.geometry.coordinates[0][0]
    : coverage.region.geometry.coordinates[0][0][0];
  const edge = await P.delhiRouteFromGeocode(null, edgePoint[1], edgePoint[0], 5);
  eq("location: an accuracy circle touching the Delhi edge fails closed",
     edge && edge.unrouted_reason, "location_uncertain");

  let kgisCalls = 0;
  const originalFetch = window.fetch;
  window.fetch = (url, ...args) => {
    if (String(url).includes("kgis.ksrsac.in")) kgisCalls++;
    return originalFetch(url, ...args);
  };
  const noida = await P.routeOfficer(
    {city: "Noida", state: "Uttar Pradesh", country_code: "in"}, 28.5355, 77.3910, 12);
  window.fetch = originalFetch;
  eq("state isolation: NCR outside Delhi stays outside", noida && noida.unrouted_reason,
     "outside_area");
  eq("state isolation: Delhi/NCR envelope never calls Karnataka GIS", kgisCalls, 0);

  return checks;
}
"""


def run_scenario(browser, route_override=None):
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    if route_override is not None:
        page.route(route_pattern("in-dl-routing"), route_override)
    page.goto(APP)
    page.wait_for_load_state("networkidle")
    page.wait_for_function(
        "typeof StandaloneAPI !== 'undefined' && StandaloneAPI.__pure", timeout=30000
    )
    return context, page


def main():
    failures = []
    try:
        read_pack("in-dl-routing")
        data = read_payload("in-dl-routing")
    except (AssertionError, KeyError, OSError, ValueError) as error:
        failures.append(f"Delhi routing pack pin is invalid: {error}")
        data = {}
    if not data:
        print("FAIL")
        for failure in failures:
            print("  -", failure)
        sys.exit(1)
    if data["retrieved_at"] != "2026-08-21":
        failures.append("coverage retrieval date is not pinned")
    if not (1480.0 < float(data["region"]["area_km2"]) < 1490.0):
        failures.append("coverage area is outside the verified Delhi NCT range")
    bbox = data["region"].get("bbox", {})
    if not (
        76.65 <= float(bbox.get("min_lng", -999)) <= float(bbox.get("max_lng", 999)) <= 77.65
        and 28.10 <= float(bbox.get("min_lat", -999)) <= float(bbox.get("max_lat", 999)) <= 29.10
    ):
        failures.append(f"coverage bbox falls outside the runtime Delhi envelope: {bbox}")
    geometry_bytes = json.dumps(
        data["region"]["geometry"], separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    geometry_digest = hashlib.sha256(geometry_bytes).hexdigest()
    if geometry_digest != EXPECTED_DIGEST or data["region"].get("geometry_sha256") != EXPECTED_DIGEST:
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
                body='{"version":1,"region":{"id":"delhi-nct","authority_id":"dl-pwd-sewa",'
                     '"osm_relation_id":1942586,"geometry_sha256":"' + EXPECTED_DIGEST + '",'
                     '"geometry":{"type":"Polygon","coordinates":[]}}}',
            )

        context, page = run_scenario(browser, malformed)
        reason = page.evaluate(
            "async () => (await StandaloneAPI.__pure.routeOfficer(null,28.6129,77.2295,12)).unrouted_reason"
        )
        if reason != "jurisdiction_unavailable":
            failures.append(f"malformed coverage did not fail closed: {reason}")
        context.close()

        def wrong_but_valid(route):
            route.fulfill(
                status=200,
                content_type="application/json",
                body='{"version":1,"region":{"id":"delhi-nct","authority_id":"dl-pwd-sewa",'
                     '"osm_relation_id":1942586,"geometry_sha256":"' + EXPECTED_DIGEST + '",'
                     '"geometry":{"type":"Polygon","coordinates":[[[77.20,28.60],'
                     '[77.25,28.60],[77.25,28.65],[77.20,28.65],[77.20,28.60]]]}}}',
            )

        context, page = run_scenario(browser, wrong_but_valid)
        reason = page.evaluate(
            "async () => (await StandaloneAPI.__pure.routeOfficer(null,28.6129,77.2295,12)).unrouted_reason"
        )
        if reason != "jurisdiction_unavailable":
            failures.append(f"valid-shaped wrong coverage did not fail closed: {reason}")
        context.close()

        context, page = run_scenario(
            browser, lambda route: route.fulfill(status=404, body="missing")
        )
        reason = page.evaluate(
            "async () => (await StandaloneAPI.__pure.routeOfficer(null,28.6129,77.2295,12)).unrouted_reason"
        )
        if reason != "jurisdiction_unavailable":
            failures.append(f"missing coverage did not fail closed: {reason}")
        context.close()
        browser.close()

    if failures:
        print("FAIL")
        for failure in failures:
            print("  -", failure)
        sys.exit(1)
    print(f"DELHI ROUTING TEST PASS ({len(results) + 6} checks)")


main()
