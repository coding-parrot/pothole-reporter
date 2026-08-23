#!/usr/bin/env python3
"""Pure-Python negative tests for the municipal release-pack contract."""

from __future__ import annotations

import copy
import json
import pathlib
import sys
from collections.abc import Callable
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from state_pack_tools import (  # noqa: E402
    PackError,
    SPECS,
    _authority_snapshot,
    _validate_raw_payload,
)


Mutation = Callable[[dict[str, Any], list[dict[str, Any]]], None]
MUNICIPAL_IDS = ("in-tn-routing", "in-tg-routing", "in-gj-routing")


def load_case(pack_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    spec = SPECS[pack_id]
    payload = json.loads((ROOT / spec.source_path).read_text(encoding="utf-8"))
    return payload, copy.deepcopy(_authority_snapshot(spec.state_code))


def validate(pack_id: str, payload: dict[str, Any], authorities: list[dict[str, Any]],
             generated_at: str) -> None:
    _validate_raw_payload(
        SPECS[pack_id],
        payload,
        generated_at=generated_at,
        authorities=authorities,
    )


def first_ring(region: dict[str, Any]) -> list[list[float]]:
    geometry = region["geometry"]
    if geometry["type"] == "Polygon":
        return geometry["coordinates"][0]
    return geometry["coordinates"][0][0]


def main() -> int:
    failures: list[str] = []
    for pack_id in MUNICIPAL_IDS:
        payload, authorities = load_case(pack_id)
        try:
            validate(pack_id, payload, authorities, payload["retrieved_at"])
        except PackError as error:
            failures.append(f"valid {pack_id} was rejected: {error}")

    cases: list[tuple[str, str, Mutation]] = [
        ("in-tn-routing", "extra payload field", lambda p, a: p.update({"extra": True})),
        ("in-tn-routing", "pack/source date mismatch", lambda p, a: p.update(
            {"retrieved_at": "2026-08-20"}
        )),
        ("in-tn-routing", "multiple regions", lambda p, a: p["regions"].append(
            copy.deepcopy(p["regions"][0])
        )),
        ("in-tn-routing", "extra region field", lambda p, a: p["regions"][0].update(
            {"unexpected": "field"}
        )),
        ("in-tn-routing", "missing region field", lambda p, a: p["regions"][0].pop("scope")),
        ("in-tn-routing", "unpinned scope", lambda p, a: p["regions"][0].update(
            {"scope": "all of Tamil Nadu"}
        )),
        ("in-tn-routing", "insecure source URL", lambda p, a: p["regions"][0].update(
            {"source_url": "http://example.invalid/source"}
        )),
        ("in-tn-routing", "empty alias inventory", lambda p, a: p["regions"][0].update(
            {"place_aliases": []}
        )),
        ("in-tn-routing", "inverted relevance envelope", lambda p, a: p["regions"][0][
            "envelope"
        ].update({"min_lng": 81.0})),
        ("in-tn-routing", "missing limitations", lambda p, a: p["regions"][0].update(
            {"limitations": []}
        )),
        ("in-tn-routing", "unreviewed complaint package", lambda p, a: a[0].update(
            {"handoff_package": "example.unreviewed.app"}
        )),
        ("in-tn-routing", "non-integer coordinate precision", lambda p, a: p["regions"][0].update(
            {"coordinate_precision": 7.0}
        )),
        ("in-tn-routing", "unsafe reviewed area", lambda p, a: p["regions"][0].update(
            {"area_km2": 1}
        )),
        ("in-tn-routing", "geometry digest mismatch", lambda p, a: p["regions"][0].update(
            {"geometry_sha256": "0" * 64}
        )),
        ("in-tn-routing", "declared bbox mismatch", lambda p, a: p["regions"][0]["bbox"].update(
            {"max_lng": 80.34}
        )),
        ("in-tn-routing", "unclosed polygon ring", lambda p, a: first_ring(p["regions"][0]).__setitem__(
            -1, [80.2, 13.0]
        )),
        ("in-tn-routing", "out-of-range coordinate", lambda p, a: first_ring(
            p["regions"][0]
        ).__setitem__(1, [181.0, 13.0])),
        ("in-tg-routing", "missing Cantonment exclusion", lambda p, a: p["regions"][0].update(
            {"exclusions": []}
        )),
        ("in-tg-routing", "exclusion outside envelope", lambda p, a: p["regions"][0][
            "exclusions"
        ][0]["bbox"].update({"max_lng": 79.0})),
        ("in-tg-routing", "insecure exclusion source", lambda p, a: p["regions"][0][
            "exclusions"
        ][0].update({"source_url": "http://example.invalid/cantonment"})),
        ("in-tg-routing", "CURE query uses intersects", lambda p, a: p["regions"][0].update(
            {"query_spatial_rel": "esriSpatialRelIntersects"}
        )),
        ("in-tg-routing", "insecure CURE query", lambda p, a: p["regions"][0].update(
            {"query_url": "http://example.invalid/query"}
        )),
        ("in-tg-routing", "Cantonment query does not intersect", lambda p, a: p["regions"][0][
            "exclusions"
        ][0].update({"query_spatial_rel": "esriSpatialRelWithin"})),
        ("in-tg-routing", "official query redistributes geometry", lambda p, a: p["regions"][0].update(
            {"geometry": {"type": "Polygon", "coordinates": []}}
        )),
        ("in-tg-routing", "missing legal reference", lambda p, a: p["regions"][0].update(
            {"legal_references": p["regions"][0]["legal_references"][:1]}
        )),
        ("in-gj-routing", "invalid dissolved geometry", lambda p, a: p["regions"][0].update(
            {"geometry": {"type": "Polygon", "coordinates": []}}
        )),
        ("in-gj-routing", "unreviewed source object", lambda p, a: p["regions"][0].update(
            {"source_object_id": "osm:relation:1"}
        )),
    ]

    for pack_id, label, mutate in cases:
        payload, authorities = load_case(pack_id)
        generated_at = payload["retrieved_at"]
        mutate(payload, authorities)
        try:
            validate(pack_id, payload, authorities, generated_at)
        except PackError:
            continue
        failures.append(f"{pack_id} accepted malformed case: {label}")

    if failures:
        print("Municipal state-pack validation failures:", file=sys.stderr)
        for failure in failures:
            print(f" - {failure}", file=sys.stderr)
        return 1
    print(f"municipal state-pack validation OK ({len(cases)} malformed cases refused)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
