# -*- coding: utf-8 -*-
"""Legacy generated complaint copy is cleaned without rewriting user-authored text."""
import functools
import http.server
import pathlib
import sys
import threading

from playwright.sync_api import sync_playwright


ROOT = pathlib.Path(__file__).resolve().parent.parent
FOOTER = (
    "Pothole Reporter is an independent app. Please verify any suggested authority, "
    "ward, road ownership, and tender details."
)

KANJUR_BODY = """Dear BMC Pothole QuickFix, Brihanmumbai Municipal Corporation,

I would like to report a pothole that needs repair.

Location: Kanjur Village Road, Kanjur West, Mumbai, 400042
Coordinates: 19.129443, 72.932773
Map link: https://maps.google.com/?q=19.129443,72.932773
Damage type: pothole
Approximate size: medium

PFA image. This road damage poses a danger to two wheeler riders and other road users. I request your office to inspect and repair it at the earliest, and to route it to the contractor responsible if this road section is still under a maintenance warranty.

Suggested BMC administrative ward: S. This is inferred from an OpenStreetMap administrative boundary; verify it in the official BMC app.

Suggested civic authority: Brihanmumbai Municipal Corporation. This does not prove who owns this road. This independent app does not submit a grievance; review the evidence and finish it yourself in BMC Pothole QuickFix.

Thank you for your service.

Regards,
Ashis Sen

Captured (IST): 25 Aug 2026, 12:23:10 pm
Photo provenance: taken through the app camera.
GPS accuracy: ±8 m
Suggested civic authority: Brihanmumbai Municipal Corporation (verify road ownership)
Suggested BMC ward: S (verify in the official app)
Local event ID: 2
Prepared by an independent app; no official grievance submission is confirmed."""

KARNATAKA_BODY = """Dear Chief Commissioner, Bruhat Bengaluru Mahanagara Palike,

I would like to report a pothole that needs repair.

Location: 17th Main Road, HSR Layout, Bengaluru, Karnataka, 560102
Coordinates: 12.912100, 77.644600
Map link: https://maps.google.com/?q=12.912100,77.644600
Damage type: pothole
Approximate size: medium

PFA image. This road damage poses a danger to two wheeler riders and other road users. I request your office to inspect and repair it at the earliest, and to route it to the contractor responsible if this road section is still under a maintenance warranty.

Public procurement records indicate this road stretch probably falls under tender BBMP/2025-26/OW/WORK_INDENT9001 ("Annual maintenance of carriageway roads in Ward 150"), published on 01-04-2025, with Example Roads Pvt Ltd recorded as the winning bidder, and it may still be within the defect liability period.

If the defect liability or maintenance period is in force, I request that the repair be carried out by the contractor at no additional cost to the corporation. This is a probable record match; kindly verify against the tender documents.

Thank you for your service.

Regards,
A concerned citizen"""

BENGALI_MMR_BODY = """মাননীয় Aaple Sarkar, Maharashtra authority (select in Aaple Sarkar),

রাস্তার গর্ত মেরামতের জন্য এই অভিযোগ জানাচ্ছি।

স্থান: উদাহরণ সড়ক, মুম্বাই
স্থানাঙ্ক: 19.100000, 72.900000
মানচিত্রের লিঙ্ক: https://maps.google.com/?q=19.100000,72.900000
ক্ষতির ধরন: রাস্তার গর্ত
আনুমানিক আকার: মাঝারি

ছবি সংযুক্ত করা হল। রাস্তার এই ক্ষতি বিশেষ করে দু’চাকার যানচালক ও অন্যান্য পথ ব্যবহারকারীর জন্য বিপজ্জনক। অনুগ্রহ করে দ্রুত স্থানটি পরিদর্শন করে মেরামতের ব্যবস্থা করুন।

অবস্থানের ভিত্তিতে প্রস্তাবিত পৌর কর্তৃপক্ষ: Maharashtra authority (select in Aaple Sarkar)। এতে রাস্তার মালিকানা প্রমাণিত হয় না। এই স্বাধীন অ্যাপটি অভিযোগ জমা দেয় না; প্রমাণ যাচাই করে Aaple Sarkar-এ নিজে অভিযোগ নথিভুক্ত করুন এবং অভিযোগ নম্বরটি সংরক্ষণ করুন।

ধন্যবাদ।

বিনীত,
একজন সচেতন নাগরিক"""


