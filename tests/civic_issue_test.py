# -*- coding: utf-8 -*-
"""Focused contract test for user-confirmed civic issue reporting.

The route matrix is generated from the same source inventories that build the signed
runtime packs.  That keeps this test exhaustive as bodies are added: every Karnataka
ULB and every configured metro authority is checked for all three issue categories.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys

from playwright.sync_api import sync_playwright

from state_pack_utils import read_pack, route_pattern


ROOT = pathlib.Path(__file__).resolve().parent.parent
APP = os.environ.get("POTHOLE_TEST_APP", "http://localhost:8765/")
PIXEL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def authority_inventory() -> list[dict]:
    configured = json.loads(
        (ROOT / "data" / "state-authorities.json").read_text(encoding="utf-8")
    )
    bodies = json.loads(
        (ROOT / "data" / "karnataka-bodies.json").read_text(encoding="utf-8")
    )["bodies"]

    inventory: list[dict] = []
    for state, state_config in configured.items():
        for authority in state_config.get("authorities", []):
            inventory.append({**authority, "source_state": state})
    # PMC and conservative state/metro fallbacks are separately shaped entries.
    inventory.append({**configured["MH"]["pmc"], "source_state": "MH"})
    inventory.append({**configured["MH"]["fallback"], "source_state": "MH"})
    inventory.append({**configured["MH"]["statewide"], "source_state": "MH"})
    inventory.append({**configured["WB"]["statewide"], "source_state": "WB"})
    for lgd, body in bodies.items():
        inventory.append(
            {
                "id": f"ka-lgd-{lgd}",
                "name": body["name"],
                "officer_email": body["email"],
                "source_state": "KA",
            }
        )
    return inventory


SCENARIO = r"""
async ({authorities, pixel}) => {
  const P = StandaloneAPI.__pure;
  const checks = [];
  const eq = (name, got, want) => checks.push([
    name, JSON.stringify(got) === JSON.stringify(want), got, want,
  ]);
  const ok = (name, value, detail) => checks.push([
    name, !!value, detail === undefined ? value : detail, true,
  ]);
  const errorFrom = async (promise) => {
    try { await promise; return null; }
    catch (error) { return String(error && error.message || error); }
  };
  const outboundFooterMarker = "Pothole Reporter is an independent app.";
  const forbiddenOutboundCopy = [
    "no official grievance submission is confirmed",
    "email delivery is not confirmed",
  ];
  const finalParagraphOf = (text) => String(text || "").trim().split(/\n{2,}/).at(-1) || "";
  const configuredRoute = (authority) => {
    const handoff = !authority.officer_email;
    return {
      routed: true,
      officer_name: handoff
        ? `${authority.handoff_name || "Official service"}, ${authority.name}`
        : `Civic complaint desk, ${authority.name}`,
      officer_email: authority.officer_email || null,
      authority_id: authority.id,
      authority_name: authority.name,
      delivery_channel: handoff ? "official_handoff" : "email",
      handoff_name: authority.handoff_name || null,
      handoff_url: authority.handoff_url || null,
      handoff_package: authority.handoff_package || null,
      alternate_handoff_name: authority.alternate_handoff_name || null,
      alternate_handoff_url: authority.alternate_handoff_url || null,
      whatsapp_url: authority.whatsapp_url || null,
      helpline: authority.helpline || null,
      requires_official_reference: handoff,
      // Only exact Karnataka ULB routes are eligible for the separate tender matcher;
      // neutral statewide containment never identifies a contract owner.
      tender_eligible: authority.id.startsWith("ka-lgd-"),
    };
  };

  eq("categories: exact public contract", [...P.ISSUE_TYPES],
     ["road_damage", "garbage", "open_manhole"]);
  eq("legacy: absent issue type remains road damage", P.normaliseIssueType(undefined),
     "road_damage");
  eq("legacy: unknown issue type fails safe to road damage", P.normaliseIssueType("other"),
     "road_damage");
  eq("categories: valid civic type survives normalization", P.normaliseIssueType("garbage"),
     "garbage");

  const expectedIds = new Set(authorities.map((authority) => authority.id));
  eq("inventory: every configured authority has a unique ID", expectedIds.size,
     authorities.length);
  const matrixProblems = [];
  let matrixCount = 0;
  for (const authority of authorities) {
    const base = configuredRoute(authority);
    for (const issue of P.ISSUE_TYPES) {
      matrixCount += 1;
      const route = P.routeForIssue(base, issue);
      const civicSupported = authority.id.startsWith("mh-")
        || P.GENERAL_CIVIC_AUTHORITY_IDS.has(authority.id)
        || !!P.CIVIC_HANDOFF_OVERRIDES[authority.id]
        || P.BENGALURU_AUTHORITY_NAMES.has(P.normaliseAuthorityValue(authority.name));
      if (issue === "road_damage" || civicSupported) {
        if (!route || route.routed !== true || route.authority_id !== authority.id
            || route.issue_type !== issue) {
          matrixProblems.push(`${authority.id}/${issue}: supported route identity changed`);
        }
        if (route && route.delivery_channel === "email" && !route.officer_email) {
          matrixProblems.push(`${authority.id}/${issue}: email route has no recipient`);
        }
        if (route && route.delivery_channel === "official_handoff" && !route.handoff_url) {
          matrixProblems.push(`${authority.id}/${issue}: official route has no handoff URL`);
        }
        if (issue !== "road_damage" && route.tender_eligible !== false) {
          matrixProblems.push(`${authority.id}/${issue}: tender remained eligible`);
        }
      } else if (!route || route.routed !== false
          || route.unrouted_reason !== "unsupported_issue_type"
          || route.issue_type !== issue) {
        matrixProblems.push(`${authority.id}/${issue}: unverified category did not fail closed`);
      }
    }
  }
  eq("matrix: all configured bodies x all categories are checked", matrixCount,
     authorities.length * P.ISSUE_TYPES.length);
  eq("matrix: verified categories route and unverified categories fail closed", matrixProblems, []);

  const routeFor = (authorityId, issue) => {
    const authority = authorities.find((item) => item.id === authorityId);
    return authority && P.routeForIssue(configuredRoute(authority), issue);
  };
  eq("distinction: road routes retain their issue-specific official channels", {
    bmc: routeFor("mh-bmc", "road_damage").handoff_package,
    pmc: routeFor("mh-pmc", "road_damage").handoff_package,
    delhi: routeFor("dl-pwd-sewa", "road_damage").handoff_package,
  }, {
    bmc: "com.bmc.potholequickfix",
    pmc: "com.nyatitechnologies.pmcroadmitra",
    delhi: "com.sis.pwdsewaapp",
  });
  for (const issue of ["garbage", "open_manhole"]) {
    const bmc = routeFor("mh-bmc", issue);
    eq(`BMC ${issue}: general MARG portal`, bmc && bmc.handoff_url,
       "https://marg.mcgm.gov.in/MARG/welcomePage.html");
    eq(`BMC ${issue}: general civic app`, bmc && bmc.handoff_package,
       "com.esri.ugms_bmc");
    eq(`BMC ${issue}: civic helpline`, bmc && bmc.helpline, "1916");

    const pmc = routeFor("mh-pmc", issue);
    eq(`PMC ${issue}: general CARE portal`, pmc && pmc.handoff_url,
       "https://www.pmccare.in/");
    eq(`PMC ${issue}: general CARE app`, pmc && pmc.handoff_package,
       "in.gov.pmc.pmccare");
    eq(`PMC ${issue}: official WhatsApp`, pmc && pmc.whatsapp_url,
       "https://wa.me/919689900002");

    const delhi = routeFor("dl-pwd-sewa", issue);
    eq(`Delhi ${issue}: full-NCT coordination portal`, delhi && delhi.handoff_url,
       "https://cmjansunwai.delhi.gov.in/");
    eq(`Delhi ${issue}: general civic app`, delhi && delhi.handoff_package,
       "com.nic.dl.delhijanmitra");
    eq(`Delhi ${issue}: PGMS fallback`, delhi && delhi.alternate_handoff_url,
       "https://pgms.delhi.gov.in/");

    const statewide = routeFor("mh-statewide-unverified", issue);
    eq(`Maharashtra ${issue}: statewide Aaple Sarkar route`,
       statewide && statewide.handoff_url, "https://grievances.maharashtra.gov.in/en");
    eq(`Maharashtra ${issue}: urban alternate is retained`,
       statewide && statewide.alternate_handoff_url, "https://mahaulb.in/MahaULB/index");
    eq(`Maharashtra ${issue}: technical-support number is not shown as a civic helpline`,
       statewide && statewide.helpline, null);

    const westBengal = routeFor("wb-statewide-unverified", issue);
    eq(`West Bengal ${issue}: statewide PGRS route`,
       westBengal && westBengal.handoff_url,
       "https://finance.wb.gov.in/pgrs/page/PGMS_Lodge_Greivance.aspx");
    eq(`West Bengal ${issue}: CMO alternate is retained`,
       westBengal && westBengal.alternate_handoff_url,
       "https://cmo.wb.gov.in/landing/raise-grievance");
    eq(`West Bengal ${issue}: no unsupported helpline is invented`,
       westBengal && westBengal.helpline, null);

    const telangana = routeFor("tg-statewide-unverified", issue);
    eq(`Telangana ${issue}: statewide Prajavani route`,
       telangana && telangana.handoff_url, "https://prajavani.cgg.gov.in/");
    eq(`Telangana ${issue}: municipal alternate is retained`,
       telangana && telangana.alternate_handoff_url,
       "https://play.google.com/store/apps/details?id=vmax.com.citizenbuddy");
    eq(`Telangana ${issue}: no unsupported helpline is invented`,
       telangana && telangana.helpline, null);

    const karnataka = routeFor("ka-statewide-unverified", issue);
    eq(`Karnataka ${issue}: statewide Janaspandana route`,
       karnataka && karnataka.handoff_url, "https://ipgrs.karnataka.gov.in/");
    eq(`Karnataka ${issue}: urban alternate is retained`,
       karnataka && karnataka.alternate_handoff_url,
       "https://www.mrc.gov.in/janahita/login");
    eq(`Karnataka ${issue}: official helpline`, karnataka && karnataka.helpline, "1902");

    const kerala = routeFor("kl-statewide-unverified", issue);
    eq(`Kerala ${issue}: statewide CMO route`, kerala && kerala.handoff_url,
       "https://complaints.cmo.kerala.gov.in/cmoportal/login.htm?lang=en");
    eq(`Kerala ${issue}: K-SMART alternate is retained`,
       kerala && kerala.alternate_handoff_url,
       "https://ksmart.lsgkerala.gov.in/ui/web-portal/services");
    eq(`Kerala ${issue}: official helpline`, kerala && kerala.helpline, "1076");
  }

  const bengaluru = authorities.filter((authority) =>
    P.BENGALURU_AUTHORITY_NAMES.has(P.normaliseAuthorityValue(authority.name)));
  eq("Bengaluru: all five city corporations are present", bengaluru.length, 5);
  const bengaluruProblems = [];
  for (const authority of bengaluru) {
    for (const issue of ["garbage", "open_manhole"]) {
      const route = routeFor(authority.id, issue);
      if (!route || route.handoff_url !== "https://nammabengaluru.org.in/login"
          || route.handoff_package !== "com.nammabengaluruNew.org"
          || route.helpline !== "1533" || route.delivery_channel !== "official_handoff") {
        bengaluruProblems.push(`${authority.id}/${issue}`);
      }
    }
  }
  eq("Bengaluru: every civic route uses Sahaaya 2.0", bengaluruProblems, []);

  const nonBengaluruKarnataka = authorities.find((authority) =>
    authority.id.startsWith("ka-lgd-")
    && !P.BENGALURU_AUTHORITY_NAMES.has(P.normaliseAuthorityValue(authority.name)));
  const unsupported = nonBengaluruKarnataka
    && routeFor(nonBengaluruKarnataka.id, "garbage");
  eq("category truth: an unverified Karnataka civic recipient fails closed",
     unsupported && unsupported.unrouted_reason, "unsupported_issue_type");

  // Delhi is a useful end-to-end guard because road routing normally checks NH data
  // before the NCT boundary. Civic routing must skip that entire highway branch.
  const delhiCivic = await P.routeOfficer(
    {city: "New Delhi", state: "Delhi", country_code: "in",
     full: "New Delhi, Delhi, India"},
    28.6129, 77.2295, 8, 0, 0, "open_manhole");
  eq("routeOfficer: civic Delhi route stays municipal", delhiCivic && delhiCivic.authority_id,
     "dl-pwd-sewa");
  eq("routeOfficer: civic Delhi route uses the general service",
     delhiCivic && delhiCivic.handoff_url, "https://cmjansunwai.delhi.gov.in/");
  eq("routeOfficer: civic Delhi can never be tender matched",
     delhiCivic && delhiCivic.tender_eligible, false);

  const maharashtraCivic = await P.routeOfficer(
    {city: "Nagpur", state: "Maharashtra", country_code: "in",
     full: "Nagpur, Maharashtra, India"},
    21.1458, 79.0882, 8, 0, 0, "garbage");
  eq("routeOfficer: civic Nagpur uses the statewide route",
     maharashtraCivic && maharashtraCivic.authority_id, "mh-statewide-unverified");
  eq("routeOfficer: civic Nagpur uses Aaple Sarkar",
     maharashtraCivic && maharashtraCivic.handoff_url,
     "https://grievances.maharashtra.gov.in/en");
  eq("routeOfficer: civic Nagpur can never be tender matched",
     maharashtraCivic && maharashtraCivic.tender_eligible, false);

  const westBengalCivic = await P.routeOfficer(
    {city: "Darjeeling", state: "West Bengal", country_code: "in",
     full: "Darjeeling, West Bengal, India"},
    27.0410, 88.2663, 8, 0, 0, "open_manhole");
  eq("routeOfficer: civic Darjeeling uses the statewide route",
     westBengalCivic && westBengalCivic.authority_id, "wb-statewide-unverified");
  eq("routeOfficer: civic Darjeeling uses West Bengal PGRS",
     westBengalCivic && westBengalCivic.handoff_url,
     "https://finance.wb.gov.in/pgrs/page/PGMS_Lodge_Greivance.aspx");
  eq("routeOfficer: civic Darjeeling can never be tender matched",
     westBengalCivic && westBengalCivic.tender_eligible, false);

  const uiSource = document.documentElement.innerHTML;
  ok("capture UI: native Photo flow requests the live camera explicitly",
     uiSource.includes('source: "CAMERA"') && uiSource.includes('resultType: "uri"'));
  const photoPermissionBody = uiSource.match(
    /async function requestNativeCapturePermissions\(\) \{([\s\S]*?)\n\}/)?.[1] || "";
  ok("capture UI: one-off Photo never asks for Drive notification permission",
     !photoPermissionBody.includes("requestDrivePermissions")
       && photoPermissionBody.includes("Camera.requestPermissions")
       && photoPermissionBody.includes("Geolocation.requestPermissions"));
  ok("capture UI: web fallback records file time and asks before binding current GPS",
     uiSource.includes("file.lastModified")
       && uiSource.includes('confirm(t("confirm_import_location"))')
       && uiSource.includes('captureSource: "manual_import"'));

  const blob = await (await fetch(pixel)).blob();
  const created = [];
  const createdReports = [];
  for (const [index, issue] of ["garbage", "open_manhole"].entries()) {
    const form = new FormData();
    form.append("issue_type", issue);
    form.append("photo", new File([blob], `${issue}.png`, {type: "image/png"}));
    form.append("captured_at_ms", "1787470200000");
    form.append("capture_source", index === 0 ? "manual_import" : "manual_camera");
    const report = await StandaloneAPI.handle(
      "/api/civic-report", {method: "POST", body: form});
    createdReports.push(report);
    created.push({
      issue_type: report.issue_type,
      issue_confirmation: report.issue_confirmation,
      capture_source: report.capture_source,
      capture_time_source: report.capture_time_source,
      report_origin: report.report_origin,
      decision: report.decision,
      detection_model: report.detection_model,
      image_detail: report.image_detail,
      status: report.status,
      unrouted_reason: report.unrouted_reason,
      tender_number: report.tender_number,
      tender_title: report.tender_title,
      contractor: report.contractor,
      dedupe_eligible: report.dedupe_eligible,
      is_blob: report.photo_url instanceof Blob,
      full_is_blob: report.photo_full instanceof Blob,
      full_size: report.photo_full && report.photo_full.size,
    });
  }
  eq("creation: both user-confirmed civic reports bypass AI and tenders", created, [
    {
      issue_type: "garbage", issue_confirmation: "user_selected_for_import",
      capture_source: "manual_import", capture_time_source: "file_last_modified",
      report_origin: "user_reported", decision: "manual", detection_model: null,
      image_detail: null, status: "unrouted", unrouted_reason: "no_location",
      tender_number: null, tender_title: null, contractor: null,
      dedupe_eligible: false, is_blob: true,
      full_is_blob: true, full_size: blob.size,
    },
    {
      issue_type: "open_manhole", issue_confirmation: "user_selected_before_capture",
      capture_source: "manual_camera", capture_time_source: "camera_return_time",
      report_origin: "user_reported", decision: "manual", detection_model: null,
      image_detail: null, status: "unrouted", unrouted_reason: "no_location",
      tender_number: null, tender_title: null, contractor: null,
      dedupe_eligible: false, is_blob: true,
      full_is_blob: true, full_size: blob.size,
    },
  ]);
  const importedEvidence = await StandaloneAPI.handle(
    `/api/reports/${createdReports[0].id}/evidence`, {method: "GET"});
  ok("provenance: imported evidence never claims an app-camera capture",
     importedEvidence.text.includes("selected/imported by the user")
       && importedEvidence.text.includes("Selected photo file date")
       && !importedEvidence.text.includes("Captured (IST)"), importedEvidence.text);

  // Simulate a report saved while its verified pack was unavailable. A retry must use
  // these stored coordinates and original evidence—not ask for a new photo or GPS fix.
  const retryDb = await new Promise((resolve, reject) => {
    const request = indexedDB.open("potholes");
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  await new Promise((resolve, reject) => {
    const tx = retryDb.transaction("reports", "readwrite");
    const store = tx.objectStore("reports");
    const get = store.get(createdReports[0].id);
    get.onsuccess = () => {
      const report = get.result;
      report.lat = 28.6129;
      report.lng = 77.2295;
      report.gps_accuracy = 8;
      report.location_source = "current_confirmed_for_import";
      report.unrouted_reason = "jurisdiction_unavailable";
      store.put(report);
    };
    tx.oncomplete = resolve;
    tx.onabort = () => reject(tx.error || new Error("retry seed transaction aborted"));
    tx.onerror = () => {};
  });
  retryDb.close();
  const transientBeforeRetry = (await StandaloneAPI.handle("/api/reports"))
    .find((report) => report.id === createdReports[0].id);
  openDetail(transientBeforeRetry, [transientBeforeRetry]);
  ok("retry UI: a transient routing-service failure offers Retry",
     !!document.getElementById("retryRoutingBtn"));
  const retried = await StandaloneAPI.handle(
    `/api/reports/${createdReports[0].id}/retry-routing`, {method: "POST"});
  eq("retry: stored civic coordinates become a sendable verified draft", {
    status: retried.status,
    authority_id: retried.authority_id,
    issue_type: retried.issue_type,
    retry_count: retried.routing_retry_count,
    unrouted_reason: retried.unrouted_reason,
    original_evidence_size: retried.photo_full && retried.photo_full.size,
    imported_provenance_in_draft: /selected\/imported by the user/.test(retried.email_body || ""),
  }, {
    status: "draft", authority_id: "dl-pwd-sewa", issue_type: "garbage",
    retry_count: 1, unrouted_reason: null, original_evidence_size: blob.size,
    imported_provenance_in_draft: true,
  });
  const retriedCivicEvidence = await StandaloneAPI.handle(
    `/api/reports/${retried.id}/evidence`, {method: "GET"});
  const retriedCivicEvidenceLower = retriedCivicEvidence.text.toLowerCase();
  const retriedCivicFooter = finalParagraphOf(retriedCivicEvidence.text);
  ok("outbound civic evidence: obsolete negative submission boilerplate is absent",
     forbiddenOutboundCopy.every((copy) => !retriedCivicEvidenceLower.includes(copy)),
     retriedCivicEvidence.text);
  ok("outbound civic evidence: independent-app verification footer is final",
     retriedCivicFooter.startsWith(outboundFooterMarker)
       && /\bverify\b/i.test(retriedCivicFooter), retriedCivicFooter);
  eq("outbound civic evidence: footer appears exactly once",
     retriedCivicEvidence.text.split(outboundFooterMarker).length - 1, 1);
  const permanentRetryDb = await new Promise((resolve, reject) => {
    const request = indexedDB.open("potholes");
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  await new Promise((resolve, reject) => {
    const tx = permanentRetryDb.transaction("reports", "readwrite");
    const store = tx.objectStore("reports");
    const get = store.get(createdReports[1].id);
    get.onsuccess = () => {
      const report = get.result;
      report.lat = 11.0;
      report.lng = 77.0;
      report.gps_accuracy = 8;
      report.unrouted_reason = "outside_area";
      store.put(report);
    };
    tx.oncomplete = resolve;
    tx.onabort = () => reject(tx.error || new Error("permanent retry seed aborted"));
    tx.onerror = () => {};
  });
  permanentRetryDb.close();
  ok("retry: a permanent outside-area result refuses a pointless identical retry",
     /cannot change this saved location/.test(await errorFrom(StandaloneAPI.handle(
       `/api/reports/${createdReports[1].id}/retry-routing`, {method: "POST"}))));
  const permanentAfterRetry = (await StandaloneAPI.handle("/api/reports"))
    .find((report) => report.id === createdReports[1].id);
  openDetail(permanentAfterRetry, [permanentAfterRetry]);
  eq("retry UI: a permanent outside-area result does not offer Retry",
     !!document.getElementById("retryRoutingBtn"), false);
  const invalid = new FormData();
  invalid.append("issue_type", "road_damage");
  invalid.append("photo", new File([blob], "road.png", {type: "image/png"}));
  ok("creation: road damage cannot enter the user-confirmed civic endpoint",
     /garbage or open\/damaged manhole/.test(await errorFrom(
       StandaloneAPI.handle("/api/civic-report", {method: "POST", body: invalid}))));

  // A manual civic label must never contaminate the detector-training export. A legacy
  // record with no issue_type remains road data for backwards compatibility.
  const db = await new Promise((resolve, reject) => {
    const request = indexedDB.open("potholes");
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  await new Promise((resolve, reject) => {
    const tx = db.transaction("reports", "readwrite");
    tx.objectStore("reports").clear();
    const common = {
      created_at: 1787470200, photo: pixel, human_label: "pothole_cavity",
      status: "draft", damage_type: "pothole_cavity", decision: "accept",
    };
    tx.objectStore("reports").put({...common, id: 89001});
    tx.objectStore("reports").put({...common, id: 89002, issue_type: "garbage"});
    tx.oncomplete = resolve;
    tx.onabort = () => reject(tx.error || new Error("seed transaction aborted"));
    tx.onerror = () => {};
  });
  db.close();
  const exported = await StandaloneAPI.handle("/api/export", {method: "POST"});
  eq("export: legacy road labels remain included", exported.count, 1);
  ok("export: output remains explicitly road-damage-only",
     /^road-damage-dataset-/.test(exported.name), exported.name);

  return checks;
}
"""


def main() -> None:
    inventory = authority_inventory()
    # These counts pin the intended current scope and make an accidental source omission
    # visible instead of silently reducing the matrix.
    if len(inventory) != 255:
        raise AssertionError(f"expected 255 configured routes/bodies, found {len(inventory)}")
    if sum(item["source_state"] == "KA" for item in inventory) != 183:
        raise AssertionError("expected the Karnataka statewide route and all 182 configured ULBs")
    for state in ("GA", "MP", "BR", "OD"):
        if sum(item["source_state"] == state for item in inventory) != 1:
            raise AssertionError(f"expected one statewide {state} authority")
    for state in (
        "AR", "AS", "HR", "HP", "JH", "MN", "ML", "MZ", "NL", "SK",
        "TR", "UK", "AN", "CH", "DH", "JK", "LA", "LD", "PY",
    ):
        if sum(item["source_state"] == state for item in inventory) != 1:
            raise AssertionError(f"expected one statewide/UT {state} authority")
    if sum(item["source_state"] == "GJ" for item in inventory) != 2:
        raise AssertionError("expected separate Ahmedabad and Gujarat statewide authorities")

    _, delhi_pack_raw = read_pack("in-dl-routing")
    _, maharashtra_pack_raw = read_pack("in-mh-routing")
    highway_requests: list[str] = []
    openai_requests: list[str] = []
    failures: list[str] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=["--disable-web-security"])
        page = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
        page.route(
            route_pattern("in-dl-routing"),
            lambda route: route.fulfill(
                status=200, content_type="application/json", body=delhi_pack_raw
            ),
        )
        page.route(
            route_pattern("in-mh-routing"),
            lambda route: route.fulfill(
                status=200, content_type="application/json", body=maharashtra_pack_raw
            ),
        )

        def observe_highway(route) -> None:
            highway_requests.append(route.request.url)
            route.abort()

        def reject_openai(route) -> None:
            openai_requests.append(route.request.url)
            route.abort()

        # State-pack cache pruning reads the small NH manifest as catalog metadata.  A
        # routing attempt is distinguished by fetching a coordinate-specific NH tile.
        page.route("**/packs/v1/highways/**", observe_highway)
        page.route("https://api.openai.com/**", reject_openai)
        page.goto(APP)
        page.wait_for_load_state("networkidle")
        page.wait_for_function(
            "typeof StandaloneAPI !== 'undefined' && StandaloneAPI.__pure"
            " && typeof StandaloneAPI.__pure.routeForIssue === 'function'",
            timeout=30_000,
        )
        # App startup may warm immutable manifests.  The contract under test is that
        # selecting a civic category does not initiate an NH lookup, so start counting
        # immediately before the civic scenario itself.
        highway_requests.clear()
        openai_requests.clear()
        results = page.evaluate(
            SCENARIO, {"authorities": inventory, "pixel": PIXEL}
        )
        browser.close()

    for name, passed, got, want in results:
        if passed:
            print(f"  ok   {name}")
        else:
            print(f"  FAIL {name}\n         got  {got}\n         want {want}")
            failures.append(name)
    if highway_requests:
        print(f"  FAIL network: civic route requested an NH tile: {highway_requests}")
        failures.append("network: civic route bypasses highway data")
    else:
        print("  ok   network: civic route bypasses highway data")
    if openai_requests:
        print(f"  FAIL network: civic creation requested OpenAI: {openai_requests}")
        failures.append("network: civic creation bypasses OpenAI")
    else:
        print("  ok   network: civic creation bypasses OpenAI")

    print()
    total = len(results) + 2
    if failures:
        print(f"{len(failures)} of {total} failed")
        sys.exit(1)
    print(
        f"CIVIC ISSUE TEST PASS ({total} checks; "
        f"{len(inventory) * 3} route/category combinations)"
    )


if __name__ == "__main__":
    main()
