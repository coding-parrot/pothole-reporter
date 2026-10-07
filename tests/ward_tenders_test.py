# -*- coding: utf-8 -*-
"""The app shows the ward tenders the central resolver returns, in the app and nowhere else.

/v1/tenders/resolve answers `ward_tenders` beside `tender`: up to five road works
tendered for the ward the point is in. It is a weaker claim than `tender` ("tendered for
your ward", not "the contract for this road"), and no build read it, so a tester whose
street matched nothing saw nothing although the service had named five works.

What this holds:
  - the list is sanitised once, by one pure helper, wherever it enters the app;
  - every record built or refreshed from a resolver answer keeps the list and the ward's
    name, through save and reload; an answer replaces the stored list and an outage
    leaves it alone;
  - the report card shows it folded, with or without a street-level tender, escaped,
    only where the civic attribution is shown, and inside a 360 px screen;
  - the complaint is unchanged, byte for byte.
"""

import json
import sys
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

import flow_harness as fh

LONG = ("Improvements to roads and drains by providing asphalting, kerb stones and footpath "
        "slabs in Munnekolala colony, Shanthiniketan layout and surrounding areas")
XSS = ('WARD_XSS_MARKER</li><img src="ward-xss-missing" data-ward-xss="1" '
       'onerror="window.__wardXss=1"><li>')


def ward_tender(index, title=None, published="12-08-2025"):
    return {
        "tender_number": f"BBMP/2025-26/RD/WORK_INDENT{1000 + index}",
        "title": title or f"{LONG} at Munnekolala ward no.105, package {index}",
        "location": "Mahadevapura Division", "published": published,
        "source_name": "KPPP", "source_url": "https://kppp.karnataka.gov.in/",
        "match_basis": "ward_name", "scope": "ward",
    }


FIVE = [ward_tender(1), ward_tender(2, published=None), ward_tender(3, title=XSS),
        ward_tender(4), ward_tender(5)]
# A body the bundled Karnataka routing pack has an address for.
MUNICIPAL = {"address": "Test Road, Kalaburagi", "lgd": "248127", "town": "Kalaburagi",
             "source": "kgis", "address_source": "nominatim", "road_ownership": "municipal",
             "ward_name": "Munnekolala", "ward_no": 41, "ward_code": "1006041",
             "ward_numbering": "kgis_current"}
STREET = {"tender_number": "BBMP/2024-25/RD/WORK_INDENT77", "contractor": "Test Builders",
          "title": "Asphalting of Test Road", "published": "03-02-2025", "confidence": 0.9,
          "match_method": "model_adjudicated"}
STATE = {"mode": "municipal", "ward": FIVE, "ward_name": "Munnekolala", "tender": None}


def service(route, request):
    if urlparse(request.url).path != "/v1/tenders/resolve":
        return fh.central_service(route, request)
    if STATE["mode"] == "down":
        return fh.envelope(route, {"error": "road_ownership_unavailable",
                                   "message": "Road ownership could not be verified."}, 503)
    body = json.loads(request.post_data or "{}")
    if STATE["mode"] == "nowhere":
        return fh.envelope(route, {"jurisdiction": None, "tender": None, "reason": "unresolved",
                                   "ward_tenders": STATE["ward"]})
    fh.envelope(route, {
        "jurisdiction": {**MUNICIPAL, "lat": body.get("lat"), "lng": body.get("lng"),
                         "ward_name": STATE["ward_name"]},
        "tender": STATE["tender"],
        "reason": "tender_matched" if STATE["tender"] else "no_match",
        **({} if STATE["ward"] is None else {"ward_tenders": STATE["ward"]}),
    })


