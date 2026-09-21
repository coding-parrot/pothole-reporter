# -*- coding: utf-8 -*-
"""A shared-mode report outside Karnataka says why it has no recipient, truthfully.

The central resolver only knows Karnataka: anywhere else it answers outside_state. The
detail card then told a Chennai, Panaji or Delhi tester the point was "outside India's
verified State/UT boundaries", which is false. Shared complaints are routed only inside
Karnataka for now, and the card has to say that in every language.
"""

import json
import sys
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

import flow_harness as fh


def outside_state(route, request):
    if urlparse(request.url).path != "/v1/tenders/resolve":
        return fh.central_service(route, request)
    body = json.loads(request.post_data or "{}")
    fh.envelope(route, {
        "jurisdiction": {"lat": body.get("lat"), "lng": body.get("lng"), "address": None,
                         "source": "kgis", "address_source": "unresolved",
                         "road_ownership": "outside_state"},
        "tender": None, "reason": "outside_state",
    })


COPY = r"""
(report) => {
  const out = {};
  const saved = LANG;
  for (const lang of ["en", "kn", "mr", "bn"]) {
    LANG = lang;
    out[lang] = { title: unroutedTitle(report), help: unroutedHelp(report),
                  generic: unroutedHelp({ ...report, road_ownership: null }) };
  }
  LANG = saved;
  return out;
}
"""

fails = []
with sync_playwright() as playwright:
    browser, page, errors = fh.open_flow(playwright, native=False)
    try:
        page.context.route(f"{fh.SERVICE}/**", outside_state)
        report = page.evaluate(fh.report_form_script(13.0827, 80.2707))
        if report.get("status") != "unrouted" or report.get("unrouted_reason") != "outside_area":
            fails.append(f"Chennai did not file unrouted: {report.get('status')} "
                         f"{report.get('unrouted_reason')}")
        copy = page.evaluate(COPY, report)
        en = copy["en"]
        print("  en title:", en["title"])
        print("  en help:", en["help"])
        if "Karnataka" not in en["title"] + en["help"]:
            fails.append("the card does not say complaints are Karnataka-only")
        if "outside India" in en["help"] or "State/UT" in en["help"]:
            fails.append("the card still claims the point is outside India's boundaries")
        for lang, text in copy.items():
            if text["help"] == text["generic"]:
                fails.append(f"{lang} shows the generic outside-coverage help")
            if any(ch in text["title"] + text["help"] for ch in "\u2013\u2014"):
                fails.append(f"{lang} copy has an em or en dash")
        # The same answer at Email time must not fall back to the boundary sentence.
        message = page.evaluate("""async (id) => {
          const P = StandaloneAPI.__pure;
          try { await P.prepareComplaint(await P.getReport(id)); return null; }
          catch (error) { return error.message; }
        }""", report["id"])
        print("  email-time:", message)
        if not message or "Karnataka" not in message or "State/UT" in message:
            fails.append(f"the Email-time reason is not the Karnataka one: {message!r}")
        fails += fh.error_failures(errors, "shared outside Karnataka")
    finally:
        browser.close()

if fails:
    print("FAIL shared outside Karnataka")
    for fail in fails:
        print("  -", fail)
    sys.exit(1)
print("PASS shared outside Karnataka")
