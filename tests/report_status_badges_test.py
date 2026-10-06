# -*- coding: utf-8 -*-
"""Every detected pothole says where it got to: saved on the phone, sent to the service,
failed, or drafted as an email.

A tester wrote after a real drive: "Live recording detects multiple potholes, but there
is no way to track which are submitted vs pending." The Drive screen counted potholes
and the Home list wore one "Draft" chip on all of them, whether the shared-map write
had landed, was waiting in the outbox for a connection, or had been refused. Here the
project service drops the write, then answers, then refuses with a 503, and the Drive
tally, the end summary and the Home chips are read after each.
"""

import json
import sys
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

import flow_harness as fh
from central_stub_harness import Central, capture, open_central, reply, routed
from web_drive_harness import open_web_drive, wait_for_dialog

mode = {"report": "offline"}


def service(route, request):
    if urlparse(request.url).path == "/v1/potholes/report" and mode["report"] == "offline":
        # The phone has no connection: the request never reaches the service.
        return route.abort("connectionfailed")
    return fh.central_service(route, request)


def tally_key(page, key, n):
    return page.evaluate("([key, n]) => t(key, { n })", [key, n])


def hud_tally(page):
    return page.evaluate("""() => {
      const el = document.getElementById('driveStates');
      return el ? el.textContent.trim() : null;
    }""")


def home_chips(page):
    page.evaluate("() => loadReports()")
    page.wait_for_timeout(600)
    return page.evaluate("""() => {
      return [...document.querySelectorAll('#list .card.row')].map((card) => ({
        id: card.dataset.id,
        chip: card.querySelector('.chip') ? card.querySelector('.chip').textContent : '' }));
    }""")


