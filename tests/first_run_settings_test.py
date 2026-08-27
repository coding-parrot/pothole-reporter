# -*- coding: utf-8 -*-
"""A fresh install must complete Settings before Home can be used."""

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

    # A truly fresh origin must render Settings, never Home. The mandatory screen cannot
    # be dismissed with Android Back and cannot be completed with a blank API key.
    context = browser.new_context(viewport={"width": 390, "height": 844})
    context.add_init_script(
        """window.__firstRunAlerts = [];
        window.alert = (message) => window.__firstRunAlerts.push(String(message));"""
    )
    page = context.new_page()
    page.goto(APP)
    wait_until_ready(page)
    page.locator("#settings").wait_for(state="visible")

    fresh = ui_state(page)
    if not fresh["settingsVisible"] or fresh["homeVisible"]:
        failures.append(f"fresh install did not lead with Settings: {fresh}")
    if not fresh["required"] or not fresh["active"] or not fresh["backHidden"]:
        failures.append(f"fresh Settings was not mandatory: {fresh}")
    if fresh["setup"] is not None:
        failures.append(f"fresh install was marked complete before Save: {fresh}")

    handled = page.evaluate("handleAppBack()")
    after_back = ui_state(page)
    if not handled or not after_back["settingsVisible"] or after_back["homeVisible"]:
        failures.append(f"Android Back escaped mandatory Settings: {after_back}")

    page.locator("#setKey").fill("   ")
    page.locator("#setSave").click()
    page.wait_for_function("window.__firstRunAlerts.length === 1")
    blank = ui_state(page)
    if not blank["settingsVisible"] or blank["homeVisible"] or blank["setup"] is not None:
        failures.append(f"blank key completed first-run Settings: {blank}")
    if not blank["alerts"] or "key" not in blank["alerts"][0].lower():
        failures.append(f"blank key did not explain the requirement: {blank}")

    # Saving a key completes onboarding and persists that decision across a reload.
    page.locator("#setKey").fill("test-key-never-sent")
    page.locator("#setSave").click()
    page.locator("#home").wait_for(state="visible", timeout=30_000)
    saved = ui_state(page)
    if saved["settingsVisible"] or not saved["homeVisible"]:
        failures.append(f"valid Save did not open Home: {saved}")
    if saved["key"] != "test-key-never-sent" or saved["setup"] != "1":
        failures.append(f"valid Save did not persist onboarding: {saved}")
    if saved["required"] or saved["active"]:
        failures.append(f"valid Save left first-run guards active: {saved}")

    page.reload()
    wait_until_ready(page)
    page.locator("#home").wait_for(state="visible", timeout=30_000)
    reloaded = ui_state(page)
    if reloaded["settingsVisible"] or not reloaded["homeVisible"]:
        failures.append(f"completed onboarding was shown again after reload: {reloaded}")
    context.close()

    # Existing users predate the completion marker. A saved legacy setting must migrate
    # silently so an app update does not block them behind first-run onboarding.
    context = browser.new_context(viewport={"width": 390, "height": 844})
    context.add_init_script(
        """localStorage.setItem("openai_key", "legacy-key-never-sent");
        if (localStorage.getItem("initial_setup_complete") !== null) {
          throw new Error("legacy fixture unexpectedly has the new marker");
        }"""
    )
    page = context.new_page()
    page.goto(APP)
    wait_until_ready(page)
    page.locator("#home").wait_for(state="visible", timeout=30_000)
    migrated = ui_state(page)
    if migrated["settingsVisible"] or not migrated["homeVisible"]:
        failures.append(f"legacy install was blocked by first-run Settings: {migrated}")
    if migrated["setup"] != "1" or migrated["required"] or migrated["active"]:
        failures.append(f"legacy install was not migrated to the completion marker: {migrated}")
    if migrated["key"] != "legacy-key-never-sent":
        failures.append(f"legacy migration altered the saved key: {migrated}")
    context.close()

    browser.close()

if failures:
    print("FAIL")
    for failure in failures:
        print("  -", failure)
    sys.exit(1)

print("FIRST-RUN SETTINGS TEST PASS")
