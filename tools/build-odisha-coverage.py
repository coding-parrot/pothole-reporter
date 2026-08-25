#!/usr/bin/env python3
"""Build the pinned full-State-of-Odisha routing boundary."""

from __future__ import annotations

import argparse
from pathlib import Path

from statewide_boundary_builder import BuildError, ROOT, StateBoundaryConfig, run


CONFIG = StateBoundaryConfig(
    label="Odisha",
    relation_id=1_984_022,
    retrieved_at="2026-08-25",
    output=ROOT / "data" / "metro-coverage" / "od-state.json",
    region_id="odisha-state",
    authority_id="od-statewide-unverified",
    scope="Full State of Odisha",
    geometry_sha256="af0fe4941b6cdd2abe5dc5717db8875bec6b68a2d6671002d2afc9c7d37d5179",
    bbox={"min_lng": 81.3885855, "min_lat": 17.8122733,
          "max_lng": 87.4861351, "max_lat": 22.5675932},
    source_bbox=[17.8122733, 22.5675932, 81.3885855, 87.4861351],
    routing_note=(
        "State containment enables a neutral Odisha Jana Sunani handoff. National "
        "Highways retain their own route; containment alone does not identify a local "
        "body, road owner, complaint category, public authority, or department."
    ),
    limitations=(
        "The OpenStreetMap boundary is a routing aid, not a legal boundary record.",
        "Neighbouring states and the Bay of Bengal are outside this route.",
        "The user must select and verify the district, department, local body, category, and road owner.",
        "Municipal, works, rural-development, panchayat, NHAI and other ownership is not inferred.",
        "Jana Sunani requires an interactive user flow; Pothole Reporter does not submit a complaint automatically.",
    ),
    inside_fixtures={
        "bhubaneswar": (20.2961, 85.8245), "cuttack": (20.4625, 85.8830),
        "rourkela": (22.2604, 84.8536), "sambalpur": (21.4669, 83.9812),
        "berhampur": (19.3149, 84.7941), "puri": (19.8135, 85.8312),
        "balasore": (21.4934, 86.9135), "koraput": (18.8135, 82.7123),
    },
    outside_fixtures={
        "visakhapatnam": (17.6868, 83.2185), "raipur": (21.2514, 81.6296),
        "ranchi": (23.3441, 85.3096), "kolkata": (22.5726, 88.3639),
        "jamshedpur": (22.8046, 86.2029), "bay-of-bengal": (19.5000, 87.6000),
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
