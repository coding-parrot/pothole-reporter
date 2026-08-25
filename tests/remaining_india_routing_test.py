#!/usr/bin/env python3
"""The final 13 states and seven UTs route by exact polygons, never coarse labels."""

from __future__ import annotations

from playwright.sync_api import sync_playwright

from state_pack_utils import read_pack, resource_for


APP = "http://localhost:8765/"
PACK_IDS = [
    "in-ar-routing",
    "in-as-routing",
    "in-gj-state-routing",
    "in-hr-routing",
    "in-hp-routing",
    "in-jh-routing",
    "in-mn-routing",
    "in-ml-routing",
    "in-mz-routing",
    "in-nl-routing",
    "in-sk-routing",
    "in-tr-routing",
    "in-uk-routing",
    "in-an-routing",
    "in-ch-routing",
    "in-dh-routing",
    "in-jk-routing",
    "in-la-routing",
    "in-ld-routing",
    "in-py-routing",
]

# Independently reviewed city coordinates. They are intentionally not derived from the
# generated polygons, so moving or widening a polygon cannot make an invalid test pass.
JURISDICTIONS = [
    {
        "name": "Arunachal Pradesh", "code": "AR", "kind": "state",
        "pack": "in-ar-routing", "region": "arunachal-pradesh-state",
        "authority": "ar-statewide-unverified", "relation": 2027346,
        "place": "Itanagar", "lat": 27.0844, "lng": 93.6053,
    },
    {
        "name": "Assam", "code": "AS", "kind": "state",
        "pack": "in-as-routing", "region": "assam-state",
        "authority": "as-statewide-unverified", "relation": 2025886,
        "place": "Guwahati", "lat": 26.1445, "lng": 91.7362,
    },
    {
        "name": "Gujarat", "code": "GJ", "kind": "state",
        "pack": "in-gj-state-routing", "region": "gujarat-state",
        "authority": "gj-statewide-unverified", "relation": 1949080,
        "place": "Gandhinagar", "lat": 23.2156, "lng": 72.6369,
    },
    {
        "name": "Haryana", "code": "HR", "kind": "state",
        "pack": "in-hr-routing", "region": "haryana-state",
        "authority": "hr-statewide-unverified", "relation": 1942601,
        "place": "Gurugram", "lat": 28.4595, "lng": 77.0266,
    },
    {
        "name": "Himachal Pradesh", "code": "HP", "kind": "state",
        "pack": "in-hp-routing", "region": "himachal-pradesh-state",
        "authority": "hp-statewide-unverified", "relation": 364186,
        "place": "Shimla", "lat": 31.1048, "lng": 77.1734,
    },
    {
        "name": "Jharkhand", "code": "JH", "kind": "state",
        "pack": "in-jh-routing", "region": "jharkhand-state",
        "authority": "jh-statewide-unverified", "relation": 1960191,
        "place": "Bokaro", "lat": 23.6693, "lng": 86.1511,
    },
    {
        "name": "Manipur", "code": "MN", "kind": "state",
        "pack": "in-mn-routing", "region": "manipur-state",
        "authority": "mn-statewide-unverified", "relation": 2027869,
        "place": "Imphal", "lat": 24.8170, "lng": 93.9368,
    },
    {
        "name": "Meghalaya", "code": "ML", "kind": "state",
        "pack": "in-ml-routing", "region": "meghalaya-state",
        "authority": "ml-statewide-unverified", "relation": 2027521,
        "place": "Shillong", "lat": 25.5788, "lng": 91.8933,
    },
    {
        "name": "Mizoram", "code": "MZ", "kind": "state",
        "pack": "in-mz-routing", "region": "mizoram-state",
        "authority": "mz-statewide-unverified", "relation": 2029046,
        "place": "Aizawl", "lat": 23.7271, "lng": 92.7176,
    },
    {
        "name": "Nagaland", "code": "NL", "kind": "state",
        "pack": "in-nl-routing", "region": "nagaland-state",
        "authority": "nl-statewide-unverified", "relation": 2027973,
        "place": "Kohima", "lat": 25.6751, "lng": 94.1086,
    },
    {
        "name": "Sikkim", "code": "SK", "kind": "state",
        "pack": "in-sk-routing", "region": "sikkim-state",
        "authority": "sk-statewide-unverified", "relation": 1791324,
        "place": "Gangtok", "lat": 27.3314, "lng": 88.6138,
    },
    {
        "name": "Tripura", "code": "TR", "kind": "state",
        "pack": "in-tr-routing", "region": "tripura-state",
        "authority": "tr-statewide-unverified", "relation": 2026458,
        "place": "Agartala", "lat": 23.8315, "lng": 91.2868,
    },
    {
        "name": "Uttarakhand", "code": "UK", "kind": "state",
        "pack": "in-uk-routing", "region": "uttarakhand-state",
        "authority": "uk-statewide-unverified", "relation": 9987086,
        "place": "Dehradun", "lat": 30.3165, "lng": 78.0322,
    },
    {
        "name": "Andaman and Nicobar Islands", "code": "AN", "kind": "ut",
        "pack": "in-an-routing", "region": "andaman-and-nicobar-islands-ut",
        "authority": "an-statewide-unverified", "relation": 2025855,
        "place": "Port Blair", "lat": 11.6234, "lng": 92.7265,
    },
    {
        "name": "Chandigarh", "code": "CH", "kind": "ut",
        "pack": "in-ch-routing", "region": "chandigarh-ut",
        "authority": "ch-statewide-unverified", "relation": 1942809,
        "place": "Chandigarh", "lat": 30.7333, "lng": 76.7794,
    },
    {
        "name": "Dadra and Nagar Haveli and Daman and Diu", "code": "DH", "kind": "ut",
        "pack": "in-dh-routing", "region": "dadra-nagar-haveli-daman-diu-ut",
        "authority": "dh-statewide-unverified", "relation": 1952530,
        "place": "Silvassa", "lat": 20.2766, "lng": 73.0083,
    },
    {
        "name": "Jammu and Kashmir", "code": "JK", "kind": "ut",
        "pack": "in-jk-routing", "region": "jammu-and-kashmir-ut",
        "authority": "jk-statewide-unverified", "relation": 1943188,
        "place": "Jammu", "lat": 32.7266, "lng": 74.8570,
    },
    {
        "name": "Ladakh", "code": "LA", "kind": "ut",
        "pack": "in-la-routing", "region": "ladakh-ut",
        "authority": "la-statewide-unverified", "relation": 5515045,
        "place": "Leh", "lat": 34.1526, "lng": 77.5771,
    },
    {
        "name": "Lakshadweep", "code": "LD", "kind": "ut",
        "pack": "in-ld-routing", "region": "lakshadweep-ut",
        "authority": "ld-statewide-unverified", "relation": 2027460,
        "place": "Kavaratti", "lat": 10.5667, "lng": 72.6417,
    },
    {
        "name": "Puducherry", "code": "PY", "kind": "ut",
        "pack": "in-py-routing", "region": "puducherry-ut",
        "authority": "py-statewide-unverified", "relation": 107001,
        "place": "Puducherry", "lat": 11.9416, "lng": 79.8083,
    },
]


