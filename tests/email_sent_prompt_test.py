# -*- coding: utf-8 -*-
"""Back from the mail app, the report asks once: "Email sent?"

The app opens a pre-addressed draft and cannot see whether the person pressed Send.
A tester wrote: "after opening the email app, there's no clear status for sent vs
cancelled reports." The composer plugin resolves its promise when the mail activity
returns, so that return is where one small prompt belongs. Sent records sent_at and the
card says so; Not yet keeps the draft and offers to open the email again; a report
whose composer never opened is never asked.
"""

import json
import sys

from playwright.sync_api import sync_playwright

import flow_harness as fh

PIXEL = ("data:image/png;base64,"
         "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")

# A composer whose promise stays open until the test "returns" from the mail app, the way
# the Android plugin resolves open() from its activity result.
COMPOSER = r"""
(() => {
  window.__composerOpen = [];
  window.__composerReturn = () => {
    const call = window.__composerOpen.shift();
    if (!call) throw new Error("no composer is open");
    call.resolve({ value: true });
  };
  const EmailComposer = { open: (options) => new Promise((resolve) => {
    window.__composerOpen.push({ options: JSON.parse(JSON.stringify(options)), resolve });
  }) };
  window.Capacitor = {
    isNativePlatform: () => true,
    registerPlugin: (name) => window.Capacitor.Plugins[name],
    Plugins: { EmailComposer, App: { addListener: () => null, exitApp: () => null } },
  };
})();
"""

SEED = r"""
async ({ pixel }) => {
  await StandaloneAPI.handle("/api/reports", { method: "DELETE" });
  const base = {
    created_at: 1787260200, captured_at: 1787260200, decision: "accept", status: "draft",
    damage_type: "pothole_cavity", assessment: "clear", image_quality: "usable",
    size: "medium", description: "Road cavity", address: "Test Road, Kalaburagi",
    email_subject: "Pothole complaint", email_body: "Please inspect and repair this pothole.",
    officer_name: "Commissioner, Kalaburagi", officer_email: "ka.kalaburagi.cc@gmail.com",
    lat: 17.3297, lng: 76.8343, gps_accuracy: 6, photo: pixel, photo_full: pixel,
    road_ownership: "municipal", road_ownership_source: "central_v1",
    body_lgd: "248127", body_name: "Kalaburagi", tender_resolution_checked_at: 1787260200,
    central_sync_pending: false, server_duplicate: false,
    official_grievance_id: null, submitted_at: null, sent_at: null, email_opened_at: null,
  };
  const records = [
    { ...base, id: 91001, server_pothole_id: 800001 },
    { ...base, id: 91002, server_pothole_id: 800002, created_at: base.created_at - 60 },
  ];
  const db = await new Promise((resolve, reject) => {
    const request = indexedDB.open("potholes");
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  await new Promise((resolve, reject) => {
    const tx = db.transaction("reports", "readwrite");
    for (const record of records) tx.objectStore("reports").put(record);
    tx.oncomplete = resolve;
    tx.onabort = () => reject(tx.error || new Error("seed aborted"));
    tx.onerror = () => {};
  });
  db.close();
  await loadReports();
  return loadReports.latest.map((r) => r.id);
}
"""

VIEW = """() => ({
  prompts: document.querySelectorAll('#emailSentPrompt').length,
  chip: (document.querySelector('#detail .chip') || {}).textContent || '',
  send: (document.querySelector('#detail #sendBtn') || {}).textContent || null,
  text: document.getElementById('detail').innerText,
})"""

RECORD = """async (id) => {
  const r = (await StandaloneAPI.handle('/api/reports')).find((x) => x.id === id);
  return r && { status: r.status, sent_at: r.sent_at, opened: r.email_opened_at,
                confirmed: !!r.email_sent_confirmed };
}"""


def open_report(page, report_id):
    page.evaluate("(id) => { const r = loadReports.latest.find((x) => x.id === id);"
                  " openDetail(r, loadReports.latest); }", report_id)
    page.wait_for_timeout(200)


def resume(page):
    page.evaluate("() => document.dispatchEvent(new Event('visibilitychange'))")
    page.wait_for_timeout(200)


