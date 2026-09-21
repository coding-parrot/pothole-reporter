#!/usr/bin/env python3
"""Concurrent storage callers must share one IndexedDB connection.

Boot fires several reads at once. When each opened its own connection only the last one
got the versionchange handler, and the leaked ones blocked any later schema upgrade.
"""

import sys

from playwright.sync_api import sync_playwright

from browser_test_utils import open_app


COUNT_OPENS = """
(() => {
  const open = IDBFactory.prototype.open;
  window.__potholeOpens = 0;
  IDBFactory.prototype.open = function (name, ...rest) {
    if (name === "potholes" && rest.length) window.__potholeOpens += 1;
    return open.call(this, name, ...rest);
  };
})();
"""

failures = []
with sync_playwright() as playwright:
    browser = playwright.chromium.launch()
    context = browser.new_context()
    context.add_init_script(COUNT_OPENS)
    page = context.new_page()
    open_app(page, "test-key-never-sent")
    # Boot renders History, which reads reports, drives and footage at the same time.
    result = page.evaluate("() => ({opens: window.__potholeOpens})")
    other = context.new_page()
    other.goto(page.url + "privacy.html" if page.url.endswith("/") else page.url)
    upgrade = other.evaluate(
        """() => new Promise((resolve) => {
          const started = Date.now();
          const request = indexedDB.open("potholes", 9);
          let blocked = false;
          request.onblocked = () => { blocked = true; };
          request.onsuccess = () => {
            request.result.close();
            resolve({ok: true, blocked, ms: Date.now() - started});
          };
          request.onerror = () => resolve({ok: false, error: String(request.error)});
          setTimeout(() => resolve({ok: false, blocked, timeout: true}), 2000);
        })"""
    )
    context.close()
    browser.close()

if result["opens"] != 1:
    failures.append(f"boot opened {result['opens']} connections, expected 1")
if not upgrade.get("ok"):
    failures.append(f"a later schema upgrade was blocked by leaked connections: {upgrade}")

if failures:
    print("IDB SINGLE CONNECTION TEST FAIL")
    for failure in failures:
        print(" -", failure)
    sys.exit(1)
print("IDB SINGLE CONNECTION TEST PASS")
