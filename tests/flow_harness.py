# -*- coding: utf-8 -*-
"""Shared setup for the signup, drive and reporting flow tests.

Every flow test opens the real bundled app with the network intercepted, drives the
same screens a tester touches, and fails on any uncaught exception or console error.

v1.38.1 shipped a Settings screen that threw "trimmedKey is not defined" on the first
tap of a fresh install. Nothing caught it because no test loaded that screen and
watched the console. These helpers make that impossible to repeat: page errors are
collected by default and asserted empty at the end of every flow.
"""

import json
import os
import pathlib
import re
from urllib.parse import urlparse

APP = os.environ.get("POTHOLE_TEST_APP", "http://localhost:8765/")
SERVICE = "https://flow-harness.test"
RECIPIENT = "commissioner@example.gov.in"
# Read from the app, never pinned: a copy of this string that falls behind the bundle
# makes every flow suite start from consent the app no longer considers accepted.
DATA_NOTICE_VERSION = re.search(
    r'const DATA_NOTICE_VERSION = "([^"]+)"',
    (pathlib.Path(__file__).resolve().parent.parent / "static/index.html")
    .read_text(encoding="utf-8")).group(1)

ACCEPTED = {
    "image_quality": "acceptable",
    "assessment": "damaged",
    "damage_type": "pothole_cavity",
    "size": "medium",
    "description": "A cavity with a broken rim is visible on the travelled surface.",
}


def envelope(route, payload, status=200, request_id="req-flow"):
    route.fulfill(
        status=status,
        headers={"content-type": "application/json", "x-request-id": request_id},
        body=json.dumps({"request_id": request_id, **payload}),
    )


def central_service(route, request):
    import hashlib

    path = urlparse(request.url).path
    body = json.loads(request.post_data or "{}") if request.method == "POST" else {}
    if path == "/v1/health":
        envelope(route, {"ok": True, "shared_vision_configured": True})
    elif path == "/v1/installations":
        envelope(route, {"install_id": "flow-harness-install"}, 201)
    elif path == "/v1/activity":
        envelope(route, {"accepted": True, "event": "vision_check"}, 202)
    elif path == "/v1/feedback":
        envelope(route, {"accepted": True, "created_at": 1}, 201)
    elif path == "/v1/vision/detect":
        envelope(route, {
            **ACCEPTED,
            "detection_receipt": hashlib.sha256(
                f"receipt:{body.get('client_observation_id')}".encode("utf-8")).hexdigest(),
            "detector": {
                "provider": "shared_server",
                "model": body.get("model", "gpt-5-mini"),
                "prompt_version": "road-damage-v5",
                "schema_version": 4,
                "evidence_count": len(body.get("images", [])),
            },
        })
    elif path == "/v1/tenders/resolve":
        envelope(route, {
            "jurisdiction": {
                "lat": body.get("lat"), "lng": body.get("lng"),
                "address": "Test Road, Central Ward, Test City, 560001",
                "lgd": "999001", "town": "Test City Corporation",
                "source": "kgis", "address_source": "nominatim",
                "road_ownership": "municipal",
            },
            "tender": None,
            "reason": "no_tenders_for_jurisdiction",
        })
    elif path == "/v1/potholes/report":
        envelope(route, {
            "duplicate": False, "dedupe": None,
            "pothole": {
                "id": 4242, "lat": body.get("lat"), "lng": body.get("lng"),
                "damage_type": body.get("damage_type"), "size": body.get("size"),
                "first_seen_at": body.get("observed_at"),
                "last_seen_at": body.get("observed_at"), "seen_count": 1,
                "lgd": "999001", "town": "Test City Corporation",
            },
        }, 201)
    elif path == "/v1/map":
        envelope(route, {"type": "FeatureCollection", "total": 0, "features": []})
    elif path == "/v1/impact":
        envelope(route, {
            "period": {}, "active_installations": 0, "requests_total": 0, "requests": [],
            "potholes": {"total": 0},
            "observations": {"total": 0, "distinct_observers": 0},
        })
    else:
        envelope(route, {"error": "not_mocked", "message": path}, 404)


