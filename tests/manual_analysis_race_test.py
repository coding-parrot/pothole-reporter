# -*- coding: utf-8 -*-
"""Back navigation cannot start a second paid manual analysis in parallel.

The first detector request is held behind a local promise.  Pressing Android Back may
return Home, but another capture remains blocked until that request settles.  A stale
result refreshes History without taking over the screen, after which capture works again.
"""
import json
import os
import sys

from playwright.sync_api import sync_playwright


APP = os.environ.get("POTHOLE_TEST_APP", "http://localhost:8765/")
fails = []
remote_leaks = []


with sync_playwright() as playwright:
    browser = playwright.chromium.launch()
    context = browser.new_context(viewport={"width": 390, "height": 844})

    def block_remote(route):
        url = route.request.url
        if url.startswith(APP) or url.startswith("blob:") or url.startswith("data:"):
            route.continue_()
        elif url == "https://ffjvg34k07.execute-api.ap-south-1.amazonaws.com/v1/health":
            route.fulfill(status=200, content_type="application/json", body=json.dumps({
                "ok": True, "shared_vision_configured": True,
            }))
        else:
            remote_leaks.append(url)
            route.abort()

    context.route("**/*", block_remote)
    page = context.new_page()
    page.goto(APP)
    page.wait_for_load_state("networkidle")
    page.wait_for_function("typeof handleFile === 'function'", timeout=30_000)

    result = page.evaluate(r"""async () => {
      const originalApi = api;
      const originalLoadReports = loadReports;
      const originalGetPosition = getPosition;
      const originalRequestLocation = requestNativeLocationPermission;
      const alerts = [];
      const pending = [];
      let reportCalls = 0;
      let listRefreshes = 0;

      window.alert = (message) => alerts.push(String(message));
      loadReports = async () => { listRefreshes++; };
      getPosition = async () => null;
      requestNativeLocationPermission = async () => true;
      api = async (path, options = {}) => {
        if (path !== "/api/report") return originalApi(path, options);
        reportCalls++;
        return new Promise((resolve, reject) => pending.push({ resolve, reject }));
      };
      const report = (id) => ({
        id, status: "rejected", decision: "reject", assessment: "undamaged",
        image_quality: "acceptable", damage_type: null, description: "No damage.",
        created_at: Date.now() / 1000, photo_url: "",
      });
      const waitFor = async (predicate) => {
        const until = Date.now() + 3000;
        while (!predicate()) {
          if (Date.now() > until) throw new Error("timed out waiting for test state");
          await new Promise((resolve) => setTimeout(resolve, 10));
        }
      };

      let first;
      let third;
      try {
        first = handleFile(new Blob(["first"], { type: "image/jpeg" }));
        await waitFor(() => reportCalls === 1);
        const backHandled = handleAppBack();
        await new Promise((resolve) => setTimeout(resolve, 0));
        const refreshesAfterBack = listRefreshes;
        const homeAfterBack = !document.getElementById("home").classList.contains("hidden");

        await handleFile(new Blob(["second"], { type: "image/jpeg" }));
        const callsWhileFirstPending = reportCalls;

        pending.shift().resolve(report(1));
        await first;
        await new Promise((resolve) => setTimeout(resolve, 0));
        const homeAfterStaleResult = !document.getElementById("home").classList.contains("hidden");
        const refreshesAfterStaleResult = listRefreshes;

        third = handleFile(new Blob(["third"], { type: "image/jpeg" }));
        await waitFor(() => reportCalls === 2);
        pending.shift().resolve(report(2));
        await third;
        const detailAfterNextCapture = !document.getElementById("detail").classList.contains("hidden");

        // Settings opened while a check runs is where the tester is when it lands.
        show("home");
        const fourth = handleFile(new Blob(["fourth"], { type: "image/jpeg" }));
        await waitFor(() => reportCalls === 3);
        document.getElementById("gearBtn").click();
        const refreshesBeforeSettingsResult = listRefreshes;
        pending.shift().resolve(report(3));
        await fourth;
        await new Promise((resolve) => setTimeout(resolve, 0));
        const settingsAfterResult = !document.getElementById("settings").classList.contains("hidden")
          && document.getElementById("detail").classList.contains("hidden");
        return {
          backHandled, homeAfterBack, homeAfterStaleResult,
          callsWhileFirstPending, finalReportCalls: reportCalls,
          refreshesAfterBack, refreshesAfterStaleResult, detailAfterNextCapture,
          settingsAfterResult,
          settingsResultRefreshed: listRefreshes > refreshesBeforeSettingsResult,
          alerts,
        };
      } finally {
        api = originalApi;
        loadReports = originalLoadReports;
        getPosition = originalGetPosition;
        requestNativeLocationPermission = originalRequestLocation;
      }
    }""")
    browser.close()


if remote_leaks:
    fails.append(f"real remote request escaped the deterministic test: {remote_leaks}")
if not result["backHandled"] or not result["homeAfterBack"]:
    fails.append(f"Back did not return Home during analysis: {result}")
if result["callsWhileFirstPending"] != 1:
    fails.append("a second detector request started while the first was pending: "
                 f"{result['callsWhileFirstPending']}")
if not result["homeAfterStaleResult"]:
    fails.append("the stale first result stole the UI after Back")
if result["refreshesAfterStaleResult"] <= result["refreshesAfterBack"]:
    fails.append("the committed stale result did not refresh History")
if not result["settingsAfterResult"] or not result["settingsResultRefreshed"]:
    fails.append(f"a result landing under Settings replaced it or skipped History: {result}")
if result["finalReportCalls"] != 3 or not result["detailAfterNextCapture"]:
    fails.append(f"capture did not recover after the first request settled: {result}")
if not any("still being analysed" in message for message in result["alerts"]):
    fails.append(f"blocked capture gave no useful explanation: {result['alerts']}")

print(f"  detector calls while pending/final: {result['callsWhileFirstPending']}/{result['finalReportCalls']}")
print(f"  list refreshes after Back/stale: {result['refreshesAfterBack']}/{result['refreshesAfterStaleResult']}")
if fails:
    print("\nFAIL")
    for failure in fails:
        print("  -", failure)
    sys.exit(1)
print("\nMANUAL ANALYSIS RACE TEST PASS")
