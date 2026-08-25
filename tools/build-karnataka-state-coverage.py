#!/usr/bin/env python3
"""Build the pinned full-State-of-Karnataka routing boundary."""

from __future__ import annotations

import argparse
from pathlib import Path

from statewide_boundary_builder import BuildError, ROOT, StateBoundaryConfig, run


CONFIG = StateBoundaryConfig(
    label="Karnataka",
    relation_id=2_019_939,
    retrieved_at="2026-08-25",
    output=ROOT / "data" / "metro-coverage" / "ka-state.json",
    region_id="karnataka-state",
    authority_id="ka-statewide-unverified",
    scope="Full State of Karnataka",
    geometry_sha256="9d7fe3f01a80cb41712c09139efcd43e0e11a644849d5f3bffe125cc0bc1c5ad",
    bbox={"min_lng": 74.0543908, "min_lat": 11.5945587,
          "max_lng": 78.5875761, "max_lat": 18.4766494},
    source_bbox=[11.5945587, 18.4766494, 74.0543908, 78.5875761],
    routing_note=(
        "State containment enables a neutral Karnataka Janaspandana grievance handoff. "
        "Exact KGIS urban-body routes remain more specific; containment alone does not "
        "identify a municipality, road owner, complaint category, or department."
    ),
    limitations=(
        "The OpenStreetMap boundary is a routing aid, not a legal boundary record.",
        "Neighbouring states and Goa are outside this route.",
        "The user must select and verify the district, department, local body, category, and road owner.",
        "Municipal, PWD, panchayat, NHAI, cantonment and other ownership is not inferred.",
        "Pothole Reporter opens the official service but does not submit a complaint automatically.",
    ),
    inside_fixtures={
        "bengaluru": (12.9716, 77.5946), "mysuru": (12.2958, 76.6394),
        "mangaluru": (12.9141, 74.8560), "hubballi": (15.3647, 75.1240),
        "kalaburagi": (17.3297, 76.8343), "madikeri": (12.4244, 75.7382),
    },
    outside_fixtures={
        "panaji": (15.4909, 73.8278), "hyderabad": (17.3850, 78.4867),
        "kozhikode": (11.2588, 75.7804), "chennai": (13.0827, 80.2707),
        "kolhapur": (16.7050, 74.2433), "anantapur": (14.6819, 77.6006),
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
