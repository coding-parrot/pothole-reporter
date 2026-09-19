# -*- coding: utf-8 -*-
"""Central road ownership routes municipal complaints and refuses other owners."""

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
SERVICE = "https://routing-authority.test"
IMG = ROOT / "eval/images/seed/IMG20260720144404.jpg"

ACCEPTED = {
    "image_quality": "acceptable",
    "assessment": "damaged",
    "damage_type": "pothole_cavity",
    "size": "medium",
    "description": "A pothole cavity is visible on the road.",
}

# name, lat, lng, central ownership, expected post-tap status/reason, body code/type
TOWN_BY_LGD = {
    "305850": "Bengaluru East City Corporation",
    "252045": "Mysuru",
    "299417": "M.K.Hubballi",
    "251979": "Chikkaballapur",
}

CASES = [
    ("Bengaluru HSR", 12.9115, 77.6427, "municipal", "queued", None, "305850", "CC"),
    ("Mysuru city", 12.2958, 76.6394, "municipal", "queued", None, "252045", "CC"),
    ("Hubballi-Dharwad", 15.3647, 75.1240, "municipal", "queued", None, "299417", "TP"),
    ("Chikkaballapur CMC", 13.4310, 77.7270, "municipal", "queued", None, "251979", "CMC"),
    ("NH69 at Chikkaballapur", 13.4355, 77.7315,
     "national_highway", "unrouted", "national_highway", None, None),
    ("rural Magadi taluk", 13.0000, 77.2000,
     "rural", "unrouted", "rural_road", None, None),
    ("Chennai, out of state", 13.0827, 80.2707,
     "outside_state", "unrouted", "outside_area", None, None),
    ("no GPS", None, None, None, "unrouted", "no_location", None, None),
]


case_by_coord = {
    (round(lat, 4), round(lng, 4)): (name, ownership, lgd, town_type)
    for name, lat, lng, ownership, _status, _reason, lgd, town_type in CASES
    if lat is not None
}
central_paths = []
client_gis_leaks = []
next_pothole_id = 7000


def envelope(route, payload, status=200, request_id="routing-request"):
    route.fulfill(
        status=status,
        headers={"content-type": "application/json", "x-request-id": request_id},
        body=json.dumps({"request_id": request_id, **payload}),
    )


def central_service(route, request):
    global next_pothole_id
    path = urlparse(request.url).path
    central_paths.append(path)
    body = json.loads(request.post_data or "{}") if request.method == "POST" else {}
    if path == "/v1/health":
        envelope(route, {"ok": True, "shared_vision_configured": True, "ai_configured": True})
    elif path == "/v1/installations":
        envelope(route, {"install_id": f"routing-install-{len(central_paths)}"}, 201)
    elif path == "/v1/activity":
        envelope(route, {"accepted": True}, 202)
    elif path == "/v1/potholes/report":
        next_pothole_id += 1
        envelope(route, {
            "duplicate": False, "dedupe": None,
            "pothole": {
                "id": next_pothole_id, "lat": body.get("lat"), "lng": body.get("lng"),
                "damage_type": body.get("damage_type"), "size": body.get("size"),
                "first_seen_at": body.get("observed_at"),
                "last_seen_at": body.get("observed_at"), "seen_count": 1,
                "lgd": None, "town": None,
            },
        }, 201, f"report-{next_pothole_id}")
    elif path == "/v1/tenders/resolve":
        case = case_by_coord[(round(float(body["lat"]), 4), round(float(body["lng"]), 4))]
        _name, ownership, lgd, town_type = case
        jurisdiction = {"road_ownership": ownership, "lat": body["lat"], "lng": body["lng"]}
        reason = ownership
        if ownership == "municipal":
            # The resolver returns the body's LGD code; the app looks the officer up in
            # the signed State pack, so the fixture only has to name a real code.
            town = TOWN_BY_LGD[lgd]
            jurisdiction.update({
                "address": f"Test Road, {town}", "lgd": lgd,
                "town": town, "town_type": town_type,
            })
            reason = "no_tender_match"
        elif ownership == "national_highway":
            jurisdiction["highway_name"] = "NH 69"
        elif ownership == "rural":
            jurisdiction["rural_body"] = "Magadi Gram Panchayat"
        envelope(route, {"jurisdiction": jurisdiction, "tender": None, "reason": reason})
    elif path == "/v1/map":
        envelope(route, {"type": "FeatureCollection", "total": 0, "features": []})
    elif path == "/v1/impact":
        envelope(route, {"requests_total": 0, "requests": [], "potholes": {"total": 0}})
    else:
        envelope(route, {"error": "not_mocked", "message": path}, 404)


def openai_success(route, _request):
    verdict = json.dumps(ACCEPTED, separators=(",", ":"))
    event = json.dumps({"type": "response.output_text.delta", "delta": verdict})
    route.fulfill(
        status=200,
        headers={"content-type": "text/event-stream"},
        body=f"data: {event}\n\ndata: [DONE]\n\n",
    )


