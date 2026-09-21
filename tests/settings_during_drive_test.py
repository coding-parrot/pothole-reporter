# -*- coding: utf-8 -*-
"""Settings opened during a web Drive must lead back to that drive, never strand it.

Two ways it went wrong:
  - the gear hid the live camera, Back and Save went to Home with the camera still
    running, the Drive button showed no active dot and a tap on it did nothing, so
    nothing on screen could stop the drive;
  - Delete all ran while the drive was recording, which the native path refuses.
"""

import sys

from playwright.sync_api import sync_playwright

from flow_harness import error_failures, open_flow


failures = []


def check(ok, message):
    if not ok:
        failures.append(message)


STATE = """() => ({
  screens: ["home", "drive", "settings"].filter(
    (id) => !document.getElementById(id).classList.contains("hidden")),
  drive: !!drive,
  track: (() => {
    const stream = document.getElementById("driveVideo").srcObject;
    const track = stream && stream.getVideoTracks()[0];
    return track ? track.readyState : null;
  })(),
  driveLabel: document.getElementById("driveBtn").getAttribute("aria-label"),
})"""


def start_web_drive(page):
    page.evaluate("window.alert = (m) => (window.__alerts = window.__alerts || []).push(String(m));")
    page.locator("#driveBtn").click()
    page.wait_for_function("() => !!drive && !drive.native && !!document.getElementById("
                           "'driveVideo').srcObject", timeout=15_000)
    page.wait_for_timeout(300)


with sync_playwright() as p:
    # Back and Save from Settings return to the live drive; the Drive button reopens it.
    browser, page, errors = open_flow(p, native=False)
    try:
        start_web_drive(page)
        page.locator("#gearBtn").click()
        state = page.evaluate(STATE)
        check(state["screens"] == ["settings"], f"gear did not open Settings: {state}")
        check(state["driveLabel"], f"Drive button shows no active drive: {state}")
        page.locator("#setBack").click()
        state = page.evaluate(STATE)
        check(state["screens"] == ["drive"] and state["track"] == "live",
              f"Back from Settings did not return to the live drive: {state}")

        page.locator("#gearBtn").click()
        page.locator("#setSave").click()
        page.wait_for_function("() => document.getElementById('settings')"
                               ".classList.contains('hidden')", timeout=10_000)
        state = page.evaluate(STATE)
        check(state["screens"] == ["drive"] and state["track"] == "live",
              f"Save from Settings did not return to the live drive: {state}")

        # Home is still reachable through a script path (a deep link or a stale screen);
        # from there the Drive button must open the drive, not ignore the tap.
        page.evaluate("show('home')")
        page.locator("#driveBtn").click()
        page.wait_for_timeout(200)
        state = page.evaluate(STATE)
        check(state["screens"] == ["drive"], f"Drive tap during a drive did not open it: {state}")

        # Hardware Back in Settings leaves Settings; it must not end the drive behind it.
        page.locator("#gearBtn").click()
        page.evaluate("window.handleAppBack()")
        state = page.evaluate(STATE)
        check(state["screens"] == ["drive"] and state["drive"],
              f"hardware Back in Settings did not return to the drive: {state}")

        # Before the fix the drive screen was unreachable; reach it by script so the
        # remaining checks still run and the camera is released.
        if page.evaluate("!!drive"):
            if not page.locator("#driveStop").is_visible():
                page.evaluate("show('drive')")
            page.locator("#driveStop").click()
            page.wait_for_function("() => !drive", timeout=15_000)
        page.wait_for_timeout(300)
        state = page.evaluate(STATE)
        check(state["track"] in (None, "ended"), f"Stop did not end the camera: {state}")
        check(not state["driveLabel"], f"Drive button still shows a drive after Stop: {state}")
        failures.extend(error_failures(errors, "settings during drive"))
    finally:
        browser.close()

    # Delete all is refused while the drive records; footage and the drive survive.
    browser, page, errors = open_flow(p, native=False)
    try:
        start_web_drive(page)
        page.evaluate("""() => {
          // A wipe ends in a reload, which drops this marker.
          window.__samePage = true;
          window.confirm = () => true;
        }""")
        page.locator("#gearBtn").click()
        page.locator("#wipeBtn").click()
        page.wait_for_timeout(1500)
        result = page.evaluate("""() => ({
          samePage: window.__samePage === true, alerts: window.__alerts || [],
          drive: !!drive,
        })""")
        check(result["samePage"] and result["drive"],
              f"Delete all ran during a web Drive: {result}")
        check(any("stop" in alert.lower() for alert in result["alerts"]),
              f"Delete all during a drive gave no reason: {result}")
        failures.extend(error_failures(errors, "wipe during drive"))
    finally:
        browser.close()

if failures:
    print("FAIL")
    for failure in failures:
        print(" -", failure)
    sys.exit(1)
print("PASS: Settings during a web Drive returns to it, and Delete all waits for Stop")
