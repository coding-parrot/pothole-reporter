# -*- coding: utf-8 -*-
"""Drive makes no promise it does not keep: a time or battery limit, an orange guide.

Settings saved drive_limit_minutes on every Save, nothing offered a way to choose it,
no drive ever read it, and the background-drive notice told testers that scanning
"stops automatically at the selected battery limit". A tester who trusted that left the
phone running. The setting and the sentence are gone, and Save clears a value an older
build left behind. The Drive tip and the README also told testers to fill an orange
guide that the preview never drew.
"""

import pathlib
import sys

from playwright.sync_api import sync_playwright

import flow_harness as fh

ROOT = pathlib.Path(__file__).resolve().parent.parent

failures = []
source = (ROOT / "static/index.html").read_text(encoding="utf-8")
for name in ("README.md", "static/index.html"):
    if "orange guide" in (ROOT / name).read_text(encoding="utf-8"):
        failures.append(f"{name} still points testers at an orange guide")
for phrase in ("battery limit", "drive_limit_minutes\", String", "DRIVE_LIMIT_OPTIONS", "limitTimer"):
    if phrase in source:
        failures.append(f"static/index.html still carries {phrase!r}")

with sync_playwright() as playwright:
    browser, page, errors = fh.open_flow(playwright, storage={"drive_limit_minutes": "15"})
    try:
        page.locator("#home").wait_for(state="visible", timeout=30_000)
        page.evaluate("() => { window.alert = () => {}; }")
        page.locator("#gearBtn").click()
        page.locator("#settings").wait_for(state="visible", timeout=30_000)
        page.locator("#setSave").click()
        page.locator("#home").wait_for(state="visible", timeout=30_000)
        stored = page.evaluate("localStorage.getItem('drive_limit_minutes')")
        if stored is not None:
            failures.append(f"Save kept drive_limit_minutes={stored!r}, which nothing reads")
        notice = page.locator("#nativeBackgroundNotice").text_content()
        if "limit" in notice:
            failures.append(f"the background notice still promises a limit: {notice!r}")
        failures += fh.error_failures(errors, "settings save")
    finally:
        browser.close()

if failures:
    print("FAIL drive limit")
    for failure in failures:
        print(" -", failure)
    sys.exit(1)
print("PASS drive limit")
