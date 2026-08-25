# -*- coding: utf-8 -*-
"""What the app tells the user must be true in every supported language and render.

Two bugs this guards against, both of which shipped once:
  - HTML entities inside strings applied with textContent, which render literally.
  - Translated strings drifting behind the English ones and describing an older build.
"""
import re, sys, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
fails = []

for name in ("static/index.html", "android-app/www/index.html", "docs/index.html"):
    s = (ROOT / name).read_text(encoding="utf-8")

    # The two mirrors must be byte-identical; a partial patch is how the recording
    # toggle silently went missing once.
    if name.startswith("android"):
        if s != (ROOT / "static/index.html").read_text(encoding="utf-8"):
            fails.append("android-app/www/index.html has drifted from static/index.html")
    if name.startswith("docs"):
        if s != (ROOT / "static/index.html").read_text(encoding="utf-8"):
            fails.append("docs/index.html has drifted from static/index.html")

    # Disclosure: the user must be told the photo leaves the device in every language.
    en = re.search(r'settings_note: "([^"]+)"', s)
    if not en or "OpenAI" not in en.group(1):
        fails.append(f"{name}: English settings note does not say the photo goes to OpenAI")
    notes = re.findall(r'settings_note: "([^"]+)"', s)
    if len(notes) != 4:
        fails.append(f"{name}: expected 4 settings_note strings, found {len(notes)}")
    else:
        for language, note in zip(("English", "Kannada", "Marathi", "Bengali"), notes):
            if "OpenAI" not in note:
                fails.append(f"{name}: {language} settings note does not mention OpenAI")
            if "GitHub Pages" not in note:
                fails.append(f"{name}: {language} settings note does not disclose the pack host")
            if "2°" not in note:
                fails.append(f"{name}: {language} settings note omits highway-tile granularity")

    # Scope: localized refusals must describe all supported geographies.
    coverage = re.findall(r'^\s{4}outside_coverage_help: "([^"]+)"', s, re.MULTILINE)
    if len(coverage) == 4:
        if "ಬೆಂಗಳೂರಿಗೆ" in coverage[1] or "ಜಿಬಿಎ" in coverage[1]:
            fails.append(f"{name}: Kannada out-of-coverage text still says Bengaluru only")
        if any(term not in coverage[0] for term in (
            "National Highways", "across India",
            "Maharashtra", "West Bengal", "Punjab", "Karnataka", "Kerala", "Mahe",
            "Tamil Nadu", "Andhra Pradesh", "Yanam", "Telangana",
            "Uttar Pradesh", "Chhattisgarh", "Census top-50",
            "Delhi NCT",
        )):
            fails.append(f"{name}: English out-of-coverage text omits a supported region")
        if any(term not in coverage[1] for term in ("ಭಾರತದೆಲ್ಲೆಡೆ", "ರಾಷ್ಟ್ರೀಯ ಹೆದ್ದಾರಿಗಳು", "ಸಂಪೂರ್ಣ ಮಹಾರಾಷ್ಟ್ರ", "ಪಶ್ಚಿಮ ಬಂಗಾಳ", "ಪಂಜಾಬ್", "ಕರ್ನಾಟಕ", "ಕೇರಳ", "ಮಾಹೆ", "ತಮಿಳುನಾಡು", "ಆಂಧ್ರ ಪ್ರದೇಶ", "ಯಾನಂ", "ತೆಲಂಗಾಣ", "ಉತ್ತರ ಪ್ರದೇಶ", "ಛತ್ತೀಸ್‌ಗಢ", "ಟಾಪ್-50", "ದೆಹಲಿ NCT")):
            fails.append(f"{name}: Kannada out-of-coverage text omits a supported region")
        if any(term not in coverage[2] for term in ("भारतभर", "राष्ट्रीय महामार्ग", "संपूर्ण महाराष्ट्र", "पश्चिम बंगाल", "पंजाब", "कर्नाटक", "केरळ", "माहे", "तामिळनाडू", "आंध्र प्रदेश", "यानम", "तेलंगणा", "उत्तर प्रदेश", "छत्तीसगड", "टॉप-50", "दिल्ली NCT")):
            fails.append(f"{name}: Marathi out-of-coverage text omits a supported region")
        if any(term not in coverage[3] for term in ("ভারতজুড়ে", "জাতীয় সড়ক", "সমগ্র মহারাষ্ট্র", "পশ্চিমবঙ্গ", "পাঞ্জাব", "কর্ণাটক", "কেরল", "মাহে", "তামিলনাড়ু", "অন্ধ্রপ্রদেশ", "ইয়ানাম", "তেলেঙ্গানা", "উত্তরপ্রদেশ", "ছত্তিশগড়", "শীর্ষ-৫০", "দিল্লি NCT")):
            fails.append(f"{name}: Bengali out-of-coverage text omits a supported region")
    else:
        fails.append(f"{name}: expected 4 outside_coverage_help strings, found {len(coverage)}")

    # Mumbai handoff copy must never turn opening another app into a submission claim.
    queued_bmc = re.findall(r'chip_queued_bmc: "([^"]+)"', s)
    if len(queued_bmc) != 4:
        fails.append(f"{name}: expected 4 chip_queued_bmc strings, found {len(queued_bmc)}")
    elif "handoff" not in queued_bmc[0].lower() or re.search(r"submitted|sent", queued_bmc[0], re.I):
        fails.append(f"{name}: English BMC queued chip does not truthfully describe a handoff")

    queued_official = re.findall(r'chip_queued_official: "([^"]+)"', s)
    if len(queued_official) != 4:
        fails.append(f"{name}: expected 4 generic official-handoff chips, found {len(queued_official)}")
    elif "handoff" not in queued_official[0].lower() or re.search(r"submitted|sent", queued_official[0], re.I):
        fails.append(f"{name}: generic queued chip does not truthfully describe a handoff")

    reported = re.findall(r'stat_reported: "([^"]+)"', s)
    if len(reported) != 4:
        fails.append(f"{name}: expected 4 stat_reported strings, found {len(reported)}")
    elif "confirmed submissions" not in reported[0].lower():
        fails.append(f"{name}: dashboard metric does not distinguish confirmed submissions")

    disclaimers = re.findall(r'bmc_disclaimer: "([^"]+)"', s)
    if len(disclaimers) != 4:
        fails.append(f"{name}: expected 4 BMC disclaimers, found {len(disclaimers)}")
    else:
        if "does not submit" not in disclaimers[0] or "official grievance ID" not in disclaimers[0]:
            fails.append(f"{name}: English BMC disclaimer does not state the submission boundary")
        kn_disclaimer = disclaimers[1]
        if "BMC" not in kn_disclaimer or "ಸಲ್ಲಿಸುವುದಿಲ್ಲ" not in kn_disclaimer or "ಸಂಖ್ಯೆಯಿಲ್ಲದೆ" not in kn_disclaimer:
            fails.append(f"{name}: Kannada BMC disclaimer does not state the submission boundary")
        mr_disclaimer = disclaimers[2]
        if "BMC" not in mr_disclaimer or "दाखल करत नाही" not in mr_disclaimer or "क्रमांकाशिवाय" not in mr_disclaimer:
            fails.append(f"{name}: Marathi BMC disclaimer does not state the submission boundary")
        bn_disclaimer = disclaimers[3]
        if "BMC" not in bn_disclaimer or "জমা দেয় না" not in bn_disclaimer or "নম্বর ছাড়া" not in bn_disclaimer:
            fails.append(f"{name}: Bengali BMC disclaimer does not state the submission boundary")

    official_disclaimers = re.findall(r'official_disclaimer: "([^"]+)"', s)
    if len(official_disclaimers) != 4:
        fails.append(f"{name}: expected 4 generic official disclaimers, found {len(official_disclaimers)}")
    else:
        if "does not prove who owns this road" not in official_disclaimers[0] or "only prepares evidence" not in official_disclaimers[0]:
            fails.append(f"{name}: English generic disclaimer omits ownership or submission truth")
        if "ಮಾಲೀಕತ್ವ" not in official_disclaimers[1] or "ಸಾಕ್ಷ್ಯವನ್ನು ಮಾತ್ರ" not in official_disclaimers[1]:
            fails.append(f"{name}: Kannada generic disclaimer omits ownership or evidence-only truth")
        if "मालकी सिद्ध होत नाही" not in official_disclaimers[2] or "फक्त पुरावा" not in official_disclaimers[2]:
            fails.append(f"{name}: Marathi generic disclaimer omits ownership or evidence-only truth")
        if "মালিকানা প্রমাণিত হয় না" not in official_disclaimers[3] or "কেবল প্রমাণ" not in official_disclaimers[3]:
            fails.append(f"{name}: Bengali generic disclaimer omits ownership or evidence-only truth")

    authority_disclaimers = re.findall(r'authority_disclaimer: "([^"]+)"', s)
    if len(authority_disclaimers) != 4:
        fails.append(f"{name}: expected 4 suggested-email authority disclaimers, found {len(authority_disclaimers)}")
    elif "Road ownership is not verified" not in authority_disclaimers[0]:
        fails.append(f"{name}: email authority disclaimer does not qualify road ownership")

    suggested_email_confirms = re.findall(r'confirm_suggested_email: "([^"]+)"', s)
    if len(suggested_email_confirms) != 4:
        fails.append(f"{name}: expected 4 suggested-email confirmation strings, found {len(suggested_email_confirms)}")
    elif "does not prove road ownership" not in suggested_email_confirms[0]:
        fails.append(f"{name}: suggested-email confirmation does not repeat the ownership warning")

    whatsapp_confirms = re.findall(r'confirm_whatsapp_share: "([^"]+)"', s)
    if len(whatsapp_confirms) != 4:
        fails.append(f"{name}: expected 4 WhatsApp disclosure strings, found {len(whatsapp_confirms)}")
    elif "text and exact location" not in whatsapp_confirms[0] or "Nothing is sent until" not in whatsapp_confirms[0]:
        fails.append(f"{name}: WhatsApp confirmation omits shared data or the final-send boundary")

    if '<option value="mr">मराठी</option>' not in s:
        fails.append(f"{name}: Marathi is missing from the language selector")
    if '<option value="bn">বাংলা</option>' not in s:
        fails.append(f"{name}: Bengali is missing from the language selector")
    if not re.search(r'official_grievance_label: "[^"]*BMC[^"]*"', s):
        fails.append(f"{name}: official BMC grievance-ID label is missing")
    if not re.search(r'official_grievance_generic_label: "[^"]+"', s):
        fails.append(f"{name}: generic official grievance/reference label is missing")

    # Every refusal reason the engine can emit needs user-facing text.
    eng = (ROOT / "static/standalone.js").read_text(encoding="utf-8")
    reasons = set(re.findall(r'return \[null, null, "([a-z_]+)"', eng))
    for r in reasons:
        key = {"outside_area": "outside_coverage", "rural_road": "rural_road",
               "no_location": "no_location", "no_address_for_body": "no_address",
               "national_highway": "nat_highway", "road_class_unknown": "road_unknown",
               "jurisdiction_unavailable": "jurisdiction_unavailable",
               "location_uncertain": "location_uncertain"}.get(r)
        if key and f"{key}:" not in s:
            fails.append(f"{name}: refusal reason '{r}' has no UI string ({key})")

    # Entities are fine inside innerHTML, fatal inside textContent.
    for m in re.finditer(r'\$\("(\w+)"\)\.textContent = t\("(\w+)"\)', s):
        val = re.search(rf'\n    {m.group(2)}: "([^"]*)"', s)
        if val and re.search(r"&[a-z]+;|&#\d+;", val.group(1)):
            fails.append(f"{name}: {m.group(2)} holds an HTML entity but is set via textContent")

runtime = (ROOT / "static/standalone.js").read_text(encoding="utf-8")
road_outside_error = re.search(
    r'outside_area: "(This road damage is outside mapped National Highways[^\"]+)"',
    runtime,
)
if not road_outside_error or any(term not in road_outside_error.group(1) for term in (
    "Maharashtra", "West Bengal", "Punjab", "Karnataka", "Kerala", "Tamil Nadu",
    "Andhra Pradesh", "Telangana", "Uttar Pradesh", "Chhattisgarh", "Delhi NCT",
)):
    fails.append("standalone.js road out-of-coverage error omits a supported region")

if (ROOT / "android-app/www/standalone.js").read_bytes() != (ROOT / "static/standalone.js").read_bytes():
    fails.append("android-app/www/standalone.js has drifted from static/standalone.js")
if (ROOT / "docs/standalone.js").read_bytes() != (ROOT / "static/standalone.js").read_bytes():
    fails.append("docs/standalone.js has drifted from static/standalone.js")

if fails:
    print("FAIL"); [print("  -", f) for f in fails]; sys.exit(1)
print("UI TEXT TEST PASS")
