# -*- coding: utf-8 -*-
"""A fresh install opens on Home and can report a pothole without touching Settings.

The app used to open on a mandatory Settings screen that could not be dismissed. It was
asking every tester to make a choice the app already had a good default for: shared
detection needs no key, and the sender's name is optional. This pins the replacement
contract, including the parts that must not regress with it. Settings still has to be
reachable and dismissible, the key field must appear only for the path that needs one,
and a personal key that is blank must still be refused.
"""

import os
import sys

from playwright.sync_api import sync_playwright


APP = os.environ.get("POTHOLE_TEST_APP", "http://localhost:8765/")


def ui_state(page):
    return page.evaluate(
        """() => ({
          homeVisible: !document.getElementById("home").classList.contains("hidden"),
          settingsVisible: !document.getElementById("settings").classList.contains("hidden"),
          backHidden: document.getElementById("setBack").classList.contains("hidden"),
          keyVisible: !document.getElementById("setKey").classList.contains("hidden"),
          keyLabelVisible: !document.getElementById("keyLabel").classList.contains("hidden"),
          key: localStorage.getItem("openai_key"),
          setup: localStorage.getItem("initial_setup_complete"),
          required: initialSettingsRequired,
          active: initialSettingsActive,
          alerts: window.__firstRunAlerts || [],
        })"""
    )


def wait_until_ready(page):
    page.wait_for_function(
        """() => typeof handleAppBack === "function"
          && typeof initialSettingsRequired === "boolean"
          && document.getElementById("settings")""",
        timeout=30_000,
    )


failures = []
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(args=["--disable-web-security"])

    context = browser.new_context(viewport={"width": 390, "height": 844})
    context.add_init_script(
        """window.__firstRunAlerts = [];
        window.alert = (message) => window.__firstRunAlerts.push(String(message));"""
    )
    page = context.new_page()
    page.goto(APP)
    wait_until_ready(page)
    page.locator("#home").wait_for(state="visible", timeout=30_000)

    fresh = ui_state(page)
    if not fresh["homeVisible"] or fresh["settingsVisible"]:
        failures.append(f"a fresh install did not open on Home: {fresh}")
    if fresh["required"] or fresh["active"]:
        failures.append(f"a fresh install still armed the mandatory Settings guards: {fresh}")

    # The shared detector is the default, so a tester who never opens Settings is already
    # on a working configuration.
    provider = page.evaluate("StandaloneAPI.__pure.effectiveVisionProvider("
                             "localStorage.getItem('vision_provider'), "
                             "localStorage.getItem('openai_key'))")
    if provider != "shared":
        failures.append(f"an untouched install did not default to shared detection: {provider}")

    # Settings is somewhere you choose to go, and you can leave it again.
    page.locator("#gearBtn").click()
    page.locator("#settings").wait_for(state="visible", timeout=30_000)
    opened = ui_state(page)
    if not opened["settingsVisible"] or opened["backHidden"]:
        failures.append(f"Settings opened without a way back: {opened}")
    if opened["keyVisible"] or opened["keyLabelVisible"]:
        failures.append(f"the key field was shown on the shared detector: {opened}")

    handled = page.evaluate("handleAppBack()")
    after_back = ui_state(page)
    if not handled or after_back["settingsVisible"] or not after_back["homeVisible"]:
        failures.append(f"Android Back did not leave Settings: {handled} {after_back}")

    # Choosing the personal path is the only thing that reveals the key field.
    page.locator("#gearBtn").click()
    page.locator("#settings").wait_for(state="visible", timeout=30_000)
    page.select_option("#setProvider", "personal")
    personal = ui_state(page)
    if not personal["keyVisible"] or not personal["keyLabelVisible"]:
        failures.append(f"the key field stayed hidden on the personal path: {personal}")

    # A personal key that is only whitespace is still not a key.
    page.locator("#setKey").fill("   ")
    page.locator("#setSave").click()
    page.wait_for_function("window.__firstRunAlerts.length === 1")
    blank = ui_state(page)
    if not blank["settingsVisible"]:
        failures.append(f"a blank personal key was accepted: {blank}")
    if not blank["alerts"] or "key" not in blank["alerts"][0].lower():
        failures.append(f"a blank key did not explain the requirement: {blank}")

    # Saving on the shared path needs no key and returns to Home.
    page.select_option("#setProvider", "shared")
    page.locator("#setSave").click()
    page.locator("#home").wait_for(state="visible", timeout=30_000)
    saved = ui_state(page)
    if saved["settingsVisible"] or not saved["homeVisible"]:
        failures.append(f"Save did not return to Home: {saved}")
    if saved["key"]:
        failures.append(f"the shared path stored a key: {saved}")

    page.reload()
    wait_until_ready(page)
    page.locator("#home").wait_for(state="visible", timeout=30_000)
    reloaded = ui_state(page)
    if reloaded["settingsVisible"] or not reloaded["homeVisible"]:
        failures.append(f"a reload did not return to Home: {reloaded}")
    context.close()

    # An existing install carrying old settings still opens on Home rather than being
    # pushed back through onboarding by an update.
    context = browser.new_context(viewport={"width": 390, "height": 844})
    context.add_init_script(
        """localStorage.setItem("openai_key", "legacy-key-never-sent");"""
    )
    page = context.new_page()
    page.goto(APP)
    wait_until_ready(page)
    page.locator("#home").wait_for(state="visible", timeout=30_000)
    migrated = ui_state(page)
    if migrated["settingsVisible"] or not migrated["homeVisible"]:
        failures.append(f"an existing install was blocked by onboarding: {migrated}")
    context.close()
    browser.close()

if failures:
    print("FIRST RUN TEST FAIL")
    for failure in failures:
        print(" -", failure)
    sys.exit(1)
print("FIRST RUN TEST PASS")
