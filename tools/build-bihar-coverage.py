#!/usr/bin/env python3
"""Build the pinned full-State-of-Bihar routing boundary."""

from __future__ import annotations

import argparse
from pathlib import Path

from statewide_boundary_builder import BuildError, ROOT, StateBoundaryConfig, run


CONFIG = StateBoundaryConfig(
    label="Bihar",
    relation_id=1_958_982,
    retrieved_at="2026-08-25",
    output=ROOT / "data" / "metro-coverage" / "br-state.json",
    region_id="bihar-state",
    authority_id="br-statewide-unverified",
    scope="Full State of Bihar",
    geometry_sha256="3d846e20cfee28a656d6dd808c4dad37a4f1c95852f9f292b0acefde708f4b24",
    bbox={"min_lng": 83.3212566, "min_lat": 24.2857164,
          "max_lng": 88.2937958, "max_lat": 27.521635},
    source_bbox=[24.2857164, 27.521635, 83.3212566, 88.2937958],
    routing_note=(
        "State containment enables a neutral Bihar Lok Shikayat handoff. National "
        "Highways retain their own route; containment alone does not identify a local "
        "body, road owner, complaint category, public authority, or department."
    ),
    limitations=(
        "The OpenStreetMap boundary is a routing aid, not a legal boundary record.",
        "Neighbouring states and Nepal are outside this route.",
        "The user must select and verify the district, public authority, local body, category, and road owner.",
        "Municipal, road-construction, panchayat, NHAI and other ownership is not inferred.",
        "Bihar Lok Shikayat requires an interactive user flow; Pothole Reporter does not submit a complaint automatically.",
    ),
    inside_fixtures={
        "patna": (25.5941, 85.1376), "gaya": (24.7914, 85.0002),
        "muzaffarpur": (26.1197, 85.3910), "bhagalpur": (25.2425, 86.9842),
        "darbhanga": (26.1542, 85.8918), "purnia": (25.7771, 87.4753),
        "motihari": (26.6460, 84.9089), "sasaram": (24.9490, 84.0315),
    },
    outside_fixtures={
        "varanasi": (25.3176, 82.9739), "ranchi": (23.3441, 85.3096),
        "siliguri": (26.7271, 88.3953), "gorakhpur": (26.7606, 83.3732),
        "birgunj-nepal": (27.0104, 84.8774), "kolkata": (22.5726, 88.3639),
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