SCENARIO = """
async ({kanjurBody, karnatakaBody, bengaliMmrBody}) => {
  const migrate = StandaloneAPI.__pure.migrateLegacyComplaintRecord;
  if (typeof migrate !== "function") throw new Error("migrateLegacyComplaintRecord is not exported");

  const kanjur = {
    id: 2,
    status: "draft",
    issue_type: "road_damage",
    delivery_channel: "bmc_quickfix",
    officer_name: "BMC Pothole QuickFix, Brihanmumbai Municipal Corporation",
    authority_id: "mh-bmc",
    authority_name: "Brihanmumbai Municipal Corporation",
    handoff_name: "BMC Pothole QuickFix",
    ward_code: "S",
    ownership_unverified: true,
    email_body: kanjurBody,
  };
  const migratedKanjur = await Promise.resolve(migrate(kanjur));

  const customLegacy = {
    ...kanjur,
    id: 6,
    email_body: kanjurBody.replace(
      "\\n\\nThank you for your service.",
      "\\n\\nCustom note from the reporter: the cavity is beside the lamp post."
        + "\\n\\nThank you for your service.",
    ),
  };
  const migratedCustomLegacy = await Promise.resolve(migrate(customLegacy));
  const inlineCustom = {
    ...kanjur,
    id: 7,
    email_body: kanjurBody.replace(
      "Prepared by an independent app; no official grievance submission is confirmed.",
      "Reporter note: beside the lamp post.\\n"
        + "Prepared by an independent app; no official grievance submission is confirmed.",
    ),
  };
  const migratedInlineCustom = await Promise.resolve(migrate(inlineCustom));
  const mixedScript = {
    ...kanjur,
    id: 8,
    email_body: kanjurBody.replace("Kanjur Village Road", "कांजूर Village Road"),
  };
  const migratedMixedScript = await Promise.resolve(migrate(mixedScript));
  const reorderedFooter = StandaloneAPI.__pure.complaintBodyWithFooter(
    migratedKanjur.email_body + "\\n\\nReporter-added final note.", "road_damage",
  );

  const sent = {...kanjur, id: 3, status: "sent"};
  const sentBefore = JSON.parse(JSON.stringify(sent));
  const migratedSent = await Promise.resolve(migrate(sent));

  const custom = {
    ...kanjur,
    id: 4,
    email_body: "Dear BMC,\\n\\nPlease repair the pothole. This is my own draft.",
  };
  const customBefore = JSON.parse(JSON.stringify(custom));
  const migratedCustom = await Promise.resolve(migrate(custom));

  const karnataka = {
    id: 5,
    status: "draft",
    issue_type: "road_damage",
    delivery_channel: "email",
    officer_name: "Chief Commissioner, Bruhat Bengaluru Mahanagara Palike",
    authority_id: "ka-bbmp",
    authority_name: "Bruhat Bengaluru Mahanagara Palike",
    tender_number: "BBMP/2025-26/OW/WORK_INDENT9001",
    tender_title: "Annual maintenance of carriageway roads in Ward 150",
    email_body: karnatakaBody,
  };
  const migratedKarnataka = await Promise.resolve(migrate(karnataka));
  const bengaliMmr = {
    id: 9,
    status: "draft",
    issue_type: "road_damage",
    delivery_channel: "official_handoff",
    officer_name: "Aaple Sarkar, Maharashtra authority (select in Aaple Sarkar)",
    authority_id: "mh-statewide-unverified",
    authority_name: "Maharashtra authority (select in Aaple Sarkar)",
    handoff_name: "Aaple Sarkar",
    ownership_unverified: true,
    email_body: bengaliMmrBody,
  };
  const migratedBengaliMmr = await Promise.resolve(migrate(bengaliMmr));

  return {kanjur, migratedKanjur, customLegacy, migratedCustomLegacy,
          migratedInlineCustom, migratedMixedScript, reorderedFooter,
          sentBefore, migratedSent, customBefore, migratedCustom,
          karnataka, migratedKarnataka, migratedBengaliMmr};
}
"""


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, _format, *_args):
        pass


handler = functools.partial(QuietHandler, directory=str(ROOT / "static"))
server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()

fails = []
try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=["--disable-web-security"])
        page = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
        page.goto(f"http://127.0.0.1:{server.server_port}/")
        page.wait_for_load_state("networkidle")
        page.wait_for_function(
            "typeof StandaloneAPI !== 'undefined' && StandaloneAPI.__pure",
            timeout=30000,
        )
        result = page.evaluate(
            SCENARIO,
            {"kanjurBody": KANJUR_BODY, "karnatakaBody": KARNATAKA_BODY,
             "bengaliMmrBody": BENGALI_MMR_BODY},
        )
        browser.close()
finally:
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