SCENARIO = r"""
async ({rows, packIds}) => {
  const P = StandaloneAPI.__pure;
  const checks = [];
  const eq = (name, got, want) => checks.push([
    name, JSON.stringify(got) === JSON.stringify(want), got, want,
  ]);
  const ok = (name, value, detail) => checks.push([
    name, !!value, detail === undefined ? value : detail, true,
  ]);
  const configs = P.REMAINING_STATE_ROUTE_CONFIGS;
  eq("config: all and only the 20 expansion packs are registered",
     Object.keys(configs || {}).sort(), [...packIds].sort());

  const loaded = new Map();
  for (const row of rows) {
    const pack = await P.loadStatePack(row.pack);
    const region = pack && pack.payload && pack.payload.region;
    const config = configs && configs[row.pack];
    loaded.set(row.pack, {pack, region, config});

    ok(`${row.code}: runtime config exists`, config, row);
    eq(`${row.code}: config identity`, config && [
      config.state_code, config.region_id, config.relation_id, config.routing_source,
      config.authority_id,
    ], [
      row.code, row.region, row.relation,
      `osm_${row.code.toLowerCase()}_${row.kind}_boundary`, row.authority,
    ]);
    eq(`${row.code}: pinned payload identity`, region && [
      region.id, region.authority_id, region.osm_relation_id,
    ], [row.region, row.authority, row.relation]);
    ok(`${row.code}: exact polygon is present`,
       region && ["Polygon", "MultiPolygon"].includes(region.geometry && region.geometry.type),
       region);

    const direct = await P.remainingStateRouteFromGeocode(
      {state: "intentionally stale label", country_code: "in"},
      row.lat, row.lng, 12);
    eq(`${row.code}/${row.place}: coordinates select the exact jurisdiction`, direct && [
      direct.routed, direct.authority_id, direct.routing_pack_id, direct.region,
      direct.routing_source, direct.routing_match_field, direct.routing_match_value,
    ], [
      true, row.authority, row.pack, row.region,
      `osm_${row.code.toLowerCase()}_${row.kind}_boundary`, "boundary",
      `${row.name} (OpenStreetMap relation ${row.relation})`,
    ]);
    eq(`${row.code}/${row.place}: route is a neutral official handoff`, direct && [
      direct.delivery_channel, direct.officer_email, direct.ownership_unverified,
      direct.requires_official_reference, direct.tender_eligible,
    ], ["official_handoff", null, true, true, false]);

    const coarse = await P.remainingStateRouteFromGeocode(
      {state: row.name, country_code: "in"}, row.lat, row.lng, 31);
    eq(`${row.code}: a 31 m GPS fix fails closed`,
       coarse && [coarse.routed, coarse.unrouted_reason],
       [false, "location_uncertain"]);

    const saved = {
      ...direct, lat: row.lat, lng: row.lng, gps_accuracy: 12,
      issue_type: "road_damage",
    };
    const binding = await P.savedOfficialRouteBinding(
      saved, row.pack, row.authority, pack);
    eq(`${row.code}: a current saved report revalidates`, binding && [
      binding.region, binding.routing_source,
    ], [row.region, `osm_${row.code.toLowerCase()}_${row.kind}_boundary`]);
    eq(`${row.code}: a forged saved digest is rejected`,
       await P.savedOfficialRouteBinding(
         {...saved, routing_pack_sha256: "0".repeat(64)},
         row.pack, row.authority, pack), null);
    eq(`${row.code}: a saved report with 31 m accuracy is rejected`,
       await P.savedOfficialRouteBinding(
         {...saved, gps_accuracy: 31}, row.pack, row.authority, pack), null);
  }

  // Each exact boundary has a deliberately wider download envelope. Find a point in
  // that envelope but outside its polygon, then prove that even a matching state label
  // cannot turn the coarse rectangle into routing evidence.
  const sampleFractions = [0.01, 0.99, 0.10, 0.90, 0.25, 0.75, 0.50];
  for (const row of rows) {
    const {region, config} = loaded.get(row.pack);
    const envelope = config && config.envelope;
    let refusal = null;
    for (const latFraction of sampleFractions) {
      if (refusal) break;
      for (const lngFraction of sampleFractions) {
        const lat = envelope.min_lat
          + (envelope.max_lat - envelope.min_lat) * latFraction;
        const lng = envelope.min_lng
          + (envelope.max_lng - envelope.min_lng) * lngFraction;
        if (P.pointInGeometry(lng, lat, region.geometry)
            || P.geometryBoundaryDistanceMeters(lng, lat, region.geometry) <= 100) continue;
        const route = await P.remainingStateRouteFromGeocode(
          {state: row.name, country_code: "in"}, lat, lng, 12);
        if (route === null) refusal = {lat, lng};
      }
    }
    ok(`${row.code}: fixture finds an envelope-only point`, refusal, envelope);
    eq(`${row.code}: coarse state envelope and label never route`,
       refusal && await P.remainingStateRouteFromGeocode(
         {state: row.name, country_code: "in"}, refusal.lat, refusal.lng, 12), null);

    const direct = await P.remainingStateRouteFromGeocode(
      null, row.lat, row.lng, 12);
    const saved = {
      ...direct, lat: refusal && refusal.lat, lng: refusal && refusal.lng,
      gps_accuracy: 12, issue_type: "road_damage",
    };
    eq(`${row.code}: saved provenance cannot bless envelope-only coordinates`,
       refusal && await P.savedOfficialRouteBinding(
         saved, row.pack, row.authority, loaded.get(row.pack).pack), null);
  }

  // New reports keep the most specific pre-existing city route. Statewide additions
  // only fill the rest of Gujarat, Haryana, Jharkhand and Jammu & Kashmir.
  for (const fixture of [
    ["Ahmedabad", {city:"Ahmedabad",state:"Gujarat",country_code:"in"},
      23.0225,72.5714,"gj-amc","in-gj-routing","ahmedabad-amc"],
    ["Surat", {city:"Surat",state:"Gujarat",country_code:"in"},
      21.2094892,72.8317058,"in-gj-enagar","in-top50-routing","surat"],
    ["Faridabad", {city:"Faridabad",state:"Haryana",country_code:"in"},
      28.4031478,77.3105561,"in-hr-nagar-darshan","in-top50-routing","faridabad"],
    ["Ranchi", {city:"Ranchi",state:"Jharkhand",country_code:"in"},
      23.3700501,85.3250387,"in-jh-municipal-grievance","in-top50-routing","ranchi"],
    ["Srinagar", {city:"Srinagar",state:"Jammu and Kashmir",country_code:"in"},
      34.0747444,74.8204443,"in-jk-samadhan","in-top50-routing","srinagar"],
  ]) {
    const [place, geo, lat, lng, authority, packId, region] = fixture;
    const route = await P.routeOfficer(geo, lat, lng, 12, null, null, "garbage");
    eq(`${place}: the existing more-specific route keeps precedence`, route && [
      route.authority_id, route.routing_pack_id, route.region,
    ], [authority, packId, region]);
  }

  // Every other representative point must terminate on its new exact route before the
  // old Karnataka live-GIS fallback. Garbage bypasses nationwide road-class matching,
  // making this a deterministic civic-jurisdiction test.
  let kgisCalls = 0;
  const originalFetch = window.fetch;
  window.fetch = (url, ...args) => {
    if (String(url).includes("kgis.ksrsac.in")) {
      kgisCalls++;
      return Promise.resolve(new Response('{"features":[]}', {
        status: 200, headers: {"Content-Type": "application/json"},
      }));
    }
    return originalFetch(url, ...args);
  };
  for (const row of rows) {
    const route = await P.routeOfficer(
      {city: row.place, state: row.name, country_code: "in"},
      row.lat, row.lng, 12, null, null, "garbage");
    eq(`${row.code}/${row.place}: routeOfficer uses the exact expansion pack`, route && [
      route.authority_id, route.routing_pack_id, route.region,
    ], [row.authority, row.pack, row.region]);
  }
  window.fetch = originalFetch;
  eq("routing: the 20 exact routes never fall through to Karnataka GIS", kgisCalls, 0);

  return checks;
}
"""


