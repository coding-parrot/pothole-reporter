# -*- coding: utf-8 -*-
"""A report in a Karnataka town with no published address says what the tester can do.

137 of Karnataka's 319 towns (28 city municipal councils among them: Mandya, Kolar,
Koppal) have no entry in the bodies registry. Their reports ended on "No published
address" with a Retry button, and the card said the address "can be added to the
directory". The registry is a static pack, so Retry could only repeat the miss. The
card now hides Retry and points the tester at Karnataka Janaspandana.
"""

import json
import sys
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

import flow_harness as fh


def unlisted_town(route, request):
    if urlparse(request.url).path != "/v1/tenders/resolve":
        return fh.central_service(route, request)
    body = json.loads(request.post_data or "{}")
    fh.envelope(route, {
        "jurisdiction": {
            "lat": body.get("lat"), "lng": body.get("lng"), "address": None,
            # Not an LGD code in the bodies registry.
            "lgd": "999999", "town": "Mandya City Municipal Council", "source": "kgis",
            "address_source": "unresolved", "road_ownership": "municipal",
        },
        "tender": None, "reason": "no_tenders_for_jurisdiction",
    })


fails = []
with sync_playwright() as playwright:
    browser, page, errors = fh.open_flow(playwright, native=False)
    try:
        page.context.route(f"{fh.SERVICE}/**", unlisted_town)
        report = page.evaluate(fh.report_form_script(12.5218, 76.8951))
        print("  status:", report.get("status"), report.get("unrouted_reason"))
        if report.get("unrouted_reason") != "no_address_for_body":
            fails.append(f"the fixture did not file no_address_for_body: "
                         f"{report.get('unrouted_reason')!r}")
        if page.evaluate("(r) => canRetryRouting(r)", report):
            fails.append("no_address_for_body still offers Retry routing")
        refused = page.evaluate("""async (id) => {
          try { await StandaloneAPI.handle(`/api/reports/${id}/retry-routing`, {method: "POST"});
                return null; }
          catch (error) { return error.message; } }""", report["id"])
        if not refused:
            fails.append("the retry route still re-runs a static-pack miss")
        copy = page.evaluate("""(r) => { const saved = LANG, out = {};
          for (const lang of ["en", "kn", "mr", "bn"]) { LANG = lang; out[lang] = unroutedHelp(r); }
          LANG = saved; return out; }""", report)
        for lang, text in copy.items():
            print(f"  {lang}: {text}")
            if "ipgrs.karnataka.gov.in" not in text:
                fails.append(f"{lang} help does not offer the Janaspandana handoff")
            if "directory" in text or any(ch in text for ch in "\u2013\u2014"):
                fails.append(f"{lang} help promises a directory update or has a dash")
        fails += fh.error_failures(errors, "no-address handoff")
    finally:
        browser.close()

if fails:
    print("FAIL no-address handoff")
    for fail in fails:
        print("  -", fail)
    sys.exit(1)
print("PASS no-address handoff")
