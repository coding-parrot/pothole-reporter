#!/usr/bin/env python3
"""Build the pinned full-State-of-Rajasthan routing boundary."""

from __future__ import annotations

import argparse
from pathlib import Path

from statewide_boundary_builder import BuildError, ROOT, StateBoundaryConfig, run


CONFIG = StateBoundaryConfig(
    label="Rajasthan",
    relation_id=1_942_920,
    retrieved_at="2026-08-25",
    output=ROOT / "data" / "metro-coverage" / "rj-state.json",
    region_id="rajasthan-state",
    authority_id="rj-statewide-unverified",
    scope="Full State of Rajasthan",
    geometry_sha256="dcde670675d0fc50e292c6b306b1f80d9d68a1323250c29d6eddc97992491a36",
    bbox={"min_lng": 69.4844368, "min_lat": 23.0586612,
          "max_lng": 78.2720089, "max_lat": 30.198253},
    source_bbox=[23.0586612, 30.198253, 69.4844368, 78.2720089],
    routing_note=(
        "State containment enables a neutral Rajasthan Sampark grievance handoff. "
        "National Highways retain their own route; containment alone does not identify "
        "a local body, PWD road, road owner, complaint category, or department."
    ),
    limitations=(
        "The OpenStreetMap boundary is a routing aid, not a legal boundary record.",
        "Neighbouring states and countries are outside this route.",
        "The user must select and verify the district, department, local body, category, and road owner.",
        "Municipal, PWD, panchayat, NHAI, cantonment and other ownership is not inferred.",
        "Rajasthan Sampark requires an interactive user flow; Pothole Reporter does not submit a complaint automatically.",
    ),
    inside_fixtures={
        "jaipur": (26.9154576, 75.8189817), "jodhpur": (26.2967719, 73.0351433),
        "kota": (25.2138, 75.8648), "udaipur": (24.5854, 73.7125),
        "bikaner": (28.0229, 73.3119), "jaisalmer": (26.9157, 70.9083),
        "bharatpur": (27.2152, 77.5030), "banswara": (23.5461, 74.4349),
    },
    outside_fixtures={
        "ahmedabad": (23.0225, 72.5714), "gwalior": (26.2037247, 78.1573628),
        "agra": (27.1752554, 78.0098161), "hisar": (29.1492, 75.7217),
        "fazilka": (30.4036, 74.0280), "bahawalpur": (29.3956, 71.6836),
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
