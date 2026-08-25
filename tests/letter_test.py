# -*- coding: utf-8 -*-
"""The letter must be true for whoever receives it.

164 of the 182 addressable bodies are municipal councils or town panchayats, headed by a
Chief Officer. Calling them "the city corporation" is wrong for nine recipients in ten, and
a letter that gets the reader's own office wrong is easy to dismiss.
"""
import sys, pathlib
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
fails = []

EN_FOOTER = (
    "Pothole Reporter is an independent app. Please verify any suggested authority, "
    "ward, road ownership, and tender details."
)
KN_FOOTER = (
    "Pothole Reporter ಒಂದು ಸ್ವತಂತ್ರ ಆ್ಯಪ್. ಸೂಚಿಸಲಾದ ಸಂಸ್ಥೆ, ವಾರ್ಡ್, ರಸ್ತೆ ಮಾಲೀಕತ್ವ "
    "ಮತ್ತು ಯಾವುದೇ ಟೆಂಡರ್ ವಿವರಗಳನ್ನು ದಯವಿಟ್ಟು ಪರಿಶೀಲಿಸಿ."
)

JS = r"""
(() => {
  const P = StandaloneAPI.__pure;
  const a = { is_pothole: true, size: "medium", confidence: 0.8, description: "d" };
  const unsignedTender = { tender_number: "DMA/1", contractor: "ACME", title: "Road work",
                           published: "01-02-2026", warranty: "within the defect liability period",
                           warranty_code: "dlp" };
  const tender = {
    ...unsignedTender, tender_pack_id: "in-ka-tenders", tender_pack_version: 1,
    tender_pack_sha256: "a".repeat(64), tender_pack_state_code: "KA",
  };
  const eligibleRoute = {tender_eligible: true, ownership_unverified: false};
  const out = {};
  out.tenderGate = {
    signed: !!P.normaliseTenderMatch(tender, eligibleRoute),
    missingProvenance: P.normaliseTenderMatch(unsignedTender, eligibleRoute) === null,
    missingRoute: P.normaliseTenderMatch(tender) === null,
    nonRoadScope: P.normaliseTenderMatch({...tender, title: "Construction of drain and footpath"},
                                         eligibleRoute) === null,
  };
  const [, councilBody] = P.draftEmail(a, 12.9, 77.6, "Main Road, Channagiri, 577213",
                                       "Chief Officer, Channagiri", tender, eligibleRoute);
  const [, corpBody] = P.draftEmail(a, 12.9, 77.6, "17th Main, HSR Layout, Bengaluru",
                                    "Commissioner, Bengaluru South City Corporation", tender,
                                    eligibleRoute);
  out.council = councilBody;
  out.corporation = corpBody;
  // and the no-contract case, which most complaints are
  const [, noTender] = P.draftEmail(a, 12.9, 77.6, "Main Road, Channagiri, 577213",
                                    "Chief Officer, Channagiri", null);
  out.noTender = noTender;
  const [, ineligibleTender] = P.draftEmail(a, 12.9, 77.6,
    "Main Road, Channagiri, 577213", "Chief Officer, Channagiri", tender,
    {tender_eligible: false, ownership_unverified: false});
  out.ineligibleTender = ineligibleTender;
  out.types = {};
  for (const type of ["pothole_cavity", "failed_patch", "surface_breakup", "rut_or_depression"]) {
    const [subject, body] = P.draftEmail({ damage_type:type, size:"medium", description:"d" },
      12.9, 77.6, "Main Road, Channagiri, 577213", "Chief Officer, Channagiri", null);
    out.types[type] = {subject, body};
  }
  localStorage.setItem("app_lang", "kn");
  const [, kannadaBody] = P.draftEmail(a, 12.9, 77.6, "Main Road, Channagiri, 577213",
                                       "Chief Officer, Channagiri", null);
  out.kannada = kannadaBody;
  localStorage.setItem("app_lang", "en");
  return out;
})()
"""

with sync_playwright() as p:
    b = p.chromium.launch(args=["--disable-web-security"])
    pg = b.new_context(viewport={"width": 390, "height": 844}).new_page()
    pg.goto("http://localhost:8765/"); pg.wait_for_load_state("networkidle")
    pg.wait_for_function("typeof StandaloneAPI !== 'undefined' && StandaloneAPI.__pure", timeout=30000)
    r = pg.evaluate(JS)
    b.close()

