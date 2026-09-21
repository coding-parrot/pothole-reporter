# -*- coding: utf-8 -*-
"""A drive shows how many shared checks are left today, and a drive the cap ends says so.

Each phone gets 50 shared checks a day. Drive never showed how many were left, and when
the cap refused a frame the drive stopped with the cap alert followed by a summary that
blamed the connection: "2 frames could not be checked because of the connection." A
tester who reads that checks their signal, not their budget. Here the project service
checks five frames and then refuses with daily_vision_limit.
"""

import json
import sys
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

import flow_harness as fh
from web_drive_harness import open_web_drive, wait_for_dialog

LIMIT = 50
ALLOWED = 5
detects = []


def service(route, request):
    if urlparse(request.url).path != "/v1/vision/detect":
        return fh.central_service(route, request)
    detects.append(1)
    if len(detects) > ALLOWED:
        return fh.envelope(route, {"error": "daily_vision_limit",
                                   "message": "The configured shared-vision limit has been reached.",
                                   "details": {"retryable": False, "limit": LIMIT}}, 503)
    fh.envelope(route, {"image_quality": "acceptable", "assessment": "undamaged",
                        "damage_type": None, "size": None, "description": "Clear road.",
                        "detector": {"provider": "shared_server", "model": "gpt-5-mini",
                                     "prompt_version": "road-damage-v5", "schema_version": 4,
                                     "evidence_count": 1},
                        "quota": {"used": None, "limit": LIMIT}})


fails = []
with sync_playwright() as playwright:
    browser, page, dialogs, errors = open_web_drive(playwright, service=service,
                                                    stub_frames=False)
    try:
        page.evaluate("() => { window.__huds = []; }")
        page.locator("#driveBtn").click()
        page.wait_for_function(f"() => drive && drive.tally.checked >= {ALLOWED - 1}",
                               timeout=60_000)
        page.wait_for_timeout(1100)
        hud = page.locator("#driveStatus").text_content()
        left = page.evaluate("t('shared_checks_left', { left: 'L', limit: 'M' })") \
            if page.evaluate("'shared_checks_left' in I18N.en") else None
        if not left or f"of {LIMIT}" not in hud or "left" not in hud:
            fails.append(f"the HUD does not show the checks left today: {hud!r}")
        if not wait_for_dialog(page, dialogs, 2, 60):
            fails.append(f"expected the cap alert and a summary, got {dialogs}")
        summary = dialogs[-1] if dialogs else ""
        if "because of the connection" in summary:
            fails.append(f"the cap is reported as a connection problem: {summary!r}")
        cap_note = page.evaluate("'drive_end_cap' in I18N.en && t('drive_end_cap', { n: 'N' }).split(':')[0]")
        if not cap_note or cap_note not in summary:
            fails.append(f"the summary does not name the daily limit: {summary!r}")
        stored = page.evaluate("() => StandaloneAPI.sharedChecksToday && StandaloneAPI.sharedChecksToday()")
        if not stored or stored.get("used") != LIMIT or stored.get("limit") != LIMIT:
            fails.append(f"the refusal did not mark today's checks as used up: {stored}")
        if errors:
            fails.append(f"page errors {errors[:3]}")
    except Exception as error:
        fails.append(f"flow broke: {str(error)[:300]}")
    finally:
        browser.close()

if fails:
    print("FAIL drive quota summary")
    for failure in fails:
        print(" -", failure)
    sys.exit(1)
print("PASS drive quota summary")
