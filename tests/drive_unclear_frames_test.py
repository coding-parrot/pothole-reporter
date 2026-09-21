# -*- coding: utf-8 -*-
"""A drive whose frames the model cannot judge never ends with "No road damage found".

At night or in heavy rain the detector answers image_quality "rejected" for most
frames. Those frames were counted as checked and the drive ended with "No road damage
found in this drive (20 events checked).", a clean bill for road nobody could see.
Here every detection is unjudgeable; the summary must say so and must not claim a
clear road.
"""

import json
import sys
import time
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

import flow_harness as fh

UNCLEAR = {"image_quality": "rejected", "assessment": "undamaged", "damage_type": None,
           "size": None, "description": "Too dark to judge the road surface.",
           "detector": {"provider": "shared_server", "model": "gpt-5-mini",
                        "prompt_version": "road-damage-v5", "schema_version": 4,
                        "evidence_count": 1}}

# A car at 30 km/h so the frame gate keeps sampling.
GEO = """
(() => {
  const start = Date.now(), lat0 = 12.9716, lng0 = 77.5946, v = 8.33;
  const pos = () => { const t = (Date.now() - start) / 1000;
    return { coords: { latitude: lat0 + v * t / 111320, longitude: lng0, accuracy: 5,
      speed: v, heading: 0, altitude: null, altitudeAccuracy: null }, timestamp: Date.now() }; };
  const proto = Object.getPrototypeOf(navigator.geolocation);
  proto.watchPosition = function (ok) { ok(pos()); return setInterval(() => ok(pos()), 500); };
  proto.clearWatch = function (id) { clearInterval(id); };
  proto.getCurrentPosition = function (ok) { setTimeout(() => ok(pos()), 5); };
})();
"""

detections = []


def service(route, request):
    if urlparse(request.url).path != "/v1/vision/detect":
        return fh.central_service(route, request)
    detections.append(time.time())
    fh.envelope(route, UNCLEAR)


fails = []
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(args=[
        "--disable-web-security", "--use-fake-device-for-media-stream",
        "--use-fake-ui-for-media-stream"])
    try:
        context = browser.new_context(
            viewport={"width": 412, "height": 915}, is_mobile=True, device_scale_factor=2.625,
            has_touch=True, geolocation={"latitude": 12.9716, "longitude": 77.5946},
            permissions=["camera", "geolocation"], locale="en-IN")
        values = {"service_url": fh.SERVICE, "data_notice_version": fh.DATA_NOTICE_VERSION,
                  "initial_setup_complete": "1", "vision_provider": "shared",
                  "sender_name": "Night Driver"}
        context.add_init_script(script="(() => {" + "".join(
            f"localStorage.setItem({json.dumps(k)}, {json.dumps(v)});"
            for k, v in values.items()) + "})();" + GEO)
        context.route(f"{fh.SERVICE}/**", service)
        context.route("**/karnataka-bodies.json", fh.support_services)
        context.route("https://nominatim.openstreetmap.org/**", fh.support_services)
        context.route("https://kgis.ksrsac.in/**", fh.support_services)
        page = context.new_page()
        dialogs = []
        page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.accept()))
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(fh.APP)
        page.wait_for_function("() => !!window.StandaloneAPI", timeout=30_000)
        page.locator("#home").wait_for(state="visible", timeout=30_000)
        page.locator("#driveBtn").click()
        start = time.time()
        while len(detections) < 4 and time.time() - start < 40:
            page.wait_for_timeout(500)
        hud = page.locator("#drive").inner_text()
        page.locator("#driveStop").click()
        start = time.time()
        while not dialogs and time.time() - start < 30:
            page.wait_for_timeout(250)
        if len(detections) < 4:
            fails.append(f"the drive sent only {len(detections)} frames")
        zero = page.evaluate("t('drive_end_zero', {checked: 'N', debug: ''})").split("(")[0].strip()
        summary = dialogs[0] if dialogs else ""
        if not summary:
            fails.append("no end-of-drive summary")
        elif zero in summary:
            fails.append(f"an all-unclear drive still reports a clear road: {summary!r}")
        elif "unclear" not in summary and "too dark" not in summary:
            fails.append(f"the summary does not say the frames were unclear: {summary!r}")
        if "unclear" not in hud:
            fails.append(f"the HUD does not count unclear frames: {hud!r}")
        if errors:
            fails.append(f"page errors {errors[:3]}")
    finally:
        browser.close()

if fails:
    print("FAIL drive unclear frames")
    for failure in fails:
        print(" -", failure)
    sys.exit(1)
print("PASS drive unclear frames")
