#!/usr/bin/env python3
"""Focused contract checks for State/UT-bound National Highway candidates."""

from __future__ import annotations

import sys

from playwright.sync_api import sync_playwright


SCENARIO = r"""
async () => {
  const P = StandaloneAPI.__pure;
  const current = {
    record_id: "nhai:ABC123", reference_label: "UPC", reference_value: "ABC123",
    state_code: "MH", agency: "NHAI", lifecycle: "current_project",
    lifecycle_status: "Under Maintenance & Repair through Agency at Site",
    title: "Maintenance of NH-48 from Pune to Satara km 10 to km 110",
    highway_refs: ["NH-48"], chainages: [{start_km: 10, end_km: 110}],
    contractor: "Example Roads Limited", published_at: null, start_date: "01/01/2025",
    likely_completion_date: "01/01/2027", division: "PIU Pune",
    source_name: "NHAI Data Lake", source_url: "https://datalakem.nhai.gov.in/",
    retrieved_at: "2026-08-26", scope_verified: true, segment_verified: false,
    award_verified: true, dlp_verified: false,
  };
  const notice = {
    ...current, record_id: "nhidcl:notice", reference_label: "Tender ID",
    reference_value: "2026_NHIDC_1", agency: "NHIDCL", lifecycle: "procurement_notice",
    lifecycle_status: "Open for bids", title: "Strengthening of NH-48 near Pune",
    contractor: null, published_at: "2026-08-20", start_date: null,
    likely_completion_date: null, award_verified: false,
  };
  const wrongHighway = {...current, record_id: "nhai:wrong", highway_refs: ["NH-44"]};
  const drain = {...current, record_id: "nhai:drain",
    title: "Construction of drain and footpath beside NH-48"};
  const ranked = P.highwayContractCandidates(
    [notice, wrongHighway, drain, current], "NH-48", "Pune Satara Highway, Pune, Maharashtra");
  const refOnly = P.highwayContractCandidates(
    [current], "NH-48", "Unrelated Place, Maharashtra");
  const route = {
    routed: true, tender_eligible: true, region: "national-highway",
    highway_ref: "NH-48", contract_state_code: "MH", routing_pack_state_code: "IN",
  };
  const candidate = {
    tender_number: "ABC123", reference_label: "UPC", title: current.title,
    contractor: current.contractor, lifecycle: current.lifecycle,
    lifecycle_status: current.lifecycle_status, source_name: current.source_name,
    source_url: current.source_url, detail_url: current.source_url,
    organisation: "NHAI — PIU Pune", project_start: current.start_date,
    project_completion: current.likely_completion_date,
    package_reference: current.reference_value, highway_reference: "NH-48",
    published_chainage: "km 10–110", match_basis: "State/UT MH; mapped NH-48; title/address Pune, Satara",
    bid_closing: null, bid_opening: null, agreement_number: null, agreement_date: null,
    scope_verified: true, segment_verified: false,
    award_verified: true, dlp_verified: false,
    tender_pack_id: "in-nh-contracts-mh", tender_pack_version: 1,
    tender_pack_sha256: "a".repeat(64), tender_pack_state_code: "MH",
  };
  const manifest = await P.getContractPackManifest();
  const mhPack = await P.loadHighwayContractPack("MH");
  const complaint = P.buildComplaintOutputs({
    size: "medium", surface_type: "bituminous_asphalt",
    measurement_provenance: "visual_estimate", measurement_confidence: "low",
  }, 18.5204, 73.8567, "Pune Satara Highway, Pune, Maharashtra",
  "National Highway Authority", candidate,
  {...route, authority_id:"in-national-highway", authority_name:"National Highway",
   officer_name:"National Highway Authority", routing_source:"osm_national_highway"}, {});
  const exactStates = await Promise.all([
    P.exactPinnedContractStateCode("MH", 18.5204, 73.8567, 8),
    P.exactPinnedContractStateCode("MH", 23.0225, 72.5714, 8),
    P.exactPinnedContractStateCode("GJ", 18.5204, 73.8567, 8),
    P.exactPinnedContractStateCode("MH", 18.5204, 73.8567, 31),
  ]);
  P.resetContractPackMemory();
  P.resetRoadAgreementPackMemory();
  P.resetRoadNoticePackMemory();
  const originalFetch = window.fetch;
  const optionalManifestNames = [
    "contract-manifest-v1.36.json", "road-agreement-manifest-v1.36.json",
    "road-notice-manifest-v1.36.json",
  ];
  const optionalStarts = [], optionalAborts = [];
  window.fetch = (input, init = {}) => {
    const name = optionalManifestNames.find((item) => String(input).includes(item));
    if (!name) return originalFetch(input, init);
    optionalStarts.push({name, at: performance.now()});
    return new Promise((resolve, reject) => {
      const abort = () => {
        optionalAborts.push(name);
        reject(new DOMException("catalog timeout", "AbortError"));
      };
      if (init.signal && init.signal.aborted) abort();
      else if (init.signal) init.signal.addEventListener("abort", abort, {once:true});
    });
  };
  const timeoutStarted = performance.now();
  let kpppStarted = null;
  const hangingKppp = {then: () => { kpppStarted = performance.now(); }};
  let timeoutResult = "not-run";
  try {
    timeoutResult = await P.matchTenderAt(
      "Unlisted Road, Unlisted Locality, Karnataka",
      {routed:true, tender_eligible:true, issue_type:"road_damage",
       region:"national-highway", highway_ref:"NH-48",
       contract_state_code:"KA", routing_pack_state_code:"IN"},
      12.9716, 77.5946, hangingKppp);
  } finally {
    window.fetch = originalFetch;
  }
  const timeoutElapsed = performance.now() - timeoutStarted;
  const optionalStartTimes = [...optionalStarts.map((item) => item.at), kpppStarted];
  return {
    manifestResources: manifest ? Object.keys(manifest.resources).length : 0,
    mhPackRecords: mhPack && mhPack.contracts ? mhPack.contracts.length : 0,
    mhPackState: mhPack && mhPack.state_code,
    stateCodes: [
      P.stateCodeForGeocode({country_code:"in", state:"Maharashtra"}),
      P.stateCodeForGeocode({country_code:"in", state:"National Capital Territory of Delhi"}),
      P.stateCodeForGeocode({country_code:"in", state:"Odisha"}),
      P.stateCodeForGeocode({country_code:"us", state:"Maharashtra"}),
    ],
    rankedIds: ranked.map((item) => item.record.record_id),
    currentFirst: ranked[0] && ranked[0].record.record_id,
    wrongIncluded: ranked.some((item) => item.record.record_id === "nhai:wrong"),
    drainIncluded: ranked.some((item) => item.record.record_id === "nhai:drain"),
    refOnlyCount: refOnly.length,
    accepted: P.normaliseTenderMatch(candidate, route),
    complaintBody: complaint.email_body,
    portalFields: complaint.portal_fields,
    crossState: P.normaliseTenderMatch({...candidate,
      tender_pack_id:"in-nh-contracts-gj", tender_pack_state_code:"GJ"}, route),
    exactStates,
    trustedHighwayStates: [
      P.trustedContractStateCode({routed:true, region:"national-highway",
        routing_pack_state_code:"IN", contract_state_code:"MH"}, "MH"),
      P.trustedContractStateCode({routed:true, region:"national-highway",
        routing_pack_state_code:"IN", contract_state_code:null}, "MH"),
      P.trustedContractStateCode({routed:true, region:"national-highway",
        routing_pack_state_code:"IN", contract_state_code:"GJ"}, "MH"),
    ],
    optionalRuntime: {
      timeoutResult,
      elapsed: timeoutElapsed,
      starts: optionalStarts.map((item) => item.name).sort(),
      startSpread: Math.max(...optionalStartTimes) - Math.min(...optionalStartTimes),
      kpppStarted: kpppStarted !== null,
      aborts: [...new Set(optionalAborts)].sort(),
      configuredTimeout: P.OPTIONAL_CATALOG_TIMEOUT_MS,
    },
  };
}
"""


