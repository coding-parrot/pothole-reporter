#!/usr/bin/env python3
"""Nationwide NH tiles route before cities and fail closed on ambiguity or bad data."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
APP = "http://localhost:8765/"
MANIFEST = json.loads((ROOT / "static" / "highway-manifest.json").read_text())


def hooks_ready(page) -> bool:
    return page.evaluate(
        """() => {
          const P = window.StandaloneAPI && StandaloneAPI.__pure;
          return P && ["getHighwayPackManifest", "loadHighwayTile", "matchHighwayTile",
            "nationalHighwayRoute", "routeOfficer", "openNationalHighwayHandoff"]
            .every((name) => typeof P[name] === "function");
        }"""
    )


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
  const manifest = await P.getHighwayPackManifest();
  ok("catalog loads", manifest, manifest);
  eq("catalog has every generated tile", Object.keys(manifest.tiles).length, 101);
  eq("catalog records distinct mapped references", manifest.source.distinct_refs, 680);
  eq("catalog source date is pinned", manifest.source.source_retrieved_at, "2026-08-20");
  eq("catalog source checksum is pinned", manifest.source.source_md5,
     "c5e0a62a1cb00c80d8c5948bf18370d7");
  eq("official handoff is Rajmargyatra", manifest.authority.handoff_package,
     "com.nhai.rajmargyatra");
  eq("official helpline is 1033", manifest.authority.helpline, "1033");

  const fixtures = [
    ["NH-27 west", 21.67393, 69.724575, "NH-27"],
    ["NH-44 north", 31.994375, 75.612435, "NH-44"],
    ["NH-16 south", 14.116915, 79.874755, "NH-16"],
  ];
  let savedHighway = null;
  for (const [name, lat, lng, ref] of fixtures) {
    const route = await P.nationalHighwayRoute(lat, lng, 5, null, null);
    if (ref === "NH-27") savedHighway = {...route, lat, lng, gps_accuracy: 5};
    eq(`${name}: routes`, route && route.routed, true);
    eq(`${name}: reference`, route && route.highway_ref, ref);
    eq(`${name}: national authority`, route && route.authority_id, "in-national-highway");
    eq(`${name}: highway wins before any city router`, route && route.region,
       "national-highway");
    eq(`${name}: no guessed owner`, route && route.ownership_unverified, true);
    eq(`${name}: no guessed email`, route && route.officer_email, null);
    eq(`${name}: official handoff`, route && route.delivery_channel, "official_handoff");
    eq(`${name}: country-level provenance`, route && route.routing_pack_state_code, "IN");
    ok(`${name}: tile checksum provenance`,
       route && /^[0-9a-f]{64}$/.test(route.routing_pack_sha256), route);
  }

  const delhiHighway = await P.routeOfficer(
    {city: "Delhi", state: "Delhi", country_code: "in"},
    28.84946, 77.125995, 5, null, null);
  eq("routeOfficer checks highway before Delhi", delhiHighway && delhiHighway.authority_id,
     "in-national-highway");
  eq("Delhi highway identifies NH-44", delhiHighway && delhiHighway.highway_ref, "NH-44");

  const syntheticManifest = {match: manifest.match};
  const scale = 100000;
  const horizontal = ["NH-1", [6999000, 1999900, 7001000, 2000100],
    [6999000, 2000000, 2000, 0]];
  const vertical = ["NH-2", [6999900, 1999000, 7000100, 2001000],
    [7000000, 1999000, 0, 2000]];
  const oneRoad = {coordinate_scale: scale, features: [horizontal]};
  const crossing = {coordinate_scale: scale, features: [horizontal, vertical]};
  const aligned = P.matchHighwayTile(oneRoad, syntheticManifest, 20, 70, 5, 90, 10);
  eq("heading-aligned drive matches", aligned && aligned.match.ref, "NH-1");
  eq("perpendicular moving vehicle is not snapped to nearby highway",
     P.matchHighwayTile(oneRoad, syntheticManifest, 20, 70, 5, 0, 10), null);
  eq("missing GPS accuracy fails closed after a geometric hit",
     P.matchHighwayTile(oneRoad, syntheticManifest, 20, 70, Number.NaN, null, null).uncertain,
     true);
  eq("two different highway references at a junction are ambiguous",
     P.matchHighwayTile(crossing, syntheticManifest, 20, 70, 5, null, null).uncertain,
     true);
  eq("a point well away from the carriageway does not match",
     P.matchHighwayTile(oneRoad, syntheticManifest, 20.002, 70, 5, null, null), null);

  ok("saved report fixture is highway-bound",
     savedHighway && savedHighway.authority_id === "in-national-highway", savedHighway);
  if (savedHighway && savedHighway.authority_id === "in-national-highway") {
    const handoff = await P.openNationalHighwayHandoff({
      ...savedHighway, heading: null, speed_mps: null,
    });
    eq("saved report revalidation keeps Rajmargyatra", handoff.handoff_package,
       "com.nhai.rajmargyatra");
    eq("saved report revalidation keeps the mapped reference", handoff.highway_ref, "NH-27");
    eq("saved report revalidation refreshes current tile provenance",
       handoff.routing_pack_id, savedHighway.routing_pack_id);
  }

  return checks;
}
"""


