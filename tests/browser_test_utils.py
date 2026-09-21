"""Shared browser-test setup that never puts credentials in URLs or server logs."""

import json
import os
from urllib.parse import urlsplit


def _central_service(route, request):
    path = urlsplit(request.url).path
    body = json.loads(request.post_data or "{}") if request.method == "POST" else {}
    headers = {"content-type": "application/json", "x-request-id": "test-central-request"}
    if path == "/v1/installations":
        payload, status = {"request_id": "test-install", "install_id": "test-installation"}, 201
    elif path == "/v1/activity":
        payload, status = {"request_id": "test-activity", "accepted": True,
                           "event": "vision_check"}, 202
    elif path == "/v1/health":
        payload, status = {"request_id": "test-health", "ok": True,
                           "shared_vision_configured": True}, 200
    elif path == "/v1/tenders/resolve":
        payload, status = {"request_id": "test-tender", "jurisdiction": {
            "lat": body.get("lat"), "lng": body.get("lng"), "address": None,
            "lgd": None, "town": None, "source": "unresolved",
            "address_source": "unresolved"}, "tender": None,
            "reason": "test_no_match"}, 200
    elif path == "/v1/potholes/report":
        payload, status = {"request_id": "test-report", "duplicate": False,
            "dedupe": None, "pothole": {"id": 9001, "lat": body.get("lat"),
            "lng": body.get("lng"), "damage_type": body.get("damage_type"),
            "size": body.get("size"),
            "first_seen_at": body.get("observed_at"), "last_seen_at": body.get("observed_at"),
            "seen_count": 1, "lgd": None, "town": None}}, 201
    elif path == "/v1/map":
        payload, status = {"request_id": "test-map", "type": "FeatureCollection",
                           "total": 0, "features": []}, 200
    elif path == "/v1/impact":
        payload, status = {"request_id": "test-impact", "period": {},
            "active_installations": 0, "requests_total": 0, "requests": [],
            "potholes": {"total": 0},
            "observations": {"total": 0, "distinct_observers": 0}}, 200
    else:
        payload, status = {"request_id": "test-error", "error": "not_mocked",
                           "message": f"Unmocked central route: {path}"}, 404
    route.fulfill(status=status, headers=headers, body=json.dumps(payload))


def open_app(page, key):
    page.route("https://ffjvg34k07.execute-api.ap-south-1.amazonaws.com/**", _central_service)
    page.goto(os.environ.get("POTHOLE_TEST_APP", "http://localhost:8765/"))
    page.wait_for_load_state("domcontentloaded")
    page.evaluate("""key => {
      localStorage.setItem('vision_provider', 'personal');
      localStorage.setItem('openai_key', key);
    }""", key)
    page.reload()
    page.wait_for_load_state("networkidle")


# A placeholder for suites that need the personal-key path switched on but stub every
# detector call. Suites that used the real key from .env spent paid detections per run.
OFFLINE_KEY = "sk-offline-test-not-a-real-key"


def block_openai(page, leaks):
    """Keep OpenAI off the network. The app's free key probe (/v1/models) is answered
    here; anything else is recorded and refused, so an unstubbed detection fails the
    suite instead of spending one."""
    def refuse(route):
        if urlsplit(route.request.url).path == "/v1/models":
            route.fulfill(status=200, headers={"content-type": "application/json"},
                          body=json.dumps({"object": "list", "data": []}))
            return
        leaks.append(route.request.url)
        route.abort()
    page.route("https://api.openai.com/**", refuse)