def support_services(route, request):
    target = request.url
    if target.endswith("/karnataka-bodies.json"):
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"bodies": {
            "999001": {
                "name": "Test City Corporation", "type": "CC",
                "officer": "Commissioner", "email": RECIPIENT,
            }
        }}))
    elif "nominatim.openstreetmap.org" in target:
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "display_name": "Test Road, Central Ward, Test City, Karnataka, 560001, India",
            "address": {"road": "Test Road", "neighbourhood": "Central Ward",
                        "city": "Test City", "postcode": "560001"},
        }))
    elif "Admin_Dynamic_New" in target:
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"features": [{
            "attributes": {"KGISTownName": "Test City Corporation", "Town_Type": "CC",
                           "LGD_TownCode": "999001"}
        }]}))
    elif any(layer in target for layer in ("State_Basemap", "GP_Boundary", "NH_", "SH_")):
        route.fulfill(status=200, content_type="application/json", body='{"features":[]}')
    else:
        route.abort("blockedbyclient")


# A Capacitor stub that behaves like the phone: it grants permissions, runs a fake
# native Drive session, and records every exitApp call so a screen that quietly closes
# the app is a test failure rather than a tester's complaint.
NATIVE_STUB = r"""
(() => {
  window.__exitAppCalls = 0;
  window.__composerCalls = [];
  window.__driveCalls = [];
  const listeners = {};
  const status = {
    running: false, sessionId: null, frames: 0, reports: 0,
    recordingEnabled: false, paused: false,
  };
  const DriveMode = {
    async getStatus() { return { ...status }; },
    async requestDrivePermissions() {
      return { granted: true, notificationsGranted: true };
    },
    async getCentralIdentity() { return { installId: "flowharnessinstall0000000000000a" }; },
    async signCentralRequest(options) {
      return { installId: "flowharnessinstall0000000000000a", signature: "c2ln",
               timestamp: options.timestamp, idempotencyKey: options.idempotencyKey };
    },
    async start(options) {
      window.__driveCalls.push(["start", options || {}]);
      status.running = true;
      status.sessionId = "flow-session-1";
      return { ...status };
    },
    async stop() {
      window.__driveCalls.push(["stop", {}]);
      status.running = false;
      return { ...status, sessionId: "flow-session-1", frames: 3, reports: 1 };
    },
    async setVideoRecording(options) {
      status.recordingEnabled = !!(options && options.enabled);
      return { ...status };
    },
    async listReports() { return { reports: [] }; },
    async listDriveSessions() { return { sessions: [] }; },
    async listPendingKeyframes() { return { keyframes: [] }; },
    addListener(name, handler) {
      (listeners[name] = listeners[name] || []).push(handler);
      return { remove() {} };
    },
  };
  const App = {
    addListener(name, handler) {
      (listeners[name] = listeners[name] || []).push(handler);
      return { remove() {} };
    },
    exitApp() { window.__exitAppCalls += 1; },
    async getInfo() { return { version: "1.39.1", build: "70" }; },
  };
  const EmailComposer = {
    async open(options) {
      window.__composerCalls.push(JSON.parse(JSON.stringify(options)));
      return { value: true };
    },
  };
  const Camera = {
    async checkPermissions() { return { camera: "granted", photos: "granted" }; },
    async requestPermissions() { return { camera: "granted", photos: "granted" }; },
  };
  const Geolocation = {
    async checkPermissions() { return { location: "granted", coarseLocation: "granted" }; },
    async requestPermissions() { return { location: "granted", coarseLocation: "granted" }; },
    async getCurrentPosition() {
      return { coords: { latitude: 12.9716, longitude: 77.5946, accuracy: 4 },
               timestamp: Date.now() };
    },
    watchPosition() { return "watch-1"; },
    clearWatch() {},
  };
  window.__fireNative = (name, payload) => {
    for (const handler of listeners[name] || []) handler(payload);
  };
  window.Capacitor = {
    isNativePlatform: () => true,
    registerPlugin: (name) => window.Capacitor.Plugins[name],
    Plugins: { DriveMode, App, EmailComposer, Camera, Geolocation },
  };
})();
"""