def run() -> None:
    failures: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(APP)
        page.wait_for_function(
            "() => window.StandaloneAPI && StandaloneAPI.__pure "
            "&& typeof StandaloneAPI.__pure.nationalHighwayRoute === 'function'"
        )
        if not hooks_ready(page):
            failures.append("highway test hooks were not exposed")
        else:
            for name, passed, got, want in page.evaluate(SCENARIO):
                if not passed:
                    failures.append(f"{name}: got {got!r}, wanted {want!r}")
        page.close()

        # A corrupt immutable tile must not be interpreted as "no highway" and then fall
        # through to a municipal recipient. Use a fresh context so no valid cache exists.
        resource = MANIFEST["tiles"]["e076n28"]
        corrupt = browser.new_page()
        corrupt.route(
            f"**/{resource['path']}",
            lambda route: route.fulfill(
                status=200, content_type="application/json", body=b'{"corrupt":true}'
            ),
        )
        corrupt.goto(APP)
        corrupt.wait_for_function(
            "() => window.StandaloneAPI && StandaloneAPI.__pure "
            "&& typeof StandaloneAPI.__pure.routeOfficer === 'function'"
        )
        result = corrupt.evaluate(
            """async () => StandaloneAPI.__pure.routeOfficer(
              {city:"Delhi",state:"Delhi",country_code:"in"},
              28.68203,77.07151,5,90,12)"""
        )
        if result.get("routed") is not False or result.get("unrouted_reason") != "road_class_unknown":
            failures.append(f"corrupt highway tile did not fail closed: {result!r}")
        corrupt.close()

        missing = browser.new_page()
        missing.route(
            "**/highway-manifest.json",
            lambda route: route.fulfill(status=503, content_type="text/plain", body="unavailable"),
        )
        missing.goto(APP)
        missing.wait_for_function(
            "() => window.StandaloneAPI && StandaloneAPI.__pure "
            "&& typeof StandaloneAPI.__pure.routeOfficer === 'function'"
        )
        result = missing.evaluate(
            """async () => StandaloneAPI.__pure.routeOfficer(
              {city:"Delhi",state:"Delhi",country_code:"in"},
              28.68203,77.07151,5,90,12)"""
        )
        if result.get("routed") is not False or result.get("unrouted_reason") != "road_class_unknown":
            failures.append(f"missing highway manifest did not fail closed: {result!r}")
        missing.close()
        browser.close()

    if failures:
        print("FAIL")
        for failure in failures:
            print("  -", failure)
        sys.exit(1)
    print("NATIONAL HIGHWAY ROUTING TEST PASS")


if __name__ == "__main__":
    run()
