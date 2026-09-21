# -*- coding: utf-8 -*-
"""The Pothole map draws as soon as /v1/map answers, not after /v1/impact.

openDash awaited the map, the impact counts and the whole local history together, so
the pins waited for the slowest of five requests. /v1/impact is about seven times
slower than /v1/map in production (p50 151 ms against 22 ms, far more on 3G). Here the
map answers at once and the impact answer is held back 3 s: the markers must be on
screen while the community tiles are still waiting, and the tiles must fill in after.
"""

import asyncio
import sys

from map_dash_states_test import features, fulfill, open_app

IMPACT_DELAY_S = 3.0
IMPACT = {"period": {}, "active_installations": 2, "requests_total": 9,
          "potholes": {"total": 5}, "observations": {"total": 15}}


async def case(browser, fails, hash_):
    where = hash_ or "Home dash"

    async def map_now(route):
        await fulfill(route, 200, {"type": "FeatureCollection", "features": features(50, "Near")})

    context, page = await open_app(browser, map_now, hash_=hash_)

    async def late_impact(route):
        await asyncio.sleep(IMPACT_DELAY_S)
        await fulfill(route, 200, IMPACT)

    await context.route("**/v1/impact", late_impact)
    try:
        if hash_:
            # The shared link opens the map on load, before the held-back route existed.
            await page.reload()
            await page.wait_for_function("() => typeof openDash === 'function'")
        else:
            await page.evaluate("() => { openDash(); }")
        await page.wait_for_function(
            "() => !document.getElementById('dash').classList.contains('hidden')")
        await page.wait_for_timeout(int(IMPACT_DELAY_S * 1000 / 2))
        early = await page.evaluate("""() => ({
          markers: document.querySelectorAll('#map .leaflet-interactive').length
            + document.querySelectorAll('#map > svg circle').length,
          community: document.getElementById('communityStats').textContent.trim(),
        })""")
        if not early["markers"]:
            fails.append(f"{where}: no pins {IMPACT_DELAY_S / 2:.1f} s in, while only"
                         f" /v1/impact was still out: {early}")
        await page.wait_for_function(
            "() => !document.getElementById('dashRefresh').disabled", timeout=15_000)
        community = await page.evaluate(
            "() => document.getElementById('communityStats').textContent")
        if "15" not in community:
            fails.append(f"{where}: the late impact answer never filled the tiles: {community!r}")
    finally:
        await context.close()


async def main():
    fails = []
    from playwright.async_api import async_playwright
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            await case(browser, fails, "#public-map")
            await case(browser, fails, "")
        finally:
            await browser.close()
    return fails


failures = asyncio.run(main())
if failures:
    print("FAIL dash map before impact")
    for failure in failures:
        print(" -", failure)
    sys.exit(1)
print("PASS dash map before impact")
