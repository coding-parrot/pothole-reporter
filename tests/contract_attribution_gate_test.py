#!/usr/bin/env python3
"""Only complete official responsibility evidence may enter outbound complaint copy."""

from playwright.sync_api import sync_playwright


SCENARIO = r"""
() => {
  const P = StandaloneAPI.__pure;
  const capturedAt = 1787625000;
  const assessment = {
    size: "medium", surface_type: "bituminous_asphalt",
    measurement_provenance: "visual_estimate_no_scale", measurement_confidence: "low",
  };
  const route = {
    routed: true, issue_type: "road_damage", tender_eligible: true,
    authority_id: "ka-lgd-305852", authority_name: "Bengaluru South City Corporation",
    officer_name: "Commissioner, Bengaluru South City Corporation",
    routing_source: "kgis", routing_match_field: "lgd", routing_match_value: "305852",
    routing_pack_state_code: "KA", contract_state_code: "KA", region: "karnataka",
    road_owner_id: "ka-lgd-305852", road_owner_name: "Bengaluru South City Corporation",
    road_owner_evidence: {
      verified: true, segment_identity: "Official segment 17M-01",
      reference: "Road register 17M-01",
      source_url: "https://roads.karnataka.gov.in/register/17M-01",
    },
  };
  const tender = {
    tender_number: "BBMP/2026/RD/42", title: "Resurfacing of 17th Main Road",
    contractor: "Verified Roads Limited", organisation: "Bengaluru South City Corporation",
    source_name: "Official work-order register",
    source_url: "https://roads.karnataka.gov.in/contracts/42",
    tender_pack_id: "in-ka-tenders", tender_pack_version: 1,
    tender_pack_sha256: "a".repeat(64), tender_pack_state_code: "KA",
    segment_verified: true, award_verified: true, dlp_verified: true,
    responsibility_active_verified: true, unambiguous: true,
    responsibility_valid_from: "2026-01-01T00:00:00Z",
    responsibility_valid_until: "2027-01-01T00:00:00Z",
    responsible_authority_id: "ka-lgd-305852",
    verification_evidence: {
      segment: {reference: "Schedule segment 17M-01",
        source_url: "https://roads.karnataka.gov.in/contracts/42/segment"},
      award: {reference: "Work order 42",
        source_url: "https://roads.karnataka.gov.in/contracts/42/award"},
      responsibility: {reference: "DLP schedule 42",
        source_url: "https://roads.karnataka.gov.in/contracts/42/dlp"},
    },
  };
  const render = (candidate, candidateRoute = route, observed = capturedAt) =>
    P.buildComplaintOutputs(assessment, 12.912345, 77.612345,
      "17th Main Road, HSR Layout, Bengaluru", candidateRoute.officer_name,
      candidate, candidateRoute, {captured_at: observed, gps_accuracy: 8});
  const complete = render(tender);
  const missing = Object.fromEntries([
    ["segment", {...tender, segment_verified: false}],
    ["award", {...tender, award_verified: false}],
    ["dlp", {...tender, dlp_verified: false}],
    ["active", {...tender, responsibility_active_verified: false}],
    ["ambiguity", {...tender, unambiguous: false}],
    ["official_source", {...tender, source_url: "https://example.com/contracts/42"}],
    ["expired", tender],
    ["wrong_owner", {...tender, responsible_authority_id: "ka-lgd-305851"}],
  ].map(([name, candidate]) => [name, render(candidate,
      route, name === "expired" ? Date.parse("2028-01-01T00:00:00Z") / 1000 : capturedAt)]));

  const stale = P.migrateLegacyComplaintRecord({
    id: 42, status: "draft", issue_type: "road_damage", complaint_template_version: 3,
    email_body: `Dear authority,\n\nCONTRACT CANDIDATE\nStatus: Candidate only\nTender number: BAD-42\nExact work name: Garden repair near 17th Main Road\nListed contractor: Wrong Person\n\nPlease repair.`,
    whatsapp_text: "Pothole report\nContract: candidate BAD-42; contractor Wrong Person.\nPothole Reporter is an independent app. Please verify any suggested authority, ward, road ownership, and tender details.",
    portal_fields: {
      coordinates: "12.912345, 77.612345", contract_candidate_status: "candidate",
      tender_number: "BAD-42", exact_work_name: "Garden repair near 17th Main Road",
      listed_contractor: "Wrong Person", contract_source_url: "https://example.com/bad",
    },
    portal_copy_text: "tender number: BAD-42\nlisted contractor: Wrong Person",
  });
  const scopeRejects = [
    "Providing bituminous concrete on footpath at MG Road",
    "Providing dense bituminous macadam on park walkway near MG Road",
    "Maintenance of garden adjoining newly resurfaced MG Road",
    "Repair of garden wall along pothole road",
    "Providing seal coat to bridge deck at NH 48",
    "Providing wet mix macadam for playground walkway",
    "Resurfacing of Lawn Tennis courts with 3 layer Acrylic system at Fatorda, Goa",
    "Providing Asphalting to CKC garden and surrounding areas in Ward No 160",
  ].map((title) => [title, P.tenderCoversCarriageway(title)]);
  return {complete, missing, stale, scopeRejects};
}
"""


def main() -> None:
    failures: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("http://localhost:8765/web-app/")
        page.wait_for_function(
            "() => window.StandaloneAPI && StandaloneAPI.__pure "
            "&& typeof StandaloneAPI.__pure.verifiedContractForComplaint === 'function'"
        )
        result = page.evaluate(SCENARIO)
        browser.close()

    identity = ("BBMP/2026/RD/42", "Verified Roads Limited")
    for label, text in {
        "email": result["complete"]["email_body"],
        "WhatsApp": result["complete"]["whatsapp_text"],
        "portal": result["complete"]["portal_copy_text"],
    }.items():
        if not all(value in text for value in identity):
            failures.append(f"{label} omitted fully verified attribution")

    for gate, output in result["missing"].items():
        combined = "\n".join((output["email_body"], output["whatsapp_text"],
                              output["portal_copy_text"]))
        if any(value in combined for value in identity):
            failures.append(f"missing {gate} gate still leaked contract identity")
        if "No verified exact-road public contract found" not in combined:
            failures.append(f"missing {gate} gate did not state fail-closed result")

    stale = result["stale"]
    stale_combined = "\n".join((stale["email_body"], stale["whatsapp_text"],
                                stale["portal_copy_text"]))
    for leaked in ("BAD-42", "Garden repair near 17th Main Road", "Wrong Person"):
        if leaked in stale_combined:
            failures.append(f"stale v3 draft retained unsafe identity: {leaked}")
    if stale["complaint_template_version"] != 4:
        failures.append("stale unsafe draft was not migrated to template v4")

    accepted_scope = [title for title, accepted in result["scopeRejects"] if accepted]
    if accepted_scope:
        failures.append(f"non-road surface treatments passed classifier: {accepted_scope!r}")

    if failures:
        raise SystemExit("contract attribution gate failed:\n- " + "\n- ".join(failures))
    print("contract attribution gate passed")


if __name__ == "__main__":
    main()
