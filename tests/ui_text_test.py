# -*- coding: utf-8 -*-
"""What the app tells the user must be true in every supported language and render.

Two bugs this guards against, both of which shipped once:
  - HTML entities inside strings applied with textContent, which render literally.
  - Translated strings drifting behind the English ones and describing an older build.
"""
import re, sys, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
fails = []

# The order the dictionaries appear in, which is the order every findall returns them.
LANGUAGES = ("English", "Kannada", "Marathi", "Bengali")
CENTRAL_SERVICE_TERMS = ("project service", "ಯೋಜನೆಯ ಸೇವೆ", "प्रकल्प सेवे", "প্রকল্প পরিষেবা")
RETRY_TERMS = ("retries", "ಮತ್ತೆ ಪ್ರಯತ್ನಿಸುತ್ತದೆ", "पुन्हा प्रयत्न", "আবার চেষ্টা")
EMAIL_ONLY_TERMS = ("Email is the only", "ಏಕೈಕ ಆಯ್ಕೆ ಇಮೇಲ್", "एकमेव पर्याय ईमेल",
                    "একমাত্র উপায় ইমেল")
RETRY_SCOPE_TERMS = ("accepted-metadata upload", "ಮೆಟಾಡೇಟಾ ಅಪ್‌ಲೋಡ್", "पुन्हा प्रयत्न",
                     "আবার চেষ্টা")

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

    # Disclosure: both provider routes and central accepted-pothole collection must be
    # visible in both languages. A personal key must never be described as server-bound.
    shared_notes = re.findall(r'provider_shared_note: "([^"]+)"', s)
    personal_notes = re.findall(r'provider_personal_note: "([^"]+)"', s)
    settings_notes = re.findall(r'settings_note: "([^"]+)"', s)
    privacy_local = re.findall(r'privacy_local: "([^"]+)"', s)
    if not all(len(values) == 4 for values in (
            shared_notes, personal_notes, settings_notes, privacy_local)):
        fails.append(f"{name}: expected provider and central-service notes in four languages")
    else:
        for idx, language in enumerate(LANGUAGES):
            if "OpenAI" not in shared_notes[idx] or "OpenAI" not in personal_notes[idx]:
                fails.append(f"{name}: {language} provider notes do not name OpenAI")
            if "YOLO" not in shared_notes[idx]:
                fails.append(f"{name}: {language} shared note omits the in-house detector option")
            if "API" not in personal_notes[idx]:
                fails.append(f"{name}: {language} personal note does not explain the key")
        for idx, language in enumerate(LANGUAGES):
            # Every language must describe the same three facts: what leaves the phone,
            # that a failed upload keeps retrying, and that email is the only channel.
            if not any(term in settings_notes[idx] for term in CENTRAL_SERVICE_TERMS):
                fails.append(f"{name}: {language} note omits the project service")
            if not any(term in settings_notes[idx] for term in RETRY_TERMS):
                fails.append(f"{name}: {language} note omits durable retry/deletion behavior")
            if not any(term in settings_notes[idx] for term in EMAIL_ONLY_TERMS):
                fails.append(f"{name}: {language} note does not say email is the only channel")
        # Consent copy: every language states that only accepted metadata is retried.
        for idx, language in enumerate(LANGUAGES):
            if not any(term in privacy_local[idx] for term in RETRY_SCOPE_TERMS):
                fails.append(f"{name}: {language} consent omits accepted-only retry scope")

    name_placeholders = re.findall(r'^\s{4}name_placeholder: "([^"]+)"', s, re.MULTILINE)
    if len(name_placeholders) != 4:
        fails.append(f"{name}: expected 4 localized email-name placeholders, found {len(name_placeholders)}")
    if "Gaurav Sen" in s:
        fails.append(f"{name}: Settings still contains the maintainer's personal-name placeholder")
    if '$("setName").placeholder = t("name_placeholder")' not in s:
        fails.append(f"{name}: Settings does not apply the localized email-name placeholder")

    # Scope: localized refusals must describe all supported geographies.
    coverage = re.findall(r'^\s{4}outside_coverage_help: "([^"]+)"', s, re.MULTILINE)
    if len(coverage) == 4:
        if "ಬೆಂಗಳೂರಿಗೆ" in coverage[1] or "ಜಿಬಿಎ" in coverage[1]:
            fails.append(f"{name}: Kannada out-of-coverage text still says Bengaluru only")
        if any(term not in coverage[0] for term in (
            "India", "State/UT", "National Highway",
        )):
            fails.append(f"{name}: English out-of-coverage text omits a supported region")
        if any(term not in coverage[1] for term in ("ಭಾರತ", "ರಾಜ್ಯ/ಕೇಂದ್ರಾಡಳಿತ", "ರಾಷ್ಟ್ರೀಯ ಹೆದ್ದಾರಿ")):
            fails.append(f"{name}: Kannada out-of-coverage text omits a supported region")
        if any(term not in coverage[2] for term in ("भारत", "राज्य/केंद्रशासित", "राष्ट्रीय महामार्ग")):
            fails.append(f"{name}: Marathi out-of-coverage text omits a supported region")
        if any(term not in coverage[3] for term in ("ভারত", "রাজ্য/কেন্দ্রশাসিত", "জাতীয় সড়ক")):
            fails.append(f"{name}: Bengali out-of-coverage text omits a supported region")
    else:
        fails.append(f"{name}: expected 4 outside_coverage_help strings, found {len(coverage)}")

    # Email is the sole complaint channel. The BMC, WhatsApp, helpline and portal
    # handoffs were removed along with the grievance-ID fields that recorded them:
    # opening another service proves nothing about whether a complaint was filed.
    # confirm_suggested_email survives: it is the ownership warning shown before the
    # one channel that is left.
    for removed in ("chip_queued_bmc", "chip_queued_official", "bmc_disclaimer",
                    "official_disclaimer", "authority_disclaimer",
                    "confirm_official_handoff", "confirm_whatsapp_share",
                    "official_grievance_label", "official_grievance_generic_label"):
        if re.search(rf'^\s+{removed}: "', s, re.MULTILINE):
            fails.append(f"{name}: removed handoff string {removed} is back in the UI copy")
    for element in ('id="grievanceId"', 'id="whatsappBtn"', 'id="portalFieldsBtn"'):
        if element in s:
            fails.append(f"{name}: removed handoff control {element} is back in the UI")

    if '<option value="mr">मराठी</option>' not in s:
        fails.append(f"{name}: Marathi is missing from the language selector")
    if '<option value="bn">বাংলা</option>' not in s:
        fails.append(f"{name}: Bengali is missing from the language selector")

    # New detections have one public decision only. Do not let confidence, subtype,
    # or clear/probable wording creep back into the visible result or labelling UI.
    detected = re.findall(r'^\s{4}verdict_detected: "([^"]+)"', s, re.MULTILINE)
    rejected = re.findall(r'^\s{4}verdict_rejected: "([^"]+)"', s, re.MULTILINE)
    if len(detected) != 4 or len(rejected) != 4:
        fails.append(f"{name}: expected four localized binary pothole verdict pairs")
    elif detected[0] != "Pothole: YES" or rejected[0] != "Pothole: NO":
        fails.append(f"{name}: English detection verdict is not binary YES/NO")
    if re.search(r'^\s{4}confidence:', s, re.MULTILINE):
        fails.append(f"{name}: visible confidence wording returned")
    if any(button in s for button in ('id="lblPatch"', 'id="lblSurface"', 'id="lblRut"')):
        fails.append(f"{name}: human detector labels are not binary")

    # Email is the sole complaint channel. Refusal/help copy must not steer people to
    # another app or phone line, and opening a composer must not be counted as delivery.
    for legacy_claim in ("Rajmargyatra", "1033 helpline", "Complaint sent", "complaints sent"):
        if legacy_claim.lower() in s.lower():
            fails.append(f"{name}: contains legacy alternate-channel/delivery claim: {legacy_claim}")

    # Every refusal reason the engine can emit needs user-facing text.
    eng = (ROOT / "static/standalone.js").read_text(encoding="utf-8")
    reasons = set(re.findall(r'return \[null, null, "([a-z_]+)"', eng))
    for r in reasons:
        key = {"outside_area": "outside_coverage", "rural_road": "rural_road",
               "no_location": "no_location", "no_address_for_body": "no_address",
               "national_highway": "nat_highway", "state_highway": "state_highway",
               "district_highway": "district_highway",
               "road_class_unknown": "road_unknown"}.get(r)
        if key and f"{key}:" not in s:
            fails.append(f"{name}: refusal reason '{r}' has no UI string ({key})")

    # Native Drive Mode cannot read WebView localStorage itself. The selected UI
    # language must cross the plugin boundary with the model settings.
    if "language: LANG" not in s:
        fails.append(f"{name}: native Drive Mode does not receive the selected language")

    # Entities are fine inside innerHTML, fatal inside textContent.
    for m in re.finditer(r'\$\("(\w+)"\)\.textContent = t\("(\w+)"\)', s):
        val = re.search(rf'\n    {m.group(2)}: "([^"]*)"', s)
        if val and re.search(r"&[a-z]+;|&#\d+;", val.group(1)):
            fails.append(f"{name}: {m.group(2)} holds an HTML entity but is set via textContent")

