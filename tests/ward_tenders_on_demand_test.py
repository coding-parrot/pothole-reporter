# -*- coding: utf-8 -*-
"""A report that was never asked about its ward is asked once, when it is opened or emailed.

Most reports come from Drive Mode, and those live in Room: a row with a tender number and
a contractor, no tender title and no ward list. Its card showed no road works and its
letter could only ask the plain question, because a road work cannot be put to an office
by its number alone. Reports from older builds were never asked either.

What this holds:
  - such a report gets one lookup when its card is drawn under the civic attribution or
    when Email is tapped, and none from the History list, in personal-key mode, or for a
    report that already holds an answer, even an empty one;
  - the card is drawn at once and the list arrives after it; a card opened again, and a
    row read again from the bridge, draw the list with no second lookup;
  - the letter asks about the street-level tender when the answer names the number the
    row already stores, otherwise about the first ward tender, otherwise only whether the
    road is the office's;
  - the answer changes nothing the row's own resolver stored: owner, authority,
    recipient, tender number and contractor are the same before and after;
  - Email never waits more than its budget: a slow, failed or retrying lookup opens the
    composer with the plain question, shows no error, and leaves the report to be asked
    again the next time.
"""

import json
import sys

from playwright.sync_api import sync_playwright

import flow_harness as fh

RECIPIENT = "ka.kalaburagi.cc@gmail.com"
BUDGET_MS = 2500


def ward_tender(index, published="12-08-2025"):
    return {"tender_number": f"KLB/2025-26/RD/WORK_INDENT{2000 + index}",
            "title": f"Improvements to roads in Test Ward no.12, package {index}",
            "location": "Kalaburagi", "published": published, "source_name": "KPPP",
            "source_url": "https://kppp.karnataka.gov.in/", "match_basis": "ward_name",
            "scope": "ward"}


FIVE = [ward_tender(n) for n in (1, 2, 3, 4, 5)]
FIRST = FIVE[0]["tender_number"]
PLAIN = "Please verify that this road is maintained by your office."
ASK = "Please verify that this road is maintained by your office and if this location is covered by - "

# One Room row per case, told apart by latitude. Room keeps a tender's number and its
# contractor, never its title.
ROWS = {
    "ward": {"id": 77, "lat": 17.3301, "tender_number": "NATIVE-TENDER-77",
             "contractor": "Native Roads Example Ltd",
             "tender_note": "Probable contract: NATIVE-TENDER-77, Native Roads Example Ltd"},
    "street": {"id": 78, "lat": 17.3302, "tender_number": "STREET-78",
               "contractor": "Native Roads Example Ltd"},
    "other": {"id": 79, "lat": 17.3303, "tender_number": "STORED-79",
              "contractor": "Stored Contractor Ltd"},
    "down": {"id": 80, "lat": 17.3304},
    "slow": {"id": 81, "lat": 17.3305},
    "unproven": {"id": 82, "lat": 17.3306, "road_ownership": None},
    "none": {"id": 83, "lat": 17.3307},
    "emailed": {"id": 85, "lat": 17.3309},
}
PLANS = {
    "17.3301": {"ward": FIVE},
    "17.3302": {"ward": FIVE, "street": {
        "tender_number": "STREET-78", "title": "Resurfacing of Native Test Road",
        "contractor": "Answer Contractor Ltd", "published": "02-03-2026", "confidence": 0.9}},
    "17.3303": {"ward": FIVE, "street": {
        "tender_number": "OTHER-79", "title": "Asphalting of Another Road",
        "contractor": "Other Contractor Ltd", "published": "05-05-2026", "confidence": 0.9}},
    "17.3304": {"down": True},
    "17.3305": {"ward": FIVE, "delay": 4000},
    "17.3306": {"ward": FIVE},
    "17.3307": {"ward": []},
    "17.3309": {"ward": FIVE},
    # Browser rows.
    "17.3401": {"ward": FIVE},
    "17.3402": {"ward": FIVE},
    "17.3403": {"ward": FIVE},
    "17.3404": {"retryable": True, "delay": 1300},
}

