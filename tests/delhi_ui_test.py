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

  ok("privacy: background-drive disclosure forces fresh consent",
     /v12-civic-and-hyderabad-gis-disclosure$/.test(DATA_NOTICE_VERSION), DATA_NOTICE_VERSION);
  for (const [lang, dictionary] of Object.entries(I18N)) {
    ok(`scope: ${lang} names Delhi NCT`, /Delhi NCT|ದೆಹಲಿ NCT|दिल्ली NCT|দিল্লি NCT/.test(
      dictionary.outside_coverage_help), dictionary.outside_coverage_help);
    ok(`privacy: ${lang} discloses the state-pack host`,
       /GitHub Pages/.test(dictionary.privacy_government),
       dictionary.privacy_government);
  }

  const P = StandaloneAPI.__pure;
  const route = await P.delhiRouteFromGeocode(null, 28.6129, 77.2295, 12);
  eq("route: UI fixture uses PWD Sewa", route && route.handoff_name, "PWD Sewa");
  eq("route: UI fixture uses Delhi PGMS fallback",
     route && route.alternate_handoff_name, "Delhi PGMS");
  const [subject, body] = P.draftEmail({
    damage_type: "pothole_cavity", assessment: "clear", size: "medium",
  }, 28.6129, 77.2295, "India Gate, New Delhi", route.officer_name, null, route);
  ok("draft: complaint retains coordinates and map link",
     /28\.612900, 77\.229500/.test(body) && /maps\.google\.com/.test(body), body);
  ok("draft: complaint does not assert road ownership or automatic filing",
     /does not prove who owns this road/.test(body)
       && /does not submit a grievance/.test(body) && /PWD Sewa/.test(body), body);
  ok("draft: Delhi route never adds a tender or contractor identity claim",
     !/probable contract match|Tender:|Winning bidder|Contractor:/.test(body), body);

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
  openDetail(report, [report]);
  const detailText = document.getElementById("detail").textContent;
  ok("handoff UI: clearly names PWD Sewa and the Delhi route",
     /PWD Sewa/.test(detailText) && /Delhi road grievance coordination/.test(detailText),
     detailText);
  ok("handoff UI: exposes PGMS, WhatsApp and 1908",
     /Delhi PGMS/.test(detailText) && /WhatsApp/.test(detailText) && /1908/.test(detailText),
     detailText);
  ok("handoff UI: explains that boundary containment is not ownership",
     /does not prove who owns this road/.test(detailText), detailText);
  ok("handoff UI: requires an official reference before marking submitted",
     /Official grievance\/reference ID/.test(detailText)
       && !!document.getElementById("grievanceId"), detailText);
  eq("handoff UI: primary button opens PWD Sewa",
     document.getElementById("sendBtn").textContent.trim(), "Open PWD Sewa");

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
