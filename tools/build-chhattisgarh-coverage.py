#!/usr/bin/env python3
"""Build the pinned full-State-of-Chhattisgarh routing boundary."""

from __future__ import annotations

import argparse
from pathlib import Path

from statewide_boundary_builder import BuildError, ROOT, StateBoundaryConfig, run


CONFIG = StateBoundaryConfig(
    label="Chhattisgarh",
    relation_id=1_972_004,
    retrieved_at="2026-08-25",
    output=ROOT / "data" / "metro-coverage" / "cg-state.json",
    region_id="chhattisgarh-state",
    authority_id="cg-statewide-unverified",
    scope="Full State of Chhattisgarh",
    geometry_sha256="827e89a598571ade84db77390bca5daf98c9f67fbae716b17193f4ccdc2876eb",
    bbox={"min_lng": 80.2441803, "min_lat": 17.7822157,
          "max_lng": 84.3959641, "max_lat": 24.1066864},
    source_bbox=[17.7822157, 24.1066864, 80.2441803, 84.3959641],
    routing_note=(
        "State containment enables a neutral Chhattisgarh CM Helpline grievance handoff, "
        "with NIDAAN 1100 as an urban civic alternative. It does not identify a local "
        "body, PWD road, road owner, complaint category, or department."
    ),
    limitations=(
        "The OpenStreetMap boundary is a routing aid, not a legal boundary record.",
        "Neighbouring states are outside this route.",
        "The user must select and verify the district, department, local body, category, and road owner.",
        "Municipal, PWD, panchayat, NHAI, cantonment and other ownership is not inferred.",
        "The official services require an interactive user flow; Pothole Reporter does not submit a complaint automatically.",
    ),
    inside_fixtures={
        "raipur": (21.2514, 81.6296), "bhilai": (21.1938, 81.3509),
        "bilaspur": (22.0796, 82.1409), "jagdalpur": (19.0748, 82.0080),
        "ambikapur": (23.1355, 83.1818), "dantewada": (18.9000, 81.3500),
        "korba": (22.3595, 82.7501),
    },
    outside_fixtures={
        "nagpur": (21.1458, 79.0882), "ranchi": (23.3441, 85.3096),
        "hyderabad": (17.3850, 78.4867), "bhubaneswar": (20.2961, 85.8245),
        "shahdol": (23.3002, 81.3568), "koraput": (18.8120, 82.7100),
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
