# -*- coding: utf-8 -*-
"""A photo taken while the live GPS fix is late is placed with the phone's last fix, or
waits with a Retry; it is never filed without a location.

Production, last 30 days: 32 of 79 manual photo checks went out with location_source
"none", so nothing could route them, and two 1-star reviews say the app "does not
detect location properly". The old flow sampled GPS only while the camera was open,
accepted a fix only within 5 s of the shutter, tried one more 10 s request, and then
quietly filed the photo with no coordinates.

The rule now: at the shutter, a fix at most 15 s old counts, and the request says how
old it was (location_age_ms). Older than that, the flow keeps acquiring and shows the
tester it is waiting, with Retry and Cancel; the first fix that lands continues the
check. The phone here is the Capacitor stub: its camera returns a frame at once and its
Geolocation plugin is scripted per case.
"""

import json
import sys

from playwright.sync_api import sync_playwright

import flow_harness as fh
from central_stub_harness import Central

PHONE = r"""
(scenario) => {
  const canvas = document.createElement("canvas");
  canvas.width = 320; canvas.height = 240;
  const g = canvas.getContext("2d");
  g.fillStyle = "#777"; g.fillRect(0, 0, 320, 240);
  g.fillStyle = "#111"; g.fillRect(100, 100, 120, 80);
  const dataUrl = canvas.toDataURL("image/jpeg", .85);
  const fix = (ageMs) => ({
    coords: { latitude: 12.9716, longitude: 77.5946, accuracy: 12, speed: 0, heading: null },
    timestamp: Date.now() - ageMs,
  });
  window.__geo = { watchers: [], cleared: [], oneShots: 0, oneShotFix: null,
                   watchFixAgeMs: scenario.watchFixAgeMs };
  const Geolocation = {
    async checkPermissions() { return { location: "granted", coarseLocation: "granted" }; },
    async requestPermissions() { return { location: "granted", coarseLocation: "granted" }; },
    async getCurrentPosition() {
      __geo.oneShots += 1;
      if (__geo.oneShotFix) return __geo.oneShotFix();
      throw new Error("location unavailable");
    },
    async watchPosition(options, callback) {
      const id = `watch-${__geo.watchers.length + 1}`;
      __geo.watchers.push({ id, callback });
      // The one fix the phone has is delivered to every new watch straight away.
      if (__geo.watchFixAgeMs != null) {
        const age = __geo.watchFixAgeMs;
        setTimeout(() => callback(fix(age)), 0);
      }
      return id;
    },
    async clearWatch({ id }) { __geo.cleared.push(id); },
  };
  const Camera = {
    async checkPermissions() { return { camera: "granted", photos: "granted" }; },
    async requestPermissions() { return { camera: "granted", photos: "granted" }; },
    async getPhoto() {
      await new Promise((resolve) => setTimeout(resolve, 40));
      return { dataUrl, format: "jpeg" };
    },
  };
  Object.assign(window.Capacitor.Plugins, { Geolocation, Camera });
  window.__deliverFix = (ageMs) => {
    for (const watcher of __geo.watchers) {
      if (!__geo.cleared.includes(watcher.id)) watcher.callback(fix(ageMs));
    }
  };
}
"""

STATE = """() => ({
  screen: ["home", "progress", "detail"].find((id) => !document.getElementById(id).classList.contains("hidden")) || null,
  text: document.getElementById("progressText").textContent,
  retryVisible: !!document.getElementById("progressRetry")
    && !document.getElementById("progressRetry").classList.contains("hidden"),
  cancelVisible: !document.getElementById("progressCancel").classList.contains("hidden"),
  busy: manualAnalysisActive,
})"""


def detect_bodies(central):
    return [json.loads(call["body"]) for call in central.calls if call["path"] == "/v1/vision/detect"]


def report_bodies(central):
    return [json.loads(call["body"]) for call in central.calls if call["path"] == "/v1/potholes/report"]


def open_phone(playwright, watch_fix_age_ms):
    central = Central()
    browser, page, errors = fh.open_flow(playwright, native=True)
    page.route(f"{fh.SERVICE}/**", central.handle)
    page.locator("#home").wait_for(state="visible", timeout=30_000)
    page.evaluate(PHONE, {"watchFixAgeMs": watch_fix_age_ms})
    return central, browser, page, errors


def wait_until(page, predicate, timeout_ms):
    waited = 0
    while waited < timeout_ms:
        if predicate():
            return True
        page.wait_for_timeout(100)
        waited += 100
    return predicate()


