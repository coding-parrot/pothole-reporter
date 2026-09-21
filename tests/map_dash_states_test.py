# -*- coding: utf-8 -*-
"""The public map screen says what it is doing on a slow link, and a late answer never wins.

Four ways a tester on a 2G/3G connection saw the map screen go wrong:
  - /v1/map slow: #map stayed an empty black box for 20 s with Refresh disabled, then
    settled on the same "temporarily unavailable" a 5xx gets, with no hint to retry.
  - Back and reopen while the first request was still out: the first answer landed 8 s
    later and replaced the fresh pins and counts with its older ones.
  - Tiles slower than 6 s: a working map with markers and popups was torn down for the
    SVG scatter, and the tiles that arrived at 9 s were never shown.

Routes are async so a delayed answer does not stall every other request of the page.
"""

import asyncio
import base64
import json
import sys

from playwright.async_api import async_playwright

from flow_harness import APP, DATA_NOTICE_VERSION

SERVICE = "https://map-states.test"
TILE_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
LOADING = "Loading public map"


def features(count, tag):
    return [{
        "type": "Feature",
        "geometry": {"type": "Point",
                     "coordinates": [77.55 + (i % 10) * 0.004, 12.95 + (i // 10) * 0.004]},
        "properties": {"id": i + 1, "damage_type": "pothole_cavity", "seen_count": 3,
                       "town": f"{tag} Ward {i}"},
    } for i in range(count)]


async def fulfill(route, status, payload):
    try:
        await route.fulfill(status=status, headers={"content-type": "application/json"},
                            body=json.dumps({"request_id": "req-map-states", **payload}))
    except Exception:
        pass  # the page closed while this answer was being held back


def service(map_handler):
    async def handle(route, request):
        path = request.url.split(SERVICE, 1)[-1].split("?", 1)[0]
        if path == "/v1/map":
            await map_handler(route)
        elif path == "/v1/impact":
            await fulfill(route, 200, {"period": {}, "active_installations": 2,
                                       "requests_total": 9, "potholes": {"total": 5},
                                       "observations": {"total": 15}})
        elif path == "/v1/health":
            await fulfill(route, 200, {"ok": True, "shared_vision_configured": True})
        else:
            await fulfill(route, 404, {"error": "not_mocked"})
    return handle


def tiles(delay=0.0):
    async def handle(route):
        if delay:
            await asyncio.sleep(delay)
        try:
            await route.fulfill(status=200, content_type="image/png", body=TILE_PNG)
        except Exception:
            pass
    return handle


async def open_app(browser, map_handler, tile_delay=0.0, hash_=""):
    context = await browser.new_context(
        viewport={"width": 412, "height": 915}, is_mobile=True, device_scale_factor=2.625)
    await context.add_init_script(
        f"localStorage.setItem('service_url', {json.dumps(SERVICE)});"
        f"localStorage.setItem('data_notice_version', {json.dumps(DATA_NOTICE_VERSION)});"
        "localStorage.setItem('initial_setup_complete', '1');"
        "localStorage.setItem('app_lang', 'en');")
    await context.route(f"{SERVICE}/**", service(map_handler))
    await context.route("https://tile.openstreetmap.org/**", tiles(tile_delay))
    page = await context.new_page()
    await page.goto(APP + hash_)
    await page.wait_for_function("() => typeof openDash === 'function'", timeout=30_000)
    return context, page


STATE = """() => ({
  map: document.getElementById('map').textContent.trim(),
  note: document.getElementById('mapNote').textContent.trim(),
  refreshDisabled: document.getElementById('dashRefresh').disabled,
  markers: document.querySelectorAll('#map .leaflet-interactive').length,
  scatter: document.querySelectorAll('#map > svg').length,
  tilesLoaded: document.querySelectorAll('#map .leaflet-tile-loaded').length,
  popups: (() => {
    const out = [];
    if (typeof mapObj !== 'undefined' && mapObj) mapObj.eachLayer((layer) => {
      if (layer && layer._popup && layer._popup._content) out.push(layer._popup._content);
    });
    return out.join(' ');
  })(),
})"""


async def settled_note(browser, map_handler, fails, where, slow=False):
    """Open the public map, check the loading state, and return the copy it settles on."""
    context, page = await open_app(browser, map_handler, hash_="#public-map")
    try:
        await page.wait_for_function(
            "() => !document.getElementById('dash').classList.contains('hidden')")
        await page.wait_for_timeout(500)
        early = await page.evaluate(STATE)
        if slow and LOADING not in early["map"]:
            fails.append(f"{where}: #map shows no loading text 500 ms after opening: {early}")
        try:
            await page.wait_for_function(
                "() => !document.getElementById('dashRefresh').disabled", timeout=10_000)
        except Exception:
            fails.append(f"{where}: the map screen had not settled after 10 s: "
                         f"{await page.evaluate(STATE)}")
        # The problem is said in the map box; the note under it carries only counts.
        return (await page.evaluate(STATE))["map"]
    finally:
        await context.close()


async def main():
    fails = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            async def stalled(route):
                await asyncio.sleep(20)
                await fulfill(route, 200, {"type": "FeatureCollection", "features": []})

            async def refused(route):
                await fulfill(route, 503, {"error": "unavailable", "message": "down"})

            slow = await settled_note(browser, stalled, fails, "slow /v1/map", slow=True)
            down = await settled_note(browser, refused, fails, "503 /v1/map")
            if not slow or slow == down:
                fails.append(f"a slow link and a 5xx read the same: {slow!r} / {down!r}")
            if "Refresh" not in slow:
                fails.append(f"the slow-link copy does not say to Refresh: {slow!r}")

            # An earlier, slower openDash must not overwrite a newer one.
            calls = {"n": 0}

            async def sequence(route):
                calls["n"] += 1
                if calls["n"] == 1:
                    await asyncio.sleep(8)
                    await fulfill(route, 200, {"type": "FeatureCollection",
                                               "features": features(2, "STALE")})
                else:
                    await fulfill(route, 200, {"type": "FeatureCollection",
                                               "features": features(5, "FRESH")})

            context, page = await open_app(browser, sequence)
            try:
                await page.locator("#home").wait_for(state="visible", timeout=30_000)
                await page.locator("#dashBtn").click()
                await page.wait_for_timeout(1000)
                await page.locator("#dashBack").click()
                await page.locator("#dashBtn").click()
                await page.wait_for_function(
                    "() => document.getElementById('mapNote').textContent.startsWith('5 potholes')",
                    timeout=10_000)
                await page.wait_for_timeout(10_000)
                late = await page.evaluate(STATE)
                if not late["note"].startswith("5 potholes") or "STALE" in late["popups"]:
                    fails.append(f"a late first answer replaced the fresh map: {late}")
            finally:
                await context.close()

            # Tiles that take 9 s are slow, not missing.
            async def fifty(route):
                await fulfill(route, 200, {"type": "FeatureCollection",
                                           "features": features(50, "SLOWTILE")})

            context, page = await open_app(browser, fifty, tile_delay=9, hash_="#public-map")
            try:
                await page.wait_for_function(
                    "() => document.querySelectorAll('#map .leaflet-interactive').length === 50",
                    timeout=10_000)
                await page.wait_for_timeout(7_500)
                at8 = await page.evaluate(STATE)
                if at8["markers"] != 50 or at8["scatter"]:
                    fails.append(f"slow tiles tore down the working map by 8 s: {at8}")
                await page.wait_for_timeout(3_000)
                at11 = await page.evaluate(STATE)
                if not at11["tilesLoaded"] or at11["scatter"]:
                    fails.append(f"tiles that arrived at 9 s were never shown: {at11}")
            finally:
                await context.close()
        finally:
            await browser.close()

    if fails:
        print("FAIL")
        for failure in fails:
            print("  -", failure)
        return 1
    print("MAP DASH STATES TEST PASS")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
