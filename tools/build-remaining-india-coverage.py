#!/usr/bin/env python3
"""Build the pinned State/UT boundaries added to the v1.35 routing catalog."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from india_jurisdictions import JURISDICTIONS, RETRIEVED_AT, pack_id, source_path
from statewide_boundary_builder import BuildError, ROOT, StateBoundaryConfig, run


def config_for(item: dict[str, object]) -> StateBoundaryConfig:
    label = str(item["label"])
    authority = item["authority"]
    if not isinstance(authority, dict):
        raise BuildError(f"{label} authority pin is invalid")
    handoff_name = str(authority["handoff_name"])
    territory = item["kind"] == "union_territory"
    return StateBoundaryConfig(
        label=label,
        relation_id=int(item["relation_id"]),
        retrieved_at=RETRIEVED_AT,
        output=ROOT / source_path(item),
        region_id=str(item["region_id"]),
        authority_id=str(authority["id"]),
        scope=f"Full {'Union Territory' if territory else 'State'} of {label}",
        geometry_sha256=str(item["geometry_sha256"]),
        bbox=dict(item["bbox"]),
        source_bbox=list(item["source_bbox"]),
        routing_note=(
            f"Jurisdiction containment enables a neutral {handoff_name} grievance "
            "handoff. National Highways retain their own route; containment alone "
            "does not identify a local body, PWD road, road owner, complaint category, "
            "or department."
        ),
        limitations=(
            "The OpenStreetMap boundary is a routing aid, not a legal boundary record.",
            "The user must select and verify the department, local body, category, and road owner.",
            "Municipal, PWD, panchayat, NHAI and other ownership is not inferred.",
            f"{handoff_name} requires an interactive user flow; Pothole Reporter does not submit a complaint automatically.",
        ),
        inside_fixtures=dict(item["inside"]),
        outside_fixtures={"bengaluru": (12.9716, 77.5946)},
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--code", choices=[str(item["code"]) for item in JURISDICTIONS])
    parser.add_argument("--input", type=Path, help="saved Nominatim response; requires --code")
    parser.add_argument("--check", action="store_true", help="verify without writing")
    args = parser.parse_args()
    if args.input and not args.code:
        parser.error("--input requires --code")

    selected = [item for item in JURISDICTIONS if not args.code or item["code"] == args.code]
    for index, item in enumerate(selected):
        run(config_for(item), args.input, args.check)
        if not args.input and index + 1 < len(selected):
            time.sleep(1.1)


if __name__ == "__main__":
    try:
        main()
    except BuildError as exc:
        raise SystemExit(f"FAIL: {exc}") from exc
