# -*- coding: utf-8 -*-
"""The Pothole map is a map of India and nothing else.

The owner (8 Oct 2026): "it should only focus on india. Complaints outside india not
possible anyway." The map opened on India but could be dragged and zoomed out to the
whole world, and it drew the three reports filed from Mauritius, Brazil and Java, which
no Indian road authority can be written to about. Here the service answers with four
reports in India and two abroad: only the four are drawn and counted, the map cannot
be zoomed out past India, and it cannot be dragged away to where the other two were.
"""

import asyncio
import sys

from playwright.async_api import async_playwright

from map_dash_states_test import fulfill, open_app

INDIA = {"south": 6.0, "north": 37.6, "west": 67.5, "east": 98.0}
POINTS = [(77.5946, 12.9716, "Bengaluru"), (77.60, 12.98, "Bengaluru"), (77.209, 28.6139, "Delhi"),
          (88.3639, 22.5726, "Kolkata"), (57.6, -20.4, "Mauritius"), (110.4, -7.7, "Java")]


def features():
    return [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [lng, lat]},
             "properties": {"id": i + 1, "damage_type": "pothole_cavity", "seen_count": 2, "town": town}}
            for i, (lng, lat, town) in enumerate(POINTS)]


VIEW = """() => {
  const b = mapObj.getBounds(), c = mapObj.getCenter();
  return { zoom: mapObj.getZoom(), minZoom: mapObj.getMinZoom(), lat: c.lat, lng: c.lng,
           south: b.getSouth(), north: b.getNorth(), west: b.getWest(), east: b.getEast(),
           markers: document.querySelectorAll('#map .leaflet-interactive').length,
           note: document.getElementById('mapNote').textContent.trim() };
}"""


async def main():
    fails = []
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            async def answer(route):
                await fulfill(route, 200, {"type": "FeatureCollection", "features": features()})
            context, page = await open_app(browser, answer)
            await page.evaluate("() => openDash()")
            await page.wait_for_function(
                "() => document.querySelectorAll('#map .leaflet-interactive').length > 0", timeout=30_000)
            await page.wait_for_timeout(800)
            view = await page.evaluate(VIEW)
            if view["markers"] != 4:
                fails.append(f"{view['markers']} reports are drawn; four of the six are in India")
            if not view["note"].startswith("4 "):
                fails.append(f"the count under the map includes reports outside India: {view['note']!r}")

            # Zoomed all the way out, the map still shows India and not the world.
            await page.evaluate("() => mapObj.setZoom(1, { animate: false })")
            await page.wait_for_timeout(600)
            out = await page.evaluate(VIEW)
            if out["zoom"] < 3 or out["east"] - out["west"] > 60:
                fails.append(f"the map zooms out past India: zoom {out['zoom']}, "
                             f"{out['east'] - out['west']:.0f} degrees of longitude on screen")
            if not (INDIA["south"] <= out["lat"] <= INDIA["north"] and INDIA["west"] <= out["lng"] <= INDIA["east"]):
                fails.append(f"zoomed out, the map is centred outside India: {out['lat']:.1f}, {out['lng']:.1f}")
            # Whole India fits at the widest zoom: Kashmir to Kanyakumari, Kutch to Arunachal.
            if out["south"] > 8.1 or out["north"] < 35.0 or out["west"] > 68.5 or out["east"] < 97.0:
                fails.append("the widest view does not hold the whole of India: "
                             f"{out['south']:.1f} to {out['north']:.1f} N, {out['west']:.1f} to {out['east']:.1f} E")

            # Dragged towards Mauritius, it comes back.
            await page.evaluate("() => mapObj.panTo([-20.4, 57.6], { animate: false })")
            await page.wait_for_timeout(900)
            far = await page.evaluate(VIEW)
            if not (INDIA["south"] <= far["lat"] <= INDIA["north"] and INDIA["west"] <= far["lng"] <= INDIA["east"]):
                fails.append(f"the map can be taken to {far['lat']:.1f}, {far['lng']:.1f}, outside India")
            await context.close()
        finally:
            await browser.close()
    if fails:
        print("FAIL")
        for fail in fails:
            print("  -", fail)
        sys.exit(1)
    print("MAP INDIA ONLY TEST PASS")


asyncio.run(main())
