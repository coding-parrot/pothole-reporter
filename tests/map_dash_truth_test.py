# -*- coding: utf-8 -*-
"""The map screen reports only what it knows, and fits a phone.

What an audit of the dash found on a 412x915 phone:
  - Offline or with /v1/map and /v1/impact both failing, the community card showed
    "0 complaint reports" and "0 potholes" that nobody had counted, and the same
    "temporarily unavailable" sentence was printed three times.
  - "server requests" counted every /v1/health ping: 9640 of 9968 on the live service.
  - #public-map showed a zero-filled "Your contribution" card for history it never loads.
  - The map asked for the service default (1000 pins, 180 days) and said nothing when
    that cap was the whole answer.
  - Empty breakdowns printed em dashes, and one pin read "1 potholes · 1 reports".
  - One pin fitted the map to zoom 19 with + disabled.
  - Back sat 440 px below the fold.
  - Zoom buttons were 30 px and popup text 12 px.
  - "events checked" fell back to the report count when there were no drives.
  - Offline, a map seen a minute earlier was gone: nothing kept the last good answer.
"""

import asyncio
import json
import sys

from playwright.async_api import async_playwright

from flow_harness import APP, DATA_NOTICE_VERSION
from map_dash_states_test import features, fulfill, tiles

SERVICE = "https://map-truth.test"
UNAVAILABLE = "The public map is temporarily unavailable."
OFFLINE = "The public map did not load"
LIVE_REQUESTS = [
    {"route": "/v1/health", "outcome": "healthy", "vision_mode": "none", "count": 9640},
    {"route": "/v1/map", "outcome": "internal_error", "vision_mode": "none", "count": 145},
    {"route": "/v1/impact", "outcome": "impact_read", "vision_mode": "none", "count": 108},
    {"route": "/v1/detect", "outcome": "detected", "vision_mode": "shared", "count": 60},
    {"route": "/v1/reports", "outcome": "created", "vision_mode": "shared", "count": 15},
]


class Service:
    """A stub central service whose answers each case can change."""

    def __init__(self):
        self.map = lambda: (200, {"type": "FeatureCollection", "features": features(3, "T")})
        self.impact = lambda: (200, {"period": {}, "active_installations": 2,
                                     "requests_total": 9968, "requests": LIVE_REQUESTS,
                                     "potholes": {"total": 5}, "observations": {"total": 15}})
        self.down = False
        self.map_urls = []

    async def handle(self, route, request):
        path = request.url.split(SERVICE, 1)[-1].split("?", 1)[0]
        if self.down:
            await route.abort("internetdisconnected")
            return
        if path == "/v1/map":
            self.map_urls.append(request.url)
            await fulfill(route, *self.map())
        elif path == "/v1/impact":
            await fulfill(route, *self.impact())
        elif path == "/v1/health":
            await fulfill(route, 200, {"ok": True, "shared_vision_configured": True})
        else:
            await fulfill(route, 404, {"error": "not_mocked"})


async def open_app(browser, service, hash_=""):
    context = await browser.new_context(
        viewport={"width": 412, "height": 915}, is_mobile=True, device_scale_factor=2.625)
    await context.add_init_script(
        f"localStorage.setItem('service_url', {json.dumps(SERVICE)});"
        f"localStorage.setItem('data_notice_version', {json.dumps(DATA_NOTICE_VERSION)});"
        "localStorage.setItem('initial_setup_complete', '1');"
        "localStorage.setItem('app_lang', 'en');")
    await context.route(f"{SERVICE}/**", service.handle)
    await context.route("https://tile.openstreetmap.org/**", tiles())
    page = await context.new_page()
    await page.goto(APP + hash_)
    await page.wait_for_function("() => typeof openDash === 'function'", timeout=30_000)
    return context, page


STATE = """() => {
  const dash = document.getElementById('dash');
  const box = (sel) => {
    const el = document.querySelector(sel);
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return {top: r.top, width: r.width, height: r.height};
  };
  return {
    text: dash.innerText,
    stats: document.getElementById('communityStats').innerText.trim(),
    note: document.getElementById('mapNote').textContent.trim(),
    breakdown: document.getElementById('dashBreak').innerText,
    dashStats: document.getElementById('dashStats').innerText,
    contributionShown: document.getElementById('dashTitle').offsetParent !== null,
    breakShown: document.getElementById('dashBreak').offsetParent !== null,
    markers: document.querySelectorAll('#map .leaflet-interactive').length,
    scatter: document.querySelectorAll('#map > svg circle').length,
    zoom: typeof mapObj !== 'undefined' && mapObj ? mapObj.getZoom() : null,
    back: box('#dashBack'),
    zoomIn: box('.leaflet-control-zoom-in'),
  };
}"""


async def open_dash(page):
    await page.evaluate("() => { void openDash(); }")
    await page.wait_for_function(
        "() => !document.getElementById('dash').classList.contains('hidden')"
        " && !document.getElementById('dashRefresh').disabled", timeout=15_000)
    await page.wait_for_timeout(300)
    return await page.evaluate(STATE)


def squash(text):
    return "".join(text.split())


