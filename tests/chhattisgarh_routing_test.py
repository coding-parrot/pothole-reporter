# -*- coding: utf-8 -*-
"""Chhattisgarh routes by exact state geometry and keeps neighbouring states separate."""

from __future__ import annotations

import hashlib
import json

from playwright.sync_api import sync_playwright

from state_pack_utils import read_pack, read_payload, resource_for, route_pattern


APP = "http://localhost:8765/"
PACK_ID = "in-cg-routing"
EXPECTED_GEOMETRY_SHA256 = (
    "827e89a598571ade84db77390bca5daf98c9f67fbae716b17193f4ccdc2876eb"
)
INSIDE = [
    {"name": "Raipur", "lat": 21.2381, "lng": 81.6337},
    {"name": "Durg-Bhilai", "lat": 21.2121, "lng": 81.3733},
    {"name": "Bilaspur", "lat": 22.0797, "lng": 82.1409},
    {"name": "Jagdalpur", "lat": 19.0748, "lng": 82.0080},
    {"name": "Ambikapur", "lat": 23.1355, "lng": 83.1818},
    # A non-top-50 interior point proves statewide rather than city-name coverage.
    {"name": "Narayanpur", "lat": 19.7200, "lng": 81.2500},
]
OUTSIDE = [
    # Koraput lies inside the coarse state-pack rectangle but outside Chhattisgarh.
    {"name": "Koraput", "lat": 18.8135, "lng": 82.7123},
    {"name": "Jabalpur", "lat": 23.1702, "lng": 79.9325},
    {"name": "Ranchi", "lat": 23.3701, "lng": 85.3250},
    {"name": "Bhadrachalam", "lat": 17.6688, "lng": 80.8936},
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
  const resource = manifest && manifest.resources && manifest.resources["in-cg-routing"];
  const coverage = await P.chhattisgarhCoverage();
  const region = coverage && coverage.region;
  const pack = await P.loadStatePack("in-cg-routing");
  const legacyCities = await P.majorCityCoverage();

  eq("pack: v1.34 has twenty-two independently pinned resources",
     Object.keys(manifest && manifest.resources || {}).length, 22);
  ok("pack: statewide manifest entry exists", resource, manifest);
  eq("pack: adapter is data-only", resource && resource.adapter,
     "statewide-general-v1");
  eq("pack: state code is Chhattisgarh", resource && resource.state_code, "CG");
  eq("pack: full-state scope is explicit", resource && resource.statewide, true);
  ok("pack: content digest is pinned",
     resource && /^[0-9a-f]{64}$/.test(resource.sha256), resource);
  eq("pack: path is content-addressed", resource && resource.path,
     resource && `packs/v1/states/cg/routing-${resource.sha256}.json`);
  eq("pack: immutable compatibility inventory is retained",
     legacyCities && legacyCities.regions && legacyCities.regions.length, 35);

  ok("coverage: pinned payload loads", region, coverage);
  eq("coverage: stable region ID", region && region.id, "chhattisgarh-state");
  eq("coverage: stable authority ID", region && region.authority_id,
     "cg-statewide-unverified");
  eq("coverage: relation ID is pinned", region && region.osm_relation_id, 1972004);
  eq("coverage: geometry digest is pinned", region && region.geometry_sha256,
     P.CHHATTISGARH_STATE_GEOMETRY_SHA256);
  ok("coverage: source attribution is explicit",
     /OpenStreetMap contributors/.test(region && region.source_name || "")
       && /ODbL/.test(region && region.source_license || ""), region);
  ok("coverage: limitations disclaim ownership and automatic submission",
     (region && region.limitations || []).some((item) => /ownership is not inferred/i.test(item))
       && (region && region.limitations || []).some((item) => /does not submit/i.test(item)),
     region && region.limitations);

  eq("registry: statewide expansion is versioned", P.AUTHORITY_REGISTRY_VERSION, 17);
  eq("registry: stable statewide authority is installed",
     P.CHHATTISGARH_STATE_AUTHORITY.id, "cg-statewide-unverified");
  eq("registry: primary CM Helpline handoff",
     P.CHHATTISGARH_STATE_AUTHORITY.handoff_url,
     "https://cmhelpline.cg.gov.in/Home/VerifyOTPBeforeOnlineComplaint");
  eq("registry: NIDAAN remains the urban civic alternative",
     P.CHHATTISGARH_STATE_AUTHORITY.alternate_handoff_url,
     "https://crm.nidaan.cg.gov.in/");
  eq("registry: state helpline", P.CHHATTISGARH_STATE_AUTHORITY.helpline, "1076");
  ok("registry: installed official routes pass structural validation",
     P.validateOfficialHandoffRegistry(P.OFFICIAL_AUTHORITIES), null);

  let saved = null;
  for (const fixture of inside) {
    const raw = await P.chhattisgarhRouteFromGeocode(
      null, fixture.lat, fixture.lng, 12);
    ok(`${fixture.name}: coordinates route without a geocoder`, raw && raw.routed, raw);
    eq(`${fixture.name}: neutral Chhattisgarh authority`, raw && raw.authority_id,
       "cg-statewide-unverified");
    eq(`${fixture.name}: exact state-boundary evidence`, raw && [
      raw.routing_source, raw.routing_match_field, raw.routing_match_value, raw.region,
    ], [
      "osm_chhattisgarh_state_boundary", "boundary",
      "Chhattisgarh (OpenStreetMap relation 1972004)", "chhattisgarh-state",
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
      "in-cg-routing", resource && resource.pack_version,
      resource && resource.sha256, "CG",
    ]);
    ok(`${fixture.name}: routing never claims submission`, absent(raw, [
      "official_grievance_id", "submitted_at", "sent_at", "handoff_opened_at",
    ]), raw);
    for (const issue of ["road_damage", "garbage", "open_manhole"]) {
      const typed = P.routeForIssue(raw, issue);
      eq(`${fixture.name}/${issue}: remains routable`, typed && typed.routed, true);
      eq(`${fixture.name}/${issue}: keeps statewide authority`,
         typed && typed.authority_id, "cg-statewide-unverified");
      eq(`${fixture.name}/${issue}: issue is explicit`, typed && typed.issue_type, issue);
      eq(`${fixture.name}/${issue}: never becomes tender-eligible`,
         typed && typed.tender_eligible, false);
    }
    if (fixture.name === "Narayanpur") {
      saved = {...raw, ...fixture, gps_accuracy: 12, issue_type: "road_damage"};
    }
  }

  const staleChhattisgarh = {
    city: "Raipur", state: "Chhattisgarh", country_code: "in",
    full: "stale Chhattisgarh address",
  };
  for (const fixture of outside) {
    const direct = await P.chhattisgarhRouteFromGeocode(
      staleChhattisgarh, fixture.lat, fixture.lng, 12);
    eq(`${fixture.name}: stale state label cannot cross the polygon`, direct, null);
  }

  const atLimit = await P.chhattisgarhRouteFromGeocode(null, 19.72, 81.25, 30);
  eq("accuracy: 30 metres is accepted away from the state edge",
     atLimit && atLimit.authority_id, "cg-statewide-unverified");
  for (const [label, accuracy] of [
    ["31 metres", 31], ["negative", -1], ["not finite", Number.NaN],
  ]) {
    const uncertain = await P.chhattisgarhRouteFromGeocode(
      null, 19.72, 81.25, accuracy);
    eq(`accuracy: ${label} fails closed`,
       uncertain && uncertain.unrouted_reason, "location_uncertain");
  }
  const edgePoint = region.geometry.type === "Polygon"
    ? region.geometry.coordinates[0][0]
    : region.geometry.coordinates[0][0][0];
  const edge = await P.chhattisgarhRouteFromGeocode(
    staleChhattisgarh, edgePoint[1], edgePoint[0], 5);
  eq("boundary: a five-metre circle touching the state edge fails closed",
     edge && edge.unrouted_reason, "location_uncertain");

  // Exact checked-in NH-130 line vertex north of Raipur.
  const highway = await P.routeOfficer(
    {state: "Chhattisgarh", country_code: "in"},
    21.61680, 81.70411, 5, null, null, "road_damage");
  eq("precedence: NH-130 beats the Chhattisgarh statewide handoff",
     highway && [highway.authority_id, highway.highway_ref],
     ["in-national-highway", "NH-130"]);

  for (const [city, lat, lng] of [
    ["Raipur", 21.2380912, 81.6336993],
    ["Durg-Bhilai", 21.2120677, 81.3732849],
  ]) {
    const current = await P.routeOfficer(
      {city, state:"Chhattisgarh", country_code:"in"},
      lat, lng, 12, null, null, "garbage");
    eq(`supersession: ${city} uses exact statewide containment`,
       current && [current.authority_id, current.routing_pack_id, current.region],
       ["cg-statewide-unverified", "in-cg-routing", "chhattisgarh-state"]);
  }

  const jabalpur = await P.routeOfficer(
    {city:"Jabalpur", state:"Madhya Pradesh", country_code:"in"},
    23.1701522, 79.9324505, 12, null, null, "garbage");
  eq("cross-state: Jabalpur uses exact Madhya Pradesh statewide containment",
     jabalpur && [jabalpur.authority_id, jabalpur.routing_pack_id, jabalpur.region],
     ["mp-statewide-unverified", "in-mp-routing", "madhya-pradesh-state"]);

  // All three points overlap Chhattisgarh's coarse download rectangle. Exact state
  // geometry must win even when a stale reverse geocoder says Chhattisgarh.
  for (const [name, geo, lat, lng, authority, packId, stateRegion] of [
    ["Cherla, Telangana", {city:"Raipur",state:"Chhattisgarh",country_code:"in"},
      18.0800540,80.8255624,"tg-statewide-unverified","in-tg-state-routing",
      "telangana-state"],
    ["Araku Valley, Andhra Pradesh",
      {city:"Raipur",state:"Chhattisgarh",country_code:"in"},
      18.3273,82.8775,"ap-statewide-unverified","in-ap-routing",
      "andhra-pradesh-state"],
    ["Bhamragad, Maharashtra",
      {city:"Raipur",state:"Chhattisgarh",country_code:"in"},
      19.4160,80.5830,"mh-statewide-unverified","in-mh-routing","maharashtra"],
  ]) {
    const route = await P.routeOfficer(
      geo, lat, lng, 12, null, null, "garbage");
    eq(`cross-state: ${name} keeps its exact state route`, route && [
      route.authority_id, route.routing_pack_id, route.region,
    ], [authority, packId, stateRegion]);
  }

  ok("saved binding: valid current Chhattisgarh record is accepted",
     saved && await P.savedOfficialRouteBinding(
       saved, "in-cg-routing", "cg-statewide-unverified", pack), saved);
  const rejected = async (name, changes) => {
    const candidate = {...saved, ...changes};
    const binding = await P.savedOfficialRouteBinding(
      candidate, "in-cg-routing", "cg-statewide-unverified", pack);
    eq(name, binding, null);
  };
  await rejected("saved binding: cross-pack ID is rejected",
    {routing_pack_id: "in-top50-routing"});
  await rejected("saved binding: forged digest is rejected",
    {routing_pack_sha256: "0".repeat(64)});
  await rejected("saved binding: cross-region binding is rejected",
    {region: "raipur"});
  await rejected("saved binding: outside coordinates are rejected",
    {lat: 18.8135, lng: 82.7123});
  await rejected("saved binding: over-30m accuracy is rejected", {gps_accuracy: 31});
  await rejected("saved binding: changed source is rejected",
    {routing_source: "nominatim_structured_city"});

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
        "&& typeof StandaloneAPI.__pure.chhattisgarhRouteFromGeocode === 'function'",
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
            failures.append(f"unexpected Chhattisgarh adapter: {pack.get('adapter')!r}")
        if pack.get("state_code") != "CG" or pack.get("pack_id") != PACK_ID:
            failures.append("Chhattisgarh hosted pack identity is invalid")
        if payload.get("retrieved_at") != "2026-08-25":
            failures.append("Chhattisgarh retrieval date is not pinned")
        if actual_digest != EXPECTED_GEOMETRY_SHA256:
            failures.append(f"Chhattisgarh geometry digest changed: {actual_digest}")
        if region.get("geometry_sha256") != EXPECTED_GEOMETRY_SHA256:
            failures.append("Chhattisgarh payload does not record the pinned geometry digest")
        if hashlib.sha256(raw).hexdigest() != resource_for(PACK_ID).get("sha256"):
            failures.append("Chhattisgarh hosted pack digest changed after validation")
    except (AssertionError, KeyError, OSError, TypeError, ValueError) as error:
        failures.append(f"Chhattisgarh routing pack pin is invalid: {error}")

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

        for label, responder in [
            ("missing", lambda route: route.fulfill(status=404, body="missing")),
            (
                "tampered",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=raw.replace(b"Chhattisgarh", b"Xhhattisgarh", 1),
                ),
            ),
        ]:
            context, page = open_page(browser, responder)
            failed = page.evaluate("""async () => {
              const P = StandaloneAPI.__pure;
              const direct = await P.chhattisgarhRouteFromGeocode(
                null,21.2381,81.6337,12);
              const orchestrated = await P.routeOfficer(
                {city:'Raipur',state:'Chhattisgarh',country_code:'in'},
                21.2381,81.6337,12,null,null,'garbage');
              const unrelated = await P.routeOfficer(
                {city:'Jabalpur',state:'Madhya Pradesh',country_code:'in'},
                23.1701522,79.9324505,12,null,null,'garbage');
              const telangana = await P.routeOfficer(
                {city:'Raipur',state:'Chhattisgarh',country_code:'in'},
                18.0800540,80.8255624,12,null,null,'garbage');
              const andhraPradesh = await P.routeOfficer(
                {city:'Raipur',state:'Chhattisgarh',country_code:'in'},
                18.3273,82.8775,12,null,null,'garbage');
              const maharashtra = await P.routeOfficer(
                {city:'Raipur',state:'Chhattisgarh',country_code:'in'},
                19.4160,80.5830,12,null,null,'garbage');
              return {direct, orchestrated, unrelated, telangana, andhraPradesh, maharashtra};
            }""")
            for route_name in ("direct", "orchestrated"):
                result = failed[route_name]
                if not isinstance(result, dict) or result.get("routed") is not False \
                        or result.get("unrouted_reason") != "jurisdiction_unavailable":
                    failures.append(
                        f"{label} Chhattisgarh pack allowed {route_name} "
                        f"or old city fallback: {result!r}"
                    )
            unrelated = failed["unrelated"]
            if [unrelated.get("authority_id"), unrelated.get("routing_pack_id")] \
                    != ["mp-statewide-unverified", "in-mp-routing"]:
                failures.append(
                    f"{label} Chhattisgarh pack blocked Jabalpur: {unrelated!r}"
                )
            for route_name, want in {
                "telangana": ["tg-statewide-unverified", "in-tg-state-routing"],
                "andhraPradesh": ["ap-statewide-unverified", "in-ap-routing"],
                "maharashtra": ["mh-statewide-unverified", "in-mh-routing"],
            }.items():
                result = failed[route_name]
                if [result.get("authority_id"), result.get("routing_pack_id")] != want:
                    failures.append(
                        f"{label} Chhattisgarh pack blocked {route_name}: {result!r}"
                    )
            extra_checks += len(failed)
            context.close()
        browser.close()

    if failures:
        print("FAIL")
        for failure in failures:
            print("  -", failure)
        raise SystemExit(1)
    print(f"CHHATTISGARH ROUTING TEST PASS ({len(results) + extra_checks + 5} checks)")


if __name__ == "__main__":
    main()
