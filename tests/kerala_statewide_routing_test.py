# -*- coding: utf-8 -*-
"""Kerala routes by its exact state polygon before legacy city-name compatibility routes."""

from __future__ import annotations

import hashlib
import json

from playwright.sync_api import sync_playwright

from state_pack_utils import read_pack, read_payload, resource_for, route_pattern


APP = "http://localhost:8765/"
PACK_ID = "in-kl-routing"
EXPECTED_GEOMETRY_SHA256 = (
    "51e226750b1d6c08a5030e6074e2641282e01c328f46d8aee741de664bef705c"
)
INSIDE = [
    {"name": "Thiruvananthapuram", "lat": 8.5241, "lng": 76.9366},
    {"name": "Kochi", "lat": 9.9312, "lng": 76.2673},
    {"name": "Kozhikode", "lat": 11.2588, "lng": 75.7804},
    {"name": "Kannur", "lat": 11.8745, "lng": 75.3704},
    # Non-top-50 interior points prove that this is not a city-name expansion.
    {"name": "rural Idukki", "lat": 9.8500, "lng": 77.0500},
    {"name": "Wayanad", "lat": 11.6854, "lng": 76.1320},
]
OUTSIDE = [
    # Mahe is a Puducherry enclave and must not inherit Kerala from a stale geocoder.
    {"name": "Mahe", "lat": 11.7006, "lng": 75.5360},
    {"name": "Mangaluru", "lat": 12.9141, "lng": 74.8560},
    {"name": "Kanyakumari", "lat": 8.0883, "lng": 77.5385},
    {"name": "Lakshadweep", "lat": 10.5667, "lng": 72.6417},
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
  const resource = manifest && manifest.resources && manifest.resources["in-kl-routing"];
  const coverage = await P.keralaCoverage();
  const region = coverage && coverage.region;
  const pack = await P.loadStatePack("in-kl-routing");
  const legacyCities = await P.majorCityCoverage();

  eq("pack: v1.35 has forty-two independently pinned resources",
     Object.keys(manifest && manifest.resources || {}).length, 42);
  ok("pack: statewide manifest entry exists", resource, manifest);
  eq("pack: adapter is data-only", resource && resource.adapter,
     "statewide-general-v1");
  eq("pack: state code is Kerala", resource && resource.state_code, "KL");
  eq("pack: full-state scope is explicit", resource && resource.statewide, true);
  ok("pack: content digest is pinned",
     resource && /^[0-9a-f]{64}$/.test(resource.sha256), resource);
  eq("pack: path is content-addressed", resource && resource.path,
     resource && `packs/v1/states/kl/routing-${resource.sha256}.json`);
  eq("pack: statewide rollout does not mutate the compatibility city inventory",
     legacyCities && legacyCities.regions && legacyCities.regions.length, 35);

  ok("coverage: pinned payload loads", region, coverage);
  eq("coverage: stable region ID", region && region.id, "kerala-state");
  eq("coverage: stable authority ID", region && region.authority_id,
     "kl-statewide-unverified");
  eq("coverage: relation ID is pinned", region && region.osm_relation_id, 2018151);
  eq("coverage: geometry digest is pinned", region && region.geometry_sha256,
     P.KERALA_STATE_GEOMETRY_SHA256);
  ok("coverage: source attribution is explicit",
     /OpenStreetMap contributors/.test(region && region.source_name || "")
       && /ODbL/.test(region && region.source_license || ""), region);
  ok("coverage: scope explicitly excludes Mahe",
     /excludes Mahe, Puducherry Union Territory/i.test(region && region.scope || ""),
     region && region.scope);
  ok("coverage: limitations disclaim ownership and automatic submission",
     (region && region.limitations || []).some((item) => /ownership is not inferred/i.test(item))
       && (region && region.limitations || []).some((item) => /does not submit/i.test(item)),
     region && region.limitations);

  eq("registry: statewide expansion is versioned", P.AUTHORITY_REGISTRY_VERSION, 18);
  eq("registry: stable statewide authority is installed",
     P.KERALA_STATE_AUTHORITY.id, "kl-statewide-unverified");
  eq("registry: primary CMO grievance handoff",
     P.KERALA_STATE_AUTHORITY.handoff_url,
     "https://complaints.cmo.kerala.gov.in/cmoportal/login.htm?lang=en");
  eq("registry: K-SMART remains the local-body alternative",
     P.KERALA_STATE_AUTHORITY.alternate_handoff_url,
     "https://ksmart.lsgkerala.gov.in/ui/web-portal/services");
  ok("registry: installed official routes pass structural validation",
     P.validateOfficialHandoffRegistry(P.OFFICIAL_AUTHORITIES), null);

  let saved = null;
  for (const fixture of inside) {
    const raw = await P.keralaRouteFromGeocode(null, fixture.lat, fixture.lng, 12);
    ok(`${fixture.name}: coordinates route without a geocoder`, raw && raw.routed, raw);
    eq(`${fixture.name}: neutral Kerala authority`, raw && raw.authority_id,
       "kl-statewide-unverified");
    eq(`${fixture.name}: exact state-boundary evidence`, raw && [
      raw.routing_source, raw.routing_match_field, raw.routing_match_value, raw.region,
    ], [
      "osm_kerala_state_boundary", "boundary",
      "Kerala (OpenStreetMap relation 2018151)", "kerala-state",
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
      "in-kl-routing", resource && resource.pack_version,
      resource && resource.sha256, "KL",
    ]);
    ok(`${fixture.name}: routing never claims submission`, absent(raw, [
      "official_grievance_id", "submitted_at", "sent_at", "handoff_opened_at",
    ]), raw);
    for (const issue of ["road_damage", "garbage", "open_manhole"]) {
      const typed = P.routeForIssue(raw, issue);
      eq(`${fixture.name}/${issue}: remains routable`, typed && typed.routed, true);
      eq(`${fixture.name}/${issue}: keeps Kerala authority`,
         typed && typed.authority_id, "kl-statewide-unverified");
      eq(`${fixture.name}/${issue}: issue is explicit`, typed && typed.issue_type, issue);
      eq(`${fixture.name}/${issue}: never becomes tender-eligible`,
         typed && typed.tender_eligible, false);
    }
    if (fixture.name === "rural Idukki") {
      saved = {...raw, ...fixture, gps_accuracy: 12, issue_type: "road_damage"};
    }
  }

  const staleKerala = {
    city: "Kozhikode", state: "Kerala", country_code: "in",
    full: "stale Kerala address",
  };
  for (const fixture of outside) {
    const direct = await P.keralaRouteFromGeocode(
      staleKerala, fixture.lat, fixture.lng, 12);
    eq(`${fixture.name}: stale state label cannot cross the polygon`, direct, null);
  }

  const atLimit = await P.keralaRouteFromGeocode(null, 9.85, 77.05, 30);
  eq("accuracy: 30 metres is accepted away from the state edge",
     atLimit && atLimit.authority_id, "kl-statewide-unverified");
  for (const [label, accuracy] of [
    ["31 metres", 31], ["negative", -1], ["not finite", Number.NaN],
  ]) {
    const uncertain = await P.keralaRouteFromGeocode(null, 9.85, 77.05, accuracy);
    eq(`accuracy: ${label} fails closed`,
       uncertain && uncertain.unrouted_reason, "location_uncertain");
  }
  const edgePoint = region.geometry.type === "Polygon"
    ? region.geometry.coordinates[0][0]
    : region.geometry.coordinates[0][0][0];
  const edge = await P.keralaRouteFromGeocode(
    staleKerala, edgePoint[1], edgePoint[0], 5);
  eq("boundary: a five-metre circle touching the state edge fails closed",
     edge && edge.unrouted_reason, "location_uncertain");

  // Exact checked-in NH-66 line vertex in Kochi.
  const highway = await P.routeOfficer(
    {state: "Kerala", country_code: "in"},
    9.87727, 76.30379, 5, null, null, "road_damage");
  eq("precedence: NH-66 beats the Kerala state handoff",
     highway && [highway.authority_id, highway.highway_ref],
     ["in-national-highway", "NH-66"]);

  const kochi = await P.routeOfficer(
    {city: "Kochi", state: "Kerala", country_code: "in"},
    9.9312, 76.2673, 12, null, null, "garbage");
  eq("precedence: exact state geometry supersedes the legacy Kochi city route",
     kochi && [kochi.authority_id, kochi.routing_pack_id],
     ["kl-statewide-unverified", "in-kl-routing"]);
  const kozhikode = await P.routeOfficer(
    {city: "Kozhikode", state: "Kerala", country_code: "in"},
    11.2588, 75.7804, 12, null, null, "open_manhole");
  eq("precedence: exact state geometry supersedes the legacy Kozhikode city route",
     kozhikode && [kozhikode.authority_id, kozhikode.routing_pack_id],
     ["kl-statewide-unverified", "in-kl-routing"]);

  ok("saved binding: valid current Kerala record is accepted",
     saved && await P.savedOfficialRouteBinding(
       saved, "in-kl-routing", "kl-statewide-unverified", pack), saved);
  const rejected = async (name, changes) => {
    const candidate = {...saved, ...changes};
    const binding = await P.savedOfficialRouteBinding(
      candidate, "in-kl-routing", "kl-statewide-unverified", pack);
    eq(name, binding, null);
  };
  await rejected("saved binding: cross-pack ID is rejected",
    {routing_pack_id: "in-top50-routing"});
  await rejected("saved binding: forged digest is rejected",
    {routing_pack_sha256: "0".repeat(64)});
  await rejected("saved binding: cross-region binding is rejected", {region: "kochi"});
  await rejected("saved binding: Mahe coordinates are rejected",
    {lat: 11.7006, lng: 75.5360});
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
        "&& typeof StandaloneAPI.__pure.keralaRouteFromGeocode === 'function'",
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
            failures.append(f"unexpected Kerala adapter: {pack.get('adapter')!r}")
        if pack.get("state_code") != "KL" or pack.get("pack_id") != PACK_ID:
            failures.append("Kerala hosted pack identity is invalid")
        if payload.get("retrieved_at") != "2026-08-25":
            failures.append("Kerala retrieval date is not pinned")
        if actual_digest != EXPECTED_GEOMETRY_SHA256:
            failures.append(f"Kerala geometry digest changed: {actual_digest}")
        if region.get("geometry_sha256") != EXPECTED_GEOMETRY_SHA256:
            failures.append("Kerala payload does not record the pinned geometry digest")
        if hashlib.sha256(raw).hexdigest() != resource_for(PACK_ID).get("sha256"):
            failures.append("Kerala hosted pack digest changed after validation")
    except (AssertionError, KeyError, OSError, TypeError, ValueError) as error:
        failures.append(f"Kerala routing pack pin is invalid: {error}")

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
                    body=raw.replace(b"Kerala", b"Xerala", 1),
                ),
            ),
        ]:
            context, page = open_page(browser, responder)
            failed = page.evaluate("""async () => {
              const P = StandaloneAPI.__pure;
              const direct = await P.keralaRouteFromGeocode(null,9.9312,76.2673,12);
              const orchestrated = await P.routeOfficer(
                {city:'Kochi',state:'Kerala',country_code:'in'},
                9.9312,76.2673,12,null,null,'garbage');
              return {direct, orchestrated};
            }""")
            for route_name, result in failed.items():
                if not isinstance(result, dict) or result.get("routed") is not False \
                        or result.get("unrouted_reason") != "jurisdiction_unavailable":
                    failures.append(
                        f"{label} Kerala pack allowed {route_name} or old city fallback: {result!r}"
                    )
            extra_checks += len(failed)
            context.close()
        browser.close()

    if failures:
        print("FAIL")
        for failure in failures:
            print("  -", failure)
        raise SystemExit(1)
    print(f"KERALA STATEWIDE ROUTING TEST PASS ({len(results) + extra_checks + 5} checks)")


if __name__ == "__main__":
    main()
