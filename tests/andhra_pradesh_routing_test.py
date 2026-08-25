# -*- coding: utf-8 -*-
"""Andhra Pradesh routes statewide while old city routes and bad packs fail closed."""

from __future__ import annotations

import hashlib
import json

from playwright.sync_api import sync_playwright

from state_pack_utils import read_pack, read_payload, route_pattern


APP = "http://localhost:8765/"
PACK_ID = "in-ap-routing"
EXPECTED_GEOMETRY_SHA256 = (
    "4e36d9c16fda044dceab7a5b08955cb19046bb1bddd052b7671a8311e90cd71c"
)
EXPECTED_PACK_SHA256 = (
    "7532126b708aa1b2aa5d7c6be6480f433aedac6e8b28de7e14f539cf05131a77"
)
LEGACY_TOP50_SHA256 = (
    "0250e95980b7c801986a2bf025c82e4b8eb2745fe36dad09fc6dfb2a5a4f8bf5"
)
INSIDE = [
    {"name": "Visakhapatnam", "lat": 17.6935526, "lng": 83.2921297},
    {"name": "Vijayawada", "lat": 16.5115306, "lng": 80.6160469},
    # A non-top-50 point proves that the release covers the state, not a city list.
    {"name": "rural Prakasam", "lat": 15.4500, "lng": 79.1200},
]
OUTSIDE = [
    {"name": "Yanam", "lat": 16.7333, "lng": 82.2167},
    {"name": "Hyderabad", "lat": 17.3850, "lng": 78.4867},
    {"name": "Ballari", "lat": 15.1394, "lng": 76.9214},
    {"name": "Berhampur", "lat": 19.3149, "lng": 84.7941},
    {"name": "Chennai", "lat": 13.0827, "lng": 80.2707},
]


