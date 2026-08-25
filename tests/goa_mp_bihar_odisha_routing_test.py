#!/usr/bin/env python3
"""Four exact state polygons route safely and preserve highway/neighbor precedence."""

from __future__ import annotations

import hashlib
import json
import sys

from playwright.sync_api import sync_playwright

from state_pack_utils import read_pack, read_payload, resource_for, route_pattern


APP = "http://localhost:8765/"
STATES = [
    {
        "name": "Goa", "code": "GA", "pack": "in-ga-routing",
        "region": "goa-state", "authority": "ga-statewide-unverified",
        "relation": 11251493, "source": "osm_goa_state_boundary",
        "digest": "f4c47a79a3671d333d47f66a597d66b6295a78b1cd7cd3cba7bc2db472190e4f",
        "coverage_fn": "goaCoverage", "route_fn": "goaRouteFromGeocode",
        "inside": [["Panaji", 15.4909, 73.8278], ["Margao", 15.2832, 73.9862],
                   ["Mapusa", 15.5915, 73.80898], ["Canacona", 15.0100, 74.0232]],
        "outside": [["Sawantwadi", 15.9048, 73.8213],
                    ["Belagavi", 15.8497, 74.4977], ["Karwar", 14.8136, 74.1297]],
        "highway": [15.68544, 73.83161, "NH-66"],
        "handoff": "CM Helpline Goa", "url": "https://cmhelpline.dpg.goa.gov.in/",
        "package": "in.gov.dpg.cmhelpline", "helpline": "1905",
    },
    {
        "name": "Madhya Pradesh", "code": "MP", "pack": "in-mp-routing",
        "region": "madhya-pradesh-state", "authority": "mp-statewide-unverified",
        "relation": 1950071, "source": "osm_madhya_pradesh_state_boundary",
        "digest": "24f0c93ed8bd40c4c6b4e1f650c3b9870b1e65ccd5d7b00ea0193a8a5aedc357",
        "coverage_fn": "madhyaPradeshCoverage", "route_fn": "madhyaPradeshRouteFromGeocode",
        "inside": [["Bhopal", 23.2599, 77.4126], ["Indore", 22.7196, 75.8577],
                   ["Gwalior", 26.2183, 78.1828], ["Jabalpur", 23.1815, 79.9864]],
        "outside": [["Kota", 25.2138, 75.8648], ["Nagpur", 21.1458, 79.0882],
                    ["Raipur", 21.2514, 81.6296]],
        "highway": [23.33699, 77.39263, "NH-46"],
        "handoff": "Madhya Pradesh CM Helpline",
        "url": "https://www.cmhelpline.mp.gov.in/Public/VerifyOTPBeforeOnlineComplaint.aspx",
        "package": "com.magnum.helpline", "helpline": "181",
    },
    {
        "name": "Bihar", "code": "BR", "pack": "in-br-routing",
        "region": "bihar-state", "authority": "br-statewide-unverified",
        "relation": 1958982, "source": "osm_bihar_state_boundary",
        "digest": "3d846e20cfee28a656d6dd808c4dad37a4f1c95852f9f292b0acefde708f4b24",
        "coverage_fn": "biharCoverage", "route_fn": "biharRouteFromGeocode",
        "inside": [["Patna", 25.5941, 85.1376], ["Gaya", 24.7955, 85.0002],
                   ["Muzaffarpur", 26.1197, 85.3910], ["Bhagalpur", 25.2425, 86.9842]],
        "outside": [["Varanasi", 25.3176, 82.9739], ["Ranchi", 23.3441, 85.3096],
                    ["Birgunj", 27.0104, 84.8778]],
        "highway": [25.59963, 85.13462, "NH-139"],
        "handoff": "Bihar Lok Shikayat",
        "url": "https://lokshikayat.bihar.gov.in/Default.aspx",
        "package": "com.bpsms.jansamadhan", "helpline": "18003456284",
    },
    {
        "name": "Odisha", "code": "OD", "pack": "in-od-routing",
        "region": "odisha-state", "authority": "od-statewide-unverified",
        "relation": 1984022, "source": "osm_odisha_state_boundary",
        "digest": "af0fe4941b6cdd2abe5dc5717db8875bec6b68a2d6671002d2afc9c7d37d5179",
        "coverage_fn": "odishaCoverage", "route_fn": "odishaRouteFromGeocode",
        "inside": [["Bhubaneswar", 20.2961, 85.8245], ["Cuttack", 20.4625, 85.8830],
                   ["Rourkela", 22.2604, 84.8536], ["Koraput", 18.8120, 82.7100]],
        "outside": [["Raipur", 21.2514, 81.6296], ["Jamshedpur", 22.8046, 86.2029],
                    ["Visakhapatnam", 17.6868, 83.2185]],
        "highway": [19.94542, 84.57829, "NH-157"],
        "handoff": "Odisha Jana Sunani",
        "url": "https://janasunani.odisha.gov.in/grievance-details",
        "package": "com.sociomatic.janasunani", "helpline": "155335",
        "whatsapp": "https://wa.me/916370951930",
    },
]


