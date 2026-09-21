"""Zero-setup shared vision, personal-key opt-in, and bounded outage UX."""

import json
import os

from playwright.sync_api import sync_playwright

# The data notice version is read from the bundle: a pinned copy that falls behind
# leaves every run of this suite stuck on the consent screen it thought it accepted.
from flow_harness import DATA_NOTICE_VERSION

from server_client_contract_test import (
    ACCEPTED,
    CREATE_REPORT,
    CentralHarness,
    SERVICE,
    route_support,
)


APP = os.environ.get("POTHOLE_TEST_APP", "http://localhost:8765/")
fails = []


def openai_success(calls):
    def handler(route, request):
        calls.append({"headers": request.headers, "body": request.post_data or ""})
        verdict = json.dumps(ACCEPTED, separators=(",", ":"))
        event = json.dumps({"type": "response.output_text.delta", "delta": verdict})
        route.fulfill(
            status=200,
            headers={"content-type": "text/event-stream"},
            body=f"data: {event}\n\ndata: [DONE]\n\n",
        )

    return handler


def wire_support(context, harness):
    context.route(f"{SERVICE}/**", harness.handle)
    context.route("**/karnataka-bodies.json", route_support)
    context.route("https://nominatim.openstreetmap.org/**", route_support)
    context.route("https://kgis.ksrsac.in/**", route_support)


