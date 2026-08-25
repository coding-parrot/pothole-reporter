#!/usr/bin/env python3
"""Build the pinned full-State-of-Madhya-Pradesh routing boundary."""

from __future__ import annotations

import argparse
from pathlib import Path

from statewide_boundary_builder import BuildError, ROOT, StateBoundaryConfig, run


CONFIG = StateBoundaryConfig(
    label="Madhya Pradesh",
    relation_id=1_950_071,
    retrieved_at="2026-08-25",
    output=ROOT / "data" / "metro-coverage" / "mp-state.json",
    region_id="madhya-pradesh-state",
    authority_id="mp-statewide-unverified",
    scope="Full State of Madhya Pradesh",
    geometry_sha256="24f0c93ed8bd40c4c6b4e1f650c3b9870b1e65ccd5d7b00ea0193a8a5aedc357",
    bbox={"min_lng": 74.029382, "min_lat": 21.0706885,
          "max_lng": 82.8126116, "max_lat": 26.8695616},
    source_bbox=[21.0706885, 26.8695616, 74.029382, 82.8126116],
    routing_note=(
        "State containment enables a neutral Madhya Pradesh CM Helpline handoff. "
        "National Highways retain their own route; containment alone does not identify "
        "a local body, PWD road, road owner, complaint category, or department."
    ),
    limitations=(
        "The OpenStreetMap boundary is a routing aid, not a legal boundary record.",
        "Neighbouring states are outside this route.",
        "The user must select and verify the district, department, local body, category, and road owner.",
        "Municipal, PWD, panchayat, NHAI and other ownership is not inferred.",
        "MP CM Helpline requires an interactive user flow; Pothole Reporter does not submit a complaint automatically.",
    ),
    inside_fixtures={
        "bhopal": (23.2599, 77.4126), "indore": (22.7196, 75.8577),
        "gwalior": (26.2183, 78.1828), "jabalpur": (23.1815, 79.9864),
        "ujjain": (23.1765, 75.7885), "sagar": (23.8388, 78.7378),
        "rewa": (24.5362, 81.3037), "satna": (24.6005, 80.8322),
    },
    outside_fixtures={
        "nagpur": (21.1458, 79.0882), "kota": (25.2138, 75.8648),
        "varanasi": (25.3176, 82.9739), "raipur": (21.2514, 81.6296),
        "jhansi": (25.4484, 78.5685), "ahmedabad": (23.0225, 72.5714),
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