UNIT = r"""
() => {
  const clean = StandaloneAPI.__pure.sanitiseWardTenders;
  const out = [];
  const eq = (name, got, want) => out.push([name, JSON.stringify(got) === JSON.stringify(want), got]);
  if (typeof clean !== "function") return [["sanitiseWardTenders is exported from __pure", false, typeof clean]];
  const item = (n, extra = {}) => ({ tender_number: `T-${n}`, title: `Road ${n}`,
    published: "01-02-2025", match_basis: "ward_name", scope: "ward", ...extra });
  for (const value of [undefined, null, "x", 7, {}, { length: 2 }]) {
    eq(`a non-array gives []: ${JSON.stringify(value)}`, clean(value), []);
  }
  eq("keeps only tender_number, title and published",
     clean([item(1, { source_url: "https://example.test", location: "Division" })]),
     [{ tender_number: "T-1", title: "Road 1", published: "01-02-2025" }]);
  eq("at most five, in the order given",
     clean([1, 2, 3, 4, 5, 6, 7].map((n) => item(n))).map((t) => t.tender_number),
     ["T-1", "T-2", "T-3", "T-4", "T-5"]);
  eq("malformed entries do not use up the five",
     clean([null, 4, "T-9", [], {}, item(1), item(2), item(3), item(4), item(5), item(6)])
       .map((t) => t.tender_number), ["T-1", "T-2", "T-3", "T-4", "T-5"]);
  eq("strings are trimmed",
     clean([{ tender_number: "  T-1 ", title: "\n Road 1\t", published: " 01-02-2025 " }]),
     [{ tender_number: "T-1", title: "Road 1", published: "01-02-2025" }]);
  eq("a missing, null or blank date is null",
     clean([{ tender_number: "T-1", title: "A" }, item(2, { published: null }),
            item(3, { published: "   " })]).map((t) => t.published), [null, null, null]);
  eq("a number or title that is not a string is dropped",
     clean([item(1, { tender_number: 105 }), item(2, { title: { text: "Road" } }),
            item(3, { tender_number: null }), item(4, { title: undefined }), item(5)])
       .map((t) => t.tender_number), ["T-5"]);
  eq("a blank number or title is dropped",
     clean([item(1, { tender_number: "  " }), item(2, { title: "" }), item(3)])
       .map((t) => t.tender_number), ["T-3"]);
  eq("a date that is not a string is dropped",
     clean([item(1, { published: 20250201 }), item(2, { published: { d: 1 } }), item(3)])
       .map((t) => t.tender_number), ["T-3"]);
  const long = clean([item(1, { tender_number: "N".repeat(300), title: "T".repeat(900) })])[0];
  eq("tender_number is cut at 120 and title at 240",
     [long.tender_number.length, long.title.length], [120, 240]);
  const input = [item(1)];
  const copy = clean(input);
  copy[0].title = "changed";
  eq("the answer is a copy", input[0].title, "Road 1");
  return out;
}
"""

RESOLVE = r"""
async (observation) => {
  const r = await StandaloneAPI.__pure.tenderFromService(12.9562, 77.7141, null, null, observation);
  return { reached: r.reached, tender: r.tender && r.tender.tender_number,
           ward_tenders: r.ward_tenders, ward_name: r.ward_name };
}
"""

STORED = r"""
async (id) => {
  const r = await StandaloneAPI.__pure.getReport(id);
  return r && { id: r.id, status: r.status, ward_tenders: r.ward_tenders, ward_name: r.ward_name,
    tender_note: r.tender_note, checked: r.tender_resolution_checked_at,
    email_subject: r.email_subject, email_body: r.email_body,
    whatsapp_text: r.whatsapp_text, portal_copy_text: r.portal_copy_text };
}
"""

# Marks a stored row as never checked by the resolver, with a list an older answer left.
STALE = r"""
async ([id, wardTenders]) => {
  const P = StandaloneAPI.__pure;
  const rec = await P.getReport(id);
  rec.tender_resolution_checked_at = null;
  rec.ward_tenders = wardTenders;
  rec.ward_name = "Older Ward";
  await P.putReport(rec);
}
"""

PREPARE = r"""
async ([id, keepList]) => {
  const rec = await StandaloneAPI.__pure.getReport(id);
  rec.tender_resolution_checked_at = null;
  if (!keepList) { delete rec.ward_tenders; delete rec.ward_name; }
  const prepared = await StandaloneAPI.prepareComplaint(rec);
  return { to: prepared.to, subject: prepared.subject, body: prepared.body };
}
"""

