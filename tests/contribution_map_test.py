#!/usr/bin/env python3
"""The dashboard map shows the public map only, and stays useful without tiles.

Private reports are deliberately not plotted here: the map is the shared, deduplicated
public view, and when it cannot be reached the app says so rather than quietly drawing
this phone's own history in its place.
"""

import base64
import json
import os
import pathlib
import sys

from playwright.sync_api import sync_playwright


# Exercise the canonical web source. pages_assets_test separately guarantees that this
# exact file is what Android and GitHub Pages ship.
APP = os.environ.get("POTHOLE_TEST_APP", "http://localhost:8765/web-app/")
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
  // Private history, to prove it never reaches the map.
  const now = Date.now() / 1000;
  const base = {
    created_at: now, captured_at: now, status: "draft", condition_status: "open",
    issue_type: "road_damage", decision: "accept", damage_type: "pothole_cavity",
    assessment: "damaged", image_quality: "acceptable", size: "medium",
    photo: pixel, photo_full: null, email_subject: "Pothole report",
    email_body: "Please inspect this pothole.",
  };
  const reports = [
    {...base, id: 88001, address: "Private Map Road", lat: 19.0760, lng: 72.8777},
    {...base, id: 88002, address: "Private Second Road", lat: 19.0770, lng: 72.8787},
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

# One plottable pothole and three the map must refuse: out-of-range latitude,
# out-of-range longitude, and a non-numeric coordinate pair.
PUBLIC_MAP = {
    "request_id": "test-map",
    "type": "FeatureCollection",
    "total": 4,
    "features": [
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [72.8777, 19.0760]},
         "properties": {"id": 501, "damage_type": "pothole_cavity", "size": "medium",
                        "first_seen_at": 1788500000000, "last_seen_at": 1788500010000,
                        "seen_count": 3, "town": "Mumbai", "lgd": "802791"}},
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [72.8777, 91]},
         "properties": {"id": 502, "damage_type": "pothole_cavity", "size": "medium",
                        "seen_count": 1}},
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [181, 19.0760]},
         "properties": {"id": 503, "damage_type": "pothole_cavity", "size": "medium",
                        "seen_count": 1}},
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": ["not-a-lng", "not-a-lat"]},
         "properties": {"id": 504, "damage_type": "pothole_cavity", "size": "medium",
                        "seen_count": 1}},
    ],
}


def route_central(route):
    """Answer the central service so no request leaves the machine."""
    path = route.request.url.split("amazonaws.com", 1)[-1].split("?")[0]
    body = {"request_id": "test", "error": "not_mocked", "message": path}
    status = 404
    if path.startswith("/v1/map"):
        body, status = PUBLIC_MAP, 200
    elif path.startswith("/v1/installations"):
        body, status = {"request_id": "test", "install_id": "test-installation"}, 201
    elif path.startswith("/v1/activity"):
        body, status = {"request_id": "test", "accepted": True}, 202
    elif path.startswith("/v1/health"):
        body, status = {"request_id": "test", "ai_configured": False}, 200
    route.fulfill(status=status, content_type="application/json", body=json.dumps(body))


def main():
    failures = []
    # Only shared pins reach the map, so a branch that opened a local report from a
    # pin, and a string for "showing this device's own", are text nobody can reach.
    source = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    if "map_local_pins" in source:
        failures.append("the unreachable map_local_pins string is still in the app")
    if "openDetail(r, pins)" in source:
        failures.append("drawMap still carries an unreachable open-local-report branch")
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
        context.route("**/*.amazonaws.com/**", route_central)
        # Deterministically offline for tiles: whether this machine can reach
        # openstreetmap.org must not decide which branch the suite exercises.
        context.route("https://tile.openstreetmap.org/**", lambda route: route.abort())
        page = context.new_page()
        page.goto(APP)
        page.wait_for_load_state("networkidle")
        page.wait_for_function(
            "typeof StandaloneAPI !== 'undefined' && typeof openDash === 'function'"
        )
        page.evaluate("StandaloneAPI.handle('/api/reports', {method: 'DELETE'})")

        # Offline: Leaflet cannot fetch tiles, so the points are plotted on plain SVG
        # and the note says why. The private reports seeded below must never appear.
        page.evaluate(SEED, {"pixel": PIXEL})
        page.evaluate("openDash()")
        page.locator("#map > svg").wait_for(state="visible", timeout=20_000)
        offline = page.evaluate(
            """() => ({
              online: navigator.onLine,
              points: document.querySelectorAll('#map > svg circle').length,
              note: document.querySelector('#mapNote').textContent.trim(),
              mapText: document.getElementById('map').textContent,
            })"""
        )
        if offline["online"] is not False:
            failures.append(f"offline branch was not exercised: {offline}")
        if offline["points"] != 1:
            failures.append(
                f"invalid or out-of-range coordinates leaked into the map: {offline}")
        if "Map tiles need a connection" not in offline["note"]:
            failures.append(f"offline fallback was not disclosed: {offline}")
        if "1 pothole · 3 reports" not in offline["note"]:
            failures.append(
                f"offline fallback described shared pins as this device's own: {offline}")
        for private in ("Private Map Road", "Private Second Road"):
            if private in offline["mapText"] or private in offline["note"]:
                failures.append(f"private history was drawn on the public map: {private}")
        context.close()

        # With the public map unreachable, the app says so instead of falling back to
        # the reports on this phone.
        context = browser.new_context(viewport={"width": 390, "height": 844})
        context.add_init_script(INIT)
        context.route(
            "**/*.amazonaws.com/**",
            lambda route: route.fulfill(status=503, content_type="application/json",
                                        body=json.dumps({"error": "unavailable"})),
        )
        page = context.new_page()
        page.goto(APP)
        page.wait_for_load_state("networkidle")
        page.wait_for_function("typeof openDash === 'function'")
        page.evaluate(SEED, {"pixel": PIXEL})
        page.evaluate("openDash()")
        page.locator("#dash").wait_for(state="visible")
        page.wait_for_timeout(500)
        # The map box says it once; the note under it is for counts only.
        unavailable = page.evaluate(
            """() => ({
              note: document.getElementById('map').textContent.trim(),
              mapText: document.getElementById('map').textContent,
              points: document.querySelectorAll('#map > svg circle').length,
            })"""
        )
        if "temporarily unavailable" not in unavailable["note"] or unavailable["points"]:
            failures.append(f"unreachable public map was not disclosed: {unavailable}")
        if "Private Map Road" in unavailable["mapText"]:
            failures.append("private history replaced the unreachable public map")
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
        context.route("**/*.amazonaws.com/**", route_central)
        page = context.new_page()
        page.route(
            "https://tile.openstreetmap.org/**",
            lambda route: route.fulfill(status=200, content_type="image/png", body=TILE_PNG),
        )
        page.goto(APP)
        page.wait_for_load_state("networkidle")
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
        if "Map tiles need a connection" in online["note"] or online["offlinePlot"]:
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
