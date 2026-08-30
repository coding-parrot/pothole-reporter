#!/usr/bin/env python3
"""Regression contract for strict detection and authority-specific complaint outputs."""
import sys

from playwright.sync_api import sync_playwright


FOOTER = (
    "Pothole Reporter is an independent app. Please verify any suggested authority, "
    "ward, road ownership, and tender details."
)

JS = r"""
(async () => {
  const P = StandaloneAPI.__pure;
  const raw = {
    is_pothole: true,
    looks_like_speed_breaker: false,
    image_quality: "usable",
    surface_type: "cement_concrete",
    on_drivable_surface: true,
    has_localized_cavity: true,
    has_broken_edge_or_rim: true,
    has_depth_or_surface_loss: true,
    temporal_consistency: "single_view",
    size: "large",
    description: "Localized cavity",
  };
  const accepted = P.binaryAssessment(raw, false, 1);
  const southRoute = {
    routed: true,
    authority_id: "ka-lgd-305852",
    authority_name: "Bengaluru South City Corporation",
    officer_name: "Commissioner, Bengaluru South City Corporation",
    routing_source: "Karnataka GIS municipal boundary",
    routing_match_field: "town_lgd_code",
    routing_match_value: "305852",
    handoff_name: "Namma Bengaluru (Sahaaya 2.0)",
    handoff_url: "https://nammabengaluru.org.in/login",
    region: "karnataka",
    routing_pack_state_code: "KA",
    contract_state_code: "KA",
    tender_eligible: true,
  };
  const bmcRoute = {
    routed: true,
    authority_id: "mh-bmc",
    authority_name: "Brihanmumbai Municipal Corporation (BMC)",
    officer_name: "Brihanmumbai Municipal Corporation",
    routing_source: "Pinned BMC administrative boundary",
    routing_match_field: "ward_code",
    routing_match_value: "S",
    ward_code: "S",
    handoff_name: "BMC Pothole QuickFix",
    handoff_url: "https://marg.mcgm.gov.in/MARG/welcomePage.html",
    tender_eligible: false,
  };
  const tender = {
    tender_number: "BBMP/2025-26/RD/WORK-42",
    title: "Resurfacing of 17th Main Road in HSR Layout",
    contractor: "ACME Roads Pvt Ltd",
    published: "25-08-2026",
    source_name: "Karnataka Public Procurement Portal (KPPP) snapshot",
    source_url: "https://kppp.karnataka.gov.in/",
    tender_pack_id: "in-ka-tenders",
    tender_pack_version: 1,
    tender_pack_sha256: "b".repeat(64),
    tender_pack_state_code: "KA",
  };
  const outputs = P.buildComplaintOutputs(accepted, 12.912345, 77.612345,
    "17th Main Road, HSR Layout, Bengaluru, 560102", southRoute.officer_name,
    tender, southRoute, {
      captured_at: 1787625000,
      gps_accuracy: 8,
      photo_provenance: "Pothole Reporter camera evidence",
    });
  const noCandidate = P.buildComplaintOutputs(accepted, 19.129443, 72.932773,
    "Kanjur Village Road, Kanjur West, Mumbai, 400042", bmcRoute.officer_name,
    null, bmcRoute, {gps_accuracy: 8, photo_provenance: "Pothole Reporter camera evidence"});
  const verifiedRoute = {
    ...southRoute,
    road_owner_id: "ka-lgd-305852",
    road_owner_name: "Bengaluru South City Corporation",
    road_owner_evidence: {
      verified: true,
      segment_identity: "Official road segment KA-305852-17M-01",
      reference: "Road register entry 17M-01",
      source_url: "https://roads.karnataka.gov.in/register/17M-01",
    },
  };
  const verifiedTender = {
    ...tender,
    segment_verified: true,
    award_verified: true,
    dlp_verified: true,
    responsibility_active_verified: true,
    responsibility_valid_from: "2026-01-01T00:00:00Z",
    responsibility_valid_until: "2027-01-01T00:00:00Z",
    responsible_authority_id: "ka-lgd-305852",
    unambiguous: true,
    verification_evidence: {
      segment: {reference: "Contract schedule segment 17M-01",
        source_url: "https://roads.karnataka.gov.in/contracts/42/segment"},
      award: {reference: "Work order WO-42",
        source_url: "https://roads.karnataka.gov.in/contracts/42/award"},
      responsibility: {reference: "DLP schedule WO-42",
        source_url: "https://roads.karnataka.gov.in/contracts/42/dlp"},
    },
  };
  const verifiedOutputs = P.buildComplaintOutputs(accepted, 12.912345, 77.612345,
    "17th Main Road, HSR Layout, Bengaluru, 560102", verifiedRoute.officer_name,
    verifiedTender, verifiedRoute, {
      captured_at: 1787625000, gps_accuracy: 8,
      photo_provenance: "Pothole Reporter camera evidence",
    });

  const uiReport = {
    id: 91001,
    created_at: 1787625000,
    captured_at: 1787625000,
    status: "draft",
    issue_type: "road_damage",
    decision: "accept",
    damage_type: "pothole_cavity",
    assessment: "clear",
    image_quality: "usable",
    size: accepted.size,
    surface_type: accepted.surface_type,
    measurement_provenance: accepted.measurement_provenance,
    measurement_confidence: accepted.measurement_confidence,
    lat: 12.912345,
    lng: 77.612345,
    gps_accuracy: 8,
    address: "17th Main Road, HSR Layout, Bengaluru, 560102",
    photo: "data:image/png;base64,iVBORw0KGgo=",
    authority_id: southRoute.authority_id,
    authority_name: southRoute.authority_name,
    officer_name: southRoute.officer_name,
    routing_source: southRoute.routing_source,
    routing_match_field: southRoute.routing_match_field,
    routing_match_value: southRoute.routing_match_value,
    delivery_channel: "email",
    ...outputs,
  };
  openDetail(uiReport, [uiReport]);
  const copyWhatsAppButton = document.getElementById("copyWhatsAppBtn");
  const portalFieldsButton = document.getElementById("portalFieldsBtn");
  const detailActions = {
    copyWhatsApp: !!copyWhatsAppButton,
    portalFields: !!portalFieldsButton,
  };
  portalFieldsButton.click();
  const portalUi = {
    visible: !document.getElementById("portalCopy").classList.contains("hidden"),
    readOnly: document.getElementById("portalCopyText").readOnly,
    text: document.getElementById("portalCopyText").value,
  };
  const originalComplaintOutputsForRecord = P.complaintOutputsForRecord;
  P.complaintOutputsForRecord = () => { throw new Error("invalid stale draft"); };
  const unsafeStoredFallback = roadComplaintOutputs({
    whatsapp_text: "Contract: BAD-42; contractor Wrong Person",
    portal_copy_text: "listed contractor: Wrong Person",
    portal_fields: {listed_contractor: "Wrong Person"},
  });
  P.complaintOutputsForRecord = originalComplaintOutputsForRecord;

  const realWatchPosition = navigator.geolocation.watchPosition.bind(navigator.geolocation);
  const realClearWatch = navigator.geolocation.clearWatch.bind(navigator.geolocation);
  let locationCallback = null, clearedWatch = null;
  navigator.geolocation.watchPosition = (ok) => { locationCallback = ok; return 812; };
  navigator.geolocation.clearWatch = (id) => { clearedWatch = id; };
  const sampler = startCapturePositionSampler();
  const capturedAtMs = 1787625000000;
  locationCallback({timestamp: capturedAtMs - 200,
    coords: {latitude: 12.91, longitude: 77.64, accuracy: 85, speed: null, heading: null}});
  locationCallback({timestamp: capturedAtMs - 80,
    coords: {latitude: 12.912345, longitude: 77.612345, accuracy: 9, speed: 0, heading: 90}});
  const captureFix = await sampler.positionAt(capturedAtMs);
  navigator.geolocation.watchPosition = realWatchPosition;
  navigator.geolocation.clearWatch = realClearWatch;
  const roadRetryable = canRetryRouting({
    issue_type: "road_damage", lat: 12.912345, lng: 77.612345,
    unrouted_reason: "road_class_unknown",
  });

  const profileRoutes = [
    ["mh-bmc", "Brihanmumbai Municipal Corporation (BMC)"],
    ["ka-lgd-305851", "Bengaluru Central City Corporation"],
    ["ka-lgd-305850", "Bengaluru East City Corporation"],
    ["ka-lgd-305853", "Bengaluru North City Corporation"],
    ["ka-lgd-305852", "Bengaluru South City Corporation"],
    ["ka-lgd-305854", "Bengaluru West City Corporation"],
  ];
  const profiles = Object.fromEntries(profileRoutes.map(([authority_id, authority_name]) => [
    authority_id,
    P.authorityComplaintProfile({authority_id, authority_name}).profile_id,
  ]));
  const incompleteBda = {
    ...southRoute,
    road_owner_id: "ka-bengaluru-bda",
    road_owner_name: "Bangalore Development Authority (BDA)",
    road_owner_evidence: {
      verified: true,
      segment_identity: "17th Main Road segment",
      source_url: "https://example.gov.in/bda-road-list",
      // No document/reference: deliberately insufficient.
    },
  };
  const completeBda = {
    ...incompleteBda,
    road_owner_evidence: {
      ...incompleteBda.road_owner_evidence,
      reference: "BDA road inventory item 42",
    },
  };

  return {
    accepted,
    speedBreaker: P.binaryAssessment({...raw, looks_like_speed_breaker: true}, false, 1),
    unknownSurface: P.binaryAssessment({...raw, surface_type: "unknown"}, false, 1),
    unpavedSurface: P.binaryAssessment({...raw, surface_type: "unpaved_or_nonroad"}, false, 1),
    profiles,
    incompleteBdaProfile: P.authorityComplaintProfile(incompleteBda).profile_id,
    completeBdaProfile: P.authorityComplaintProfile(completeBda).profile_id,
    incompleteBdaResponsibility: P.verifiedBdaResponsibility(incompleteBda),
    completeBdaResponsibility: P.verifiedBdaResponsibility(completeBda),
    separatedBmc: P.separateRoadResponsibility(bmcRoute),
    separatedIncompleteBda: P.separateRoadResponsibility(incompleteBda),
    separatedCompleteBda: P.separateRoadResponsibility(completeBda),
    drainOnly: P.tenderCoversCarriageway(
      "Construction of drain and footpath", "BBMP/2023-24/OW/WORK_INDENT2505"),
    normalisedDrainOnly: P.normaliseTenderMatch({...tender,
      tender_number: "BBMP/2023-24/OW/WORK_INDENT2505",
      title: "Construction of drain and footpath"}, southRoute),
    recentContract: P.contractVerificationFor(tender),
    oldContract: P.contractVerificationFor({...tender, published: "01-01-2018"}),
    outputs,
    noCandidate,
    verifiedOutputs,
    detailActions,
    portalUi,
    unsafeStoredFallback,
    captureFix,
    clearedWatch,
    roadRetryable,
  };
})()
"""


