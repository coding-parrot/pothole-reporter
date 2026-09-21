# -*- coding: utf-8 -*-
"""A phone the server no longer knows registers again instead of failing forever.

The server answers 401 unknown_installation whenever its installations row is missing
or revoked (a table restore, a revoke). The app kept the stored key and failed every
later shared call with "This installation is not registered." until the tester found
"Delete all data". Here the first detect is refused that way; the app must register a
fresh key, resend the same request once, and show the result.
"""

import json
import sys

from playwright.sync_api import sync_playwright

from central_stub_harness import Central, capture, open_central, reply
import flow_harness as fh

registrations = []


def script(route, request, path, central):
    if path == "/v1/installations":
        key = json.loads(request.post_data or "{}").get("public_key")
        registrations.append(key)
        fh.envelope(route, {"install_id": f"install-{len(registrations)}"}, 201)
        return True
    if path == "/v1/vision/detect" and central.count("/v1/vision/detect") == 1:
        return reply(route, 401, "unknown_installation", "This installation is not registered.")
    return False


fails = []
with sync_playwright() as playwright:
    central = Central(script)
    browser, page, dialogs, errors = open_central(playwright, central)
    try:
        page.wait_for_timeout(600)
        outcome, text = capture(page, dialogs)
        detects = [call for call in central.calls if call["path"] == "/v1/vision/detect"]
        if outcome != "detail":
            fails.append(f"capture did not recover from a forgotten installation: {outcome} {text!r}")
        if len(registrations) != 2:
            fails.append(f"expected a second registration, saw {len(registrations)}")
        elif registrations[0] == registrations[1]:
            fails.append("the second registration reused the forgotten public key")
        if len(detects) != 2:
            fails.append(f"expected the detect to be resent once, saw {len(detects)}")
        elif detects[1]["headers"].get("x-install-id") != "install-2":
            fails.append(f"the resent detect was not signed by the new install: "
                         f"{detects[1]['headers'].get('x-install-id')}")
        stored = page.evaluate("""() => new Promise((resolve) => {
          const req = indexedDB.open('potholes');
          req.onsuccess = () => {
            const all = req.result.transaction('identity').objectStore('identity').getAll();
            all.onsuccess = () => resolve(all.result.map((row) => row.installId));
          };
        })""")
        if stored != ["install-2"]:
            fails.append(f"the stored identity is not the new install: {stored}")
        if errors:
            fails.append(f"page errors {errors[:3]}")
    finally:
        browser.close()

    # A server that keeps refusing must not loop: one re-registration, then a clear error.
    registrations.clear()
    central = Central(lambda route, request, path, c: script(route, request, path, c) if
                      path == "/v1/installations" else (path == "/v1/vision/detect" and reply(
                          route, 401, "unknown_installation", "This installation is not registered.")))
    browser, page, dialogs, errors = open_central(playwright, central)
    try:
        page.wait_for_timeout(600)
        outcome, text = capture(page, dialogs)
        if outcome != "alert" or "not registered" in (text or ""):
            fails.append(f"a persistent 401 did not end in the app's own message: {outcome} {text!r}")
        if central.count("/v1/vision/detect") != 2 or len(registrations) != 2:
            fails.append(f"a persistent 401 looped: {central.count('/v1/vision/detect')} detects, "
                         f"{len(registrations)} registrations")
    finally:
        browser.close()

if fails:
    print("FAIL central identity recovery")
    for failure in fails:
        print(" -", failure)
    sys.exit(1)
print("PASS central identity recovery")