def main() -> None:
    failures: list[str] = []
    for row in JURISDICTIONS:
        try:
            pack, _raw = read_pack(row["pack"])
        except (AssertionError, FileNotFoundError, ValueError) as exc:
            failures.append(f"{row['code']} pack cannot be read: {exc}")
            continue
        resource = resource_for(row["pack"])
        region = pack.get("payload", {}).get("region", {})
        if (
            pack.get("pack_id") != row["pack"]
            or pack.get("state_code") != row["code"]
            or resource.get("adapter") != "statewide-general-v1"
            or resource.get("statewide") is not True
        ):
            failures.append(f"{row['code']} manifest/envelope identity is invalid")
        if (
            region.get("id") != row["region"]
            or region.get("authority_id") != row["authority"]
            or region.get("osm_relation_id") != row["relation"]
        ):
            failures.append(f"{row['code']} pinned region identity is invalid")

    if failures:
        print("REMAINING INDIA ROUTING TEST FAIL")
        for failure in failures:
            print("  -", failure)
        raise SystemExit(1)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--disable-web-security"])
        context = browser.new_context(viewport={"width": 390, "height": 844})
        page = context.new_page()
        page.goto(APP)
        page.wait_for_load_state("networkidle")
        page.wait_for_function(
            "() => window.StandaloneAPI && StandaloneAPI.__pure "
            "&& typeof StandaloneAPI.__pure.remainingStateRouteFromGeocode === 'function' "
            "&& StandaloneAPI.__pure.REMAINING_STATE_ROUTE_CONFIGS "
            "&& Object.keys(StandaloneAPI.__pure.REMAINING_STATE_ROUTE_CONFIGS).length === 20",
            timeout=30_000,
        )
        results = page.evaluate(
            SCENARIO,
            {"rows": JURISDICTIONS, "packIds": PACK_IDS},
        )
        context.close()
        browser.close()

    for name, passed, got, want in results:
        if not passed:
            failures.append(name)
            print(f"  FAIL {name}\n         got  {got!r}\n         want {want!r}")

    if failures:
        print("REMAINING INDIA ROUTING TEST FAIL")
        for failure in failures:
            print("  -", failure)
        raise SystemExit(1)
    print(f"REMAINING INDIA ROUTING TEST PASS ({len(results)} checks)")


if __name__ == "__main__":
    main()
