# -*- coding: utf-8 -*-
"""Bengali/KMC UI must be complete, truthful, and render without English fallback."""
import sys

from playwright.sync_api import sync_playwright


APP = "http://localhost:8765/"

INIT = """
localStorage.setItem('openai_key', 'test-key-never-sent');
localStorage.setItem('app_lang', 'bn');
"""

SCENARIO = r"""
async () => {
  const checks = [];
  const eq = (name, got, want) => checks.push([
    name, JSON.stringify(got) === JSON.stringify(want), got, want,
  ]);
  const ok = (name, value, detail) => checks.push([
    name, !!value, detail === undefined ? value : detail, true,
  ]);
  const placeholders = (value) => [...String(value || "").matchAll(/\{([^}]+)\}/g)]
    .map((m) => m[1]).sort();

  const enKeys = Object.keys(I18N.en).sort();
  const bnKeys = Object.keys(I18N.bn || {}).sort();
  eq("i18n: Bengali has exactly the English key set", bnKeys, enKeys);
  eq("i18n: Bengali key count is pinned", bnKeys.length, 240);
  for (const key of enKeys) {
    eq(`i18n placeholder parity: ${key}`,
       placeholders(I18N.bn[key]), placeholders(I18N.en[key]));
  }
  eq("i18n: runtime selected Bengali", LANG, "bn");
  eq("i18n: document language is Bengali", document.documentElement.lang, "bn");
  eq("i18n: selector exposes four languages",
     [...document.querySelectorAll("#setLang option")].map((x) => x.value),
     ["en", "kn", "mr", "bn"]);
  ok("i18n: visible capture action is concise Bengali",
     document.getElementById("captureBtn").textContent
       .replace(/^[^\p{L}\p{N}]+/u, "").trim() === "ছবি",
     document.getElementById("captureBtn").textContent);
  ok("i18n: visible disclosure is Bengali and names OpenAI",
     /OpenAI/.test(document.getElementById("privacyBody").textContent)
       && /ক্যামেরা/.test(document.getElementById("privacyBody").textContent),
     document.getElementById("privacyBody").textContent);
  ok("privacy: background-drive disclosure forces fresh consent",
     /v14-durable-drive-frames$/.test(DATA_NOTICE_VERSION), DATA_NOTICE_VERSION);

  const P = StandaloneAPI.__pure;
  const complaintFooter = "Pothole Reporter একটি স্বাধীন অ্যাপ। প্রস্তাবিত কর্তৃপক্ষ, ওয়ার্ড, "
    + "রাস্তার মালিকানা এবং টেন্ডারের তথ্য অনুগ্রহ করে যাচাই করুন।";
  const route = await P.kolkataRouteFromGeocode(null, 22.5726, 88.3639, 12);
  const [subject, body] = P.draftEmail({
    damage_type: "pothole_cavity", assessment: "clear", size: "medium",
  }, 22.5726, 88.3639, "Esplanade, Kolkata", route.officer_name, null, route);
  ok("draft: Bengali subject identifies a pothole complaint",
     /রাস্তার গর্ত.*অভিযোগ/.test(subject), subject);
  const complaintBlocks = body.trim().split(/\n{2,}/).map((part) => part.trim());
  const complaintWithoutFooter = complaintBlocks.slice(0, -1).join("\n\n");
  ok("draft: Bengali body uses KMC's formal civic terminology",
     /কলকাতা পৌরসংস্থা/.test(body), body);
  ok("draft: Bengali body retains exact coordinates and map link",
     /22\.572600, 88\.363900/.test(body) && /maps\.google\.com/.test(body), body);
  ok("draft: complaint has one final independent-app verification footer",
     body.split(complaintFooter).length - 1 === 1
       && complaintBlocks.at(-1) === complaintFooter, body);
  ok("draft: complaint removes the old no-submission sentence",
     !body.includes("অভিযোগ জমা দেয় না")
       && !/official (?:grievance )?submission/i.test(body), body);
  ok("draft: KMC route keeps a truthful mandatory no-candidate contract block",
     /CONTRACT VERIFICATION/.test(complaintWithoutFooter)
       && /No verified exact-road public contract found/.test(complaintWithoutFooter)
       && !/Tender number:|Listed contractor:|Exact work name:/.test(complaintWithoutFooter)
       && !/warranty is active|under warranty/i.test(complaintWithoutFooter), body);

  const report = {
    id: 72001, created_at: Date.now() / 1000, captured_at: Date.now() / 1000,
    status: "draft", decision: "accept", damage_type: "pothole_cavity",
    assessment: "clear", image_quality: "usable", size: "medium",
    address: "Esplanade, Kolkata", lat: 22.5726, lng: 88.3639, gps_accuracy: 12,
    photo: "data:image/png;base64,iVBORw0KGgo=", photo_full: null,
    email_subject: subject, email_body: body, officer_email: null,
    officer_name: route.officer_name, authority_id: route.authority_id,
    authority_name: route.authority_name,
    authority_registry_version: route.authority_registry_version,
    delivery_channel: "official_handoff", region: "kolkata",
    routing_source: route.routing_source, routing_match_field: "boundary",
    routing_match_value: route.routing_match_value, ownership_unverified: true,
    routing_pack_id: route.routing_pack_id,
    routing_pack_version: route.routing_pack_version,
    routing_pack_sha256: route.routing_pack_sha256,
    routing_pack_state_code: route.routing_pack_state_code,
    handoff_name: route.handoff_name, handoff_url: route.handoff_url,
    alternate_handoff_name: route.alternate_handoff_name,
    alternate_handoff_url: route.alternate_handoff_url,
    whatsapp_url: route.whatsapp_url, helpline: route.helpline,
    requires_official_reference: true, official_grievance_id: null,
  };
  const db = await new Promise((resolve, reject) => {
    const request = indexedDB.open("potholes");
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  await new Promise((resolve, reject) => {
    const tx = db.transaction("reports", "readwrite");
    tx.objectStore("reports").put(report);
    tx.oncomplete = resolve;
    tx.onabort = () => reject(tx.error || new Error("seed transaction aborted"));
    tx.onerror = () => {};
  });
  db.close();
  openDetail(report, [report]);
  const detailText = document.getElementById("detail").textContent;
  ok("handoff UI: names KMC and Grievance 2.0",
     /Kolkata Municipal Corporation/.test(detailText) && /KMC Grievance 2\.0/.test(detailText),
     detailText);
  ok("handoff UI: Bengali warning says containment is not road ownership",
     /রাস্তার মালিকানা প্রমাণিত হয় না/.test(detailText), detailText);
  ok("handoff UI: asks for a generic official reference",
     /সরকারি অভিযোগ\/রেফারেন্স নম্বর/.test(detailText)
       && !!document.getElementById("grievanceId"), detailText);
  ok("handoff UI: offers app, WhatsApp and helpline without claiming submission",
     /KMC APP/.test(detailText) && /WhatsApp/.test(detailText)
       && /18003453375/.test(detailText) && !/অভিযোগ জমা হয়েছে/.test(detailText),
     detailText);

  const confirmations = [];
  const priorConfirm = window.confirm;
  window.confirm = (message) => { confirmations.push(String(message)); return false; };
  await openOfficialWhatsApp(report);
  window.confirm = priorConfirm;
  ok("WhatsApp: Bengali confirmation states exact location and final-send boundary",
     confirmations.some((message) => /সঠিক অবস্থান/.test(message)
       && /Send না চাপা পর্যন্ত কিছুই পাঠানো হবে না/.test(message)), confirmations);

  return checks;
}
"""


def main():
    failures = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=["--disable-web-security"])
        context = browser.new_context(viewport={"width": 390, "height": 844})
        context.add_init_script(INIT)
        page = context.new_page()
        page.goto(APP)
        page.wait_for_load_state("networkidle")
        page.wait_for_function(
            "typeof I18N !== 'undefined' && typeof openDetail === 'function' "
            "&& typeof StandaloneAPI !== 'undefined'",
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
    print(f"KOLKATA UI TEST PASS ({len(results)} checks)")


main()
