# -*- coding: utf-8 -*-
"""Opening the map screen again does not keep the previous Leaflet map alive.

drawMap built a new L.map on every open and called remove() on the old one, but Leaflet
1.9.4 keeps a removed map reachable. Ten open-and-back cycles at 1000 pins grew the page
from 1857 to 12069 DOM nodes and from 5.1 MB to 20.7 MB of heap, never reclaimed. The
screen now keeps one map for the page and swaps its markers, so after any number of
opens there is one L.Map and exactly one CircleMarker per pin.
"""

import base64
import json
import sys

from playwright.sync_api import sync_playwright

from flow_harness import APP, DATA_NOTICE_VERSION

SERVICE = "https://map-memory.test"
PINS = 200
OPENS = 5
TILE_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
FEATURES = [{
    "type": "Feature",
    "geometry": {"type": "Point",
                 "coordinates": [77.5 + (i % 20) * 0.003, 12.9 + (i // 20) * 0.003]},
    "properties": {"id": i + 1, "damage_type": "pothole_cavity", "seen_count": 1},
} for i in range(PINS)]


def route_service(route, request):
    path = request.url.split(SERVICE, 1)[-1].split("?", 1)[0]
    payload = {"type": "FeatureCollection", "features": FEATURES} if path == "/v1/map" else {
        "period": {}, "active_installations": 1, "requests_total": 1,
        "potholes": {"total": PINS}, "observations": {"total": PINS}}
    route.fulfill(status=200, headers={"content-type": "application/json"},
                  body=json.dumps({"request_id": "req-map-memory", **payload}))


def live_instances(cdp, prototype):
    """Instances of `prototype`, counted after a full GC.

    queryObjects also returns the prototypes of subclasses, such as L.Circle.prototype
    under L.CircleMarker; those own a `constructor` and are not counted.
    """
    cdp.send("HeapProfiler.collectGarbage")
    proto = cdp.send("Runtime.evaluate", {"expression": prototype})["result"]["objectId"]
    found = cdp.send("Runtime.queryObjects", {"prototypeObjectId": proto})["objects"]
    return cdp.send("Runtime.callFunctionOn", {
        "objectId": found["objectId"],
        "functionDeclaration": "function () { return this.filter((o) =>"
                               " !Object.prototype.hasOwnProperty.call(o, 'constructor')).length; }",
        "returnByValue": True})["result"]["value"]


fails = []
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    try:
        context = browser.new_context(
            viewport={"width": 412, "height": 915}, is_mobile=True, device_scale_factor=2.625)
        context.add_init_script(
            f"localStorage.setItem('service_url', {json.dumps(SERVICE)});"
            f"localStorage.setItem('data_notice_version', {json.dumps(DATA_NOTICE_VERSION)});"
            "localStorage.setItem('initial_setup_complete', '1');"
            "localStorage.setItem('app_lang', 'en');")
        context.route(f"{SERVICE}/**", route_service)
        context.route("https://tile.openstreetmap.org/**", lambda route: route.fulfill(
            status=200, content_type="image/png", body=TILE_PNG))
        page = context.new_page()
        page.goto(APP)
        page.locator("#home").wait_for(state="visible", timeout=30_000)
        cdp = context.new_cdp_session(page)
        for _ in range(OPENS):
            page.locator("#dashBtn").click()
            page.wait_for_function(
                f"() => document.querySelectorAll('#map .leaflet-interactive').length === {PINS}",
                timeout=20_000)
            page.locator("#dashBack").click()
            page.locator("#home").wait_for(state="visible")
        page.locator("#dashBtn").click()
        page.wait_for_function(
            f"() => document.querySelectorAll('#map .leaflet-interactive').length === {PINS}",
            timeout=20_000)
        maps = live_instances(cdp, "L.Map.prototype")
        markers = live_instances(cdp, "L.CircleMarker.prototype")
        if maps != 1:
            fails.append(f"{OPENS + 1} opens left {maps} live L.Map instances, expected 1")
        if markers != PINS:
            fails.append(f"{OPENS + 1} opens left {markers} live CircleMarkers, expected {PINS}")
        context.close()
    finally:
        browser.close()

if fails:
    print("FAIL")
    for failure in fails:
        print("  -", failure)
    sys.exit(1)
print("MAP DASH MEMORY TEST PASS")