# Counts every lookup and answers it from the plan for that latitude. The jurisdiction it
# answers is deliberately not the row's: nothing of it may reach the row.
INSTALL = r"""
([serviceUrl, plans, rows, recipient]) => {
  const original = window.fetch;
  window.__ward = { calls: [], plans };
  window.fetch = async (input, options = {}) => {
    const url = input && input.url ? input.url : String(input);
    if (!url.startsWith(serviceUrl) || new URL(url).pathname !== "/v1/tenders/resolve") {
      return original(input, options);
    }
    const body = JSON.parse(options.body || "{}");
    const plan = window.__ward.plans[String(body.lat)] || {};
    window.__ward.calls.push(body.lat);
    if (plan.delay) await new Promise((resolve) => setTimeout(resolve, plan.delay));
    const reply = (value, status) => new Response(JSON.stringify({ request_id: "req-ward", ...value }),
      { status, headers: { "content-type": "application/json" } });
    if (plan.down || plan.retryable) {
      return reply({ error: "road_ownership_unavailable", message: "Retry later.",
                     ...(plan.retryable ? { details: { retryable: true } } : {}) }, 503);
    }
    return reply({
      jurisdiction: { lat: body.lat, lng: body.lng, address: "Answer Road, Elsewhere",
        lgd: "999999", town: "Elsewhere", road_ownership: "state_highway",
        ward_name: "Answer Ward" },
      tender: plan.street || null, reason: plan.street ? "tender_matched" : "no_match",
      ward_tenders: plan.ward || [],
    }, 200);
  };
  window.__saved = [];
  window.__room = {};
  window.__alerts = [];
  window.alert = (message) => window.__alerts.push(String(message));
  Object.assign(Capacitor.Plugins.DriveMode, {
    listReports: async () => ({ reports: Object.values(rows).map((row) => ({
      status: "queued", decision: "accept", assessment: "damaged", image_quality: "acceptable",
      damage_type: "pothole_cavity", size: "medium",
      description: "A pothole is visible in the traffic lane.", lng: 76.8343, gps_accuracy: 4,
      created_at: 1791344700, captured_at: 1791344700,
      address: "Native Test Road, Kalaburagi", body_lgd: "248127", body_name: "Kalaburagi",
      road_ownership: "municipal", tender_resolution_checked_at: 1791344700,
      email_to: recipient, officer_title: "Commissioner, Kalaburagi",
      has_photo: true, server_pothole_id: 7000 + row.id, server_duplicate: false, seen_count: 1,
      ...row, ...(window.__room[row.id] || {}) })) }),
    saveComplaintPreparation: async (options) => {
      window.__saved.push({ ...options });
      return window.__room[options.id] = { id: options.id, decision: "accept", status: "queued",
        server_pothole_id: 7000 + options.id, server_duplicate: false,
        road_ownership: "municipal", tender_resolution_checked_at: 1791344700,
        tender_number: options.tenderNumber, contractor: options.contractor,
        tender_note: options.tenderNote, address: options.address, body_lgd: options.bodyLgd,
        body_name: options.bodyName, email_to: options.emailTo,
        officer_title: options.officerTitle, email_subject: options.emailSubject,
        email_body: options.emailBody };
    },
    getReportPhoto: async () => ({ dataUrl: "data:image/jpeg;base64,/9j/" + "A".repeat(240) }),
  });
}
"""

CALLS = "() => window.__ward.calls.slice()"
ROW = "(id) => loadReports.latest.find((r) => r.id === `native_${id}`)"

# Draws the card the way a tap on the History row does, and says what it showed at once.
OPEN = r"""
async (id) => {
  await loadReports();
  const row = loadReports.latest.find((r) => String(r.id) === String(id));
  await openReportDetail(row, loadReports.latest);
  const box = document.querySelector("#detail details.ward-tenders");
  return { drawn: !document.getElementById("detail").classList.contains("hidden"),
           listed: box ? box.querySelectorAll("li").length : 0 };
}
"""
LISTED = r"""
() => {
  const box = document.querySelector("#detail details.ward-tenders");
  return box ? [...box.querySelectorAll(".ward-tender-ref")].map((el) => el.textContent.trim()) : [];
}
"""
STORED_FIELDS = ("tender_number", "contractor", "tender_note", "email_to", "officer_title",
                 "road_ownership", "body_lgd", "body_name", "address")
