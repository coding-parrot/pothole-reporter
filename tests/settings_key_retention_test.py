#!/usr/bin/env python3
"""Switching to the shared service must not keep a personal OpenAI key on the phone.

The shared service never reads openai_key, and the field is hidden once Shared is
chosen, so a key saved back from it was plaintext the tester could no longer see or
clear short of Delete all.
"""

import sys

from playwright.sync_api import sync_playwright

from flow_harness import error_failures, open_flow


DUMMY = "sk-dummy-not-a-real-key-0000"

failures = []
with sync_playwright() as playwright:
    browser, page, errors = open_flow(playwright, native=False)
    try:
        page.wait_for_function("() => typeof openSettings === 'function'")
        page.evaluate("() => openSettings()")
        page.select_option("#setProvider", "personal")
        page.fill("#setKey", DUMMY)
        page.click("#setSave")
        page.wait_for_function("() => localStorage.getItem('vision_provider') === 'personal'"
                               " && !$('home').classList.contains('hidden')")
        stored_personal = page.evaluate("() => localStorage.getItem('openai_key')")

        page.evaluate("() => openSettings()")
        page.select_option("#setProvider", "shared")
        page.click("#setSave")
        page.wait_for_function("() => localStorage.getItem('vision_provider') === 'shared'"
                               " && !$('home').classList.contains('hidden')")
        after_shared = page.evaluate("() => localStorage.getItem('openai_key')")

        page.evaluate("() => openSettings()")
        field_after_reopen = page.input_value("#setKey")
    finally:
        browser.close()

if stored_personal != DUMMY:
    failures.append("the personal key was not saved while Personal was selected")
if after_shared is not None:
    failures.append(f"openai_key survived switching to Shared (length {len(after_shared)})")
if field_after_reopen:
    failures.append("the hidden key field still holds the old key after switching to Shared")
failures += error_failures(errors, "settings key retention")

if failures:
    print("SETTINGS KEY RETENTION TEST FAIL")
    for failure in failures:
        print(" -", failure)
    sys.exit(1)
print("SETTINGS KEY RETENTION TEST PASS")
