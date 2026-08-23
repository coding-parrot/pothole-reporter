# -*- coding: utf-8 -*-
"""Maharashtra routing must preserve exact local routes and fail closed at state edges."""
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
  const geo = (field, value, district) => ({
    [field]: value, state_district: district || "Thane District",
    state: "Maharashtra", country_code: "in", full: `${value}, Maharashtra, India`,
  });
  const route = (g, lat, lng) => P.maharashtraRouteFromGeocode(g, lat, lng);

  // Loading the verified routing pack installs the state authority registry.
  const coverage = await P.maharashtraCoverage();

  eq("state: pinned relation ID", coverage.regions.maharashtra.source_relation_id, 1950884);
  eq("state: pinned geometry digest", coverage.regions.maharashtra.geometry_sha256,
     P.MAHARASHTRA_STATE_GEOMETRY_SHA256);
  eq("state: statewide fallback authority is installed",
     P.MAHARASHTRA_STATE_AUTHORITY.id, "mh-statewide-unverified");
  ok("state: ODbL attribution is recorded",
     /ODbL/.test(coverage.regions.maharashtra.licence || ""),
     coverage.regions.maharashtra.licence);

  ok("registry: validates", P.validateAuthorityRegistry(), null);
  eq("registry: contains all 19 MMR urban bodies", P.MMR_AUTHORITIES.length, 19);
  eq("registry: IDs are unique",
     new Set(P.MMR_AUTHORITIES.map((a) => a.id)).size, 19);
  let collision = null;
  try {
    P.validateAuthorityRegistry([
      {id: "one", name: "One", aliases: ["same"]},
      {id: "two", name: "Two", aliases: ["same"]},
    ]);
  } catch (e) { collision = String(e.message || e); }
  ok("registry: alias collisions fail closed", /collision/i.test(collision || ""), collision);

  const boundaryIds = Object.keys(coverage.regions.mmr.authority_boundaries).sort();
  const directAuthorityIds = [
    "mh-bmc", "mh-tmc", "mh-kdmc", "mh-nmmc", "mh-umc", "mh-bncmc",
    "mh-vvcmc", "mh-mbmc", "mh-panvel", "mh-ambarnath", "mh-badlapur",
  ].sort();
  const missingBoundaryIds = [
    "mh-matheran", "mh-karjat", "mh-khopoli", "mh-pen", "mh-uran",
    "mh-alibag", "mh-palghar", "mh-khalapur",
  ].sort();
  eq("boundaries: exactly 11 MMR authorities have containment polygons",
     boundaryIds.length, 11);
  eq("boundaries: polygon-backed authority IDs are explicit and complete",
     boundaryIds, directAuthorityIds);
  eq("boundaries: metadata records the same complete authority set",
     [...coverage.regions.mmr.boundary_complete_authority_ids].sort(), directAuthorityIds);
  eq("boundaries: metadata records all eight missing council polygons",
     [...coverage.regions.mmr.boundary_missing_authority_ids].sort(), missingBoundaryIds);
  ok("boundaries: runtime validates the exact checked-in authority and ward sets",
     P.validMmrAuthorityBoundaries(coverage.regions.mmr), null);
  const wrongIdBoundaries = {...coverage.regions.mmr.authority_boundaries};
  wrongIdBoundaries["mh-pmc"] = wrongIdBoundaries["mh-tmc"];
  delete wrongIdBoundaries["mh-tmc"];
  eq("boundaries: a wrong authority ID makes the asset fail closed",
     P.validMmrAuthorityBoundaries({
       ...coverage.regions.mmr, authority_boundaries: wrongIdBoundaries,
     }), false);
  const incompleteWardBoundaries = {...coverage.regions.mmr.authority_boundaries};
  incompleteWardBoundaries["mh-bmc"] = {
    ...incompleteWardBoundaries["mh-bmc"],
    wards: incompleteWardBoundaries["mh-bmc"].wards.slice(1),
  };
  eq("boundaries: an incomplete BMC ward set makes the asset fail closed",
     P.validMmrAuthorityBoundaries({
       ...coverage.regions.mmr, authority_boundaries: incompleteWardBoundaries,
     }), false);
  eq("boundaries: source is recorded",
     coverage.regions.mmr.authority_boundary_source,
     "OpenStreetMap administrative relations via Nominatim lookup");
  ok("boundaries: ODbL attribution is recorded",
     /ODbL/.test(coverage.regions.mmr.authority_boundary_licence || ""),
     coverage.regions.mmr.authority_boundary_licence);

  const polygonFixtures = [
    ["mh-bmc", geo("city", "Mumbai", "Mumbai City District"), 19.0760, 72.8777],
    ["mh-tmc", geo("city", "Thane"), 19.2183, 72.9781],
    ["mh-kdmc", geo("city", "Kalyan"), 19.2403, 73.1305],
    ["mh-nmmc", geo("city", "Navi Mumbai"), 19.0330, 73.0290],
    ["mh-umc", geo("city", "Ulhasnagar"), 19.2215, 73.1645],
    ["mh-bncmc", geo("city", "Bhiwandi"), 19.2813, 73.0483],
    ["mh-vvcmc", geo("city", "Vasai", "Palghar District"), 19.3919, 72.8397],
    ["mh-mbmc", geo("city", "Mira-Bhayandar"), 19.2952, 72.8544],
    ["mh-panvel", geo("city", "Panvel", "Raigad District"), 18.9894, 73.1175],
    ["mh-ambarnath", geo("town", "Ambarnath"), 19.1860, 73.1910],
    ["mh-badlapur", geo("town", "Badlapur"), 19.1668, 73.2368],
  ];
  for (const [want, g, lat, lng] of polygonFixtures) {
    const got = await route(g, lat, lng);
    eq(`route: ${want}`, got && got.authority_id, want);
    ok(`route: ${want} is explicitly routable`, got && got.routed, got);
    ok(`route: ${want} records ownership warning`, got && got.ownership_unverified, got);
    eq(`route: ${want} is selected by containment`, got && got.routing_source, "osm_ulb_boundary");
    eq(`route: ${want} records a boundary match`, got && got.routing_match_field, "boundary");
  }
  const tmcPrecise = await P.maharashtraRouteFromGeocode(
    geo("city", "Thane"), 19.2183, 72.9781, 12);
  eq("location quality: a precise MMR fix remains routable",
     tmcPrecise && tmcPrecise.authority_id, "mh-tmc");
  const tmcAtLimit = await P.maharashtraRouteFromGeocode(
    geo("city", "Thane"), 19.2183, 72.9781, 30);
  eq("location quality: a 30 m MMR fix remains eligible away from boundaries",
     tmcAtLimit && tmcAtLimit.authority_id, "mh-tmc");
  const tmcCoarse = await P.maharashtraRouteFromGeocode(
    geo("city", "Thane"), 19.2183, 72.9781, 31);
  eq("location quality: an over-30 m MMR fix fails closed",
     tmcCoarse && tmcCoarse.unrouted_reason, "location_uncertain");
  const tmcNegative = await P.maharashtraRouteFromGeocode(
    geo("city", "Thane"), 19.2183, 72.9781, -1);
  eq("location quality: an impossible negative accuracy fails closed",
     tmcNegative && tmcNegative.unrouted_reason, "location_uncertain");

  const missingPolygonFixtures = [
    ["mh-matheran", "Matheran Municipal Council", geo("town", "Matheran", "Raigad District"), 18.9887, 73.2712],
    ["mh-karjat", "Karjat Municipal Council", geo("town", "Karjat", "Raigad District"), 18.9102, 73.3236],
    ["mh-khopoli", "Khopoli Municipal Council", geo("town", "Khopoli", "Raigad District"), 18.7856, 73.3459],
    ["mh-pen", "Pen Municipal Council", geo("town", "Pen", "Raigad District"), 18.7373, 73.0960],
    ["mh-uran", "Uran Municipal Council", geo("town", "Uran", "Raigad District"), 18.8780, 72.9390],
    ["mh-alibag", "Alibag Municipal Council", geo("town", "Alibag", "Raigad District"), 18.6414, 72.8722],
    ["mh-palghar", "Palghar Municipal Council", geo("town", "Palghar", "Palghar District"), 19.6967, 72.7699],
    ["mh-khalapur", "Khalapur Nagar Panchayat", geo("town", "Khalapur", "Raigad District"), 18.8210, 73.2840],
  ];
  for (const [missingId, clueName, g, lat, lng] of missingPolygonFixtures) {
    const got = await route(g, lat, lng);
    eq(`missing boundary: ${missingId} routes neutral`,
       got && got.authority_id, "mh-mmr-unverified");
    eq(`missing boundary: ${missingId} preserves an unverified clue`,
       got && got.routing_match_field, "unverified_place_clue");
    ok(`missing boundary: ${missingId} names the clue without selecting it`,
       got && got.routing_match_value === clueName, got);
    eq(`missing boundary: ${missingId} uses Aaple Sarkar`,
       got && got.handoff_url, "https://grievances.maharashtra.gov.in/en");
    eq(`missing boundary: ${missingId} never exposes the council email`,
       got && got.officer_email, null);
  }

  const palgharCoverage = await route(
    geo("town", "Palghar", "Palghar District"), 19.6967, 72.7699);
  eq("extent: current Palghar taluka remains in MMR but routes neutral without a ULB polygon",
     palgharCoverage.authority_id, "mh-mmr-unverified");
  const dahanu = await route(geo("town", "Dahanu", "Palghar District"), 19.9900, 72.7380);
  eq("extent: Dahanu outside MMR uses the statewide handoff",
     dahanu && dahanu.authority_id, "mh-statewide-unverified");

  const rural = await route({
    village: "Example Village", state_district: "Palghar District",
    state: "Maharashtra", country_code: "in", full: "Example Village, Palghar",
  }, 19.7500, 72.8500);
  eq("fallback: rural MMR does not borrow a nearby municipality",
     rural && rural.authority_id, "mh-mmr-unverified");
  eq("fallback: rural MMR uses the neutral state portal",
     rural && rural.handoff_url, "https://grievances.maharashtra.gov.in/en");
  eq("fallback: rural MMR prefers the current web portal over the stale Android app",
     rural && rural.handoff_package, null);

  const ruralWithNearestCity = await route({
    city: "Vasai", village: "Example Village", state_district: "Palghar District",
    state: "Maharashtra", country_code: "in",
    full: "Example Village, near Vasai, Palghar, Maharashtra",
  }, 19.7500, 72.8500);
  eq("fallback: a nearest-city alias cannot select a non-containing corporation",
     ruralWithNearestCity && ruralWithNearestCity.authority_id, "mh-mmr-unverified");
  eq("fallback: the nearest-city alias is retained only as an unverified clue",
     ruralWithNearestCity && ruralWithNearestCity.routing_match_field,
     "unverified_place_clue");
  eq("fallback: the preserved nearest-city clue names VVCMC",
     ruralWithNearestCity && ruralWithNearestCity.routing_match_value,
     "Vasai-Virar City Municipal Corporation");

  const districtOnly = await route({
    state_district: "Thane District", state: "Maharashtra", country_code: "in",
    full: "Navi Mumbai appears only in display_name",
  }, 19.7500, 72.8500);
  eq("matching: display_name and district alone never select a distant NMMC polygon",
     districtOnly && districtOnly.authority_id, "mh-mmr-unverified");

  // A locality may contain the same words as a corporation without being that
  // corporation. Address aliases are clues only; the containing polygon decides.
  for (const field of ["village", "suburb", "neighbourhood", "county"]) {
    const localityOnly = await route({
      [field]: "Navi Mumbai", state_district: "Thane District",
      state: "Maharashtra", country_code: "in",
      full: `${field}=Navi Mumbai, Maharashtra, India`,
    }, 19.7500, 72.8500);
    eq(`matching: ${field} never selects NMMC`,
       localityOnly && localityOnly.authority_id, "mh-mmr-unverified");
  }
  const partialAlias = await route({
    city: "Navi Mumbai East", state_district: "Thane District",
    state: "Maharashtra", country_code: "in", full: "Navi Mumbai East",
  }, 19.7500, 72.8500);
  eq("matching: a partial authority alias cannot replace containment",
     partialAlias && partialAlias.authority_id, "mh-mmr-unverified");
  const municipalityMatch = await route(
    geo("municipality", "Panvel", "Raigad District"), 18.9894, 73.1175);
  eq("matching: Panvel's polygon selects Panvel at a matching address",
     municipalityMatch && municipalityMatch.authority_id, "mh-panvel");
  const cityDistrictMatch = await route(
    geo("city_district", "Navi Mumbai", "Thane District"), 19.0330, 73.0290);
  eq("matching: NMMC's polygon selects NMMC at a matching address",
     cityDistrictMatch && cityDistrictMatch.authority_id, "mh-nmmc");
  const mismatchedAddress = await route(
    geo("city", "Navi Mumbai", "Thane District"), 18.9894, 73.1175);
  eq("matching: the containing Panvel polygon beats a conflicting NMMC alias",
     mismatchedAddress && mismatchedAddress.authority_id, "mh-panvel");
  const conflictingFields = await route({
    city: "Thane", municipality: "Navi Mumbai", state_district: "Thane District",
    state: "Maharashtra", country_code: "in", full: "Conflicting civic fields",
  }, 19.2183, 72.9781);
  eq("matching: the containing Thane polygon beats conflicting civic fields",
     conflictingFields && conflictingFields.authority_id, "mh-tmc");
  eq("matching: a polygon-selected route records boundary, not alias ambiguity",
     conflictingFields && conflictingFields.routing_match_field, "boundary");

  const bmcPolygonRoute = await route(
    geo("city", "Mumbai", "Mumbai City District"), 19.0760, 72.8777);
  eq("BMC ward: a ward-free address gets ward L from the bundled ward polygon",
     bmcPolygonRoute && bmcPolygonRoute.ward_code, "L");
  eq("BMC ward: ward routing source is the ULB boundary dataset",
     bmcPolygonRoute && bmcPolygonRoute.routing_source, "osm_ulb_boundary");
  ok("BMC ward: boundary provenance is retained",
     /BMC administrative wards.*OSM/.test(bmcPolygonRoute.routing_match_value || ""),
     bmcPolygonRoute && bmcPolygonRoute.routing_match_value);

  // This coordinate is a shared OSM boundary vertex between BMC and MBMC. A point on
  // both polygons must not pick either authority by object iteration order.
  const overlap = await route({
    state: "Maharashtra", country_code: "in", state_district: "Mumbai Suburban District",
    full: "Shared BMC / MBMC boundary",
  }, 19.2648545, 72.7837151);
  eq("overlap: shared authority boundary routes neutral",
     overlap && overlap.authority_id, "mh-mmr-unverified");
  eq("overlap: ambiguity is recorded explicitly",
     overlap && overlap.routing_match_field, "overlapping_boundaries");
  ok("overlap: both candidate authorities are retained for verification",
     /Brihanmumbai Municipal Corporation/.test(overlap.routing_match_value || "")
       && /Mira-Bhayandar Municipal Corporation/.test(overlap.routing_match_value || ""),
     overlap && overlap.routing_match_value);
  const adjacentNoAccuracy = await route(
    geo("city", "Mira-Bhayandar"), 19.237441, 72.914528);
  eq("boundary accuracy: a centre point on the MBMC side routes without accuracy data",
     adjacentNoAccuracy && adjacentNoAccuracy.authority_id, "mh-mbmc");
  const adjacentPrecise = await P.maharashtraRouteFromGeocode(
    geo("city", "Mira-Bhayandar"), 19.237441, 72.914528, 3);
  eq("boundary accuracy: a 3 m circle clear of the adjacent TMC edge routes MBMC",
     adjacentPrecise && adjacentPrecise.authority_id, "mh-mbmc");
  const adjacentCrossing = await P.maharashtraRouteFromGeocode(
    geo("city", "Mira-Bhayandar"), 19.237441, 72.914528, 5);
  eq("boundary accuracy: a circle crossing a neighbouring ULB edge fails closed",
     adjacentCrossing && adjacentCrossing.unrouted_reason, "location_uncertain");

  const pmcCore = await route(geo("city", "Pune", "Pune District"), 18.5204, 73.8567);
  eq("PMC: core city uses the official GIS polygon", pmcCore && pmcCore.authority_id, "mh-pmc");
  eq("PMC: potholes open Road Mitra", pmcCore && pmcCore.handoff_package,
     "com.nyatitechnologies.pmcroadmitra");
  eq("PMC: uses the generic official handoff channel",
     pmcCore && pmcCore.delivery_channel, "official_handoff");
  eq("PMC: stores the verified alternate channel name",
     pmcCore && pmcCore.alternate_handoff_name, "PMC CARE");
  eq("PMC: stores the verified alternate channel URL",
     pmcCore && pmcCore.alternate_handoff_url, "https://pmccare.in/");
  eq("PMC: requires the official reference before local submission confirmation",
     pmcCore && pmcCore.requires_official_reference, true);
  eq("PMC: records official polygon source", pmcCore && pmcCore.routing_source, "pmc_official_gis");
  const pmcPrecise = await P.maharashtraRouteFromGeocode(
    geo("city", "Pune", "Pune District"), 18.5204, 73.8567, 12);
  eq("location quality: a precise core-PMC fix remains routable",
     pmcPrecise && pmcPrecise.authority_id, "mh-pmc");
  const pmcCoarse = await P.maharashtraRouteFromGeocode(
    geo("city", "Pune", "Pune District"), 18.5204, 73.8567, 31);
  eq("location quality: a coarse PMC fix fails closed",
     pmcCoarse && pmcCoarse.routed, false);
  eq("location quality: coarse rejection names location uncertainty",
     pmcCoarse && pmcCoarse.unrouted_reason, "location_uncertain");
  const pmcMissingAccuracy = await P.maharashtraRouteFromGeocode(
    geo("city", "Pune", "Pune District"), 18.5204, 73.8567, Number.NaN);
  eq("location quality: an explicitly missing accuracy fails closed",
     pmcMissingAccuracy && pmcMissingAccuracy.routed, false);
  eq("location quality: missing accuracy is reported as location uncertainty",
     pmcMissingAccuracy && pmcMissingAccuracy.unrouted_reason, "location_uncertain");

  const firstBoundaryPoint = (geometry) => {
    if (geometry.type === "Polygon") return geometry.coordinates[0][0];
    if (geometry.type === "MultiPolygon") return geometry.coordinates[0][0][0];
    if (geometry.type === "GeometryCollection") {
      for (const part of geometry.geometries || []) {
        const point = firstBoundaryPoint(part);
        if (point) return point;
      }
    }
    return null;
  };
  const pmcEdgePoint = firstBoundaryPoint(coverage.regions.pmc.geometry);
  const pmcEdge = await P.maharashtraRouteFromGeocode(
    geo("city", "Pune", "Pune District"), pmcEdgePoint[1], pmcEdgePoint[0], 5);
  eq("location quality: a GPS circle touching the PMC edge fails closed",
     pmcEdge && pmcEdge.routed, false);
  eq("location quality: boundary ambiguity is explicit",
     pmcEdge && pmcEdge.unrouted_reason, "location_uncertain");
  const mmrEdgePoint = firstBoundaryPoint(coverage.regions.mmr.geometry);
  const mmrEdge = await P.maharashtraRouteFromGeocode(
    geo("village", "MMR boundary", "Raigad District"),
    mmrEdgePoint[1], mmrEdgePoint[0], 5);
  eq("location quality: a GPS circle touching the MMR outer edge fails closed",
     mmrEdge && mmrEdge.routed, false);
  eq("location quality: MMR outer-edge ambiguity is explicit",
     mmrEdge && mmrEdge.unrouted_reason, "location_uncertain");

  // Wagholi's structured address may remain a town/village even though it is inside PMC.
  const wagholi = await route(geo("town", "Wagholi", "Pune District"), 18.5807, 73.9787);
  eq("PMC: full current boundary includes Wagholi", wagholi && wagholi.authority_id, "mh-pmc");
  const pcmc = await route(geo("city", "Pimpri-Chinchwad", "Pune District"), 18.6279, 73.8009);
  eq("PMC: PCMC is not conflated with Pune Municipal Corporation",
     pcmc && pcmc.authority_id, "mh-statewide-unverified");
  const baramati = await route(geo("town", "Baramati", "Pune District"), 18.1510, 74.5770);
  eq("PMC: Pune district alone does not create PMC-specific routing",
     baramati && baramati.authority_id, "mh-statewide-unverified");

  const statewideFixtures = [
    ["Nagpur", 21.1458, 79.0882],
    ["Nashik", 19.9975, 73.7898],
    ["Kolhapur", 16.7050, 74.2433],
    ["Solapur", 17.6599, 75.9064],
    ["Chhatrapati Sambhajinagar", 19.8762, 75.3433],
  ];
  for (const [city, lat, lng] of statewideFixtures) {
    const got = await route(geo("city", city, `${city} District`), lat, lng);
    eq(`statewide: ${city} uses the neutral state route`,
       got && got.authority_id, "mh-statewide-unverified");
    eq(`statewide: ${city} uses Aaple Sarkar`, got && got.handoff_url,
       "https://grievances.maharashtra.gov.in/en");
    eq(`statewide: ${city} never claims an exact owner`,
       got && got.ownership_unverified, true);
  }
  const noGeocode = await route(null, 21.1458, 79.0882);
  eq("statewide: polygon containment works without a geocoder state label",
     noGeocode && noGeocode.authority_id, "mh-statewide-unverified");
  const statewidePrecise = await P.maharashtraRouteFromGeocode(
    geo("city", "Nagpur", "Nagpur District"), 21.1458, 79.0882, 12);
  eq("statewide: a precise fix remains routable",
     statewidePrecise && statewidePrecise.authority_id, "mh-statewide-unverified");
  const statewideCoarse = await P.maharashtraRouteFromGeocode(
    geo("city", "Nagpur", "Nagpur District"), 21.1458, 79.0882, 31);
  eq("statewide: an over-30 m fix fails closed",
     statewideCoarse && statewideCoarse.unrouted_reason, "location_uncertain");

  for (const [name, state, lat, lng] of [
    ["Panaji", "Goa", 15.4909, 73.8278],
    ["Belagavi", "Karnataka", 15.8497, 74.4977],
    ["Hyderabad", "Telangana", 17.3850, 78.4867],
  ]) {
    const outside = await route({
      city: name, state, country_code: "in", full: `${name}, ${state}, India`,
    }, lat, lng);
    eq(`state boundary: ${name} is not accepted`, outside, null);
  }
  const falseLabel = await route(geo("city", "Panaji", "North Goa District"),
    15.4909, 73.8278);
  eq("state boundary: a false Maharashtra geocoder label cannot override containment",
     falseLabel, null);

  const stateEdgePoint = firstBoundaryPoint(coverage.regions.maharashtra.geometry);
  const stateEdge = await P.maharashtraRouteFromGeocode(
    geo("village", "Maharashtra border", "Sindhudurg District"),
    stateEdgePoint[1], stateEdgePoint[0], 5);
  eq("state boundary: a GPS circle touching the state edge fails closed",
     stateEdge && stateEdge.unrouted_reason, "location_uncertain");

  return checks;
}
"""


def run_scenario(browser, route_override=None):
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    if route_override is not None:
        page.route(route_pattern("in-mh-routing"), route_override)
    page.goto(APP)
    page.wait_for_load_state("networkidle")
    page.wait_for_function(
        "typeof StandaloneAPI !== 'undefined' && StandaloneAPI.__pure",
        timeout=30000,
    )
    return context, page


def main():
    failures = []
    try:
        envelope, _ = read_pack("in-mh-routing")
        payload = read_payload("in-mh-routing")
        if not isinstance(envelope.get("adapter"), str) or not envelope["adapter"]:
            failures.append("Maharashtra routing pack has no adapter")
        if not isinstance(payload.get("regions"), dict):
            failures.append("Maharashtra routing payload has no regions object")
    except (AssertionError, KeyError, OSError, ValueError) as error:
        failures.append(f"Maharashtra routing pack pin is invalid: {error}")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=["--disable-web-security"])
        context, page = run_scenario(browser)
        results = page.evaluate(SCENARIO)
        context.close()

        for label, responder in [
            ("missing", lambda route: route.fulfill(status=404, body="missing")),
            ("malformed", lambda route: route.fulfill(
                status=200, content_type="application/json", body='{"not":"a routing pack"}'
            )),
        ]:
            context, page = run_scenario(browser, responder)
            reason = page.evaluate(
                """async () => (await StandaloneAPI.__pure.routeOfficer(
                  {city:'Thane',state:'Maharashtra',country_code:'in'},
                  19.2183,72.9781,12)).unrouted_reason"""
            )
            if reason != "jurisdiction_unavailable":
                failures.append(f"{label} Maharashtra routing pack did not fail closed: {reason}")
            context.close()
        browser.close()

    for name, passed, got, want in results:
        if not passed:
            failures.append(name)
            print(f"  FAIL {name}\n         got  {got}\n         want {want}")
    if failures:
        print(f"{len(failures)} of {len(results)} failed")
        sys.exit(1)
    print(f"MAHARASHTRA ROUTING TEST PASS ({len(results)} checks)")


main()
