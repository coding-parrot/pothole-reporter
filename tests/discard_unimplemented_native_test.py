#!/usr/bin/env python3
"""Deleting a report must not end in a developer alert when the native side has nothing to clean.

The wired Android DriveMode plugin has no clearRepairTarget. Capacitor's registerPlugin
returns a Proxy whose every property is a function, so a typeof check cannot tell, and
the call rejects with UNIMPLEMENTED. That must stay silent; a real cleanup failure must
still be reported.
"""

import sys

from playwright.sync_api import sync_playwright

from flow_harness import error_failures, open_flow, report_form_script


# Mirrors @capacitor/core registerPlugin: an unimplemented method exists as a function
# and rejects with code UNIMPLEMENTED.
PROXY = r"""
(realFailure) => {
  const base = window.Capacitor.Plugins.DriveMode;
  window.Capacitor.Plugins.DriveMode = new Proxy(base, { get(target, prop) {
    if (typeof prop !== "string" || prop in target) return target[prop];
    if (prop === "clearRepairTarget" && realFailure) {
      return async () => { throw new Error("simulated repair cache failure"); };
    }
    return async () => {
      const e = new Error(`"DriveMode.${prop}()" is not implemented on android`);
      e.code = "UNIMPLEMENTED";
      throw e;
    };
  } });
}
"""


def delete_one(playwright, real_failure):
    browser, page, errors = open_flow(playwright)
    dialogs = []
    try:
        report = page.evaluate(report_form_script())
        page.evaluate(PROXY, real_failure)
        page.evaluate("""async (id) => {
          const rows = await StandaloneAPI.handle('/api/reports');
          const row = rows.find((r) => r.id === id);
          openDetail(row, rows);
        }""",
                      report["id"])
        page.on("dialog", lambda d: (dialogs.append((d.type, d.message)), d.accept()))
        page.click("#discardBtn")
        page.wait_for_function("() => !document.getElementById('home').classList.contains('hidden')")
        page.wait_for_timeout(300)
        left = page.evaluate("async () => (await StandaloneAPI.handle('/api/reports')).length")
    finally:
        browser.close()
    # The unimplemented rejection is expected noise in the stub, not an app error.
    return dialogs, left, errors


failures = []
with sync_playwright() as playwright:
    dialogs, left, errors = delete_one(playwright, real_failure=False)
    alerts = [message for kind, message in dialogs if kind == "alert"]
    if left != 0:
        failures.append(f"report was not deleted: {left} left")
    if alerts:
        failures.append(f"unimplemented native cleanup raised an alert: {alerts}")
    failures += error_failures(errors, "unimplemented cleanup")

    dialogs, left, errors = delete_one(playwright, real_failure=True)
    alerts = [message for kind, message in dialogs if kind == "alert"]
    if left != 0:
        failures.append(f"report was not deleted when cleanup failed: {left} left")
    if len(alerts) != 1 or "simulated repair cache failure" not in alerts[0]:
        failures.append(f"a real native cleanup failure was not reported once: {alerts}")
    elif "retried later" in alerts[0]:
        failures.append(f"alert promises a retry that does not exist: {alerts[0]}")

if failures:
    print("DISCARD UNIMPLEMENTED NATIVE TEST FAIL")
    for failure in failures:
        print(" -", failure)
    sys.exit(1)
print("DISCARD UNIMPLEMENTED NATIVE TEST PASS")
