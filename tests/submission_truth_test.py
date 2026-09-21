# -*- coding: utf-8 -*-
"""Opening an email composer must never be presented or counted as a submission.

The app files nothing. It opens a draft addressed to the routed authority, and the
person presses Send in their own mail app. Every claim on screen, in storage and in the
outbound text has to stay inside that boundary: a queued report is queued, a report the
shared map has not confirmed cannot be emailed at all, and no grievance number,
submission time or contractor name is ever invented.

This suite replaced a v1.14 one that drove BMC/PMC portal handoffs, WhatsApp launches,
helpline numbers and a /submitted endpoint. Those channels were removed: opening another
service proves nothing about whether a complaint was filed.
"""
import os
import sys

from playwright.sync_api import sync_playwright


APP = os.environ.get("POTHOLE_TEST_APP", "http://localhost:8765/")
PIXEL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)

SCENARIO = r"""
async ({pixel}) => {
  const checks = [];
  const eq = (name, got, want) => checks.push([
    name, JSON.stringify(got) === JSON.stringify(want), got, want,
  ]);
  const ok = (name, condition, detail) => checks.push([
    name, !!condition, detail === undefined ? condition : detail, true,
  ]);
  const errorFrom = async (promise) => {
    try { await promise; return null; }
    catch (error) { return String(error && error.message || error); }
  };
  const byId = async (id) => (await StandaloneAPI.handle("/api/reports"))
    .find((report) => report.id === id);
  const outboundFooterMarker = "Pothole Reporter is an independent app.";
  const forbiddenOutboundCopy = [
    "no official grievance submission is confirmed",
    "email delivery is not confirmed",
  ];
  const finalParagraphOf = (text) => String(text || "").trim().split(/\n{2,}/).at(-1) || "";

  await StandaloneAPI.handle("/api/reports", {method: "DELETE"});
  const base = {
    created_at: 1787260200,
    captured_at: 1787260200,
    decision: "accept",
    damage_type: "pothole_cavity",
    assessment: "clear",
    image_quality: "usable",
    size: "medium",
    description: "Road cavity",
    address: "Juhu Lane, Mumbai",
    email_subject: "Pothole complaint",
    email_body: "Please inspect and repair this pothole.",
    lat: 19.1197,
    lng: 72.8468,
    gps_accuracy: 12,
    photo: pixel,
    photo_full: pixel,
    official_grievance_id: null,
    submitted_at: null,
    sent_at: null,
  };

  // ---------- outbound text ----------
  const [generatedRoadSubject, generatedRoadBody] = StandaloneAPI.__pure.draftEmail(
    {damage_type: "pothole_cavity", assessment: "clear", size: "medium", is_pothole: true},
    18.5308, 73.8475, "Shivajinagar, Pune", "PMC Road Mitra, Pune Municipal Corporation",
    null,
    {
      authority_id: "mh-pmc", authority_name: "Pune Municipal Corporation",
      delivery_channel: "official_handoff", handoff_name: "PMC Road Mitra",
      ownership_unverified: true, ward_code: null,
    },
  );
  const generatedRoadEvidence = await StandaloneAPI.__pure.evidenceForReport({
    ...base, id: "generated-road-outbound", issue_type: "road_damage", status: "draft",
    capture_source: "manual_camera", email_subject: generatedRoadSubject,
    email_body: generatedRoadBody, authority_id: "mh-pmc",
    authority_name: "Pune Municipal Corporation",
  });
  const generatedRoadEvidenceLower = generatedRoadEvidence.text.toLowerCase();
  const generatedRoadFooter = finalParagraphOf(generatedRoadEvidence.text);
  ok("outbound road evidence: obsolete negative submission boilerplate is absent",
     forbiddenOutboundCopy.every((copy) => !generatedRoadEvidenceLower.includes(copy)),
     generatedRoadEvidence.text);
  ok("outbound road evidence: independent-app verification footer is final",
     generatedRoadFooter.startsWith(outboundFooterMarker)
       && /\bverify\b/i.test(generatedRoadFooter), generatedRoadFooter);
  eq("outbound road evidence: footer appears exactly once",
     generatedRoadEvidence.text.split(outboundFooterMarker).length - 1, 1);

  // ---------- stored reports ----------
  const records = [
    {
      // A routed council draft. It has been through the central resolver, so it
      // carries the ownership answer, the time it was checked, and its shared-map id.
      ...base, id: 71006, created_at: base.created_at + 5, status: "draft",
      address: "Ambarnath, Thane", lat: 19.1860, lng: 73.1910,
      delivery_channel: "email", ward_code: null,
      officer_name: "Civic complaint desk, Ambarnath Municipal Council",
      officer_email: "coud.ambernath@maharashtra.gov.in",
      authority_id: "mh-ambarnath", authority_name: "Ambarnath Municipal Council",
      authority_registry_version: 1, region: "mmr",
      // Deliberately stale: a legacy row from before polygon provenance.
      routing_source: "openstreetmap_structured", routing_match_field: "town",
      routing_match_value: "Ambarnath", ownership_unverified: true,
      requires_official_reference: false,
    },
    {
      // Never reached the shared map: no server id, so email must stay unavailable.
      ...base, id: 71007, created_at: base.created_at + 6, status: "draft",
      address: "Ambarnath, Thane", lat: 19.1860, lng: 73.1910,
      delivery_channel: "email", officer_name: "Civic complaint desk",
      officer_email: "coud.ambernath@maharashtra.gov.in",
      authority_id: "mh-ambarnath", authority_name: "Ambarnath Municipal Council",
      server_pothole_id: null, central_sync_pending: true,
    },
    {
      // The shared map grouped this with a pothole already on the map. That is a fact
      // about the map's counting, not a reason to withhold this reporter's complaint.
      ...base, id: 71008, created_at: base.created_at + 7, status: "draft",
      address: "Ambarnath, Thane", lat: 19.1860, lng: 73.1910,
      server_pothole_id: 5150, central_sync_pending: false,
      server_duplicate: true, seen_count: 3,
    },
    {
      // Routing failed at capture time and can be retried.
      ...base, id: 71022, created_at: base.created_at + 21, status: "unrouted",
      issue_type: "road_damage", address: "Shivajinagar, Pune",
      lat: 18.5308, lng: 73.8475, gps_accuracy: 8,
      surface_type: "bituminous_asphalt",
      measurement_provenance: "visual_estimate_no_scale",
      measurement_confidence: "low",
      email_subject: null, email_body: null, delivery_channel: null,
      officer_name: null, officer_email: null, authority_id: null, authority_name: null,
      routing_source: null, routing_match_field: null, routing_match_value: null,
      unrouted_reason: "road_class_unknown", unrouted_body: null,
    },
  ];
  // A saved draft has already been through the central resolver: it carries the
  // ownership answer and the time it was checked. Without those the send path
  // re-resolves against the live service, which a test must never reach.
  records.forEach((record, index) => {
    if (record.server_pothole_id === undefined) record.server_pothole_id = 800001 + index;
    if (record.road_ownership === undefined) record.road_ownership = "municipal";
    if (record.road_ownership_source === undefined) record.road_ownership_source = "central_v1";
    if (record.tender_resolution_checked_at === undefined) {
      record.tender_resolution_checked_at = record.created_at;
    }
  });

  const db = await new Promise((resolve, reject) => {
    const request = indexedDB.open("potholes");
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  await new Promise((resolve, reject) => {
    const tx = db.transaction("reports", "readwrite");
    for (const record of records) tx.objectStore("reports").put(record);
    tx.oncomplete = resolve;
    tx.onabort = () => reject(tx.error || new Error("seed transaction aborted"));
    tx.onerror = () => {};
  });
  db.close();

  // ---------- refusals ----------
  const unconfirmedError = await errorFrom(
    StandaloneAPI.handle("/api/reports/71007/send", {method: "POST"}));
  ok("refusal: an unconfirmed report cannot open an email",
     /shared map has not confirmed/i.test(unconfirmedError || ""), unconfirmedError);
  eq("refusal: the refused report is not mutated",
     (await byId(71007)).status, "draft");

  // Repeat-detection dedupe was removed. A sighting the shared map counted against an
  // existing pothole still gets its own complaint, so this must not be refused.
  const repeatError = await errorFrom(
    StandaloneAPI.handle("/api/reports/71008/send", {method: "POST"}));
  ok("a sighting the map already knows is still emailable",
     !/already reported|duplicate/i.test(repeatError || ""), repeatError);

  const unroutedError = await errorFrom(
    StandaloneAPI.handle("/api/reports/71022/send", {method: "POST"}));
  ok("refusal: an unrouted report names its actual reason",
     /national, state, or district highway/i.test(unroutedError || "")
       && !/outside/i.test(unroutedError || ""), unroutedError);

  // ---------- the one complaint action ----------
  const councilDraft = await byId(71006);
  openDetail(councilDraft, [councilDraft]);
  const councilUiText = document.getElementById("detail").textContent;
  ok("detail: names the authority the draft is addressed to", 
     /Ambarnath Municipal Council/.test(councilUiText), councilUiText);

  // One tap, one action: no second in-app confirmation and no choice of channel. The
  // ownership caveat travels with the complaint instead, in its closing paragraph.
  const priorCouncilConfirm = window.confirm;
  const councilConfirms = [];
  window.confirm = (message) => { councilConfirms.push(String(message)); return false; };
  await sendReport(councilDraft);
  window.confirm = priorCouncilConfirm;
  eq("send: Email is one tap, with no in-app confirmation step", councilConfirms, []);

  const opened = await StandaloneAPI.handle("/api/reports/71006/send", {method: "POST"});
  eq("send: the composer is addressed to the routed recipient",
     opened.officer_email, "coud.ambernath@maharashtra.gov.in");
  ok("send: the draft carries the complaint text, not an empty composer",
     !!opened.email_subject && !!opened.email_body, opened.email_subject);

  const councilAfterSend = await byId(71006);
  eq("send: opening the composer leaves the report queued, not sent",
     councilAfterSend.status, "queued");
  eq("send: no grievance ID, submission time or send time is invented",
     [councilAfterSend.official_grievance_id, councilAfterSend.submitted_at,
      councilAfterSend.sent_at], [null, null, null]);
  ok("send: the recorded time is when the composer opened, not a delivery",
     Number.isFinite(councilAfterSend.email_opened_at),
     councilAfterSend.email_opened_at);
  // The central service owns the ownership answer. A send must not quietly replace it
  // with a phone-side guess, and must not drop the recipient it was routed to.
  eq("send: the central ownership answer is kept, not re-guessed locally",
     [councilAfterSend.road_ownership, councilAfterSend.road_ownership_source],
     ["municipal", "central_v1"]);

  // A queued report stays reopenable: cancelling the composer must not strand it.
  const reopenError = await errorFrom(StandaloneAPI.handle(
    "/api/reports/71006/send", {method: "POST"}));
  eq("send: a queued report can open its composer again", reopenError, null);
  eq("send: reopening still claims nothing", (await byId(71006)).submitted_at, null);

  // ---------- routing retry ----------
  const retriedRoad = await StandaloneAPI.handle(
    "/api/reports/71022/retry-routing", {method: "POST"});
  eq("road retry: a saved pothole recovers after temporary routing failure",
     [retriedRoad.status, retriedRoad.authority_id, retriedRoad.delivery_channel],
     ["draft", "mh-pmc", "official_handoff"]);
  ok("road retry: recovered complaint keeps coordinate and intake invariants",
     /18\.530800, 73\.847500/.test(retriedRoad.portal_copy_text || "")
       && /Pune Municipal Corporation/.test(retriedRoad.portal_copy_text || ""),
     retriedRoad.portal_copy_text);
  ok("road retry: unverified contract identity is still omitted",
     /No verified exact-road public contract found/.test(retriedRoad.portal_copy_text || "")
       && !/listed contractor:/i.test(retriedRoad.portal_copy_text || ""),
     retriedRoad.portal_copy_text);

  return checks;
}
"""


def main():
    failures = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=["--disable-web-security"])
        context = browser.new_context(viewport={"width": 390, "height": 844})
        context.add_init_script(
            "localStorage.setItem('openai_key', 'test-key-never-sent');"
            "localStorage.setItem('app_lang', 'en');"
            "localStorage.setItem('initial_setup_complete', '1');"
        )
        page = context.new_page()
        page.goto(APP)
        page.wait_for_load_state("networkidle")
        page.wait_for_function(
            "typeof StandaloneAPI !== 'undefined' && StandaloneAPI.__pure "
            "&& typeof openDetail === 'function' && typeof sendReport === 'function'",
            timeout=30000,
        )
        results = page.evaluate(SCENARIO, {"pixel": PIXEL})
        context.close()
        browser.close()

    for name, passed, got, want in results:
        if not passed:
            failures.append(f"{name}\n         got  {got}\n         want {want}")
    if failures:
        print("FAIL")
        for failure in failures:
            print("  -", failure)
        sys.exit(1)
    print(f"SUBMISSION TRUTH TEST PASS ({len(results)} checks)")


main()
