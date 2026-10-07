# -*- coding: utf-8 -*-
"""The road-damage email is short, states facts, and asks the office to verify.

The old letter told an officer which body covered the point, which ward it was in, who
owned the road and whether a contract had been verified for it. None of that is the
app's to assert. The letter now gives the place, the time, the photo and the size, and
asks the office to confirm two things: that the road is its own, and whether one named
road work from a public tender notice covers the spot.

What this holds:
  - the English text, character for character, with no road work, with a street-level
    tender and with a ward tender;
  - a line with nothing to say is left out, and a title that ends in a full stop does
    not get a second one;
  - Kannada, Marathi and Bengali letters have the same parts and are still told apart
    by their greeting;
  - no letter and no WhatsApp text makes a mapping or contract claim, and none carries
    the old footer;
  - an unsent draft still holding the previous template's text, exactly as the app wrote
    it, is written again when it is shown and when it is sent, while a draft with the
    person's own words in it and a sent complaint are left alone.
"""

import json
import sys
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright

import flow_harness as fh

# 7 Oct 2026, 9:15:00 am IST.
CAPTURED = int(datetime(2026, 10, 7, 3, 45, 0, tzinfo=timezone.utc).timestamp())
FORBIDDEN = ("Verified", "ROUTING", "Road owner/maintainer", "Suggested ward", "DLP",
             "Road-segment match", "No verified exact-road")
OLD_FOOTER = ("Pothole Reporter is an independent app. Please verify any suggested authority, "
              "ward, road ownership, and tender details.")
STREET = {"tender_number": "BBMP/2025-26/RD/WORK-42",
          "title": "Resurfacing of 17th Main Road in HSR Layout",
          "contractor": "ACME Roads Pvt Ltd", "published": "01-02-2026"}
WARD = [{"tender_number": f"BBMP/2025-26/RD/WORK_INDENT{n}",
         "title": f"Improvements to roads in HSR Layout ward no.174, package {n}",
         "published": None if n == 1 else "12-08-2025"} for n in (1, 2, 3, 4)]

HEAD = """Dear Commissioner, Bengaluru South City Corporation,

I wish you a pleasant day. Please register this pothole complaint.

Location: 17th Main Road, HSR Layout, Bengaluru
Coordinates: 12.911600, 77.638900 (GPS accuracy ±5 m)
Map: https://maps.google.com/?q=12.911600,77.638900
Photographed: 7 Oct 2026, 9:15:00 am IST
Photo: attached
Size: medium (visual estimate)

"""
TAIL = """

I would appreciate if you could repair the pothole and share the complaint number.

Thank you for your service.

Regards,
Test Citizen"""
NO_WORK = HEAD + "Please verify that this road is maintained by your office." + TAIL
STREET_WORK = HEAD + (
    "Please verify that this road is maintained by your office and if this location is "
    "covered by - BBMP/2025-26/RD/WORK-42: Resurfacing of 17th Main Road in HSR Layout. "
    "Contractor listed: ACME Roads Pvt Ltd. Published 01-02-2026.") + TAIL
WARD_WORK = HEAD + (
    "Please verify that this road is maintained by your office and if this location is "
    "covered by - BBMP/2025-26/RD/WORK_INDENT1: Improvements to roads in HSR Layout ward "
    "no.174, package 1.") + TAIL
WHATSAPP_STREET = """Pothole report: 17th Main Road, HSR Layout, Bengaluru
Coordinates: 12.911600, 77.638900 (GPS accuracy ±5 m)
Map: https://maps.google.com/?q=12.911600,77.638900
Photographed: 7 Oct 2026, 9:15:00 am IST
Size: medium (visual estimate)
Please verify that this road is maintained by your office and if this location is covered by - BBMP/2025-26/RD/WORK-42: Resurfacing of 17th Main Road in HSR Layout. Contractor listed: ACME Roads Pvt Ltd. Published 01-02-2026.
I would appreciate if you could repair the pothole and share the complaint number."""

ROUTES = {
    "en": {"authority_id": "ka-lgd-305852", "authority_name": "Bengaluru South City Corporation",
           "officer_name": "Commissioner, Bengaluru South City Corporation",
           "routing_pack_state_code": "KA", "contract_state_code": "KA"},
    "kn": {"authority_id": "ka-lgd-305852", "authority_name": "Bengaluru South City Corporation",
           "officer_name": "Commissioner, Bengaluru South City Corporation",
           "routing_pack_state_code": "KA", "contract_state_code": "KA"},
    "mr": {"authority_id": "mh-pune", "authority_name": "Pune Municipal Corporation",
           "officer_name": "Commissioner, Pune Municipal Corporation",
           "routing_pack_state_code": "MH", "contract_state_code": "MH"},
    "bn": {"authority_id": "wb-howrah", "authority_name": "Howrah Municipal Corporation",
           "officer_name": "Commissioner, Howrah Municipal Corporation",
           "routing_pack_state_code": "WB", "contract_state_code": "WB"},
}

