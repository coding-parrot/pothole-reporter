# -*- coding: utf-8 -*-
"""The app stays responsive on a weak link: nothing the tester does waits on the network.

A tester wrote that the app is noticeably less responsive on poor connectivity. This
suite models a weak link the way a phone has one: the app's own assets are local and
fast, every remote call (service health, detection, resolver) pays a round-trip latency
and a bandwidth cost proportional to its bytes, and the link drops for a few seconds in
the middle of a drive and at the moment of a photo. It measures, per flow:

  home_cold_ms        cold start to an interactive Home
  drive_preview_ms    Drive tap to the camera preview playing
  drive_capture_ms    Drive tap to the first frame captured (not waiting on health)
  drive_hud_gap_ms    longest stretch the HUD showed nothing new during the drive
  drive_resume_ms     link back to the next frame checked (how long the drive stayed blind)
  drive_lost_frames   frames captured and never checked
  drive_stop_ms       Stop tap to Home (the frames still waiting are checked first)
  photo_saved_ms      shutter to the report existing on the phone (link slow)
  photo_detail_ms     shutter to the saved report's own screen (handed back)
  photo_checked_ms    shutter to the verdict, which is the link's speed, not the app's
  offline_saved_ms    shutter to the report existing on the phone (link down)
  offline_detail_ms   shutter to the saved report's own screen (link down)
  offline_checked_ms  link back to the queued photo checked

Env: POTHOLE_TEST_APP; POTHOLE_NET_LATENCY_MS (800), POTHOLE_NET_KBPS (150),
POTHOLE_NET_HEALTH_MS (5000: a cold service probe on a weak link). --report prints only.
"""
import asyncio
import hashlib
import json
import os
import sys
import time

from playwright.async_api import async_playwright

import flow_harness

APP = flow_harness.APP
SERVICE = flow_harness.SERVICE
LATENCY_MS = int(os.environ.get("POTHOLE_NET_LATENCY_MS", "800"))
KBPS = int(os.environ.get("POTHOLE_NET_KBPS", "150"))
HEALTH_MS = int(os.environ.get("POTHOLE_NET_HEALTH_MS", "5000"))
FLOWS = set(os.environ.get("POTHOLE_NET_FLOWS", "drive,photo,offline").split(","))
DRIVE_S = 30
OUTAGE = (6, 12)  # seconds into the drive during which the service is unreachable

BUDGET = {
    "home_cold_ms": 3000,
    "drive_preview_ms": 3000,
    "drive_capture_ms": 4000,
    "drive_hud_gap_ms": 3000,
    "drive_resume_ms": 9000,
    "photo_saved_ms": 3000,
    "photo_detail_ms": 12000,
    "offline_saved_ms": 3000,
    "offline_detail_ms": 3000,
}
# The retry begins as soon as the link is back; how long the check then takes is the
# link's own speed, which the slow-link photo already measured.
RESUME_START_BUDGET_MS = 10000

PHOTO = r"""
async () => {
  if (window.__bigPhoto) return true;
  const canvas = document.createElement("canvas");
  canvas.width = 800; canvas.height = 600;
  const g = canvas.getContext("2d");
  const image = g.createImageData(800, 600);
  const px = image.data;
  let seed = 11;
  for (let i = 0; i < px.length; i += 4) {
    seed = (seed * 1103515245 + 12345) & 0x7fffffff;
    px[i] = 90 + (seed & 63); px[i + 1] = 90 + ((seed >> 8) & 63); px[i + 2] = 80 + ((seed >> 16) & 63);
    px[i + 3] = 255;
  }
  g.putImageData(image, 0, 0);
  window.__bigPhoto = await new Promise((r) => canvas.toBlob(r, "image/jpeg", 0.85));
  return window.__bigPhoto.size;
}
"""

SHUTTER = r"""
() => {
  const file = new File([window.__bigPhoto], "shutter.jpg", { type: "image/jpeg", lastModified: Date.now() });
  window.__shutterAt = performance.now();
  window.__shutterWall = Date.now();
  void handleFile(file, { captureSource: "manual_camera", capturedAtMs: Date.now(),
    position: { lat: 12.9916, lng: 77.6146, accuracy: 4, speed: null, heading: null,
                observed_at_ms: Date.now() } });
  return true;
}
"""