async def main():
    fails = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            # Both endpoints failing: no invented tiles, the problem said once.
            service = Service()
            service.map = lambda: (503, {"error": "unavailable", "message": "down"})
            service.impact = lambda: (500, {"error": "internal_error", "message": "down"})
            context, page = await open_app(browser, service)
            try:
                state = await open_dash(page)
                if state["stats"]:
                    fails.append(f"5xx: community tiles invented from nothing: {state['stats']!r}")
                if state["text"].count(UNAVAILABLE) != 1:
                    fails.append(f"5xx: the unavailable sentence appears "
                                 f"{state['text'].count(UNAVAILABLE)} times")
            finally:
                await context.close()

            # Offline, then online, then offline again with the last good map kept.
            service = Service()
            context, page = await open_app(browser, service)
            try:
                service.down = True
                await context.set_offline(True)
                state = await open_dash(page)
                if state["stats"]:
                    fails.append(f"offline: community tiles invented: {state['stats']!r}")
                if state["text"].count(OFFLINE) != 1 or UNAVAILABLE in state["text"]:
                    fails.append(f"offline: expected the offline sentence once: {state['text']!r}")
                await context.set_offline(False)
                service.down = False
                state = await open_dash(page)
                if state["markers"] != 3:
                    fails.append(f"online: expected 3 markers: {state}")
                service.down = True
                await context.set_offline(True)
                state = await open_dash(page)
                if state["markers"] + state["scatter"] != 3:
                    fails.append(f"offline reopen lost the map seen a moment ago: {state}")
                if "saved" not in state["note"].lower():
                    fails.append(f"offline reopen does not say the map is a saved copy: "
                                 f"{state['note']!r}")
            finally:
                await context.close()

            # Health pings are not activity; history the page never loads is not zero.
            service = Service()
            context, page = await open_app(browser, service, "#public-map")
            try:
                await page.wait_for_function(
                    "() => !document.getElementById('dashRefresh').disabled"
                    " && document.getElementById('mapNote').textContent", timeout=15_000)
                state = await page.evaluate(STATE)
                if "75serverrequests" not in squash(state["stats"]):
                    fails.append(f"server requests still count health, map and impact reads: "
                                 f"{state['stats']!r}")
                if state["contributionShown"] or state["breakShown"]:
                    fails.append("#public-map shows the personal contribution card it never loads")
                if not service.map_urls or "limit=2000" not in service.map_urls[-1]:
                    fails.append(f"map is not asked for the service maximum: {service.map_urls}")
            finally:
                await context.close()

            # The normal open: the personal cards are back, Back is on screen, copy is clean.
            service = Service()
            service.map = lambda: (200, {"type": "FeatureCollection", "features": [{
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [77.59, 12.97]},
                "properties": {"id": 7, "damage_type": "pothole_cavity", "seen_count": 1},
            }]})
            context, page = await open_app(browser, service)
            try:
                await page.evaluate("""() => {
                  const real = api;
                  const now = Math.floor(Date.now() / 1000);
                  const reports = Array.from({length: 300}, (_, i) => ({
                    id: i + 1, status: 'review', created_at: now - i,
                  }));
                  window.api = (path, opts) => path === '/api/reports' ? Promise.resolve(reports)
                    : path === '/api/drives' || path === '/api/footage' ? Promise.resolve([])
                    : real(path, opts);
                }""")
                state = await open_dash(page)
                if not state["contributionShown"] or not state["breakShown"]:
                    fails.append("the contribution cards stay hidden outside #public-map")
                if "opened0eventschecked" not in squash(state["dashStats"]):
                    fails.append(f"events checked is not the drive total: {state['dashStats']!r}")
                if "\u2014" in state["text"] or "\u2013" in state["text"]:
                    fails.append(f"the dash still prints a dash as a value: {state['breakdown']!r}")
                if "none yet" not in state["breakdown"]:
                    fails.append(f"empty breakdowns do not say none yet: {state['breakdown']!r}")
                if state["note"] != "1 pothole · 1 report":
                    fails.append(f"one pin is not singular: {state['note']!r}")
                if state["zoom"] is None or state["zoom"] > 16:
                    fails.append(f"one pin fits the map to zoom {state['zoom']}")
                if not state["back"] or state["back"]["top"] >= 915:
                    fails.append(f"Back is below the fold: {state['back']}")
                zoom_in = state["zoomIn"]
                if not zoom_in or zoom_in["width"] < 44 or zoom_in["height"] < 44:
                    fails.append(f"zoom control is under 44 px: {zoom_in}")
                popup = await page.evaluate("""async () => {
                  let marker = null;
                  mapGroup.eachLayer((layer) => { marker = marker || layer; });
                  marker.openPopup();
                  await new Promise((r) => setTimeout(r, 100));
                  const content = document.querySelector('.leaflet-popup-content');
                  const close = document.querySelector('.leaflet-popup-close-button')
                    .getBoundingClientRect();
                  return {font: parseFloat(getComputedStyle(content).fontSize),
                          close: Math.min(close.width, close.height)};
                }""")
                if popup["font"] < 15 or popup["close"] < 32:
                    fails.append(f"popup text or close button too small: {popup}")
            finally:
                await context.close()

            # A full answer is the cap, and the note says so.
            service = Service()
            service.map = lambda: (200, {"type": "FeatureCollection",
                                         "features": features(2000, "CAP")})
            context, page = await open_app(browser, service, "#public-map")
            try:
                await page.wait_for_function(
                    "() => document.getElementById('mapNote').textContent.includes('2000')",
                    timeout=20_000)
                note = (await page.evaluate(STATE))["note"]
                if "newest 2000" not in note or "6 months" not in note:
                    fails.append(f"a capped map reads as the total: {note!r}")
            finally:
                await context.close()
        finally:
            await browser.close()

    if fails:
        print("FAIL")
        for failure in fails:
            print("  -", failure)
        return 1
    print("MAP DASH TRUTH TEST PASS")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