# A card straight from a record, the way History hands one to the detail screen.
CARD = r"""
(overrides) => {
  const r = { id: 7001, status: "draft", decision: "accept", assessment: "damaged",
    damage_type: "pothole_cavity", size: "medium", description: "A cavity with a broken rim.",
    address: "Test Road, Munnekolala", lat: 12.9562, lng: 77.7141, created_at: 1790000000,
    officer_name: "Commissioner", officer_email: "commissioner@example.gov.in",
    road_ownership: "municipal", road_ownership_source: "central_v1",
    tender_resolution_checked_at: 1790000000, server_pothole_id: 4242,
    photo_url: "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==",
    ...overrides };
  openDetail(r, [r]);
  const box = document.querySelector("#detail details.ward-tenders");
  if (!box) return { shown: false, text: document.getElementById("detail").innerText };
  return {
    shown: true, open: box.open,
    summary: box.querySelector("summary").textContent.trim(),
    note: box.querySelector(".ward-tenders-note").textContent.trim(),
    items: [...box.querySelectorAll("li")].map((li) => ({
      title: li.querySelector(".ward-tender-title").textContent.trim(),
      ref: li.querySelector(".ward-tender-ref").textContent.trim() })),
    injected: document.querySelectorAll("#detail [data-ward-xss]").length,
    ran: window.__wardXss || 0,
    tenderNote: document.getElementById("detail").innerText.includes("Probable contract"),
  };
}
"""

# Nothing in the opened list may reach past the card, and nothing may be cut off.
FIT = r"""
() => {
  const card = document.querySelector("#detail .card").getBoundingClientRect();
  const box = document.querySelector("#detail details.ward-tenders");
  const wide = [box, ...box.querySelectorAll("*")].filter((el) => {
    const r = el.getBoundingClientRect();
    return r.right > card.right + 0.5 || r.left < card.left - 0.5 || el.scrollWidth > el.clientWidth + 1;
  }).map((el) => `${el.tagName.toLowerCase()}.${el.className}`);
  return { wide, page: document.documentElement.scrollWidth, screen: window.innerWidth,
           height: box.getBoundingClientRect().height };
}
"""

fails = []


def check(ok, message):
    if not ok:
        fails.append(message)


def numbers(found):
    return [entry.get("tender_number") for entry in (found or [])]


FIVE_NUMBERS = numbers(FIVE)

