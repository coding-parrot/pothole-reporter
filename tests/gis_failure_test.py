# -*- coding: utf-8 -*-
"""An unavailable central ownership resolver fails closed without phone-side fallback."""

import base64
import json
import os
import pathlib
import sys
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

# The data notice version is read from the bundle: a pinned copy that falls behind
# leaves every run of this suite stuck on the consent screen it thought it accepted.
from flow_harness import DATA_NOTICE_VERSION


ROOT = pathlib.Path(__file__).resolve().parent.parent
APP = os.environ.get("POTHOLE_TEST_APP", "http://localhost:8765/")
SERVICE = "https://ownership-failure.test"
IMG = ROOT / "eval/images/seed/IMG20260720144404.jpg"
CASES = [
    ("NH48 at Nelamangala", 13.094709, 77.389412),
    ("Bengaluru HSR", 12.9115, 77.6427),
]
ACCEPTED = {
    "image_quality": "acceptable", "assessment": "damaged",
    "damage_type": "pothole_cavity", "size": "medium",
    "description": "A pothole cavity is visible on the road.",
}


def envelope(route, payload, status=200, request_id="ownership-failure"):
    route.fulfill(
        status=status,
        headers={"content-type": "application/json", "x-request-id": request_id},
        body=json.dumps({"request_id": request_id, **payload}),
    )


def openai_success(route, _request):
    verdict = json.dumps(ACCEPTED, separators=(",", ":"))
    event = json.dumps({"type": "response.output_text.delta", "delta": verdict})
    route.fulfill(status=200, headers={"content-type": "text/event-stream"},
                  body=f"data: {event}\n\ndata: [DONE]\n\n")


POST = r"""async ([b64, lat, lng]) => {
  await StandaloneAPI.handle('/api/reports', {method:'DELETE'});
  // A retryable resolver 503 deliberately marks the service unavailable. Simulate the
  // next lifecycle reconnect so the following independent case can sync its map row.
  window.dispatchEvent(new Event('online'));
  await new Promise((resolve) => setTimeout(resolve, 50));
  const alerts = [];
  window.alert = (message) => alerts.push(String(message));
  const bin = atob(b64); const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  const form = new FormData();
  form.append('photo', new Blob([bytes], {type:'image/jpeg'}), 'road.jpg');
  form.append('lat', String(lat)); form.append('lng', String(lng));
  const initial = await StandaloneAPI.handle('/api/report', {method:'POST', body:form});
  let confirmed = null;
  const deadline = Date.now() + 4000;
  while (Date.now() < deadline) {
    confirmed = (await StandaloneAPI.handle('/api/reports'))
      .find((row) => row.id === initial.id);
    if (confirmed && confirmed.server_pothole_id && !confirmed.central_sync_pending) break;
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  openDetail(confirmed, [confirmed]);
  await sendReport(confirmed);
  const final = (await StandaloneAPI.handle('/api/reports'))
    .find((row) => row.id === initial.id);
  return {
    initial_status: initial.status, confirmed_id: confirmed && confirmed.server_pothole_id,
    status: final.status, reason: final.unrouted_reason,
    email: final.officer_email, subject: final.email_subject,
    alert: alerts.at(-1) || '', detail_text: document.getElementById('detail').innerText,
    send_buttons: document.querySelectorAll('#detail #sendBtn').length,
  };
}"""


fails = []
client_gis_leaks = []
source = base64.standard_b64encode(IMG.read_bytes()).decode()