SCENARIO = r"""
async (states) => {
  const P = StandaloneAPI.__pure;
  const checks = [];
  const eq = (name, got, want) => checks.push([
    name, JSON.stringify(got) === JSON.stringify(want), got, want,
  ]);
  const ok = (name, value, detail) => checks.push([
    name, !!value, detail === undefined ? value : detail, true,
  ]);
  const manifest = await P.getStatePackManifest();
  eq("catalog: v1.35 has 42 reviewed resources",
     Object.keys(manifest && manifest.resources || {}).length, 42);
  eq("registry: nationwide release is versioned", P.AUTHORITY_REGISTRY_VERSION, 18);

  for (const state of states) {
    const resource = manifest.resources[state.pack];
    const coverage = await P[state.coverage_fn]();
    const pack = await P.loadStatePack(state.pack);
    const region = coverage && coverage.region;
    const authority = pack && pack.authorities && pack.authorities[0];
    eq(`${state.name}: catalog identity`, resource && [
      resource.pack_id, resource.state_code, resource.adapter, resource.statewide,
    ], [state.pack, state.code, "statewide-general-v1", true]);
    eq(`${state.name}: exact boundary pin`, region && [
      region.id, region.authority_id, region.osm_relation_id, region.geometry_sha256,
    ], [state.region, state.authority, state.relation, state.digest]);
    eq(`${state.name}: official handoff`, authority && [
      authority.id, authority.handoff_name, authority.handoff_url,
      authority.handoff_package || null, authority.helpline || null,
    ], [state.authority, state.handoff, state.url, state.package, state.helpline]);
    if (state.whatsapp) {
      eq(`${state.name}: official WhatsApp`, authority && authority.whatsapp_url,
         state.whatsapp);
    }
    ok(`${state.name}: limitations disclaim ownership and automatic filing`,
       (region.limitations || []).some((item) => /ownership is not inferred/i.test(item))
         && (region.limitations || []).some((item) => /does not submit/i.test(item)),
       region.limitations);

    let saved = null;
    for (const [place, lat, lng] of state.inside) {
      const raw = await P[state.route_fn](null, lat, lng, 12);
      eq(`${state.name}/${place}: exact statewide route`, raw && [
        raw.routed, raw.authority_id, raw.routing_pack_id, raw.region,
        raw.routing_source, raw.routing_match_field, raw.routing_match_value,
      ], [true, state.authority, state.pack, state.region, state.source, "boundary",
        `${state.name} (OpenStreetMap relation ${state.relation})`]);
      eq(`${state.name}/${place}: neutral verified handoff`, raw && [
        raw.delivery_channel, raw.officer_email, raw.ownership_unverified,
        raw.requires_official_reference, raw.tender_eligible,
      ], ["official_handoff", null, true, true, false]);
      for (const issue of ["road_damage", "garbage", "open_manhole"]) {
        const typed = P.routeForIssue(raw, issue);
        eq(`${state.name}/${place}/${issue}: remains statewide`, typed && [
          typed.routed, typed.authority_id, typed.issue_type, typed.tender_eligible,
        ], [true, state.authority, issue, false]);
      }
      if (!saved) saved = {...raw, lat, lng, gps_accuracy:12, issue_type:"road_damage"};
    }

    for (const [place, lat, lng] of state.outside) {
      eq(`${state.name}/${place}: stale label cannot cross polygon`,
         await P[state.route_fn]({state:state.name,country_code:"in"},lat,lng,12), null);
    }
    const [place, lat, lng] = state.inside[0];
    eq(`${state.name}: 30m accepted`,
       (await P[state.route_fn](null,lat,lng,30)).authority_id, state.authority);
    for (const [label, accuracy] of [["31m",31],["negative",-1],["NaN",Number.NaN]]) {
      const route = await P[state.route_fn](null,lat,lng,accuracy);
      eq(`${state.name}: ${label} fails closed`, route && route.unrouted_reason,
         "location_uncertain");
    }
    const vertex = region.geometry.type === "Polygon"
      ? region.geometry.coordinates[0][0] : region.geometry.coordinates[0][0][0];
    eq(`${state.name}: boundary-touching accuracy fails closed`,
       (await P[state.route_fn](null,vertex[1],vertex[0],5)).unrouted_reason,
       "location_uncertain");

    const [nhLat, nhLng, nhRef] = state.highway;
    const highway = await P.routeOfficer(
      {state:state.name,country_code:"in"},nhLat,nhLng,5,null,null,"road_damage");
    eq(`${state.name}: mapped NH keeps first refusal`, highway && [
      highway.authority_id, highway.highway_ref,
    ], ["in-national-highway",nhRef]);
    const civic = await P.routeOfficer(
      {state:state.name,country_code:"in"},nhLat,nhLng,5,null,null,"garbage");
    eq(`${state.name}: civic issue beside NH stays statewide`, civic && [
      civic.authority_id,civic.routing_pack_id,
    ], [state.authority,state.pack]);

    ok(`${state.name}: current saved binding is accepted`,
       saved && await P.savedOfficialRouteBinding(saved,state.pack,state.authority,pack), saved);
    eq(`${state.name}: forged saved digest is rejected`,
       await P.savedOfficialRouteBinding(
         {...saved,routing_pack_sha256:"0".repeat(64)},state.pack,state.authority,pack), null);
    eq(`${state.name}: outside saved coordinates are rejected`,
       await P.savedOfficialRouteBinding(
         {...saved,lat:state.outside[0][1],lng:state.outside[0][2]},
         state.pack,state.authority,pack), null);
  }

  for (const [city,stateName,lat,lng,authority,pack,region] of [
    ["Indore","Madhya Pradesh",22.7196,75.8577,"mp-statewide-unverified","in-mp-routing","madhya-pradesh-state"],
    ["Bhopal","Madhya Pradesh",23.2599,77.4126,"mp-statewide-unverified","in-mp-routing","madhya-pradesh-state"],
    ["Jabalpur","Madhya Pradesh",23.1815,79.9864,"mp-statewide-unverified","in-mp-routing","madhya-pradesh-state"],
    ["Gwalior","Madhya Pradesh",26.2183,78.1828,"mp-statewide-unverified","in-mp-routing","madhya-pradesh-state"],
    ["Patna","Bihar",25.5941,85.1376,"br-statewide-unverified","in-br-routing","bihar-state"],
  ]) {
    const route = await P.routeOfficer(
      {city,state:stateName,country_code:"in"},lat,lng,12,null,null,"garbage");
    eq(`${city}: legacy top-50 route is superseded`, route && [
      route.authority_id,route.routing_pack_id,route.region,
    ], [authority,pack,region]);
  }
  return checks;
}
"""


