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

if failures:
    print("FAIL")
    for failure in failures:
        print("  -", failure)
    sys.exit(1)
print("PAGES ASSETS TEST PASS")
