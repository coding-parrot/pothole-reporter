#!/usr/bin/env python3
"""Immutable, offline-only detector contracts used by committed archive receipts.

Nothing in this module is a production selector. Current execution continues to read the
shipped native/Web contract. These constants exist only so historical, content-free receipts
can be authenticated without pretending that their old model output is current evidence.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


class HistoricalContractError(RuntimeError):
    """The checked-in historical contract no longer matches its immutable seal."""


V15_PROMPT = """You are a strict binary pothole detector for a civic complaint app. Inspect the supplied road views in chronological order and return only the structured fields in the schema. False positives are more harmful than false negatives: ambiguous geometry is NO.

A pothole is a localized wheel-dropping depression caused by missing, displaced, or disintegrated road-surface material. It does not need to be round, deep, dark, fully enclosed, or surrounded by intact pavement.

Return is_pothole true only when all are visible:
1. The damaged footprint lies on the surface the recording vehicle is actually traversing.
2. The footprint is lower than the adjacent wheel path and has localized material loss.
3. At least one side has an irregular broken lip, eroded edge, or abrupt material-height drop.
4. With multiple views, the same lower footprint moves or grows predictably as the vehicle approaches.
5. It is not an intentional raised speed breaker, hump, or rumble strip.

"Localized" means a stable, limited footprint within the traffic path; it does not mean a closed circular rim. On an unfinished, gravel-covered, failed, or construction-stage traffic lane, a jagged eroded depression or connected cavity cluster is YES when it occupies only part of the wheel path, is visibly lower than the adjacent path, and keeps at least one abrupt lip across the approach. Loose rubble within or beside that lower footprint and one boundary blending into surrounding failed material do not turn it into general roughness. A water-filled depression is YES when its stable edge and lower opening remain visible; the floor need not be visible. Do not reject such a feature merely because the rest of the lane is also rough.

A shallow pothole is still YES when an irregular, bounded area is visibly concave or sunken relative to the surrounding traffic surface and that geometry persists through the approach. A worn or rounded eroded lip, exposed aggregate, or a stable material-height transition is sufficient; do not demand a steep wall, deep dark interior, or sharp rim. A flat patch remains NO: colour, texture, or a repair outline without a concave centre or surface-height loss is not a pothole.

General gravel texture, road-wide grading, corrugation, broad roughness with no local lower footprint, a wheel rut with smooth sides, loose debris resting on a level surface, a stain, shadow, puddle with no visible depressed boundary, intact patch, crack, seam, manhole, drain, shoulder erosion, construction obstacle, or damage outside the wheel-traversed surface is NO. A darker or rougher strip at a paved-to-loose-material transition is also NO when no interior is visibly lower than both adjacent surfaces; persistence of that flat transition across views is not depth evidence.

Speed-breaker hard veto: set looks_like_speed_breaker true and is_pothole false whenever the feature is or could reasonably be a raised transverse ridge. Painted bands or rectangles, reflectors, parallel leading/trailing edges, a vehicle jolt, and camera pitch support a breaker. A separate cavity beside a breaker is YES only when clearly distinct from the raised ridge; raised-versus-concave ambiguity is NO.

Utility-reinstatement veto: a circular manhole or utility-cover ring, collar, rectangular trench patch, or linear reinstatement around an intact cover is NO even when its repair material is rough, cracked, or slightly sunken. Return YES only for a separate irregular wheel-dropping cavity that clearly extends beyond the utility repair footprint and independently satisfies every pothole condition.

Surface type:
- bituminous_asphalt, cement_concrete, mastic_asphalt, or paver_blocks when identifiable;
- temporary_drivable_surface for an unsealed or construction-stage path that the recording vehicle traverses. In a forward-facing Drive burst, coherent forward motion along a continuous wheel path proves this use even when no second vehicle is visible;
- unpaved_or_nonroad for a shoulder, construction bed, work area, service path, or roadside ground not being traversed; otherwise unknown.
unpaved_or_nonroad and unknown are always NO.

A cavity at a road edge may be YES when its opening removes part of the flat traffic surface or creates a wheel-reachable drop, even if rubble extends beneath a raised roadside slab. It is NO when an intact kerb or gutter separates the entire opening from traffic.

