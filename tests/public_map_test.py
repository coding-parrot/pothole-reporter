# -*- coding: utf-8 -*-
"""The public map is keyless, deduplicated, count-bearing, and PII-free."""

import base64
import json
import os
import pathlib
import sys

from playwright.sync_api import sync_playwright

# A 1x1 transparent PNG: enough for Leaflet to count a loaded tile.
TILE_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


ROOT = pathlib.Path(__file__).resolve().parent.parent
APP = os.environ.get("POTHOLE_TEST_APP", "http://localhost:8765/")
SERVICE = "https://public-map.test"
PII = {
    "reporter_name": "REPORTER_PII_SENTINEL",
    "reporter_email": "EMAIL_PII_SENTINEL@example.invalid",
    "install_id": "INSTALL_ID_PII_SENTINEL",
    "request_id": "REQUEST_ID_PII_SENTINEL",
    "photo": "PHOTO_PII_SENTINEL",
}


def envelope(payload, request_id):
    return {"request_id": request_id, **payload}


def route_api(route, request):
    path = request.url.split(SERVICE, 1)[-1].split("?", 1)[0]
    headers = {"content-type": "application/json", "x-request-id": "header-secret"}
    if path == "/v1/health":
        payload = envelope({"ok": True, "shared_vision_configured": True}, "health-secret")
        status = 200
    elif path == "/v1/map":
        payload = envelope({
            "type": "FeatureCollection",
            "total": 2,
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [77.5946, 12.9716]},
                    "properties": {
                        "id": 41,
                        "damage_type": "pothole_cavity",
                        "size": "medium",
                        "first_seen_at": 1788500000000,
                        "last_seen_at": 1788500010000,
                        "seen_count": 9,
                        "complaint_count": 3,
                        "town": "Bengaluru",
                        **PII,
                    },
                },
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [77.6046, 12.9816]},
                    "properties": {
                        "id": 42,
                        "damage_type": "surface_breakup",
                        "size": "large",
                        "first_seen_at": 1788500020000,
                        "last_seen_at": 1788500030000,
                        # Current service contract: complaint_count may be absent.
                        "seen_count": 2,
                        "town": "Mysuru",
                    },
                },
                # Invalid coordinates can never become a misleading public marker.
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [999, 999]},
                    "properties": {"id": 43, "seen_count": 99, **PII},
                },
            ],
        }, "map-response-request-id-must-not-render")
        status = 200
    elif path == "/v1/impact":
        payload = envelope({
            "period": {"from": "2026-08-15", "to": "2026-09-13"},
            "active_installations": 7,
            "requests_total": 42,
            "potholes": {"total": 4},
            "observations": {"total": 11, "distinct_observers": 6},
            **PII,
        }, "impact-response-request-id-must-not-render")
        status = 200
    else:
        payload = envelope({"error": "not_mocked", "message": path}, "error-secret")
        status = 404
    route.fulfill(status=status, headers=headers, body=json.dumps(payload))


def main():
    failures = []
    static = (ROOT / "static/index.html").read_bytes()
    android = (ROOT / "android-app/www/index.html").read_bytes()
    if static != android:
        failures.append("static and packaged Android public-map assets differ")

    source = static.decode("utf-8")
    for required in (
        'api("/api/map")',
        'api("/api/impact")',
        'location.hash === "#public-map"',
        "sharedMap.features.map(publicMapPin)",
        'complaint_count: complaintCount',
    ):
        if required not in source:
            failures.append(f"public map contract is missing {required!r}")
    open_dash = source.split("async function openDash()", 1)[1].split(
        "\nfunction trackKm", 1
    )[0]
    if "ensureDataConsent" in open_dash:
        failures.append("public map still requires capture/location consent")
    if "...properties" in open_dash:
        failures.append("public map still copies arbitrary server properties")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 390, "height": 844})
        context.add_init_script(
            "localStorage.setItem('service_url', 'https://public-map.test');"
            "localStorage.removeItem('openai_key');"
            "localStorage.removeItem('data_notice_version');"
        )
        page = context.new_page()
        page.route(f"{SERVICE}/**", route_api)
        # Serve a real (tiny) tile rather than aborting: with no tile at all the app
        # correctly falls back to the offline scatter plot, and this test is about the
        # map's markers and popups, not the fallback.
        page.route("https://tile.openstreetmap.org/**", lambda route: route.fulfill(
            status=200, content_type="image/png", body=TILE_PNG))
        page.goto(APP + "#public-map")
        page.wait_for_function(
            "document.querySelector('#dash') && "
            "!document.querySelector('#dash').classList.contains('hidden') && "
            "document.querySelector('#mapNote').textContent.includes('5 reports')"
        )

        state = page.evaluate(
            """() => {
              const popups = [];
              mapObj.eachLayer(layer => {
                if (layer && layer._popup && layer._popup._content) {
                  popups.push(layer._popup._content);
                }
              });
              const sample = publicMapPin({
                geometry: {type: 'Point', coordinates: [77.5, 12.9]},
                properties: {id: 99, damage_type: 'pothole_cavity', seen_count: 1,
                  reporter_name: 'PRIVATE', install_id: 'PRIVATE', request_id: 'PRIVATE',
                  photo: 'PRIVATE'}
              });
              return {
                body: document.body.textContent,
                stats: document.querySelector('#communityStats').textContent,
                note: document.querySelector('#communityNote').textContent,
                mapNote: document.querySelector('#mapNote').textContent,
                popups,
                pinKeys: Object.keys(sample).sort(),
                consentHidden: document.querySelector('#dataConsent').classList.contains('hidden'),
                homeHidden: document.querySelector('#home').classList.contains('hidden'),
              };
            }"""
        )
        context.close()
        browser.close()

    if not state["consentHidden"] or not state["homeHidden"]:
        failures.append("#public-map did not open directly without the consent screen")
    for expected in (
        "7phonesusingtheapp",
        "42serverrequests",
        "11reports",
        "4potholes",
    ):
        if expected not in "".join(state["stats"].split()):
            failures.append(f"public aggregate tile missing {expected!r}: {state['stats']!r}")
    if state["mapNote"] != "2 potholes · 5 reports":
        failures.append(f"wrong canonical-location/count summary: {state['mapNote']!r}")
    if "cannot verify that an email was sent" not in state["note"]:
        failures.append("public count does not disclose that email delivery is unverified")
    popup_text = " ".join(state["popups"])
    for expected in ("Pothole #41", "3 complaint reports", "Pothole #42", "2 complaint reports"):
        if expected not in popup_text:
            failures.append(f"public marker popup missing {expected!r}: {popup_text!r}")
    rendered = state["body"] + popup_text
    for value in PII.values():
        if value in rendered:
            failures.append(f"public map rendered private field value {value!r}")
    expected_keys = sorted([
        "_shared", "complaint_count", "damage_type", "first_seen_at", "id", "last_seen_at",
        "lat", "lng", "size", "town", "verification",
    ])
    if state["pinKeys"] != expected_keys:
        failures.append(f"public map pin is not a strict allowlist: {state['pinKeys']!r}")

    if failures:
        print("FAIL")
        for failure in failures:
            print("  -", failure)
        return 1
    print("PUBLIC MAP TEST PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
