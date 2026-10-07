# -*- coding: utf-8 -*-
"""The generated email is concise, complete, and does not overclaim.

The detector has one accepted road-defect class: pothole. The letter states where and
when the photo was taken and asks the office to verify the rest: that the road is its
own, and whether one named road work covers the spot. It never says a contract, a ward
or an owner was verified (verify_request_letter_test pins the full text).
"""
import os
import sys

from playwright.sync_api import sync_playwright


FOOTER = (
    "Pothole Reporter is an independent app. Please verify any suggested authority, "
    "ward, road ownership, and tender details."
)

JS = r"""
(override) => {
  const P = StandaloneAPI.__pure;
  const assessment = P.binaryAssessment({
    is_pothole: true,
    looks_like_speed_breaker: false,
    image_quality: "usable",
    surface_type: "bituminous_asphalt",
    on_drivable_surface: true,
    has_localized_cavity: true,
    has_unambiguous_lower_interior: true,
    has_broken_edge_or_rim: true,
    has_depth_or_surface_loss: true,
    temporal_consistency: "single_view",
    size: "medium",
    description: "A localized cavity with material loss",
  }, false, 1);
  const route = {
    routed: true,
    authority_id: "ka-lgd-305852",
    authority_name: "Bengaluru South City Corporation",
    officer_name: "Commissioner, Bengaluru South City Corporation",
    routing_source: "Karnataka GIS municipal boundary",
    routing_match_field: "town_lgd_code",
    routing_match_value: "305852",
    handoff_name: "Namma Bengaluru (Sahaaya 2.0)",
    region: "karnataka",
    routing_pack_state_code: "KA",
    contract_state_code: "KA",
    tender_eligible: true,
    ...(override || {}),
  };
  const tender = {
    tender_number: "BBMP/2025-26/RD/WORK-42",
    title: "Resurfacing of 17th Main Road in HSR Layout",
    contractor: "ACME Roads Pvt Ltd",
    published: "01-02-2026",
    source_name: "Karnataka Public Procurement Portal (KPPP) snapshot",
    source_url: "https://kppp.karnataka.gov.in/",
    tender_pack_id: "in-ka-tenders",
    tender_pack_version: 1,
    tender_pack_sha256: "a".repeat(64),
    tender_pack_state_code: "KA",
  };
  const evidence = {
    captured_at: 1787625000,
    gps_accuracy: 8,
    photo_provenance: "Pothole Reporter camera evidence",
  };
  return {
    matched: P.buildComplaintOutputs(assessment, 12.912345, 77.612345,
      "17th Main Road, HSR Layout, Bengaluru", route.officer_name, tender, route, evidence),
    // The shared detector contract carries no surface_type, so most letters have it unknown.
    unknownSurface: P.buildComplaintOutputs({...assessment, surface_type: "unknown"},
      12.912345, 77.612345, "17th Main Road, HSR Layout, Bengaluru", route.officer_name,
      null, route, evidence),
    noCandidate: P.buildComplaintOutputs(assessment, 12.912345, 77.612345,
      "17th Main Road, HSR Layout, Bengaluru", route.officer_name, null, route, evidence),
    rejectedScope: P.normaliseTenderMatch({...tender,
      tender_number: "BBMP/2023-24/OW/WORK_INDENT2505",
      title: "Construction of drain and footpath"}, route),
  };
}
"""


def require(failures, condition, message):
    if not condition:
        failures.append(message)


