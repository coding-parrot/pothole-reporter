# -*- coding: utf-8 -*-
"""Potholes found and not yet sent get a reminder on the phone.

The owner (14 Oct 2026): "if the app notes that they detected potholes and haven't filed
complaints, they should be nudged to do so." Most phones used the app once and never came
back, with finds left unsent. The phone schedules the reminder itself (Capacitor local
notifications): nothing is sent to any server.

The rules, checked on the planner with India's clock:
  - only a found pothole whose email was never opened ("draft") counts; one whose mail
    app was opened, one already sent, one with nobody to send it to, and a repeat of a
    known pothole do not;
  - the reminder comes an hour after the newest unsent find, so never during a drive;
  - never between 21:00 and 09:00: an evening find is reminded at 09:00 next morning;
  - a second reminder a day after the first, then none for the same finds;
  - a newer find starts again, but still not within a day of the last reminder shown.

And on the phone, with the plugin stubbed:
  - a new unsent find asks once for permission (not during a drive) and schedules one
    reminder that says how many;
  - opening its email cancels the reminder;
  - permission refused: nothing is scheduled and it is not asked again;
  - the website, which has no plugin, behaves as before.
"""

import json
import sys
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

import flow_harness as fh
from central_stub_harness import routed
from flow_harness import error_failures, open_flow, report_form_script

IST = 330
HOUR = 3600 * 1000
# 14 Oct 2026, 10:00 IST
TEN_AM = 1791952200 * 1000


def at_ist(ms):
    import datetime
    t = datetime.datetime.utcfromtimestamp(ms / 1000) + datetime.timedelta(minutes=IST)
    return t.strftime("%d %H:%M")


PLANNER_CASES = r"""
([tenAm, IST]) => {
  const P = StandaloneAPI.__pure;
  const s = (ms) => ms / 1000;
  const draft = (createdMs, extra = {}) => ({ status: "draft", created_at: s(createdMs), ...extra });
  const out = {};
  out.none = P.planUnreportedReminder([], tenAm, null, IST);
  out.notCounted = P.planUnreportedReminder([
    draft(tenAm, { status: "queued" }), draft(tenAm, { status: "sent" }),
    draft(tenAm, { status: "unrouted" }), draft(tenAm, { status: "duplicate" })], tenAm, null, IST);
  const finds = [draft(tenAm - 5 * 60e3), draft(tenAm - 20 * 60e3), draft(tenAm, { status: "sent" })];
  out.first = P.planUnreportedReminder(finds, tenAm, null, IST);
  out.evening = P.planUnreportedReminder([draft(tenAm + 11.5 * 3600e3)], tenAm + 11.6 * 3600e3, null, IST);
  // The first reminder's time has passed: it was shown.
  const afterFirst = P.planUnreportedReminder(finds, tenAm + 2 * 3600e3, out.first.record, IST);
  out.second = afterFirst;
  out.third = P.planUnreportedReminder(finds, afterFirst.schedule ? afterFirst.schedule.at + 60e3 : 0,
    afterFirst.record, IST);
  // A newer find three hours on, after the first reminder was shown.
  out.newer = P.planUnreportedReminder([...finds, draft(tenAm + 3 * 3600e3)], tenAm + 3 * 3600e3,
    afterFirst.record, IST);
  return out;
}
"""

LOCAL_NOTIFICATIONS = r"""
(() => {
  window.__ln = { schedule: [], cancel: [], asked: 0, state: localStorage.getItem("test_ln_state") || "prompt" };
  const LN = {
    async checkPermissions() { return { display: window.__ln.state }; },
    async requestPermissions() {
      window.__ln.asked += 1;
      window.__ln.state = localStorage.getItem("test_ln_answer") || "granted";
      return { display: window.__ln.state };
    },
    async schedule(options) { window.__ln.schedule.push(JSON.parse(JSON.stringify(options))); return {}; },
    async cancel(options) { window.__ln.cancel.push(JSON.parse(JSON.stringify(options))); },
    addListener() { return { remove() {} }; },
  };
  // The flow harness defines window.Capacitor in its own init script; add the plugin
  // as soon as it exists, whichever script runs first.
  const add = () => {
    if (!(window.Capacitor && window.Capacitor.Plugins)) return false;
    window.Capacitor.Plugins.LocalNotifications = LN;
    return true;
  };
  if (!add()) {
    const timer = setInterval(() => { if (add()) clearInterval(timer); }, 0);
    document.addEventListener("DOMContentLoaded", add);
  }
})();
"""


def service(route, request):
    path = urlparse(request.url).path
    # A body the shipped pack has an address for, so the find is a draft that can be sent.
    if routed(route, request, path, None):
        return None
    return fh.central_service(route, request)


def settle(page):
    page.evaluate("loadReports()")
    page.wait_for_timeout(700)
    return page.evaluate("() => ({ ...window.__ln })")


def phone(playwright, answer="granted"):
    browser, page, errors = open_flow(playwright, storage={"test_ln_answer": answer})
    page.add_init_script(LOCAL_NOTIFICATIONS)
    page.route(f"{fh.SERVICE}/**", service)
    page.reload()
    page.locator("#home").wait_for(state="visible", timeout=30_000)
    page.wait_for_function("() => !!(window.StandaloneAPI && window.openDetail)", timeout=30_000)
    return browser, page, errors


