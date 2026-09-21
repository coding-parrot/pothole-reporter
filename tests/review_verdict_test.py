# -*- coding: utf-8 -*-
"""A photo the model cannot judge asks for a retake; it is never filed as undamaged.

decisionFor() calls an image_quality other than "acceptable" a review, but the report
was stored as "rejected", so the detail screen said "Road damage: NO" with the chip
"Undamaged" for a photo that was too dark to judge. The review copy and chip existed in
all four languages and no code path ever reached them.
"""

import sys

from playwright.sync_api import sync_playwright

from central_stub_harness import Central, capture, open_central

UNJUDGEABLE = {
    "image_quality": "rejected", "assessment": "undamaged",
    "damage_type": None, "size": None,
    "description": "The image is too dark to judge the road surface.",
}


def unjudgeable(route, request, path, central):
    if path != "/v1/vision/detect":
        return False
    import flow_harness as fh
    fh.envelope(route, {**UNJUDGEABLE, "detector": {
        "provider": "shared_server", "model": "gpt-5-mini",
        "prompt_version": "road-damage-v5", "schema_version": 4, "evidence_count": 1}})
    return True


fails = []
with sync_playwright() as playwright:
    for lang in ("en", "kn"):
        central = Central(unjudgeable)
        browser, page, dialogs, errors = open_central(playwright, central, lang=lang)
        try:
            page.wait_for_timeout(600)
            outcome, text = capture(page, dialogs)
            if outcome != "detail":
                fails.append(f"{lang}: capture did not reach the detail screen: {outcome} {text!r}")
                continue
            detail = page.locator("#detail").inner_text()
            review, rejected, undamaged = page.evaluate(
                "[t('verdict_review'), t('verdict_rejected'), t('chip_rejected')]")
            if review not in detail:
                fails.append(f"{lang}: detail does not ask for a retake: {detail!r}")
            for banned in (rejected, undamaged):
                if banned in detail:
                    fails.append(f"{lang}: an unjudgeable photo reads as {banned!r}")
            if not page.locator("#retakeBtn").is_visible():
                fails.append(f"{lang}: no Retake button on a review result")
            stored = page.evaluate(
                "async () => (await StandaloneAPI.handle('/api/reports')).map((r) => r.status)")
            if stored != ["review"]:
                fails.append(f"{lang}: stored status is {stored}, want ['review']")
            if errors:
                fails.append(f"{lang}: page errors {errors[:3]}")
        finally:
            browser.close()

if fails:
    print("FAIL review verdict")
    for failure in fails:
        print(" -", failure)
    sys.exit(1)
print("PASS review verdict")