for failure_mode in ("service_503", "unknown_success"):
    print(f"\n  central resolver mode: {failure_mode}")
    central_paths = []
    next_id = [8000]

    def central_service(route, request):
        path = urlparse(request.url).path
        central_paths.append(path)
        body = json.loads(request.post_data or "{}") if request.method == "POST" else {}
        if path == "/v1/health":
            envelope(route, {"ok": True, "shared_vision_configured": True, "ai_configured": True})
        elif path == "/v1/installations":
            envelope(route, {"install_id": f"failure-install-{failure_mode}"}, 201)
        elif path == "/v1/activity":
            envelope(route, {"accepted": True}, 202)
        elif path == "/v1/potholes/report":
            next_id[0] += 1
            envelope(route, {
                "duplicate": False, "dedupe": None,
                "pothole": {
                    "id": next_id[0], "lat": body.get("lat"), "lng": body.get("lng"),
                    "damage_type": body.get("damage_type"), "size": body.get("size"),
                    "first_seen_at": body.get("observed_at"),
                    "last_seen_at": body.get("observed_at"), "seen_count": 1,
                    "lgd": None, "town": None,
                },
            }, 201)
        elif path == "/v1/tenders/resolve" and failure_mode == "service_503":
            envelope(route, {"error": "ownership_unavailable",
                             "message": "Central ownership is unavailable."}, 503)
        elif path == "/v1/tenders/resolve":
            envelope(route, {
                "jurisdiction": {"road_ownership": "unknown",
                                 "lat": body.get("lat"), "lng": body.get("lng")},
                "tender": None, "reason": "road_class_unknown",
            })
        elif path == "/v1/map":
            envelope(route, {"type": "FeatureCollection", "total": 0, "features": []})
        elif path == "/v1/impact":
            envelope(route, {"requests_total": 0, "requests": [], "potholes": {"total": 0}})
        else:
            envelope(route, {"error": "not_mocked", "message": path}, 404)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=["--disable-web-security"])
        context = browser.new_context(viewport={"width": 390, "height": 844})
        context.add_init_script(script=f"""(() => {{
          localStorage.clear();
          localStorage.setItem("service_url", {json.dumps(SERVICE)});
          localStorage.setItem("vision_provider", "personal");
          localStorage.setItem("openai_key", "sk-ownership-failure-test");
          localStorage.setItem("data_notice_version", "{DATA_NOTICE_VERSION}");
        }})();""")
        context.route(f"{SERVICE}/**", central_service)
        context.route("https://api.openai.com/v1/responses", openai_success)
        context.route("**/karnataka-bodies.json", lambda route: route.fulfill(
            status=200, content_type="application/json", body='{"bodies":{}}'))

        def block_client_gis(route):
            client_gis_leaks.append(route.request.url)
            route.abort()

        context.route("https://nominatim.openstreetmap.org/**", block_client_gis)
        context.route("https://kgis.ksrsac.in/**", block_client_gis)
        page = context.new_page()
        page.goto(APP)
        page.wait_for_load_state("networkidle")
        page.wait_for_function("window.StandaloneAPI && typeof sendReport === 'function'")

        for name, lat, lng in CASES:
            result = page.evaluate(POST, [source, lat, lng])
            print(f"    {name:22} status={result['status']:9} "
                  f"reason={str(result['reason'] or '-'):20} email={result['email'] or '-'}")
            if result["initial_status"] != "draft" or not result["confirmed_id"]:
                fails.append(f"{failure_mode}/{name}: did not finish central dedupe before Email")
            if result["email"] or result["subject"]:
                fails.append(f"{failure_mode}/{name}: retained a sendable unverified complaint")
            if result["status"] != "unrouted" or result["reason"] != "road_class_unknown":
                fails.append(f"{failure_mode}/{name}: did not persist road_class_unknown: {result}")
            if "could not check" not in result["alert"].lower():
                fails.append(f"{failure_mode}/{name}: refusal was not surfaced: {result['alert']!r}")
            if "could not check who owns this road" not in result["detail_text"].lower():
                fails.append(f"{failure_mode}/{name}: detail did not show ownership refusal")
            if result["send_buttons"]:
                fails.append(f"{failure_mode}/{name}: unrouted detail still offered Email")
        context.close()
        browser.close()

    if central_paths.count("/v1/tenders/resolve") != len(CASES):
        fails.append(f"{failure_mode}: resolver call count was {central_paths}")

if client_gis_leaks:
    fails.append(f"central failure leaked phone-side GIS/geocoder calls: {client_gis_leaks}")

if fails:
    print("\nFAIL")
    for failure in fails:
        print("  -", failure)
    sys.exit(1)
print("\nGIS FAILURE TEST PASS")
