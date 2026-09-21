# -*- coding: utf-8 -*-
"""The shared detector runs the evaluated baseline; only a personal key picks its model.

Settings showed the model and image-detail selectors to every tester, and each shared
detection sent the stored choice, so one tester could move up to 50 owner-paid
detections a day onto the unevaluated gpt-5.6/original arm. The note beside the
selectors was developer copy in en and kn and, in mr and bn, claimed Drive always used
gpt-5.6/high. A tester on the shared service now sees no selector, and the service
receives the baseline even when an older build stored gpt-5.6/original.
"""

import json
import sys

from playwright.sync_api import sync_playwright

from central_stub_harness import Central, capture, open_central

STORED = {"detection_model": "gpt-5.6", "image_detail": "original"}
SELECTORS = ("setModel", "modelLabel", "setDetail", "detailLabel", "accuracyNote")

fails = []
with sync_playwright() as playwright:
    central = Central()
    browser, page, dialogs, errors = open_central(playwright, central, storage=STORED)
    try:
        page.wait_for_timeout(600)
        default_model, default_detail = page.evaluate(
            "[LLM_UI_CONFIG.defaultModel, LLM_UI_CONFIG.defaultImageDetail]")
        page.locator("#gearBtn").click()
        page.locator("#settings").wait_for(state="visible", timeout=10_000)
        shown = [name for name in SELECTORS if page.locator(f"#{name}").is_visible()]
        if shown:
            fails.append(f"shared mode shows the detector selectors: {shown}")
        page.select_option("#setProvider", "personal")
        page.wait_for_timeout(100)
        if not page.locator("#setModel").is_visible():
            fails.append("a personal key no longer offers the model selector")
        page.select_option("#setProvider", "shared")
        page.locator("#setBack").click()

        outcome, text = capture(page, dialogs)
        if outcome != "detail":
            fails.append(f"capture did not finish: {outcome} {text!r}")
        bodies = [json.loads(call["body"] or "{}") for call in central.calls
                  if call["path"] == "/v1/vision/detect"]
        sent = [(body.get("model"), body.get("image_detail")) for body in bodies]
        if not sent or any(pair != (default_model, default_detail) for pair in sent):
            fails.append(f"shared detection sent {sent}, want the baseline "
                         f"{(default_model, default_detail)}")
        if errors:
            fails.append(f"page errors {errors[:3]}")
    finally:
        browser.close()

    # The note is tester copy in every language, and never claims a fixed Drive model.
    browser, page, dialogs, errors = open_central(playwright, Central())
    try:
        for lang in ("en", "kn", "mr", "bn"):
            note = page.evaluate(f"I18N[{json.dumps(lang)}].accuracy_note")
            for banned in ("gpt-5.6/high", "benchmark", "Drive gpt"):
                if banned in note:
                    fails.append(f"{lang} accuracy note still says {banned!r}: {note!r}")
    finally:
        browser.close()

if fails:
    print("FAIL shared detector pin")
    for failure in fails:
        print(" -", failure)
    sys.exit(1)
print("PASS shared detector pin")