council = r["council"]
print("  letter to a Chief Officer of a town municipal council:")
for line in council.split("\n"):
    if line.strip(): print(f"    | {line[:104]}")

if r["tenderGate"] != {
    "signed": True,
    "missingProvenance": True,
    "missingRoute": True,
    "nonRoadScope": True,
}:
    fails.append(f"tender validation gate accepted incomplete provenance: {r['tenderGate']}")

for phrase in ("city corporation", "the city"):
    if phrase in council:
        fails.append(f'a letter to a Chief Officer says "{phrase}", but that body is not a corporation')
if "Chief Officer, Channagiri" not in council:
    fails.append("the greeting does not address the routed officer")
if "probable" not in council.lower() and "may still be" not in council.lower():
    fails.append("the contract claim lost its hedge")
if "ACME" not in council:
    fails.append("the contractor is not named when one is recorded")
if "DMA/1" not in council:
    fails.append("the validated tender number is missing from the letter")
if "Work name: Road work" not in council:
    fails.append("the validated tender title is missing from the letter")

for label, body, footer in (
    ("matched tender", council, EN_FOOTER),
    ("corporation", r["corporation"], EN_FOOTER),
    ("no tender", r["noTender"], EN_FOOTER),
    ("ineligible tender", r["ineligibleTender"], EN_FOOTER),
    ("Kannada", r["kannada"], KN_FOOTER),
):
    paragraphs = [paragraph.strip() for paragraph in body.strip().split("\n\n")]
    if body.count(footer) != 1:
        fails.append(f"{label} letter does not contain exactly one independent-app footer")
    if not paragraphs or paragraphs[-1] != footer:
        fails.append(f"{label} letter does not end with the independent-app verification footer")

legacy_submission_phrases = (
    "does not submit a grievance",
    "does not submit the grievance",
    "no official grievance submission is confirmed",
    "ದೂರು ಸಲ್ಲಿಸುವುದಿಲ್ಲ",
)
for label, body in (
    ("matched tender", council),
    ("corporation", r["corporation"]),
    ("no tender", r["noTender"]),
    ("ineligible tender", r["ineligibleTender"]),
    ("Kannada", r["kannada"]),
):
    lowered = body.lower()
    for phrase in legacy_submission_phrases:
        if phrase.lower() in lowered:
            fails.append(f'{label} letter retains old submission wording: "{phrase}"')

# The footer always tells the reader to verify any tender details. Ignore that generic
# warning when checking that a report with no match contains no actual contract claim.
no_tender_content = r["noTender"].rsplit("\n\n", 1)[0].lower()
ineligible_tender_content = r["ineligibleTender"].rsplit("\n\n", 1)[0].lower()
for phrase in (
    "probable tender match", "tender number:", "work name:", "contractor:",
    "warranty status:", "winning bidder", "defect liability", "maintenance period",
    "DMA/1", "Road work", "ACME",
):
    if phrase.lower() in no_tender_content:
        fails.append(f'a report with no tender match still contains "{phrase}"')
    if phrase.lower() in ineligible_tender_content:
        fails.append(f'an ineligible route still contains tender detail "{phrase}"')

types = r["types"]
if "pothole" not in types["pothole_cavity"]["subject"].lower():
    fails.append("a cavity complaint is no longer named as a pothole")
for kind in ("failed_patch", "surface_breakup", "rut_or_depression"):
    # The branded footer necessarily says "Pothole Reporter"; classify the complaint
    # using its subject and substantive body, not the app name in that final footer.
    substantive_body = types[kind]["body"].rsplit("\n\n", 1)[0]
    combined = types[kind]["subject"] + " " + substantive_body
    if "pothole" in combined.lower():
        fails.append(f"{kind} is falsely described as a pothole")
if "Broken road repair" not in types["failed_patch"]["subject"]:
    fails.append("failed-patch subject does not distinguish a broken repair")
if "Road surface failure" not in types["surface_breakup"]["subject"]:
    fails.append("surface-breakup subject is not distinct")
if "Road depression" not in types["rut_or_depression"]["subject"]:
    fails.append("rut/depression subject is not distinct")

print()
if fails:
    print("FAIL"); [print("  -", f) for f in fails]; sys.exit(1)
print("LETTER TEST PASS")
