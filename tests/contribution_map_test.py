#!/usr/bin/env python3
"""Contribution map stays useful with no reports, invalid GPS, or no tile network."""

import base64
import json
import pathlib
import sys

from playwright.sync_api import sync_playwright


# Exercise the canonical web source. pages_assets_test separately guarantees that this
# exact file is what Android and GitHub Pages ship.
APP = "http://localhost:8765/web-app/"
ROOT = pathlib.Path(__file__).resolve().parents[1]
PIXEL = (
    "data:image/gif;base64,"
    "R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs="
)
TILE_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)

INIT = r"""
(() => {
  localStorage.setItem("openai_key", "test-key-never-sent");
  localStorage.setItem("initial_setup_complete", "1");
  localStorage.setItem("app_lang", "en");
  Object.defineProperty(Navigator.prototype, "onLine", {
    configurable: true,
    get: () => false,
  });
})();
"""

SEED = r"""
async ({pixel}) => {
  const now = Date.now() / 1000;
  const base = {
    created_at: now,
    captured_at: now,
    status: "draft",
    condition_status: "open",
    issue_type: "road_damage",
    decision: "accept",
    damage_type: "pothole_cavity",
    assessment: "clear",
    image_quality: "usable",
    size: "medium",
    photo: pixel,
    photo_full: null,
    email_subject: "Pothole report",
    email_body: "Please inspect this pothole.",
  };
  const reports = [
    {...base, id: 88001, address: "Valid Map Road", lat: 19.0760, lng: 72.8777},
    {...base, id: 88002, address: "Invalid Latitude", lat: 91, lng: 72.8777},
    {...base, id: 88003, address: "Invalid Longitude", lat: 19.0760, lng: 181},
    {...base, id: 88004, address: "Non-numeric GPS", lat: "not-a-lat", lng: "not-a-lng"},
  ];

  const db = await new Promise((resolve, reject) => {
    const request = indexedDB.open("potholes");
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  await new Promise((resolve, reject) => {
    const tx = db.transaction("reports", "readwrite");
    for (const report of reports) tx.objectStore("reports").put(report);
    tx.oncomplete = resolve;
    tx.onabort = () => reject(tx.error || new Error("map seed transaction aborted"));
    tx.onerror = () => {};
  });
  db.close();
}
"""


def main():
    failures = []
    capacitor = json.loads(
        (ROOT / "android-app" / "capacitor.config.json").read_text(encoding="utf-8")
    )
    tile_user_agent = capacitor.get("appendUserAgent", "")
    if not all(value in tile_user_agent for value in (
        "PotholeReporter/", "coding-parrot.github.io/pothole-reporter", "contact@aiengg.dev"
    )):
        failures.append(f"Android map requests have no identifiable user agent: {tile_user_agent}")
    with sync_playwright() as playwright:
        launch_options = {"args": ["--disable-web-security"]}
        system_chrome = pathlib.Path(
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        )
        if system_chrome.is_file():
            launch_options["executable_path"] = str(system_chrome)
        browser = playwright.chromium.launch(**launch_options)
        context = browser.new_context(viewport={"width": 390, "height": 844})
        context.add_init_script(INIT)
        page = context.new_page()
        page.goto(APP)
        page.wait_for_load_state("networkidle")
        page.wait_for_function(
            "typeof StandaloneAPI !== 'undefined' && typeof openDash === 'function'"
        )

        page.evaluate(
            "StandaloneAPI.handle('/api/reports', {method: 'DELETE'})"
        )
        page.evaluate("openDash()")
        page.locator("#dash").wait_for(state="visible")
        empty = page.evaluate(
            """() => ({
              message: document.querySelector('#map .empty')?.textContent.trim() || '',
              note: document.querySelector('#mapNote').textContent.trim(),
              svgCount: document.querySelectorAll('#map svg').length,
            })"""
        )
        if empty != {
            "message": "No mapped potholes yet.",
            "note": "0 pinned",
            "svgCount": 0,
        }:
            failures.append(f"empty dashboard did not show a visible map state: {empty}")

        page.evaluate(SEED, {"pixel": PIXEL})
        page.evaluate("openDash()")
        page.locator("#map svg").wait_for(state="visible")
        offline = page.evaluate(
            """() => ({
              online: navigator.onLine,
              points: document.querySelectorAll('#map [data-map-report]').length,
              note: document.querySelector('#mapNote').textContent.trim(),
            })"""
        )
        if offline["online"] is not False:
            failures.append(f"offline branch was not exercised: {offline}")
        if offline["points"] != 1:
            failures.append(
                "invalid or out-of-range coordinates leaked into the map: "
                f"{offline}"
            )
        if not offline["note"].startswith("1 pinned · Map tiles need a connection"):
            failures.append(f"offline fallback was not disclosed: {offline}")

        page.locator('#map [data-map-report="0"]').click()
        page.locator("#detail").wait_for(state="visible")
        detail = page.locator("#detail").inner_text()
        if "Valid Map Road" not in detail:
            failures.append(f"fallback point did not open its report detail: {detail}")
        if any(label in detail for label in (
            "Invalid Latitude", "Invalid Longitude", "Non-numeric GPS"
        )):
            failures.append(f"invalid GPS reports leaked into map navigation: {detail}")

        context.close()

        # Exercise the normal Leaflet path deterministically. A tiny intercepted image
        # proves tileload, map sizing, and marker rendering without making the suite
        # depend on the public OSM service being reachable at test time.
        context = browser.new_context(viewport={"width": 390, "height": 844})
        context.add_init_script(
            "localStorage.setItem('openai_key', 'test-key-never-sent');"
            "localStorage.setItem('initial_setup_complete', '1');"
            "localStorage.setItem('app_lang', 'en');"
        )
        page = context.new_page()
        page.route(
            "https://tile.openstreetmap.org/**",
            lambda route: route.fulfill(status=200, content_type="image/png", body=TILE_PNG),
        )
        page.goto(APP)
        page.wait_for_load_state("networkidle")
        page.evaluate(SEED, {"pixel": PIXEL})
        page.evaluate("openDash()")
        page.locator("#map .leaflet-tile-loaded").first.wait_for(state="visible")
        online = page.evaluate(
            """() => ({
              tiles: document.querySelectorAll('#map .leaflet-tile-loaded').length,
              markers: document.querySelectorAll('#map .leaflet-interactive').length,
              note: document.querySelector('#mapNote').textContent.trim(),
              offlinePlot: document.querySelectorAll('#map > svg').length,
            })"""
        )
        if online["tiles"] < 1 or online["markers"] != 1:
            failures.append(f"Leaflet tiles/marker did not render: {online}")
        if online["note"] != "1 pinned" or online["offlinePlot"]:
            failures.append(f"successful tiles incorrectly fell back offline: {online}")
        context.close()
        browser.close()

    if failures:
        print("CONTRIBUTION MAP TEST FAIL")
        for failure in failures:
            print("  -", failure)
        return 1
    print("CONTRIBUTION MAP TEST PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
