# -*- coding: utf-8 -*-
"""Tamil Nadu routes by its state polygon while GCC and National Highways win."""

from __future__ import annotations

import hashlib
import json

from playwright.sync_api import sync_playwright

from state_pack_utils import read_pack, read_payload, route_pattern


APP = "http://localhost:8765/"
PACK_ID = "in-tn-state-routing"
EXPECTED_GEOMETRY_SHA256 = (
    "b3034527326b1120366adaf4b7c3df4bd0b8c7aab4d82b28e3dde189b39c313e"
)
EXPECTED_PACK_SHA256 = (
    "5933d7ff31dcc2c6bd34849c7f4349c71b713bdf9f06f5a4f00733058fe8dcd9"
)
LEGACY_TOP50_SHA256 = (
    "0250e95980b7c801986a2bf025c82e4b8eb2745fe36dad09fc6dfb2a5a4f8bf5"
)
INSIDE = [
    {"name": "Coimbatore", "lat": 11.0018115, "lng": 76.9628425},
    {"name": "Madurai", "lat": 9.9261153, "lng": 78.1140983},
    {"name": "Tiruchirappalli", "lat": 10.7905, "lng": 78.7047},
    # A non-top-50 fixture proves that coverage is not a city-name list.
    {"name": "Erode", "lat": 11.3410, "lng": 77.7172},
]
OUTSIDE = [
    {"name": "Puducherry", "lat": 11.9416, "lng": 79.8083},
    {"name": "Karaikal", "lat": 10.9254, "lng": 79.8380},
    {"name": "Bengaluru", "lat": 12.9716, "lng": 77.5946},
    {"name": "Palakkad", "lat": 10.7867, "lng": 76.6548},
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
  const resource = manifest && manifest.resources && manifest.resources["in-tn-state-routing"];
  const coverage = await P.tamilNaduCoverage();
  const region = coverage && coverage.region;
  const pack = await P.loadStatePack("in-tn-state-routing");

  ok("pack: manifest entry exists", resource, manifest);
  eq("pack: adapter is statewide and data-only", resource && resource.adapter,
     "statewide-general-v1");
  eq("pack: state code is Tamil Nadu", resource && resource.state_code, "TN");
  eq("pack: full-state scope is explicit", resource && resource.statewide, true);
  eq("pack: content digest is pinned", resource && resource.sha256,
     "5933d7ff31dcc2c6bd34849c7f4349c71b713bdf9f06f5a4f00733058fe8dcd9");
  ok("coverage: pinned payload loads", region, coverage);
  eq("coverage: stable region ID", region && region.id, "tamil-nadu-state");
  eq("coverage: stable authority ID", region && region.authority_id,
     "tn-statewide-unverified");
  eq("coverage: relation ID is pinned", region && region.osm_relation_id, 96905);
  eq("coverage: geometry digest is pinned", region && region.geometry_sha256,
     P.TAMIL_NADU_STATE_GEOMETRY_SHA256);
  ok("coverage: source attribution is explicit",
     /OpenStreetMap contributors/.test(region && region.source_name || "")
       && /ODbL/.test(region && region.source_license || ""), region);
  ok("coverage: scope excludes Puducherry",
     /excludes Puducherry Union Territory enclaves/i.test(region && region.scope || ""),
     region && region.scope);
  ok("coverage: limitations disclaim ownership and require user verification",
     (region && region.limitations || []).some((item) => /ownership is not inferred/i.test(item))
       && (region && region.limitations || []).some((item) => /user must select/i.test(item)),
     region && region.limitations);

  eq("registry: Tamil Nadu statewide release is versioned",
     P.AUTHORITY_REGISTRY_VERSION, 12);
  eq("registry: stable statewide authority is installed",
     P.TAMIL_NADU_STATE_AUTHORITY.id, "tn-statewide-unverified");
  eq("registry: primary official handoff",
     P.TAMIL_NADU_STATE_AUTHORITY.handoff_url,
     "https://cmhelpline.tnega.org/portal/en/home");
  eq("registry: official Android package",
     P.TAMIL_NADU_STATE_AUTHORITY.handoff_package,
     "org.tnega.cmhelpline.citizen");
  ok("registry: no municipality-scoped alternate is guessed",
     !P.TAMIL_NADU_STATE_AUTHORITY.alternate_handoff_url,
     P.TAMIL_NADU_STATE_AUTHORITY);
  eq("registry: state helpline", P.TAMIL_NADU_STATE_AUTHORITY.helpline, "1100");
  ok("registry: installed official routes pass structural validation",
     P.validateOfficialHandoffRegistry(P.OFFICIAL_AUTHORITIES), null);

  let saved = null;
  for (const fixture of inside) {
    const raw = await P.tamilNaduRouteFromGeocode(null, fixture.lat, fixture.lng, 12);
    ok(`${fixture.name}: coordinates route without a geocoder`, raw && raw.routed, raw);
    eq(`${fixture.name}: neutral Tamil Nadu authority`, raw && raw.authority_id,
       "tn-statewide-unverified");
    eq(`${fixture.name}: exact state-boundary evidence`, raw && [
      raw.routing_source, raw.routing_match_field, raw.routing_match_value, raw.region,
    ], [
      "osm_tamil_nadu_state_boundary", "boundary",
      "Tamil Nadu (OpenStreetMap relation 96905)", "tamil-nadu-state",
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
      "in-tn-state-routing", resource && resource.pack_version,
      resource && resource.sha256, "TN",
    ]);
    ok(`${fixture.name}: routing never claims submission`, absent(raw, [
      "official_grievance_id", "submitted_at", "sent_at", "handoff_opened_at",
    ]), raw);
    for (const issue of ["road_damage", "garbage", "open_manhole"]) {
      const typed = P.routeForIssue(raw, issue);
      eq(`${fixture.name}/${issue}: remains routable`, typed && typed.routed, true);
      eq(`${fixture.name}/${issue}: keeps Tamil Nadu authority`,
         typed && typed.authority_id, "tn-statewide-unverified");
      eq(`${fixture.name}/${issue}: issue is explicit`, typed && typed.issue_type, issue);
      eq(`${fixture.name}/${issue}: never becomes tender-eligible`,
         typed && typed.tender_eligible, false);
    }
    if (fixture.name === "Coimbatore") {
      saved = {...raw, ...fixture, gps_accuracy: 12, issue_type: "road_damage"};
    }
  }

  const staleTamilNadu = {
    city: "Coimbatore", state: "Tamil Nadu", country_code: "in",
    full: "stale Tamil Nadu address",
  };
  for (const fixture of outside) {
    const direct = await P.tamilNaduRouteFromGeocode(
      staleTamilNadu, fixture.lat, fixture.lng, 12);
    eq(`${fixture.name}: stale Tamil Nadu label cannot cross the polygon`, direct, null);
    const orchestrated = await P.routeOfficer(
      staleTamilNadu, fixture.lat, fixture.lng, 12, null, null, "garbage");
    ok(`${fixture.name}: routeOfficer never assigns Tamil Nadu`,
       !orchestrated || orchestrated.authority_id !== "tn-statewide-unverified",
       orchestrated);
  }

  const atLimit = await P.tamilNaduRouteFromGeocode(null, 11.0018115, 76.9628425, 30);
  eq("accuracy: 30 metres is accepted away from the state edge",
     atLimit && atLimit.authority_id, "tn-statewide-unverified");
  for (const [label, accuracy] of [
    ["31 metres", 31], ["negative", -1], ["not finite", Number.NaN],
  ]) {
    const uncertain = await P.tamilNaduRouteFromGeocode(
      null, 11.0018115, 76.9628425, accuracy);
    eq(`accuracy: ${label} fails closed`,
       uncertain && uncertain.unrouted_reason, "location_uncertain");
  }
  const edgePoint = region.geometry.type === "Polygon"
    ? region.geometry.coordinates[0][0]
    : region.geometry.coordinates[0][0][0];
  const edge = await P.tamilNaduRouteFromGeocode(
    staleTamilNadu, edgePoint[1], edgePoint[0], 5);
  eq("boundary: a five-metre circle touching the state edge fails closed",
     edge && edge.unrouted_reason, "location_uncertain");

  // This checked-in NH-138 coordinate is inside Tamil Nadu. Road damage must route
  // nationally before the state-neutral fallback; civic categories stay statewide.
  const highway = await P.routeOfficer(
    {city: "Tirunelveli", state: "Tamil Nadu", country_code: "in"},
    8.73471, 77.9895, 5, null, null, "road_damage");
  eq("precedence: NH-138 beats the Tamil Nadu state handoff",
     highway && [highway.authority_id, highway.highway_ref],
     ["in-national-highway", "NH-138"]);
  const highwayGarbage = await P.routeOfficer(
    {city: "Tirunelveli", state: "Tamil Nadu", country_code: "in"},
    8.73471, 77.9895, 5, null, null, "garbage");
  eq("precedence: civic issues beside NH-138 stay with the state handoff",
     highwayGarbage && [highwayGarbage.authority_id, highwayGarbage.routing_pack_id],
     ["tn-statewide-unverified", "in-tn-state-routing"]);

  // The exact GCC route must remain more specific than the statewide fallback.
  const chennaiGeo = {city: "Chennai", state: "Tamil Nadu", country_code: "in"};
  const chennai = await P.routeOfficer(
    chennaiGeo, 13.0827, 80.2707, 12, null, null, "garbage");
  eq("precedence: Greater Chennai Corporation beats statewide Tamil Nadu",
     chennai && [chennai.authority_id, chennai.routing_pack_id],
     ["tn-gcc", "in-tn-routing"]);
  const coimbatore = await P.routeOfficer(
    {city: "Coimbatore", state: "Tamil Nadu", country_code: "in"},
    11.0018115, 76.9628425, 12, null, null, "garbage");
  eq("precedence: Coimbatore now uses statewide geometry before old top-50 matching",
     coimbatore && [coimbatore.authority_id, coimbatore.routing_pack_id],
     ["tn-statewide-unverified", "in-tn-state-routing"]);
  const madurai = await P.routeOfficer(
    {city: "Madurai", state: "Tamil Nadu", country_code: "in"},
    9.9261153, 78.1140983, 12, null, null, "open_manhole");
  eq("precedence: Madurai now uses statewide geometry before old top-50 matching",
     madurai && [madurai.authority_id, madurai.routing_pack_id],
     ["tn-statewide-unverified", "in-tn-state-routing"]);

  ok("saved binding: valid current Tamil Nadu record is accepted",
     saved && await P.savedOfficialRouteBinding(
       saved, "in-tn-state-routing", "tn-statewide-unverified", pack), saved);
  const rejected = async (name, changes) => {
    const candidate = {...saved, ...changes};
    const binding = await P.savedOfficialRouteBinding(
      candidate, "in-tn-state-routing", "tn-statewide-unverified", pack);
    eq(name, binding, null);
  };
  await rejected("saved binding: cross-pack ID is rejected",
    {routing_pack_id: "in-top50-routing"});
  await rejected("saved binding: cross-state provenance is rejected",
    {routing_pack_state_code: "IN"});
  await rejected("saved binding: forged digest is rejected",
    {routing_pack_sha256: "0".repeat(64)});
  await rejected("saved binding: cross-region binding is rejected", {region: "coimbatore"});
  await rejected("saved binding: Puducherry coordinates are rejected",
    {lat: 11.9416, lng: 79.8083});
  await rejected("saved binding: over-30m accuracy is rejected", {gps_accuracy: 31});
  await rejected("saved binding: changed boundary evidence is rejected",
    {routing_match_value: "Tamil Nadu (OpenStreetMap relation 1)"});

  const legacy = {
    ...saved,
    authority_id: "in-tn-cm-helpline",
    authority_name: "untrusted stale label",
    handoff_name: "untrusted stale handoff",
    handoff_url: "https://example.invalid/stale",
    handoff_package: "example.invalid.stale",
    region: "coimbatore",
    routing_source: "nominatim_structured_city",
    routing_match_field: "structured_place",
    routing_match_value: "city: Coimbatore",
    routing_pack_id: "in-top50-routing",
    routing_pack_version: 1,
    routing_pack_sha256: legacySha,
    routing_pack_state_code: "IN",
  };
  const migrated = await P.migrateLegacyTamilNaduHandoff(legacy);
  eq("migration: v1.25 Coimbatore report moves to the current statewide binding",
     migrated && [migrated.authority_id, migrated.routing_pack_id, migrated.region],
     ["tn-statewide-unverified", "in-tn-state-routing", "tamil-nadu-state"]);
  eq("migration: stale saved URL is never trusted", migrated && migrated.handoff_url,
     "https://cmhelpline.tnega.org/portal/en/home");
  eq("migration: stale saved package is never trusted", migrated && migrated.handoff_package,
     "org.tnega.cmhelpline.citizen");
  eq("migration: current pack provenance replaces old provenance",
     migrated && migrated.routing_pack_sha256, resource && resource.sha256);
  for (const [name, changes] of [
    ["forged old digest", {routing_pack_sha256: "0".repeat(64)}],
    ["wrong old region", {region: "surat"}],
    ["wrong old city evidence", {routing_match_value: "city: Chennai"}],
    ["moved coordinates", {lat: 13.0827, lng: 80.2707}],
    ["coarse old GPS", {gps_accuracy: 31}],
  ]) {
    eq(`migration: ${name} is rejected`,
       await P.migrateLegacyTamilNaduHandoff({...legacy, ...changes}), null);
  }

  const gccPack = await P.loadStatePack("in-tn-routing");
  const gccRaw = await P.municipalCityRouteFromGeocode(
    "in-tn-routing", chennaiGeo, 13.0827, 80.2707, 12);
  const gccSaved = {...gccRaw, lat: 13.0827, lng: 80.2707, gps_accuracy: 12,
    issue_type: "garbage"};
  ok("saved GCC: current Chennai report remains bound to GCC",
     await P.savedOfficialRouteBinding(gccSaved, "in-tn-routing", "tn-gcc", gccPack),
     gccSaved);
  eq("saved GCC: it is not treated as a legacy statewide migration",
     await P.migrateLegacyTamilNaduHandoff(gccSaved), null);

  return checks;
}
"""


def open_page(browser, override=None, gcc_override=None, mock_bengaluru=False):
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    if override is not None:
        page.route(route_pattern(PACK_ID), override)
    if gcc_override is not None:
        page.route(route_pattern("in-tn-routing"), gcc_override)
    if mock_bengaluru:
        page.route(
            "**/Admin_Dynamic_New/MapServer/1/query*",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({
                    "features": [{
                        "attributes": {
                            "KGISTownName": "Bengaluru Central City Corporation",
                            "Town_Type": "CC",
                            "LGD_TownCode": 305851,
                        }
                    }]
                }),
            ),
        )
    page.goto(APP)
    page.wait_for_load_state("networkidle")
    page.wait_for_function(
        "() => window.StandaloneAPI && StandaloneAPI.__pure "
        "&& typeof StandaloneAPI.__pure.tamilNaduRouteFromGeocode === 'function'",
        timeout=30_000,
    )
    return context, page


def main() -> None:
    failures: list[str] = []
    raw = b""
    gcc_raw = b""
    extra_checks = 0
    try:
        pack, raw = read_pack(PACK_ID)
        _, gcc_raw = read_pack("in-tn-routing")
        payload = read_payload(PACK_ID)
        region = payload["region"]
        encoded_geometry = json.dumps(
            region["geometry"], ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        actual_digest = hashlib.sha256(encoded_geometry).hexdigest()
        if pack.get("adapter") != "statewide-general-v1":
            failures.append(f"unexpected Tamil Nadu adapter: {pack.get('adapter')!r}")
        if payload.get("retrieved_at") != "2026-08-24":
            failures.append("Tamil Nadu retrieval date is not pinned")
        if actual_digest != EXPECTED_GEOMETRY_SHA256:
            failures.append(f"Tamil Nadu geometry digest changed: {actual_digest}")
        if region.get("geometry_sha256") != EXPECTED_GEOMETRY_SHA256:
            failures.append("Tamil Nadu payload does not record the pinned geometry digest")
        if hashlib.sha256(raw).hexdigest() != EXPECTED_PACK_SHA256:
            failures.append("Tamil Nadu hosted pack digest changed")
    except (AssertionError, KeyError, OSError, TypeError, ValueError) as error:
        failures.append(f"Tamil Nadu routing pack pin is invalid: {error}")

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
                    body=raw.replace(b"Tamil Nadu", b"Xamil Nadu", 1),
                ),
            ),
        ]:
            context, page = open_page(browser, responder, mock_bengaluru=True)
            direct = page.evaluate(
                """async () => StandaloneAPI.__pure.tamilNaduRouteFromGeocode(
                  null,11.3410,77.7172,12)"""
            )
            if direct.get("routed") is not False or direct.get(
                "unrouted_reason"
            ) != "jurisdiction_unavailable":
                failures.append(
                    f"{label} Tamil Nadu pack did not fail closed: {direct!r}"
                )
            fallthrough = page.evaluate("""async () => {
              const P = StandaloneAPI.__pure;
              const kochi = await P.routeOfficer(
                {city:'Kochi',state:'Kerala',country_code:'in'},
                9.9679032,76.2444378,12,null,null,'garbage');
              const bengaluru = await P.routeOfficer(
                {city:'Bengaluru',state:'Karnataka',country_code:'in'},
                12.9716,77.5946,12,null,null,'garbage');
              const tamilNadu = await P.routeOfficer(
                {city:'Erode',state:'Tamil Nadu',country_code:'in'},
                11.3410,77.7172,12,null,null,'garbage');
              const chennai = await P.routeOfficer(
                {city:'Chennai',state:'Tamil Nadu',country_code:'in'},
                13.0827,80.2707,12,null,null,'garbage');
              return {kochi, bengaluru, tamilNadu, chennai};
            }""")
            if fallthrough["kochi"].get("authority_id") != "in-kl-ksmart":
                failures.append(
                    f"{label} Tamil Nadu pack blocked Kochi: {fallthrough['kochi']!r}"
                )
            if fallthrough["bengaluru"].get("routing_pack_id") != "in-ka-routing":
                failures.append(
                    f"{label} Tamil Nadu pack blocked Bengaluru: {fallthrough['bengaluru']!r}"
                )
            if fallthrough["tamilNadu"].get("unrouted_reason") != "jurisdiction_unavailable":
                failures.append(
                    f"{label} Tamil Nadu point did not retain transient failure: "
                    f"{fallthrough['tamilNadu']!r}"
                )
            if fallthrough["chennai"].get("authority_id") != "tn-gcc":
                failures.append(
                    f"{label} statewide pack blocked verified GCC: {fallthrough['chennai']!r}"
                )
            extra_checks += 5
            context.close()

        for label, gcc_responder in [
            ("missing", lambda route: route.fulfill(status=404, body="missing")),
            (
                "tampered",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=gcc_raw.replace(b"Greater Chennai", b"Xreater Chennai", 1),
                ),
            ),
        ]:
            context, page = open_page(browser, gcc_override=gcc_responder)
            chennai = page.evaluate("""async () => StandaloneAPI.__pure.routeOfficer(
              {city:'Chennai',state:'Tamil Nadu',country_code:'in'},
              13.0827,80.2707,12,null,null,'garbage')""")
            if [chennai.get("authority_id"), chennai.get("routing_pack_id")] != [
                "tn-statewide-unverified", "in-tn-state-routing"
            ]:
                failures.append(
                    f"{label} GCC pack did not fall back statewide: {chennai!r}"
                )
            extra_checks += 1
            context.close()
        browser.close()

    if failures:
        print("FAIL")
        for failure in failures:
            print("  -", failure)
        raise SystemExit(1)
    print(f"TAMIL NADU ROUTING TEST PASS ({len(results) + extra_checks} checks)")


if __name__ == "__main__":
    main()
