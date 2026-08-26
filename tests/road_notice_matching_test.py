#!/usr/bin/env python3
"""Focused browser checks for checksum-pinned State/UT procurement notices."""

from __future__ import annotations

from playwright.sync_api import sync_playwright


SCENARIO = r"""
async () => {
  const P = StandaloneAPI.__pure;
  const manifest = await P.getRoadNoticeManifest();
  const resources = Object.values(manifest.resources);
  const loaded = [];
  for (const resource of resources) {
    const pack = await P.loadRoadNoticePack(resource.state_code);
    let error = null;
    let rejectedScope = [];
    if (!pack) {
      try {
        const response = await fetch("/" + resource.path);
        const decoded = await response.json();
        rejectedScope = decoded.notices.filter((row) =>
          !P.tenderCoversCarriageway(row.title, row.tender_reference))
          .slice(0, 10).map((row) => ({id: row.tender_id, title: row.title}));
        P.validateRoadNoticePack(resource, decoded);
      } catch (caught) {
        error = String(caught && caught.message || caught);
      }
    }
    loaded.push({
      state: resource.state_code,
      expected: resource.records,
      actual: pack && pack.notices ? pack.notices.length : null,
      error,
      rejectedScope,
    });
  }
  const testResource = resources.find((resource) => resource.records > 0);
  const testPack = testResource ? await P.loadRoadNoticePack(testResource.state_code) : null;
  let testPackError = null;
  if (testResource && !testPack) {
    const resource = testResource;
    try {
      const response = await fetch("/" + resource.path);
      P.validateRoadNoticePack(resource, await response.json());
    } catch (caught) {
      testPackError = String(caught && caught.message || caught);
    }
  }
  const seed = testPack && testPack.notices.find((row) =>
    String(row.title || "").trim().split(/\s+/).length >= 4);
  const seedAddress = seed ? `${seed.title}, India` : "";
  const seedSource = seed && testPack.sources.find((row) => row.source_id === seed.source_id);
  const testRoute = {
    routed: true, tender_eligible: false, issue_type: "road_damage",
    contract_state_code: testResource && testResource.state_code,
    routing_pack_state_code: testResource && testResource.state_code,
    region: "statewide-unverified", authority_id: "test-statewide-unverified",
    authority_name: "Test road authority",
    officer_name: "Test road authority",
    routing_source: "test_exact_boundary", ownership_unverified: true,
  };
  const actual = seed ? await P.matchRoadNotice(seedAddress, testRoute) : null;
  const absent = await P.matchRoadNotice(
    "ZXQ Unlisted Test Road, QZX Unlisted Locality, India", testRoute);
  const ranked = P.roadNoticeCandidates(testPack && testPack.notices,
    seedAddress, testRoute);
  const normalised = P.normaliseTenderMatch(actual, testRoute);
  const complaint = P.buildComplaintOutputs({
    size: "medium", surface_type: "bituminous_asphalt",
    measurement_provenance: "visual_estimate", measurement_confidence: "low",
  }, 19.8762, 75.3433, seedAddress,
  testRoute.officer_name, actual, testRoute, {});
  const crossState = P.normaliseTenderMatch(actual, {...testRoute,
    contract_state_code: testRoute.contract_state_code === "GJ" ? "MH" : "GJ"});
  const civic = P.normaliseTenderMatch(actual,
    {...testRoute, issue_type: "garbage"});
  const drain = {
    award_verified: false,
    closing_at: "2026-08-31T17:00:00+05:30",
    dlp_verified: false,
    lifecycle: "procurement_notice",
    opening_at: "2026-09-02T12:00:00+05:30",
    organisation_chain: "Public Works Department",
    published_at: "2026-08-25T15:05:00+05:30",
    record_id: "drain-only",
    scope: "road_surface",
    segment_verified: false,
    source_id: "in-mh-gepnic",
    source_url: "https://mahatenders.gov.in/",
    tender_id: "DRAIN-1",
    tender_reference: "DRAIN-1",
    title: "Construction of drain and footpath beside Kanjur Village Road",
  };
  const drainRanked = P.roadNoticeCandidates([drain],
    "Kanjur Village Road, Kanjur West, Mumbai", testRoute);
  const feeder = {...drain, record_id: "nh-feeder", tender_id: "FEEDER-1",
    tender_reference: "FEEDER-1", title: "Resurfacing feeder road from Rampur to NH-48"};
  const nhRefOnly = P.roadNoticeCandidates([feeder],
    "Unrelated Place, Maharashtra", {...testRoute, highway_ref: "NH-48"});
  const plantation = {...drain, record_id: "plantation", tender_id: "PLANT-1",
    tender_reference: "PLANT-1",
    title: "Maintenance of road side plantations in Social Forestry Range"};
  const plantationRanked = P.roadNoticeCandidates([plantation],
    "Social Forestry Range Road, Chhattisgarh", testRoute);
  return {
    resourceCount: resources.length,
    expectedTotal: resources.reduce((sum, resource) => sum + resource.records, 0),
    actualTotal: loaded.reduce((sum, item) => sum + (item.actual || 0), 0),
    failedLoads: loaded.filter((item) => item.actual !== item.expected),
    testPackError,
    seedId: seed && seed.tender_id,
    seedTitle: seed && seed.title,
    seedOrganisation: seed && seed.organisation_chain,
    seedClosing: seed && seed.closing_at,
    seedOpening: seed && seed.opening_at,
    seedDetailUrl: seed && seed.source_url,
    seedSourceRoot: seedSource && seedSource.source_url,
    rankedFirst: ranked[0] && ranked[0].record.tender_id,
    actual,
    absent,
    normalised,
    complaintBody: complaint.email_body,
    portalFields: complaint.portal_fields,
    crossState,
    civic,
    drainCount: drainRanked.length,
    nhRefOnlyCount: nhRefOnly.length,
    plantationCount: plantationRanked.length,
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
            "&& typeof StandaloneAPI.__pure.matchRoadNotice === 'function'"
        )
        result = page.evaluate(SCENARIO)
        browser.close()

    if result["resourceCount"] < 29:
        failures.append(f"expected at least 29 public official jurisdiction packs: {result['resourceCount']}")
    if result["expectedTotal"] <= 0 or result["actualTotal"] != result["expectedTotal"]:
        failures.append(
            f"notice total differs (manifest/runtime): "
            f"{result['expectedTotal']}/{result['actualTotal']}"
        )
    if result["failedLoads"]:
        failures.append(f"packs failed schema/hash loading: {result['failedLoads']!r}")
    if result["testPackError"]:
        failures.append(f"selected real pack failed schema validation: {result['testPackError']}")
    actual = result["actual"]
    if not actual or actual.get("lifecycle") != "procurement_notice":
        failures.append(f"real State/UT notice did not match: {actual!r}")
    elif actual.get("contractor") is not None or actual.get("award_verified") is not False:
        failures.append(f"procurement notice inferred an award/contractor: {actual!r}")
    elif result["seedId"] not in actual.get("tender_number", ""):
        failures.append(f"wrong real notice ranked first: {actual!r}")
    elif "component=%24DirectLink" in actual.get("source_url", ""):
        failures.append(f"session-shaped GePNIC detail URL escaped into complaint: {actual!r}")
    elif actual.get("source_url") != result["seedSourceRoot"]:
        failures.append(f"notice did not cite the stable official portal: {actual!r}")
    elif actual.get("organisation") != result["seedOrganisation"]:
        failures.append(f"notice lost its official organisation chain: {actual!r}")
    elif actual.get("bid_closing") != result["seedClosing"]:
        failures.append(f"notice lost its bid-closing timestamp: {actual!r}")
    elif actual.get("bid_opening") != result["seedOpening"]:
        failures.append(f"notice lost its bid-opening timestamp: {actual!r}")
    elif actual.get("detail_url") != result["seedDetailUrl"]:
        failures.append(f"notice lost its captured official detail link: {actual!r}")
    if result["absent"] is not None:
        failures.append(f"unlisted synthetic road received a false notice: {result['absent']!r}")
    if not result["normalised"]:
        failures.append("same-State road-notice candidate was rejected")
    complaint_body = result["complaintBody"]
    if (result["seedId"] not in complaint_body
            or result["seedTitle"] not in complaint_body):
        failures.append("complaint lost the official tender identity or exact work name")
    if "Open procurement notice candidate — not an awarded contract" not in complaint_body:
        failures.append("complaint did not distinguish a procurement notice from an award")
    if "Listed contractor: Not listed" not in complaint_body:
        failures.append("complaint invented or omitted the notice's unknown-contractor truth")
    for expected_detail in (
        f"Organisation / department: {result['seedOrganisation']}",
        f"Bid closing: {result['seedClosing']}",
        f"Bid opening: {result['seedOpening']}",
        "Candidate match basis:",
    ):
        if expected_detail not in complaint_body:
            failures.append(f"complaint omitted tender detail: {expected_detail}")
    portal_fields = result["portalFields"]
    for field in ("organisation_department", "bid_closing", "bid_opening",
                  "candidate_match_basis", "official_tender_detail_url"):
        if not portal_fields.get(field):
            failures.append(f"portal copy omitted tender field: {field}")
    if result["crossState"] is not None:
        failures.append("cross-State road-notice candidate was accepted")
    if result["civic"] is not None:
        failures.append("a road procurement notice leaked into a garbage complaint")
    if result["drainCount"] != 0:
        failures.append("drain/footpath-only notice entered the road candidate pool")
    if result["nhRefOnlyCount"] != 0:
        failures.append("NH-reference-only feeder notice matched without locality evidence")
    if result["plantationCount"] != 0:
        failures.append("roadside plantation notice entered the road candidate pool")

    if failures:
        print("FAIL: State/UT road-notice matching")
        for failure in failures:
            print(f"  - {failure}")
        raise SystemExit(1)
    print(
        f"ok: {result['resourceCount']} public catalogs load; "
        "same-State title evidence and notice-only truth hold"
    )


if __name__ == "__main__":
    main()