BROWSER = os.environ.get("POTHOLE_TEST_BROWSER", "chromium")


def open_flow(playwright, *, fresh=False, native=True, storage=None, headless=True):
    """Open the app the way a tester's phone does, with errors recorded.

    fresh=True simulates a brand-new install: no settings, no consent, nothing stored.
    """
    # A synthetic camera lets the default (WebView) Drive path run for real in CI: the
    # preview, the frame grab and the stop sequence are all exercised without hardware.
    engine = getattr(playwright, BROWSER)
    # Chromium takes flags for a synthetic camera; Firefox and WebKit have their own
    # defaults, so the same flows run on all three without Chromium-only switches.
    args = ["--disable-web-security", "--use-fake-device-for-media-stream",
            "--use-fake-ui-for-media-stream"] if BROWSER == "chromium" else []
    browser = engine.launch(headless=headless, args=args)
    context_options = {"viewport": {"width": 390, "height": 844},
                       "geolocation": {"latitude": 12.9716, "longitude": 77.5946},
                       "locale": "en-IN"}
    # Only Chromium implements the "camera" permission name; the others grant capture
    # through their own launch defaults.
    context_options["permissions"] = ["camera", "geolocation"] if BROWSER == "chromium" \
        else ["geolocation"]
    context = browser.new_context(**context_options)
    prelude = [f'localStorage.setItem("service_url", {json.dumps(SERVICE)});']
    if not fresh:
        prelude += [
            f'localStorage.setItem("data_notice_version", {json.dumps(DATA_NOTICE_VERSION)});',
            'localStorage.setItem("initial_setup_complete", "1");',
            'localStorage.setItem("vision_provider", "shared");',
            'localStorage.setItem("sender_name", "Test Citizen");',
        ]
    for key, value in (storage or {}).items():
        prelude.append(f"localStorage.setItem({json.dumps(key)}, {json.dumps(value)});")
    script = "(() => {" + "\n".join(prelude) + "})();"
    if native:
        script += NATIVE_STUB
    context.add_init_script(script=script)
    context.route(f"{SERVICE}/**", central_service)
    context.route("**/karnataka-bodies.json", support_services)
    context.route("https://nominatim.openstreetmap.org/**", support_services)
    context.route("https://kgis.ksrsac.in/**", support_services)

    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(f"uncaught: {error}"))
    page.on("console", lambda message: errors.append(f"console.{message.type}: {message.text}")
            if message.type == "error" else None)
    page.goto(APP)
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_function("() => !!window.StandaloneAPI", timeout=30_000)
    return browser, page, errors


def error_failures(errors, where):
    """Console noise from blocked third-party assets is not an app bug; anything the
    app itself threw is."""
    ignorable = ("blockedbyclient", "net::ERR_FAILED", "Failed to load resource",
                 "favicon", "leaflet", "tile")
    real = [error for error in errors
            if not any(token.lower() in error.lower() for token in ignorable)]
    return [f"{where}: {error}" for error in real]


def report_form_script(lat=12.9716, lng=77.5946):
    return r"""
    async () => {
      const canvas = document.createElement("canvas");
      canvas.width = 160; canvas.height = 120;
      const ctx = canvas.getContext("2d");
      ctx.fillStyle = "#777"; ctx.fillRect(0, 0, 160, 120);
      ctx.fillStyle = "#111"; ctx.fillRect(50, 52, 60, 38);
      const photo = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", .88));
      const form = new FormData();
      form.append("photo", photo, "flow-road.jpg");
      form.append("lat", "__LAT__"); form.append("lng", "__LNG__");
      form.append("gps_accuracy", "4"); form.append("captured_at_ms", String(Date.now()));
      const report = await StandaloneAPI.handle("/api/report", { method: "POST", body: form });
      window.__flowReport = report;
      return report;
    }
    """.replace("__LAT__", str(lat)).replace("__LNG__", str(lng))