runtime = (ROOT / "static/standalone.js").read_text(encoding="utf-8")
road_outside_error = re.search(
    r'outside_area: "(This road damage is outside India[^\"]+)"',
    runtime,
)
if not road_outside_error or any(term not in road_outside_error.group(1) for term in (
    "State/UT", "National Highways", "exact routing data",
)):
    fails.append("standalone.js road out-of-coverage error omits a supported region")

if (ROOT / "android-app/www/standalone.js").read_bytes() != (ROOT / "static/standalone.js").read_bytes():
    fails.append("android-app/www/standalone.js has drifted from static/standalone.js")
if (ROOT / "docs/standalone.js").read_bytes() != (ROOT / "static/standalone.js").read_bytes():
    fails.append("docs/standalone.js has drifted from static/standalone.js")

privacy = (ROOT / "docs/privacy.html").read_text(encoding="utf-8")
for phrase in (
        "Rejected photos do not use the project tender endpoint",
        "retried when the app starts or reconnects",
        "Deleting that local report",
        "configured detector",
        "no more than 24 hours",
        "without a photo, name, complaint text, or API key"):
    if phrase not in privacy:
        fails.append(f"docs/privacy.html omits retry/privacy disclosure: {phrase}")

if fails:
    print("FAIL"); [print("  -", f) for f in fails]; sys.exit(1)
print("UI TEXT TEST PASS")
