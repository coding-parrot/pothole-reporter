# -*- coding: utf-8 -*-
"""Generic municipal-city packs route conservatively and never fall through to KGIS."""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import sync_playwright

from state_pack_utils import load_manifest, read_pack, route_pattern


APP = "http://localhost:8765/"
PACKS = {
    "in-tn-routing": {
        "state_code": "TN",
        "region_id": "chennai-gcc",
        "authority_id": "tn-gcc",
        "routing_mode": "boundary",
        "routing_source": "osm_gcc_boundary",
        "envelope": {"min_lng": 80.05, "min_lat": 12.75, "max_lng": 80.40, "max_lat": 13.30},
        "has_geometry": True,
    },
    "in-tg-routing": {
        "state_code": "TG",
        "region_id": "hyderabad-cure-2053",
        "authority_id": "tg-cure-shared",
        "routing_mode": "official_point_query",
        "routing_source": "tgrac_cure_2053_point_query",
        "envelope": {"min_lng": 78.15, "min_lat": 17.10, "max_lng": 78.82, "max_lat": 17.72},
        "has_geometry": False,
    },
    "in-gj-routing": {
        "state_code": "GJ",
        "region_id": "ahmedabad-amc",
        "authority_id": "gj-amc",
        "routing_mode": "boundary",
        "routing_source": "opencity_amc_wards_union",
        "envelope": {
            "min_lng": 72.40, "min_lat": 22.85,
            "max_lng": 72.75, "max_lat": 23.20,
        },
        "has_geometry": True,
    },
}