kanjur = result["migratedKanjur"]
kanjur_body = kanjur["email_body"]
if not kanjur_body.startswith("Dear Brihanmumbai Municipal Corporation,"):
    fails.append("legacy BMC handoff is not addressed directly to the BMC authority")
concise_request = (
    "PFA image. This road damage poses a danger to two-wheeler riders and other road "
    "users. I request your office to inspect and repair it at the earliest."
)
if concise_request not in kanjur_body:
    fails.append("legacy BMC request was not reduced to the concise repair request")
for obsolete in (
    "does not submit a grievance",
    "no official grievance submission is confirmed",
    "does not prove who owns this road",
    "inferred from an OpenStreetMap administrative boundary",
    "verify it in the official BMC app",
    "verify road ownership",
):
    if obsolete.lower() in kanjur_body.lower():
        fails.append(f'legacy BMC copy retains obsolete wording: "{obsolete}"')
if kanjur_body.count("Suggested BMC administrative ward: S.") != 1:
    fails.append("legacy BMC copy does not contain exactly one concise ward line")
custom_note = "Custom note from the reporter: the cavity is beside the lamp post."
if custom_note not in result["migratedCustomLegacy"]["email_body"]:
    fails.append("migration deleted an arbitrary reporter-authored paragraph")
if "Reporter note: beside the lamp post." not in result["migratedInlineCustom"]["email_body"]:
    fails.append("migration deleted reporter text sharing a paragraph with legacy evidence")
if not result["migratedMixedScript"]["email_body"].startswith(
    "Dear Brihanmumbai Municipal Corporation,"
):
    fails.append("a local-script address changed the English template language")
if kanjur_body.count(FOOTER) != 1:
    fails.append("legacy BMC copy does not contain exactly one independent-app footer")
if kanjur_body.strip().split("\n\n")[-1] != FOOTER:
    fails.append("legacy BMC copy does not end with the independent-app footer")
if result["reorderedFooter"].count(FOOTER) != 1:
    fails.append("edited complaint does not retain exactly one footer")
if result["reorderedFooter"].strip().split("\n\n")[-1] != FOOTER:
    fails.append("edited complaint footer was not restored to the final paragraph")
if "Reporter-added final note." not in result["reorderedFooter"]:
    fails.append("restoring the edited complaint footer deleted reporter text")
if kanjur["id"] != 2 or kanjur["status"] != "draft":
    fails.append("migration changed record identity or status")

if result["migratedSent"] != result["sentBefore"]:
    fails.append("a sent legacy complaint was modified")
if result["migratedCustom"] != result["customBefore"]:
    fails.append("a custom draft without a known legacy marker was modified")

karnataka_body = result["migratedKarnataka"]["email_body"]
for leaked in (
    "BBMP/2025-26/OW/WORK_INDENT9001",
    "Annual maintenance of carriageway roads in Ward 150",
    "Example Roads Pvt Ltd",
    "defect liability or maintenance period",
):
    if leaked.lower() in karnataka_body.lower():
        fails.append(f'legacy unverified contract attribution survived migration: "{leaked}"')
if "No verified exact-road public contract found" not in karnataka_body:
    fails.append("legacy contractor allegation was not replaced by a fail-closed status")
if "This is a probable record match; kindly verify against the tender documents." in karnataka_body:
    fails.append("legacy tender-specific verification suffix was not removed")
if karnataka_body.count(FOOTER) != 1:
    fails.append("legacy Karnataka copy does not contain exactly one independent-app footer")
if karnataka_body.strip().split("\n\n")[-1] != FOOTER:
    fails.append("legacy Karnataka copy does not end with the independent-app footer")

bengali_body = result["migratedBengaliMmr"]["email_body"]
if "এই স্বাধীন অ্যাপটি অভিযোগ জমা দেয় না" in bengali_body:
    fails.append("legacy Bengali authority disclaimer was not removed")
if "(select in Aaple Sarkar)" in bengali_body:
    fails.append("legacy Bengali authority label retained its selection instruction")
bengali_footer = (
    "Pothole Reporter একটি স্বাধীন অ্যাপ। প্রস্তাবিত কর্তৃপক্ষ, ওয়ার্ড, রাস্তার "
    "মালিকানা এবং টেন্ডারের তথ্য অনুগ্রহ করে যাচাই করুন।"
)
if bengali_body.strip().split("\n\n")[-1] != bengali_footer:
    fails.append("legacy Bengali MMR copy does not end with its localized footer")

if fails:
    print("LEGACY COMPLAINT COPY TEST FAIL")
    for failure in fails:
        print("  -", failure)
    sys.exit(1)
print("LEGACY COMPLAINT COPY TEST PASS")
