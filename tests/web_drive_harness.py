# -*- coding: utf-8 -*-
"""Run the real WebView Drive path with a synthetic camera, a moving car and a scripted
detector.

The drive suites care about what a tester reads during and after a drive: the status
line, the summary, the drive record. Chromium's fake camera feeds the preview, the GPS
stub moves the phone at 30 km/h so the frame gate keeps sampling, and window.api is
wrapped so /api/frame answers after a chosen delay with a chosen result. Nothing leaves
the machine: the project service is the flow_harness stub and the detector never runs.
"""

import json

import flow_harness as fh

# A car at 30 km/h, north along one street.
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

# window.__frameStub = {delayMs, answer(n) -> result | throws}. calls counts attempts.
FRAME_STUB = r"""
(() => {
  window.__frameStub = { delayMs: 300, calls: 0, answer: () => ({ found: false }) };
  const install = () => {
    if (typeof window.api !== "function" || window.api.__stubbed) return false;
    const real = window.api;
    const stubbed = async (path, opts) => {
      if (path !== "/api/frame") return real(path, opts);
      const stub = window.__frameStub;
      const n = ++stub.calls;
      await new Promise((resolve) => setTimeout(resolve, stub.delayMs));
      return stub.answer(n);
    };
    stubbed.__stubbed = true;
    window.api = stubbed;
    return true;
  };
  const timer = setInterval(() => { if (install()) clearInterval(timer); }, 20);
})();
"""


def open_web_drive(playwright, storage=None, service=None, stub_frames=True):
    """Open Home with the stubs in place. Returns browser, page, dialogs, errors.

    service replaces the project-service handler; stub_frames=False sends frames through
    the real engine to that service instead of the in-page detector stub."""
    browser = playwright.chromium.launch(args=[
        "--disable-web-security", "--use-fake-device-for-media-stream",
        "--use-fake-ui-for-media-stream"])
    context = browser.new_context(
        viewport={"width": 412, "height": 915}, is_mobile=True, device_scale_factor=2.625,
        has_touch=True, geolocation={"latitude": 12.9716, "longitude": 77.5946},
        permissions=["camera", "geolocation"], locale="en-IN")
    values = {"service_url": fh.SERVICE, "data_notice_version": fh.DATA_NOTICE_VERSION,
              "initial_setup_complete": "1", "vision_provider": "shared",
              "sender_name": "Drive Tester", **(storage or {})}
    context.add_init_script(script="(() => {" + "".join(
        f"localStorage.setItem({json.dumps(k)}, {json.dumps(v)});"
        for k, v in values.items()) + "})();" + GEO + (FRAME_STUB if stub_frames else ""))
    context.route(f"{fh.SERVICE}/**", service or fh.central_service)
    context.route("**/karnataka-bodies.json", fh.support_services)
    context.route("https://nominatim.openstreetmap.org/**", fh.support_services)
    context.route("https://kgis.ksrsac.in/**", fh.support_services)
    page = context.new_page()
    dialogs = []
    page.on("dialog", lambda dialog: (
        dialogs.append(dialog.message) if dialog.type == "alert" else None, dialog.accept()))
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(fh.APP)
    page.wait_for_function("(stubbed) => !!window.StandaloneAPI"
                           " && (!stubbed || !!(window.api && window.api.__stubbed))",
                           arg=stub_frames, timeout=30_000)
    page.locator("#home").wait_for(state="visible", timeout=30_000)
    return browser, page, dialogs, errors


def wait_for_dialog(page, dialogs, count=1, timeout_s=60):
    waited = 0
    while len(dialogs) < count and waited < timeout_s * 1000:
        page.wait_for_timeout(200)
        waited += 200
    return len(dialogs) >= count