HOOKS_READY = """
() => {
  const P = window.StandaloneAPI && StandaloneAPI.__pure;
  return P && ["municipalCityCoverage", "municipalCityRouteFromGeocode", "routeOfficer",
    "gpsAccuracyEnvelope", "officialPointRegionMatch", "savedMunicipalLocationMatches"]
    .every((name) => typeof P[name] === "function");
}
"""

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
  const refused = (name, value) => ok(name, !value || value.routed === false, value);
  const regionOf = (coverage, id) => coverage && Array.isArray(coverage.regions)
    ? coverage.regions.find((region) => region && region.id === id) : null;
  const edgeOf = (geometry) => geometry && geometry.type === "Polygon"
    ? geometry.coordinates[0][0]
    : geometry && geometry.type === "MultiPolygon" ? geometry.coordinates[0][0][0] : null;

  const manifest = await P.getStatePackManifest();
  const coverage = {
    tn: await P.municipalCityCoverage("in-tn-routing"),
    tg: await P.municipalCityCoverage("in-tg-routing"),
    gj: await P.municipalCityCoverage("in-gj-routing"),
  };
  const regions = {
    tn: regionOf(coverage.tn, "chennai-gcc"),
    tg: regionOf(coverage.tg, "hyderabad-cure-2053"),
    gj: regionOf(coverage.gj, "ahmedabad-amc"),
  };

  for (const [name, packId, regionId, authorityId, source, geometry] of [
    ["Chennai", "in-tn-routing", "chennai-gcc", "tn-gcc", "osm_gcc_boundary", true],
    ["Hyderabad", "in-tg-routing", "hyderabad-cure-2053", "tg-cure-shared",
      "tgrac_cure_2053_point_query", false],
    ["Ahmedabad", "in-gj-routing", "ahmedabad-amc", "gj-amc",
      "opencity_amc_wards_union", true],
  ]) {
    const key = packId === "in-tn-routing" ? "tn" : packId === "in-tg-routing" ? "tg" : "gj";
    const region = regions[key];
    ok(`coverage: ${name} pack loads`, coverage[key], coverage[key]);
    ok(`coverage: ${name} region exists`, region, coverage[key]);
    eq(`coverage: ${name} authority is pinned`, region && region.authority_id, authorityId);
    eq(`coverage: ${name} routing source is pinned`, region && region.routing_source, source);
    ok(`coverage: ${name} has state aliases`,
       region && Array.isArray(region.state_aliases) && region.state_aliases.length, region);
    ok(`coverage: ${name} has place aliases`,
       region && Array.isArray(region.place_aliases) && region.place_aliases.length, region);
    ok(`coverage: ${name} has an exclusion inventory`,
       region && Array.isArray(region.exclusions), region);
    eq(`coverage: ${name} geometry policy`, !!(region && region.geometry), geometry);
  }

  const assertRoute = (
    name, route, packId, region, authorityId, source, matchField, matchValue
  ) => {
    const resource = manifest && manifest.resources && manifest.resources[packId];
    ok(`${name}: route exists`, route, route);
    eq(`${name}: routes`, route && route.routed, true);
    eq(`${name}: authority`, route && route.authority_id, authorityId);
    eq(`${name}: region`, route && route.region, region && region.id);
    eq(`${name}: source`, route && route.routing_source, source);
    eq(`${name}: match field`, route && route.routing_match_field, matchField);
    eq(`${name}: match value is explicit`, route && route.routing_match_value,
       matchValue === undefined ? region && region.match_value : matchValue);
    eq(`${name}: uses an official handoff`, route && route.delivery_channel, "official_handoff");
    eq(`${name}: has no guessed email`, route && route.officer_email, null);
    eq(`${name}: does not infer road ownership`, route && route.ownership_unverified, true);
    eq(`${name}: requires the official reference`,
       route && route.requires_official_reference, true);
    eq(`${name}: never infers a tender`, route && route.tender_eligible, false);
    eq(`${name}: records pack ID`, route && route.routing_pack_id, packId);
    eq(`${name}: records pack version`, route && route.routing_pack_version,
       resource && resource.pack_version);
    eq(`${name}: records pack digest`, route && route.routing_pack_sha256,
       resource && resource.sha256);
    eq(`${name}: records pack state`, route && route.routing_pack_state_code,
       resource && resource.state_code);
  };

  const tnGeo = {city: "Chennai", state: "Tamil Nadu", country_code: "in",
                 full: "Chennai, Tamil Nadu, India"};
  const tnInside = await P.municipalCityRouteFromGeocode(
    "in-tn-routing", tnGeo, 13.0827, 80.2707, 12);
  assertRoute("Chennai inside", tnInside, "in-tn-routing", regions.tn, "tn-gcc",
              "osm_gcc_boundary", "boundary");
  eq("Chennai: GCC grievance portal",
     tnInside && tnInside.handoff_url,
     "https://erp.chennaicorporation.gov.in/pgr/citizen/BeforeReg.do");
  eq("Chennai: official app package", tnInside && tnInside.handoff_package,
     "com.ceedeev.grivenancev2");
  eq("Chennai: official WhatsApp", tnInside && tnInside.whatsapp_url,
     "https://wa.me/919445061913");
  eq("Chennai: official helpline", tnInside && tnInside.helpline, "1913");

  ok("Chennai fixture: Tambaram is outside the GCC polygon",
     regions.tn && !P.pointInGeometry(80.1000, 12.9249, regions.tn.geometry), regions.tn);
  const tnOutside = await P.municipalCityRouteFromGeocode(
    "in-tn-routing", tnGeo, 12.9249, 80.1000, 12);
  eq("Chennai outside: Tambaram is refused", tnOutside && tnOutside.routed, false);
  eq("Chennai outside: reason is explicit",
     tnOutside && tnOutside.unrouted_reason, "outside_area");
  const tnLimit = await P.municipalCityRouteFromGeocode(
    "in-tn-routing", tnGeo, 13.0827, 80.2707, 30);
  eq("Chennai accuracy: 30 m is accepted away from the edge",
     tnLimit && tnLimit.authority_id, "tn-gcc");
  for (const [name, accuracy] of [["31 m", 31], ["negative", -1], ["not a number", Number.NaN]]) {
    const route = await P.municipalCityRouteFromGeocode(
      "in-tn-routing", tnGeo, 13.0827, 80.2707, accuracy);
    eq(`Chennai accuracy: ${name} fails closed`,
       route && route.unrouted_reason, "location_uncertain");
  }
  const tnEdgePoint = edgeOf(regions.tn && regions.tn.geometry);
  ok("Chennai edge fixture exists", tnEdgePoint, regions.tn);
  const tnEdge = tnEdgePoint && await P.municipalCityRouteFromGeocode(
    "in-tn-routing", tnGeo, tnEdgePoint[1], tnEdgePoint[0], 5);
  eq("Chennai edge: an accuracy circle touching the boundary fails closed",
     tnEdge && tnEdge.unrouted_reason, "location_uncertain");

  const tgGeo = {city: "Hyderabad", state: "Telangana", country_code: "in",
                 full: "Hyderabad, Telangana, India"};
  const tgInside = await P.municipalCityRouteFromGeocode(
    "in-tg-routing", tgGeo, 17.3616, 78.4747, 12);
  assertRoute("Hyderabad CURE inside", tgInside, "in-tg-routing", regions.tg,
              "tg-cure-shared",
              "tgrac_cure_2053_point_query", "official_accuracy_envelope");
  eq("Hyderabad: shared My Cure portal", tgInside && tgInside.handoff_url,
     "https://igs.ghmc.gov.in/operator/send_otp_mobile");
  eq("Hyderabad: shared My Cure package", tgInside && tgInside.handoff_package,
     "cgg.gov.ghmc");
  eq("Hyderabad: no false corporation attribution",
     tgInside && tgInside.authority_id, "tg-cure-shared");
  eq("Hyderabad: official area metadata", regions.tg && regions.tg.official_area_km2, 2053);
  eq("Hyderabad: CURE requires the entire accuracy envelope within the official layer",
     regions.tg && regions.tg.query_spatial_rel, "esriSpatialRelWithin");
  eq("Hyderabad: no unlicensed CURE polygon is bundled",
     regions.tg && regions.tg.geometry, undefined);

  const tgOuter = await P.municipalCityRouteFromGeocode(
    "in-tg-routing", tgGeo, 17.2599, 78.3982, 12);
  eq("Hyderabad outer: newly merged Shamshabad area is covered by official CURE",
     tgOuter && tgOuter.authority_id, "tg-cure-shared");
  const tgOutside = await P.municipalCityRouteFromGeocode(
    "in-tg-routing", tgGeo, 17.1500, 78.1800, 12);
  eq("Hyderabad outside: official service refusal is honoured",
     tgOutside && tgOutside.unrouted_reason, "outside_area");
  const cantonment = await P.municipalCityRouteFromGeocode(
    "in-tg-routing", tgGeo, 17.4815673, 78.4980533, 12);
  eq("Hyderabad exclusion: exact Secunderabad Cantonment query is refused",
     cantonment && cantonment.routed, false);
  eq("Hyderabad exclusion: Cantonment refusal is explicit",
     cantonment && cantonment.unrouted_reason, "outside_area");
  eq("Hyderabad exclusion: official layer is pinned",
     regions.tg && regions.tg.exclusions[0] && regions.tg.exclusions[0].id,
     "secunderabad-cantonment");
  eq("Hyderabad exclusion: accuracy envelope must not intersect Cantonment",
     regions.tg && regions.tg.exclusions[0] && regions.tg.exclusions[0].query_spatial_rel,
     "esriSpatialRelIntersects");
  const tgLimit = await P.municipalCityRouteFromGeocode(
    "in-tg-routing", tgGeo, 17.3616, 78.4747, 30);
  eq("Hyderabad accuracy: 30 m is accepted away from the edge",
     tgLimit && tgLimit.authority_id, "tg-cure-shared");
  for (const [name, accuracy] of [["31 m", 31], ["negative", -1], ["not a number", Number.NaN]]) {
    const route = await P.municipalCityRouteFromGeocode(
      "in-tg-routing", tgGeo, 17.3616, 78.4747, accuracy);
    eq(`Hyderabad accuracy: ${name} fails closed`,
       route && route.unrouted_reason, "location_uncertain");
  }
  const accuracyEnvelope = P.gpsAccuracyEnvelope(17.3616, 78.4747, 12);
  ok("Hyderabad accuracy: a non-zero GPS envelope is queried",
     accuracyEnvelope.xmin < 78.4747 && accuracyEnvelope.xmax > 78.4747
       && accuracyEnvelope.ymin < 17.3616 && accuracyEnvelope.ymax > 17.3616,
     accuracyEnvelope);
  const tgPack = await P.loadStatePack("in-tg-routing");
  const savedInside = await P.savedMunicipalLocationMatches(
    {...tgInside, lat: 17.3616, lng: 78.4747, gps_accuracy: 12},
    P.MUNICIPAL_CITY_CONFIGS["in-tg-routing"], tgPack);
  eq("Hyderabad saved handoff: live official revalidation succeeds", savedInside, true);
  const savedWrongEvidence = await P.savedMunicipalLocationMatches(
    {...tgInside, lat: 17.3616, lng: 78.4747, gps_accuracy: 12,
      routing_match_field: "boundary"},
    P.MUNICIPAL_CITY_CONFIGS["in-tg-routing"], tgPack);
  eq("Hyderabad saved handoff: stale boundary evidence is refused", savedWrongEvidence, false);

  const gjCity = {city: "Ahmedabad", state: "Gujarat", country_code: "in",
                  full: "Ahmedabad, Gujarat, India"};
  const gjInside = await P.municipalCityRouteFromGeocode(
    "in-gj-routing", gjCity, 23.0225, 72.5714, 12);
  assertRoute("Ahmedabad AMC inside", gjInside, "in-gj-routing", regions.gj,
              "gj-amc", "opencity_amc_wards_union", "boundary");
  eq("Ahmedabad: AMC CCRS portal", gjInside && gjInside.handoff_url,
     "https://www.amccrs.com/AMCPortal/View/ComplaintRegistration.aspx?m=Online");
  eq("Ahmedabad: official app package", gjInside && gjInside.handoff_package,
     "com.amplvb.ccrs");
  eq("Ahmedabad: official WhatsApp", gjInside && gjInside.whatsapp_url,
     "https://wa.me/917567855303");
  eq("Ahmedabad: official helpline", gjInside && gjInside.helpline, "155303");

  const gjNull = await P.municipalCityRouteFromGeocode(
    "in-gj-routing", null, 23.0225, 72.5714, 12);
  eq("Ahmedabad boundary: null geocode still routes by coordinates",
     gjNull && gjNull.authority_id, "gj-amc");
  const gjWrong = await P.municipalCityRouteFromGeocode(
    "in-gj-routing", {city: "Gandhinagar", state: "Gujarat", country_code: "in"},
    23.0225, 72.5714, 12);
  eq("Ahmedabad boundary: a wrong place label cannot override coordinates",
     gjWrong && gjWrong.authority_id, "gj-amc");
  ok("Ahmedabad fixture: AUDA-side point is outside the AMC polygon",
     regions.gj && !P.pointInGeometry(72.5700, 23.1700, regions.gj.geometry), regions.gj);
  const gjOutside = await P.municipalCityRouteFromGeocode(
    "in-gj-routing", gjCity, 23.1700, 72.5700, 12);
  refused("Ahmedabad boundary: wider AUDA point is refused", gjOutside);
  const gjLimit = await P.municipalCityRouteFromGeocode(
    "in-gj-routing", gjCity, 23.0225, 72.5714, 30);
  eq("Ahmedabad accuracy: 30 m is accepted", gjLimit && gjLimit.authority_id, "gj-amc");
  for (const [name, accuracy] of [["31 m", 31], ["negative", -1], ["not a number", Number.NaN]]) {
    const route = await P.municipalCityRouteFromGeocode(
      "in-gj-routing", gjCity, 23.0225, 72.5714, accuracy);
    eq(`Ahmedabad accuracy: ${name} fails closed`,
       route && route.unrouted_reason, "location_uncertain");
  }
  const gjEdgePoint = edgeOf(regions.gj && regions.gj.geometry);
  ok("Ahmedabad edge fixture exists", gjEdgePoint, regions.gj);
  const gjEdge = gjEdgePoint && await P.municipalCityRouteFromGeocode(
    "in-gj-routing", gjCity, gjEdgePoint[1], gjEdgePoint[0], 5);
  eq("Ahmedabad edge: an accuracy circle touching the boundary fails closed",
     gjEdge && gjEdge.unrouted_reason, "location_uncertain");

  // The generic state routes must terminate before Karnataka's live GIS. Stub it so a
  // regression is counted immediately and cannot turn this deterministic test into a
  // live-service test.
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
  const routedByOfficer = {
    tn: await P.routeOfficer(tnGeo, 13.0827, 80.2707, 12, null, null, "garbage"),
    tg: await P.routeOfficer(tgGeo, 17.3616, 78.4747, 12, null, null, "garbage"),
    gj: await P.routeOfficer(gjCity, 23.0225, 72.5714, 12, null, null, "garbage"),
    gjOutside: await P.routeOfficer(gjCity, 23.1700, 72.5700, 12, null, null, "garbage"),
  };
  window.fetch = originalFetch;
  eq("routeOfficer: Chennai uses the generic pack", routedByOfficer.tn.authority_id, "tn-gcc");
  eq("routeOfficer: Hyderabad uses shared CURE intake",
     routedByOfficer.tg.authority_id, "tg-cure-shared");
  eq("routeOfficer: Ahmedabad uses the generic pack", routedByOfficer.gj.authority_id, "gj-amc");
  eq("routeOfficer: wider AUDA outside the Ahmedabad boundary stays outside",
     routedByOfficer.gjOutside.unrouted_reason, "outside_area");
  eq("state isolation: municipal-city routing never calls Karnataka GIS", kgisCalls, 0);

  return checks;
}
"""

ROUTE_FIXTURES = {
    "in-tn-routing": (
        "{city:'Chennai',state:'Tamil Nadu',country_code:'in'}", 13.0827, 80.2707
    ),
    "in-tg-routing": (
        "{city:'Hyderabad',state:'Telangana',country_code:'in'}", 17.3616, 78.4747
    ),
    "in-gj-routing": (
        "{city:'Ahmedabad',state:'Gujarat',country_code:'in'}", 23.0225, 72.5714
    ),
}


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
        call = {
            "url": route.request.url,
            "path": urlsplit(route.request.url).path,
            "spatial_rel": query.get("spatialRel", [None])[0],
            "geometry_type": query.get("geometryType", [None])[0],
            "in_sr": query.get("inSR", [None])[0],
            "return_count_only": query.get("returnCountOnly", [None])[0],
            "geometry": geometry,
        }
        calls.append(call)
        if unavailable:
            route.fulfill(status=503, content_type="application/json", body='{"error":true}')
            return
        if not isinstance(geometry, dict):
            route.fulfill(status=200, content_type="application/json", body='{"error":true}')
            return
        lng = (geometry.get("xmin", 0) + geometry.get("xmax", 0)) / 2
        lat = (geometry.get("ymin", 0) + geometry.get("ymax", 0)) / 2
        if call["path"].endswith("/Administrative_Layer/MapServer/1/query"):
            intersects = not (
                geometry.get("xmax", 0) < cantonment["min_lng"]
                or geometry.get("xmin", 0) > cantonment["max_lng"]
                or geometry.get("ymax", 0) < cantonment["min_lat"]
                or geometry.get("ymin", 0) > cantonment["max_lat"]
            )
            count = 1 if intersects else 0
        else:
            # Deterministic service fixture: center and expanded Shamshabad are in CURE;
            # the south-west relevance-envelope point is outside the official layer.
            count = 0 if lat < 17.20 and lng < 78.25 else 1
        route.fulfill(
            status=200,
            content_type="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
            body=json.dumps({"count": count}, separators=(",", ":")),
        )

    return respond


def open_page(
    browser,
    pack_id: str | None = None,
    responder=None,
    *,
    native: bool = False,
    official_responder=None,
):
    context = browser.new_context(viewport={"width": 390, "height": 844})
    if native:
        context.add_init_script(
            "window.Capacitor={isNativePlatform:()=>true,Plugins:{}};"
        )
        local_packs = {
            resource["url"]: read_pack(pack_id)[1]
            for pack_id, resource in load_manifest()["resources"].items()
        }

        def serve_local_pack(route):
            body = local_packs.get(route.request.url)
            if body is None:
                route.fulfill(status=404, content_type="text/plain", body="not found")
            else:
                route.fulfill(status=200, content_type="application/json", body=body)

        context.route(
            "https://coding-parrot.github.io/pothole-reporter/packs/v1/**",
            serve_local_pack,
        )
    if official_responder is not None:
        context.route("https://tgrac.telangana.gov.in/**", official_responder)
    page = context.new_page()
    if pack_id is not None and responder is not None:
        page.route(route_pattern(pack_id), responder)
    page.goto(APP)
    page.wait_for_load_state("networkidle")
    page.wait_for_function(HOOKS_READY, timeout=30000)
    return context, page


def route_reason(page, pack_id: str) -> str | None:
    geo, lat, lng = ROUTE_FIXTURES[pack_id]
    return page.evaluate(
        f"""async () => {{
          const result = await StandaloneAPI.__pure.municipalCityRouteFromGeocode(
            {json.dumps(pack_id)}, {geo}, {lat}, {lng}, 12);
          return result && result.unrouted_reason;
        }}"""
    )


def semantic_tamper_reason(browser, pack_id: str, envelope: dict) -> str | None:
    body = json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(body).hexdigest()
    manifest = copy.deepcopy(load_manifest())
    resource = manifest["resources"][pack_id]
    old_digest = resource["sha256"]
    old_path = resource["path"]
    base_url = resource["url"][: -len(old_path)]
    resource["bytes"] = len(body)
    resource["sha256"] = digest
    resource["path"] = old_path.replace(old_digest, digest)
    resource["url"] = base_url + resource["path"]
    manifest_body = json.dumps(manifest, ensure_ascii=False, separators=(",", ":"))

    context = browser.new_context(viewport={"width": 390, "height": 844})
    context.route(
        "**/pack-manifest-v1.33.json",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body=manifest_body
        ),
    )
    context.route(
        f"**/{resource['path']}",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body=body
        ),
    )
    page = context.new_page()
    page.goto(APP)
    page.wait_for_load_state("networkidle")
    page.wait_for_function(HOOKS_READY, timeout=30000)
    reason = route_reason(page, pack_id)
    context.close()
    return reason


def semantic_tamper_variants(envelope: dict) -> list[tuple[str, dict]]:
    variants: list[tuple[str, dict]] = []

    changed = copy.deepcopy(envelope)
    changed["payload"]["regions"][0]["id"] = "chennai-other"
    variants.append(("region ID", changed))

    changed = copy.deepcopy(envelope)
    changed["authorities"][0]["id"] = "tn-other"
    changed["payload"]["regions"][0]["authority_id"] = "tn-other"
    variants.append(("authority", changed))

    changed = copy.deepcopy(envelope)
    region = changed["payload"]["regions"][0]
    region["routing_mode"] = "structured_geocode"
    for field in ("coordinate_precision", "area_km2", "bbox", "geometry_sha256", "geometry"):
        region.pop(field)
    variants.append(("routing mode", changed))

    changed = copy.deepcopy(envelope)
    changed["payload"]["regions"][0]["routing_source"] = "osm_other_boundary"
    variants.append(("routing source", changed))

    changed = copy.deepcopy(envelope)
    changed["payload"]["regions"][0]["envelope"]["max_lng"] += 0.01
    variants.append(("relevance envelope", changed))

    changed = copy.deepcopy(envelope)
    changed["authorities"][0]["handoff_url"] = "https://example.invalid/malicious-same-id"
    variants.append(("same-ID authority handoff", changed))

    return variants


def self_consistent_official_query_tamper(envelope: dict) -> dict:
    changed = copy.deepcopy(envelope)
    region = changed["payload"]["regions"][0]
    region["query_url"] = region["query_url"].replace("/22/query", "/23/query")
    region["source_url"] = region["source_url"].replace("/22", "/23")
    region["source_object_id"] = region["source_object_id"].replace(":22", ":23")
    return changed


def reordered_semantic_pack(value):
    if isinstance(value, list):
        return [reordered_semantic_pack(item) for item in value]
    if not isinstance(value, dict):
        return value
    # The pre-existing geometry_sha256 contract hashes the two-key GeoJSON object
    # verbatim; this regression targets the new semantic authority/region pins.
    keys = list(value)
    if set(keys) == {"type", "coordinates"}:
        return {key: reordered_semantic_pack(value[key]) for key in keys}
    return {
        key: reordered_semantic_pack(value[key])
        for key in reversed(keys)
    }


def main() -> None:
    failures: list[str] = []
    envelopes: dict[str, dict] = {}
    for pack_id, expected in PACKS.items():
        try:
            envelope, _ = read_pack(pack_id)
            envelopes[pack_id] = envelope
            if envelope.get("adapter") != "municipal-city-v1":
                failures.append(f"{pack_id} does not use municipal-city-v1")
            if envelope.get("state_code") != expected["state_code"]:
                failures.append(f"{pack_id} has the wrong state code")
            regions = envelope.get("payload", {}).get("regions")
            if not isinstance(regions, list) or not regions:
                failures.append(f"{pack_id} has no municipal-city regions")
                continue
            region = next(
                (item for item in regions if item.get("id") == expected["region_id"]), None
            )
            if not isinstance(region, dict):
                failures.append(f"{pack_id} has no {expected['region_id']} region")
                continue
            if region.get("authority_id") != expected["authority_id"]:
                failures.append(f"{pack_id} region has the wrong authority")
            if region.get("routing_mode") != expected["routing_mode"]:
                failures.append(f"{pack_id} region has the wrong routing mode")
            if region.get("routing_source") != expected["routing_source"]:
                failures.append(f"{pack_id} region has the wrong routing source")
            if region.get("envelope") != expected["envelope"]:
                failures.append(f"{pack_id} region has the wrong relevance envelope")
            if bool(region.get("geometry")) != expected["has_geometry"]:
                failures.append(f"{pack_id} region has the wrong geometry policy")
        except (AssertionError, KeyError, OSError, TypeError, ValueError) as error:
            failures.append(f"{pack_id} pin is invalid: {error}")

    if failures:
        print("FAIL")
        for failure in failures:
            print("  -", failure)
        sys.exit(1)

    result_count = 0
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=["--disable-web-security"])
        official_calls: list[dict] = []
        context, page = open_page(
            browser,
            native=True,
            official_responder=tgrac_responder(official_calls),
        )
        results = page.evaluate(SCENARIO)
        result_count = len(results)
        context.close()
        for name, passed, got, want in results:
            if not passed:
                failures.append(name)
                print(f"  FAIL {name}\n         got  {got}\n         want {want}")

        cure_calls = [
            call for call in official_calls
            if call["path"].endswith("/TCUR_Telangana_Core_Urban_Region_V2/MapServer/22/query")
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
            failures.append("native Hyderabad coverage does not use the pinned Within envelope query")
        if not cantonment_calls or any(
            call["spatial_rel"] != "esriSpatialRelIntersects"
            or call["geometry_type"] != "esriGeometryEnvelope"
            or call["in_sr"] != "4326"
            or call["return_count_only"] != "true"
            for call in cantonment_calls
        ):
            failures.append("native Hyderabad exclusion does not use the pinned Intersects envelope query")
        if any(
            not isinstance(call["geometry"], dict)
            or call["geometry"].get("xmin") >= call["geometry"].get("xmax")
            or call["geometry"].get("ymin") >= call["geometry"].get("ymax")
            for call in official_calls
        ):
            failures.append("native Hyderabad query did not send a non-zero GPS-accuracy envelope")

        # The same hosted client must not attempt the non-CORS government service in a
        # browser. It fails closed without leaking a coordinate to TGRAC.
        web_calls: list[dict] = []
        context, page = open_page(
            browser,
            official_responder=tgrac_responder(web_calls),
        )
        web_reason = route_reason(page, "in-tg-routing")
        if web_reason != "jurisdiction_unavailable":
            failures.append(f"web Hyderabad route did not fail closed: {web_reason}")
        if web_calls:
            failures.append("web Hyderabad route sent coordinates to the native-only TGRAC service")
        context.close()

        failed_calls: list[dict] = []
        context, page = open_page(
            browser,
            native=True,
            official_responder=tgrac_responder(failed_calls, unavailable=True),
        )
        failed_reason = route_reason(page, "in-tg-routing")
        if failed_reason != "jurisdiction_unavailable":
            failures.append(f"unavailable TGRAC service did not fail closed: {failed_reason}")
        context.close()

        # A syntactically malformed response and a valid-shaped byte-tampered response
        # must both be rejected by the same full-byte SHA-256 gate used in production.
        context, page = open_page(
            browser,
            "in-tn-routing",
            lambda route: route.fulfill(
                status=200, content_type="application/json", body="{not-json"
            ),
        )
        reason = route_reason(page, "in-tn-routing")
        if reason != "jurisdiction_unavailable":
            failures.append(f"malformed municipal pack did not fail closed: {reason}")
        context.close()

        for pack_id, envelope in envelopes.items():
            tampered = copy.deepcopy(envelope)
            tampered["payload"]["regions"][0]["authority_id"] = "xx-tampered"
            body = json.dumps(tampered, ensure_ascii=False, separators=(",", ":"))

            def make_tampered_response(response_body):
                def respond(route):
                    route.fulfill(
                        status=200, content_type="application/json", body=response_body
                    )
                return respond

            context, page = open_page(browser, pack_id, make_tampered_response(body))
            reason = route_reason(page, pack_id)
            if reason != "jurisdiction_unavailable":
                failures.append(f"tampered {pack_id} response did not fail closed: {reason}")
            context.close()

        # Re-pin the catalog to each internally valid mutation. This gets past the byte
        # hash gate and proves the runtime binds safety semantics to the pack ID itself.
        for label, tampered in semantic_tamper_variants(envelopes["in-tn-routing"]):
            reason = semantic_tamper_reason(browser, "in-tn-routing", tampered)
            if reason != "jurisdiction_unavailable":
                failures.append(
                    f"municipal pack with changed {label} did not fail closed: {reason}"
                )
        changed_tg = self_consistent_official_query_tamper(envelopes["in-tg-routing"])
        reason = semantic_tamper_reason(browser, "in-tg-routing", changed_tg)
        if reason != "jurisdiction_unavailable":
            failures.append(
                "self-consistent replacement official query did not fail closed: "
                f"{reason}"
            )
        reordered = reordered_semantic_pack(envelopes["in-tn-routing"])
        reason = semantic_tamper_reason(browser, "in-tn-routing", reordered)
        if reason is not None:
            failures.append(
                f"semantically identical reordered municipal pack did not route: {reason}"
            )
        browser.close()

    if failures:
        print("FAIL")
        for failure in failures:
            print("  -", failure)
        sys.exit(1)
    print(f"MUNICIPAL CITY ROUTING TEST PASS ({result_count + 17} checks)")


if __name__ == "__main__":
    main()