with sync_playwright() as playwright:
    browser, page, errors = fh.open_flow(playwright)
    try:
        page.context.route(f"{fh.SERVICE}/**", service)
        page.locator("#home").wait_for(state="visible", timeout=30_000)

        # ---- the pure helper ----
        for name, ok, got in page.evaluate(UNIT):
            check(ok, f"sanitiseWardTenders: {name}: got {json.dumps(got)[:200]}")

        # ---- tenderFromService: every answer that reached the service carries both ----
        seen = page.evaluate(RESOLVE, "ward-unit-no-tender")
        check(seen["reached"] and seen["tender"] is None
              and numbers(seen["ward_tenders"]) == FIVE_NUMBERS
              and seen["ward_name"] == "Munnekolala",
              f"no street tender: the resolution lost the ward list or name: {seen}")
        check(all(sorted(entry) == ["published", "tender_number", "title"]
                  for entry in seen["ward_tenders"] or []),
              f"the resolution carries unsanitised ward tenders: {seen['ward_tenders']}")
        STATE["tender"] = STREET
        seen = page.evaluate(RESOLVE, "ward-unit-street-tender")
        check(seen["tender"] == STREET["tender_number"]
              and numbers(seen["ward_tenders"]) == FIVE_NUMBERS
              and seen["ward_name"] == "Munnekolala",
              f"street tender: the resolution lost the ward list or name: {seen}")
        STATE["tender"] = None
        STATE["mode"] = "nowhere"
        seen = page.evaluate(RESOLVE, "ward-unit-no-jurisdiction")
        check(seen["reached"] and numbers(seen["ward_tenders"]) == FIVE_NUMBERS
              and seen["ward_name"] is None,
              f"no jurisdiction: the resolution lost the ward list: {seen}")
        STATE["mode"] = "municipal"
        STATE["ward"] = None
        seen = page.evaluate(RESOLVE, "ward-unit-older-service")
        check(seen["reached"] and seen["ward_tenders"] == [],
              f"a service that sends no list must read as an empty one: {seen}")
        STATE["ward"] = "not a list"
        STATE["ward_name"] = 41
        seen = page.evaluate(RESOLVE, "ward-unit-malformed")
        check(seen["ward_tenders"] == [] and seen["ward_name"] is None,
              f"a malformed list or name was passed through: {seen}")
        STATE["ward"], STATE["ward_name"] = FIVE, "Munnekolala"
        STATE["mode"] = "down"
        seen = page.evaluate(RESOLVE, "ward-unit-down")
        check(seen["reached"] is False and seen.get("ward_tenders") is None,
              f"an unreached lookup must not claim a ward list: {seen}")
        STATE["mode"] = "municipal"

        # ---- capture stores it, and it survives a reload ----
        report = page.evaluate(fh.report_form_script(12.9562, 77.7141))
        check(report.get("status") == "draft", f"the fixture did not route: {report.get('status')}")
        check(numbers(report.get("ward_tenders")) == FIVE_NUMBERS
              and report.get("ward_name") == "Munnekolala",
              f"capture did not keep the ward list and name: {report.get('ward_tenders')!r} "
              f"{report.get('ward_name')!r}")
        captured_id = report["id"]
        page.reload()
        page.wait_for_function("() => !!window.StandaloneAPI", timeout=30_000)
        page.locator("#home").wait_for(state="visible", timeout=30_000)
        stored = page.evaluate(STORED, captured_id)
        check(numbers(stored["ward_tenders"]) == FIVE_NUMBERS and stored["ward_name"] == "Munnekolala",
              f"the ward list did not survive a reload: {stored['ward_tenders']!r}")
        full = page.evaluate("(id) => StandaloneAPI.handle(`/api/reports/${id}`)", captured_id)
        check(numbers(full.get("ward_tenders")) == FIVE_NUMBERS,
              "the detail read of a report does not carry its ward list")
        listed = page.evaluate("() => StandaloneAPI.handle('/api/history')")["reports"]
        check(listed and all("ward_tenders" not in row for row in listed),
              "History rows carry the ward list, which only the detail screen reads")
        page.evaluate("""async (id) => {
          const row = (await StandaloneAPI.handle('/api/history')).reports.find((r) => r.id === id);
          openDetail(row, [row]);
        }""", captured_id)
        try:
            page.wait_for_selector("#detail details.ward-tenders", state="attached", timeout=10_000)
        except Exception:
            fails.append("a report opened from History never shows its ward list")
        page.evaluate("show('home')")

        # ---- the complaint never mentions it ----
        for field in ("email_subject", "email_body", "whatsapp_text", "portal_copy_text"):
            text = stored.get(field) or ""
            leaked = [n for n in FIVE_NUMBERS if n in text] + (["title"] if LONG in text else [])
            check(not leaked, f"{field} of the captured report mentions ward tenders: {leaked}")
        with_list = page.evaluate(PREPARE, [captured_id, True])
        STATE["ward"] = []
        without = page.evaluate(PREPARE, [captured_id, False])
        STATE["ward"] = FIVE
        check(with_list["body"] and with_list["body"].encode("utf-8") == without["body"].encode("utf-8"),
              "the prepared email body differs when the record has ward tenders")
        check(with_list["subject"] == without["subject"] and with_list["to"] == without["to"],
              "the prepared email subject or recipient differs when the record has ward tenders")
        check(not [n for n in FIVE_NUMBERS if n in with_list["body"] or n in with_list["subject"]],
              "the prepared email names a ward tender")

        # ---- send-time revalidation: an answer replaces the list, an outage leaves it ----
        older = [ward_tender(91), ward_tender(92)]
        newer = [ward_tender(31), ward_tender(32), ward_tender(33)]
        page.evaluate(STALE, [captured_id, older])
        STATE["ward"], STATE["ward_name"] = newer, "Marathahalli"
        sent = page.evaluate(
            "(id) => StandaloneAPI.handle(`/api/reports/${id}/send`, { method: 'POST' })"
            ".then((r) => ({ ok: true, status: r.status }), (e) => ({ ok: false, message: e.message }))",
            captured_id)
        check(sent.get("ok"), f"Email on the revalidated draft failed: {sent}")
        stored = page.evaluate(STORED, captured_id)
        check(numbers(stored["ward_tenders"]) == numbers(newer) and stored["ward_name"] == "Marathahalli",
              f"a revalidation that reached the service kept the old list: "
              f"{numbers(stored['ward_tenders'])} {stored['ward_name']!r}")
        composed = page.evaluate("() => window.__composerCalls.slice(-1)[0] || null")
        check(composed and composed["body"].encode("utf-8") == with_list["body"].encode("utf-8"),
              "the email the composer received differs from the one prepared without ward tenders")
        STATE["ward"], STATE["ward_name"] = [], None
        page.evaluate(STALE, [captured_id, older])
        page.evaluate(
            "(id) => StandaloneAPI.handle(`/api/reports/${id}/send`, { method: 'POST' })"
            ".catch(() => null)", captured_id)
        stored = page.evaluate(STORED, captured_id)
        check(stored["ward_tenders"] == [] and stored["ward_name"] is None,
              f"an answer with no ward tenders must clear the stored list: {stored['ward_tenders']}")
        page.evaluate(STALE, [captured_id, older])
        STATE["mode"] = "down"
        page.evaluate(
            "(id) => StandaloneAPI.handle(`/api/reports/${id}/send`, { method: 'POST' })"
            ".catch(() => null)", captured_id)
        stored = page.evaluate(STORED, captured_id)
        check(numbers(stored["ward_tenders"]) == numbers(older) and stored["ward_name"] == "Older Ward",
              f"a revalidation that never reached the service changed the stored list: "
              f"{numbers(stored['ward_tenders'])} {stored['ward_name']!r}")

        # ---- retry routing: unrouted in an outage, then the answer brings the list ----
        outage = page.evaluate(fh.report_form_script(12.9563, 77.7142))
        check(outage.get("status") == "unrouted" and not outage.get("ward_tenders"),
              f"setup: the outage did not file an unrouted report: {outage.get('status')} "
              f"{outage.get('ward_tenders')!r}")
        page.evaluate(STALE, [outage["id"], older])
        retried = page.evaluate(
            "(id) => StandaloneAPI.handle(`/api/reports/${id}/retry-routing`, { method: 'POST' })",
            outage["id"])
        check(numbers(retried.get("ward_tenders")) == numbers(older),
              f"a retry with the resolver still down changed the stored list: {retried.get('ward_tenders')!r}")
        STATE["mode"], STATE["ward"], STATE["ward_name"] = "municipal", FIVE, "Munnekolala"
        retried = page.evaluate(
            "(id) => StandaloneAPI.handle(`/api/reports/${id}/retry-routing`, { method: 'POST' })",
            outage["id"])
        check(retried.get("status") == "draft"
              and numbers(retried.get("ward_tenders")) == FIVE_NUMBERS
              and retried.get("ward_name") == "Munnekolala",
              f"retry routing did not store the ward list: {retried.get('status')} "
              f"{retried.get('ward_tenders')!r} {retried.get('ward_name')!r}")
        stored = page.evaluate(STORED, outage["id"])
        check(numbers(stored["ward_tenders"]) == FIVE_NUMBERS,
              "the retried report did not save its ward list")

        # ---- the card ----
        page.set_viewport_size({"width": 360, "height": 780})
        card = page.evaluate(CARD, {"ward_tenders": FIVE, "ward_name": "Munnekolala"})
        check(card["shown"], "a report with ward tenders shows no list on its card")
        if card["shown"]:
            check(card["open"] is False, "the ward tender list starts open")
            check(card["summary"] == "Road works tendered in this ward: 5",
                  f"summary reads {card['summary']!r}")
            check(card["note"] == "From public tender notices. Not confirmed for this exact road.",
                  f"the note reads {card['note']!r}")
            check(len(card["items"]) == 5, f"the card lists {len(card['items'])} of 5 ward tenders")
            first, undated, hostile = card["items"][:3]
            check(first["title"] == FIVE[0]["title"]
                  and first["ref"] == f"{FIVE[0]['tender_number']} · Published 12-08-2025",
                  f"the first ward tender reads {first}")
            check(undated["ref"] == FIVE[1]["tender_number"],
                  f"a ward tender with no date reads {undated['ref']!r}")
            check(hostile["title"] == XSS and not card["injected"] and not card["ran"],
                  f"a ward tender title was rendered as markup: {hostile['title']!r} "
                  f"nodes={card['injected']} ran={card['ran']}")
            closed = page.evaluate(FIT)
            page.locator("#detail details.ward-tenders summary").click()
            page.wait_for_function(
                "() => document.querySelector('#detail details.ward-tenders').open")
            opened = page.evaluate(FIT)
            for name, fit in (("closed", closed), ("open", opened)):
                check(not fit["wide"] and fit["page"] <= fit["screen"],
                      f"at 360 px the {name} list overflows: {fit['wide']} "
                      f"page {fit['page']} of {fit['screen']}")
            check(opened["height"] > closed["height"] + 100,
                  f"opening the list showed nothing: {closed['height']} to {opened['height']} px")

        with_street = page.evaluate(CARD, {
            "ward_tenders": FIVE[:2], "tender_number": STREET["tender_number"],
            "tender_note": "Probable contract: BBMP/2024-25/RD/WORK_INDENT77, Test Builders, published 03-02-2025"})
        check(with_street["shown"] and with_street["tenderNote"]
              and with_street.get("summary") == "Road works tendered in this ward: 2",
              f"beside a street-level tender the ward list is missing: {with_street}")
        native = page.evaluate(CARD, {"ward_tenders": FIVE, "_native": True, "_nativeId": 7001,
                                      "road_ownership_source": None})
        check(native["shown"] and len(native.get("items", [])) == 5,
              "a Drive Mode report with ward tenders shows no list on its card")
        for name, overrides in (
                ("an empty list", {"ward_tenders": []}),
                ("no list", {}),
                ("a wiped list", {"ward_tenders": None}),
                ("a list of nothing usable", {"ward_tenders": [{"title": "No number"}, 7]}),
                ("ownership the service never proved",
                 {"ward_tenders": FIVE, "road_ownership_source": None}),
                ("a road that is not municipal",
                 {"ward_tenders": FIVE, "road_ownership": "state_highway"})):
            hidden = page.evaluate(CARD, overrides)
            check(not hidden["shown"], f"{name}: the card still shows a ward tender list")

        fails += fh.error_failures(errors, "ward tenders")
    finally:
        browser.close()

    # ---- every language says it ----
    for lang in ("kn", "mr", "bn"):
        browser, page, errors = fh.open_flow(playwright, storage={"app_lang": lang})
        try:
            page.locator("#home").wait_for(state="visible", timeout=30_000)
            page.set_viewport_size({"width": 360, "height": 780})
            card = page.evaluate(CARD, {"ward_tenders": FIVE})
            want = page.evaluate("""() => ({
              summary: I18N[LANG].ward_tenders_summary, note: I18N[LANG].ward_tenders_note,
              published: I18N[LANG].ward_tenders_published })""")
            check(card["shown"] and want["summary"] and want["note"] and want["published"]
                  and card["summary"] == want["summary"].replace("{n}", "5")
                  and card["note"] == want["note"]
                  and want["published"].replace("{date}", "12-08-2025") in card["items"][0]["ref"],
                  f"{lang}: the ward tender list is not in the chosen language: {card}")
            for english in ("Road works", "tender notices", "Published"):
                check(english not in json.dumps(card, ensure_ascii=False),
                      f"{lang}: the ward tender list still says {english!r}")
            if card["shown"]:
                page.locator("#detail details.ward-tenders summary").click()
                fit = page.evaluate(FIT)
                check(not fit["wide"] and fit["page"] <= fit["screen"],
                      f"{lang}: at 360 px the open list overflows: {fit['wide']}")
            fails += fh.error_failures(errors, f"{lang} ward tenders")
        finally:
            browser.close()

if fails:
    print("FAIL ward tenders")
    for fail in fails:
        print("  -", fail)
    sys.exit(1)
print("PASS ward tenders")