def open_page(browser, pack_id: str | None = None, responder=None):
    context = browser.new_context(viewport={"width": 390, "height": 844})
    if pack_id and responder:
        context.route(route_pattern(pack_id), responder)
    page = context.new_page()
    page.goto(APP)
    page.wait_for_load_state("networkidle")
    page.wait_for_function(
        "() => window.StandaloneAPI && StandaloneAPI.__pure "
        "&& typeof StandaloneAPI.__pure.odishaRouteFromGeocode === 'function'",
        timeout=30_000,
    )
    return context, page


def main() -> None:
    failures: list[str] = []
    raw_by_pack: dict[str, bytes] = {}
    for state in STATES:
        pack, raw = read_pack(state["pack"])
        payload = read_payload(state["pack"])
        region = payload["region"]
        geometry = json.dumps(
            region["geometry"], ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        if pack.get("pack_id") != state["pack"] or pack.get("state_code") != state["code"]:
            failures.append(f"{state['name']} hosted pack identity is invalid")
        if payload.get("retrieved_at") != "2026-08-25":
            failures.append(f"{state['name']} retrieval date is not pinned")
        if hashlib.sha256(geometry).hexdigest() != state["digest"]:
            failures.append(f"{state['name']} geometry digest changed")
        if hashlib.sha256(raw).hexdigest() != resource_for(state["pack"]).get("sha256"):
            failures.append(f"{state['name']} hosted pack digest differs")
        raw_by_pack[state["pack"]] = raw

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--disable-web-security"])
        context, page = open_page(browser)
        results = page.evaluate(SCENARIO, STATES)
        context.close()
        for name, passed, got, want in results:
            if not passed:
                failures.append(name)
                print(f"  FAIL {name}\n         got  {got!r}\n         want {want!r}")

        for state in STATES:
            raw = raw_by_pack[state["pack"]]
            for label, responder in [
                ("missing", lambda route, _request=None: route.fulfill(status=404, body="missing")),
                ("tampered", lambda route, _request=None, raw=raw,
                 state_name=state["name"]: route.fulfill(
                    status=200, content_type="application/json",
                    body=raw.replace(state_name.encode(), b"X" + state_name.encode()[1:], 1),
                )),
            ]:
                context, page = open_page(browser, state["pack"], responder)
                place, lat, lng = state["inside"][0]
                values = page.evaluate("""async (state) => {
                  const P = StandaloneAPI.__pure;
                  return {
                    direct: await P[state.route_fn](null,state.inside[0][1],state.inside[0][2],12),
                    routed: await P.routeOfficer(
                      {city:state.inside[0][0],state:state.name,country_code:'in'},
                      state.inside[0][1],state.inside[0][2],12,null,null,'garbage'),
                  };
                }""", state)
                for key, route in values.items():
                    if route.get("routed") is not False \
                            or route.get("unrouted_reason") != "jurisdiction_unavailable":
                        failures.append(
                            f"{label} {state['name']} pack allowed {key} fallback: {route!r}"
                        )
                context.close()

        # Missing Bihar/Odisha packs must not block neighbouring structured-city routes.
        for state, neighbor in [
            (next(item for item in STATES if item["code"] == "BR"),
             {"city":"Ranchi","state":"Jharkhand","lat":23.3441,"lng":85.3096,
              "authority":"in-jh-municipal-grievance"}),
            (next(item for item in STATES if item["code"] == "OD"),
             {"city":"Jamshedpur","state":"Jharkhand","lat":22.8046,"lng":86.2029,
              "authority":"in-jh-municipal-grievance"}),
        ]:
            context, page = open_page(
                browser, state["pack"],
                lambda route, _request=None: route.fulfill(status=404, body="missing"),
            )
            result = page.evaluate("""async (n) => StandaloneAPI.__pure.routeOfficer(
              {city:n.city,state:n.state,country_code:'in'},n.lat,n.lng,12,null,null,'garbage')
            """, neighbor)
            if result.get("authority_id") != neighbor["authority"]:
                failures.append(
                    f"missing {state['name']} pack blocked {neighbor['city']}: {result!r}"
                )
            context.close()
        browser.close()

    if failures:
        print("GOA/MP/BIHAR/ODISHA ROUTING TEST FAIL")
        for failure in failures:
            print("  -", failure)
        raise SystemExit(1)
    print(f"GOA/MP/BIHAR/ODISHA ROUTING TEST PASS ({len(results) + 34} checks)")


if __name__ == "__main__":
    main()