def main() -> None:
    failures: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        # The server root intentionally exercises the frozen Play-test Android bundle.
        # Contract packs belong to the current web runtime until a later APK release.
        page.goto("http://localhost:8765/web-app/")
        page.wait_for_function(
            "() => window.StandaloneAPI && StandaloneAPI.__pure "
            "&& typeof StandaloneAPI.__pure.highwayContractCandidates === 'function'"
        )
        result = page.evaluate(SCENARIO)
        browser.close()

    if result["stateCodes"] != ["MH", "DL", "OD", None]:
        failures.append(f"State/UT mapping failed: {result['stateCodes']!r}")
    if result["manifestResources"] < 30:
        failures.append(
            f"Generated nationwide highway catalog is incomplete: {result['manifestResources']}"
        )
    if result["mhPackState"] != "MH" or result["mhPackRecords"] <= 0:
        failures.append(
            f"Content-addressed Maharashtra pack did not load: {result['mhPackState']!r}, "
            f"{result['mhPackRecords']!r}"
        )
    if result["currentFirst"] != "nhai:ABC123":
        failures.append(f"Current contract was not ranked first: {result['rankedIds']!r}")
    if result["wrongIncluded"]:
        failures.append("A different NH reference entered the candidate pool")
    if result["drainIncluded"]:
        failures.append("A drain/footpath-only record entered the candidate pool")
    if result["refOnlyCount"] != 0:
        failures.append("same-State/NH-only record matched without independent locality evidence")
    accepted = result["accepted"]
    if not accepted or accepted.get("reference_label") != "UPC":
        failures.append(f"Valid highway candidate was rejected: {accepted!r}")
    elif accepted.get("segment_verified") is not False:
        failures.append(f"Unmapped chainage was marked verified: {accepted!r}")
    elif accepted.get("award_verified") is not True:
        failures.append(f"Source-backed award was discarded: {accepted!r}")
    complaint_body = result["complaintBody"]
    for expected_detail in (
        "Organisation / department: NHAI — PIU Pune",
        "Project start: 01/01/2025",
        "Likely completion: 01/01/2027",
        "Package / project reference: ABC123",
        "Highway reference: NH-48",
        "Published package chainage (GPS point not verified): km 10–110",
        "Candidate match basis:",
    ):
        if expected_detail not in complaint_body:
            failures.append(f"highway complaint omitted tender detail: {expected_detail}")
    if result["crossState"] is not None:
        failures.append("A cross-State contract pack was accepted")
    if result["exactStates"] != ["MH", None, None, None]:
        failures.append(
            f"exact pinned State containment failed closed: {result['exactStates']!r}"
        )
    if result["trustedHighwayStates"] != ["MH", None, None]:
        failures.append(
            f"National Highway State trust bypassed containment: "
            f"{result['trustedHighwayStates']!r}"
        )
    runtime = result["optionalRuntime"]
    expected_manifests = sorted([
        "contract-manifest-v1.36.json",
        "road-agreement-manifest-v1.36.json",
        "road-notice-manifest-v1.36.json",
    ])
    if runtime["timeoutResult"] is not None:
        failures.append(f"timed-out optional catalogs produced a result: {runtime!r}")
    if runtime["starts"] != expected_manifests or runtime["aborts"] != expected_manifests:
        failures.append(f"optional manifest timeout coverage is incomplete: {runtime!r}")
    if not runtime["kpppStarted"]:
        failures.append(f"KPPP tier did not start with the other eligible tiers: {runtime!r}")
    if runtime["startSpread"] > 250:
        failures.append(f"lower-priority catalogs started serially: {runtime!r}")
    if runtime["elapsed"] > runtime["configuredTimeout"] + 1200:
        failures.append(f"optional catalogs blocked beyond one short deadline: {runtime!r}")

    if failures:
        print("FAIL: highway contract matching")
        for failure in failures:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("ok: exact highway/state gating, strict road scope and lifecycle truth")


if __name__ == "__main__":
    main()
