#!/usr/bin/env python3
"""Build the pinned full-State-of-Kerala routing boundary."""

from __future__ import annotations

import argparse
from pathlib import Path

from statewide_boundary_builder import BuildError, ROOT, StateBoundaryConfig, run


CONFIG = StateBoundaryConfig(
    label="Kerala",
    relation_id=2_018_151,
    retrieved_at="2026-08-25",
    output=ROOT / "data" / "metro-coverage" / "kl-state.json",
    region_id="kerala-state",
    authority_id="kl-statewide-unverified",
    scope="Full State of Kerala; excludes Mahe, Puducherry Union Territory",
    geometry_sha256="51e226750b1d6c08a5030e6074e2641282e01c328f46d8aee741de664bef705c",
    bbox={"min_lng": 74.8640682, "min_lat": 8.2935318,
          "max_lng": 77.4123612, "max_lat": 12.7960559},
    source_bbox=[8.2935318, 12.7960559, 74.8640682, 77.4123612],
    routing_note=(
        "State containment enables a neutral Kerala CMO grievance handoff, with K-SMART "
        "as a local-body alternative. It does not identify a local body, PWD road, road "
        "owner, complaint category, water authority, or department."
    ),
    limitations=(
        "The OpenStreetMap boundary is a routing aid, not a legal boundary record.",
        "Mahe and neighbouring states are outside this route.",
        "The user must select and verify the district, department, local body, category, and asset owner.",
        "Local-body, PWD, KWA, NHAI and other ownership is not inferred.",
        "Pothole Reporter opens the official service but does not submit a complaint automatically.",
    ),
    inside_fixtures={
        "thiruvananthapuram": (8.5241, 76.9366), "kochi": (9.9312, 76.2673),
        "kozhikode": (11.2588, 75.7804), "kannur": (11.8745, 75.3704),
        "idukki": (9.8494, 76.9720), "wayanad": (11.6854, 76.1320),
    },
    outside_fixtures={
        "mahe": (11.7010, 75.5350), "mangaluru": (12.9141, 74.8560),
        "coimbatore": (11.0168, 76.9558), "nagercoil": (8.1833, 77.4119),
        "kavaratti": (10.5667, 72.6417),
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
