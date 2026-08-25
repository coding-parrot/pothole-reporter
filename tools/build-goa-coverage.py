#!/usr/bin/env python3
"""Build the pinned full-State-of-Goa routing boundary."""

from __future__ import annotations

import argparse
from pathlib import Path

from statewide_boundary_builder import BuildError, ROOT, StateBoundaryConfig, run


CONFIG = StateBoundaryConfig(
    label="Goa",
    relation_id=11_251_493,
    retrieved_at="2026-08-25",
    output=ROOT / "data" / "metro-coverage" / "ga-state.json",
    region_id="goa-state",
    authority_id="ga-statewide-unverified",
    scope="Full State of Goa",
    geometry_sha256="f4c47a79a3671d333d47f66a597d66b6295a78b1cd7cd3cba7bc2db472190e4f",
    bbox={"min_lng": 73.6756012, "min_lat": 14.7529315,
          "max_lng": 74.3361139, "max_lat": 15.8007631},
    source_bbox=[14.7529315, 15.8007631, 73.6756012, 74.3361139],
    routing_note=(
        "State containment enables a neutral CM Helpline Goa grievance handoff. "
        "National Highways retain their own route; containment alone does not "
        "identify a local body, PWD road, road owner, complaint category, or department."
    ),
    limitations=(
        "The OpenStreetMap boundary is a routing aid, not a legal boundary record.",
        "Neighbouring states and the Arabian Sea are outside this route.",
        "The user must select and verify the department, local body, category, and road owner.",
        "Municipal, PWD, panchayat, NHAI and other ownership is not inferred.",
        "CM Helpline Goa requires an interactive user flow; Pothole Reporter does not submit a complaint automatically.",
    ),
    inside_fixtures={
        "panaji": (15.4909, 73.8278), "margao": (15.2993, 73.9580),
        "vasco-da-gama": (15.3860, 73.8440), "mapusa": (15.5915, 73.8080),
        "ponda": (15.4027, 74.0078), "canacona": (14.9950, 74.0500),
    },
    outside_fixtures={
        "belagavi": (15.8497, 74.4977), "karwar": (14.8136, 74.1297),
        "sawantwadi": (15.9040, 73.8210), "arabian-sea": (15.3000, 73.5000),
    },
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="saved Nominatim jsonv2 response")
    parser.add_argument("--check", action="store_true", help="verify without writing")
    args = parser.parse_args()
    run(CONFIG, args.input, args.check)


if __name__ == "__main__":
    try:
        main()
    except BuildError as exc:
        raise SystemExit(f"FAIL: {exc}") from exc