BUILD = r"""
([route, tender, evidence, size, provenance]) => {
  const P = StandaloneAPI.__pure;
  const assessment = P.binaryAssessment({
    is_pothole: true, looks_like_speed_breaker: false, image_quality: "usable",
    surface_type: "bituminous_asphalt", on_drivable_surface: true, has_localized_cavity: true,
    has_unambiguous_lower_interior: true, has_broken_edge_or_rim: true,
    has_depth_or_surface_loss: true, temporal_consistency: "single_view",
    size: size || "medium", description: "A localized cavity with material loss",
  }, false, 1);
  if (!size) assessment.size = null;
  if (provenance) assessment.measurement_provenance = provenance;
  const out = P.buildComplaintOutputs(assessment, 12.9116, 77.6389,
    "17th Main Road, HSR Layout, Bengaluru", route.officer_name, tender,
    { routed: true, routing_source: "Karnataka GIS municipal boundary", tender_eligible: true,
      ward_code: "174", ...route }, evidence);
  return { subject: out.email_subject, body: out.email_body, whatsapp: out.whatsapp_text,
           language: P.storedComplaintLanguage(out.email_body), portal: out.portal_fields };
}
"""

fails = []


def check(ok, message):
    if not ok:
        fails.append(message)


def same(name, got, want):
    if got == want:
        return
    lines_got, lines_want = got.split("\n"), want.split("\n")
    for index in range(max(len(lines_got), len(lines_want))):
        left = lines_got[index] if index < len(lines_got) else "<nothing>"
        right = lines_want[index] if index < len(lines_want) else "<nothing>"
        if left != right:
            fails.append(f"{name}: line {index + 1} reads {left!r}, expected {right!r}")
            return


def evidence(**extra):
    return {"captured_at": CAPTURED, "gps_accuracy": 5.2,
            "photo_provenance": "Pothole Reporter camera evidence", **extra}


def build(page, lang="en", tender=None, size="medium", provenance=None, **extra):
    return page.evaluate(BUILD, [ROUTES[lang], tender, evidence(**extra), size, provenance])


# ---- the stored drafts ----
# An untouched draft written by the previous template, as it sits in IndexedDB.
OLD_BODY = """Dear Commissioner, Kalaburagi,

Please register the following pothole grievance.

LOCATION
Address / landmark: Test Road, Kalaburagi
Coordinates: 17.329700, 76.834300
Map: https://maps.google.com/?q=17.329700,76.834300
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

""" + OLD_FOOTER
ANNOTATED_BODY = OLD_BODY.replace(
    "\n\nRegards,", "\n\nThe pothole is beside the lamp post outside the school gate.\n\nRegards,")
EDITED_BODY = "Dear Commissioner,\n\nThe pothole outside my gate has grown since June. Please fix it.\n\nRegards,\nA Resident"

SEED = r"""
async ([rows, wardTenders]) => {
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
        created_at: 1791344700, captured_at: 1791344700, gps_accuracy: 4,
        decision: "accept", damage_type: "pothole_cavity", assessment: "damaged",
        image_quality: "acceptable", size: "medium", is_pothole: 1, issue_type: "road_damage",
        description: "A cavity with a broken rim.", lat: 17.3297, lng: 76.8343,
        address: "Test Road, Kalaburagi", officer_name: "Commissioner, Kalaburagi",
        officer_email: "commissioner@example.gov.in", authority_id: "lgd-248127",
        authority_name: "Kalaburagi", routing_source: "central_v1", delivery_channel: "email",
        email_subject: "Pothole complaint: Test Road", capture_source: "manual_camera",
        road_ownership: "municipal", road_ownership_source: "central_v1",
        body_lgd: "248127", body_name: "Kalaburagi",
        tender_resolution_checked_at: 1791344700, tender_resolution_reason: "no_match",
        server_pothole_id: 4242, server_duplicate: false, central_sync_pending: false,
        ward_tenders: wardTenders, ward_name: "Test Ward", photo, photo_full: photo, ...row });
      add.onsuccess = () => { ids[name] = add.result; };
    }
    tx.oncomplete = resolve;
    tx.onabort = () => reject(tx.error);
  });
  db.close();
  return ids;
}
"""
ROW = r"""
async (id) => {
  const r = await StandaloneAPI.__pure.getReport(id);
  return { status: r.status, body: r.email_body, subject: r.email_subject,
           version: r.complaint_template_version, whatsapp: r.whatsapp_text };
}
"""
SEND = ("(id) => StandaloneAPI.handle(`/api/reports/${id}/send`, { method: 'POST' })"
        ".then((r) => ({ ok: true, status: r.status }), (e) => ({ ok: false, message: e.message }))")

