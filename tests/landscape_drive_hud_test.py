# -*- coding: utf-8 -*-
"""A phone in a landscape car mount still shows the Drive count, status and Stop.

Measured at 780x360 (a 360x780 phone turned sideways): the video kept its portrait
340 px minimum under a 78 px header, so the count and status overlay on its bottom
edge sat at 362 to 402 px of a 360 px screen. At 915x412 the status line was off
screen too. The driver cannot scroll while driving.

It also covers the side insets (pixels-a11y-7): edge to edge on Android 15 in
landscape, the camera cutout sits on the left or right, and only the top and bottom
insets were honoured, so the Pothole map and the photo viewer drew under it.
"""

import sys

from playwright.sync_api import sync_playwright

from flow_harness import error_failures, open_flow


PIXEL_GIF = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw=="

failures = []


def check(ok, message):
    if not ok:
        failures.append(message)


HUD = """() => {
  const box = (id) => {
    const r = document.getElementById(id).getBoundingClientRect();
    return { top: Math.round(r.top), bottom: Math.round(r.bottom) };
  };
  return { video: box('driveVideo'), count: box('driveCount'), status: box('driveStatus'),
           stop: box('driveStop'), height: innerHeight };
}"""


def left_edge(page, selector):
    return page.evaluate(
        f"() => Math.round(document.querySelector({selector!r}).getBoundingClientRect().left)")


with sync_playwright() as p:
    for width, height in ((780, 360), (915, 412)):
        browser, page, errors = open_flow(p, native=False)
        try:
            page.set_viewport_size({"width": width, "height": height})
            page.wait_for_function(
                "() => !document.getElementById('home').classList.contains('i18n-pending')")
            page.click("#driveBtn")
            page.wait_for_function(
                "() => !document.getElementById('drive').classList.contains('hidden')")
            page.wait_for_timeout(1500)
            hud = page.evaluate(HUD)
            for part in ("count", "status", "stop", "video"):
                check(hud[part]["bottom"] <= hud["height"],
                      f"pixels-a11y-1: at {width}x{height} #{part} ends at "
                      f"{hud[part]['bottom']} px of {hud['height']}: {hud}")
            failures.extend(error_failures(errors, f"landscape drive {width}x{height}"))
        finally:
            browser.close()

    # A portrait phone keeps its tall preview: the rule is for landscape only.
    browser, page, errors = open_flow(p, native=False)
    try:
        page.set_viewport_size({"width": 412, "height": 915})
        page.click("#driveBtn")
        page.wait_for_function(
            "() => !document.getElementById('drive').classList.contains('hidden')")
        hud = page.evaluate(HUD)
        check(hud["video"]["bottom"] - hud["video"]["top"] >= 340,
              f"pixels-a11y-1: the portrait Drive preview shrank: {hud}")
    finally:
        browser.close()

    # pixels-a11y-7: a 48 px cutout on the left of a landscape phone.
    browser, page, errors = open_flow(p, native=False)
    try:
        page.set_viewport_size({"width": 915, "height": 412})
        cdp = page.context.new_cdp_session(page)
        cdp.send("Emulation.setSafeAreaInsetsOverride",
                 {"insets": {"left": 48, "right": 0, "top": 0, "bottom": 0}})
        page.wait_for_function(
            "() => !document.getElementById('home').classList.contains('i18n-pending')")
        page.evaluate("document.body.classList.add('public-map-view'); show('dash')")
        check(left_edge(page, "#communityCard") >= 48,
              f"pixels-a11y-7: #communityCard starts at {left_edge(page, '#communityCard')} px, "
              "under a 48 px cutout")
        check(left_edge(page, "#map") >= 48,
              f"pixels-a11y-7: #map starts at {left_edge(page, '#map')} px, under a 48 px cutout")
        page.evaluate("document.body.classList.remove('public-map-view'); show('home')")
        page.evaluate(f"""() => openViewer({{id: 7, status: 'draft', assessment: 'damaged',
          damage_type: 'surface_breakup', size: 'medium', description: 'Broken road.',
          photo_url: {PIXEL_GIF!r}}})""")
        check(left_edge(page, "#viewerMeta") >= 48,
              f"pixels-a11y-7: #viewerMeta starts at {left_edge(page, '#viewerMeta')} px, "
              "under a 48 px cutout")
        cdp.send("Emulation.setSafeAreaInsetsOverride",
                 {"insets": {"left": 0, "right": 48, "top": 0, "bottom": 0}})
        close_right = page.evaluate(
            "() => Math.round(innerWidth - document.getElementById('viewerClose')"
            ".getBoundingClientRect().right)")
        check(close_right >= 48,
              f"pixels-a11y-7: #viewerClose is {close_right} px from the right edge, "
              "under a 48 px cutout")
        page.evaluate("closeViewer()")
        failures.extend(error_failures(errors, "safe-area insets"))
    finally:
        browser.close()

if failures:
    print("FAIL")
    for failure in failures:
        print(" -", failure)
    sys.exit(1)
print("PASS: landscape Drive keeps its HUD on screen and nothing sits under a side cutout")