POST = r"""async ([b64, lat, lng]) => {
  await StandaloneAPI.handle('/api/reports', {method:'DELETE'});
  const bin = atob(b64); const arr = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
  const fd = new FormData();
  fd.append('photo', new Blob([arr], {type:'image/jpeg'}), 'p.jpg');
  if (lat !== null) { fd.append('lat', String(lat)); fd.append('lng', String(lng)); }
  const initial = await StandaloneAPI.handle('/api/report', {method:'POST', body:fd});
  if (lat !== null) {
    const deadline = Date.now() + 4000;
    while (Date.now() < deadline) {
      const row = (await StandaloneAPI.handle('/api/reports'))
        .find((item) => item.id === initial.id);
      if (row && row.server_pothole_id && !row.central_sync_pending) break;
      await new Promise((resolve) => setTimeout(resolve, 20));
    }
  }
  let sent = null, blocked = null;
  try {
    sent = await StandaloneAPI.handle('/api/reports/' + initial.id + '/send', {method:'POST'});
  } catch (error) { blocked = error.message; }
  const stored = (await StandaloneAPI.handle('/api/reports'))
    .find((row) => row.id === initial.id);
  const result = sent || stored || initial;
  return {
    initial_status: initial.status, status: result.status,
    reason: result.unrouted_reason, body: result.unrouted_body,
    officer: result.officer_name, email: result.officer_email,
    subject: result.email_subject, tender: result.tender_number,
    server_pothole_id: result.server_pothole_id, blocked,
  };
}"""


fails = []
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(args=["--disable-web-security"])
    context = browser.new_context(viewport={"width": 390, "height": 844})
    context.add_init_script(script=f"""(() => {{
      localStorage.clear();
      localStorage.setItem("service_url", {json.dumps(SERVICE)});
      localStorage.setItem("vision_provider", "personal");
      localStorage.setItem("openai_key", "sk-routing-test");
      localStorage.setItem("data_notice_version", "{DATA_NOTICE_VERSION}");
    }})();""")
    context.route(f"{SERVICE}/**", central_service)
    context.route("https://api.openai.com/v1/responses", openai_success)

    def block_client_gis(route):
        client_gis_leaks.append(route.request.url)
        route.abort()

    context.route("https://nominatim.openstreetmap.org/**", block_client_gis)
    context.route("https://kgis.ksrsac.in/**", block_client_gis)
    page = context.new_page()
    page.goto(APP)
    page.wait_for_load_state("networkidle")
    page.wait_for_function("window.StandaloneAPI && typeof StandaloneAPI.handle === 'function'")
    source = base64.standard_b64encode(IMG.read_bytes()).decode()

    for name, lat, lng, _ownership, want, reason, _lgd, town_type in CASES:
        result = page.evaluate(POST, [source, lat, lng])
        print(f"  {name:24} {result['status']:9} {str(result['reason'] or ''):20} "
              f"{str(result['officer'] or '')[:34]}")
        if result["status"] != want:
            fails.append(f"{name}: expected {want}, got {result['status']}")
        if lat is not None and result["initial_status"] != "draft":
            fails.append(f"{name}: personal detection did not return its initial draft")
        if lat is not None and not result["server_pothole_id"]:
            fails.append(f"{name}: Email path ran before central deduplication completed")
        if reason and result["reason"] != reason:
            fails.append(f"{name}: expected reason {reason}, got {result['reason']}")
        if want == "unrouted":
            if result["email"] or result["subject"] or result["tender"]:
                fails.append(f"{name}: retained a sendable municipal complaint")
            if not result["blocked"]:
                fails.append(f"{name}: refusal was not surfaced")
        else:
            if not result["email"] or result["blocked"]:
                fails.append(f"{name}: verified municipal point was not sendable: {result}")
            # City corporations are headed by a Commissioner; councils, town municipal
            # councils and town panchayats by a Chief Officer.
            expected_title = "Commissioner" if town_type == "CC" else "Chief Officer"
            if expected_title.lower() not in (result["officer"] or "").lower():
                fails.append(f"{name}: wrong officer class: {result['officer']!r}")
    context.close()
    browser.close()

if client_gis_leaks:
    fails.append(f"central-authority flow leaked phone-side GIS/geocoder calls: {client_gis_leaks}")
expected_resolutions = len([case for case in CASES if case[1] is not None])
if central_paths.count("/v1/tenders/resolve") != expected_resolutions:
    fails.append(f"central resolver calls={central_paths.count('/v1/tenders/resolve')}, "
                 f"expected {expected_resolutions}")

if fails:
    print("\nFAIL")
    for failure in fails:
        print("  -", failure)
    sys.exit(1)
print("\nROUTING TEST PASS")
