#!/usr/bin/env python3
"""Focused browser checks for checksum-pinned PMGSY road-agreement candidates."""

from __future__ import annotations

from playwright.sync_api import sync_playwright


SCENARIO = r"""
async () => {
  const P = StandaloneAPI.__pure;
  const manifest = await P.getRoadAgreementManifest();
  const resources = manifest ? Object.values(manifest.resources) : [];
  const populated = resources.find((resource) => resource.records > 0);
  const pack = populated ? await P.loadRoadAgreementPack(populated.state_code) : null;
  const first = pack && pack.agreements && pack.agreements[0];
  const route = populated ? {
    routed: true, tender_eligible: false, issue_type: "road_damage",
    contract_state_code: populated.state_code,
    routing_pack_state_code: populated.state_code,
    region: "statewide-unverified", authority_id: "test-authority",
    authority_name: "Test road authority", officer_name: "Test road authority",
    routing_source: "test_exact_state", ownership_unverified: true,
  } : null;
  const address = first
    ? `${first.title}, ${first.road_from || ""}, ${first.district_name || ""}` : "";
  const actual = first ? await P.matchRoadAgreement(address, route) : null;
  const normalised = actual ? P.normaliseTenderMatch(actual, route) : null;
  const complaint = actual ? P.buildComplaintOutputs({
    size: "medium", surface_type: "bituminous_asphalt",
    measurement_provenance: "visual_estimate", measurement_confidence: "low",
  }, 25.5941, 85.1376, address, route.officer_name, actual, route, {}) : null;
  const crossState = actual ? P.normaliseTenderMatch(actual,
    {...route, contract_state_code: route.contract_state_code === "MH" ? "GJ" : "MH"}) : null;

  const synthetic = {
    record_id: "PMGSY:5:271312", state_code: "BR", lifecycle: "current_project",
    lifecycle_status: "In Progress", title: "MRL05-PALASI TO SAHANDAR",
    road_from: "PALASI", road_to: "SAHANDAR", district_name: "Araria",
    scope_verified: true, segment_verified: false, contractor: null,
    contractor_assignment_verified: false, dlp_verified: false,
  };
  const strong = P.roadAgreementCandidates([synthetic],
    "Palasi to Sahandar, Palasi, Araria, Bihar");
  const weak = P.roadAgreementCandidates([synthetic], "Araria, Bihar");
  const sharedLocality = P.roadAgreementCandidates([
    synthetic,
    {...synthetic, record_id: "PMGSY:5:shared-locality",
      title: "MRL08-PALASI TO FORBESGANJ", road_from: "PALASI", road_to: "FORBESGANJ"},
  ], "Palasi, Araria, Bihar");
  const districtNamedRoad = {...synthetic, record_id: "PMGSY:5:district-name",
    title: "MRL07-AURANGABAD TO DAUDNAGAR", road_from: "AURANGABAD",
    road_to: "DAUDNAGAR", district_name: "Aurangabad"};
  const districtOnly = P.roadAgreementCandidates([districtNamedRoad],
    "Unknown Road, Aurangabad, Bihar");
  const multiWordDistrictRoad = {...synthetic, record_id: "PMGSY:3:district-name",
    title: "MRL07-TARN TARAN TO PATTI", road_from: "TARN TARAN",
    road_to: "PATTI", district_name: "Tarn Taran"};
  const multiWordDistrictOnly = P.roadAgreementCandidates([multiWordDistrictRoad],
    "Unknown Road, Tarn Taran, Punjab");

  const notice = {
    award_verified: false, dlp_verified: false, lifecycle: "procurement_notice",
    organisation_chain: "Public Works Department", published_at: "2026-08-01T00:00:00Z",
    opening_at: "2026-08-02T00:00:00Z", record_id: "expiry-check",
    scope: "road_surface", segment_verified: false, source_id: "in-mh-gepnic",
    source_url: "https://example.gov.in/", tender_id: "EXPIRY-1",
    tender_reference: "EXPIRY-1", title: "Resurfacing Palasi Sahandar Road",
  };
  const noticeRoute = {highway_ref: null, authority_name: "Public Works Department"};
  const expired = P.roadNoticeCandidates([{...notice,
    closing_at: "2026-08-25T23:59:59Z"}],
    "Palasi Sahandar Road, Araria", noticeRoute, Date.parse("2026-08-26T00:00:00Z"));
  const open = P.roadNoticeCandidates([{...notice,
    closing_at: "2026-08-27T00:00:00Z"}],
    "Palasi Sahandar Road, Araria", noticeRoute, Date.parse("2026-08-26T00:00:00Z"));
  let stalePackLoaded = null;
  if (populated) {
    const reviewAfter = populated.review_after;
    populated.review_after = "2000-01-01";
    stalePackLoaded = await P.loadRoadAgreementPack(populated.state_code);
    populated.review_after = reviewAfter;
  }
  const nonWorksRejected = [
    "Project Management Consultancy Services for construction and strengthening of roads",
    "Consultancy services for preparation of DPR for widening of NH 66",
    "Appointment of Authority Engineer for rehabilitation of NH 48",
    "Survey and investigation for construction of concrete road",
    "Third party quality monitoring of PMGSY road maintenance works",
    "Development of commercial facility at Auhar on NH 154 under PPP mode",
    "Utility shifting work as part of widening and strengthening of a route connecting NH123",
  ].every((title) => !P.tenderCoversCarriageway(title));
  const epcAccepted = P.tenderCoversCarriageway(
    "Engineering procurement and construction for widening and strengthening of NH 48");
  return {
    resourceCount: resources.length,
    populatedState: populated && populated.state_code,
    expectedRecords: populated && populated.records,
    loadedRecords: pack && pack.agreements && pack.agreements.length,
    actual, normalised, crossState,
    complaintBody: complaint && complaint.email_body,
    portalFields: complaint && complaint.portal_fields,
    sourceAgreementNumber: first && first.agreement_number,
    sourceAgreementDate: first && first.agreement_date,
    sourcePackage: first && first.package_number,
    sourceDistrict: first && first.district_name,
    sourceRoadFrom: first && first.road_from,
    sourceRoadTo: first && first.road_to,
    strongCount: strong.length, weakCount: weak.length, districtOnlyCount: districtOnly.length,
    multiWordDistrictOnlyCount: multiWordDistrictOnly.length,
    sharedLocalityCount: sharedLocality.length,
    expiredCount: expired.length, openCount: open.length,
    staleReviewAccepted: P.catalogResourceWithinReview(
      {review_after: "2026-08-25"}, Date.parse("2026-08-26T00:00:00Z")),
    currentReviewAccepted: P.catalogResourceWithinReview(
      {review_after: "2026-08-26"}, Date.parse("2026-08-26T00:00:00Z")),
    trustedStateKeys: [
      P.trustedContractStateCode({routed:true, routing_pack_state_code:"MH"}, "MH"),
      P.trustedContractStateCode({routed:true, routing_pack_state_code:"MH"}, "GJ"),
      P.trustedContractStateCode({routed:true, routing_pack_state_code:"IN"}, "GJ"),
      P.trustedContractStateCode({routed:true, routing_pack_state_code:null}, "GJ"),
    ],
    stalePackLoaded: !!stalePackLoaded,
    nonWorksRejected, epcAccepted,
  };
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
            "&& typeof StandaloneAPI.__pure.matchRoadAgreement === 'function'"
        )
        result = page.evaluate(SCENARIO)
        browser.close()

    if result["resourceCount"] < 1 or not result["populatedState"]:
        failures.append(f"no populated PMGSY State/UT pack: {result!r}")
    if result["loadedRecords"] != result["expectedRecords"]:
        failures.append(
            f"content-addressed pack failed to load: "
            f"{result['loadedRecords']!r}/{result['expectedRecords']!r}"
        )
    actual = result["actual"]
    if not actual or actual.get("lifecycle") != "current_project":
        failures.append(f"strong same-State road evidence did not match: {actual!r}")
    elif "Source-reported In Progress" not in actual.get("lifecycle_status", ""):
        failures.append(f"portal status lost its source-qualified wording: {actual!r}")
    elif actual.get("contractor") is not None or actual.get("award_verified") is not False:
        failures.append(f"PMGSY feed invented contractor assignment/award: {actual!r}")
    elif actual.get("segment_verified") is not False or actual.get("dlp_verified") is not False:
        failures.append(f"PMGSY title match invented segment/DLP proof: {actual!r}")
    elif actual.get("source_url") != "https://pmgsy.dord.gov.in/dbweb":
        failures.append(f"PMGSY candidate did not cite the stable official dashboard: {actual!r}")
    elif actual.get("agreement_number") != result["sourceAgreementNumber"]:
        failures.append(f"PMGSY candidate lost agreement number: {actual!r}")
    elif actual.get("agreement_date") != result["sourceAgreementDate"]:
        failures.append(f"PMGSY candidate lost agreement date: {actual!r}")
    elif actual.get("package_reference") != result["sourcePackage"]:
        failures.append(f"PMGSY candidate lost package reference: {actual!r}")
    elif result["sourceDistrict"] not in (actual.get("organisation") or ""):
        failures.append(f"PMGSY candidate lost district/organisation: {actual!r}")
    elif actual.get("road_from") != result["sourceRoadFrom"]:
        failures.append(f"PMGSY candidate lost road-from detail: {actual!r}")
    elif actual.get("road_to") != result["sourceRoadTo"]:
        failures.append(f"PMGSY candidate lost road-to detail: {actual!r}")
    complaint_body = result["complaintBody"] or ""
    if "No verified exact-road public contract found; tender and contractor omitted" not in complaint_body:
        failures.append("PMGSY complaint did not fail closed without segment/contractor/DLP proof")
    for leaked in (result["sourceAgreementNumber"], result["sourcePackage"],
                   result["actual"].get("organisation") if result["actual"] else None):
        if leaked and leaked in complaint_body:
            failures.append(f"unverified PMGSY identity leaked into complaint: {leaked}")
    if not result["normalised"]:
        failures.append("valid PMGSY road record was rejected by normalisation")
    if result["crossState"] is not None:
        failures.append("cross-State PMGSY road record was accepted")
    if result["strongCount"] != 1 or result["weakCount"] != 0:
        failures.append(
            f"road evidence gate was not fail-closed: "
            f"strong={result['strongCount']}, weak={result['weakCount']}"
        )
    if result["districtOnlyCount"] != 0:
        failures.append("district token was incorrectly counted as PMGSY road-name evidence")
    if result["multiWordDistrictOnlyCount"] != 0:
        failures.append("multi-word district phrase was incorrectly treated as road evidence")
    if result["sharedLocalityCount"] != 0:
        failures.append("one locality shared by multiple PMGSY roads was treated as unambiguous")
    if result["expiredCount"] != 0 or result["openCount"] != 1:
        failures.append(
            f"procurement closing-time gate failed: "
            f"expired={result['expiredCount']}, open={result['openCount']}"
        )
    if result["staleReviewAccepted"] or not result["currentReviewAccepted"]:
        failures.append("manifest review window did not fail closed")
    if result["stalePackLoaded"]:
        failures.append("stale PMGSY manifest resource still loaded from memory/cache")
    if result["trustedStateKeys"] != ["MH", None, "GJ", None]:
        failures.append(f"routing/geocoder State trust failed: {result['trustedStateKeys']!r}")
    if not result["nonWorksRejected"] or not result["epcAccepted"]:
        failures.append("browser tender-scope classifier diverged on services versus EPC works")

    if failures:
        print("FAIL: PMGSY road-agreement matching")
        for failure in failures:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("ok: PMGSY pack integrity, strong road evidence and stale-status gates hold")


if __name__ == "__main__":
    main()
