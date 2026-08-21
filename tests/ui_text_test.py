# -*- coding: utf-8 -*-
"""What the app tells the user must be true in every supported language and render.

Two bugs this guards against, both of which shipped once:
  - HTML entities inside strings applied with textContent, which render literally.
  - Translated strings drifting behind the English ones and describing an older build.
"""
import re, sys, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
fails = []

for name in ("static/index.html", "android-app/www/index.html"):
    s = (ROOT / name).read_text(encoding="utf-8")

    # The two mirrors must be byte-identical; a partial patch is how the recording
    # toggle silently went missing once.
    if name.startswith("android"):
        if s != (ROOT / "static/index.html").read_text(encoding="utf-8"):
            fails.append("android-app/www/index.html has drifted from static/index.html")

    # Disclosure: the user must be told the photo leaves the device in every language.
    en = re.search(r'settings_note: "([^"]+)"', s)
    if not en or "OpenAI" not in en.group(1):
        fails.append(f"{name}: English settings note does not say the photo goes to OpenAI")
    notes = re.findall(r'settings_note: "([^"]+)"', s)
    if len(notes) != 3:
        fails.append(f"{name}: expected 3 settings_note strings, found {len(notes)}")
    else:
        for language, note in zip(("English", "Kannada", "Marathi"), notes):
            if "OpenAI" not in note:
                fails.append(f"{name}: {language} settings note does not mention OpenAI")

    # Scope: localized refusals must describe both supported geographies.
    coverage = re.findall(r'outside_coverage_help: "([^"]+)"', s)
    if len(coverage) == 3:
        if "ಬೆಂಗಳೂರಿಗೆ" in coverage[1] or "ಜಿಬಿಎ" in coverage[1]:
            fails.append(f"{name}: Kannada out-of-coverage text still says Bengaluru only")
        if "ಕರ್ನಾಟಕ" not in coverage[1] or "ಮುಂಬೈ" not in coverage[1]:
            fails.append(f"{name}: Kannada out-of-coverage text does not name Karnataka and Mumbai")
        if "कर्नाटक" not in coverage[2] or "मुंबई" not in coverage[2]:
            fails.append(f"{name}: Marathi out-of-coverage text does not name Karnataka and Mumbai")
    else:
        fails.append(f"{name}: expected 3 outside_coverage_help strings, found {len(coverage)}")

    # Mumbai handoff copy must never turn opening another app into a submission claim.
    queued_bmc = re.findall(r'chip_queued_bmc: "([^"]+)"', s)
    if len(queued_bmc) != 3:
        fails.append(f"{name}: expected 3 chip_queued_bmc strings, found {len(queued_bmc)}")
    elif "handoff" not in queued_bmc[0].lower() or re.search(r"submitted|sent", queued_bmc[0], re.I):
        fails.append(f"{name}: English BMC queued chip does not truthfully describe a handoff")

    reported = re.findall(r'stat_reported: "([^"]+)"', s)
    if len(reported) != 3:
        fails.append(f"{name}: expected 3 stat_reported strings, found {len(reported)}")
    elif "confirmed submissions" not in reported[0].lower():
        fails.append(f"{name}: dashboard metric does not distinguish confirmed submissions")

    disclaimers = re.findall(r'bmc_disclaimer: "([^"]+)"', s)
    if len(disclaimers) != 3:
        fails.append(f"{name}: expected 3 BMC disclaimers, found {len(disclaimers)}")
    else:
        if "does not submit" not in disclaimers[0] or "official grievance ID" not in disclaimers[0]:
            fails.append(f"{name}: English BMC disclaimer does not state the submission boundary")
        kn_disclaimer = disclaimers[1]
        if "BMC" not in kn_disclaimer or "ಸಲ್ಲಿಸುವುದಿಲ್ಲ" not in kn_disclaimer or "ಸಂಖ್ಯೆಯಿಲ್ಲದೆ" not in kn_disclaimer:
            fails.append(f"{name}: Kannada BMC disclaimer does not state the submission boundary")
        mr_disclaimer = disclaimers[2]
        if "BMC" not in mr_disclaimer or "दाखल करत नाही" not in mr_disclaimer or "क्रमांकाशिवाय" not in mr_disclaimer:
            fails.append(f"{name}: Marathi BMC disclaimer does not state the submission boundary")

    if '<option value="mr">मराठी</option>' not in s:
        fails.append(f"{name}: Marathi is missing from the language selector")
    if not re.search(r'official_grievance_label: "[^"]*BMC[^"]*"', s):
        fails.append(f"{name}: official BMC grievance-ID label is missing")

    # Every refusal reason the engine can emit needs user-facing text.
    eng = (ROOT / "static/standalone.js").read_text(encoding="utf-8")
    reasons = set(re.findall(r'return \[null, null, "([a-z_]+)"', eng))
    for r in reasons:
        key = {"outside_area": "outside_coverage", "rural_road": "rural_road",
               "no_location": "no_location", "no_address_for_body": "no_address",
               "national_highway": "nat_highway", "road_class_unknown": "road_unknown"}.get(r)
        if key and f"{key}:" not in s:
            fails.append(f"{name}: refusal reason '{r}' has no UI string ({key})")

    # Entities are fine inside innerHTML, fatal inside textContent.
    for m in re.finditer(r'\$\("(\w+)"\)\.textContent = t\("(\w+)"\)', s):
        val = re.search(rf'\n    {m.group(2)}: "([^"]*)"', s)
        if val and re.search(r"&[a-z]+;|&#\d+;", val.group(1)):
            fails.append(f"{name}: {m.group(2)} holds an HTML entity but is set via textContent")

if (ROOT / "android-app/www/standalone.js").read_bytes() != (ROOT / "static/standalone.js").read_bytes():
    fails.append("android-app/www/standalone.js has drifted from static/standalone.js")

if fails:
    print("FAIL"); [print("  -", f) for f in fails]; sys.exit(1)
print("UI TEXT TEST PASS")
