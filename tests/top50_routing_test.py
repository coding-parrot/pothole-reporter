# -*- coding: utf-8 -*-
"""The 35 compatibility top-50 entries require exact, precise, neutral city routing."""

from __future__ import annotations

import json
import math
import sys

from playwright.sync_api import sync_playwright

from state_pack_utils import read_pack, read_payload, route_pattern


APP = "http://localhost:8765/"
PACK_ID = "in-top50-routing"
ISSUES = {"road_damage", "garbage", "open_manhole"}

STATE = {
    "GJ": ("Gujarat", "in-gj-enagar"),
    "RJ": ("Rajasthan", "in-rj-sampark"),
    "UP": ("Uttar Pradesh", "in-up-jansunwai"),
    "MP": ("Madhya Pradesh", "in-mp-cm-helpline"),
    "TN": ("Tamil Nadu", "in-tn-cm-helpline"),
    "KL": ("Kerala", "in-kl-ksmart"),
    "BR": ("Bihar", "in-br-lok-shikayat"),
    "AP": ("Andhra Pradesh", "in-ap-puramithra"),
    "HR": ("Haryana", "in-hr-nagar-darshan"),
    "JH": ("Jharkhand", "in-jh-municipal-grievance"),
    "JK": ("Jammu and Kashmir", "in-jk-samadhan"),
    "CG": ("Chhattisgarh", "in-cg-nidaan"),
}

# Independent reviewed coordinates. They are the pinned Nominatim objects' centres,
# not values derived from the generated pack during the test.
CITY_ROWS = [
    (9, "surat", "Surat", "GJ", 21.2094892, 72.8317058),
    (10, "jaipur", "Jaipur", "RJ", 26.9154576, 75.8189817),
    (11, "kanpur", "Kanpur", "UP", 26.4609135, 80.3217588),
    (12, "lucknow", "Lucknow", "UP", 26.8381, 80.9346001),
    (14, "ghaziabad", "Ghaziabad", "UP", 28.6711527, 77.4120356),
    (15, "indore", "Indore", "MP", 22.7203616, 75.8681996),
    (16, "coimbatore", "Coimbatore", "TN", 11.0018115, 76.9628425),
    (17, "kochi", "Kochi", "KL", 9.9679032, 76.2444378),
    (18, "patna", "Patna", "BR", 25.6093239, 85.1235252),
    (19, "kozhikode", "Kozhikode", "KL", 11.2450558, 75.7754716),
    (20, "bhopal", "Bhopal", "MP", 23.2584857, 77.401989),
    (21, "thrissur", "Thrissur", "KL", 10.5270099, 76.214621),
    (22, "vadodara", "Vadodara", "GJ", 22.2973142, 73.1942567),
    (23, "agra", "Agra", "UP", 27.1752554, 78.0098161),
    (24, "visakhapatnam", "Visakhapatnam", "AP", 17.6935526, 83.2921297),
    (25, "malappuram", "Malappuram", "KL", 11.0428925, 76.0807838),
    (26, "thiruvananthapuram", "Thiruvananthapuram", "KL", 8.4882267, 76.947551),
    (27, "kannur", "Kannur", "KL", 11.8763836, 75.3737973),
    (30, "vijayawada", "Vijayawada", "AP", 16.5115306, 80.6160469),
    (31, "madurai", "Madurai", "TN", 9.9261153, 78.1140983),
    (32, "varanasi", "Varanasi", "UP", 25.3356491, 83.0076292),
    (33, "meerut", "Meerut", "UP", 28.9963296, 77.7061915),
    (34, "faridabad", "Faridabad", "HR", 28.4031478, 77.3105561),
    (35, "rajkot", "Rajkot", "GJ", 22.3053263, 70.8028377),
    (36, "jamshedpur", "Jamshedpur", "JH", 22.8015194, 86.2029579),
    (37, "jabalpur", "Jabalpur", "MP", 23.1701522, 79.9324505),
    (38, "srinagar", "Srinagar", "JK", 34.0747444, 74.8204443),
    (41, "prayagraj", "Prayagraj", "UP", 25.4381302, 81.8338005),
    (42, "dhanbad", "Dhanbad", "JH", 23.7952809, 86.4309638),
    (45, "jodhpur", "Jodhpur", "RJ", 26.2967719, 73.0351433),
    (46, "ranchi", "Ranchi", "JH", 23.3700501, 85.3250387),
    (47, "raipur", "Raipur", "CG", 21.2380912, 81.6336993),
    (48, "kollam", "Kollam", "KL", 8.8870533, 76.5906696),
    (49, "gwalior", "Gwalior", "MP", 26.2037247, 78.1573628),
    (50, "durg-bhilai", "Durg-Bhilai", "CG", 21.2120677, 81.3732849),
]


