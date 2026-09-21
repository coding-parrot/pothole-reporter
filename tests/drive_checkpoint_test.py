# -*- coding: utf-8 -*-
"""A web drive keeps its record on disk while it runs, so a killed app loses little.

The only write of a web drive's row was in Stop. When Android killed the app mid-drive
(no Stop, no visibility event), the relaunch showed no drive at all: the GPS trail and
the checked count were gone and History could only count the damage it had kept. Here
the drive is left running and storage is read the way a relaunch would read it.
"""

import sys

from playwright.sync_api import sync_playwright

from web_drive_harness import open_web_drive

fails = []
with sync_playwright() as playwright:
    browser, page, dialogs, errors = open_web_drive(playwright)
    try:
        page.locator("#driveBtn").click()
        page.wait_for_function("() => drive && drive.tally.checked >= 3", timeout=60_000)
        page.wait_for_timeout(6500)
        live = page.evaluate("() => ({ id: drive.sessionId, checked: drive.tally.checked })")
        rows = page.evaluate("() => StandaloneAPI.handle('/api/drives')")
        row = next((r for r in rows if str(r.get("id")) == str(live["id"])), None)
        if not row:
            fails.append(f"no drive row on disk while the drive runs: {rows}")
        else:
            if not row.get("checked"):
                fails.append(f"the on-disk drive row has no checked count: {row}")
            if not row.get("gps_track"):
                fails.append("the on-disk drive row has no GPS trail")
        # A clean Stop still writes the final numbers over the checkpoint.
        page.locator("#driveStop").click()
        page.wait_for_function("() => !drive", timeout=30_000)
        page.wait_for_timeout(1500)
        final = page.evaluate("(id) => StandaloneAPI.handle('/api/drives')"
                              ".then((rows) => rows.find((r) => String(r.id) === id))",
                              str(live["id"]))
        if not final or final.get("checked", 0) < live["checked"]:
            fails.append(f"Stop did not leave the final count: {final and final.get('checked')}")
        if errors:
            fails.append(f"page errors {errors[:3]}")
    except Exception as error:
        fails.append(f"flow broke: {str(error)[:300]}")
    finally:
        browser.close()

if fails:
    print("FAIL drive checkpoint")
    for failure in fails:
        print(" -", failure)
    sys.exit(1)
print("PASS drive checkpoint")
