#!/usr/bin/env python3
"""GitHub Pages must contain the complete runnable pure-client asset tree."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
PAGES = ROOT / "docs"
failures: list[str] = []

for source in sorted(path for path in STATIC.rglob("*") if path.is_file()):
    relative = source.relative_to(STATIC)
    deployed = PAGES / relative
    if not deployed.is_file():
        failures.append(f"GitHub Pages asset is missing: docs/{relative}")
    elif deployed.read_bytes() != source.read_bytes():
        failures.append(f"GitHub Pages asset differs from source: docs/{relative}")

# Tester feedback carries a phone model, app version and an optional email. The policy
# the Play listing links to has to say so, and say how long the project keeps it.
privacy = (PAGES / "privacy.html").read_text(encoding="utf-8")
for phrase in ("Tester feedback", "phone model", "app version", "optional email",
               "only to reply"):
    if phrase not in privacy:
        failures.append(f"docs/privacy.html does not disclose tester feedback: {phrase!r}")

if failures:
    print("FAIL")
    for failure in failures:
        print("  -", failure)
    sys.exit(1)
print("PAGES ASSETS TEST PASS")