fails = []
with sync_playwright() as playwright:
    # ---------- a drive while offline, then the outbox flushes ----------
    browser, page, dialogs, errors = open_web_drive(playwright, service=service,
                                                    stub_frames=False)
    try:
        page.evaluate("() => { window.__centralRetryDelayMs = 400; }")
        page.locator("#driveBtn").click()
        page.wait_for_function("() => drive && drive.tally.found >= 1", timeout=90_000)
        page.wait_for_timeout(1200)
        saved_one = tally_key(page, "hud_tally_saved", 1)
        tally = hud_tally(page)
        if tally is None:
            fails.append("the Drive screen has no per-state tally line (#driveStates)")
        elif saved_one.split("1")[-1].strip() not in tally:
            fails.append(f"an event saved while offline is not counted as saved: {tally!r}")
        sent_word = tally_key(page, "hud_tally_sent", 1).split("1")[-1].strip()
        if tally and sent_word and sent_word in tally.replace(saved_one, ""):
            fails.append(f"an event the service never received is counted as sent: {tally!r}")

        mode["report"] = "ok"
        page.evaluate("() => StandaloneAPI.__pure.flushCentralOutbox()")
        page.wait_for_function("""() => {
          const el = document.getElementById('driveStates');
          return el && /\\d/.test(el.textContent) && !el.textContent.includes(%s);
        }""" % json.dumps(saved_one.split("1")[-1].strip()), timeout=30_000)
        tally = hud_tally(page)
        if sent_word not in (tally or ""):
            fails.append(f"after the outbox flushed the tally does not say sent: {tally!r}")

        page.evaluate("() => stopDrive()")
        if not wait_for_dialog(page, dialogs, 1, 90):
            fails.append("no summary after Stop")
        else:
            summary = dialogs[-1]
            if sent_word not in summary:
                fails.append(f"the end summary does not count the sent events: {summary!r}")
        page.locator("#home").wait_for(state="visible", timeout=30_000)
        synced = page.evaluate("() => t('chip_synced')")
        chips = page.evaluate("""async () => {
          const reports = await StandaloneAPI.handle('/api/reports');
          return reports.map((r) => ({ id: r.id, status: r.status,
            pending: !!r.central_sync_pending, pothole: r.server_pothole_id || null }));
        }""")
        if not chips or any(c["pending"] or not c["pothole"] for c in chips):
            fails.append(f"after the flush some reports still wait for the service: {chips}")
        badge = page.evaluate("""() => {
          const r = loadReports.latest && loadReports.latest[0];
          const html = chip(r.status, r);
          const el = document.createElement('div'); el.innerHTML = html;
          return el.textContent;
        }""")
        if badge != synced:
            fails.append(f"a report the service accepted wears {badge!r}, expected {synced!r}")
        if errors:
            fails.append(f"drive page errors {errors[:3]}")
    except Exception as error:
        fails.append(f"drive flow broke: {str(error)[:300]}")
    finally:
        browser.close()

    # ---------- a refused write shows Failed with a one-tap retry, then an email draft ----------
    state = {"report": "fail"}

    def refusing(route, request, path, central):
        if routed(route, request, path, central):
            return True
        if path == "/v1/potholes/report" and state["report"] == "fail":
            return reply(route, 503, "service_temporarily_unavailable",
                         "The shared map is temporarily unavailable.", {"retryable": True})
        return False

    central = Central(refusing)
    browser, page, dialogs, errors = open_central(playwright, central)
    try:
        # No automatic retry during the test: the tap is what must fix it.
        page.evaluate("() => { window.__centralRetryDelayMs = 600000; }")
        page.evaluate("""() => {
          window.__mailto = [];
          document.addEventListener('click', (event) => {
            const link = event.target && event.target.closest && event.target.closest("a[href^='mailto:']");
            if (link) { window.__mailto.push(link.href); event.preventDefault(); }
          }, true);
        }""")
        page.wait_for_timeout(600)
        outcome, text = capture(page, dialogs)
        if outcome != "detail":
            fails.append(f"refused write: capture did not finish: {outcome} {text!r}")
        failed = page.evaluate("() => t('chip_failed')")
        chip_text = page.evaluate("() => document.querySelector('#detail .chip')?.textContent || ''")
        if chip_text != failed:
            fails.append(f"a write the service refused wears {chip_text!r}, expected {failed!r}")
        if not page.locator("#detail #syncRetryBtn").count():
            fails.append("a failed write offers no retry")
        chips = home_chips(page)
        if not chips or chips[0]["chip"] != failed:
            fails.append(f"Home does not show the failed write: {chips}")
        page.evaluate("() => openDetail(loadReports.latest[0], loadReports.latest)")

        state["report"] = "ok"
        page.locator("#detail #syncRetryBtn").click()
        synced = page.evaluate("() => t('chip_synced')")
        page.wait_for_function(
            "(want) => (document.querySelector('#detail .chip') || {}).textContent === want",
            arg=synced, timeout=30_000)
        if central.count("/v1/potholes/report") != 2:
            fails.append(f"retry sent {central.count('/v1/potholes/report')} writes, expected 2")
        chips = home_chips(page)
        if not chips or chips[0]["chip"] != synced:
            fails.append(f"Home does not show the accepted write: {chips}")

        # The email draft is its own state, distinct from the service write.
        page.evaluate("() => openDetail(loadReports.latest[0], loadReports.latest)")
        if not page.locator("#detail #sendBtn").count():
            fails.append("an accepted report offers no Email action")
        else:
            page.locator("#detail #sendBtn").click()
            page.wait_for_timeout(1500)
            drafted = page.evaluate("() => t('chip_queued')")
            chip_text = page.evaluate("() => document.querySelector('#detail .chip')?.textContent || ''")
            if chip_text != drafted:
                fails.append(f"after the composer opened the card wears {chip_text!r}, expected {drafted!r}")
            chips = home_chips(page)
            if not chips or chips[0]["chip"] != drafted:
                fails.append(f"Home does not show the email draft: {chips}")
            sent = page.evaluate("() => t('chip_sent')")
            if sent == drafted:
                fails.append("an opened draft and a confirmed send read the same")
        if errors:
            fails.append(f"page errors {errors[:3]}")
    except Exception as error:
        fails.append(f"refusal flow broke: {str(error)[:300]}")
    finally:
        browser.close()

if fails:
    print("FAIL report status badges")
    for failure in fails:
        print(" -", failure)
    sys.exit(1)
print("PASS report status badges")
