# -*- coding: utf-8 -*-
"""The generated email is concise, complete, and does not overclaim.

The detector has one accepted road-defect class: pothole. Candidate public records
must never appear in outbound copy until every responsibility gate is verified.
"""
import sys

from playwright.sync_api import sync_playwright


FOOTER = (
    "Pothole Reporter is an independent app. Please verify any suggested authority, "
    "ward, road ownership, and tender details."
)

JS = r"""
(() => {
  const P = StandaloneAPI.__pure;
  const assessment = P.binaryAssessment({
    is_pothole: true,
    looks_like_speed_breaker: false,
    image_quality: "usable",
    surface_type: "bituminous_asphalt",
    on_drivable_surface: true,
    has_localized_cavity: true,
    has_broken_edge_or_rim: true,
    has_depth_or_surface_loss: true,
    temporal_consistency: "single_view",
    size: "medium",
    description: "A localized cavity with material loss",
  }, false, 1);
  const route = {
    routed: true,
    authority_id: "ka-lgd-305852",
    authority_name: "Bengaluru South City Corporation",
    officer_name: "Commissioner, Bengaluru South City Corporation",
    routing_source: "Karnataka GIS municipal boundary",
    routing_match_field: "town_lgd_code",
    routing_match_value: "305852",
    handoff_name: "Namma Bengaluru (Sahaaya 2.0)",
    region: "karnataka",
    routing_pack_state_code: "KA",
    contract_state_code: "KA",
    tender_eligible: true,
  };
  const tender = {
    tender_number: "BBMP/2025-26/RD/WORK-42",
    title: "Resurfacing of 17th Main Road in HSR Layout",
    contractor: "ACME Roads Pvt Ltd",
    published: "01-02-2026",
    source_name: "Karnataka Public Procurement Portal (KPPP) snapshot",
    source_url: "https://kppp.karnataka.gov.in/",
    tender_pack_id: "in-ka-tenders",
    tender_pack_version: 1,
    tender_pack_sha256: "a".repeat(64),
    tender_pack_state_code: "KA",
  };
  const evidence = {
    captured_at: 1787625000,
    gps_accuracy: 8,
    photo_provenance: "Pothole Reporter camera evidence",
  };
  return {
    matched: P.buildComplaintOutputs(assessment, 12.912345, 77.612345,
      "17th Main Road, HSR Layout, Bengaluru", route.officer_name, tender, route, evidence),
    noCandidate: P.buildComplaintOutputs(assessment, 12.912345, 77.612345,
      "17th Main Road, HSR Layout, Bengaluru", route.officer_name, null, route, evidence),
    rejectedScope: P.normaliseTenderMatch({...tender,
      tender_number: "BBMP/2023-24/OW/WORK_INDENT2505",
      title: "Construction of drain and footpath"}, route),
  };
})()
"""


def require(failures, condition, message):
    if not condition:
        failures.append(message)


def main():
    failures = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=["--disable-web-security"])
        page = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
        page.goto("http://localhost:8765/")
        page.wait_for_load_state("networkidle")
        page.wait_for_function(
            "typeof StandaloneAPI !== 'undefined' && StandaloneAPI.__pure",
            timeout=30000,
        )
        result = page.evaluate(JS)
        browser.close()

    matched = result["matched"]
    body = matched["email_body"]
    no_candidate = result["noCandidate"]["email_body"]

    print("  generated structured complaint:")
    for line in body.splitlines():
        if line.strip():
            print(f"    | {line[:112]}")

    require(failures, matched["email_subject"] == "Pothole complaint — 17th Main Road",
            "subject is not the concise road-specific subject")
    for heading in ("LOCATION", "CLASSIFICATION", "ROUTING", "CONTRACT VERIFICATION"):
        require(failures, body.count(heading) == 1,
                f"email must contain exactly one {heading} section")
    for expected in (
        "Address / landmark: 17th Main Road, HSR Layout, Bengaluru",
        "Coordinates: 12.912345, 77.612345",
        "Map: https://maps.google.com/?q=12.912345,77.612345",
        "Defect decision: Pothole — YES",
        "Surface: Bituminous / asphalt",
        "App visual size class: medium",
        "Physical dimensions (length / width / depth): Unknown / Unknown / Unknown",
        "Measurement provenance: Visual estimate without a scale reference",
        "Measurement confidence: Low",
        "Geographic corporation/body: Bengaluru South City Corporation",
        "Complaint intake authority: Bengaluru South City Corporation",
        "Road owner/maintainer: Unknown — authority to inspect and transfer if required",
        "Status: No verified exact-road public contract found; tender and contractor omitted.",
    ):
        require(failures, expected in body, f'missing or altered email field: "{expected}"')
    for leaked in (
        "BBMP/2025-26/RD/WORK-42", "Resurfacing of 17th Main Road in HSR Layout",
        "ACME Roads Pvt Ltd", "Karnataka Public Procurement Portal (KPPP) snapshot",
    ):
        require(failures, leaked not in body,
                f'unverified contract identity leaked into the email: "{leaked}"')

    require(failures, body.count(FOOTER) == 1,
            "email must contain exactly one independent-app disclaimer")
    require(failures, body.rstrip().endswith(FOOTER),
            "independent-app disclaimer must be the final email paragraph")
    for forbidden in (
        "within the defect liability period",
        "within maintenance period",
        "official size",
        "official category",
        "does not submit a grievance",
        "no official grievance submission is confirmed",
    ):
        require(failures, forbidden not in body.lower(),
                f'email retains an unsupported or noisy claim: "{forbidden}"')

    require(failures,
            "Status: No verified exact-road public contract found; tender and contractor omitted."
            in no_candidate,
            "no-candidate email does not state the fail-closed attribution result")
    require(failures, "BBMP/2025-26/RD/WORK-42" not in no_candidate,
            "no-candidate email leaked a tender from another render")
    require(failures, result["rejectedScope"] is None,
            "drain-and-footpath-only WORK_INDENT2505 was accepted as road work")

    print()
    if failures:
        print("FAIL")
        for failure in failures:
            print("  -", failure)
        sys.exit(1)
    print("LETTER TEST PASS")


if __name__ == "__main__":
    main()