fails = []
with sync_playwright() as playwright:
    # 1. The only fix is 8 s older than the shutter: used, and its age is sent.
    central, browser, page, errors = open_phone(playwright, watch_fix_age_ms=8000)
    try:
        page.locator("#captureBtn").click()
        if not wait_until(page, lambda: page.evaluate(STATE)["screen"] == "detail", 25_000):
            fails.append(f"recent fix: the check did not finish: {page.evaluate(STATE)}")
        detect = detect_bodies(central)
        if len(detect) != 1:
            fails.append(f"recent fix: expected one detect call, saw {len(detect)}")
        else:
            body = detect[0]
            age = body.get("location_age_ms")
            if body.get("lat") != 12.9716 or body.get("location_source") != "device_gps":
                fails.append(f"recent fix: the photo went out without its location: {body}")
            if not isinstance(age, (int, float)) or not 7500 <= age <= 12000:
                fails.append(f"recent fix: location_age_ms should be about 8000, got {age!r}")
        reports = report_bodies(central)
        if len(reports) != 1 or not 7500 <= (reports[0].get("location_age_ms") or -1) <= 12000:
            fails.append(f"recent fix: the shared-map write did not carry the fix age: {reports}")
        fails += fh.error_failures(errors, "recent fix")
    finally:
        browser.close()

    # 2. The only fix is 20 s old: the flow waits, says so, and continues on the first
    #    fix the watch delivers.
    central, browser, page, errors = open_phone(playwright, watch_fix_age_ms=20000)
    try:
        page.locator("#captureBtn").click()
        if not wait_until(page, lambda: page.evaluate(STATE)["retryVisible"], 25_000):
            fails.append(f"stale fix: no Retry was offered: {page.evaluate(STATE)}")
        state = page.evaluate(STATE)
        if state["screen"] != "progress" or not state["cancelVisible"]:
            fails.append(f"stale fix: the wait is not on the progress screen with Cancel: {state}")
        if state["text"] != page.evaluate("t('location_waiting')"):
            fails.append(f"stale fix: the wait does not say it is waiting for GPS: {state['text']!r}")
        page.wait_for_timeout(1500)
        if detect_bodies(central):
            fails.append("stale fix: the photo was filed while the fix was 20 s old")
        page.evaluate("window.__deliverFix(0)")
        if not wait_until(page, lambda: page.evaluate(STATE)["screen"] == "detail", 25_000):
            fails.append(f"stale fix: a fresh fix did not continue the check: {page.evaluate(STATE)}")
        detect = detect_bodies(central)
        if len(detect) != 1 or detect[0].get("lat") != 12.9716:
            fails.append(f"stale fix: the check did not use the fresh fix: {detect}")
        elif not 0 <= detect[0].get("location_age_ms", -1) <= 1500:
            fails.append(f"stale fix: a fix taken after the shutter is fresh, got {detect[0].get('location_age_ms')!r}")
        fails += fh.error_failures(errors, "stale fix")
    finally:
        browser.close()

    # 3. No fix at all: Retry asks the phone again, and the fix it then has is used.
    central, browser, page, errors = open_phone(playwright, watch_fix_age_ms=None)
    try:
        page.locator("#captureBtn").click()
        if not wait_until(page, lambda: page.evaluate(STATE)["retryVisible"], 25_000):
            fails.append(f"no fix: no Retry was offered: {page.evaluate(STATE)}")
        asked = page.evaluate("__geo.oneShots")
        page.evaluate("__geo.oneShotFix = () => ({ coords: { latitude: 12.9716, longitude: 77.5946, accuracy: 9 }, timestamp: Date.now() })")
        page.locator("#progressRetry").click()
        if not wait_until(page, lambda: page.evaluate(STATE)["screen"] == "detail", 25_000):
            fails.append(f"no fix: Retry did not finish the check: {page.evaluate(STATE)}")
        if page.evaluate("__geo.oneShots") <= asked:
            fails.append("no fix: Retry did not ask the phone for a position again")
        detect = detect_bodies(central)
        if len(detect) != 1 or detect[0].get("lat") != 12.9716:
            fails.append(f"no fix: the retried check went out without the fix: {detect}")
        fails += fh.error_failures(errors, "no fix")
    finally:
        browser.close()

    # 4. Cancel while waiting files nothing and frees the capture for the next photo.
    central, browser, page, errors = open_phone(playwright, watch_fix_age_ms=None)
    try:
        page.locator("#captureBtn").click()
        if not wait_until(page, lambda: page.evaluate(STATE)["retryVisible"], 25_000):
            fails.append(f"cancel: no Retry was offered: {page.evaluate(STATE)}")
        page.locator("#progressCancel").click()
        if not wait_until(page, lambda: page.evaluate(STATE)["screen"] == "home", 10_000):
            fails.append(f"cancel: did not return Home: {page.evaluate(STATE)}")
        page.wait_for_timeout(800)
        if detect_bodies(central):
            fails.append("cancel: a cancelled photo was still sent for checking")
        if page.evaluate("manualAnalysisActive"):
            fails.append("cancel: the capture lock was not released")
        fails += fh.error_failures(errors, "cancel")
    finally:
        browser.close()

if fails:
    print("FAIL manual recent fix")
    for failure in fails:
        print(" -", failure)
    sys.exit(1)
print("PASS manual recent fix")
