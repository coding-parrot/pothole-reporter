# -*- coding: utf-8 -*-
"""Uttar Pradesh uses exact statewide containment without swallowing Delhi or MP."""

from __future__ import annotations

import hashlib
import json

from playwright.sync_api import sync_playwright

from state_pack_utils import read_pack, read_payload, resource_for, route_pattern


APP = "http://localhost:8765/"
PACK_ID = "in-up-routing"
EXPECTED_GEOMETRY_SHA256 = (
    "2dbb5237cab5eb029f517c1d79451663c1fc49affe0e0789b11f0565180db015"
)
INSIDE = [
    {"name": "Lucknow", "lat": 26.8381, "lng": 80.9346},
    {"name": "Kanpur", "lat": 26.4609, "lng": 80.3218},
    {"name": "Varanasi", "lat": 25.3356, "lng": 83.0076},
    {"name": "Agra", "lat": 27.1753, "lng": 78.0098},
    {"name": "Ghaziabad", "lat": 28.6712, "lng": 77.4120},
    {"name": "Noida", "lat": 28.5355, "lng": 77.3910},
    # A non-top-50 point proves that this is a state polygon, not a city list.
    {"name": "Gorakhpur", "lat": 26.7606, "lng": 83.3732},
]
OUTSIDE = [
    {"name": "Delhi", "lat": 28.6129, "lng": 77.2295},
    {"name": "Gwalior", "lat": 26.2037, "lng": 78.1574},
    {"name": "Patna", "lat": 25.6093, "lng": 85.1235},
    {"name": "Jaipur", "lat": 26.9155, "lng": 75.8190},
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
  const resource = manifest && manifest.resources && manifest.resources["in-up-routing"];
  const coverage = await P.uttarPradeshCoverage();
  const region = coverage && coverage.region;
  const pack = await P.loadStatePack("in-up-routing");
  const legacyCities = await P.majorCityCoverage();

  eq("pack: v1.35 has forty-two independently pinned resources",
     Object.keys(manifest && manifest.resources || {}).length, 42);
  ok("pack: statewide manifest entry exists", resource, manifest);
  eq("pack: adapter is data-only", resource && resource.adapter,
     "statewide-general-v1");
  eq("pack: state code is Uttar Pradesh", resource && resource.state_code, "UP");
  eq("pack: full-state scope is explicit", resource && resource.statewide, true);
  ok("pack: content digest is pinned",
     resource && /^[0-9a-f]{64}$/.test(resource.sha256), resource);
  eq("pack: path is content-addressed", resource && resource.path,
     resource && `packs/v1/states/up/routing-${resource.sha256}.json`);
  eq("pack: immutable compatibility inventory is retained",
     legacyCities && legacyCities.regions && legacyCities.regions.length, 35);

  ok("coverage: pinned payload loads", region, coverage);
  eq("coverage: stable region ID", region && region.id, "uttar-pradesh-state");
  eq("coverage: stable authority ID", region && region.authority_id,
     "up-statewide-unverified");
  eq("coverage: relation ID is pinned", region && region.osm_relation_id, 1942587);
  eq("coverage: geometry digest is pinned", region && region.geometry_sha256,
     P.UTTAR_PRADESH_STATE_GEOMETRY_SHA256);
  ok("coverage: source attribution is explicit",
     /OpenStreetMap contributors/.test(region && region.source_name || "")
       && /ODbL/.test(region && region.source_license || ""), region);
  ok("coverage: Delhi exclusion is explicit",
     /excludes Delhi National Capital Territory/i.test(region && region.scope || ""),
     region && region.scope);
  ok("coverage: limitations disclaim ownership and automatic submission",
     (region && region.limitations || []).some((item) => /ownership is not inferred/i.test(item))
       && (region && region.limitations || []).some((item) => /does not submit/i.test(item)),
     region && region.limitations);

  eq("registry: statewide expansion is versioned", P.AUTHORITY_REGISTRY_VERSION, 18);
  eq("registry: stable statewide authority is installed",
     P.UTTAR_PRADESH_STATE_AUTHORITY.id, "up-statewide-unverified");
  eq("registry: primary Jansunwai handoff",
     P.UTTAR_PRADESH_STATE_AUTHORITY.handoff_url,
     "https://jansunwai.up.nic.in/?language=en_US");
  eq("registry: official Android package is retained",
     P.UTTAR_PRADESH_STATE_AUTHORITY.handoff_package,
     "in.nic.up.jansunwai.upjansunwai");
  eq("registry: state helpline", P.UTTAR_PRADESH_STATE_AUTHORITY.helpline, "1076");
  ok("registry: installed official routes pass structural validation",
     P.validateOfficialHandoffRegistry(P.OFFICIAL_AUTHORITIES), null);

  let saved = null;
  for (const fixture of inside) {
    const raw = await P.uttarPradeshRouteFromGeocode(
      null, fixture.lat, fixture.lng, 12);
    ok(`${fixture.name}: coordinates route without a geocoder`, raw && raw.routed, raw);
    eq(`${fixture.name}: neutral Uttar Pradesh authority`, raw && raw.authority_id,
       "up-statewide-unverified");
    eq(`${fixture.name}: exact state-boundary evidence`, raw && [
      raw.routing_source, raw.routing_match_field, raw.routing_match_value, raw.region,
    ], [
      "osm_uttar_pradesh_state_boundary", "boundary",
      "Uttar Pradesh (OpenStreetMap relation 1942587)", "uttar-pradesh-state",
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
      "in-up-routing", resource && resource.pack_version,
      resource && resource.sha256, "UP",
    ]);
    ok(`${fixture.name}: routing never claims submission`, absent(raw, [
      "official_grievance_id", "submitted_at", "sent_at", "handoff_opened_at",
    ]), raw);
    for (const issue of ["road_damage", "garbage", "open_manhole"]) {
      const typed = P.routeForIssue(raw, issue);
      eq(`${fixture.name}/${issue}: remains routable`, typed && typed.routed, true);
      eq(`${fixture.name}/${issue}: keeps statewide authority`,
         typed && typed.authority_id, "up-statewide-unverified");
      eq(`${fixture.name}/${issue}: issue is explicit`, typed && typed.issue_type, issue);
      eq(`${fixture.name}/${issue}: never becomes tender-eligible`,
         typed && typed.tender_eligible, false);
    }
    if (fixture.name === "Gorakhpur") {
      saved = {...raw, ...fixture, gps_accuracy: 12, issue_type: "road_damage"};
    }
  }

  const staleUttarPradesh = {
    city: "Lucknow", state: "Uttar Pradesh", country_code: "in",
    full: "stale Uttar Pradesh address",
  };
  for (const fixture of outside) {
    const direct = await P.uttarPradeshRouteFromGeocode(
      staleUttarPradesh, fixture.lat, fixture.lng, 12);
    eq(`${fixture.name}: stale state label cannot cross the polygon`, direct, null);
  }

  const atLimit = await P.uttarPradeshRouteFromGeocode(null, 26.7606, 83.3732, 30);
  eq("accuracy: 30 metres is accepted away from the state edge",
     atLimit && atLimit.authority_id, "up-statewide-unverified");
  for (const [label, accuracy] of [
    ["31 metres", 31], ["negative", -1], ["not finite", Number.NaN],
  ]) {
    const uncertain = await P.uttarPradeshRouteFromGeocode(
      null, 26.7606, 83.3732, accuracy);
    eq(`accuracy: ${label} fails closed`,
       uncertain && uncertain.unrouted_reason, "location_uncertain");
  }
  const edgePoint = region.geometry.type === "Polygon"
    ? region.geometry.coordinates[0][0]
    : region.geometry.coordinates[0][0][0];
  const edge = await P.uttarPradeshRouteFromGeocode(
    staleUttarPradesh, edgePoint[1], edgePoint[0], 5);
  eq("boundary: a five-metre circle touching the state edge fails closed",
     edge && edge.unrouted_reason, "location_uncertain");

  // Exact checked-in NE-6 line vertex near Kanpur.
  const highway = await P.routeOfficer(
    {state: "Uttar Pradesh", country_code: "in"},
    26.46653, 80.44522, 5, null, null, "road_damage");
  eq("precedence: NE-6 beats the Uttar Pradesh statewide handoff",
     highway && [highway.authority_id, highway.highway_ref],
     ["in-national-highway", "NE-6"]);

  const delhi = await P.routeOfficer(
    {city: "Delhi", state: "Delhi", country_code: "in"},
    28.6129, 77.2295, 12, null, null, "garbage");
  eq("precedence: exact Delhi NCT remains on its Delhi route",
     delhi && [delhi.authority_id, delhi.routing_pack_id],
     ["dl-pwd-sewa", "in-dl-routing"]);

  for (const [city, lat, lng] of [
    ["Kanpur", 26.4609, 80.3218], ["Lucknow", 26.8381, 80.9346],
    ["Ghaziabad", 28.6712, 77.4120], ["Agra", 27.1753, 78.0098],
    ["Varanasi", 25.3356, 83.0076], ["Meerut", 28.9963, 77.7062],
    ["Prayagraj", 25.4381, 81.8338],
  ]) {
    const current = await P.routeOfficer(
      {city, state:"Uttar Pradesh", country_code:"in"},
      lat, lng, 12, null, null, "garbage");
    eq(`supersession: ${city} uses exact statewide containment`,
       current && [current.authority_id, current.routing_pack_id, current.region],
       ["up-statewide-unverified", "in-up-routing", "uttar-pradesh-state"]);
  }

  const gwalior = await P.routeOfficer(
    {city:"Gwalior", state:"Madhya Pradesh", country_code:"in"},
    26.2037247, 78.1573628, 12, null, null, "garbage");
  eq("cross-state: Gwalior uses exact Madhya Pradesh statewide containment",
     gwalior && [gwalior.authority_id, gwalior.routing_pack_id, gwalior.region],
     ["mp-statewide-unverified", "in-mp-routing", "madhya-pradesh-state"]);

  ok("saved binding: valid current Uttar Pradesh record is accepted",
     saved && await P.savedOfficialRouteBinding(
       saved, "in-up-routing", "up-statewide-unverified", pack), saved);
  const rejected = async (name, changes) => {
    const candidate = {...saved, ...changes};
    const binding = await P.savedOfficialRouteBinding(
      candidate, "in-up-routing", "up-statewide-unverified", pack);
    eq(name, binding, null);
  };
  await rejected("saved binding: cross-pack ID is rejected",
    {routing_pack_id: "in-top50-routing"});
  await rejected("saved binding: forged digest is rejected",
    {routing_pack_sha256: "0".repeat(64)});
  await rejected("saved binding: cross-region binding is rejected",
    {region: "lucknow"});
  await rejected("saved binding: outside coordinates are rejected",
    {lat: 28.6129, lng: 77.2295});
  await rejected("saved binding: over-30m accuracy is rejected", {gps_accuracy: 31});
  await rejected("saved binding: changed source is rejected",
    {routing_source: "nominatim_structured_city"});

  return checks;
}
"""


def open_page(browser, *, override=None, delhi_override=None):
    context = browser.new_context(viewport={"width": 390, "height": 844})
    if override is not None:
        context.route(route_pattern(PACK_ID), override)
    if delhi_override is not None:
        context.route(route_pattern("in-dl-routing"), delhi_override)
    page = context.new_page()
    page.goto(APP)
    page.wait_for_load_state("networkidle")
    page.wait_for_function(
        "() => window.StandaloneAPI && StandaloneAPI.__pure "
        "&& typeof StandaloneAPI.__pure.uttarPradeshRouteFromGeocode === 'function'",
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
            failures.append(f"unexpected Uttar Pradesh adapter: {pack.get('adapter')!r}")
        if pack.get("state_code") != "UP" or pack.get("pack_id") != PACK_ID:
            failures.append("Uttar Pradesh hosted pack identity is invalid")
        if payload.get("retrieved_at") != "2026-08-25":
            failures.append("Uttar Pradesh retrieval date is not pinned")
        if actual_digest != EXPECTED_GEOMETRY_SHA256:
            failures.append(f"Uttar Pradesh geometry digest changed: {actual_digest}")
        if region.get("geometry_sha256") != EXPECTED_GEOMETRY_SHA256:
            failures.append("Uttar Pradesh payload does not record the pinned geometry digest")
        if hashlib.sha256(raw).hexdigest() != resource_for(PACK_ID).get("sha256"):
            failures.append("Uttar Pradesh hosted pack digest changed after validation")
    except (AssertionError, KeyError, OSError, TypeError, ValueError) as error:
        failures.append(f"Uttar Pradesh routing pack pin is invalid: {error}")

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

        # Delhi's pack is an independent candidate. Its failure inside the broad NCR
        # envelope must not block exact Uttar Pradesh containment in Ghaziabad.
        context, page = open_page(
            browser,
            delhi_override=lambda route: route.fulfill(status=404, body="missing"),
        )
        ghaziabad = page.evaluate("""async () => StandaloneAPI.__pure.routeOfficer(
          {city:'Ghaziabad',state:'Uttar Pradesh',country_code:'in'},
          28.6711527,77.4120356,12,null,null,'garbage')""")
        if [ghaziabad.get("authority_id"), ghaziabad.get("routing_pack_id")] \
                != ["up-statewide-unverified", "in-up-routing"]:
            failures.append(f"missing Delhi pack blocked valid Ghaziabad: {ghaziabad!r}")
        extra_checks += 1
        context.close()

        for label, responder in [
            ("missing", lambda route: route.fulfill(status=404, body="missing")),
            (
                "tampered",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=raw.replace(b"Uttar Pradesh", b"Xttar Pradesh", 1),
                ),
            ),
        ]:
            context, page = open_page(browser, override=responder)
            failed = page.evaluate("""async () => {
              const P = StandaloneAPI.__pure;
              const direct = await P.uttarPradeshRouteFromGeocode(
                null,26.8381,80.9346,12);
              const orchestrated = await P.routeOfficer(
                {city:'Lucknow',state:'Uttar Pradesh',country_code:'in'},
                26.8381,80.9346,12,null,null,'garbage');
              const unrelated = await P.routeOfficer(
                {city:'Gwalior',state:'Madhya Pradesh',country_code:'in'},
                26.2037247,78.1573628,12,null,null,'garbage');
              return {direct, orchestrated, unrelated};
            }""")
            for route_name in ("direct", "orchestrated"):
                result = failed[route_name]
                if not isinstance(result, dict) or result.get("routed") is not False \
                        or result.get("unrouted_reason") != "jurisdiction_unavailable":
                    failures.append(
                        f"{label} UP pack allowed {route_name} or old city fallback: {result!r}"
                    )
            unrelated = failed["unrelated"]
            if [unrelated.get("authority_id"), unrelated.get("routing_pack_id")] \
                    != ["mp-statewide-unverified", "in-mp-routing"]:
                failures.append(
                    f"{label} UP pack blocked unrelated Madhya Pradesh route: {unrelated!r}"
                )
            extra_checks += len(failed)
            context.close()
        browser.close()

    if failures:
        print("FAIL")
        for failure in failures:
            print("  -", failure)
        raise SystemExit(1)
    print(f"UTTAR PRADESH ROUTING TEST PASS ({len(results) + extra_checks + 5} checks)")


if __name__ == "__main__":
    main()