# Taps Email on the open card and reports what the composer and Room received.
EMAIL = r"""
async ([id, fields]) => {
  const row = loadReports.latest.find((r) => String(r.id) === String(id));
  const pick = () => Object.fromEntries(fields.map((field) => [field, row[field] === undefined ? null : row[field]]));
  const before = pick();
  const composed = window.__composerCalls.length, saved = window.__saved.length;
  const started = performance.now();
  await sendReport(row);
  const elapsed = performance.now() - started;
  return { elapsed, before, after: pick(),
    composer: window.__composerCalls.slice(composed).map((call) => call.body),
    to: window.__composerCalls.slice(composed).map((call) => call.to),
    saved: window.__saved.slice(saved), alerts: window.__alerts.slice() };
}
"""

# A browser row from a build that never asked: municipal by the service's word, a letter
# from the previous template exactly as the app wrote it, and no ward field at all.
OLD_BODY = """Dear Commissioner, Kalaburagi,

Please register the following pothole grievance.

LOCATION
Address / landmark: Test Road, Kalaburagi
Coordinates: {lat:.6f}, 76.834300
Map: https://maps.google.com/?q={lat:.6f},76.834300
GPS accuracy: ±4 m
Captured: 7 Oct 2026, 9:15:00 am IST
Photo: Pothole Reporter camera evidence

CLASSIFICATION
Defect decision: Pothole (YES)
App visual size class: medium
Measurement provenance: Visual estimate without a scale reference

ROUTING
Geographic corporation/body: Kalaburagi
Complaint intake authority: Kalaburagi
Suggested portal category: Road / Pothole
Suggested ward: Not identified
Road owner/maintainer: Unknown (authority to inspect and transfer if required)

CONTRACT VERIFICATION
Status: No verified exact-road public contract found; tender and contractor omitted.

Please register this grievance, inspect and repair the pothole, return the grievance number, and transfer it if another agency maintains the road.

Regards,
Test Citizen

Pothole Reporter is an independent app. Please verify any suggested authority, ward, road ownership, and tender details."""

SEED = r"""
async (rows) => {
  const canvas = document.createElement("canvas");
  canvas.width = 64; canvas.height = 48;
  canvas.getContext("2d").fillRect(0, 0, 64, 48);
  const photo = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", .8));
  const db = await new Promise((resolve, reject) => {
    const request = indexedDB.open("potholes");
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  const ids = {};
  await new Promise((resolve, reject) => {
    const tx = db.transaction("reports", "readwrite");
    for (const [name, row] of Object.entries(rows)) {
      const add = tx.objectStore("reports").add({
        created_at: 1791344700, captured_at: 1791344700, gps_accuracy: 4, status: "draft",
        decision: "accept", damage_type: "pothole_cavity", assessment: "damaged",
        image_quality: "acceptable", size: "medium", is_pothole: 1, issue_type: "road_damage",
        description: "A cavity with a broken rim.", lng: 76.8343,
        address: "Test Road, Kalaburagi", officer_name: "Commissioner, Kalaburagi",
        officer_email: "ka.kalaburagi.cc@gmail.com", authority_id: "lgd-248127",
        authority_name: "Kalaburagi", routing_source: "central_v1", delivery_channel: "email",
        email_subject: "Pothole complaint: Test Road", capture_source: "manual_camera",
        road_ownership: "municipal", road_ownership_source: "central_v1",
        body_lgd: "248127", body_name: "Kalaburagi", complaint_template_version: 4,
        tender_resolution_checked_at: 1791344700, tender_resolution_reason: "no_match",
        server_pothole_id: 4242, server_duplicate: false, central_sync_pending: false,
        photo, photo_full: photo, ...row });
      add.onsuccess = () => { ids[name] = add.result; };
    }
    tx.oncomplete = resolve;
    tx.onabort = () => reject(tx.error);
  });
  db.close();
  return ids;
}
"""
BROWSER_ROW = r"""
async (id) => {
  const r = await StandaloneAPI.__pure.getReport(id);
  return { ward_tenders: r.ward_tenders === undefined ? "absent" : r.ward_tenders,
    ward_name: r.ward_name === undefined ? null : r.ward_name, email_body: r.email_body,
    kept: [r.tender_number || null, r.contractor || null, r.officer_email, r.officer_name,
           r.road_ownership, r.road_ownership_source, r.body_lgd, r.body_name, r.authority_id,
           r.address] };
}
"""
SEND = ("(id) => StandaloneAPI.handle(`/api/reports/${id}/send`, { method: 'POST' })"
        ".then((r) => ({ ok: true, status: r.status }), (e) => ({ ok: false, message: e.message }))")