SCENARIO = r"""
async ({inside, outside, legacySha}) => {
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
  const resource = manifest && manifest.resources && manifest.resources["in-ap-routing"];
  const coverage = await P.andhraPradeshCoverage();
  const region = coverage && coverage.region;
  const pack = await P.loadStatePack("in-ap-routing");

  ok("pack: current manifest entry exists", resource, manifest);
  eq("pack: adapter is statewide and data-only", resource && resource.adapter,
     "statewide-general-v1");
  eq("pack: state code is Andhra Pradesh", resource && resource.state_code, "AP");
  eq("pack: full-state scope is explicit", resource && resource.statewide, true);
  eq("pack: content digest is pinned", resource && resource.sha256,
     "7532126b708aa1b2aa5d7c6be6480f433aedac6e8b28de7e14f539cf05131a77");
  eq("pack: content-addressed path is pinned", resource && resource.path,
     "packs/v1/states/ap/routing-7532126b708aa1b2aa5d7c6be6480f433aedac6e8b28de7e14f539cf05131a77.json");
  ok("coverage: pinned payload loads", region, coverage);
  eq("coverage: stable region ID", region && region.id, "andhra-pradesh-state");
  eq("coverage: stable authority ID", region && region.authority_id,
     "ap-statewide-unverified");
  eq("coverage: relation ID is pinned", region && region.osm_relation_id, 2022095);
  eq("coverage: geometry digest is pinned", region && region.geometry_sha256,
     P.ANDHRA_PRADESH_STATE_GEOMETRY_SHA256);
  ok("coverage: source attribution is explicit",
     /OpenStreetMap contributors/.test(region && region.source_name || "")
       && /ODbL/.test(region && region.source_license || ""), region);
  ok("coverage: scope excludes Yanam",
     /excludes Yanam, Puducherry Union Territory/i.test(region && region.scope || ""),
     region && region.scope);
  ok("coverage: limitations disclaim ownership and require user verification",
     (region && region.limitations || []).some((item) => /ownership is not inferred/i.test(item))
       && (region && region.limitations || []).some((item) => /user must select/i.test(item)),
     region && region.limitations);

  eq("registry: Karnataka and Kerala release is versioned",
     P.AUTHORITY_REGISTRY_VERSION, 17);
  eq("registry: stable statewide authority is installed",
     P.ANDHRA_PRADESH_STATE_AUTHORITY.id, "ap-statewide-unverified");
  eq("registry: primary official PGRS handoff",
     P.ANDHRA_PRADESH_STATE_AUTHORITY.handoff_url, "https://pgrs.ap.gov.in/");
  eq("registry: Puramithra remains the urban alternate",
     P.ANDHRA_PRADESH_STATE_AUTHORITY.alternate_handoff_url,
     "https://cdma.ap.gov.in/auth/login/");
  eq("registry: state helpline", P.ANDHRA_PRADESH_STATE_AUTHORITY.helpline, "1902");
  ok("registry: installed official routes pass structural validation",
     P.validateOfficialHandoffRegistry(P.OFFICIAL_AUTHORITIES), null);

  let saved = null;
  for (const fixture of inside) {
    const raw = await P.andhraPradeshRouteFromGeocode(
      null, fixture.lat, fixture.lng, 12);
    ok(`${fixture.name}: coordinates route without a geocoder`, raw && raw.routed, raw);
    eq(`${fixture.name}: neutral Andhra Pradesh authority`, raw && raw.authority_id,
       "ap-statewide-unverified");
    eq(`${fixture.name}: exact state-boundary evidence`, raw && [
      raw.routing_source, raw.routing_match_field, raw.routing_match_value, raw.region,
    ], [
      "osm_andhra_pradesh_state_boundary", "boundary",
      "Andhra Pradesh (OpenStreetMap relation 2022095)", "andhra-pradesh-state",
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
      "in-ap-routing", resource && resource.pack_version,
      resource && resource.sha256, "AP",
    ]);
    ok(`${fixture.name}: routing never claims submission`, absent(raw, [
      "official_grievance_id", "submitted_at", "sent_at", "handoff_opened_at",
    ]), raw);
    for (const issue of ["road_damage", "garbage", "open_manhole"]) {
      const typed = P.routeForIssue(raw, issue);
      eq(`${fixture.name}/${issue}: remains routable`, typed && typed.routed, true);
      eq(`${fixture.name}/${issue}: keeps Andhra Pradesh authority`,
         typed && typed.authority_id, "ap-statewide-unverified");
      eq(`${fixture.name}/${issue}: issue is explicit`, typed && typed.issue_type, issue);
      eq(`${fixture.name}/${issue}: never becomes tender-eligible`,
         typed && typed.tender_eligible, false);
      eq(`${fixture.name}/${issue}: never claims submission`,
         absent(typed, ["official_grievance_id", "submitted_at", "sent_at"]), true);
    }
    if (fixture.name === "Visakhapatnam") {
      saved = {...raw, ...fixture, gps_accuracy: 12, issue_type: "road_damage"};
    }
  }

  const staleAndhraPradesh = {
    city: "Visakhapatnam", state: "Andhra Pradesh", country_code: "in",
    full: "stale Andhra Pradesh address",
  };
  for (const fixture of outside) {
    const direct = await P.andhraPradeshRouteFromGeocode(
      staleAndhraPradesh, fixture.lat, fixture.lng, 12);
    eq(`${fixture.name}: stale state label cannot cross the polygon`, direct, null);
    const orchestrated = await P.routeOfficer(
      staleAndhraPradesh, fixture.lat, fixture.lng, 12, null, null, "garbage");
    ok(`${fixture.name}: routeOfficer never assigns Andhra Pradesh`,
       !orchestrated || orchestrated.authority_id !== "ap-statewide-unverified",
       orchestrated);
  }

  const atLimit = await P.andhraPradeshRouteFromGeocode(
    null, 17.6935526, 83.2921297, 30);
  eq("accuracy: 30 metres is accepted away from the state edge",
     atLimit && atLimit.authority_id, "ap-statewide-unverified");
  for (const [label, accuracy] of [
    ["31 metres", 31], ["negative", -1], ["not finite", Number.NaN],
  ]) {
    const uncertain = await P.andhraPradeshRouteFromGeocode(
      null, 17.6935526, 83.2921297, accuracy);
    eq(`accuracy: ${label} fails closed`,
       uncertain && uncertain.unrouted_reason, "location_uncertain");
  }
  const edgePoint = region.geometry.type === "Polygon"
    ? region.geometry.coordinates[0][0]
    : region.geometry.coordinates[0][0][0];
  const edge = await P.andhraPradeshRouteFromGeocode(
    staleAndhraPradesh, edgePoint[1], edgePoint[0], 5);
  eq("boundary: a five-metre circle touching the state edge fails closed",
     edge && edge.unrouted_reason, "location_uncertain");

  // This exact NH-16 fixture is already pinned by national_highway_routing_test.py.
  const highway = await P.routeOfficer(
    {state: "Andhra Pradesh", country_code: "in"},
    14.116915, 79.874755, 5, null, null, "road_damage");
  eq("precedence: NH-16 beats the Andhra Pradesh state handoff",
     highway && [highway.authority_id, highway.highway_ref],
     ["in-national-highway", "NH-16"]);
  const highwayGarbage = await P.routeOfficer(
    {state: "Andhra Pradesh", country_code: "in"},
    14.116915, 79.874755, 5, null, null, "garbage");
  eq("precedence: civic issues beside NH-16 stay with the state handoff",
     highwayGarbage && [highwayGarbage.authority_id, highwayGarbage.routing_pack_id],
     ["ap-statewide-unverified", "in-ap-routing"]);

  const visakhapatnam = await P.routeOfficer(
    {city: "Visakhapatnam", state: "Andhra Pradesh", country_code: "in"},
    17.6935526, 83.2921297, 12, null, null, "garbage");
  eq("precedence: Visakhapatnam uses state geometry before the legacy top-50 route",
     visakhapatnam && [visakhapatnam.authority_id, visakhapatnam.routing_pack_id],
     ["ap-statewide-unverified", "in-ap-routing"]);
  const vijayawada = await P.routeOfficer(
    {city: "Vijayawada", state: "Andhra Pradesh", country_code: "in"},
    16.5115306, 80.6160469, 12, null, null, "open_manhole");
  eq("precedence: Vijayawada uses state geometry before the legacy top-50 route",
     vijayawada && [vijayawada.authority_id, vijayawada.routing_pack_id],
     ["ap-statewide-unverified", "in-ap-routing"]);

  ok("saved binding: valid current Andhra Pradesh record is accepted",
     saved && await P.savedOfficialRouteBinding(
       saved, "in-ap-routing", "ap-statewide-unverified", pack), saved);
  const rejected = async (name, changes) => {
    const candidate = {...saved, ...changes};
    const binding = await P.savedOfficialRouteBinding(
      candidate, "in-ap-routing", "ap-statewide-unverified", pack);
    eq(name, binding, null);
  };
  await rejected("saved binding: cross-pack ID is rejected",
    {routing_pack_id: "in-top50-routing"});
  await rejected("saved binding: cross-state provenance is rejected",
    {routing_pack_state_code: "IN"});
  await rejected("saved binding: forged digest is rejected",
    {routing_pack_sha256: "0".repeat(64)});
  await rejected("saved binding: cross-region binding is rejected",
    {region: "visakhapatnam"});
  await rejected("saved binding: Yanam coordinates are rejected",
    {lat: 16.7333, lng: 82.2167});
  await rejected("saved binding: over-30m accuracy is rejected", {gps_accuracy: 31});
  await rejected("saved binding: changed source is rejected",
    {routing_source: "nominatim_structured_city"});
  await rejected("saved binding: changed boundary evidence is rejected",
    {routing_match_value: "Andhra Pradesh (OpenStreetMap relation 1)"});

  const legacyFixtures = [
    ["Visakhapatnam", "visakhapatnam", 17.6935526, 83.2921297],
    ["Vijayawada", "vijayawada", 16.5115306, 80.6160469],
  ];
  let legacy = null;
  for (const [city, legacyRegion, lat, lng] of legacyFixtures) {
    const candidate = {
      ...saved, lat, lng, gps_accuracy: 12, issue_type: "garbage",
      authority_id: "in-ap-puramithra",
      authority_name: "untrusted stale label",
      handoff_name: "untrusted stale handoff",
      handoff_url: "https://example.invalid/stale",
      alternate_handoff_url: "https://example.invalid/alternate",
      helpline: "0000",
      region: legacyRegion,
      routing_source: "nominatim_structured_city",
      routing_match_field: "structured_place",
      routing_match_value: `city: ${city}`,
      routing_pack_id: "in-top50-routing",
      routing_pack_version: 1,
      routing_pack_sha256: legacySha,
      routing_pack_state_code: "IN",
    };
    if (city === "Visakhapatnam") legacy = candidate;
    const migrated = await P.migrateLegacyAndhraPradeshHandoff(candidate);
    eq(`migration: legacy ${city} report receives the current statewide binding`,
       migrated && [migrated.authority_id, migrated.routing_pack_id, migrated.region],
       ["ap-statewide-unverified", "in-ap-routing", "andhra-pradesh-state"]);
    eq(`migration: ${city} stale URL is replaced by PGRS`,
       migrated && migrated.handoff_url, "https://pgrs.ap.gov.in/");
    eq(`migration: ${city} receives the reviewed Puramithra alternate`,
       migrated && migrated.alternate_handoff_url, "https://cdma.ap.gov.in/auth/login/");
    eq(`migration: ${city} receives helpline 1902`,
       migrated && migrated.helpline, "1902");
    eq(`migration: ${city} receives current pack provenance`,
       migrated && migrated.routing_pack_sha256, resource && resource.sha256);
    eq(`migration: ${city} never gains a submission claim`,
       absent(migrated, ["official_grievance_id", "submitted_at", "sent_at"]), true);
  }
  for (const [name, changes] of [
    ["forged old digest", {routing_pack_sha256: "0".repeat(64)}],
    ["wrong old authority", {authority_id: "ap-statewide-unverified"}],
    ["wrong old region", {region: "surat"}],
    ["wrong old city evidence", {routing_match_value: "city: Vijayawada"}],
    ["moved coordinates", {lat: 16.5115306, lng: 80.6160469}],
    ["coarse old GPS", {gps_accuracy: 31}],
  ]) {
    eq(`migration: ${name} is rejected`,
       await P.migrateLegacyAndhraPradeshHandoff({...legacy, ...changes}), null);
  }

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
        "&& typeof StandaloneAPI.__pure.andhraPradeshRouteFromGeocode === 'function'",
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
            failures.append(f"unexpected Andhra Pradesh adapter: {pack.get('adapter')!r}")
        if pack.get("state_code") != "AP" or pack.get("pack_id") != PACK_ID:
            failures.append("Andhra Pradesh hosted pack identity is invalid")
        if payload.get("retrieved_at") != "2026-08-24":
            failures.append("Andhra Pradesh retrieval date is not pinned")
        if actual_digest != EXPECTED_GEOMETRY_SHA256:
            failures.append(f"Andhra Pradesh geometry digest changed: {actual_digest}")
        if region.get("geometry_sha256") != EXPECTED_GEOMETRY_SHA256:
            failures.append(
                "Andhra Pradesh payload does not record the pinned geometry digest"
            )
        if hashlib.sha256(raw).hexdigest() != EXPECTED_PACK_SHA256:
            failures.append("Andhra Pradesh hosted pack digest changed")
    except (AssertionError, KeyError, OSError, TypeError, ValueError) as error:
        failures.append(f"Andhra Pradesh routing pack pin is invalid: {error}")

    if not raw:
        print("FAIL")
        for failure in failures:
            print("  -", failure)
        raise SystemExit(1)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--disable-web-security"])
        context, page = open_page(browser)
        results = page.evaluate(
            SCENARIO,
            {"inside": INSIDE, "outside": OUTSIDE, "legacySha": LEGACY_TOP50_SHA256},
        )
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
                    body=raw.replace(b"Andhra Pradesh", b"Xndhra Pradesh", 1),
                ),
            ),
        ]:
            context, page = open_page(browser, responder)
            failed_routes = page.evaluate(
                """async () => {
                  const P = StandaloneAPI.__pure;
                  const direct = await P.andhraPradeshRouteFromGeocode(
                    null,15.45,79.12,12);
                  const rural = await P.routeOfficer(
                    {state:'Andhra Pradesh',country_code:'in'},
                    15.45,79.12,12,null,null,'garbage');
                  const legacyCity = await P.routeOfficer(
                    {city:'Visakhapatnam',state:'Andhra Pradesh',country_code:'in'},
                    17.6935526,83.2921297,12,null,null,'garbage');
                  return {direct, rural, legacyCity};
                }"""
            )
            for route_name, result in failed_routes.items():
                if not isinstance(result, dict) or result.get("routed") is not False \
                        or result.get("unrouted_reason") != "jurisdiction_unavailable":
                    failures.append(
                        f"{label} Andhra Pradesh pack allowed {route_name}: {result!r}"
                    )
            extra_checks += len(failed_routes)
            context.close()
        browser.close()

    if failures:
        print("FAIL")
        for failure in failures:
            print("  -", failure)
        raise SystemExit(1)
    print(
        f"ANDHRA PRADESH ROUTING TEST PASS "
        f"({len(results) + extra_checks + 5} checks)"
    )


if __name__ == "__main__":
    main()
