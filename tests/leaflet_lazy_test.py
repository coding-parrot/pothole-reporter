# -*- coding: utf-8 -*-
"""Leaflet loads with the Pothole map, not with every boot.

index.html loaded leaflet.css and a parser-blocking leaflet.js (43 KB gzipped, plus
15 KB of CSS) ahead of standalone.js, although only the map screen uses it. On the
public web build over 3G that was about a quarter second before Home. Home must now
boot without either file, and the map must still draw real Leaflet markers, styled.
"""

import asyncio
import sys

from map_dash_states_test import features, fulfill, open_app


async def main():
    fails = []
    from playwright.async_api import async_playwright
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            async def map_now(route):
                await fulfill(route, 200, {"type": "FeatureCollection",
                                           "features": features(12, "Lazy")})

            context, page = await open_app(browser, map_now)
            try:
                seen = []
                page.on("request", lambda request: seen.append(request.url))
                await page.reload()
                await page.wait_for_function("() => typeof openDash === 'function'")
                await page.locator("#home").wait_for(state="visible", timeout=30_000)
                await page.wait_for_timeout(500)
                booted = [url for url in seen if "leaflet" in url]
                if booted:
                    fails.append(f"Home booted with Leaflet: {booted}")
                if await page.evaluate("() => typeof L !== 'undefined'"):
                    fails.append("window.L exists before the map was opened")

                await page.evaluate("() => { openDash(); }")
                await page.wait_for_function(
                    "() => document.querySelectorAll('#map .leaflet-interactive').length === 12",
                    timeout=20_000)
                styled = await page.evaluate("""() => {
                  const box = document.querySelector('#map .leaflet-container');
                  return !!box && getComputedStyle(box).overflow === 'hidden';
                }""")
                if not styled:
                    fails.append("the map drew without leaflet.css")
                loads = [url for url in seen if url.endswith("leaflet.js")]
                await page.evaluate("() => { show('home'); openDash(); }")
                await page.wait_for_function(
                    "() => document.querySelectorAll('#map .leaflet-interactive').length === 12",
                    timeout=20_000)
                if len([url for url in seen if url.endswith("leaflet.js")]) != len(loads):
                    fails.append("reopening the map loaded Leaflet again")
            finally:
                await context.close()
        finally:
            await browser.close()
    return fails


failures = asyncio.run(main())
if failures:
    print("FAIL leaflet lazy")
    for failure in failures:
        print(" -", failure)
    sys.exit(1)
print("PASS leaflet lazy")
