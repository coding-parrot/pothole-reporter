# -*- coding: utf-8 -*-
"""Re-checking the same frame after a Settings change sends a new Idempotency-Key.

The detect key was derived from the frame's source_event_key alone, but the signed body
also carries the language, model and image detail. The service keeps a completed key for
30 days and answers a same-key, different-body request with 409 idempotency_conflict, so
re-analysing an imported video after switching to Kannada failed every frame it had
checked before. The same frame under the same settings must keep its key, so a retry
after a dropped connection is still answered from the service's record, not re-counted.
"""

import sys
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

import flow_harness as fh

keys = []

REJECTED = {"image_quality": "acceptable", "assessment": "undamaged", "damage_type": None,
            "size": None, "description": "Clear road.",
            "detector": {"provider": "shared_server", "model": "gpt-5-mini",
                         "prompt_version": "road-damage-v5", "schema_version": 4,
                         "evidence_count": 1}}


def service(route, request):
    if urlparse(request.url).path != "/v1/vision/detect":
        return fh.central_service(route, request)
    keys.append(request.headers.get("idempotency-key"))
    fh.envelope(route, REJECTED)


FRAME = r"""
async (lang) => {
  localStorage.setItem("app_lang", lang);
  const canvas = document.createElement("canvas");
  canvas.width = 160; canvas.height = 120;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#777"; ctx.fillRect(0, 0, 160, 120);
  const photo = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", .88));
  const form = new FormData();
  form.append("photo", photo, "frame.jpg");
  form.append("lat", "12.9716"); form.append("lng", "77.5946");
  form.append("gps_accuracy", "4");
  form.append("capture_source", "imported_video");
  form.append("source_event_key", "import:clip-0-abc:3600");
  form.append("captured_at_ms", "1787625000000");
  await StandaloneAPI.handle("/api/frame", { method: "POST", body: form });
}
"""

fails = []
with sync_playwright() as playwright:
    browser, page, errors = fh.open_flow(playwright, native=False)
    try:
        page.context.unroute(f"{fh.SERVICE}/**")
        page.context.route(f"{fh.SERVICE}/**", service)
        for lang in ("en", "en", "kn"):
            page.evaluate(FRAME, lang)
        if len(keys) != 3 or not all(keys):
            fails.append(f"expected three detect calls with keys, got {keys}")
        else:
            if keys[0] != keys[1]:
                fails.append(f"the same frame and settings changed key: {keys[:2]}")
            if keys[2] == keys[0]:
                fails.append(f"a Kannada re-check reused the English key {keys[0]!r}, "
                             "which the service answers with 409 idempotency_conflict")
        fails += fh.error_failures(errors, "detect idempotency")
    except Exception as error:
        fails.append(f"flow broke: {str(error)[:300]}")
    finally:
        browser.close()

if fails:
    print("FAIL detect idempotency after a settings change")
    for failure in fails:
        print(" -", failure)
    sys.exit(1)
print("PASS detect idempotency after a settings change")