with sync_playwright() as p:
    browser = p.chromium.launch(args=["--disable-web-security"])

    # A fresh install has neither a provider preference nor an API key. It must use
    # the shared detector and receive central authority/tender/contractor enrichment.
    shared_harness = CentralHarness()
    shared_context = browser.new_context(viewport={"width": 390, "height": 844})
    shared_context.add_init_script(script=f"""(() => {{
      localStorage.clear();
      localStorage.setItem('service_url', {json.dumps(SERVICE)});
      localStorage.setItem('data_notice_version', '{DATA_NOTICE_VERSION}');
    }})();""")
    wire_support(shared_context, shared_harness)
    shared_openai = []
    shared_context.route("https://api.openai.com/v1/responses", openai_success(shared_openai))
    page = shared_context.new_page()
    page.goto(APP)
    page.wait_for_load_state("networkidle")
    page.wait_for_function("() => !!window.StandaloneAPI")

    health = page.evaluate("StandaloneAPI.handle('/api/health')")
    if health.get("provider") != "shared_server" or not health.get("ai_configured"):
        fails.append(f"fresh no-key install did not select configured shared vision: {health}")
    if page.locator("#setProvider").input_value() != "shared":
        fails.append("Settings did not render Shared/no-key as the fresh default")
    provider_matrix = page.evaluate("""() => ({
      noKey: StandaloneAPI.__pure.effectiveVisionProvider(null, ''),
      legacyKey: StandaloneAPI.__pure.effectiveVisionProvider(null, 'sk-existing'),
      explicitPersonal: StandaloneAPI.__pure.effectiveVisionProvider('personal', 'sk-existing'),
      missingPersonal: StandaloneAPI.__pure.effectiveVisionProvider('personal', ''),
      explicitShared: StandaloneAPI.__pure.effectiveVisionProvider('shared', 'sk-existing'),
      sharedAlias: StandaloneAPI.__pure.effectiveVisionProvider('shared_server', 'sk-existing'),
      sharedAliasBlank: StandaloneAPI.__pure.effectiveVisionProvider('shared_server', ''),
      personalAlias: StandaloneAPI.__pure.effectiveVisionProvider('personal_openai', 'sk-existing'),
      personalAliasBlank: StandaloneAPI.__pure.effectiveVisionProvider('personal_openai', ''),
    })""")
    if provider_matrix != {
        "noKey": "shared",
        "legacyKey": "personal",
        "explicitPersonal": "personal",
        "missingPersonal": "shared",
        "explicitShared": "shared",
        "sharedAlias": "shared",
        "sharedAliasBlank": "shared",
        "personalAlias": "personal",
        "personalAliasBlank": "shared",
    }:
        fails.append(f"provider fallback matrix diverged: {provider_matrix}")
    if not page.locator("#settings").evaluate("element => element.classList.contains('hidden')"):
        fails.append("fresh no-key startup opened Settings instead of remaining usable")

    report = page.evaluate(CREATE_REPORT)
    if report.get("vision_provider") != "shared_server":
        fails.append(f"fresh no-key report did not use shared detection: {report}")
    if report.get("tender_number") != "TEST-2026-1":
        fails.append(f"server tender was not retained: {report}")
    if report.get("contractor") != "Example Roads Ltd":
        fails.append(f"server contractor was not retained: {report}")
    shared_paths = [request["path"] for request in shared_harness.requests]
    for required_path in ("/v1/vision/detect", "/v1/tenders/resolve", "/v1/potholes/report"):
        if required_path not in shared_paths:
            fails.append(f"fresh no-key path omitted {required_path}: {shared_paths}")
    if shared_openai:
        fails.append("fresh no-key shared mode called OpenAI directly")
    detail_text = page.evaluate("""() => {
      openDetail(window.__contractReport);
      return document.getElementById('detail').innerText;
    }""")
    if "Example Roads Ltd" not in detail_text or "TEST-2026-1" not in detail_text:
        fails.append(f"detail did not display server tender/contractor: {detail_text!r}")

    # Photo viewer remains a result screen, rather than looking like an image-only stall.
    viewer = page.evaluate("""(() => {
      openViewer({id: 7, status: 'draft', assessment: 'damaged',
        damage_type: 'surface_breakup', size: 'medium',
        description: 'Broken roadway in the travel lane.',
        photo_url: 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw=='});
      return {text: document.getElementById('viewerMeta').innerText,
              hidden: document.getElementById('viewer').classList.contains('hidden')};
    })()""")
    for expected in ("damaged", "broken road surface", "medium", "Broken roadway"):
        if expected.lower() not in viewer["text"].lower():
            fails.append(f"viewer omits visible result {expected!r}: {viewer['text']!r}")
    if viewer["hidden"]:
        fails.append("viewer did not open")
    page.locator("#viewer").click(position={"x": 5, "y": 400})
    if not page.locator("#viewer").evaluate("element => element.classList.contains('hidden')"):
        fails.append("tapping the viewer backdrop did not close it")
    shared_context.close()

    # A valid personal key plus an explicit Personal choice still bypasses shared vision.
    personal_harness = CentralHarness()
    personal_context = browser.new_context(viewport={"width": 390, "height": 844})
    personal_context.add_init_script(script=f"""(() => {{
      localStorage.clear();
      localStorage.setItem('service_url', {json.dumps(SERVICE)});
      localStorage.setItem('data_notice_version', '{DATA_NOTICE_VERSION}');
      localStorage.setItem('vision_provider', 'personal');
      localStorage.setItem('openai_key', 'sk-personal-test-secret');
    }})();""")
    wire_support(personal_context, personal_harness)
    personal_openai = []
    personal_context.route("https://api.openai.com/v1/responses", openai_success(personal_openai))
    personal = personal_context.new_page()
    personal.goto(APP)
    personal.wait_for_load_state("networkidle")
    personal.wait_for_function("() => !!window.StandaloneAPI")
    personal_report = personal.evaluate(CREATE_REPORT)
    if personal_report.get("vision_provider") != "personal_openai" or len(personal_openai) != 1:
        fails.append(f"valid personal key did not use direct OpenAI exactly once: {personal_report}")
    if any(request["path"] == "/v1/vision/detect" for request in personal_harness.requests):
        fails.append("personal-key report also spent shared detection capacity")
    personal_context.close()

    # Even an old saved Personal preference must fall back when it has no key. This is
    # the upgrade case that previously opened Settings and could leave footage at a spinner.
    outage_context = browser.new_context(
        viewport={"width": 390, "height": 844},
        geolocation={"latitude": 12.9716, "longitude": 77.5946}, permissions=["geolocation"])
    outage_context.add_init_script(script=f"""(() => {{
      localStorage.clear();
      localStorage.setItem('service_url', {json.dumps(SERVICE)});
      localStorage.setItem('data_notice_version', '{DATA_NOTICE_VERSION}');
      localStorage.setItem('vision_provider', 'personal');
      window.__alerts = [];
      window.alert = (message) => window.__alerts.push(String(message));
    }})();""")

    def outage_handler(route, request):
        route.fulfill(
            status=503,
            headers={"content-type": "application/json", "x-request-id": "req-outage"},
            body=json.dumps({
                "request_id": "req-outage",
                "error": "shared_detector_unavailable",
                "message": "Shared detector is temporarily unavailable.",
            }),
        )

    outage_context.route(f"{SERVICE}/**", outage_handler)
    outage = outage_context.new_page()
    outage.goto(APP)
    outage.wait_for_load_state("networkidle")
    outage.wait_for_function("() => !!window.StandaloneAPI")
    outage.evaluate("""() => {
      window.__fileClicks = 0;
      document.getElementById('fileInput').addEventListener('click', (event) => {
        window.__fileClicks += 1; event.preventDefault();
      });
    }""")
    effective = outage.evaluate("""() => StandaloneAPI.__pure.effectiveVisionProvider(
      localStorage.getItem('vision_provider'), localStorage.getItem('openai_key'))""")
    if effective != "shared":
        fails.append(f"missing key did not override stale Personal preference: {effective!r}")
    # The health probe is warm-up, not a gate: Photo must reach the picker during an
    # outage, and the outage is reported when the photo comes back from the service.
    outage.locator("#captureBtn").click()
    outage.wait_for_timeout(100)
    if outage.evaluate("window.__fileClicks") != 1:
        fails.append("shared outage kept Photo from opening the camera picker")
    outage.evaluate("""async () => {
      const canvas = document.createElement('canvas');
      canvas.width = 160; canvas.height = 120;
      const ctx = canvas.getContext('2d');
      ctx.fillStyle = '#777'; ctx.fillRect(0, 0, 160, 120);
      const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', .88));
      const input = document.getElementById('fileInput');
      const transfer = new DataTransfer();
      transfer.items.add(new File([blob], 'outage-road.jpg', {type: 'image/jpeg'}));
      input.files = transfer.files;
      input.dispatchEvent(new Event('change'));
    }""")
    try:
        outage.wait_for_function("() => window.__alerts.length > 0", timeout=15_000)
    except Exception:
        pass
    outage.wait_for_timeout(200)
    state = outage.evaluate("""() => ({
      alerts: window.__alerts,
      homeHidden: document.getElementById('home').classList.contains('hidden'),
      settingsHidden: document.getElementById('settings').classList.contains('hidden'),
      progressHidden: document.getElementById('progress').classList.contains('hidden'),
    })""")
    if not state["alerts"] or "unavailable" not in state["alerts"][-1].lower():
        fails.append(f"shared outage did not show a clear error on the photo: {state}")
    if state["homeHidden"] or not state["settingsHidden"] or not state["progressHidden"]:
        fails.append(f"shared outage left Settings or a spinner on screen: {state}")
    outage_context.close()
    browser.close()

if fails:
    print("FAIL")
    for fail in fails:
        print(" -", fail)
    raise SystemExit(1)

print("PASS: no-key shared, personal-key direct, tender/contractor, and outage UX")