def check(failures, name, condition, details=None):
    print(f"  {'ok  ' if condition else 'FAIL'} {name}")
    if not condition:
        failures.append(f"{name}: {details}" if details is not None else name)


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

    accepted = result["accepted"]
    check(failures, "accepted paved cavity is binary pothole YES",
          accepted["is_pothole"] is True and accepted["defect_type"] == "pothole", accepted)
    check(failures, "accepted classification records surface type",
          accepted["surface_type"] == "cement_concrete", accepted)
    check(failures, "visual class has explicit non-measurement provenance",
          accepted["measurement_provenance"] == "visual_estimate_no_scale"
          and accepted["measurement_confidence"] == "low", accepted)
    check(failures, "unknown physical dimensions stay null",
          all(accepted[field] is None for field in
              ("measurement_length_cm", "measurement_width_cm", "measurement_depth_cm")), accepted)
    for key, name in (
        ("speedBreaker", "speed breaker"),
        ("unknownSurface", "unknown surface"),
        ("unpavedSurface", "unpaved/non-road surface"),
    ):
        verdict = result[key]
        check(failures, f"{name} is strict NO",
              verdict["is_pothole"] is False
              and verdict["defect_type"] == "not_pothole"
              and verdict["size"] is None, verdict)

    expected_profiles = {
        "mh-bmc": "mh-bmc",
        "ka-lgd-305851": "ka-bengaluru-central",
        "ka-lgd-305850": "ka-bengaluru-east",
        "ka-lgd-305853": "ka-bengaluru-north",
        "ka-lgd-305852": "ka-bengaluru-south",
        "ka-lgd-305854": "ka-bengaluru-west",
    }
    check(failures, "BMC and five Bengaluru intake profiles resolve exactly",
          result["profiles"] == expected_profiles, result["profiles"])
    check(failures, "incomplete evidence cannot select the BDA profile",
          result["incompleteBdaResponsibility"] is False
          and result["incompleteBdaProfile"] == "ka-bengaluru-south",
          result["incompleteBdaProfile"])
    check(failures, "complete segment-level evidence selects BDA",
          result["completeBdaResponsibility"] is True
          and result["completeBdaProfile"] == "ka-bengaluru-bda",
          result["completeBdaProfile"])

    bmc = result["separatedBmc"]
    check(failures, "geographic body and complaint intake are preserved separately",
          bmc["geographic_authority_id"] == "mh-bmc"
          and bmc["intake_authority_id"] == "mh-bmc", bmc)
    check(failures, "boundary containment never invents road ownership",
          bmc["road_owner_status"] == "unverified"
          and bmc["road_owner_id"] is None
          and bmc["road_owner_name"] is None, bmc)
    check(failures, "incomplete BDA ownership evidence is discarded",
          result["separatedIncompleteBda"]["road_owner_status"] == "unverified"
          and result["separatedIncompleteBda"]["road_owner_id"] is None,
          result["separatedIncompleteBda"])
    check(failures, "complete BDA ownership evidence is retained",
          result["separatedCompleteBda"]["road_owner_status"] == "verified"
          and result["separatedCompleteBda"]["road_owner_id"] == "ka-bengaluru-bda",
          result["separatedCompleteBda"])

    check(failures, "drain/footpath-only tender is rejected",
          result["drainOnly"] is False and result["normalisedDrainOnly"] is None)
    for label in ("recentContract", "oldContract"):
        contract = result[label]
        check(failures, f"{label} publication date establishes no award or DLP",
              contract["candidate_status"] == "candidate"
              and contract["award_verified"] is False
              and contract["dlp_verified"] is False
              and contract["dlp_status"] == "unverified", contract)

    outputs = result["outputs"]
    renderings = {
        "email": outputs["email_body"],
        "WhatsApp": outputs["whatsapp_text"],
        "portal": outputs["portal_copy_text"],
    }
    invariant_values = (
        "17th Main Road, HSR Layout, Bengaluru, 560102",
        "12.912345, 77.612345",
        "https://maps.google.com/?q=12.912345,77.612345",
        "Bengaluru South City Corporation",
        "Karnataka GIS municipal boundary; town_lgd_code=305852",
    )
    for label, text in renderings.items():
        missing = [value for value in invariant_values if value not in text]
        check(failures, f"{label} preserves location and routing invariants",
              not missing, missing)
        leaked = [value for value in (
            "BBMP/2025-26/RD/WORK-42", "Resurfacing of 17th Main Road in HSR Layout",
            "ACME Roads Pvt Ltd", "Karnataka Public Procurement Portal (KPPP) snapshot",
        ) if value in text]
        check(failures, f"{label} omits every unverified tender/contractor identity",
              not leaked and "No verified exact-road public contract found" in text, leaked)
        check(failures, f"{label} has one final independent-app disclaimer",
              text.count(FOOTER) == 1 and text.rstrip().endswith(FOOTER),
              f"count={text.count(FOOTER)}, ending={text[-180:]}")

    no_candidate = result["noCandidate"]
    for label, text in {
        "email": no_candidate["email_body"],
        "WhatsApp": no_candidate["whatsapp_text"],
        "portal": no_candidate["portal_copy_text"],
    }.items():
        check(failures, f"{label} states that no exact-road contract was verified",
              "No verified exact-road public contract found" in text
              and "tender and contractor omitted" in text, text)

    verified = result["verifiedOutputs"]
    for label, text in {
        "email": verified["email_body"],
        "WhatsApp": verified["whatsapp_text"],
        "portal": verified["portal_copy_text"],
    }.items():
        check(failures, f"{label} names a contractor only after every proof gate",
              "Verified exact-road contract and active responsibility" in text
              and "BBMP/2025-26/RD/WORK-42" in text
              and "ACME Roads Pvt Ltd" in text, text)

    check(failures, "report detail exposes WhatsApp and portal-field actions",
          result["detailActions"] == {"copyWhatsApp": True, "portalFields": True},
          result["detailActions"])
    portal_ui = result["portalUi"]
    check(failures, "portal-field action opens a read-only copy screen",
          portal_ui["visible"] is True and portal_ui["readOnly"] is True,
          portal_ui)
    check(failures, "portal copy screen preserves the generated invariant block",
          portal_ui["text"] == outputs["portal_copy_text"], portal_ui["text"])
    check(failures, "UI never revives stale contract text when validation fails",
          result["unsafeStoredFallback"] is None, result["unsafeStoredFallback"])
    check(failures, "manual shutter chooses the precise timestamp-nearest GPS fix",
          result["captureFix"] == {
              "lat": 12.912345, "lng": 77.612345, "accuracy": 9,
              "speed": 0, "heading": 90, "observed_at_ms": 1787624999920,
          }, result["captureFix"])
    check(failures, "manual shutter clears its GPS watcher",
          result["clearedWatch"] == 812, result["clearedWatch"])
    check(failures, "temporary road-class failure exposes saved-report retry",
          result["roadRetryable"] is True, result["roadRetryable"])

    if failures:
        print(f"\n{len(failures)} complaint profile check(s) failed")
        for failure in failures:
            print("  -", failure)
        sys.exit(1)
    print("\nCOMPLAINT PROFILE TEST PASS")


if __name__ == "__main__":
    main()
