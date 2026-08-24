# -*- coding: utf-8 -*-
"""Telangana routes statewide while exact CURE and National Highways stay first."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import sync_playwright

from state_pack_utils import ROOT, read_pack, read_payload, route_pattern


APP = "http://localhost:8765/"
PACK_ID = "in-tg-state-routing"
CURE_PACK_ID = "in-tg-routing"
EXPECTED_GEOMETRY_SHA256 = (
    "77183815e4b698ec1e823f4a94a6f213d1d827ea35de8fec8c0ab3b6a9d15175"
)
EXPECTED_PACK_SHA256 = (
    "6ecabec0bbf110e023aa1f65c46e42c50efed2c63e9a67edb4aeb15a86832d67"
)
EXPECTED_CURE_PACK_SHA256 = (
    "92c67b4f7e7ec11b99f8f48411582cae0d0fabdd972480d7f1b82eb3fa1d0cb9"
)
INSIDE = [
    {"name": "Warangal", "lat": 17.9689, "lng": 79.5941},
    {"name": "Nizamabad", "lat": 18.6725, "lng": 78.0941},
    {"name": "Adilabad", "lat": 19.6641, "lng": 78.5320},
    # Non-top-50 and edge-of-state fixtures prove this is not a city-name rollout.
    {"name": "rural Mahabubnagar", "lat": 16.7488, "lng": 78.0035},
    {"name": "Bhadrachalam", "lat": 17.6688019, "lng": 80.8940083},
    {"name": "Aswaraopeta", "lat": 17.3712945, "lng": 81.1718940},
    {"name": "Cherla", "lat": 18.0800540, "lng": 80.8255624},
]
OUTSIDE = [
    # These four mandals were transferred east to Andhra Pradesh in 2014.
    {"name": "Chintoor", "lat": 17.7535803, "lng": 81.4072645},
    {"name": "Kunavaram", "lat": 17.5754041, "lng": 81.2533533},
    {"name": "Kukunoor", "lat": 17.5570349, "lng": 81.1701832},
    {"name": "Velerupadu", "lat": 17.5249017, "lng": 81.2569292},
    {"name": "Vijayawada", "lat": 16.5062, "lng": 80.6480},
    {"name": "Bidar", "lat": 17.9133, "lng": 77.5301},
    {"name": "Nanded", "lat": 19.1383, "lng": 77.3210},
    {"name": "Jagdalpur", "lat": 19.0748, "lng": 82.0080},
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
  const resource = manifest && manifest.resources && manifest.resources["in-tg-state-routing"];
  const cureResource = manifest && manifest.resources && manifest.resources["in-tg-routing"];
  const coverage = await P.telanganaCoverage();
  const region = coverage && coverage.region;
  const pack = await P.loadStatePack("in-tg-state-routing");
  const legacyCities = await P.majorCityCoverage();

  eq("pack: v1.28 keeps thirteen independently pinned resources",
     Object.keys(manifest && manifest.resources || {}).length, 13);
  ok("pack: statewide manifest entry exists", resource, manifest);
  eq("pack: statewide adapter is data-only", resource && resource.adapter,
     "statewide-general-v1");
  eq("pack: state code is Telangana", resource && resource.state_code, "TG");
  eq("pack: full-state scope is explicit", resource && resource.statewide, true);
  eq("pack: statewide content digest is pinned", resource && resource.sha256,
     "6ecabec0bbf110e023aa1f65c46e42c50efed2c63e9a67edb4aeb15a86832d67");
  eq("pack: statewide content-addressed path is pinned", resource && resource.path,
     "packs/v1/states/tg/routing-6ecabec0bbf110e023aa1f65c46e42c50efed2c63e9a67edb4aeb15a86832d67.json");
  eq("pack: exact CURE pack remains separate", cureResource && [
    cureResource.adapter, cureResource.statewide, cureResource.sha256,
  ], [
    "municipal-city-v1", false,
    "92c67b4f7e7ec11b99f8f48411582cae0d0fabdd972480d7f1b82eb3fa1d0cb9",
  ]);
  eq("pack: statewide rollout does not mutate the legacy major-city inventory",
     legacyCities && legacyCities.regions && legacyCities.regions.length, 35);

  ok("coverage: pinned payload loads", region, coverage);
  eq("coverage: stable region ID", region && region.id, "telangana-state");
  eq("coverage: stable authority ID", region && region.authority_id,
     "tg-statewide-unverified");
  eq("coverage: relation ID is pinned", region && region.osm_relation_id, 3250963);
  eq("coverage: geometry digest is pinned", region && region.geometry_sha256,
     P.TELANGANA_STATE_GEOMETRY_SHA256);
  ok("coverage: source attribution is explicit",
     /OpenStreetMap contributors/.test(region && region.source_name || "")
       && /ODbL/.test(region && region.source_license || ""), region);
  ok("coverage: scope is the full state", /Full State of Telangana/i.test(
     region && region.scope || ""), region && region.scope);
  ok("coverage: limitations disclaim ownership and require user verification",
     (region && region.limitations || []).some((item) => /ownership is not inferred/i.test(item))
       && (region && region.limitations || []).some((item) => /user must select/i.test(item)),
     region && region.limitations);

  eq("registry: Telangana statewide release is versioned", P.AUTHORITY_REGISTRY_VERSION, 13);
  eq("registry: stable statewide authority is installed",
     P.TELANGANA_STATE_AUTHORITY.id, "tg-statewide-unverified");
  eq("registry: primary Prajavani handoff", P.TELANGANA_STATE_AUTHORITY.handoff_url,
     "https://prajavani.cgg.gov.in/");
  eq("registry: Citizen Buddy is the reviewed urban alternate",
     P.TELANGANA_STATE_AUTHORITY.alternate_handoff_url,
     "https://play.google.com/store/apps/details?id=vmax.com.citizenbuddy");
  ok("registry: installed official routes pass structural validation",
     P.validateOfficialHandoffRegistry(P.OFFICIAL_AUTHORITIES), null);

  let saved = null;
  for (const fixture of inside) {
    const raw = await P.telanganaRouteFromGeocode(null, fixture.lat, fixture.lng, 12);
    ok(`${fixture.name}: coordinates route without a geocoder`, raw && raw.routed, raw);
    eq(`${fixture.name}: neutral Telangana authority`, raw && raw.authority_id,
       "tg-statewide-unverified");
    eq(`${fixture.name}: exact state-boundary evidence`, raw && [
      raw.routing_source, raw.routing_match_field, raw.routing_match_value, raw.region,
    ], [
      "osm_telangana_state_boundary", "boundary",
      "Telangana (OpenStreetMap relation 3250963)", "telangana-state",
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
      "in-tg-state-routing", resource && resource.pack_version,
      resource && resource.sha256, "TG",
    ]);
    ok(`${fixture.name}: routing never claims submission`, absent(raw, [
      "official_grievance_id", "submitted_at", "sent_at", "handoff_opened_at",
    ]), raw);
    if (fixture.name === "Warangal") {
      saved = {...raw, ...fixture, gps_accuracy: 12, issue_type: "road_damage"};
    }
  }

  const rural = await P.telanganaRouteFromGeocode(null, 16.7488, 78.0035, 12);
  for (const issue of ["road_damage", "garbage", "open_manhole"]) {
    const typed = P.routeForIssue(rural, issue);
    eq(`issue/${issue}: remains routable statewide`, typed && typed.routed, true);
    eq(`issue/${issue}: keeps Telangana authority`, typed && typed.authority_id,
       "tg-statewide-unverified");
    eq(`issue/${issue}: issue is explicit`, typed && typed.issue_type, issue);
    eq(`issue/${issue}: never becomes tender-eligible`,
       typed && typed.tender_eligible, false);
    eq(`issue/${issue}: never claims submission`, absent(typed, [
      "official_grievance_id", "submitted_at", "sent_at",
    ]), true);
  }

  const staleTelangana = {
    city: "Hyderabad", state: "Telangana", country_code: "in",
    full: "stale Telangana address",
  };
  for (const fixture of outside) {
    const direct = await P.telanganaRouteFromGeocode(
      staleTelangana, fixture.lat, fixture.lng, 12);
    eq(`${fixture.name}: stale state label cannot cross the polygon`, direct, null);
    const orchestrated = await P.routeOfficer(
      staleTelangana, fixture.lat, fixture.lng, 12, null, null, "garbage");
    ok(`${fixture.name}: routeOfficer never assigns Telangana`,
       !orchestrated || orchestrated.authority_id !== "tg-statewide-unverified",
       orchestrated);
  }

  const atLimit = await P.telanganaRouteFromGeocode(null, 17.9689, 79.5941, 30);
  eq("accuracy: 30 metres is accepted away from the state edge",
     atLimit && atLimit.authority_id, "tg-statewide-unverified");
  for (const [label, accuracy] of [
    ["31 metres", 31], ["negative", -1], ["not finite", Number.NaN],
  ]) {
    const uncertain = await P.telanganaRouteFromGeocode(
      null, 17.9689, 79.5941, accuracy);
    eq(`accuracy: ${label} fails closed`,
       uncertain && uncertain.unrouted_reason, "location_uncertain");
  }
  const edgePoint = region.geometry.type === "Polygon"
    ? region.geometry.coordinates[0][0]
    : region.geometry.coordinates[0][0][0];
  const edge = await P.telanganaRouteFromGeocode(
    staleTelangana, edgePoint[1], edgePoint[0], 5);
  eq("boundary: a five-metre circle touching the state edge fails closed",
     edge && edge.unrouted_reason, "location_uncertain");

  // This point is an exact checked-in NH-563 line vertex near Warangal.
  const highway = await P.routeOfficer(
    {state: "Telangana", country_code: "in"},
    17.97003, 79.59788, 5, null, null, "road_damage");
  eq("precedence: NH-563 beats the statewide Telangana handoff",
     highway && [highway.authority_id, highway.highway_ref],
     ["in-national-highway", "NH-563"]);
  const highwayGarbage = await P.routeOfficer(
    {state: "Telangana", country_code: "in"},
    17.97003, 79.59788, 5, null, null, "garbage");
  eq("precedence: civic issues beside NH-563 stay with the statewide handoff",
     highwayGarbage && [highwayGarbage.authority_id, highwayGarbage.routing_pack_id],
     ["tg-statewide-unverified", "in-tg-state-routing"]);

  const hyderabadGeo = {city: "Hyderabad", state: "Telangana", country_code: "in"};
  const hyderabad = await P.routeOfficer(
    hyderabadGeo, 17.3616, 78.4747, 12, null, null, "garbage");
  eq("precedence: exact official CURE match beats statewide Telangana",
     hyderabad && [
       hyderabad.authority_id, hyderabad.routing_pack_id,
       hyderabad.routing_source, hyderabad.routing_match_field,
     ], [
       "tg-cure-shared", "in-tg-routing",
       "tgrac_cure_2053_point_query", "official_accuracy_envelope",
     ]);
  eq("precedence: exact route keeps the shared My Cure portal",
     hyderabad && hyderabad.handoff_url,
     "https://igs.ghmc.gov.in/operator/send_otp_mobile");
  eq("precedence: exact route keeps the My Cure package",
     hyderabad && hyderabad.handoff_package, "cgg.gov.ghmc");

  const cantonment = await P.routeOfficer(
    hyderabadGeo, 17.4815673, 78.4980533, 12, null, null, "open_manhole");
  eq("precedence: Secunderabad Cantonment exclusion falls back statewide",
     cantonment && [cantonment.authority_id, cantonment.routing_pack_id],
     ["tg-statewide-unverified", "in-tg-state-routing"]);

  ok("saved binding: valid current Telangana record is accepted",
     saved && await P.savedOfficialRouteBinding(
       saved, "in-tg-state-routing", "tg-statewide-unverified", pack), saved);
  const rejected = async (name, changes) => {
    const candidate = {...saved, ...changes};
    const binding = await P.savedOfficialRouteBinding(
      candidate, "in-tg-state-routing", "tg-statewide-unverified", pack);
    eq(name, binding, null);
  };
  await rejected("saved binding: cross-pack ID is rejected",
    {routing_pack_id: "in-top50-routing"});
  await rejected("saved binding: cross-state provenance is rejected",
    {routing_pack_state_code: "IN"});
  await rejected("saved binding: forged digest is rejected",
    {routing_pack_sha256: "0".repeat(64)});
  await rejected("saved binding: partial provenance is rejected",
    {routing_pack_sha256: null});
  await rejected("saved binding: wrong pack version is rejected",
    {routing_pack_version: 2});
  await rejected("saved binding: cross-region binding is rejected",
    {region: "hyderabad-cure-2053"});
  await rejected("saved binding: transferred Chintoor coordinates are rejected",
    {lat: 17.7535803, lng: 81.4072645});
  await rejected("saved binding: over-30m accuracy is rejected", {gps_accuracy: 31});
  await rejected("saved binding: changed source is rejected",
    {routing_source: "nominatim_structured_city"});
  await rejected("saved binding: changed match field is rejected",
    {routing_match_field: "structured_place"});
  await rejected("saved binding: changed boundary evidence is rejected",
    {routing_match_value: "Telangana (OpenStreetMap relation 1)"});
  eq("saved binding: changed authority is rejected", await P.savedOfficialRouteBinding(
    {...saved, authority_id: "tg-cure-shared"}, "in-tg-state-routing",
    "tg-cure-shared", pack), null);

  return checks;
}
"""