Set has_localized_cavity, has_broken_edge_or_rim, and has_depth_or_surface_loss true when the physical evidence above is present. Set image_quality unusable only when blur, darkness, glare, obstruction, or distance prevents a defensible judgment. For multiple views set temporal_consistency consistent when at least two show the same footprint; a feature leaving the final full frame is not disagreement. Use single_view for one user-framed photo.

After YES, set size to small below 30 cm, medium from 30 to 60 cm, or large above 60 cm or for a connected cavity cluster. For NO, size is null. These are visual app estimates, not official measurements. Keep description factual and never output confidence or probability."""

V15_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "is_pothole", "looks_like_speed_breaker", "image_quality", "surface_type",
        "on_drivable_surface", "has_localized_cavity", "has_broken_edge_or_rim",
        "has_depth_or_surface_loss", "temporal_consistency", "size", "description",
    ],
    "properties": {
        "is_pothole": {"type": "boolean"},
        "looks_like_speed_breaker": {"type": "boolean"},
        "image_quality": {"type": "string", "enum": ["usable", "unusable"]},
        "surface_type": {"type": "string", "enum": [
            "bituminous_asphalt", "cement_concrete", "mastic_asphalt", "paver_blocks",
            "temporary_drivable_surface", "unpaved_or_nonroad", "unknown",
        ]},
        "on_drivable_surface": {"type": "boolean"},
        "has_localized_cavity": {"type": "boolean"},
        "has_broken_edge_or_rim": {"type": "boolean"},
        "has_depth_or_surface_loss": {"type": "boolean"},
        "temporal_consistency": {"type": "string", "enum": [
            "consistent", "single_view", "inconsistent", "not_applicable",
        ]},
        "size": {"type": ["string", "null"],
                 "enum": ["small", "medium", "large", None]},
        "description": {"type": "string"},
    },
}

V15_EXPECTED_PROMPT_SHA256 = "621866cba94700717358426bba70b274c8f23df081ae2da7db6e9199c97e1c98"
V15_EXPECTED_SCHEMA_SHA256 = "4a71363f4a78f0da8af8cdc58301c688bd434308733664680a5f5ef59d14945a"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def v15_contract() -> dict[str, Any]:
    """Return a fresh copy so callers cannot mutate the archived source constants."""
    contract = {
        "model": "gpt-5.6",
        "detail": "original",
        "prompt_version": "pothole-binary-v15",
        "schema_version": 7,
        "max_output_tokens": 1536,
        "prompt": V15_PROMPT,
        "schema": json.loads(json.dumps(V15_SCHEMA)),
    }
    receipt = v15_contract_receipt()
    if (receipt["prompt_sha256"] != V15_EXPECTED_PROMPT_SHA256
            or receipt["schema_sha256"] != V15_EXPECTED_SCHEMA_SHA256):
        raise HistoricalContractError("immutable v15 prompt/schema seal has drifted")
    return contract


def v15_contract_receipt() -> dict[str, Any]:
    return {
        "model": "gpt-5.6",
        "detail": "original",
        "prompt_version": "pothole-binary-v15",
        "schema_version": 7,
        "max_output_tokens": 1536,
        "prompt_sha256": _sha256(V15_PROMPT.encode()),
        "schema_sha256": _sha256(_canonical_json(V15_SCHEMA).encode()),
    }


def v15_decision(result: dict[str, Any], mode: str = "drive",
                 source_view_count: int = 3) -> str:
    """Exact archived v15 binary decision, never used for current inference."""
    if not result or result.get("is_pothole") is not True:
        return "reject"
    if result.get("looks_like_speed_breaker") is not False:
        return "reject"
    surface_type = result.get("surface_type")
    if result.get("image_quality") != "usable" or surface_type not in {
            "bituminous_asphalt", "cement_concrete", "mastic_asphalt", "paver_blocks",
            "temporary_drivable_surface"}:
        return "reject"
    if (result.get("on_drivable_surface") is not True
            or result.get("has_localized_cavity") is not True
            or result.get("has_broken_edge_or_rim") is not True
            or result.get("has_depth_or_surface_loss") is not True):
        return "reject"
    if surface_type == "temporary_drivable_surface" and mode != "drive":
        return "reject"
    if mode == "drive":
        if result.get("temporal_consistency") != "consistent" or source_view_count < 2:
            return "reject"
    elif result.get("temporal_consistency") not in {"consistent", "single_view"}:
        return "reject"
    if result.get("size") not in {"small", "medium", "large"}:
        return "reject"
    return "accept"
