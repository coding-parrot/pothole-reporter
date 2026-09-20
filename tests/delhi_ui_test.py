# -*- coding: utf-8 -*-
"""Delhi handoff UI must expose verified channels without claiming ownership/submission."""
import sys

from playwright.sync_api import sync_playwright


APP = "http://localhost:8765/"
INIT = """
localStorage.setItem('openai_key', 'test-key-never-sent');
localStorage.setItem('app_lang', 'en');
"""

SCENARIO = r"""
async () => {
  const checks = [];
  const eq = (name, got, want) => checks.push([
    name, JSON.stringify(got) === JSON.stringify(want), got, want,
  ]);
  const ok = (name, value, detail) => checks.push([
    name, !!value, detail === undefined ? value : detail, true,
  ]);

  // The version itself is guarded by tools/snapshot-data-notice.py, which fails when
  // the wording moves without a bump. Here it only has to be a dated, non-empty value.
  ok("privacy: the data notice carries a dated version",
     /^\d{4}-\d{2}-\d{2}-v\d+/.test(DATA_NOTICE_VERSION), DATA_NOTICE_VERSION);
  for (const [lang, dictionary] of Object.entries(I18N)) {
    ok(`scope: ${lang} describes India-wide State/UT coverage`, /India|ಭಾರತ|भारत|ভারত/.test(
      dictionary.outside_coverage_help), dictionary.outside_coverage_help);
    ok(`privacy: ${lang} discloses the state-pack host`,
       /GitHub Pages/.test(dictionary.privacy_government),
       dictionary.privacy_government);
  }

  const P = StandaloneAPI.__pure;
  const complaintFooter = "Pothole Reporter is an independent app. Please verify any "
    + "suggested authority, ward, road ownership, and tender details.";
  const route = await P.delhiRouteFromGeocode(null, 28.6129, 77.2295, 12);
  eq("route: UI fixture uses PWD Sewa", route && route.handoff_name, "PWD Sewa");
  eq("route: UI fixture uses Delhi PGMS fallback",
     route && route.alternate_handoff_name, "Delhi PGMS");
  const [subject, body] = P.draftEmail({
    damage_type: "pothole_cavity", assessment: "clear", size: "medium",
  }, 28.6129, 77.2295, "India Gate, New Delhi", route.officer_name, null, route);
  ok("draft: complaint retains coordinates and map link",
     /28\.612900, 77\.229500/.test(body) && /maps\.google\.com/.test(body), body);
  const complaintBlocks = body.trim().split(/\n{2,}/).map((part) => part.trim());
  const complaintWithoutFooter = complaintBlocks.slice(0, -1).join("\n\n");
  ok("draft: complaint has one final independent-app verification footer",
     body.split(complaintFooter).length - 1 === 1
       && complaintBlocks.at(-1) === complaintFooter, body);
  ok("draft: complaint names the route without old no-submission wording",
     body.includes(route.authority_name)
       && !/does not submit (?:a|the) grievance|official (?:grievance )?submission/i.test(body),
     body);
  ok("draft: Delhi route keeps a truthful mandatory no-candidate contract block",
     /CONTRACT VERIFICATION/.test(complaintWithoutFooter)
       && /No verified exact-road public contract found/.test(complaintWithoutFooter)
       && !/Tender number:|Listed contractor:|Exact work name:/.test(complaintWithoutFooter)
       && !/warranty is active|under warranty/i.test(complaintWithoutFooter), body);

  const now = Date.now() / 1000;
  const report = {
    id: 73001, created_at: now, captured_at: now, status: "draft",
    decision: "accept", damage_type: "pothole_cavity", assessment: "clear",
    image_quality: "usable", size: "medium", address: "India Gate, New Delhi",
    lat: 28.6129, lng: 77.2295,
    photo: "data:image/png;base64,iVBORw0KGgo=", photo_full: null,
    email_subject: subject, email_body: body, officer_email: null,
    officer_name: route.officer_name, authority_id: route.authority_id,
    authority_name: route.authority_name,
    authority_registry_version: route.authority_registry_version,
    delivery_channel: route.delivery_channel, region: route.region,
    routing_source: route.routing_source, routing_match_field: route.routing_match_field,
    routing_match_value: route.routing_match_value,
    ownership_unverified: route.ownership_unverified,
    handoff_name: route.handoff_name, handoff_url: route.handoff_url,
    handoff_package: route.handoff_package,
    alternate_handoff_name: route.alternate_handoff_name,
    alternate_handoff_url: route.alternate_handoff_url,
    whatsapp_url: route.whatsapp_url, helpline: route.helpline,
    requires_official_reference: true, official_grievance_id: null,
  };
  // Email is the only complaint channel the app offers. The Delhi portal, WhatsApp and
  // helpline handoffs were removed because opening another service proves nothing about
  // whether a complaint was filed, and the detail screen must not imply that it does.
  openDetail(report, [report]);
  const waitingText = document.getElementById("detail").textContent;
  ok("detail: a report still awaiting the shared-map check offers no email",
     !document.getElementById("sendBtn")
       && /shared map to confirm/i.test(waitingText), waitingText);

  report.server_pothole_id = 73001;
  openDetail(report, [report]);
  const detailText = document.getElementById("detail").textContent;
  ok("detail: shows the Delhi address the complaint is about",
     detailText.includes("India Gate, New Delhi"), detailText);
  ok("detail: a confirmed report offers email and no second channel",
     !!document.getElementById("sendBtn")
       && document.getElementById("sendBtn").dataset.complaintAction === "email"
       && !/WhatsApp|PGMS|1908|Official grievance/i.test(detailText), detailText);

  return checks;
}
"""


def main():
    failures = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=["--disable-web-security"])
        context = browser.new_context(viewport={"width": 390, "height": 844})
        context.add_init_script(INIT)
        page = context.new_page()
        page.goto(APP)
        page.wait_for_load_state("networkidle")
        page.wait_for_function(
            "typeof I18N !== 'undefined' && typeof openDetail === 'function' "
            "&& typeof StandaloneAPI !== 'undefined'",
            timeout=30000,
        )
        results = page.evaluate(SCENARIO)
        context.close()
        browser.close()

    for name, passed, got, want in results:
        if not passed:
            failures.append(name)
            print(f"  FAIL {name}\n         got  {got}\n         want {want}")
    if failures:
        print(f"{len(failures)} of {len(results)} failed")
        sys.exit(1)
    print(f"DELHI UI TEST PASS ({len(results)} checks)")


main()
