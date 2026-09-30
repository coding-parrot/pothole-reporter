# -*- coding: utf-8 -*-
"""A routing failure must never be blamed on the user's phone signal.

Reported from Gandhinagar on 30 Sep 2026: the app refused to route a pothole and said
"Try again when you have a signal" on a handset showing full 5G and four bars. The road
register was the thing that could not answer, not the phone. Telling a user to fix a
connection that is already working leaves them with nothing to do and no way to tell a
real outage from their own network.
"""
import re, sys, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
LANGUAGES = ("English", "Kannada", "Marathi", "Bengali")
fails = []

# Each language's way of saying "when you have a signal" / "when the network returns".
SIGNAL_BLAME = (
    "when you have a signal",
    "ಸಿಗ್ನಲ್ ಬಂದ ನಂತರ",
    "नेटवर्क मिळाल्यावर",
    "সংযোগ ফিরে এলে",
)

MIRRORS = ("static/index.html", "android-app/www/index.html", "docs/index.html",
           "android-app/android/app/src/main/assets/public/index.html")

for name in MIRRORS:
    path = ROOT / name
    if not path.exists():
        fails.append("%s is missing" % name)
        continue
    source = path.read_text(encoding="utf-8")
    helps = re.findall(r'road_unknown_help: "([^"]+)"', source)
    if len(helps) != len(LANGUAGES):
        fails.append("%s has %d road_unknown_help strings, expected %d"
                     % (name, len(helps), len(LANGUAGES)))
        continue
    for language, text, blame in zip(LANGUAGES, helps, SIGNAL_BLAME):
        if blame in text:
            fails.append("%s %s road_unknown_help blames the phone signal: %r"
                         % (name, language, blame))

# The same sentence is spoken by the engine when a send is refused, and that copy is the
# one the user photographed inside the alert dialog.
for name in ("static/standalone.js", "android-app/www/standalone.js", "docs/standalone.js",
             "android-app/android/app/src/main/assets/public/standalone.js"):
    path = ROOT / name
    if not path.exists():
        fails.append("%s is missing" % name)
        continue
    source = path.read_text(encoding="utf-8")
    match = re.search(r'road_class_unknown: "([^"]+)"', source)
    if not match:
        fails.append("%s has no road_class_unknown complaint message" % name)
        continue
    if "when you have a signal" in match.group(1):
        fails.append("%s road_class_unknown blames the phone signal: %r"
                     % (name, match.group(1)))

if fails:
    print("FAIL")
    for line in fails:
        print("   ", line)
    sys.exit(1)
print("PASS routing failures name the register that is down, not the user's signal")
