# -*- coding: utf-8 -*-
"""Opening a handoff must never be presented or counted as a submission."""
import sys

from playwright.sync_api import sync_playwright


APP = "http://localhost:8765/"
PIXEL = (
    "data:image/gif;base64,"
    "R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs="
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
    photo: pixel,
    photo_full: pixel,
    official_grievance_id: null,
    submitted_at: null,
    sent_at: null,
  };
  const records = [
    {
      ...base, id: 71001, status: "draft",
      delivery_channel: "bmc_quickfix", ward_code: "K/W",
      officer_name: "BMC Pothole QuickFix (K/W Ward suggested)",
      authority_name: "Brihanmumbai Municipal Corporation", officer_email: null,
    },
    {
      ...base, id: 71002, created_at: base.created_at + 1, status: "queued",
      handoff_opened_at: base.created_at + 10,
      delivery_channel: "bmc_quickfix", ward_code: "K/W",
      officer_name: "BMC Pothole QuickFix (K/W Ward suggested)",
      authority_name: "Brihanmumbai Municipal Corporation", officer_email: null,
    },
    {
      ...base, id: 71003, created_at: base.created_at + 2, status: "queued",
      handoff_opened_at: base.created_at + 11,
      delivery_channel: "email", ward_code: null,
      officer_name: "Municipal Commissioner", authority_name: "Test Corporation",
      officer_email: "commissioner@example.invalid",
    },
    {
      ...base, id: 71004, created_at: base.created_at + 3, status: "draft",
      delivery_channel: "bmc_quickfix", ward_code: "K/W",
      officer_name: "BMC Pothole QuickFix (K/W Ward suggested)",
      authority_name: "Brihanmumbai Municipal Corporation", officer_email: null,
    },
  ];

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

  const prepared = await StandaloneAPI.handle("/api/reports/71001/send", {method: "POST"});
  eq("handoff: preparing the official link stays draft", prepared.status, "draft");
  eq("handoff: preparation does not set a handoff timestamp",
     prepared.handoff_opened_at, undefined);
  eq("handoff: preparation does not invent a grievance ID", prepared.official_grievance_id, null);
  eq("handoff: preparation does not set submitted_at", prepared.submitted_at, null);
  eq("handoff: preparation does not set sent_at", prepared.sent_at, null);
  ok("handoff: preparation points to BMC's official QuickFix listing",
     String(prepared.handoff_url || "").includes("com.bmc.potholequickfix"),
     prepared.handoff_url);

  const preparedStored = await byId(71001);
  eq("handoff: preparing a link does not mutate persisted status", preparedStored.status, "draft");
  eq("handoff: preparing a link stores no handoff timestamp",
     preparedStored.handoff_opened_at, undefined);

  // The UI records this endpoint only after AppLauncher/window.open reports success.
  const handedOff = await StandaloneAPI.handle(
    "/api/reports/71001/handoff-opened", {method: "POST"});
  eq("handoff: a confirmed launcher open becomes queued", handedOff.status, "queued");
  ok("handoff: records a handoff timestamp",
     Number.isFinite(handedOff.handoff_opened_at), handedOff.handoff_opened_at);
  eq("handoff: opening does not invent a grievance ID", handedOff.official_grievance_id, null);
  eq("handoff: does not set submitted_at", handedOff.submitted_at, null);
  eq("handoff: does not set sent_at", handedOff.sent_at, null);

  const queuedStored = await byId(71001);
  eq("handoff: queued status is persisted", queuedStored.status, "queued");
  eq("handoff: persisted record remains unsubmitted", queuedStored.submitted_at, null);

  // The queued detail must explain the truth and offer the explicit confirmation step.
  openDetail(queuedStored, [queuedStored]);
  const queuedUi = {
    verdict: document.querySelector("#detail .verdict").textContent.trim(),
    text: document.getElementById("detail").textContent,
    hasReferenceInput: !!document.getElementById("grievanceId"),
    hasMarkButton: !!document.getElementById("markSubmittedBtn"),
  };
  ok("UI: queued BMC says handoff, not submitted",
     /handoff/i.test(queuedUi.verdict) && !/submitted/i.test(queuedUi.verdict), queuedUi.verdict);
  ok("UI: queued BMC explicitly says this app does not submit",
     /does not submit a BMC grievance/i.test(queuedUi.text), queuedUi.text);
  ok("UI: queued BMC asks for the official reference",
     queuedUi.hasReferenceInput && queuedUi.hasMarkButton, queuedUi);

  // If Android/the browser refuses to launch QuickFix, sendReport must leave the
  // record as a draft and tell the user; merely preparing the URL is not a handoff.
  const blockedReport = await byId(71004);
  const priorConfirm = window.confirm, priorAlert = window.alert, priorOpen = window.open;
  const blockedAlerts = [];
  window.confirm = () => true;
  window.alert = (message) => blockedAlerts.push(String(message));
  window.open = () => null;
  openDetail(blockedReport, [blockedReport]);
  await sendReport(blockedReport);
  window.confirm = priorConfirm;
  window.alert = priorAlert;
  window.open = priorOpen;
  const afterBlockedLaunch = await byId(71004);
  eq("UI handoff: blocked launcher leaves report draft", afterBlockedLaunch.status, "draft");
  eq("UI handoff: blocked launcher stores no handoff time",
     afterBlockedLaunch.handoff_opened_at, undefined);
  ok("UI handoff: blocked launcher produces an actionable error",
     blockedAlerts.some((message) => /could not open Pothole QuickFix/i.test(message)),
     blockedAlerts);

  const blankError = await errorFrom(StandaloneAPI.handle(
    "/api/reports/71001/submitted",
    {method: "POST", body: JSON.stringify({official_grievance_id: ""})},
  ));
  ok("BMC confirmation: blank grievance ID is rejected",
     /official BMC grievance ID/i.test(blankError || ""), blankError);
  const shortError = await errorFrom(StandaloneAPI.handle(
    "/api/reports/71001/submitted",
    {method: "POST", body: JSON.stringify({official_grievance_id: "123"})},
  ));
  ok("BMC confirmation: implausibly short grievance ID is rejected",
     /official BMC grievance ID/i.test(shortError || ""), shortError);
  const afterRejectedConfirmation = await byId(71001);
  eq("BMC confirmation: rejected attempt stays queued",
     afterRejectedConfirmation.status, "queued");
  eq("BMC confirmation: rejected attempt stores no submission time",
     afterRejectedConfirmation.submitted_at, null);

  const submitted = await StandaloneAPI.handle("/api/reports/71001/submitted", {
    method: "POST",
    body: JSON.stringify({official_grievance_id: "  BMC-2026-000123  "}),
  });
  eq("BMC confirmation: valid reference marks sent", submitted.status, "sent");
  eq("BMC confirmation: reference is trimmed",
     submitted.official_grievance_id, "BMC-2026-000123");
  ok("BMC confirmation: submitted_at is recorded",
     Number.isFinite(submitted.submitted_at), submitted.submitted_at);
  eq("BMC confirmation: legacy sent_at matches submitted_at",
     submitted.sent_at, submitted.submitted_at);

  const submittedStored = await byId(71001);
  eq("BMC confirmation: reference is persisted",
     submittedStored.official_grievance_id, "BMC-2026-000123");
  openDetail(submittedStored, [submittedStored]);
  const sentUi = {
    verdict: document.querySelector("#detail .verdict").textContent.trim(),
    text: document.getElementById("detail").textContent,
    hasSend: !!document.getElementById("sendBtn"),
    hasMark: !!document.getElementById("markSubmittedBtn"),
  };
  ok("UI: confirmed BMC is visibly marked submitted",
     /marked submitted/i.test(sentUi.verdict), sentUi.verdict);
  ok("UI: confirmed BMC shows the official reference",
     sentUi.text.includes("BMC-2026-000123"), sentUi.text);
  ok("UI: confirmed BMC has no repeat send/mark controls",
     !sentUi.hasSend && !sentUi.hasMark, sentUi);

  // One confirmed report plus two merely queued handoffs: the dashboard must say 1.
  await openDash();
  const stats = [...document.querySelectorAll("#dashStats .card")].map((card) => ({
    value: card.querySelector(".verdict").textContent.trim(),
    label: card.querySelector(".meta").textContent.trim(),
  }));
  const submissionTile = stats.find((tile) => /confirmed submissions/i.test(tile.label));
  ok("dashboard: labels the metric as confirmed submissions", !!submissionTile, stats);
  eq("dashboard: queued handoffs are not counted as submissions",
     submissionTile && submissionTile.value, "1");

  // Email also requires an explicit citizen confirmation, but unlike BMC it has no
  // official grievance ID to record.
  const emailSubmitted = await StandaloneAPI.handle("/api/reports/71003/submitted", {
    method: "POST", body: "{}",
  });
  eq("email confirmation: explicit confirmation marks sent", emailSubmitted.status, "sent");
  eq("email confirmation: no grievance ID is invented",
     emailSubmitted.official_grievance_id, null);

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
        )
        page = context.new_page()
        page.goto(APP)
        page.wait_for_load_state("networkidle")
        page.wait_for_function(
            "typeof StandaloneAPI !== 'undefined' && typeof openDetail === 'function' "
            "&& typeof openDash === 'function'",
            timeout=30000,
        )
        results = page.evaluate(SCENARIO, {"pixel": PIXEL})
        context.close()
        browser.close()

    for name, passed, got, want in results:
        if not passed:
            failures.append(name)
            print(f"  FAIL {name}\n         got  {got}\n         want {want}")
    if failures:
        print(f"{len(failures)} of {len(results)} failed")
        sys.exit(1)
    print(f"SUBMISSION TRUTH TEST PASS ({len(results)} checks)")


main()