def fixtures() -> list[dict]:
    return [
        {
            "rank": rank,
            "id": city_id,
            "name": name,
            "state_code": state_code,
            "state": STATE[state_code][0],
            "authority_id": STATE[state_code][1],
            "lat": lat,
            "lng": lng,
        }
        for rank, city_id, name, state_code, lat, lng in CITY_ROWS
    ]


SCENARIO = r"""
async ({fixtures}) => {
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
  const resource = manifest && manifest.resources && manifest.resources["in-top50-routing"];
  const coverage = await P.majorCityCoverage();
  const pack = await P.loadStatePack("in-top50-routing");
  const regionById = new Map((coverage && coverage.regions || [])
    .map((region) => [region.id, region]));

  ok("pack: manifest entry exists", resource, manifest);
  eq("pack: structured adapter is pinned", resource && resource.adapter,
     "major-city-structured-v1");
  eq("pack: country-level provenance is explicit", resource && resource.state_code, "IN");
  eq("pack: does not claim statewide coverage", resource && resource.statewide, false);
  eq("pack: v1.25 digest is preserved for saved-report compatibility",
     resource && resource.sha256,
     "0250e95980b7c801986a2bf025c82e4b8eb2745fe36dad09fc6dfb2a5a4f8bf5");
  eq("coverage: exactly 35 compatibility entries", regionById.size, 35);
  ok("coverage: runtime validates the complete pack", P.validateMajorCityPayload(pack), pack);
  eq("coverage: all expected ranks are present",
     [...regionById.values()].map((item) => item.rank).sort((a, b) => a - b),
     fixtures.map((item) => item.rank).sort((a, b) => a - b));

  let savedSurat = null;
  for (const fixture of fixtures) {
    const region = regionById.get(fixture.id);
    const geo = {
      city: fixture.name, state: fixture.state, country_code: "in",
      full: `${fixture.name}, ${fixture.state}, India`,
    };
    ok(`${fixture.id}: region exists`, region, fixture);
    eq(`${fixture.id}: identity is pinned`, region && [
      region.rank, region.name, region.state_code, region.authority_id,
    ], [fixture.rank, fixture.name, fixture.state_code, fixture.authority_id]);
    eq(`${fixture.id}: matching mode is structured only`, region && [
      region.routing_mode, region.routing_source,
    ], ["structured_geocode", "nominatim_structured_city"]);
    ok(`${fixture.id}: source object is explicit`,
       region && /^OpenStreetMap (node|way|relation) [1-9][0-9]*$/.test(region.match_value),
       region && region.match_value);
    ok(`${fixture.id}: no municipal-boundary or UA claim`,
       /no municipal-boundary or whole-urban-agglomeration claim/i.test(region && region.scope || ""),
       region && region.scope);
    eq(`${fixture.id}: all three issue types are reviewed`,
       [...(region && region.supported_issue_types || [])].sort(),
       ["garbage", "open_manhole", "road_damage"]);

    const raw = await P.majorCityRouteFromGeocode(
      geo, fixture.lat, fixture.lng, 12);
    eq(`${fixture.id}: exact structured city/state routes`, raw && raw.routed, true);
    eq(`${fixture.id}: selected region and neutral authority`, raw && [
      raw.region, raw.authority_id,
    ], [fixture.id, fixture.authority_id]);
    eq(`${fixture.id}: city field is recorded exactly`, raw && [
      raw.routing_source, raw.routing_match_field, raw.routing_match_value,
    ], ["nominatim_structured_city", "structured_place", `city: ${fixture.name}`]);
    eq(`${fixture.id}: official handoff has no guessed email`, raw && [
      raw.delivery_channel, raw.officer_email,
    ], ["official_handoff", null]);
    eq(`${fixture.id}: neutral ownership and contract flags`, raw && [
      raw.ownership_unverified, raw.requires_official_reference, raw.tender_eligible,
    ], [true, true, false]);
    eq(`${fixture.id}: full current pack provenance`, raw && [
      raw.routing_pack_id, raw.routing_pack_version, raw.routing_pack_sha256,
      raw.routing_pack_state_code,
    ], [
      "in-top50-routing", resource && resource.pack_version,
      resource && resource.sha256, "IN",
    ]);
    ok(`${fixture.id}: route never claims a contract or submission`,
       absent(raw, [
         "tender_id", "tender_number", "contractor", "warranty",
         "official_grievance_id", "submitted_at", "sent_at", "handoff_opened_at",
       ]), raw);

    for (const issue of ["road_damage", "garbage", "open_manhole"]) {
      const typed = P.routeForIssue(raw, issue);
      eq(`${fixture.id}/${issue}: route remains available`, typed && typed.routed, true);
      eq(`${fixture.id}/${issue}: neutral authority is retained`,
         typed && typed.authority_id, fixture.authority_id);
      eq(`${fixture.id}/${issue}: issue type is explicit`, typed && typed.issue_type, issue);
      eq(`${fixture.id}/${issue}: no tender becomes eligible`,
         typed && typed.tender_eligible, false);
    }

    // Either supported structured civic field can carry the exact reviewed name.
    const municipality = await P.majorCityRouteFromGeocode({
      municipality: fixture.name, state: fixture.state, country_code: "in",
    }, fixture.lat, fixture.lng, 12);
    eq(`${fixture.id}: exact municipality field routes`,
       municipality && municipality.routing_match_value, `municipality: ${fixture.name}`);

    // Every reviewed alias must work exactly; prefix, partial, stale, country and state
    // guesses must not. This deliberately exercises multilingual aliases as data.
    for (const alias of region.place_aliases) {
      const aliasRoute = await P.majorCityRouteFromGeocode({
        city: alias, state: region.state_aliases[0], country_code: "in",
      }, fixture.lat, fixture.lng, 12);
      eq(`${fixture.id}: reviewed place alias ${alias} routes`,
         aliasRoute && aliasRoute.authority_id, fixture.authority_id);
    }
    for (const stateAlias of region.state_aliases) {
      const aliasRoute = await P.majorCityRouteFromGeocode({
        city: region.place_aliases[0], state: stateAlias, country_code: "in",
      }, fixture.lat, fixture.lng, 12);
      eq(`${fixture.id}: reviewed state alias ${stateAlias} routes`,
         aliasRoute && aliasRoute.authority_id, fixture.authority_id);
    }
    eq(`${fixture.id}: null geocoder fails closed`,
       await P.majorCityRouteFromGeocode(null, fixture.lat, fixture.lng, 12), null);
    eq(`${fixture.id}: wrong state fails closed`,
       await P.majorCityRouteFromGeocode({
         city: fixture.name, state: "Punjab", country_code: "in",
       }, fixture.lat, fixture.lng, 12), null);
    eq(`${fixture.id}: wrong country fails closed`,
       await P.majorCityRouteFromGeocode({
         city: fixture.name, state: fixture.state, country_code: "pk",
       }, fixture.lat, fixture.lng, 12), null);
    eq(`${fixture.id}: partial city name fails closed`,
       await P.majorCityRouteFromGeocode({
         city: `${fixture.name} East`, state: fixture.state, country_code: "in",
       }, fixture.lat, fixture.lng, 12), null);
    eq(`${fixture.id}: one stale structured civic field vetoes another`,
       await P.majorCityRouteFromGeocode({
         city: fixture.name, municipality: "Stale Municipality",
         state: fixture.state, country_code: "in",
       }, fixture.lat, fixture.lng, 12), null);

    const outsideLng = region.envelope.max_lng + 0.005;
    eq(`${fixture.id}: city name outside the pinned envelope fails closed`,
       await P.majorCityRouteFromGeocode(
         geo, fixture.lat, outsideLng, 12), null);

    const atLimit = await P.majorCityRouteFromGeocode(
      geo, fixture.lat, fixture.lng, 30);
    eq(`${fixture.id}: 30-metre fix is accepted away from the edge`,
       atLimit && atLimit.authority_id, fixture.authority_id);
    const coarse = await P.majorCityRouteFromGeocode(
      geo, fixture.lat, fixture.lng, 31);
    eq(`${fixture.id}: over-30-metre fix fails closed`,
       coarse && coarse.unrouted_reason, "location_uncertain");
    const invalid = await P.majorCityRouteFromGeocode(
      geo, fixture.lat, fixture.lng, -1);
    eq(`${fixture.id}: impossible negative accuracy fails closed`,
       invalid && invalid.unrouted_reason, "location_uncertain");

    const edgeLat = (region.envelope.min_lat + region.envelope.max_lat) / 2;
    const fiveMetresLng = 5 / (111320 * Math.cos(edgeLat * Math.PI / 180));
    const nearEdgeLng = region.envelope.min_lng + fiveMetresLng;
    eq(`${fixture.id}: centre accuracy circle is wholly inside the envelope`,
       P.accuracyCircleWithinEnvelope(
         fixture.lat, fixture.lng, 12, region.envelope), true);
    eq(`${fixture.id}: near-edge accuracy circle is not wholly inside`,
       P.accuracyCircleWithinEnvelope(edgeLat, nearEdgeLng, 12, region.envelope), false);
    const edge = await P.majorCityRouteFromGeocode(
      geo, edgeLat, nearEdgeLng, 12);
    eq(`${fixture.id}: accuracy circle touching envelope edge fails closed`,
       edge && edge.unrouted_reason, "location_uncertain");

    if (fixture.id === "surat") {
      savedSurat = {...raw, lat: fixture.lat, lng: fixture.lng,
        gps_accuracy: 12, issue_type: "road_damage"};
    }
  }

  // Regression matrix for the two former coarse-envelope blockers. Delhi must fall
  // through to Ghaziabad/Faridabad; West Bengal must fall through to Jharkhand.
  for (const [name, geo, lat, lng, authority, region] of [
    ["Ghaziabad beside Delhi", {city:"Ghaziabad",state:"Uttar Pradesh",country_code:"in"},
      28.6711527,77.4120356,"in-up-jansunwai","ghaziabad"],
    ["Faridabad beside Delhi", {city:"Faridabad",state:"Haryana",country_code:"in"},
      28.4031478,77.3105561,"in-hr-nagar-darshan","faridabad"],
    ["Jamshedpur beside West Bengal", {city:"Jamshedpur",state:"Jharkhand",country_code:"in"},
      22.8015194,86.2029579,"in-jh-municipal-grievance","jamshedpur"],
    ["Dhanbad beside West Bengal", {city:"Dhanbad",state:"Jharkhand",country_code:"in"},
      23.7952809,86.4309638,"in-jh-municipal-grievance","dhanbad"],
  ]) {
    const route = await P.routeOfficer(geo, lat, lng, 12, null, null, "garbage");
    eq(`collision: ${name} reaches its intended authority`,
       route && route.authority_id, authority);
    eq(`collision: ${name} retains its intended region`, route && route.region, region);
  }
  const delhi = await P.delhiRouteFromGeocode(
    {city:"Delhi",state:"Delhi",country_code:"in"}, 28.6129,77.2295,12);
  eq("collision: actual Delhi remains on its exact NCT route",
     delhi && delhi.authority_id, "dl-pwd-sewa");
  const kolkata = await P.kolkataRouteFromGeocode(
    {city:"Kolkata",state:"West Bengal",country_code:"in"}, 22.5726,88.3639,12);
  eq("collision: actual Kolkata remains on KMC containment",
     kolkata && kolkata.authority_id, "wb-kmc");

  // This coordinate is an exact checked-in NH-48 segment inside Surat's search
  // envelope. Road damage goes national first; garbage still uses the civic handoff.
  const suratGeo = {city:"Surat",state:"Gujarat",country_code:"in"};
  const highway = await P.routeOfficer(
    suratGeo,21.27766,72.95218,5,null,null,"road_damage");
  eq("precedence: mapped NH-48 beats the Surat handoff",
     highway && highway.authority_id, "in-national-highway");
  eq("precedence: NH reference is retained", highway && highway.highway_ref, "NH-48");
  const civicAtHighway = await P.routeOfficer(
    suratGeo,21.27766,72.95218,5,null,null,"garbage");
  eq("precedence: non-road civic report stays with Gujarat",
     civicAtHighway && civicAtHighway.authority_id, "in-gj-enagar");

  ok("saved binding: valid v1.25 Surat record remains accepted",
     savedSurat && await P.savedOfficialRouteBinding(
       savedSurat, "in-top50-routing", "in-gj-enagar", pack), savedSurat);
  const rejected = async (name, changes, authority = "in-gj-enagar") => {
    const candidate = {...savedSurat, ...changes};
    const binding = await P.savedOfficialRouteBinding(
      candidate, "in-top50-routing", authority, pack);
    eq(name, binding, null);
  };
  await rejected("saved binding: cross-pack ID is rejected",
    {routing_pack_id: "in-pb-routing"});
  await rejected("saved binding: cross-pack digest is rejected",
    {routing_pack_sha256: manifest.resources["in-pb-routing"].sha256});
  await rejected("saved binding: wrong provenance state is rejected",
    {routing_pack_state_code: "GJ"});
  await rejected("saved binding: forged digest is rejected",
    {routing_pack_sha256: "0".repeat(64)});
  await rejected("saved binding: same-authority cross-region is rejected",
    {region: "vadodara"});
  await rejected("saved binding: another same-authority alias is rejected",
    {routing_match_value: "city: Vadodara"});
  await rejected("saved binding: coordinates from another region are rejected",
    {lat: 22.2973142, lng: 73.1942567});
  await rejected("saved binding: another authority is rejected",
    {authority_id: "in-rj-sampark"});
  await rejected("saved binding: wrong routing source is rejected",
    {routing_source: "openstreetmap_structured"});
  await rejected("saved binding: wrong match field is rejected",
    {routing_match_field: "boundary"});
  await rejected("saved binding: over-30m accuracy is rejected", {gps_accuracy: 31});
  const partial = {...savedSurat};
  delete partial.routing_pack_sha256;
  eq("saved binding: partial provenance is rejected",
     await P.savedOfficialRouteBinding(
       partial, "in-top50-routing", "in-gj-enagar", pack), null);

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
        "&& typeof StandaloneAPI.__pure.majorCityRouteFromGeocode === 'function'",
        timeout=30_000,
    )
    return context, page


def main() -> None:
    failures: list[str] = []
    raw = b""
    expected = fixtures()
    try:
        envelope, raw = read_pack(PACK_ID)
        payload = read_payload(PACK_ID)
        regions = payload.get("regions")
        if envelope.get("adapter") != "major-city-structured-v1":
            failures.append(f"unexpected top-50 adapter: {envelope.get('adapter')!r}")
        if payload.get("retrieved_at") != "2026-08-24":
            failures.append("top-50 retrieval date is not pinned")
        if not isinstance(regions, list) or len(regions) != 35:
            failures.append(f"top-50 pack has {len(regions or [])} regions, expected 35")
            regions = []
        actual_identity = {
            (item.get("rank"), item.get("id"), item.get("name"), item.get("state_code"))
            for item in regions
            if isinstance(item, dict)
        }
        expected_identity = {
            (item["rank"], item["id"], item["name"], item["state_code"])
            for item in expected
        }
        if actual_identity != expected_identity:
            failures.append("top-50 pack identity inventory differs from the reviewed 35")
        for item in expected:
            region = next((entry for entry in regions if entry.get("id") == item["id"]), None)
            if not region:
                continue
            bounds = region.get("envelope") or {}
            if not (
                bounds.get("min_lat", math.inf) < item["lat"] < bounds.get("max_lat", -math.inf)
                and bounds.get("min_lng", math.inf) < item["lng"] < bounds.get("max_lng", -math.inf)
            ):
                failures.append(f"{item['id']} reviewed centre left its pinned envelope")
            if set(region.get("supported_issue_types") or []) != ISSUES:
                failures.append(f"{item['id']} does not declare all three issue types")
            if region.get("authority_id") != item["authority_id"]:
                failures.append(f"{item['id']} changed its neutral authority")
            if "ODbL" not in str(region.get("source_license")):
                failures.append(f"{item['id']} has no ODbL source licence")
    except (AssertionError, KeyError, OSError, TypeError, ValueError) as error:
        failures.append(f"top-50 routing pack pin is invalid: {error}")

    if not raw:
        print("FAIL")
        for failure in failures:
            print("  -", failure)
        raise SystemExit(1)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--disable-web-security"])
        context, page = open_page(browser)
        results = page.evaluate(SCENARIO, {"fixtures": expected})
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
                    body=raw.replace(b"Surat", b"Sxrat", 1),
                ),
            ),
        ]:
            context, page = open_page(browser, responder)
            result = page.evaluate(
                """async () => StandaloneAPI.__pure.majorCityRouteFromGeocode(
                  {city:'Surat',state:'Gujarat',country_code:'in'},
                  21.2094892,72.8317058,12)"""
            )
            if result.get("routed") is not False or result.get("unrouted_reason") != "jurisdiction_unavailable":
                failures.append(f"{label} top-50 pack did not fail closed: {result!r}")
            context.close()
        browser.close()

    if failures:
        print("FAIL")
        for failure in failures:
            print("  -", failure)
        raise SystemExit(1)
    print(f"TOP-50 ROUTING TEST PASS ({len(results) + 2} checks)")


if __name__ == "__main__":
    main()
