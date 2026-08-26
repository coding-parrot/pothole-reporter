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

    # Check the manifest-selected runtime artifacts too.  A safe classifier is not
    # enough if a stale generated pack can still publish an unsafe identity.
    pack_manifest = json.loads(
        (ROOT / "static" / "pack-manifest-v1.35.json").read_text(encoding="utf-8")
    )
    kppp_resource = pack_manifest["resources"]["in-ka-tenders"]
    kppp_pack = json.loads(
        (ROOT / "docs" / kppp_resource["path"]).read_text(encoding="utf-8")
    )
    kppp_rows = kppp_pack["tenders"]
    if len(kppp_rows) != kppp_resource["records"]:
        failures.append("current KPPP runtime pack count differs from its manifest")
    unsafe_kppp_contractors = [
        item for item in kppp_rows if str(item.get("c") or "").strip()
    ]
    if unsafe_kppp_contractors:
        failures.append(
            "current KPPP runtime pack publishes unverified contractor names: "
            f"{unsafe_kppp_contractors[0].get('tn')}"
        )
    runtime_kppp_ids = {
        str(item.get("tn") or "").strip().casefold() for item in kppp_rows
    }
    for unsafe_id in (
        "BBMP/2024-25/RD/WORK_INDENT4233",
        "BBMP/2023-24/OW/WORK_INDENT2505",
    ):
        if unsafe_id.casefold() in runtime_kppp_ids:
            failures.append(f"unsafe KPPP record remains in the current runtime pack: {unsafe_id}")

    notice_manifest = json.loads(
        (ROOT / "static" / "road-notice-manifest-v1.36.json").read_text(encoding="utf-8")
    )
    goa_resource = notice_manifest["resources"]["in-road-notices-ga"]
    goa_pack = json.loads(
        (ROOT / "docs" / goa_resource["path"]).read_text(encoding="utf-8")
    )
    goa_rows = goa_pack["notices"]
    if len(goa_rows) != goa_resource["records"]:
        failures.append("current Goa road-notice pack count differs from its manifest")
    if any(
        str(item.get("tender_id") or "").strip().casefold()
        == "2026_TSAG_30877_1".casefold()
        for item in goa_rows
    ):
        failures.append("Goa tennis-court record remains in the current runtime pack")

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
    if is_road_surface_contract(
        "Maintenance of road side plantations in Social Forestry Range", "KPPP/2026/RD/7"
    ):
        failures.append("treated roadside plantation as road-surface work")
    if is_road_surface_contract(
        "oldPlantation work at Road sideof HunsurSocial Forestry range", "KPPP/2026/RD/9"
    ):
        failures.append("malformed roadside plantation text bypassed the scope filter")
    if not is_road_surface_contract(
        "Construction of a cement concrete road with plantation from A Road to B Village",
        "PWD/2026/RD/8",
    ):
        failures.append("rejected genuine mixed road construction with ancillary plantation")
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