fails = []


def check(ok, message):
    if not ok:
        fails.append(message)


def install(page):
    page.evaluate(INSTALL, [fh.SERVICE, PLANS, ROWS, RECIPIENT])


def calls_for(page, name):
    lat = ROWS[name]["lat"] if name in ROWS else name
    return sum(1 for call in page.evaluate(CALLS) if call == lat)


def wait_for_list(page, what):
    try:
        page.wait_for_selector("#detail details.ward-tenders", state="attached", timeout=10_000)
        return True
    except Exception:
        fails.append(f"{what}: the ward list never arrived on the card")
        return False


def body_of(sent, what):
    if len(sent["composer"]) != 1:
        fails.append(f"{what}: Email opened {len(sent['composer'])} composers")
        return ""
    return sent["composer"][0]


def unchanged(sent, name, what):
    row = ROWS[name]
    check(sent["before"] == sent["after"],
          f"{what}: the lookup changed what the row stores: {sent['before']} became {sent['after']}")
    for save in sent["saved"]:
        got = (save.get("tenderNumber"), save.get("contractor"), save.get("emailTo"),
               save.get("roadOwnership"), save.get("bodyLgd"), save.get("bodyName"))
        want = (row.get("tender_number"), row.get("contractor"), RECIPIENT, "municipal",
                "248127", "Kalaburagi")
        check(got == want, f"{what}: Room was handed {got}, the row stores {want}")
    check(sent["to"] in ([], [[RECIPIENT]]), f"{what}: the composer was addressed to {sent['to']}")


