#!/usr/bin/env python3
"""Build the pinned full-State-of-Uttar-Pradesh routing boundary."""

from __future__ import annotations

import argparse
from pathlib import Path

from statewide_boundary_builder import BuildError, ROOT, StateBoundaryConfig, run


CONFIG = StateBoundaryConfig(
    label="Uttar Pradesh",
    relation_id=1_942_587,
    retrieved_at="2026-08-25",
    output=ROOT / "data" / "metro-coverage" / "up-state.json",
    region_id="uttar-pradesh-state",
    authority_id="up-statewide-unverified",
    scope="Full State of Uttar Pradesh; excludes Delhi National Capital Territory",
    geometry_sha256="2dbb5237cab5eb029f517c1d79451663c1fc49affe0e0789b11f0565180db015",
    bbox={"min_lng": 77.0838761, "min_lat": 23.8706272,
          "max_lng": 84.6345091, "max_lat": 30.4063828},
    source_bbox=[23.8706272, 30.4063828, 77.0838761, 84.6345091],
    routing_note=(
        "State containment enables a neutral Uttar Pradesh Jansunwai grievance handoff. "
        "Delhi NCT and National Highways retain their own routes; containment alone does "
        "not identify a local body, road owner, complaint category, or department."
    ),
    limitations=(
        "The OpenStreetMap boundary is a routing aid, not a legal boundary record.",
        "Delhi National Capital Territory and neighbouring states are outside this route.",
        "The user must select and verify the district, department, local body, category, and road owner.",
        "Municipal, PWD, panchayat, NHAI, cantonment and other ownership is not inferred.",
        "Jansunwai requires an interactive user flow; Pothole Reporter does not submit a complaint automatically.",
    ),
    inside_fixtures={
        "lucknow": (26.8467, 80.9462), "kanpur": (26.4499, 80.3319),
        "varanasi": (25.3176, 82.9739), "agra": (27.1767, 78.0081),
        "gorakhpur": (26.7606, 83.3732), "jhansi": (25.4484, 78.5685),
        "saharanpur": (29.9680, 77.5552), "ghaziabad": (28.6692, 77.4538),
    },
    outside_fixtures={
        "new-delhi": (28.6139, 77.2090), "dehradun": (30.3165, 78.0322),
        "patna": (25.5941, 85.1376), "jaipur": (26.9124, 75.7873),
        "gwalior": (26.2183, 78.1828),
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
