# -*- coding: utf-8 -*-
"""A fresh install must reach Home on first launch, and Settings must save without error.

A fresh install now lands on Home instead of an onboarding form. Settings is still the
screen with the worst history here: v1.38.1 threw "trimmedKey is not defined" the moment
a tester tapped Save, so nobody could enter the app at all. Opening it and saving is
therefore still exercised, just as a place a tester chooses to visit.
"""

import sys

from playwright.sync_api import sync_playwright

from flow_harness import error_failures, open_flow

fails = []
with sync_playwright() as playwright:
    browser, page, errors = open_flow(playwright, fresh=True)

    page.wait_for_function("() => typeof window.openSettings === 'function'", timeout=30_000)
    page.locator("#home").wait_for(state="visible", timeout=30_000)
    fails += error_failures(errors, "first launch")

    state = page.evaluate("""() => ({
      home: !document.getElementById("home").classList.contains("hidden"),
      settings: !document.getElementById("settings").classList.contains("hidden"),
      exits: window.__exitAppCalls,
    })""")
    if not state["home"] or state["settings"]:
        fails.append(f"fresh install did not open on Home: {state}")
    if state["exits"]:
        fails.append(f"first launch closed the app {state['exits']} time(s)")

    # Opening Settings is a choice. The shared service is the default and shows no key
    # field, so there is nothing a tester has to fill in.
    errors.clear()
    page.locator("#gearBtn").click()
    page.locator("#settings").wait_for(state="visible", timeout=30_000)
    opened = page.evaluate("""() => ({
      provider: document.getElementById("setProvider").value,
      keyHidden: document.getElementById("setKey").classList.contains("hidden"),
      backHidden: document.getElementById("setBack").classList.contains("hidden"),
    })""")
    if opened["provider"] != "shared":
        fails.append(f"a fresh install must default to the no-key shared service: {opened}")
    if not opened["keyHidden"]:
        fails.append(f"the API key field must be hidden in shared mode: {opened}")
    if opened["backHidden"]:
        fails.append(f"Settings must be dismissible: {opened}")

    # Saving with no API key is the whole point of the shared default.
    page.locator("#setSave").click()
    page.locator("#home").wait_for(state="visible", timeout=30_000)
    fails += error_failures(errors, "saving settings")

    saved = page.evaluate("""() => ({
      home: !document.getElementById("home").classList.contains("hidden"),
      provider: localStorage.getItem("vision_provider"),
      exits: window.__exitAppCalls,
    })""")
    if not saved["home"]:
        fails.append(f"Save did not return to Home: {saved}")
    if saved["exits"]:
        fails.append(f"saving settings closed the app {saved['exits']} time(s)")

    # The decision must survive a restart, and the restart must be clean.
    errors.clear()
    page.reload()
    page.wait_for_function("() => !!window.StandaloneAPI", timeout=30_000)
    page.locator("#home").wait_for(state="visible", timeout=30_000)
    fails += error_failures(errors, "relaunch")

    # Reopening Settings and saving again must not throw either.
    errors.clear()
    page.evaluate("openSettings()")
    page.locator("#settings").wait_for(state="visible", timeout=15_000)
    page.locator("#setName").fill("Test Citizen")
    page.locator("#setSave").click()
    page.locator("#home").wait_for(state="visible", timeout=30_000)
    fails += error_failures(errors, "second settings save")
    if page.evaluate("localStorage.getItem('sender_name')") != "Test Citizen":
        fails.append("the name entered in Settings was not saved")

    browser.close()

if fails:
    print("FAIL flow_signup")
    for failure in fails:
        print(" -", failure)
    sys.exit(1)
print("PASS flow_signup")
