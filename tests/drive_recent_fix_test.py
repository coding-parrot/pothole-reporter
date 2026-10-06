# -*- coding: utf-8 -*-
"""A drive keeps checking the road for 15 s after its last GPS fix, and every frame
says how old the fix it was placed with is.

Under a flyover or between tall buildings the phone's fix pauses for a few seconds. The
loop used to stop capturing 10 s after the last fix and sent no fix age at all, so the
service could not tell a frame placed on a live fix from one placed on a fix that was
nine seconds stale. Now a frame is captured while the fix is at most 15 s old, and
location_age_ms travels with it; past 15 s the HUD says the signal is lost and nothing
is captured until a fresh fix lands.
"""

import sys

from playwright.sync_api import sync_playwright

from web_drive_harness import open_web_drive

# A car at 30 km/h whose fix stops arriving 2.5 s into the drive. The one-shot probe
# the loop uses to recover a silent watch gets nothing either.
BRIEF_GPS = """
(() => {
  const start = Date.now(), lat0 = 12.9716, lng0 = 77.5946, v = 8.33;
  window.__lastFixAt = null;
  const pos = () => { const t = (Date.now() - start) / 1000;
    window.__lastFixAt = Date.now();
    return { coords: { latitude: lat0 + v * t / 111320, longitude: lng0, accuracy: 5,
      speed: v, heading: 0, altitude: null, altitudeAccuracy: null }, timestamp: Date.now() }; };
  const proto = Object.getPrototypeOf(navigator.geolocation);
  proto.watchPosition = function (ok) {
    ok(pos());
    const timer = setInterval(() => {
      if (Date.now() - start > 2500) { clearInterval(timer); return; }
      ok(pos());
    }, 500);
    return timer;
  };
  proto.clearWatch = function (id) { clearInterval(id); };
  proto.getCurrentPosition = function (ok) { if (Date.now() - start <= 2500) setTimeout(() => ok(pos()), 5); };
})();
"""

RECORD = """() => {
  window.__frames = [];
  const stubbed = window.api;
  window.api = async (path, opts) => {
    if (path === "/api/frame") {
      const body = opts.body;
      window.__frames.push({
        captured_at_ms: Number(body.get("captured_at_ms")),
        location_age_ms: body.has("location_age_ms") ? Number(body.get("location_age_ms")) : null,
        lat: Number(body.get("lat")),
      });
    }
    return stubbed(path, opts);
  };
}"""

fails = []
with sync_playwright() as playwright:
    browser, page, dialogs, errors = open_web_drive(playwright)
    try:
        page.context.add_init_script(script=BRIEF_GPS)
        page.reload()
        page.wait_for_function("() => !!window.StandaloneAPI && window.api.__stubbed", timeout=30_000)
        page.locator("#home").wait_for(state="visible", timeout=30_000)
        page.evaluate(RECORD)
        page.locator("#driveBtn").click()
        page.locator("#driveStop").wait_for(state="visible", timeout=30_000)
        page.wait_for_timeout(20_000)
        hud = page.evaluate("document.getElementById('driveStatus').textContent")
        gps_lost = page.evaluate("t('gps_lost')")
        frames, last_fix_at = page.evaluate("[window.__frames, window.__lastFixAt]")
        page.locator("#driveStop").click()
        page.locator("#home").wait_for(state="visible", timeout=60_000)

        if not frames:
            fails.append("no frame was captured at all")
        ages = [frame["captured_at_ms"] - last_fix_at for frame in frames]
        if frames and max(ages) < 10_000:
            fails.append(f"capture stopped {max(ages) / 1000:.1f} s after the last fix; "
                         "a fix is usable for 15 s")
        if frames and max(ages) > 15_600:
            fails.append(f"a frame was captured {max(ages) / 1000:.1f} s after the last fix")
        missing = [frame for frame in frames if frame["location_age_ms"] is None]
        if missing:
            fails.append(f"{len(missing)} of {len(frames)} frames carry no location_age_ms")
        wrong = [(frame, age) for frame, age in zip(frames, ages)
                 if frame["location_age_ms"] is not None
                 and abs(frame["location_age_ms"] - max(0, age)) > 700]
        if wrong:
            fails.append(f"location_age_ms disagrees with the fix time: {wrong[:3]}")
        if hud != gps_lost:
            fails.append(f"after 15 s without a fix the HUD reads {hud!r}, want {gps_lost!r}")
        if errors:
            fails.append(f"page errors {errors[:3]}")
    finally:
        browser.close()

if fails:
    print("FAIL drive recent fix")
    for failure in fails:
        print(" -", failure)
    sys.exit(1)
print("PASS drive recent fix")