def main():
    failures = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=["--disable-web-security"])
        page = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
        page.goto(os.environ.get("POTHOLE_TEST_APP", "http://localhost:8765/"))
        page.wait_for_load_state("networkidle")
        page.wait_for_function(
            "typeof StandaloneAPI !== 'undefined' && StandaloneAPI.__pure",
            timeout=30000,
        )
        result = page.evaluate(JS)
        # The same routed complaint in each Indian language. Only the greeting, subject
        # and sign-off used to follow the language; the body stayed English.
        # A letter is written in the recipient State's language, not the phone's: a
        # Marathi or Bengali phone used to send BSCC a Marathi or Bengali letter.
        home_routes = {
            "kn": None,
            "mr": {"authority_id": "mh-bmc", "authority_name": "Brihanmumbai Municipal Corporation",
                   "officer_name": "Municipal Commissioner, Brihanmumbai Municipal Corporation",
                   "routing_pack_state_code": "MH", "contract_state_code": "MH"},
            "bn": {"authority_id": "wb-kmc", "authority_name": "Kolkata Municipal Corporation",
                   "officer_name": "Municipal Commissioner, Kolkata Municipal Corporation",
                   "routing_pack_state_code": "WB", "contract_state_code": "WB"},
        }
        localised = {}
        karnataka = {}
        for lang in ("en", "kn", "mr", "bn"):
            page.evaluate("(lang) => localStorage.setItem('app_lang', lang)", lang)
            karnataka[lang] = page.evaluate(JS)["noCandidate"]
            if lang != "en":
                localised[lang] = page.evaluate(JS, home_routes[lang])["noCandidate"]["email_body"]
        page.evaluate("localStorage.removeItem('app_lang')")
        browser.close()

    matched = result["matched"]
    body = matched["email_body"]
    no_candidate = result["noCandidate"]["email_body"]

    print("  generated structured complaint:")
    for line in body.splitlines():
        if line.strip():
            print(f"    | {line[:112]}")

    require(failures, matched["email_subject"] == "Pothole complaint: 17th Main Road",
            "subject is not the concise road-specific subject")
    # Complaints go out under the tester's name, and the house rule bars these dashes.
    for label, text in [("subject", matched["email_subject"]), ("en body", body),
                        *[(f"{lang} body", text) for lang, text in localised.items()]]:
        require(failures, not any(ch in text for ch in "\u2013\u2014"),
                f"complaint {label} contains an em or en dash")
    # The four headed sections stated a body, a ward, an owner and a contract status as
    # findings. The letter has no sections now and none of those lines.
    for gone in ("LOCATION", "CLASSIFICATION", "ROUTING", "CONTRACT VERIFICATION",
                 "Address / landmark:", "Defect decision:", "Surface:", "App visual size class:",
                 "Measurement provenance:", "Geographic corporation/body:",
                 "Complaint intake authority:", "Suggested portal category:", "Suggested ward:",
                 "Road owner/maintainer:", "Status:", "Not recorded"):
        require(failures, gone not in body, f'email still carries "{gone}"')
    for expected in (
        "Location: 17th Main Road, HSR Layout, Bengaluru",
        "Coordinates: 12.912345, 77.612345 (GPS accuracy ±8 m)",
        "Map: https://maps.google.com/?q=12.912345,77.612345",
        # The officer works in IST; a UTC ISO stamp read 13:48 for a 19:18 capture.
        "Photographed: 25 Aug 2026, 8:00:00 am IST",
        "Size: medium (visual estimate)",
        "I would appreciate if you could repair the pothole and share the complaint number.",
    ):
        require(failures, expected in body, f'missing or altered email field: "{expected}"')
    # Internal ids and lines that read the same on every report tell the officer nothing.
    unknown_surface = result["unknownSurface"]
    for label, text in (("email", body), ("whatsapp", matched["whatsapp_text"]),
                        ("portal copy", matched["portal_copy_text"]),
                        ("unknown-surface email", unknown_surface["email_body"])):
        for noise in ("Intake profile", "intake profile", "lgd=", "town_lgd_code",
                      "Routing basis", "routing basis", "basis Karnataka GIS",
                      "Physical dimensions", "Measurement confidence", "T02:30:00"):
            require(failures, noise not in text, f'{label} carries internal noise "{noise}"')
    require(failures, "Surface:" not in unknown_surface["email_body"],
            "an unknown surface still prints a Surface line")
    # The candidate tender is named once, as a question put to the office, and nowhere
    # as a finding. Where the record came from is the app's business, not the letter's.
    question = ("Please verify that this road is maintained by your office and if this "
                "location is covered by - BBMP/2025-26/RD/WORK-42: Resurfacing of 17th Main "
                "Road in HSR Layout. Contractor listed: ACME Roads Pvt Ltd. Published 01-02-2026.")
    require(failures, question in body.split("\n\n"),
            "the candidate tender is not put to the office as one question")
    for named in ("BBMP/2025-26/RD/WORK-42", "ACME Roads Pvt Ltd"):
        require(failures, body.count(named) == 1,
                f'the email names "{named}" outside the one question')
    for claim in ("Verified", "verified", "DLP", "Karnataka Public Procurement Portal (KPPP) snapshot",
                  "Award/work-order", "Road-segment match"):
        require(failures, claim not in body and claim not in matched["whatsapp_text"],
                f'outbound copy states a finding about the contract: "{claim}"')

    # The footer asked the officer to verify a suggested authority, ward, owner and
    # tender. The letter suggests none of them any more and ends at the sender's name.
    require(failures, FOOTER not in body and FOOTER not in matched["whatsapp_text"],
            "the independent-app disclaimer is still appended")
    require(failures, body.split("\n\n")[-1].startswith("Regards,\n"),
            "the email does not end at the sign-off")
    for forbidden in (
        "within the defect liability period",
        "within maintenance period",
        "official size",
        "official category",
        "does not submit a grievance",
        "no official grievance submission is confirmed",
    ):
        require(failures, forbidden not in body.lower(),
                f'email retains an unsupported or noisy claim: "{forbidden}"')

    require(failures,
            "Please verify that this road is maintained by your office." in no_candidate.split("\n\n"),
            "with no candidate the email does not ask the one plain question")
    require(failures, "No verified exact-road" not in no_candidate and "covered by" not in no_candidate,
            "with no candidate the email still reports on a contract search")
    require(failures, "BBMP/2025-26/RD/WORK-42" not in no_candidate,
            "no-candidate email leaked a tender from another render")
    require(failures, result["rejectedScope"] is None,
            "drain-and-footpath-only WORK_INDENT2505 was accepted as road work")

    labels = {
        "kn": ("ಸ್ಥಳ: ", "ನಿರ್ದೇಶಾಂಕಗಳು: ", "ನಕ್ಷೆ: ", "ಗಾತ್ರ: "),
        "mr": ("ठिकाण: ", "निर्देशांक: ", "नकाशा: ", "आकार: "),
        "bn": ("স্থান: ", "স্থানাঙ্ক: ", "মানচিত্র: ", "আকার: "),
    }
    gone_headings = ("ಹಾನಿಯ ವಿವರ", "ಜವಾಬ್ದಾರ ಕಚೇರಿ", "ಗುತ್ತಿಗೆ ಮಾಹಿತಿ", "नुकसानाचा तपशील",
                     "जबाबदार कार्यालय", "कंत्राट माहिती", "ক্ষতির বিবরণ", "দায়িত্বপ্রাপ্ত দপ্তর",
                     "ঠিকাদারি তথ্য")
    for lang, localised_body in localised.items():
        for english in ("Please register", "Please verify", "I wish you", "Thank you",
                        "Location:", "Coordinates:", "GPS accuracy", "Photographed", "Size:",
                        "LOCATION", "CLASSIFICATION", "ROUTING", "CONTRACT VERIFICATION",
                        "Road owner/maintainer", "Suggested ward", "No verified exact-road"):
            require(failures, english not in localised_body,
                    f"{lang} complaint body still has the English {english!r}")
        for label in labels[lang]:
            require(failures, label in localised_body,
                    f"{lang} complaint body is missing the line {label!r}")
        for heading in gone_headings:
            require(failures, heading not in localised_body,
                    f"{lang} complaint body still has the section {heading!r}")
        require(failures, "12.912345, 77.612345" in localised_body,
                f"{lang} complaint body lost the exact coordinates")
    english = karnataka["en"]
    english_greeting = english["email_body"].split("\n", 1)[0]
    for lang in ("mr", "bn"):
        letter = karnataka[lang]
        require(failures, letter["email_subject"] == english["email_subject"]
                and letter["email_body"].split("\n", 1)[0] == english_greeting,
                f"a {lang} phone writes BSCC a non-English letter: "
                f"{letter['email_subject']!r}, {letter['email_body'].splitlines()[0]!r}")
    require(failures, karnataka["kn"]["email_subject"].startswith("ರಸ್ತೆ ಗುಂಡಿ ದೂರು"),
            "a Kannada phone no longer writes BSCC in Kannada")
    require(failures, "आपला/आपली" not in localised.get("mr", ""),
            "Marathi sign-off is the form-style आपला/आपली")

    print()
    if failures:
        print("FAIL")
        for failure in failures:
            print("  -", failure)
        sys.exit(1)
    print("LETTER TEST PASS")


if __name__ == "__main__":
    main()
