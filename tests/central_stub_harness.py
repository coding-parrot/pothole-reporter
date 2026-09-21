# -*- coding: utf-8 -*-
"""Open the app against a scripted central service and drive a real photo capture.

The central-client suites each make the project server fail in one specific way and
read what a tester would see: the alert, the progress screen, the feedback status.
The scripted handler answers a path by returning True after fulfilling the route;
anything it leaves alone falls through to the healthy flow_harness service.
"""

import json
import pathlib
import time
from urllib.parse import urlparse

import flow_harness as fh

ROOT = pathlib.Path(__file__).resolve().parent.parent
PHOTO = ROOT / "docs/example-pothole.jpg"


class Central:
    def __init__(self, script=None):
        self.script = script
        self.calls = []

    def count(self, path):
        return sum(1 for call in self.calls if call["path"] == path)

    def handle(self, route, request):
        path = urlparse(request.url).path
        self.calls.append({"path": path, "at": time.time(),
                           "headers": {k.lower(): v for k, v in request.headers.items()},
                           "body": request.post_data or ""})
        if self.script and self.script(route, request, path, self):
            return
        fh.central_service(route, request)


def reply(route, status, error, message, details=None, request_id="req-central"):
    payload = {"error": error, "message": message}
    if details is not None:
        payload["details"] = details
    fh.envelope(route, payload, status, request_id)
    return True


def open_central(playwright, central, *, lang=None, storage=None):
    browser = playwright.chromium.launch(args=["--disable-web-security"])
    context = browser.new_context(
        viewport={"width": 412, "height": 915}, is_mobile=True, device_scale_factor=2.625,
        geolocation={"latitude": 12.9716, "longitude": 77.5946}, permissions=["geolocation"],
        locale="en-IN")
    values = {
        "service_url": fh.SERVICE, "data_notice_version": fh.DATA_NOTICE_VERSION,
        "initial_setup_complete": "1", "vision_provider": "shared",
        "sender_name": "Central Tester", **({"app_lang": lang} if lang else {}),
        **(storage or {}),
    }
    context.add_init_script(script="(() => {" + "".join(
        f"localStorage.setItem({json.dumps(k)}, {json.dumps(v)});" for k, v in values.items())
        + "})();")
    context.route(f"{fh.SERVICE}/**", central.handle)
    context.route("**/karnataka-bodies.json", fh.support_services)
    context.route("https://nominatim.openstreetmap.org/**", fh.support_services)
    context.route("https://kgis.ksrsac.in/**", fh.support_services)
    page = context.new_page()
    dialogs = []
    # Only alerts: the import-location confirm is a question, not an outcome.
    page.on("dialog", lambda dialog: (
        dialogs.append(dialog.message) if dialog.type == "alert" else None, dialog.accept()))
    errors = []
    page.on("pageerror", lambda error: errors.append(f"uncaught: {error}"))
    page.goto(fh.APP)
    page.wait_for_function("() => !!window.StandaloneAPI", timeout=30_000)
    page.locator("#home").wait_for(state="visible", timeout=30_000)
    return browser, page, dialogs, errors


def app_dialogs(dialogs):
    return list(dialogs)


def capture(page, dialogs, cap_s=40):
    """Pick the example photo and wait for an alert or the detail screen."""
    before = len(app_dialogs(dialogs))
    page.set_input_files("#fileInput", str(PHOTO))
    start = time.time()
    while time.time() - start < cap_s:
        if len(app_dialogs(dialogs)) > before:
            return "alert", app_dialogs(dialogs)[-1]
        if page.evaluate("() => !document.getElementById('detail').classList.contains('hidden')"):
            return "detail", None
        page.wait_for_timeout(100)
    return "timeout", None


def routed(route, request, path, central):
    """Answer the jurisdiction lookup with a body the shipped state pack has an address
    for. The healthy stub's LGD 999001 is not in the pack, so its reports are unrouted
    and never show the draft card or Email."""
    if path != "/v1/tenders/resolve":
        return False
    body = json.loads(request.post_data or "{}")
    fh.envelope(route, {
        "jurisdiction": {
            "lat": body.get("lat"), "lng": body.get("lng"),
            "address": "Test Road, Central Ward, Test City, 560001",
            "lgd": "248127", "town": "Kalaburagi", "source": "kgis",
            "address_source": "nominatim", "road_ownership": "municipal",
        },
        "tender": None, "reason": "no_tenders_for_jurisdiction",
    })
    return True
