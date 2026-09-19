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
  // The BMC/PMC portal, WhatsApp and helpline handoffs were removed: email is the only
  // complaint channel, so these keys are gone from every language. What has to stay
  // true is that Marathi is complete and never silently falls back to English.
  eq("language: saved Marathi is selected", LANG, "mr");
  openSettings(false);
  eq("language: settings selector reflects Marathi",
     document.getElementById("setLang").value, "mr");
  ok("language: primary UI renders Marathi",
     devanagari.test(document.getElementById("subTitle").textContent),
     document.getElementById("subTitle").textContent);
  const missing = Object.keys(I18N.en).filter(
    (key) => !Object.prototype.hasOwnProperty.call(I18N.mr, key));
  eq("language: every UI key has a Marathi value", missing, []);
  // Placeholders, numbers and Latin product names are identical by design; anything
  // else sharing the English string means a Marathi screen shows English.
  const untranslated = Object.keys(I18N.en).filter((key) => I18N.mr[key] === I18N.en[key]
    && /[A-Za-z]{4}/.test(String(I18N.en[key]).replace(/\{[a-z_]+\}/g, "")));
  eq("language: Marathi strings do not silently fall back to English", untranslated, []);

  const route = {
    delivery_channel: "bmc_quickfix", ward_code: "K/W",
    authority_name: "Brihanmumbai Municipal Corporation",
    handoff_name: "BMC Pothole QuickFix", handoff_url: "https://example.invalid/quickfix",
    helpline: "1916", ownership_unverified: true, requires_official_reference: true,
  };
  const complaintFooter = "Pothole Reporter हे स्वतंत्र अॅप आहे. सुचवलेली संस्था, विभाग, "
    + "रस्त्याची मालकी आणि कोणतेही निविदा तपशील कृपया पडताळा.";
  const [subject, body] = StandaloneAPI.__pure.draftEmail({
    damage_type: "pothole_cavity", size: "medium", assessment: "clear",
  }, 19.1197, 72.8468, "जुहू लेन, मुंबई", "BMC Pothole QuickFix", null, route);
  ok("draft: Marathi complaint title is Devanagari", devanagari.test(subject), subject);
  ok("draft: Marathi complaint body is Devanagari", devanagari.test(body), body);
  ok("draft: complaint addresses BMC rather than its handoff app",
     body.startsWith("प्रति Brihanmumbai Municipal Corporation,")
       && !body.startsWith("प्रति BMC Pothole QuickFix"), body);
  const complaintBlocks = body.trim().split(/\n{2,}/).map((part) => part.trim());
  const complaintWithoutFooter = complaintBlocks.slice(0, -1).join("\n\n");
  ok("draft: complaint has one final independent-app verification footer",
     body.split(complaintFooter).length - 1 === 1
       && complaintBlocks.at(-1) === complaintFooter, body);
  ok("draft: complaint removes the old no-submission sentence",
     !body.includes("दाखल करत नाही")
       && !/official (?:grievance )?submission/i.test(body), body);
  ok("draft: footer tells the reader to verify road ownership",
     complaintFooter.includes("रस्त्याची मालकी") && complaintFooter.includes("पडताळा"),
     complaintFooter);
  ok("draft: unmatched Mumbai route has no tender, contractor or warranty details",
     !/संभाव्य निविदा जुळणी|निविदा क्रमांक|कामाचे नाव|कंत्राटदार|हमी स्थिती|दोष दायित्व|देखभाल कालावधी/.test(
       complaintWithoutFooter), body);
  ok("draft: suggested ward is visibly qualified", body.includes("K/W"), body);

  const report = {
    id: 72001, status: "queued", created_at: 1787260200,
    damage_type: "pothole_cavity", assessment: "clear", image_quality: "usable",
    size: "medium", description: "खड्डा", address: "जुहू लेन, मुंबई",
    delivery_channel: "bmc_quickfix", ward_code: "K/W",
    officer_name: "BMC Pothole QuickFix (K/W Ward suggested)", officer_email: null,
    authority_name: "Brihanmumbai Municipal Corporation",
    handoff_name: "BMC Pothole QuickFix", helpline: "1916",
    ownership_unverified: true, requires_official_reference: true,
    email_subject: subject, email_body: body, photo_url: "", photo: "",
    official_grievance_id: null, submitted_at: null, sent_at: null,
  };
  report.server_pothole_id = 72001;
  openDetail(report, [report]);
  const detailText = document.getElementById("detail").textContent;
  const verdict = document.querySelector("#detail .verdict").textContent.trim();
  ok("UI: queued Mumbai verdict is Marathi", devanagari.test(verdict), verdict);
  ok("UI: Marathi detail does not fall back to English",
     !/[A-Za-z]{4}/.test(detailText.replace(/Brihanmumbai Municipal Corporation|BMC[^,]*|K\/W/g, "")),
     detailText);
  ok("UI: a confirmed Mumbai report offers email and no second channel",
     !!document.getElementById("sendBtn")
       && document.getElementById("sendBtn").dataset.complaintAction === "email"
       && !document.getElementById("grievanceId")
       && !document.getElementById("markSubmittedBtn"), detailText);

  const pmcReport = {
    ...report, id: 72002, server_pothole_id: 72002, address: "शिवाजीनगर, पुणे",
    ward_code: null, delivery_channel: "official_handoff", authority_id: "mh-pmc",
    authority_name: "Pune Municipal Corporation",
    officer_name: "PMC Road Mitra, Pune Municipal Corporation",
  };
  openDetail(pmcReport, [pmcReport]);
  const pmcText = document.getElementById("detail").textContent;
  ok("UI: Marathi PMC detail shows the Pune address the complaint is about",
     pmcText.includes("शिवाजीनगर, पुणे"), pmcText);
  ok("UI: PMC detail offers email without a second channel",
     !!document.getElementById("sendBtn")
       && !/PMC CARE|1800-103-0222|WhatsApp/.test(pmcText), pmcText);

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
