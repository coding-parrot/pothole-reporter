# -*- coding: utf-8 -*-
"""Marathi Mumbai UI and complaint drafting must not silently fall back to English."""
import sys

from playwright.sync_api import sync_playwright


APP = "http://localhost:8765/"

SCENARIO = r"""
(() => {
  const checks = [];
  const eq = (name, got, want) => checks.push([
    name, JSON.stringify(got) === JSON.stringify(want), got, want,
  ]);
  const ok = (name, condition, detail) => checks.push([
    name, !!condition, detail === undefined ? condition : detail, true,
  ]);
  const devanagari = /[\u0900-\u097f]/;
  const bmcKeys = [
    "chip_queued_bmc", "bmc_prepare_btn", "bmc_share_btn", "bmc_quickfix_btn",
    "bmc_whatsapp_btn", "bmc_call_btn", "bmc_disclaimer", "bmc_ward",
    "complaint_title_label", "complaint_body_label", "official_grievance_label",
    "official_grievance_placeholder", "mark_submitted_btn", "submitted_reference",
    "confirm_bmc_handoff", "confirm_mark_submitted", "evidence_shared",
  ];

  eq("language: saved Marathi is selected", LANG, "mr");
  openSettings(false);
  eq("language: settings selector reflects Marathi",
     document.getElementById("setLang").value, "mr");
  ok("language: primary UI renders Marathi",
     devanagari.test(document.getElementById("subTitle").textContent),
     document.getElementById("subTitle").textContent);
  const missing = bmcKeys.filter((key) => !Object.prototype.hasOwnProperty.call(I18N.mr, key));
  eq("language: every essential Mumbai UI key has a Marathi value", missing, []);
  const EnglishFallbacks = bmcKeys.filter((key) => I18N.mr[key] === I18N.en[key]);
  eq("language: Mumbai strings do not silently fall back to English", EnglishFallbacks, []);

  const route = {delivery_channel: "bmc_quickfix", ward_code: "K/W"};
  const [subject, body] = StandaloneAPI.__pure.draftEmail({
    damage_type: "pothole_cavity", size: "medium", assessment: "clear",
  }, 19.1197, 72.8468, "जुहू लेन, मुंबई", "BMC Pothole QuickFix", null, route);
  ok("draft: Marathi complaint title is Devanagari", devanagari.test(subject), subject);
  ok("draft: Marathi complaint body is Devanagari", devanagari.test(body), body);
  ok("draft: BMC official channels are named",
     body.includes("BMC") && body.includes("Pothole QuickFix") && body.includes("1916"), body);
  ok("draft: complaint says this app does not submit",
     body.includes("दाखल करत नाही"), body);
  ok("draft: suggested ward is visibly qualified", body.includes("K/W"), body);

  const report = {
    id: 72001, status: "queued", created_at: 1787260200,
    damage_type: "pothole_cavity", assessment: "clear", image_quality: "usable",
    size: "medium", description: "खड्डा", address: "जुहू लेन, मुंबई",
    delivery_channel: "bmc_quickfix", ward_code: "K/W",
    officer_name: "BMC Pothole QuickFix (K/W Ward suggested)", officer_email: null,
    email_subject: subject, email_body: body, photo_url: "", photo: "",
    official_grievance_id: null, submitted_at: null, sent_at: null,
  };
  openDetail(report, [report]);
  const detailText = document.getElementById("detail").textContent;
  const verdict = document.querySelector("#detail .verdict").textContent.trim();
  ok("UI: queued Mumbai verdict is Marathi", devanagari.test(verdict), verdict);
  ok("UI: Marathi detail says the app does not submit",
     detailText.includes("दाखल करत नाही"), detailText);
  ok("UI: Marathi detail asks for BMC grievance number",
     detailText.includes("अधिकृत BMC तक्रार क्रमांक") &&
       !!document.getElementById("grievanceId") && !!document.getElementById("markSubmittedBtn"),
     detailText);
  ok("UI: Marathi detail does not fall back to the English disclaimer",
     !detailText.includes("does not submit a BMC grievance"), detailText);

  return checks;
})()
"""


def main():
    failures = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=["--disable-web-security"])
        context = browser.new_context(viewport={"width": 390, "height": 844})
        context.add_init_script(
            "localStorage.setItem('openai_key', 'test-key-never-sent');"
            "localStorage.setItem('app_lang', 'mr');"
            "localStorage.setItem('sender_name', 'मुंबईकर');"
        )
        page = context.new_page()
        page.goto(APP)
        page.wait_for_load_state("networkidle")
        page.wait_for_function(
            "typeof StandaloneAPI !== 'undefined' && StandaloneAPI.__pure "
            "&& typeof I18N === 'object' && typeof openDetail === 'function'",
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
    print(f"MUMBAI UI TEST PASS ({len(results)} checks)")


main()
