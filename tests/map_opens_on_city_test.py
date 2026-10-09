# -*- coding: utf-8 -*-
"""The Pothole map opens on the city the person is in.

The owner (9 Oct 2026): "by default people should see the map of their current city
... and then zoom in or out as wanted", "I mean their city map level". The map opened
fitted to every report in India, so someone in Bengaluru saw a country with dots on it.

Here reports sit in Bengaluru, Delhi and Kolkata:
  - a phone in Bengaluru that already lets the app read its location opens on Bengaluru,
    a city wide, and can still be zoomed out to India and in to a street;
  - the next open, with location now refused, still opens on Bengaluru (the city is
    remembered on the phone);
  - a phone that never allowed location is not asked on this screen and sees all of
    India's reports, as before;
  - a phone abroad sees all of India's reports;
  - a person who has already moved the map is not pulled back when the fix arrives late.
"""

import asyncio
import json
import sys

from playwright.async_api import async_playwright

from flow_harness import APP, DATA_NOTICE_VERSION
from map_dash_states_test import SERVICE, fulfill, service, tiles

BENGALURU = {"latitude": 12.9716, "longitude": 77.5946}
NEW_YORK = {"latitude": 40.7128, "longitude": -74.0060}
POINTS = [(77.5946, 12.9716, "Bengaluru"), (77.62, 12.99, "Bengaluru"), (77.209, 28.6139, "Delhi"),
          (88.3639, 22.5726, "Kolkata")]

COUNT_ASKS = """(() => {
  window.__asks = 0;
  const geo = navigator.geolocation, ask = geo.getCurrentPosition.bind(geo);
  const delay = Number(localStorage.getItem('test_fix_delay_ms') || 0);
  geo.getCurrentPosition = (ok, bad, options) => {
    window.__asks += 1;
    setTimeout(() => ask(ok, bad, options), delay);
  };
})();"""

VIEW = """() => {
  const b = mapObj.getBounds(), c = mapObj.getCenter();
  return { zoom: mapObj.getZoom(), minZoom: mapObj.getMinZoom(), maxZoom: mapObj.getMaxZoom(),
           lat: c.lat, lng: c.lng, south: b.getSouth(), north: b.getNorth(),
           widthKm: mapObj.distance(b.getSouthWest(), b.getSouthEast()) / 1000,
           markers: document.querySelectorAll('#map .leaflet-interactive').length,
           asks: window.__asks };
}"""


def features():
    return [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [lng, lat]},
             "properties": {"id": i + 1, "damage_type": "pothole_cavity", "seen_count": 2, "town": town}}
            for i, (lng, lat, town) in enumerate(POINTS)]


async def answer(route):
    await fulfill(route, 200, {"type": "FeatureCollection", "features": features()})


async def new_context(browser, where=None, fix_delay_ms=0):
    context = await browser.new_context(
        viewport={"width": 412, "height": 915}, is_mobile=True, device_scale_factor=2.625,
        **({"geolocation": where, "permissions": ["geolocation"]} if where else {}))
    await context.add_init_script(
        f"localStorage.setItem('service_url', {json.dumps(SERVICE)});"
        f"localStorage.setItem('data_notice_version', {json.dumps(DATA_NOTICE_VERSION)});"
        "localStorage.setItem('initial_setup_complete', '1');"
        "localStorage.setItem('app_lang', 'en');"
        f"localStorage.setItem('test_fix_delay_ms', '{fix_delay_ms}');" + COUNT_ASKS)
    await context.route(f"{SERVICE}/**", service(answer))
    await context.route("https://server.arcgisonline.com/**", tiles())
    return context


async def open_map(context, settle_ms=2500, page=None):
    page = page or await context.new_page()
    await page.goto(APP)
    await page.wait_for_function("() => typeof openDash === 'function'", timeout=30_000)
    await page.evaluate("() => { openDash(); }")
    await page.wait_for_function(
        "() => document.querySelectorAll('#map .leaflet-interactive').length > 0", timeout=30_000)
    await page.wait_for_timeout(settle_ms)
    return page