# The row this shutter made, by capture time: a drive's late frame writes or a routing
# rewrite must not count as "saved". Newest first, so index 0 is the fresh manual row.
SHUTTER_ROW = """async () => {
  const rows = (await StandaloneAPI.handle('/api/history')).reports;
  return rows.find((r) => String(r.capture_source || "").startsWith("manual")
    && r.created_at * 1000 >= window.__shutterWall - 100) || null;
}"""


class Link:
    """A weak link: latency per request, bytes at a fixed rate, and a switch to cut it."""

    def __init__(self):
        self.offline = False
        self.calls = []

    async def wait(self, request, body_bytes):
        up = len(request.post_data or "") if request.method == "POST" else 0
        latency = HEALTH_MS if request.url.endswith("/v1/health") else LATENCY_MS
        seconds = latency / 1000 + (up + body_bytes) * 8 / (KBPS * 1000)
        self.calls.append((request.url.split(SERVICE, 1)[-1], request.method, round(seconds, 2)))
        await asyncio.sleep(seconds)

    async def handle(self, route, request):
        path = request.url.split(SERVICE, 1)[-1].split("?", 1)[0]
        if self.offline:
            await route.abort("internetdisconnected")
            return
        body = json.loads(request.post_data or "{}") if request.method == "POST" else {}
        status = 200
        if path == "/v1/health":
            payload = {"ok": True, "shared_vision_configured": True}
        elif path == "/v1/installations":
            payload, status = {"install_id": "low-connectivity-install"}, 201
        elif path == "/v1/activity":
            payload, status = {"accepted": True, "event": "vision_check"}, 202
        elif path == "/v1/vision/detect":
            payload = {
                **flow_harness.ACCEPTED,
                "detection_receipt": hashlib.sha256(
                    f"receipt:{body.get('client_observation_id')}".encode("utf-8")).hexdigest(),
                "detector": {"provider": "shared_server", "model": body.get("model", "gpt-5-mini"),
                             "prompt_version": "road-damage-v5", "schema_version": 4,
                             "evidence_count": len(body.get("images", []))},
            }
        elif path == "/v1/tenders/resolve":
            payload = {"jurisdiction": {"lat": body.get("lat"), "lng": body.get("lng"),
                                        "address": None, "lgd": "999001",
                                        "town": "Test City Corporation", "source": "kgis",
                                        "address_source": "unresolved",
                                        "road_ownership": "municipal"},
                       "tender": None, "reason": "no_tenders_for_jurisdiction"}
        elif path == "/v1/potholes/report":
            payload, status = {"duplicate": False, "dedupe": None, "pothole": {
                "id": 4242, "lat": body.get("lat"), "lng": body.get("lng"),
                "damage_type": body.get("damage_type"), "size": body.get("size"),
                "first_seen_at": body.get("observed_at"), "last_seen_at": body.get("observed_at"),
                "seen_count": 1, "lgd": "999001", "town": "Test City Corporation"}}, 201
        elif path == "/v1/map":
            payload = {"type": "FeatureCollection", "total": 0, "features": []}
        elif path == "/v1/impact":
            payload = {"period": {}, "active_installations": 0, "requests_total": 0,
                       "requests": [], "potholes": {"total": 0},
                       "observations": {"total": 0, "distinct_observers": 0}}
        else:
            payload, status = {"error": "not_mocked", "message": path}, 404
        text = json.dumps({"request_id": "req-low-connectivity", **payload})
        await self.wait(request, len(text))
        if self.offline:
            await route.abort("internetdisconnected")
            return
        await route.fulfill(status=status, headers={"content-type": "application/json",
                                                    "x-request-id": "req-low-connectivity"},
                            body=text)


async def support(route, request):
    url = request.url
    if url.endswith("/karnataka-bodies.json"):
        await route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "bodies": {"999001": {"name": "Test City Corporation", "type": "CC",
                                  "officer": "Commissioner", "email": flow_harness.RECIPIENT}}}))
    else:
        await route.abort("blockedbyclient")


async def wait_for(page, expression, timeout_ms, arg=None):
    await page.wait_for_function(expression, arg=arg, timeout=timeout_ms, polling=50)


