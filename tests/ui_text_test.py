# -*- coding: utf-8 -*-
"""What the app tells the user must be true, in both languages, and must render.

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

    # Disclosure: the user must be told the photo leaves the device, in both languages.
    en = re.search(r'settings_note: "([^"]+)"', s)
    if not en or "OpenAI" not in en.group(1):
        fails.append(f"{name}: English settings note does not say the photo goes to OpenAI")
    notes = re.findall(r'settings_note: "([^"]+)"', s)
    if len(notes) != 2:
        fails.append(f"{name}: expected 2 settings_note strings, found {len(notes)}")
    elif "OpenAI" not in notes[1]:
        fails.append(f"{name}: Kannada settings note does not mention OpenAI")

    # Scope: the Kannada refusal must not still describe the Bengaluru-only build.
    kn = re.findall(r'outside_coverage_help: "([^"]+)"', s)
    if len(kn) == 2:
        if "ಬೆಂಗಳೂರಿಗೆ" in kn[1] or "ಜಿಬಿಎ" in kn[1]:
            fails.append(f"{name}: Kannada out-of-coverage text still says Bengaluru only")
        if "ಕರ್ನಾಟಕ" not in kn[1]:
            fails.append(f"{name}: Kannada out-of-coverage text does not mention Karnataka")
    else:
        fails.append(f"{name}: expected 2 outside_coverage_help strings, found {len(kn)}")

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