def on_bengaluru(view):
    return abs(view["lat"] - BENGALURU["latitude"]) < 0.05 and abs(view["lng"] - BENGALURU["longitude"]) < 0.05


def city_wide(view):
    return 20 <= view["widthKm"] <= 80


def whole_country(view):
    # Delhi and Kolkata are both on screen.
    return view["north"] > 28.7 and view["widthKm"] > 900


async def main():
    fails = []
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            # A phone in Bengaluru that already allows location.
            context = await new_context(browser, BENGALURU)
            page = await open_map(context)
            view = await page.evaluate(VIEW)
            if not on_bengaluru(view) or not city_wide(view):
                fails.append("in Bengaluru with location allowed, the map opens on "
                             f"{view['lat']:.2f}, {view['lng']:.2f}, {view['widthKm']:.0f} km wide; "
                             "it should be Bengaluru, a city wide")
            if view["markers"] != 4:
                fails.append(f"{view['markers']} reports are drawn; all four stay on the map to zoom out to")
            await page.evaluate("() => mapObj.setZoom(1, { animate: false })")
            await page.wait_for_timeout(600)
            out = await page.evaluate(VIEW)
            if not whole_country(out):
                fails.append(f"from the city the map cannot be zoomed out to India: {out['widthKm']:.0f} km wide")
            await page.evaluate("() => mapObj.setView([12.9716, 77.5946], 18, { animate: false })")
            await page.wait_for_timeout(600)
            close = await page.evaluate(VIEW)
            if close["zoom"] < 17:
                fails.append(f"from the city the map cannot be zoomed in to a street: zoom {close['zoom']}")

            # The same phone later, location now refused: the city is remembered.
            await context.clear_permissions()
            page = await open_map(context, page=page)
            later = await page.evaluate(VIEW)
            if not on_bengaluru(later) or not city_wide(later):
                fails.append("the next open, with location refused, forgets the city: "
                             f"{later['lat']:.2f}, {later['lng']:.2f}, {later['widthKm']:.0f} km wide")
            if later["asks"]:
                fails.append(f"with location refused the map screen asked for it {later['asks']} time(s)")
            await context.close()

            # Never allowed: not asked here, and all of India's reports are on screen.
            context = await new_context(browser)
            page = await open_map(context)
            fresh = await page.evaluate(VIEW)
            if fresh["asks"]:
                fails.append(f"a phone that never allowed location was asked {fresh['asks']} time(s) by the map screen")
            if not whole_country(fresh):
                fails.append(f"with no location the map should show every report in India: {fresh['widthKm']:.0f} km wide")
            await context.close()

            # Abroad: no Indian city to open on.
            context = await new_context(browser, NEW_YORK)
            page = await open_map(context)
            abroad = await page.evaluate(VIEW)
            if not whole_country(abroad):
                fails.append(f"a phone abroad should see every report in India: centre {abroad['lat']:.1f}, "
                             f"{abroad['lng']:.1f}, {abroad['widthKm']:.0f} km wide")
            await context.close()

            # The fix arrives after the person has moved the map: they are left where they went.
            context = await new_context(browser, BENGALURU, fix_delay_ms=2500)
            page = await open_map(context, settle_ms=300)
            box = await page.locator("#map").bounding_box()
            await page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            await page.mouse.down()
            await page.mouse.move(box["x"] + box["width"] / 2 + 60, box["y"] + box["height"] / 2 + 40, steps=5)
            await page.mouse.up()
            await page.wait_for_timeout(400)
            moved = await page.evaluate(VIEW)
            await page.wait_for_timeout(3500)
            after = await page.evaluate(VIEW)
            if abs(after["lat"] - moved["lat"]) > 0.01 or abs(after["zoom"] - moved["zoom"]) > 0.01:
                fails.append("a late fix pulled the map away from where the person had moved it: "
                             f"zoom {moved['zoom']} to {after['zoom']}")
            await context.close()
        finally:
            await browser.close()
    if fails:
        print("FAIL")
        for fail in fails:
            print("  -", fail)
        sys.exit(1)
    print("MAP OPENS ON CITY TEST PASS")


asyncio.run(main())
