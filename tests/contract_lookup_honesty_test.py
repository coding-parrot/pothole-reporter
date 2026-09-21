# -*- coding: utf-8 -*-
"""A letter does not report a contract search that never happened.

The central tenders table holds no rows for any body, so the service answers
no_tenders_for_jurisdiction: there was nothing to search. Every letter still said
"No verified exact-road public contract found", which an officer reads as a search
that came back empty. A lookup that had no catalogue says so; a real miss keeps the
old line.
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


def contract_block(text):
    after = (text or "").split("CONTRACT VERIFICATION", 1)
    return after[1].split("\n\n", 1)[0].strip() if len(after) == 2 else ""


fails = []
with sync_playwright() as playwright:
    browser, page, errors = fh.open_flow(playwright, native=False)
    try:
        page.context.route(f"{fh.SERVICE}/**", service)
        report = page.evaluate(fh.report_form_script(12.9116, 77.6389))
        block = contract_block(report.get("email_body"))
        print("  no catalogue:", block)
        if report.get("status") != "draft":
            fails.append(f"the fixture did not route: {report.get('status')}")
        if "found" in block or "unavailable" not in block:
            fails.append("with no catalogue the letter still reports a negative search")
        # History rebuilds a stored letter from the saved reason; it must agree.
        rebuilt = page.evaluate("""async (id) =>
          (await StandaloneAPI.__pure.getReport(id)).tender_resolution_reason""", report["id"])
        if rebuilt != "no_tenders_for_jurisdiction":
            fails.append(f"the report did not keep the service's reason: {rebuilt!r}")
        REASON["value"] = "no_match"
        searched = contract_block(page.evaluate(
            fh.report_form_script(12.9117, 77.6390)).get("email_body"))
        print("  searched:", searched)
        if "No verified exact-road public contract found" not in searched:
            fails.append("a real miss lost the no-verified-contract line")
        fails += fh.error_failures(errors, "contract lookup honesty")
    finally:
        browser.close()

if fails:
    print("FAIL contract lookup honesty")
    for fail in fails:
        print("  -", fail)
    sys.exit(1)
print("PASS contract lookup honesty")
