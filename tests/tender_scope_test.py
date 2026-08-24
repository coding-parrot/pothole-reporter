#!/usr/bin/env python3
"""Drain/footpath locations must not become pothole-contract matches."""

from __future__ import annotations

import json
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from state_pack_tools import PackError, SPECS, _validate_raw_payload  # noqa: E402
from tender_scope import is_road_surface_contract  # noqa: E402


def row(case: dict[str, str], index: int) -> dict[str, str]:
    return {
        "tn": case["tn"],
        "t": case["title"],
        "loc": "Test municipal body",
        "c": f"Contractor {index}",
        "d": "21-08-2026",
        "b": "BLR",
    }


def main() -> int:
    fixture = json.loads(
        (ROOT / "tests" / "fixtures" / "tender-scope.json").read_text(encoding="utf-8")
    )
    failures: list[str] = []

    for case in fixture["reject"]:
        if is_road_surface_contract(case["title"], case["tn"]):
            failures.append(f"accepted non-carriageway scope: {case['tn']}")
    for case in fixture["accept"]:
        if not is_road_surface_contract(case["title"], case["tn"]):
            failures.append(f"rejected genuine road scope: {case['tn']}")

    cited = next(case for case in fixture["reject"] if case["tn"].endswith("WORK_INDENT2505"))
    if "drain and footpath" not in cited["title"].lower():
        failures.append("the cited BBMP regression fixture no longer describes drain/footpath work")

    # Anchor the regression fixture to the checked-in procurement snapshot rather than
    # letting a hand-written approximation silently diverge from the real offending row.
    canonical_rows = json.loads(
        (ROOT / "data" / "tenders-karnataka.json").read_text(encoding="utf-8")
    )
    canonical_cited = next(
        (item for item in canonical_rows if item.get("tn") == cited["tn"]), None
    )
    if canonical_cited is None:
        failures.append("the cited BBMP tender is missing from the canonical source snapshot")
    elif canonical_cited.get("t") != cited["title"]:
        failures.append("the cited BBMP fixture differs from the canonical work description")
    elif is_road_surface_contract(canonical_cited["t"], canonical_cited["tn"]):
        failures.append("the canonical cited BBMP drain/footpath tender passed the classifier")

    mixed = next(
        case for case in fixture["accept"] if case["tn"].endswith("WORK_INDENT46859/CALL-2")
    )
    canonical_mixed = next(
        (item for item in canonical_rows if item.get("tn") == mixed["tn"]), None
    )
    if canonical_mixed is None:
        failures.append("the reverse-order drain-and-road fixture is missing from the source snapshot")
    elif canonical_mixed.get("t") != mixed["title"]:
        failures.append("the reverse-order mixed-scope fixture differs from the source description")
    elif not is_road_surface_contract(canonical_mixed["t"], canonical_mixed["tn"]):
        failures.append("the canonical drain-and-CC-road tender failed the classifier")

    spec = SPECS["in-ka-tenders"]
    valid_rows = [row(case, index) for index, case in enumerate(fixture["accept"], 1)]
    try:
        _validate_raw_payload(spec, valid_rows)
    except PackError as error:
        failures.append(f"pack validator rejected eligible fixtures: {error}")

    invalid_rows = valid_rows + [row(cited, 999)]
    try:
        _validate_raw_payload(spec, invalid_rows)
    except PackError as error:
        if cited["tn"] not in str(error) or "carriageway-work scope" not in str(error):
            failures.append(f"pack validator gave an unauditable scope error: {error}")
    else:
        failures.append("pack validator accepted the cited drain/footpath tender")

    # Category-looking tender-number fragments are not evidence.  A drain-only /RD/ row
    # stays out, while genuine mixed /OW/ rows stay in based on their stated scope.
    if is_road_surface_contract("Construction of storm water drain at Ward 5", "DMA/2026-27/RD/1"):
        failures.append("trusted /RD/ tender-number metadata over the stated drain-only scope")
    if not is_road_surface_contract(
        "Improvements to roads and drains in Ward 5", "BBMP/2026-27/OW/2"
    ):
        failures.append("rejected explicit mixed carriageway scope because it used /OW/")

    if failures:
        print("TENDER SCOPE TEST FAIL", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    print(
        f"TENDER SCOPE TEST PASS ({len(fixture['accept'])} road, "
        f"{len(fixture['reject'])} non-road fixtures)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