with sync_playwright() as playwright:
    # ---- the Android app: the photo file is attached ----
    browser, page, errors = fh.open_flow(playwright)
    try:
        page.locator("#home").wait_for(state="visible", timeout=30_000)

        plain = build(page)
        same("no road work", plain["body"], NO_WORK)
        check(plain["subject"] == "Pothole complaint: 17th Main Road",
              f"the subject line changed: {plain['subject']!r}")
        street = build(page, tender=STREET, ward_tenders=WARD)
        same("street-level tender", street["body"], STREET_WORK)
        same("WhatsApp text, street-level tender", street["whatsapp"], WHATSAPP_STREET)
        ward = build(page, ward_tenders=WARD)
        same("ward tender with no contractor and no date", ward["body"], WARD_WORK)
        check(not [entry["tender_number"] for entry in WARD[1:] if entry["tender_number"] in ward["body"]],
              "the letter names more than one road work")

        # One sentence each, closed once.
        dotted = build(page, tender={**STREET, "title": STREET["title"] + ". ",
                                     "contractor": "ACME Roads Pvt. Ltd."})
        check("HSR Layout. Contractor listed: ACME Roads Pvt. Ltd. Published 01-02-2026.\n" in dotted["body"]
              and ".." not in dotted["body"],
              f"a title or contractor ending in a full stop is closed twice: {dotted['body']!r}")
        undated = build(page, tender={**STREET, "contractor": None, "published": ""})
        check("covered by - BBMP/2025-26/RD/WORK-42: Resurfacing of 17th Main Road in HSR Layout.\n\nI would"
              in undated["body"], "a tender with no contractor and no date prints empty parts")
        drain = build(page, tender={**STREET, "title": "Construction of storm water drain at 17th Main Road"},
                      ward_tenders=WARD)
        check(STREET["tender_number"] not in drain["body"] and WARD[0]["tender_number"] in drain["body"],
              "a tender for a drain was offered as the road work for this spot")

        # A line with nothing to say is left out.
        bare = page.evaluate(BUILD, [ROUTES["en"], None, {}, None, None])
        check("Coordinates: 12.911600, 77.638900\nMap:" in bare["body"],
              "with no accuracy recorded the coordinates line still mentions it")
        for label in ("Photographed:", "Size:", "Not recorded", "GPS accuracy"):
            check(label not in bare["body"] and label not in bare["whatsapp"],
                  f"a letter with nothing recorded still prints {label!r}")
        check("Photo: attached\n\nPlease verify" in bare["body"],
              "the photo line is missing when nothing else is recorded")
        measured = build(page, provenance="field_measured")
        check("Size: medium (field measured)" in measured["body"],
              "a field-measured size is called a visual estimate")
        imported = build(page, photo_provenance="User-selected/imported photo")
        check("Photo: attached" in imported["body"],
              "an imported photo is attached too, and the letter does not say so")

        # ---- every language, and nothing claimed in any of them ----
        letters = {"en": street}
        for lang in ("kn", "mr", "bn"):
            page.evaluate("(lang) => localStorage.setItem('app_lang', lang)", lang)
            page.reload()
            page.wait_for_function("() => !!window.StandaloneAPI", timeout=30_000)
            letters[lang] = build(page, lang=lang, tender=STREET, ward_tenders=WARD)
            letters[lang + "-plain"] = build(page, lang=lang)
            check(letters[lang]["language"] == lang and letters[lang + "-plain"]["language"] == lang,
                  f"{lang}: the letter is not recognised as {lang}: {letters[lang]['body'][:40]!r}")
            body = letters[lang]["body"]
            paragraphs = body.split("\n\n")
            check(len(paragraphs) == 7 and len(paragraphs[2].split("\n")) == 6,
                  f"{lang}: the letter has {len(paragraphs)} parts, "
                  f"{len(paragraphs[2].split(chr(10))) if len(paragraphs) > 2 else 0} fact lines")
            for english in ("Please", "Location", "Photographed", "attached", "visual estimate",
                            "Contractor listed", "Published", "Thank you", "Regards", "medium"):
                check(english not in body, f"{lang}: the letter still says {english!r}")
            for part in (STREET["tender_number"], STREET["title"], STREET["contractor"],
                         STREET["published"], " - " + STREET["tender_number"],
                         "https://maps.google.com/?q=12.911600,77.638900", "7 Oct 2026"):
                check(part in body, f"{lang}: the letter lost {part!r}")
            check(STREET["tender_number"] not in letters[lang + "-plain"]["body"]
                  and " - " not in letters[lang + "-plain"]["body"].split("\n\n")[3],
                  f"{lang}: a letter with no road work still points at one")
            check(letters[lang]["whatsapp"] == WHATSAPP_STREET,
                  f"{lang}: the WhatsApp text is not the English one")
        page.evaluate("() => localStorage.removeItem('app_lang')")
        page.reload()
        page.wait_for_function("() => !!window.StandaloneAPI", timeout=30_000)
        page.locator("#home").wait_for(state="visible", timeout=30_000)
        english = build(page)
        check(english["language"] == "en", "the English letter is not recognised as English")
        letters.update({"en-plain": plain, "en-ward": ward, "en-bare": bare})
        footers = page.evaluate(
            "() => ['kn', 'mr', 'bn', 'en'].map((l) => StandaloneAPI.__pure.complaintFooter(l, true))")
        for name, letter in letters.items():
            for text_name in ("body", "whatsapp"):
                text = letter[text_name]
                for claim in FORBIDDEN:
                    check(claim not in text, f"{name} {text_name} still says {claim!r}")
                check(not [footer for footer in footers if footer in text],
                      f"{name} {text_name} still carries the independent-app footer")

        # Portal fields keep their keys. "Verified" appears there only behind the gate.
        check(not [key for key, value in street["portal"].items() if "Verified" in str(value)],
              f"portal fields call an unverified match verified: {street['portal']}")
        check("contract_verification_status" in street["portal"] and "suggested_ward" in street["portal"],
              "portal fields lost keys the portal copy depends on")

        # ---- the footer helper and the stale-letter test ----
        helpers = page.evaluate("""([oldBody, newBody, annotated]) => {
          const P = StandaloneAPI.__pure;
          return { untouched: [oldBody, newBody, annotated, oldBody.replace("LOCATION", "WHERE"),
                               oldBody.replace("Map: ", "See the map at ")]
                     .map((body) => P.untouchedTemplate4RoadLetter(body)),
                   stripped: P.complaintBodyWithFooter(oldBody, "road_damage"),
                   current: P.complaintBodyWithFooter(newBody, "road_damage"),
                   civic: P.complaintBodyWithFooter("Dear Officer,\\n\\nGarbage has piled up.", "garbage"),
                   civicFooter: P.complaintFooter("en", false),
                   stale: [P.staleRoadComplaintBody(oldBody), P.staleRoadComplaintBody(newBody)],
                   version: P.COMPLAINT_TEMPLATE_VERSION };
        }""", [OLD_BODY, NO_WORK, ANNOTATED_BODY])
        check(helpers["untouched"] == [True, False, False, False, False],
              f"the previous template is not told from an edited or a current letter: {helpers['untouched']}")
        check(OLD_FOOTER not in helpers["stripped"] and helpers["stripped"].endswith("Test Citizen"),
              "complaintBodyWithFooter left the old footer on a road-damage body")
        check(helpers["current"] == NO_WORK, "complaintBodyWithFooter changed a current letter")
        check(helpers["civic"].endswith(helpers["civicFooter"]),
              "a civic complaint lost its footer")
        check(helpers["stale"] == [True, False],
              f"old and current letters are not told apart: {helpers['stale']}")
        check(helpers["version"] == 5, f"the template version is {helpers['version']}")

        # ---- drafts already on a phone ----
        ids = page.evaluate(SEED, [{
            "untouched": {"status": "draft", "email_body": OLD_BODY, "complaint_template_version": 4},
            "queued": {"status": "queued", "email_body": OLD_BODY, "complaint_template_version": 4,
                       "email_opened_at": 1791344800},
            "edited": {"status": "draft", "email_body": EDITED_BODY, "email_user_edited": True,
                       "complaint_template_version": 4},
            # Edited before the app began recording edits: only the text can tell.
            "annotated": {"status": "draft", "email_body": ANNOTATED_BODY,
                          "complaint_template_version": 4},
            "sent": {"status": "sent", "email_body": OLD_BODY, "complaint_template_version": 4,
                     "sent_at": 1791344900, "email_sent_confirmed": True},
            "civic": {"status": "draft", "issue_type": "garbage", "complaint_template_version": 4,
                      "email_body": "Dear Commissioner, Kalaburagi,\n\nThe attached photo shows "
                                    "accumulated or uncollected garbage at this location.\n\n"
                                    + "Pothole Reporter is an independent app. Please verify any "
                                      "suggested authority, civic jurisdiction, and complaint category."},
        }, WARD])
        expected = (
            "Dear Commissioner, Kalaburagi,\n\n"
            "I wish you a pleasant day. Please register this pothole complaint.\n\n"
            "Location: Test Road, Kalaburagi\n"
            "Coordinates: 17.329700, 76.834300 (GPS accuracy ±4 m)\n"
            "Map: https://maps.google.com/?q=17.329700,76.834300\n"
            "Photographed: 7 Oct 2026, 9:15:00 am IST\n"
            "Photo: attached\n"
            "Size: medium (visual estimate)\n\n"
            "Please verify that this road is maintained by your office and if this location is "
            "covered by - BBMP/2025-26/RD/WORK_INDENT1: Improvements to roads in HSR Layout ward "
            "no.174, package 1." + TAIL)
        shown = page.evaluate("(id) => StandaloneAPI.handle(`/api/reports/${id}`)", ids["untouched"])
        same("an untouched old draft, when shown", shown["email_body"], expected)
        check(shown["email_subject"] == "Pothole complaint: Test Road", "showing a draft changed its subject")
        listed = {row["id"]: row for row in page.evaluate("() => StandaloneAPI.handle('/api/reports')")}
        same("an untouched old draft, in the report list", listed[ids["untouched"]]["email_body"], expected)
        sent = page.evaluate(SEND, ids["untouched"])
        check(sent.get("ok"), f"Email on the old draft failed: {sent}")
        composed = page.evaluate("() => window.__composerCalls.slice(-1)[0] || null")
        same("an untouched old draft, when sent", composed["body"] if composed else "", expected)
        stored = page.evaluate(ROW, ids["untouched"])
        same("an untouched old draft, saved after sending", stored["body"], expected)
        check(stored["version"] == 5, f"the rewritten draft is marked template {stored['version']}")
        for claim in FORBIDDEN:
            check(claim not in (stored["whatsapp"] or ""), f"the rewritten draft's WhatsApp text says {claim!r}")

        requeued = page.evaluate("(id) => StandaloneAPI.handle(`/api/reports/${id}`)", ids["queued"])
        same("an old draft whose composer was opened but never confirmed", requeued["email_body"], expected)

        for name, body in (("edited", EDITED_BODY), ("annotated", ANNOTATED_BODY), ("sent", OLD_BODY)):
            shown = page.evaluate("(id) => StandaloneAPI.handle(`/api/reports/${id}`)", ids[name])
            check(shown["email_body"] == body, f"a {name} complaint was rewritten when shown")
        sent = page.evaluate(SEND, ids["edited"])
        composed = page.evaluate("() => window.__composerCalls.slice(-1)[0] || null")
        check(sent.get("ok") and composed and composed["body"] == EDITED_BODY,
              f"an edited draft was not sent as its author wrote it: {sent} "
              f"{(composed or {}).get('body', '')[:60]!r}")
        check(page.evaluate(ROW, ids["edited"])["body"] == EDITED_BODY,
              "an edited draft was rewritten in storage")
        check(page.evaluate(ROW, ids["sent"])["body"] == OLD_BODY,
              "a sent complaint was rewritten in storage")
        civic = page.evaluate("(id) => StandaloneAPI.handle(`/api/reports/${id}`)", ids["civic"])
        check("accumulated or uncollected garbage" in civic["email_body"]
              and civic["email_body"].endswith("civic jurisdiction, and complaint category."),
              "a civic complaint draft was changed by the road-letter migration")

        fails += fh.error_failures(errors, "verify request letter")
    finally:
        browser.close()

    # ---- the website: a mailto: link carries no file ----
    browser, page, errors = fh.open_flow(playwright, native=False)
    try:
        page.locator("#home").wait_for(state="visible", timeout=30_000)
        web = build(page, tender=STREET)
        check("Photo: Pothole Reporter camera evidence\n" in web["body"]
              and "Photo: attached" not in web["body"],
              "the website letter says a photo is attached, and a mailto: link cannot carry one")
        same("website letter apart from its photo line",
             web["body"].replace("Photo: Pothole Reporter camera evidence", "Photo: attached"), STREET_WORK)
        fails += fh.error_failures(errors, "verify request letter on the website")
    finally:
        browser.close()

if fails:
    print("FAIL verify request letter")
    for fail in fails:
        print("  -", fail)
    sys.exit(1)
print("PASS verify request letter")
