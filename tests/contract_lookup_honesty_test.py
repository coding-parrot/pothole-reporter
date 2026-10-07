# -*- coding: utf-8 -*-
"""A complaint does not report a contract search that never happened.

The central tenders table holds no rows for any body, so the service answers
no_tenders_for_jurisdiction: there was nothing to search. Every letter still said
"No verified exact-road public contract found", which an officer reads as a search
that came back empty. The letter no longer reports on a search at all: it asks the
office to verify. The portal copy still carries a status, and there a lookup that had
no catalogue says so while a real miss keeps the old line.
"""

import json
import sys
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

import flow_harness as fh

REASON = {"value": "no_tenders_for_jurisdiction"}


def service(route, request):
    if urlparse(request.url).path != "/v1/tenders/resolve":
        return fh.central_service(route, request)
    body = json.loads(request.post_data or "{}")
    fh.envelope(route, {
        "jurisdiction": {
            "lat": body.get("lat"), "lng": body.get("lng"), "address": None,
            "lgd": "305852", "town": "Bengaluru South City Corporation", "source": "kgis",
            "address_source": "unresolved", "road_ownership": "municipal",
        },
        "tender": None, "reason": REASON["value"],
    })


def portal_status(report):
    return (report.get("portal_fields") or {}).get("contract_verification_status") or ""


def letter_reports_search(report):
    text = "\n".join((report.get("email_body") or "", report.get("whatsapp_text") or ""))
    return [word for word in ("CONTRACT VERIFICATION", "Contract verification", "No verified",
                              "Contract lookup", "catalogue") if word in text]


fails = []
with sync_playwright() as playwright:
    browser, page, errors = fh.open_flow(playwright, native=False)
    try:
        page.context.route(f"{fh.SERVICE}/**", service)
        report = page.evaluate(fh.report_form_script(12.9116, 77.6389))
        block = portal_status(report)
        print("  no catalogue:", block)
        if report.get("status") != "draft":
            fails.append(f"the fixture did not route: {report.get('status')}")
        if "found" in block or "unavailable" not in block:
            fails.append("with no catalogue the portal copy still reports a negative search")
        if letter_reports_search(report):
            fails.append(f"with no catalogue the letter reports on a search: {letter_reports_search(report)}")
        if "Please verify that this road is maintained by your office.\n" not in (report.get("email_body") or ""):
            fails.append("with no catalogue the letter does not ask the office to verify the road")
        # History rebuilds a stored letter from the saved reason; it must agree.
        rebuilt = page.evaluate("""async (id) =>
          (await StandaloneAPI.__pure.getReport(id)).tender_resolution_reason""", report["id"])
        if rebuilt != "no_tenders_for_jurisdiction":
            fails.append(f"the report did not keep the service's reason: {rebuilt!r}")
        REASON["value"] = "no_match"
        missed = page.evaluate(fh.report_form_script(12.9117, 77.6390))
        searched = portal_status(missed)
        print("  searched:", searched)
        if "No verified exact-road public contract found" not in searched:
            fails.append("a real miss lost the no-verified-contract line in the portal copy")
        if letter_reports_search(missed):
            fails.append(f"after a real miss the letter reports on a search: {letter_reports_search(missed)}")
        fails += fh.error_failures(errors, "contract lookup honesty")
    finally:
        browser.close()

if fails:
    print("FAIL contract lookup honesty")
    for fail in fails:
        print("  -", fail)
    sys.exit(1)
print("PASS contract lookup honesty")