# wait_for_function takes a string it cannot parse as a function for an expression, and
# an async arrow is one such string: the function object is truthy, so it "passes" at
# once. Anything that reads the store is polled from here.
async def poll(page, js, timeout_ms, every=0.1):
    deadline = time.monotonic() + timeout_ms / 1000
    while True:
        value = await page.evaluate(js)
        if value:
            return value
        if time.monotonic() > deadline:
            raise TimeoutError(f"timed out waiting for {js[:80]}")
        await asyncio.sleep(every)


CHECKED_ROW = """async () => {
  const row = await (%s)();
  return row && ["draft", "queued", "unrouted", "rejected", "review"].includes(row.status) ? row : null;
}"""


async def main():
    report_only = "--report" in sys.argv
    numbers = {}
    notes = []
    link = Link()
    dialogs = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(args=[
            "--disable-web-security", "--use-fake-device-for-media-stream",
            "--use-fake-ui-for-media-stream"])
        context = await browser.new_context(
            viewport={"width": 390, "height": 844}, locale="en-IN",
            geolocation={"latitude": 12.9716, "longitude": 77.5946},
            permissions=["camera", "geolocation"])
        await context.add_init_script(script="(() => {" + "\n".join([
            f'localStorage.setItem("service_url", {json.dumps(SERVICE)});',
            f'localStorage.setItem("data_notice_version", {json.dumps(flow_harness.DATA_NOTICE_VERSION)});',
            'localStorage.setItem("initial_setup_complete", "1");',
            'localStorage.setItem("vision_provider", "shared");',
            'localStorage.setItem("sender_name", "Test Citizen");',
            'localStorage.setItem("app_lang", "en");',
        ]) + "})();")
        async def catch_all(route, request):
            if request.url.startswith(APP) or request.url.startswith("blob:") \
                    or request.url.startswith("data:"):
                await route.continue_()
            else:
                await support(route, request)
        # The last route registered is consulted first: the service handler must win.
        await context.route("**/*", catch_all)
        await context.route(f"{SERVICE}/**", link.handle)
        page = await context.new_page()
        page.on("dialog", lambda dialog: (dialogs.append(dialog.message),
                                          asyncio.ensure_future(dialog.dismiss())))
        try:
            # 1. Cold start with the service slow to answer.
            t0 = time.monotonic()
            await page.goto(APP)
            await wait_for(page, """() => {
              const home = document.getElementById("home");
              const list = document.getElementById("list");
              const photo = document.getElementById("captureBtn");
              return home && !home.classList.contains("hidden") && list && list.innerHTML.trim()
                && photo && !photo.disabled;
            }""", 30_000)
            numbers["home_cold_ms"] = round((time.monotonic() - t0) * 1000)
            await page.evaluate(PHOTO)

            if "drive" in FLOWS:
                # 2. Drive: preview, first capture, HUD cadence through an outage.
                t0 = time.monotonic()
                await page.click("#driveBtn")
                await wait_for(page, """() => {
                  const v = document.getElementById("driveVideo");
                  return v && v.videoWidth > 0 && !v.paused;
                }""", 20_000)
                numbers["drive_preview_ms"] = round((time.monotonic() - t0) * 1000)
                await wait_for(page, "() => drive && drive.tally && drive.tally.captured >= 1", 30_000)
                numbers["drive_capture_ms"] = round((time.monotonic() - t0) * 1000)
                samples = []
                hud_changes = []
                last_text = None
                drive_t0 = time.monotonic()
                while time.monotonic() - drive_t0 < DRIVE_S:
                    elapsed = time.monotonic() - drive_t0
                    # The service is unreachable; the phone still believes it is online
                    # and still has GPS, as in an underpass or a congested cell.
                    link.offline = OUTAGE[0] <= elapsed < OUTAGE[1]
                    # A car at about 11 m/s. A fixed emulated position is delivered once,
                    # and the drive would then report GPS lost, which is not the subject.
                    await context.set_geolocation({"latitude": 12.9716 + elapsed * 0.0001,
                                                   "longitude": 77.5946, "accuracy": 5})
                    # Everything the HUD says: the count, the status line and the tally
                    # of states ("2 sent, 5 checking"), where there is one.
                    text, tally, paused = await page.evaluate("""() => [
                      ["driveCount", "driveStatus", "driveStates"].map((id) => {
                        const el = document.getElementById(id);
                        return el && !el.classList.contains("hidden") ? el.textContent : "";
                      }).join(" | "),
                      drive ? { ...drive.tally, inFlight: drive.inFlight, queued: drive.queue.length } : null,
                      !!(drive && drive.paused)]""")
                    if text != last_text:
                        hud_changes.append(elapsed)
                        last_text = text
                    samples.append((round(elapsed, 2), tally, paused, text))
                    await asyncio.sleep(0.25)
                link.offline = False
                gaps = [b - a for a, b in zip(hud_changes, hud_changes[1:])]
                gaps.append(DRIVE_S - hud_changes[-1] if hud_changes else DRIVE_S)
                numbers["drive_hud_gap_ms"] = round(max(gaps) * 1000)
                numbers["drive_hud_timeline"] = [(round(s[0], 1), s[3]) for i, s in enumerate(samples)
                                                 if i == 0 or s[3] != samples[i - 1][3]]
                final = samples[-1][1] or {}
                numbers["drive_tally"] = final
                numbers["drive_paused"] = any(s[2] for s in samples)
                outage = [s for s in samples if OUTAGE[0] <= s[0] < OUTAGE[1]]
                numbers["drive_captured_during_outage"] = (
                    outage[-1][1]["captured"] - outage[0][1]["captured"]) if len(outage) > 1 else 0
                # Link back to the next frame checked: how long the drive stayed blind.
                after = [s for s in samples if s[0] >= OUTAGE[1]]
                resumed = next((s for s in after if s[1]["checked"] > after[0][1]["checked"]), None) if after else None
                numbers["drive_resume_ms"] = round((resumed[0] - OUTAGE[1]) * 1000) if resumed else None
                # Frames captured and never checked: road nobody looked at.
                numbers["drive_lost_frames"] = (final.get("failed", 0) or 0) + (final.get("dropped", 0) or 0)
                stop_t0 = time.monotonic()
                await page.click("#driveStop")
                await wait_for(page, """() => !drive && !document.getElementById("home").classList.contains("hidden")""", 120_000)
                numbers["drive_stop_ms"] = round((time.monotonic() - stop_t0) * 1000)

            # 3. Photo on the slow link: saved on the phone before the detector answers.
            await asyncio.sleep(2)
            await page.evaluate(SHUTTER)
            t0 = time.monotonic()
            saved = await poll(page, SHUTTER_ROW, 120_000)
            numbers["photo_saved_ms"] = round((time.monotonic() - t0) * 1000)
            numbers["photo_status_when_saved"] = saved.get("status")
            numbers["photo_progress_text"] = await page.evaluate(
                "() => document.getElementById('progressText').textContent")
            await wait_for(page, """() => !document.getElementById("detail").classList.contains("hidden")""", 120_000)
            numbers["photo_detail_ms"] = round((time.monotonic() - t0) * 1000)
            numbers["photo_status_at_detail"] = ((await page.evaluate(SHUTTER_ROW)) or {}).get("status")
            numbers["photo_detail_chip"] = await page.evaluate(
                "() => (document.querySelector('#detail .chip') || {}).textContent || ''")
            await poll(page, CHECKED_ROW % SHUTTER_ROW, 120_000)
            numbers["photo_checked_ms"] = round((time.monotonic() - t0) * 1000)
            # The verdict replaces the queued screen the tester is still looking at.
            try:
                await wait_for(page, """() => !/Waiting to be checked/.test(
                  (document.querySelector('#detail .chip') || {}).textContent || 'Waiting to be checked')""", 15_000)
                numbers["photo_detail_updated"] = True
            except Exception:
                numbers["photo_detail_updated"] = False
            await page.click("#backBtn")
            await wait_for(page, """() => !document.getElementById("home").classList.contains("hidden")""", 20_000)

            # 4. Photo with the link down: saved, visibly queued, checked once it is back.
            link.offline = True
            await context.set_offline(True)
            await asyncio.sleep(1)
            dialogs.clear()
            await page.evaluate(SHUTTER)
            t0 = time.monotonic()
            try:
                saved = await poll(page, SHUTTER_ROW, 15_000)
                numbers["offline_saved_ms"] = round((time.monotonic() - t0) * 1000)
                numbers["offline_status"] = saved.get("status")
                await wait_for(page, """() => !document.getElementById("detail").classList.contains("hidden")""", 15_000)
                numbers["offline_detail_ms"] = round((time.monotonic() - t0) * 1000)
                numbers["offline_screen"] = await page.evaluate("""() => {
                  for (const id of ["detail", "progress", "home"]) {
                    if (!document.getElementById(id).classList.contains("hidden")) return id;
                  }
                  return null;
                }""")
                numbers["offline_detail_text"] = " ".join((await page.evaluate(
                    "() => document.getElementById('detail').innerText")).split())[:240]
            except Exception:
                numbers["offline_saved_ms"] = None
                numbers["offline_dialogs"] = list(dialogs)
            await asyncio.sleep(1)
            link.offline = False
            await context.set_offline(False)
            t1 = time.monotonic()
            if numbers.get("offline_saved_ms") is not None:
                try:
                    await poll(page, CHECKED_ROW % SHUTTER_ROW, 60_000)
                    numbers["offline_checked_ms"] = round((time.monotonic() - t1) * 1000)
                except Exception:
                    numbers["offline_checked_ms"] = None
            numbers["dialogs"] = list(dialogs)
        finally:
            await browser.close()

    print("  link: latency %d ms, %d kbps, health %d ms" % (LATENCY_MS, KBPS, HEALTH_MS))
    print("  remote calls:", link.calls[:12], "..." if len(link.calls) > 12 else "")
    for key, value in numbers.items():
        print(f"  {key}: {value}")
    if report_only:
        return
    failures = []
    for key, budget in BUDGET.items():
        value = numbers.get(key)
        if value is None:
            failures.append(f"{key}: no value (the flow never completed)")
        elif value > budget:
            failures.append(f"{key}: {value} ms over the {budget} ms budget")
    if numbers.get("drive_captured_during_outage", 0) < 1:
        failures.append("the drive stopped capturing while the link was down")
    if numbers.get("drive_paused"):
        failures.append("an outage of a few seconds paused the drive for the service")
    if numbers.get("drive_lost_frames", 0) > 2:
        failures.append(f"{numbers['drive_lost_frames']} frames captured around the outage were never checked")
    if numbers.get("photo_status_when_saved") != "pending":
        failures.append("the photo was not saved before its check: "
                        f"{numbers.get('photo_status_when_saved')}")
    if "Saved on this phone" not in (numbers.get("photo_progress_text") or ""):
        failures.append(f"the progress screen did not say the photo is saved: {numbers.get('photo_progress_text')!r}")
    if numbers.get("photo_detail_chip") != "Waiting to be checked":
        failures.append(f"the handed-back screen did not show the queued state: {numbers.get('photo_detail_chip')!r}")
    if not numbers.get("photo_detail_updated"):
        failures.append("the verdict did not replace the queued screen the tester was on")
    if numbers.get("offline_status") != "pending":
        failures.append(f"the offline photo was not queued: {numbers.get('offline_status')}")
    if "Saved on this phone" not in (numbers.get("offline_detail_text") or ""):
        failures.append(f"the offline photo's screen does not say it is saved and queued: {numbers.get('offline_detail_text')!r}")
    if numbers.get("dialogs"):
        failures.append(f"the offline photo raised an alert: {numbers['dialogs']}")
    checked, baseline = numbers.get("offline_checked_ms"), numbers.get("photo_checked_ms")
    if checked is None:
        failures.append("the queued photo was never checked after the link came back")
    elif baseline is not None and checked > baseline + RESUME_START_BUDGET_MS:
        failures.append(f"the queued photo took {checked} ms after the link came back; the same "
                        f"check on this link takes {baseline} ms")
    if numbers.get("offline_screen") != "detail":
        failures.append(f"the offline photo did not open its detail screen: {numbers.get('offline_screen')}")
    if failures:
        print("FAIL")
        for failure in failures:
            print("  -", failure)
        sys.exit(1)
    print("LOW CONNECTIVITY TEST PASS")


if __name__ == "__main__":
    asyncio.run(main())