def tgrac_responder(calls: list[dict], *, unavailable: bool = False):
    cantonment = {
        "min_lng": 78.459155005,
        "min_lat": 17.443033296,
        "max_lng": 78.539634302,
        "max_lat": 17.540382430,
    }

    def respond(route):
        query = parse_qs(urlsplit(route.request.url).query)
        try:
            geometry = json.loads(query.get("geometry", ["null"])[0])
        except (TypeError, ValueError):
            geometry = None
        path = urlsplit(route.request.url).path
        calls.append({
            "path": path,
            "spatial_rel": query.get("spatialRel", [None])[0],
            "geometry_type": query.get("geometryType", [None])[0],
            "in_sr": query.get("inSR", [None])[0],
            "return_count_only": query.get("returnCountOnly", [None])[0],
            "geometry": geometry,
        })
        if unavailable:
            route.fulfill(
                status=503, content_type="application/json", body='{"error":true}'
            )
            return
        if not isinstance(geometry, dict):
            route.fulfill(
                status=200, content_type="application/json", body='{"error":true}'
            )
            return
        if path.endswith("/Administrative_Layer/MapServer/1/query"):
            intersects = not (
                geometry.get("xmax", 0) < cantonment["min_lng"]
                or geometry.get("xmin", 0) > cantonment["max_lng"]
                or geometry.get("ymax", 0) < cantonment["min_lat"]
                or geometry.get("ymin", 0) > cantonment["max_lat"]
            )
            count = 1 if intersects else 0
        else:
            # The focused fixtures put Hyderabad inside the official CURE layer.
            count = 1
        route.fulfill(
            status=200,
            content_type="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
            body=json.dumps({"count": count}, separators=(",", ":")),
        )

    return respond