fails = []
with sync_playwright() as playwright:
    browser, page, errors = phone(playwright)

    # ---------- the rules ----------
    if not page.evaluate("() => typeof StandaloneAPI.__pure.planUnreportedReminder === 'function'"):
        fails.append("there is no planner for the unsent-find reminder")
    else:
        plan = page.evaluate(PLANNER_CASES, [TEN_AM, IST])
        if not plan["none"].get("cancel"):
            fails.append(f"no unsent finds should mean no reminder: {plan['none']}")
        if not plan["notCounted"].get("cancel"):
            fails.append(f"opened, sent, unaddressed and repeat finds should not be reminded of: {plan['notCounted']}")
        first = plan["first"].get("schedule") or {}
        if first.get("count") != 2 or first.get("at") != TEN_AM - 5 * 60_000 + HOUR:
            fails.append(f"two unsent finds, newest at 09:55, should be reminded once at 10:55 saying 2: "
                         f"{first.get('count')} at {at_ist(first.get('at') or 0)}")
        evening = plan["evening"].get("schedule") or {}
        if at_ist(evening.get("at") or 0) != "15 09:00":
            fails.append(f"a find at 21:30 should be reminded at 09:00 next morning, not {at_ist(evening.get('at') or 0)}")
        second = plan["second"].get("schedule") or {}
        if second.get("at") != TEN_AM - 5 * 60_000 + HOUR + 24 * HOUR:
            fails.append(f"the second reminder should be a day after the first: {at_ist(second.get('at') or 0)}")
        if not plan["third"].get("cancel"):
            fails.append(f"after two reminders for the same finds there should be no third: {plan['third']}")
        newer = plan["newer"].get("schedule") or {}
        if newer.get("count") != 3 or newer.get("at") != TEN_AM - 5 * 60_000 + HOUR + 24 * HOUR:
            fails.append("a newer find should start again, but not within a day of the last reminder shown: "
                         f"{newer.get('count')} at {at_ist(newer.get('at') or 0)}")

    # ---------- on the phone ----------
    errors.clear()
    if page.evaluate("() => typeof drive === 'undefined' ? 'missing' : 'ok'") == "ok":
        # During a drive the permission sheet must not appear.
        page.evaluate("() => { drive = { native: true, sessionId: 'test' }; }")
        report = page.evaluate(report_form_script())
        during = settle(page)
        if during["asked"]:
            fails.append("the notification permission was asked for during a drive")
        page.evaluate("() => { drive = null; }")
    else:
        report = page.evaluate(report_form_script())
    if not report or report.get("status") != "draft":
        fails.append(f"the harness did not produce an unsent find: {report and report.get('status')}")
    state = settle(page)
    if state["asked"] != 1:
        fails.append(f"after the drive the permission should be asked for once, not {state['asked']} times")
    if not state["schedule"]:
        fails.append("no reminder was scheduled for an unsent find")
    else:
        last = state["schedule"][-1]["notifications"][0]
        text = f"{last.get('title', '')} {last.get('body', '')}"
        if "1" not in text:
            fails.append(f"the reminder does not say how many finds are unsent: {text!r}")
        if not last.get("schedule", {}).get("at"):
            fails.append(f"the reminder has no time: {last}")
    scheduled = len(state["schedule"])
    settle(page)
    again = settle(page)
    if again["asked"] != 1:
        fails.append("the permission was asked for again")

    # Opening the email: the reminder is cancelled and none is put back.
    page.evaluate("openDetail(window.__flowReport, [window.__flowReport])")
    page.locator("#detail").wait_for(state="visible", timeout=30_000)
    cancels_before = len(again["cancel"])
    if page.locator("#detail #sendBtn").count() == 1:
        page.locator("#detail #sendBtn").click()
        page.wait_for_function("() => (window.__composerCalls || []).length > 0", timeout=30_000)
        page.wait_for_timeout(500)
        after = settle(page)
        if len(after["cancel"]) <= cancels_before:
            fails.append("opening the email did not cancel the reminder")
        if len(after["schedule"]) > len(again["schedule"]):
            fails.append("a reminder was scheduled again after the email was opened")
    else:
        fails.append("the find has no Email action in this harness")
    fails += error_failures(errors, "the reminder on the phone")
    browser.close()

    # ---------- permission refused ----------
    browser, page, errors = phone(playwright, answer="denied")
    page.evaluate(report_form_script())
    refused = settle(page)
    settle(page)
    refused = settle(page)
    if refused["schedule"]:
        fails.append("a reminder was scheduled after the person refused notifications")
    if refused["asked"] != 1:
        fails.append(f"after a refusal the permission should not be asked again: asked {refused['asked']} times")
    fails += error_failures(errors, "permission refused")
    browser.close()

    # ---------- the website ----------
    browser, page, errors = open_flow(playwright, native=False)
    page.route(f"{fh.SERVICE}/**", service)
    page.locator("#home").wait_for(state="visible", timeout=30_000)
    page.evaluate(report_form_script())
    page.evaluate("loadReports()")
    page.wait_for_timeout(700)
    fails += error_failures(errors, "the website, which has no notifications")
    browser.close()

if fails:
    print("FAIL")
    for fail in fails:
        print("  -", fail)
    sys.exit(1)
print("UNREPORTED REMINDER TEST PASS")