with sync_playwright() as playwright:
    browser, page, errors = fh.open_flow(playwright)
    try:
        page.locator("#home").wait_for(state="visible", timeout=30_000)
        install(page)

        # ---- the History list asks nothing ----
        page.evaluate("() => loadReports()")
        page.wait_for_timeout(800)
        check(page.evaluate(CALLS) == [],
              f"drawing the History list looked up ward tenders: {page.evaluate(CALLS)}")

        # ---- a Room row: the card is drawn at once, the list follows, one lookup ----
        first = page.evaluate(OPEN, "native_77")
        check(first["drawn"], "the card of a Drive Mode report was held back for the lookup")
        if wait_for_list(page, "Drive Mode report"):
            refs = page.evaluate(LISTED)
            check(len(refs) == 5 and refs[0].startswith(FIRST), f"the card lists {refs}")
        check(calls_for(page, "ward") == 1, f"one opened card cost {calls_for(page, 'ward')} lookups")
        page.evaluate("() => show('home')")
        again = page.evaluate(OPEN, "native_77")
        check(again["listed"] == 5 and calls_for(page, "ward") == 1,
              f"a card opened again did not draw its list at once, or asked again: {again} "
              f"after {calls_for(page, 'ward')} lookups")

        # ---- its letter asks about the first ward tender, and the row is untouched ----
        sent = page.evaluate(EMAIL, ["native_77", STORED_FIELDS])
        body = body_of(sent, "ward tender letter")
        check(f"{ASK}{FIRST}: {FIVE[0]['title']}. Published 12-08-2025." in body.split("\n\n"),
              f"the letter of a Room row with no tender title does not ask about the first ward tender: {body[-420:]!r}")
        check("NATIVE-TENDER-77" not in body and "Native Roads Example Ltd" not in body,
              "the letter names a tender it has no title for")
        check(calls_for(page, "ward") == 1, "Email after an opened card looked the ward up again")
        unchanged(sent, "ward", "ward tender letter")
        check(sent["saved"] and sent["saved"][0].get("emailBody") == body,
              "Room was not handed the letter the composer received")
        resent = page.evaluate(EMAIL, ["native_77", STORED_FIELDS])
        check(body_of(resent, "second Email tap") == body and calls_for(page, "ward") == 1,
              "a second Email tap sent a different letter or asked again")

        # ---- the answer names the stored tender: its title, the row's own contractor ----
        page.evaluate(OPEN, "native_78")
        sent = page.evaluate(EMAIL, ["native_78", STORED_FIELDS])
        body = body_of(sent, "street tender letter")
        check(f"{ASK}STREET-78: Resurfacing of Native Test Road. Contractor listed: Native Roads "
              "Example Ltd. Published 02-03-2026." in body.split("\n\n"),
              f"a stored tender the answer names is not put with its title: {body[-420:]!r}")
        check("Answer Contractor Ltd" not in body and FIRST not in body,
              "the letter took the answer's contractor or a ward tender over the stored tender")
        unchanged(sent, "street", "street tender letter")
        check(calls_for(page, "street") == 1, f"street case cost {calls_for(page, 'street')} lookups")

        # ---- the answer names another tender: the stored number stays, the letter asks
        #      about the first ward tender because the stored one has no title to print ----
        page.evaluate(OPEN, "native_79")
        sent = page.evaluate(EMAIL, ["native_79", STORED_FIELDS])
        body = body_of(sent, "differing tender letter")
        check(f"{ASK}{FIRST}: " in body and "OTHER-79" not in body and "STORED-79" not in body
              and "Asphalting of Another Road" not in body,
              f"a differing street tender reached the letter: {body[-420:]!r}")
        unchanged(sent, "other", "differing tender letter")

        # ---- an empty answer is an answer ----
        page.evaluate(OPEN, "native_83")
        page.wait_for_timeout(600)
        check(page.evaluate(LISTED) == [] and calls_for(page, "none") == 1,
              f"an empty answer drew a list or was not asked once: {calls_for(page, 'none')}")
        page.evaluate("() => show('home')")
        page.evaluate(OPEN, "native_83")
        page.wait_for_timeout(400)
        sent = page.evaluate(EMAIL, ["native_83", STORED_FIELDS])
        check(PLAIN in body_of(sent, "empty answer letter").split("\n\n")
              and calls_for(page, "none") == 1,
              f"a report answered with no ward tenders was asked again: {calls_for(page, 'none')}")

        # ---- ownership nobody proved: the card shows no attribution and asks nothing ----
        page.evaluate(OPEN, "native_82")
        page.wait_for_timeout(600)
        check(calls_for(page, "unproven") == 0 and page.evaluate(LISTED) == [],
              "a card with no proven municipal owner looked up ward tenders")

        # ---- Email without the card: one lookup, inside the tap ----
        page.evaluate("() => loadReports()")
        page.evaluate("(id) => openDetail(loadReports.latest.find((r) => r.id === id), loadReports.latest)",
                      "native_85")
        sent = page.evaluate(EMAIL, ["native_85", STORED_FIELDS])
        check(f"{ASK}{FIRST}: " in body_of(sent, "Email with the card just drawn")
              and calls_for(page, "emailed") == 1,
              f"Email right after the card cost {calls_for(page, 'emailed')} lookups or lost the question")

        # ---- a lookup that fails: plain question, no error, asked again next time ----
        page.evaluate(OPEN, "native_80")
        page.wait_for_timeout(500)
        sent = page.evaluate(EMAIL, ["native_80", STORED_FIELDS])
        body = body_of(sent, "failed lookup letter")
        check(PLAIN in body.split("\n\n") and "covered by" not in body,
              f"a failed lookup did not leave the plain question: {body[-300:]!r}")
        check(sent["elapsed"] < BUDGET_MS, f"a failed lookup held Email for {sent['elapsed']:.0f} ms")
        check(not sent["alerts"], f"a failed lookup showed an error: {sent['alerts']}")
        # The Email tap redraws the card when the composer returns. A lookup that failed a
        # moment ago is not sent again for the tap or for that redraw.
        check(calls_for(page, "down") == 1,
              f"one failed lookup was repeated at once: {calls_for(page, 'down')} lookups for a "
              "card and the Email tap that followed it")
        page.wait_for_timeout(3100)
        unchanged(sent, "down", "failed lookup letter")
        page.evaluate("""() => { window.__ward.plans["17.3304"] = { ward: %s }; }""" % json.dumps(FIVE))
        asked = calls_for(page, "down")
        page.evaluate("() => show('home')")
        page.evaluate(OPEN, "native_80")
        if wait_for_list(page, "report whose first lookup failed"):
            check(calls_for(page, "down") == asked + 1,
                  f"the next view of a failed lookup cost {calls_for(page, 'down') - asked} lookups")
        sent = page.evaluate(EMAIL, ["native_80", STORED_FIELDS])
        check(f"{ASK}{FIRST}: " in body_of(sent, "letter after the answer arrived"),
              "the letter Room cached with the plain question was sent again after the answer arrived")
        check(sent["saved"] and f"covered by - {FIRST}: " in sent["saved"][-1].get("emailBody", ""),
              "Room was not handed the letter that asks about the ward tender")

        # ---- a slow lookup: the composer opens at the budget with the plain question ----
        page.evaluate("() => show('home')")
        page.evaluate("() => loadReports()")
        page.evaluate("(id) => { const r = loadReports.latest.find((x) => x.id === id); openDetail(r, [r]); }",
                      "native_81")
        sent = page.evaluate(EMAIL, ["native_81", STORED_FIELDS])
        body = body_of(sent, "slow lookup letter")
        check(PLAIN in body.split("\n\n") and "covered by" not in body,
              f"a lookup slower than the budget still reached the letter: {body[-300:]!r}")
        check(BUDGET_MS - 300 <= sent["elapsed"] <= BUDGET_MS + 900,
              f"Email waited {sent['elapsed']:.0f} ms for a slow lookup, budget {BUDGET_MS} ms")
        check(not sent["alerts"], f"a slow lookup showed an error: {sent['alerts']}")
        check(calls_for(page, "slow") == 1, f"a slow lookup was sent {calls_for(page, 'slow')} times")
        unchanged(sent, "slow", "slow lookup letter")

        # ---- the answer survives a restart of the app, where Room cannot hold it ----
        page.reload()
        page.wait_for_function("() => !!window.StandaloneAPI", timeout=30_000)
        page.locator("#home").wait_for(state="visible", timeout=30_000)
        install(page)
        reopened = page.evaluate(OPEN, "native_77")
        check(reopened["listed"] == 5 and page.evaluate(CALLS) == [],
              f"after a restart a Drive Mode card asked again or lost its list: {reopened} "
              f"{page.evaluate(CALLS)}")

        # ---- browser rows: one already answered, one never asked ----
        ids = page.evaluate(SEED, {
            "answered": {"lat": 17.3401, "ward_tenders": [], "ward_name": None,
                         "complaint_template_version": 5,
                         "email_body": "Dear Commissioner, Kalaburagi,\n\nPlease repair it.\n\nRegards,\nTest Citizen"},
            "old": {"lat": 17.3402, "email_body": OLD_BODY.format(lat=17.3402)},
            "emailed": {"lat": 17.3403, "email_body": OLD_BODY.format(lat=17.3403)},
            "retrying": {"lat": 17.3404, "email_body": OLD_BODY.format(lat=17.3404)},
        })
        page.evaluate(OPEN, ids["answered"])
        page.wait_for_timeout(700)
        sent = page.evaluate(SEND, ids["answered"])
        check(sent.get("ok") and calls_for(page, 17.3401) == 0,
              f"a report that already holds an answer was asked again: {sent} "
              f"{calls_for(page, 17.3401)} lookups")

        kept = page.evaluate(BROWSER_ROW, ids["old"])
        page.evaluate(OPEN, ids["old"])
        if wait_for_list(page, "report from an older build"):
            check(len(page.evaluate(LISTED)) == 5, "an older report's card does not list its ward tenders")
        page.wait_for_timeout(300)
        after = page.evaluate(BROWSER_ROW, ids["old"])
        check(after["ward_tenders"] != "absent" and len(after["ward_tenders"]) == 5
              and after["ward_name"] == "Answer Ward",
              f"the answer was not saved on the older report: {after['ward_tenders']!r}")
        check(after["kept"] == kept["kept"] and after["email_body"] == kept["email_body"],
              f"saving the answer changed the older report: {kept['kept']} became {after['kept']}")
        sent = page.evaluate(SEND, ids["old"])
        composed = page.evaluate("() => (window.__composerCalls.slice(-1)[0] || {}).body || ''")
        check(sent.get("ok") and f"{ASK}{FIRST}: " in composed and "ROUTING" not in composed,
              f"the older report's letter does not ask about its ward tender: {sent} {composed[-300:]!r}")
        check(calls_for(page, 17.3402) == 1,
              f"an older report cost {calls_for(page, 17.3402)} lookups for one card and one Email")
        check(page.evaluate(BROWSER_ROW, ids["old"])["kept"] == kept["kept"],
              "Email changed what the older report stores about its owner, recipient or tender")

        sent = page.evaluate(SEND, ids["emailed"])
        composed = page.evaluate("() => (window.__composerCalls.slice(-1)[0] || {}).body || ''")
        after = page.evaluate(BROWSER_ROW, ids["emailed"])
        check(sent.get("ok") and f"{ASK}{FIRST}: " in composed and calls_for(page, 17.3403) == 1,
              f"Email on an older report never opened did not ask once: {sent} "
              f"{calls_for(page, 17.3403)} lookups")
        check(after["ward_tenders"] != "absent" and len(after["ward_tenders"]) == 5,
              "the answer fetched for Email was not saved on the report")

        # ---- the service asks for a retry: its 1.5 s wait would overrun the budget, so the
        #      lookup is given up at once instead of being sent again after the composer ----
        timed = page.evaluate("""async (id) => {
          const started = performance.now();
          const sent = await StandaloneAPI.handle(`/api/reports/${id}/send`, { method: "POST" })
            .then(() => true, (error) => String(error && error.message));
          return { sent, elapsed: performance.now() - started,
                   body: (window.__composerCalls.slice(-1)[0] || {}).body || "" };
        }""", ids["retrying"])
        check(timed["sent"] is True and PLAIN in timed["body"].split("\n\n"),
              f"a lookup that could not be retried in time did not leave the plain question: {timed}")
        check(timed["elapsed"] < BUDGET_MS - 300,
              f"Email waited {timed['elapsed']:.0f} ms on a retry it could not use")
        page.wait_for_timeout(2200)
        check(calls_for(page, 17.3404) == 1,
              f"a retry that could not meet the budget was still sent: {calls_for(page, 17.3404)} lookups")
        check(page.evaluate(BROWSER_ROW, ids["retrying"])["ward_tenders"] == "absent",
              "a lookup that failed left the report marked as answered")

        # ---- the memory of answers is bounded ----
        size = page.evaluate("""() => {
          const P = StandaloneAPI.__pure;
          for (let n = 0; n < 700; n += 1) {
            P.rememberWardAnswer(`bound-${n}`, { lat: 1, lng: 1, ward_tenders: [], ward_name: null, street: null });
          }
          return P.wardAnswers.size;
        }""")
        check(size == 500, f"the answer memory holds {size} entries, limit 500")

        fails += fh.error_failures(errors, "ward tenders on demand")
    finally:
        browser.close()

    # ---- personal-key mode: no lookups at all ----
    browser, page, errors = fh.open_flow(playwright, storage={
        "vision_provider": "personal", "openai_key": "sk-offline-test-not-a-real-key"})
    try:
        page.context.route("https://api.openai.com/**", lambda route: route.abort())
        page.locator("#home").wait_for(state="visible", timeout=30_000)
        install(page)
        page.evaluate(OPEN, "native_77")
        page.wait_for_timeout(700)
        sent = page.evaluate(EMAIL, ["native_77", STORED_FIELDS])
        check(page.evaluate(CALLS) == [],
              f"personal-key mode looked up ward tenders: {page.evaluate(CALLS)}")
        check(page.evaluate(LISTED) == [] and PLAIN in body_of(sent, "personal-key letter").split("\n\n"),
              "personal-key mode drew a ward list or lost the plain question")
    finally:
        browser.close()

if fails:
    print("FAIL ward tenders on demand")
    for fail in fails:
        print("  -", fail)
    sys.exit(1)
print("PASS ward tenders on demand")