fails = []
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(args=["--disable-web-security"])
    context = browser.new_context(viewport={"width": 390, "height": 844}, locale="en-IN")
    prelude = {"service_url": fh.SERVICE, "data_notice_version": fh.DATA_NOTICE_VERSION,
               "initial_setup_complete": "1", "vision_provider": "shared",
               "sender_name": "Test Citizen", "app_lang": "en"}
    context.add_init_script(script="(() => {" + "".join(
        f"localStorage.setItem({json.dumps(k)}, {json.dumps(v)});" for k, v in prelude.items())
        + "})();" + COMPOSER)
    context.route(f"{fh.SERVICE}/**", fh.central_service)
    context.route("**/karnataka-bodies.json", fh.support_services)
    context.route("https://nominatim.openstreetmap.org/**", fh.support_services)
    context.route("https://kgis.ksrsac.in/**", fh.support_services)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(f"uncaught: {error}"))
    page.on("dialog", lambda dialog: dialog.accept())
    page.goto(fh.APP)
    page.wait_for_function("() => !!(window.StandaloneAPI && window.openDetail)", timeout=30_000)
    page.locator("#home").wait_for(state="visible", timeout=30_000)
    try:
        ids = page.evaluate(SEED, {"pixel": PIXEL})
        if 91001 not in ids:
            fails.append(f"the seeded reports are not listed: {ids}")
        strings = page.evaluate("""() => ({
          q: t('email_sent_q'), yes: t('email_sent_yes'), no: t('email_sent_no'),
          reopen: t('email_reopen_btn'), drafted: t('chip_queued'), sent: t('chip_sent'),
          synced: t('chip_synced') })""")
        for key, value in strings.items():
            if value.startswith("email_") or value.startswith("chip_"):
                fails.append(f"{key} has no string")
        for key, value in strings.items():
            if "—" in value or "–" in value:
                fails.append(f"{key} carries a dash: {value!r}")

        # Never drafted: no question, even after a resume.
        open_report(page, 91001)
        before = page.evaluate(VIEW)
        resume(page)
        after = page.evaluate(VIEW)
        if before["prompts"] or after["prompts"]:
            fails.append("a report whose composer never opened is asked whether it was sent")
        if before["chip"] != strings["synced"]:
            fails.append(f"a confirmed report wears {before['chip']!r}, expected {strings['synced']!r}")

        # The composer opens; while the mail app is in front there is no question yet.
        page.locator("#detail #sendBtn").click()
        page.wait_for_function("() => window.__composerOpen.length === 1", timeout=10_000)
        page.wait_for_timeout(300)
        while_open = page.evaluate(VIEW)
        if while_open["prompts"]:
            fails.append("the question appeared before the mail app returned")

        # Back from the mail app: one question, and only one, however many resumes follow.
        page.evaluate("() => window.__composerReturn()")
        page.wait_for_function("() => document.querySelectorAll('#emailSentPrompt').length > 0",
                               timeout=10_000)
        resume(page)
        resume(page)
        returned = page.evaluate(VIEW)
        if returned["prompts"] != 1:
            fails.append(f"{returned['prompts']} questions after returning, expected one")
        if strings["q"] not in returned["text"]:
            fails.append(f"the question is not on the card: {returned['text'][:160]!r}")
        if returned["chip"] != strings["drafted"]:
            fails.append(f"after the composer returned the card wears {returned['chip']!r}")

        # Not yet: still a draft, with a one-tap reopen.
        page.locator("#emailSentNo").click()
        page.wait_for_timeout(300)
        not_yet = page.evaluate(VIEW)
        record = page.evaluate(RECORD, 91001)
        if not_yet["prompts"]:
            fails.append("Not yet left the question on the card")
        if not_yet["chip"] != strings["drafted"]:
            fails.append(f"after Not yet the card wears {not_yet['chip']!r}, expected {strings['drafted']!r}")
        if not not_yet["send"] or strings["reopen"] not in not_yet["send"]:
            fails.append(f"after Not yet the action reads {not_yet['send']!r}, expected {strings['reopen']!r}")
        if record["status"] != "queued" or record["sent_at"] is not None or record["confirmed"]:
            fails.append(f"Not yet changed the record: {record}")
        resume(page)
        if page.evaluate(VIEW)["prompts"]:
            fails.append("the question came back on a later resume after Not yet")

        # Open email again, return, Sent: the record and the card both say so.
        page.locator("#detail #sendBtn").click()
        page.wait_for_function("() => window.__composerOpen.length === 1", timeout=10_000)
        page.evaluate("() => window.__composerReturn()")
        page.wait_for_function("() => document.querySelectorAll('#emailSentPrompt').length > 0",
                               timeout=10_000)
        page.locator("#emailSentYes").click()
        page.wait_for_function(
            "(want) => (document.querySelector('#detail .chip') || {}).textContent === want",
            arg=strings["sent"], timeout=10_000)
        sent = page.evaluate(VIEW)
        record = page.evaluate(RECORD, 91001)
        if sent["prompts"]:
            fails.append("Sent left the question on the card")
        if record["status"] != "sent" or not record["sent_at"] or not record["confirmed"]:
            fails.append(f"Sent did not record the confirmation: {record}")
        resume(page)
        if page.evaluate(VIEW)["prompts"]:
            fails.append("the question came back on a later resume after Sent")

        # Home: the sent one and the untouched one wear different badges.
        page.evaluate("() => loadReports()")
        page.wait_for_timeout(600)
        chips = page.evaluate("""() => Object.fromEntries(
          [...document.querySelectorAll('#list .card.row')].map((card) =>
            [card.dataset.id, (card.querySelector('.chip') || {}).textContent || '']))""")
        if chips.get("91001") != strings["sent"]:
            fails.append(f"Home shows the sent report as {chips.get('91001')!r}")
        if chips.get("91002") != strings["synced"]:
            fails.append(f"Home shows the untouched report as {chips.get('91002')!r}")

        # The question is for the return from the mail app, not for every visit.
        page.reload()
        page.wait_for_function("() => !!(window.StandaloneAPI && window.openDetail)", timeout=30_000)
        page.locator("#home").wait_for(state="visible", timeout=30_000)
        page.evaluate("() => loadReports()")
        page.wait_for_function("() => loadReports.latest && loadReports.latest.length === 2",
                               timeout=10_000)
        open_report(page, 91001)
        resume(page)
        reopened = page.evaluate(VIEW)
        if reopened["prompts"]:
            fails.append("a fresh launch asks about an email it did not just open")
        if reopened["chip"] != strings["sent"]:
            fails.append(f"after a relaunch the sent report wears {reopened['chip']!r}")
        if errors:
            fails.append(f"page errors {errors[:3]}")
    except Exception as error:
        fails.append(f"flow broke: {str(error)[:300]}")
    finally:
        browser.close()

if fails:
    print("FAIL email sent prompt")
    for failure in fails:
        print(" -", failure)
    sys.exit(1)
print("PASS email sent prompt")
