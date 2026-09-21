# -*- coding: utf-8 -*-
"""A report must be created, listed, opened and handed to the email app, cleanly.

The rendering helpers for the history list and the detail screen are exactly where
deleted functions hid: a missing helper throws while drawing the screen, so the tester
sees a blank or frozen page. Every screen here is rendered and the console watched.
"""

import sys

from playwright.sync_api import sync_playwright

from flow_harness import error_failures, open_flow, report_form_script

fails = []
with sync_playwright() as playwright:
    browser, page, errors = open_flow(playwright)
    page.locator("#home").wait_for(state="visible", timeout=30_000)
    page.wait_for_function("() => !!(window.StandaloneAPI && window.openDetail)", timeout=30_000)

    errors.clear()
    report = page.evaluate(report_form_script())
    fails += error_failures(errors, "creating a report")
    if not report or not report.get("id"):
        fails.append(f"no report was created: {report}")
    if report.get("status") not in ("draft", "unrouted"):
        fails.append(f"an accepted report ended in an unexpected state: {report.get('status')}")

    # The history list draws every report, including its chips.
    errors.clear()
    page.evaluate("loadReports()")
    page.wait_for_function("() => document.querySelectorAll('#list .card, #list .report').length > 0"
                           " || document.getElementById('list').innerText.length > 0",
                           timeout=30_000)
    page.wait_for_timeout(300)
    fails += error_failures(errors, "rendering the report list")

    listed = page.evaluate("""() => ({
      text: document.getElementById("list").innerText.slice(0, 400),
      empty: document.getElementById("list").innerText.trim().length === 0,
    })""")
    if listed["empty"]:
        fails.append("the history list rendered nothing after a report was created")

    # The detail screen is the one with the complaint action on it.
    errors.clear()
    page.evaluate("openDetail(window.__flowReport, [window.__flowReport])")
    page.locator("#detail").wait_for(state="visible", timeout=30_000)
    page.wait_for_timeout(300)
    fails += error_failures(errors, "opening report detail")

    detail = page.evaluate("""() => ({
      visible: !document.getElementById("detail").classList.contains("hidden"),
      text: document.getElementById("detail").innerText.slice(0, 400),
      sendButtons: document.querySelectorAll("#detail #sendBtn").length,
    })""")
    if not detail["visible"] or not detail["text"].strip():
        fails.append(f"the detail screen rendered empty: {detail}")
    if "{" in detail["text"] or "}" in detail["text"]:
        fails.append(f"an untranslated placeholder reached the screen: {detail['text'][:120]}")

    routed = bool(report.get("officer_email"))
    if routed:
        # A routed report has exactly one complaint action, and it hands a fully
        # addressed draft to the mail app without sending anything itself.
        if detail["sendButtons"] != 1:
            fails.append(f"expected exactly one complaint action, saw {detail['sendButtons']}")
        else:
            errors.clear()
            page.locator("#detail #sendBtn").click()
            page.wait_for_function("() => (window.__composerCalls || []).length > 0", timeout=30_000)
            fails += error_failures(errors, "handing off to the email app")
            composed = page.evaluate("() => window.__composerCalls[0]")
            if not composed or "commissioner@example.gov.in" not in str(composed.get("to")):
                fails.append(f"the draft was not addressed to the routed authority: {composed}")
    else:
        # Without coverage the app must say so instead of offering a dead button.
        if detail["sendButtons"]:
            fails.append("an unrouted report offered a complaint action")
        if report.get("status") != "unrouted" or not report.get("unrouted_reason"):
            fails.append(f"an unroutable report was not marked unrouted: {report.get('status')}")

    # A failed patch is road damage, not a pothole: the verdict above its chip must
    # not call it one.
    page.evaluate("""() => {
      const patch = { ...window.__flowReport, id: 987653, damage_type: "failed_patch" };
      openDetail(patch, [patch]);
    }""")
    page.wait_for_timeout(300)
    verdict = page.evaluate(
        "[...document.querySelectorAll('#detail .verdict')].map((n) => n.innerText).join(' | ')")
    if "Road damage: YES" not in verdict:
        fails.append(f"a failed patch detail shows no road-damage verdict: {verdict[:120]}")
    if "Pothole" in verdict:
        fails.append(f"a failed patch is announced as a pothole: {verdict[:120]}")

    # An unrouted report still has to render: this path uses the help text helpers.
    errors.clear()
    page.evaluate("""() => {
      const unrouted = { ...window.__flowReport, id: 987654, status: "unrouted",
                         officer_email: null, officer_name: null,
                         unrouted_reason: "no_address" };
      openDetail(unrouted, [unrouted]);
    }""")
    page.wait_for_timeout(300)
    fails += error_failures(errors, "rendering an unrouted report")
    unrouted_text = page.evaluate("document.getElementById('detail').innerText.trim().length")
    if not unrouted_text:
        fails.append("an unrouted report rendered an empty detail screen")

    if page.evaluate("window.__exitAppCalls"):
        fails.append("the reporting flow closed the app")

    browser.close()

if fails:
    print("FAIL flow_report")
    for failure in fails:
        print(" -", failure)
    sys.exit(1)
print("PASS flow_report")