def serve_checked_in_production_pack(route) -> None:
    """Serve immutable production pack URLs from docs in native-mode tests."""
    path = urlsplit(route.request.url).path
    marker = "/pothole-reporter/"
    if marker not in path:
        route.fulfill(status=404, content_type="text/plain", body="not found")
        return
    relative = path.split(marker, 1)[1]
    candidate = (ROOT / "docs" / relative).resolve()
    docs_root = (ROOT / "docs").resolve()
    try:
        candidate.relative_to(docs_root)
    except ValueError:
        route.fulfill(status=404, content_type="text/plain", body="not found")
        return
    if not candidate.is_file():
        route.fulfill(status=404, content_type="text/plain", body="not found")
        return
    route.fulfill(
        status=200, content_type="application/json", body=candidate.read_bytes()
    )


def open_page(browser, *, official_responder, state_override=None, native=True):
    context = browser.new_context(viewport={"width": 390, "height": 844})
    if native:
        context.add_init_script("window.Capacitor={isNativePlatform:()=>true,Plugins:{}};")
    context.route(
        "https://coding-parrot.github.io/pothole-reporter/packs/v1/**",
        serve_checked_in_production_pack,
    )
    context.route("https://tgrac.telangana.gov.in/**", official_responder)
    page = context.new_page()
    if state_override is not None:
        page.route(route_pattern(PACK_ID), state_override)
    page.goto(APP)
    page.wait_for_load_state("networkidle")
    page.wait_for_function(
        "() => window.StandaloneAPI && StandaloneAPI.__pure "
        "&& typeof StandaloneAPI.__pure.telanganaRouteFromGeocode === 'function'",
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
            failures.append(f"unexpected Telangana adapter: {pack.get('adapter')!r}")
        if pack.get("state_code") != "TG" or pack.get("pack_id") != PACK_ID:
            failures.append("Telangana hosted pack identity is invalid")
        if payload.get("retrieved_at") != "2026-08-24":
            failures.append("Telangana retrieval date is not pinned")
        if actual_digest != EXPECTED_GEOMETRY_SHA256:
            failures.append(f"Telangana geometry digest changed: {actual_digest}")
        if region.get("geometry_sha256") != EXPECTED_GEOMETRY_SHA256:
            failures.append("Telangana payload does not record the pinned geometry digest")
        if hashlib.sha256(raw).hexdigest() != EXPECTED_PACK_SHA256:
            failures.append("Telangana hosted pack digest changed")
        _, cure_raw = read_pack(CURE_PACK_ID)
        if hashlib.sha256(cure_raw).hexdigest() != EXPECTED_CURE_PACK_SHA256:
            failures.append("Hyderabad CURE pack digest changed during statewide rollout")
    except (AssertionError, KeyError, OSError, TypeError, ValueError) as error:
        failures.append(f"Telangana routing pack pin is invalid: {error}")

    if not raw:
        print("FAIL")
        for failure in failures:
            print("  -", failure)
        raise SystemExit(1)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--disable-web-security"])

        official_calls: list[dict] = []
        context, page = open_page(
            browser, official_responder=tgrac_responder(official_calls)
        )
        results = page.evaluate(SCENARIO, {"inside": INSIDE, "outside": OUTSIDE})
        context.close()
        for name, passed, got, want in results:
            if not passed:
                failures.append(name)
                print(f"  FAIL {name}\n         got  {got!r}\n         want {want!r}")

        cure_calls = [
            call for call in official_calls
            if call["path"].endswith(
                "/TCUR_Telangana_Core_Urban_Region_V2/MapServer/22/query"
            )
        ]
        cantonment_calls = [
            call for call in official_calls
            if call["path"].endswith("/Administrative_Layer/MapServer/1/query")
        ]
        if not cure_calls or any(
            call["spatial_rel"] != "esriSpatialRelWithin"
            or call["geometry_type"] != "esriGeometryEnvelope"
            or call["in_sr"] != "4326"
            or call["return_count_only"] != "true"
            for call in cure_calls
        ):
            failures.append("CURE precedence did not use the pinned official Within query")
        if not cantonment_calls or any(
            call["spatial_rel"] != "esriSpatialRelIntersects"
            or call["geometry_type"] != "esriGeometryEnvelope"
            or call["in_sr"] != "4326"
            or call["return_count_only"] != "true"
            for call in cantonment_calls
        ):
            failures.append("Cantonment fallback did not use the pinned Intersects query")
        if any(
            not isinstance(call["geometry"], dict)
            or call["geometry"].get("xmin") >= call["geometry"].get("xmax")
            or call["geometry"].get("ymin") >= call["geometry"].get("ymax")
            for call in official_calls
        ):
            failures.append("TGRAC lookup did not send a non-zero accuracy envelope")
        extra_checks += 3

        unavailable_calls: list[dict] = []
        context, page = open_page(
            browser,
            official_responder=tgrac_responder(unavailable_calls, unavailable=True),
        )
        unavailable = page.evaluate(
            """async () => StandaloneAPI.__pure.routeOfficer(
              {city:'Hyderabad',state:'Telangana',country_code:'in'},
              17.3616,78.4747,12,null,null,'garbage')"""
        )
        if [unavailable.get("authority_id"), unavailable.get("routing_pack_id")] != [
            "tg-statewide-unverified", "in-tg-state-routing"
        ]:
            failures.append(f"unavailable CURE did not fall back statewide: {unavailable!r}")
        if not unavailable_calls:
            failures.append("unavailable-CURE fixture never queried TGRAC")
        extra_checks += 2
        context.close()

        pwa_calls: list[dict] = []
        context, page = open_page(
            browser, official_responder=tgrac_responder(pwa_calls), native=False
        )
        pwa = page.evaluate(
            """async () => StandaloneAPI.__pure.routeOfficer(
              {city:'Hyderabad',state:'Telangana',country_code:'in'},
              17.3616,78.4747,12,null,null,'garbage')"""
        )
        if [pwa.get("authority_id"), pwa.get("routing_pack_id")] != [
            "tg-statewide-unverified", "in-tg-state-routing"
        ]:
            failures.append(f"browser/PWA Hyderabad did not fall back statewide: {pwa!r}")
        if pwa_calls:
            failures.append("browser/PWA fallback unexpectedly sent coordinates to TGRAC")
        extra_checks += 2
        context.close()

        for label, responder in [
            ("missing", lambda route: route.fulfill(status=404, body="missing")),
            (
                "tampered",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=raw.replace(b"Telangana", b"Xelangana", 1),
                ),
            ),
        ]:
            calls: list[dict] = []
            context, page = open_page(
                browser,
                official_responder=tgrac_responder(calls),
                state_override=responder,
            )
            routes = page.evaluate(
                """async () => {
                  const P = StandaloneAPI.__pure;
                  const direct = await P.telanganaRouteFromGeocode(
                    null,16.7488,78.0035,12);
                  const rural = await P.routeOfficer(
                    {state:'Telangana',country_code:'in'},
                    16.7488,78.0035,12,null,null,'garbage');
                  const cantonment = await P.routeOfficer(
                    {city:'Hyderabad',state:'Telangana',country_code:'in'},
                    17.4815673,78.4980533,12,null,null,'open_manhole');
                  const cure = await P.routeOfficer(
                    {city:'Hyderabad',state:'Telangana',country_code:'in'},
                    17.3616,78.4747,12,null,null,'garbage');
                  return {direct, rural, cantonment, cure};
                }"""
            )
            for route_name in ("direct", "rural", "cantonment"):
                result = routes[route_name]
                if not isinstance(result, dict) or result.get("routed") is not False \
                        or result.get("unrouted_reason") != "jurisdiction_unavailable":
                    failures.append(
                        f"{label} Telangana pack allowed {route_name}: {result!r}"
                    )
            if [routes["cure"].get("authority_id"), routes["cure"].get("routing_pack_id")] \
                    != ["tg-cure-shared", "in-tg-routing"]:
                failures.append(
                    f"{label} statewide pack blocked exact CURE: {routes['cure']!r}"
                )
            extra_checks += 4
            context.close()

        browser.close()

    if failures:
        print("FAIL")
        for failure in failures:
            print("  -", failure)
        raise SystemExit(1)
    print(f"TELANGANA ROUTING TEST PASS ({len(results) + extra_checks + 6} checks)")


if __name__ == "__main__":
    main()
