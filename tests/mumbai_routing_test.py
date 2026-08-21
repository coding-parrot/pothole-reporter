# -*- coding: utf-8 -*-
"""Greater Mumbai routing must be deterministic and conservative.

These checks exercise the exact pure helpers used after Nominatim reverse geocoding.
They deliberately include localized Marathi jurisdiction fields because Nominatim may
honour the phone/browser language even when the ward token remains English.
"""
import sys

from playwright.sync_api import sync_playwright


APP = "http://localhost:8765/"

CASES = r"""
(() => {
  const P = StandaloneAPI.__pure;
  const out = [];
  const eq = (name, got, want) => out.push([
    name, JSON.stringify(got) === JSON.stringify(want), got, want,
  ]);
  const ok = (name, condition, detail) => out.push([
    name, !!condition, detail === undefined ? condition : detail, true,
  ]);

  const wards = [
    "A", "B", "C", "D", "E", "F/N", "F/S", "G/N", "G/S", "H/E", "H/W",
    "K/E", "K/W", "L", "M/E", "M/W", "N", "P/N", "P/S", "R/C", "R/N",
    "R/S", "S", "T",
  ];
  for (const ward of wards) {
    eq(`ward: parses official ${ward}`, P.mumbaiWardFromName(
      `Example Road, ${ward} Ward, Mumbai, Maharashtra, India`), ward);
  }
  eq("ward: parsing is case-insensitive",
     P.mumbaiWardFromName("Example Road, k/w ward, Mumbai"), "K/W");
  eq("ward: accepts a standalone official label", P.mumbaiWardFromName("A Ward"), "A");
  eq("ward: accepts ward-first labels", P.mumbaiWardFromName("Example Road, Ward K/W, Mumbai"), "K/W");
  eq("ward: normalizes hyphenated slash wards", P.mumbaiWardFromName("Example Road, K-W Ward, Mumbai"), "K/W");
  eq("ward: parses expanded directional wards", P.mumbaiWardFromName("Example Road, K West Ward, Mumbai"), "K/W");
  for (const bad of [null, "", "Q Ward", "F/W Ward", "A Wardrobe", "K/W Ward West"]) {
    eq(`ward: rejects ${String(bad)}`, P.mumbaiWardFromName(bad), null);
  }

  const city = P.mumbaiFromGeocode({
    full: "Shahid Bhagat Singh Road, A Ward, Mumbai, Mumbai City District, Maharashtra, India",
    city: "Mumbai",
    state_district: "Mumbai City District",
    state: "Maharashtra",
    country_code: "in",
  });
  ok("classify: Mumbai City District is Greater Mumbai", city && city.kind === "mumbai", city);
  eq("classify: Mumbai City ward survives", city && city.ward, "A");
  eq("classify: source is explicit", city && city.source, "openstreetmap");

  const suburb = P.mumbaiFromGeocode({
    full: "Juhu Lane, K/W Ward, Mumbai, Mumbai Suburban District, Maharashtra, India",
    state_district: "Mumbai Suburban District",
    state: "Maharashtra",
    country_code: "IN",
  });
  ok("classify: Mumbai Suburban District is Greater Mumbai",
     suburb && suburb.kind === "mumbai", suburb);
  eq("classify: suburban slash ward survives", suburb && suburb.ward, "K/W");

  const noWard = P.mumbaiFromGeocode({
    full: "Mumbai, Mumbai City District, Maharashtra, India",
    state_district: "Mumbai City District",
    state: "Maharashtra",
    country_code: "in",
  });
  ok("classify: missing ward does not reject a valid Mumbai point",
     noWard && noWard.kind === "mumbai", noWard);
  eq("classify: missing ward remains unknown", noWard && noWard.ward, null);

  // Representative localized shapes returned by Nominatim when Marathi is preferred.
  const mrCity = P.mumbaiFromGeocode({
    full: "फोर्ट, A Ward, मुंबई, मुंबई शहर जिल्हा, महाराष्ट्र, भारत",
    state_district: "मुंबई शहर जिल्हा",
    state: "महाराष्ट्र",
    country_code: "in",
  });
  ok("classify: Marathi Mumbai City fields remain in coverage",
     mrCity && mrCity.kind === "mumbai", mrCity);
  eq("classify: ward parses from Marathi-localized city response", mrCity && mrCity.ward, "A");

  const mrSuburb = P.mumbaiFromGeocode({
    full: "अंधेरी, K/W Ward, मुंबई, मुंबई उपनगर जिल्हा, महाराष्ट्र, भारत",
    state_district: "मुंबई उपनगर जिल्हा",
    state: "महाराष्ट्र",
    country_code: "in",
  });
  ok("classify: Marathi Mumbai Suburban fields remain in coverage",
     mrSuburb && mrSuburb.kind === "mumbai", mrSuburb);
  eq("classify: ward parses from Marathi-localized suburban response",
     mrSuburb && mrSuburb.ward, "K/W");

  const rejected = [
    ["Thane is not Greater Mumbai", {
      full: "Thane, Thane District, Maharashtra, India",
      state_district: "Thane District", state: "Maharashtra", country_code: "in",
    }],
    ["Navi Mumbai is not inferred from the city name", {
      full: "Navi Mumbai, Thane District, Maharashtra, India",
      city: "Navi Mumbai", state_district: "Thane District",
      state: "Maharashtra", country_code: "in",
    }],
    ["Mumbai name alone is insufficient without a supported district", {
      full: "Mumbai, Maharashtra, India", city: "Mumbai",
      state_district: null, state: "Maharashtra", country_code: "in",
    }],
    ["wrong state is rejected", {
      full: "A Ward, Mumbai", state_district: "Mumbai City District",
      state: "Karnataka", country_code: "in",
    }],
    ["wrong country is rejected", {
      full: "A Ward, Mumbai", state_district: "Mumbai City District",
      state: "Maharashtra", country_code: "gb",
    }],
    ["missing geocode is rejected", null],
  ];
  for (const [name, geo] of rejected) eq(`classify: ${name}`, P.mumbaiFromGeocode(geo), null);

  return out;
})()
"""


def main():
    failures = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=["--disable-web-security"])
        page = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
        page.goto(APP)
        page.wait_for_load_state("networkidle")
        page.wait_for_function(
            "typeof StandaloneAPI !== 'undefined' && StandaloneAPI.__pure",
            timeout=30000,
        )
        results = page.evaluate(CASES)
        browser.close()

    for name, passed, got, want in results:
        if not passed:
            failures.append(name)
            print(f"  FAIL {name}\n         got  {got}\n         want {want}")
    if failures:
        print(f"{len(failures)} of {len(results)} failed")
        sys.exit(1)
    print(f"MUMBAI ROUTING TEST PASS ({len(results)} checks)")


main()
