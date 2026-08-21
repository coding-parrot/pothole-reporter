#!/usr/bin/env python3
"""Build and verify the client-downloadable, content-addressed state packs."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOCS_ROOT = PROJECT_ROOT / "docs"
STATIC_MANIFEST = PROJECT_ROOT / "static" / "pack-manifest.json"
ANDROID_MANIFEST = PROJECT_ROOT / "android-app" / "www" / "pack-manifest.json"
PAGES_MANIFEST = DOCS_ROOT / "pack-manifest.json"
AUTHORITIES_SOURCE = PROJECT_ROOT / "data" / "state-authorities.json"
PUBLIC_BASE_URL = "https://coding-parrot.github.io/pothole-reporter/"
PACK_FORMAT = "pothole-pack-manifest"
PACK_SCHEMA_VERSION = 1
CATALOG_VERSION = 1
PACK_VERSION = 1
REVIEW_AFTER = "2026-11-21"
RESOURCE_REVIEW_AFTER = {
    # Hyderabad's 2026 three-corporation reorganisation and Ahmedabad's lack of a
    # reusable current polygon deserve a much shorter re-review interval.
    "in-tg-routing": "2026-09-21",
    "in-gj-routing": "2026-09-21",
}
MAX_PACK_BYTES = 16 * 1024 * 1024
MANIFEST_KEYS = {"format", "schema_version", "catalog_version", "cache", "resources"}
CACHE_POLICY = {
    "max_bytes": 67_108_864,
    "routing_max_unused_days": 90,
    "tender_max_unused_days": 30,
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class ResourceSpec:
    pack_id: str
    state_code: str
    kind: str
    adapter: str
    coverage_scope: str
    statewide: bool
    licenses: tuple[str, ...]
    source_path: str


SPECS = {
    "in-dl-routing": ResourceSpec(
        "in-dl-routing",
        "DL",
        "routing",
        "delhi-nct-v1",
        "Delhi NCT",
        True,
        ("OpenStreetMap data: ODbL 1.0",),
        "static/delhi-coverage.json",
    ),
    "in-mh-routing": ResourceSpec(
        "in-mh-routing",
        "MH",
        "routing",
        "maharashtra-mmr-pmc-v1",
        "Mumbai Metropolitan Region and Pune Municipal Corporation",
        False,
        (
            "OpenStreetMap data: ODbL 1.0",
            "Official Maharashtra GIS and public-authority sources: respective source terms",
        ),
        "static/maharashtra-coverage.json",
    ),
    "in-wb-routing": ResourceSpec(
        "in-wb-routing",
        "WB",
        "routing",
        "kolkata-kmc-v1",
        "Kolkata Municipal Corporation",
        False,
        ("Official West Bengal UDMA and KMC sources: respective source terms",),
        "static/kolkata-coverage.json",
    ),
    "in-ka-routing": ResourceSpec(
        "in-ka-routing",
        "KA",
        "routing",
        "karnataka-kgis-v1",
        "Karnataka urban local bodies",
        True,
        ("Official Karnataka public-body records: respective source terms",),
        "data/karnataka-bodies.json",
    ),
    "in-ka-tenders": ResourceSpec(
        "in-ka-tenders",
        "KA",
        "tenders",
        "karnataka-locally-indexed-v1",
        "Karnataka municipal procurement records",
        True,
        ("Official Karnataka procurement records: respective source terms",),
        "data/tenders-karnataka.json",
    ),
    "in-tn-routing": ResourceSpec(
        "in-tn-routing",
        "TN",
        "routing",
        "municipal-city-v1",
        "Greater Chennai Corporation limits",
        False,
        ("OpenStreetMap data: ODbL 1.0",),
        "data/metro-coverage/tn.json",
    ),
    "in-tg-routing": ResourceSpec(
        "in-tg-routing",
        "TG",
        "routing",
        "municipal-city-v1",
        "Hyderabad core; shared CURE grievance intake; partial coverage",
        False,
        ("OpenStreetMap data: ODbL 1.0",),
        "data/metro-coverage/tg.json",
    ),
    "in-gj-routing": ResourceSpec(
        "in-gj-routing",
        "GJ",
        "routing",
        "municipal-city-v1",
        "Ahmedabad structured city matches; not a municipal-boundary claim",
        False,
        ("OpenStreetMap data: ODbL 1.0",),
        "data/metro-coverage/gj.json",
    ),
}
REQUIRED_RESOURCE_IDS = set(SPECS)

MUNICIPAL_PAYLOAD_KEYS = {"version", "retrieved_at", "regions"}
MUNICIPAL_COMMON_REGION_KEYS = {
    "id", "authority_id", "name", "scope", "routing_mode", "routing_source",
    "match_value", "state_aliases", "place_aliases", "envelope", "source_name",
    "source_home_url", "source_url", "source_license", "attribution",
    "official_scope_reference", "routing_note", "limitations", "exclusions",
    "source_object_id",
}
MUNICIPAL_BOUNDARY_REGION_KEYS = {
    "coordinate_precision", "area_km2", "bbox", "geometry_sha256", "geometry",
}
MUNICIPAL_STRING_FIELDS = {
    "id", "authority_id", "name", "scope", "routing_source", "match_value",
    "source_name", "source_home_url", "source_url", "source_license", "attribution",
    "official_scope_reference", "routing_note", "source_object_id",
}
MUNICIPAL_ENVELOPE_KEYS = {"min_lng", "min_lat", "max_lng", "max_lat"}
MUNICIPAL_EXCLUSION_KEYS = {
    "id", "name", "mode", "bbox", "source_name", "source_url", "routing_note",
}
MUNICIPAL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,100}$")
HTTPS_RE = re.compile(r"^https://\S+$")

# These are reviewed release facts, not defaults. A source refresh, boundary change,
# expanded city claim, or complaint-channel change must update the corresponding pin
# deliberately instead of becoming trusted merely because a new pack hash was built.
MUNICIPAL_CITY_PINS: dict[str, dict[str, Any]] = {
    "in-tn-routing": {
        "id": "chennai-gcc",
        "authority_id": "tn-gcc",
        "name": "Greater Chennai Corporation",
        "scope": "Greater Chennai Corporation only; not the wider Chennai Metropolitan Area",
        "routing_mode": "boundary",
        "routing_source": "osm_gcc_boundary",
        "match_value": "OpenStreetMap relation 1766358",
        "state_aliases": ["tamil nadu", "tamilnadu", "தமிழ்நாடு"],
        "place_aliases": ["chennai", "madras", "சென்னை"],
        "envelope": {
            "min_lng": 80.05,
            "min_lat": 12.75,
            "max_lng": 80.4,
            "max_lat": 13.3,
        },
        "source_name": "OpenStreetMap contributors",
        "source_home_url": "https://www.openstreetmap.org/relation/1766358",
        "source_url": (
            "https://nominatim.openstreetmap.org/lookup?osm_ids=R1766358&format=jsonv2"
            "&polygon_geojson=1&polygon_threshold=0.00001"
        ),
        "source_license": "Open Data Commons Open Database License (ODbL) 1.0",
        "attribution": "© OpenStreetMap contributors",
        "official_scope_reference": (
            "https://gisgcc.chennaicorporation.gov.in/server/rest/services/"
            "GCCDepts/EDPMobile2025/FeatureServer/1"
        ),
        "routing_note": (
            "ODbL coverage boundary validated against official GCC zone fixtures; "
            "it does not prove road ownership."
        ),
        "limitations": [
            "The official GCC GIS has no affirmative reuse licence and is not redistributed.",
            "Ports, airports, highways, institutional and other roads may have a different maintainer.",
        ],
        "exclusions": [],
        "source_object_id": "osm:relation:1766358",
        "coordinate_precision": 7,
        "area_km2": 433.098,
        "bbox": {
            "min_lng": 80.1401875,
            "min_lat": 12.8519771,
            "max_lng": 80.3328982,
            "max_lat": 13.235158,
        },
        "geometry_sha256": "88f13a9949f34b9c7aa9973db2b7f00659839ef3d434454208314d4479cd6cd5",
    },
    "in-tg-routing": {
        "id": "hyderabad-cure-core",
        "authority_id": "tg-cure-shared",
        "name": "Hyderabad CURE core coverage",
        "scope": "Partial Hyderabad core only; shared My Cure intake, without per-corporation attribution",
        "routing_mode": "boundary",
        "routing_source": "osm_hyderabad_core_boundary",
        "match_value": "OpenStreetMap relation 7868535",
        "state_aliases": ["telangana", "తెలంగాణ"],
        "place_aliases": ["hyderabad", "secunderabad", "హైదరాబాద్", "హైదరాబాదు"],
        "envelope": {
            "min_lng": 78.15,
            "min_lat": 17.2,
            "max_lng": 78.7,
            "max_lat": 17.65,
        },
        "source_name": "OpenStreetMap contributors",
        "source_home_url": "https://www.openstreetmap.org/relation/7868535",
        "source_url": (
            "https://nominatim.openstreetmap.org/lookup?osm_ids=R7868535&format=jsonv2"
            "&polygon_geojson=1&polygon_threshold=0.00001"
        ),
        "source_license": "Open Data Commons Open Database License (ODbL) 1.0",
        "attribution": "© OpenStreetMap contributors",
        "official_scope_reference": "https://ipass.telangana.gov.in/Downloads.aspx",
        "routing_note": (
            "Coverage only. My Cure categorizes complaints across the 2026 GHMC, CMC and MMC "
            "structure; the app does not select one corporation."
        ),
        "limitations": [
            "No authoritative reusable 2026 three-corporation vector boundaries were publicly available.",
            "Coverage is partial and must not be read as the current GHMC, CMC or MMC boundary.",
            (
                "The full published Secunderabad Cantonment layer extent is conservatively refused, "
                "so some neighbouring civic points are also excluded."
            ),
            "NHAI, TG R&B, HMDA, airport, private and other roads can have a different maintainer.",
        ],
        "exclusions": [
            {
                "id": "secunderabad-cantonment-extent",
                "name": "Secunderabad Cantonment conservative exclusion",
                "mode": "bbox",
                "bbox": {
                    "min_lng": 78.459155005,
                    "min_lat": 17.443033296,
                    "max_lng": 78.539634302,
                    "max_lat": 17.54038243,
                },
                "source_name": "Telangana Remote Sensing Applications Centre (TGRAC)",
                "source_url": (
                    "https://tgrac.telangana.gov.in/arcgis/rest/services/"
                    "Hydra_Folder/Administrative_Layer/MapServer/1"
                ),
                "routing_note": (
                    "The complete official layer extent is refused; "
                    "no unlicensed Cantonment polygon is redistributed."
                ),
            }
        ],
        "source_object_id": "osm:relation:7868535",
        "coordinate_precision": 7,
        "area_km2": 610.897,
        "bbox": {
            "min_lng": 78.2387067,
            "min_lat": 17.2916377,
            "max_lng": 78.6223912,
            "max_lat": 17.5608321,
        },
        "geometry_sha256": "6d5ef9edbf927d4037a104d12fe490630b979fbbcbfcfd948550b1d93217de31",
    },
    "in-gj-routing": {
        "id": "ahmedabad-structured",
        "authority_id": "gj-amc",
        "name": "Ahmedabad structured city coverage",
        "scope": (
            "Exact Ahmedabad/Amdavad structured address matches inside a reviewed relevance "
            "envelope; not a municipal-boundary claim"
        ),
        "routing_mode": "structured_geocode",
        "routing_source": "nominatim_structured_city",
        "match_value": "Nominatim structured city/municipality Ahmedabad",
        "state_aliases": ["gujarat", "ગુજરાત"],
        "place_aliases": ["ahmedabad", "amdavad", "અમદાવાદ", "अहमदाबाद"],
        "envelope": {
            "min_lng": 72.4200568,
            "min_lat": 22.8615374,
            "max_lng": 72.7400568,
            "max_lat": 23.1815374,
        },
        "source_name": "OpenStreetMap contributors via Nominatim",
        "source_home_url": "https://www.openstreetmap.org/node/245711197",
        "source_url": (
            "https://nominatim.openstreetmap.org/search?city=Ahmedabad&state=Gujarat"
            "&country=India&format=jsonv2&polygon_geojson=1&addressdetails=1&limit=10"
        ),
        "source_license": "Open Data Commons Open Database License (ODbL) 1.0",
        "attribution": "© OpenStreetMap contributors",
        "official_scope_reference": "https://www.amccrs.com/AMCPortal/View/AMCDetail.aspx",
        "routing_note": (
            "No current reusable AMC polygon was found. Exact structured fields gate an editable "
            "CCRS handoff and never assert road ownership."
        ),
        "limitations": [
            "This is not point-in-polygon municipal containment.",
            "A missing or conflicting structured city/state field fails closed.",
            "AMC's public GIS is stale, lacks a reuse licence and is not bundled.",
        ],
        "exclusions": [],
        "source_object_id": "osm:node:245711197",
    },
}

MUNICIPAL_AUTHORITY_PINS: dict[str, list[dict[str, Any]]] = {
    "in-tn-routing": [
        {
            "id": "tn-gcc",
            "name": "Greater Chennai Corporation complaint intake",
            "aliases": [
                "chennai",
                "madras",
                "greater chennai corporation",
                "சென்னை",
                "சென்னை மாநகராட்சி",
            ],
            "handoff_name": "GCC Public Grievance",
            "handoff_url": "https://erp.chennaicorporation.gov.in/pgr/citizen/BeforeReg.do",
            "handoff_package": "com.ceedeev.grivenancev2",
            "alternate_handoff_name": "GCC grievance portal",
            "alternate_handoff_url": "https://erp.chennaicorporation.gov.in/pgr/",
            "whatsapp_url": "https://wa.me/919445061913",
            "helpline": "1913",
        }
    ],
    "in-tg-routing": [
        {
            "id": "tg-cure-shared",
            "name": "Hyderabad CURE shared civic grievance intake",
            "aliases": [
                "hyderabad", "greater hyderabad", "హైదరాబాద్", "హైదరాబాదు",
            ],
            "handoff_name": "My Cure",
            "handoff_url": "https://igs.ghmc.gov.in/operator/send_otp_mobile",
            "handoff_package": "cgg.gov.ghmc",
            "alternate_handoff_name": "My Cure complaint status",
            "alternate_handoff_url": "https://igs.ghmc.gov.in/Operator/search",
        }
    ],
    "in-gj-routing": [
        {
            "id": "gj-amc",
            "name": "Amdavad Municipal Corporation complaint intake",
            "aliases": ["ahmedabad", "amdavad", "અમદાવાદ", "अहमदाबाद"],
            "handoff_name": "AMC CCRS",
            "handoff_url": (
                "https://www.amccrs.com/AMCPortal/View/ComplaintRegistration.aspx?m=Online"
            ),
            "handoff_package": "com.amplvb.ccrs",
            "alternate_handoff_name": "AMC CCRS instructions",
            "alternate_handoff_url": (
                "https://www.amccrs.com/AMCPortal/View/ComplainRegistrationMobile.aspx"
            ),
            "whatsapp_url": "https://wa.me/917567855303",
            "helpline": "155303",
        }
    ],
}


class PackError(RuntimeError):
    """Raised when a pack or manifest violates the release contract."""


def _compact_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def _manifest_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PackError(f"missing required file: {path.relative_to(PROJECT_ROOT)}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PackError(f"invalid UTF-8 JSON: {path.relative_to(PROJECT_ROOT)}: {exc}") from exc


def _write_if_changed(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() == content:
        return
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _is_date(value: Any) -> bool:
    if not isinstance(value, str) or DATE_RE.fullmatch(value) is None:
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _authority_snapshot(state_code: str) -> list[dict[str, Any]]:
    if state_code == "KA":
        return []
    source = _read_json(AUTHORITIES_SOURCE)
    state = source.get(state_code) if isinstance(source, dict) else None
    if not isinstance(state, dict) or not isinstance(state.get("authorities"), list):
        raise PackError(f"state-authorities.json has no authority list for {state_code}")
    authorities = list(state["authorities"])
    if state_code == "MH":
        for key in ("pmc", "fallback"):
            if not isinstance(state.get(key), dict):
                raise PackError(f"state-authorities.json has no MH {key} authority")
            authorities.append(state[key])
    identifiers = [entry.get("id") for entry in authorities if isinstance(entry, dict)]
    if len(identifiers) != len(authorities) or any(not value for value in identifiers):
        raise PackError(f"{state_code} authority snapshot contains an invalid entry")
    if len(identifiers) != len(set(identifiers)):
        raise PackError(f"{state_code} authority snapshot contains duplicate ids")
    return authorities


def _source_date(payload: Any, fallback: str | None = None) -> str:
    if isinstance(payload, dict):
        candidate = payload.get("retrieved_at") or payload.get("generated_at") or payload.get("generated")
        if _is_date(candidate):
            return candidate
    if _is_date(fallback):
        return fallback
    raise PackError("source payload has no deterministic YYYY-MM-DD retrieval/generated date")


def _is_finite_number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _validate_municipal_envelope(value: Any, label: str) -> None:
    _expect(
        isinstance(value, dict) and set(value) == MUNICIPAL_ENVELOPE_KEYS,
        f"{label} must contain exactly {sorted(MUNICIPAL_ENVELOPE_KEYS)}",
    )
    for field in ("min_lng", "max_lng"):
        _expect(
            _is_finite_number(value[field]) and -180 <= value[field] <= 180,
            f"{label}.{field} must be a finite longitude",
        )
    for field in ("min_lat", "max_lat"):
        _expect(
            _is_finite_number(value[field]) and -90 <= value[field] <= 90,
            f"{label}.{field} must be a finite latitude",
        )
    _expect(value["min_lng"] < value["max_lng"], f"{label} longitude range is empty")
    _expect(value["min_lat"] < value["max_lat"], f"{label} latitude range is empty")


def _validate_municipal_aliases(value: Any, label: str) -> None:
    _expect(isinstance(value, list) and 1 <= len(value) <= 30,
            f"{label} must contain between 1 and 30 aliases")
    _expect(
        all(isinstance(item, str) and item and len(item) <= 100 for item in value),
        f"{label} contains an invalid alias",
    )


def _municipal_geometry_bounds(geometry: dict[str, Any]) -> dict[str, float]:
    bounds = {
        "min_lng": math.inf,
        "min_lat": math.inf,
        "max_lng": -math.inf,
        "max_lat": -math.inf,
    }

    def visit(value: Any) -> None:
        if (
            isinstance(value, list)
            and len(value) == 2
            and _is_finite_number(value[0])
            and _is_finite_number(value[1])
        ):
            bounds["min_lng"] = min(bounds["min_lng"], value[0])
            bounds["min_lat"] = min(bounds["min_lat"], value[1])
            bounds["max_lng"] = max(bounds["max_lng"], value[0])
            bounds["max_lat"] = max(bounds["max_lat"], value[1])
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(geometry["coordinates"])
    _expect(all(math.isfinite(value) for value in bounds.values()),
            "municipal geometry has no finite coordinates")
    return bounds


def _validate_municipal_geometry(geometry: Any, label: str) -> dict[str, float]:
    _expect(
        isinstance(geometry, dict) and set(geometry) == {"type", "coordinates"},
        f"{label} must be a GeoJSON Polygon or MultiPolygon with no extra fields",
    )

    def valid_position(position: Any) -> bool:
        return (
            isinstance(position, list)
            and len(position) == 2
            and _is_finite_number(position[0])
            and _is_finite_number(position[1])
            and -180 <= position[0] <= 180
            and -90 <= position[1] <= 90
        )

    def valid_ring(ring: Any) -> bool:
        return (
            isinstance(ring, list)
            and len(ring) >= 4
            and all(valid_position(position) for position in ring)
            and ring[0][0] == ring[-1][0]
            and ring[0][1] == ring[-1][1]
        )

    def valid_polygon(coordinates: Any) -> bool:
        return (
            isinstance(coordinates, list)
            and bool(coordinates)
            and all(valid_ring(ring) for ring in coordinates)
        )

    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    valid = valid_polygon(coordinates) if geometry_type == "Polygon" else (
        isinstance(coordinates, list)
        and bool(coordinates)
        and all(valid_polygon(polygon) for polygon in coordinates)
        if geometry_type == "MultiPolygon"
        else False
    )
    _expect(valid, f"{label} has malformed polygon coordinates")
    return _municipal_geometry_bounds(geometry)


def _validate_municipal_authorities(spec: ResourceSpec, authorities: Any) -> None:
    expected = MUNICIPAL_AUTHORITY_PINS.get(spec.pack_id)
    _expect(expected is not None, f"{spec.pack_id} has no reviewed authority pin")
    _expect(
        authorities == expected,
        f"{spec.pack_id} complaint authority differs from its reviewed release pin",
    )


def _validate_municipal_city_payload(
    spec: ResourceSpec,
    payload: Any,
    *,
    generated_at: str | None = None,
    authorities: Any = None,
) -> None:
    pin = MUNICIPAL_CITY_PINS.get(spec.pack_id)
    _expect(pin is not None, f"{spec.pack_id} has no reviewed municipal-city pin")
    _expect(
        isinstance(payload, dict) and set(payload) == MUNICIPAL_PAYLOAD_KEYS,
        f"{spec.pack_id} municipal payload fields differ from the contract",
    )
    _expect(type(payload.get("version")) is int and payload["version"] == 1,
            f"{spec.pack_id} municipal payload version must be 1")
    retrieved_at = payload.get("retrieved_at")
    _expect(_is_date(retrieved_at), f"{spec.pack_id} municipal retrieved_at is invalid")
    if generated_at is not None:
        _expect(retrieved_at == generated_at,
                f"{spec.pack_id} municipal retrieved_at differs from the pack date")
    regions = payload.get("regions")
    _expect(isinstance(regions, list) and len(regions) == 1,
            f"{spec.pack_id} must contain exactly one reviewed municipal region")
    region = regions[0]
    _expect(isinstance(region, dict), f"{spec.pack_id} municipal region must be an object")

    routing_mode = region.get("routing_mode")
    _expect(routing_mode in {"boundary", "structured_geocode"},
            f"{spec.pack_id} municipal routing_mode is invalid")
    expected_fields = set(MUNICIPAL_COMMON_REGION_KEYS)
    if routing_mode == "boundary":
        expected_fields.update(MUNICIPAL_BOUNDARY_REGION_KEYS)
    _expect(set(region) == expected_fields,
            f"{spec.pack_id} municipal region fields differ from the {routing_mode} contract")

    _expect(
        isinstance(region.get("id"), str) and MUNICIPAL_ID_RE.fullmatch(region["id"]) is not None,
        f"{spec.pack_id} municipal region id is invalid",
    )
    authority_id = region.get("authority_id")
    authority_pattern = re.compile(rf"^{spec.state_code.lower()}-[a-z0-9-]{{2,80}}$")
    _expect(
        isinstance(authority_id, str) and authority_pattern.fullmatch(authority_id) is not None,
        f"{spec.pack_id} municipal authority id is invalid",
    )
    for field in MUNICIPAL_STRING_FIELDS:
        value = region.get(field)
        _expect(
            isinstance(value, str) and bool(value) and len(value) <= 1000,
            f"{spec.pack_id} municipal {field} is invalid",
        )
    for field in ("source_home_url", "source_url", "official_scope_reference"):
        _expect(HTTPS_RE.fullmatch(region[field]) is not None,
                f"{spec.pack_id} municipal {field} must be HTTPS")
    _validate_municipal_aliases(region.get("state_aliases"),
                                f"{spec.pack_id}.state_aliases")
    _validate_municipal_aliases(region.get("place_aliases"),
                                f"{spec.pack_id}.place_aliases")
    _validate_municipal_envelope(region.get("envelope"), f"{spec.pack_id}.envelope")

    limitations = region.get("limitations")
    _expect(isinstance(limitations, list) and 1 <= len(limitations) <= 10,
            f"{spec.pack_id} must contain between 1 and 10 limitations")
    _expect(
        all(isinstance(item, str) and item and len(item) <= 500 for item in limitations),
        f"{spec.pack_id} contains an invalid limitation",
    )
    exclusions = region.get("exclusions")
    _expect(isinstance(exclusions, list) and len(exclusions) <= 10,
            f"{spec.pack_id} exclusions must be a list of at most 10 entries")
    exclusion_ids: set[str] = set()
    for index, exclusion in enumerate(exclusions):
        label = f"{spec.pack_id}.exclusions[{index}]"
        _expect(isinstance(exclusion, dict) and set(exclusion) == MUNICIPAL_EXCLUSION_KEYS,
                f"{label} fields differ from the contract")
        exclusion_id = exclusion.get("id")
        _expect(
            isinstance(exclusion_id, str)
            and MUNICIPAL_ID_RE.fullmatch(exclusion_id) is not None
            and exclusion_id not in exclusion_ids,
            f"{label} id is invalid or duplicated",
        )
        exclusion_ids.add(exclusion_id)
        _expect(exclusion.get("mode") == "bbox", f"{label} mode must be bbox")
        for field in ("name", "source_name", "routing_note"):
            value = exclusion.get(field)
            _expect(isinstance(value, str) and value and len(value) <= 500,
                    f"{label}.{field} is invalid")
        source_url = exclusion.get("source_url")
        _expect(isinstance(source_url, str) and HTTPS_RE.fullmatch(source_url) is not None,
                f"{label}.source_url must be HTTPS")
        _validate_municipal_envelope(exclusion.get("bbox"), f"{label}.bbox")
        bbox = exclusion["bbox"]
        envelope = region["envelope"]
        _expect(
            bbox["min_lng"] >= envelope["min_lng"]
            and bbox["min_lat"] >= envelope["min_lat"]
            and bbox["max_lng"] <= envelope["max_lng"]
            and bbox["max_lat"] <= envelope["max_lat"],
            f"{label}.bbox falls outside the municipal relevance envelope",
        )

    for field, expected in pin.items():
        _expect(region.get(field) == expected,
                f"{spec.pack_id} municipal {field} differs from its reviewed release pin")

    if routing_mode == "boundary":
        _expect(type(region.get("coordinate_precision")) is int
                and region["coordinate_precision"] == 7,
                f"{spec.pack_id} coordinate_precision must be the integer 7")
        area = region.get("area_km2")
        _expect(_is_finite_number(area) and 1 < area <= 10_000,
                f"{spec.pack_id} area_km2 is outside the runtime range")
        _validate_municipal_envelope(region.get("bbox"), f"{spec.pack_id}.bbox")
        calculated = _validate_municipal_geometry(
            region.get("geometry"), f"{spec.pack_id}.geometry"
        )
        bbox = region["bbox"]
        _expect(
            all(abs(calculated[field] - bbox[field]) <= 1e-7 for field in calculated),
            f"{spec.pack_id} geometry bounds differ from its declared bbox",
        )
        envelope = region["envelope"]
        _expect(
            calculated["min_lng"] >= envelope["min_lng"]
            and calculated["min_lat"] >= envelope["min_lat"]
            and calculated["max_lng"] <= envelope["max_lng"]
            and calculated["max_lat"] <= envelope["max_lat"],
            f"{spec.pack_id} geometry falls outside the municipal relevance envelope",
        )
        geometry_digest = hashlib.sha256(
            json.dumps(
                region["geometry"], ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        _expect(
            isinstance(region.get("geometry_sha256"), str)
            and SHA256_RE.fullmatch(region["geometry_sha256"]) is not None
            and geometry_digest == region["geometry_sha256"],
            f"{spec.pack_id} geometry SHA-256 is invalid",
        )

    if authorities is not None:
        _validate_municipal_authorities(spec, authorities)
        authority_ids = [authority["id"] for authority in authorities]
        _expect(authority_ids == [region["authority_id"]],
                f"{spec.pack_id} municipal authority use is incomplete or duplicated")


def _validate_raw_payload(
    spec: ResourceSpec,
    payload: Any,
    *,
    generated_at: str | None = None,
    authorities: Any = None,
) -> None:
    if spec.kind == "tenders":
        if not isinstance(payload, list) or not payload or len(payload) > 100_000:
            raise PackError(f"{spec.pack_id} must contain a non-empty tender list")
        fields = {"tn", "t", "loc", "c", "d", "b"}
        seen: set[tuple[str, str]] = set()
        for index, row in enumerate(payload):
            if not isinstance(row, dict) or set(row) != fields:
                raise PackError(f"{spec.pack_id} tender {index} has unexpected fields")
            if (
                not isinstance(row["tn"], str) or not row["tn"] or len(row["tn"]) > 100
                or not isinstance(row["t"], str) or not row["t"] or len(row["t"]) > 500
                or not isinstance(row["loc"], str) or len(row["loc"]) > 200
                or not isinstance(row["c"], str) or len(row["c"]) > 200
                or not isinstance(row["d"], str) or re.fullmatch(r"\d{2}-\d{2}-\d{4}", row["d"]) is None
                or not isinstance(row["b"], str) or re.fullmatch(r"(?:BLR|\d{3,12})", row["b"]) is None
            ):
                raise PackError(f"{spec.pack_id} tender {index} is invalid")
            identity = (row["tn"], row["b"])
            if identity in seen:
                raise PackError(f"{spec.pack_id} contains duplicate tender/body record {identity!r}")
            seen.add(identity)
        return
    if not isinstance(payload, dict):
        raise PackError(f"{spec.pack_id} routing payload must be an object")
    if spec.pack_id == "in-ka-routing":
        if not isinstance(payload.get("bodies"), dict) or not payload["bodies"]:
            raise PackError("in-ka-routing payload has no bodies")
    elif spec.pack_id == "in-mh-routing":
        if not isinstance(payload.get("regions"), dict) or not payload["regions"]:
            raise PackError("in-mh-routing payload has no regions")
    elif spec.adapter == "municipal-city-v1":
        _validate_municipal_city_payload(
            spec,
            payload,
            generated_at=generated_at,
            authorities=authorities,
        )
    elif not isinstance(payload.get("region"), dict):
        raise PackError(f"{spec.pack_id} payload has no region")


def _pack_envelope(spec: ResourceSpec, payload: Any, generated_at: str) -> dict[str, Any]:
    authorities = _authority_snapshot(spec.state_code) if spec.kind == "routing" else None
    _validate_raw_payload(
        spec,
        payload,
        generated_at=generated_at,
        authorities=authorities,
    )
    common = {
        "format": "pothole-routing-pack" if spec.kind == "routing" else "pothole-tender-pack",
        "schema_version": PACK_SCHEMA_VERSION,
        "pack_id": spec.pack_id,
        "pack_version": PACK_VERSION,
        "state_code": spec.state_code,
        "adapter": spec.adapter,
        "generated_at": generated_at,
    }
    if spec.kind == "routing":
        common["authorities"] = authorities
        common["payload"] = payload
    else:
        common["tenders"] = payload
    return common


def _base_manifest() -> dict[str, Any]:
    return {
        "format": PACK_FORMAT,
        "schema_version": PACK_SCHEMA_VERSION,
        "catalog_version": CATALOG_VERSION,
        "cache": dict(CACHE_POLICY),
        "resources": {},
    }


def _manifest_for_update() -> dict[str, Any]:
    if not STATIC_MANIFEST.exists():
        raise PackError("no pack manifest exists; run tools/build-state-packs.py first")
    manifest = _read_json(STATIC_MANIFEST)
    if not isinstance(manifest, dict) or set(manifest) != MANIFEST_KEYS:
        raise PackError("existing pack manifest top-level fields differ from the contract")
    if (
        manifest.get("format") != PACK_FORMAT
        or manifest.get("schema_version") != PACK_SCHEMA_VERSION
        or manifest.get("catalog_version") != CATALOG_VERSION
        or manifest.get("cache") != CACHE_POLICY
    ):
        raise PackError("existing pack manifest metadata differs from the contract")
    resources = manifest.get("resources")
    if not isinstance(resources, dict) or set(resources) != REQUIRED_RESOURCE_IDS:
        raise PackError("existing pack manifest must contain the complete resource catalog")
    return manifest


def _resource_entry(spec: ResourceSpec, pack_bytes: bytes, source_date: str) -> tuple[dict[str, Any], Path]:
    if not pack_bytes or len(pack_bytes) > MAX_PACK_BYTES:
        raise PackError(f"{spec.pack_id} exceeds the {MAX_PACK_BYTES}-byte runtime limit")
    digest = hashlib.sha256(pack_bytes).hexdigest()
    path = f"packs/v1/states/{spec.state_code.lower()}/{spec.kind}-{digest}.json"
    entry: dict[str, Any] = {
        "pack_id": spec.pack_id,
        "state_code": spec.state_code,
        "kind": spec.kind,
        "pack_version": PACK_VERSION,
        "schema_version": PACK_SCHEMA_VERSION,
        "adapter": spec.adapter,
        "path": path,
        "url": PUBLIC_BASE_URL + path,
        "bytes": len(pack_bytes),
        "sha256": digest,
        "coverage_scope": spec.coverage_scope,
        "statewide": spec.statewide,
        "source_retrieved_at": source_date,
        "review_after": RESOURCE_REVIEW_AFTER.get(spec.pack_id, REVIEW_AFTER),
        "licenses": list(spec.licenses),
    }
    if spec.kind == "tenders":
        entry["records"] = len(json.loads(pack_bytes)["tenders"])
    return entry, DOCS_ROOT / path


def publish_resource(
    resource_id: str,
    payload: Any,
    *,
    source_retrieved_at: str | None = None,
    manifest: dict[str, Any] | None = None,
    write_manifest: bool = True,
) -> tuple[dict[str, Any], Path]:
    """Publish one immutable pack and update every deployed manifest mirror."""
    if resource_id not in SPECS:
        raise PackError(f"unknown resource id: {resource_id}")
    spec = SPECS[resource_id]
    source_date = _source_date(payload, source_retrieved_at)
    pack_bytes = _compact_json(_pack_envelope(spec, payload, source_date))
    entry, output = _resource_entry(spec, pack_bytes, source_date)
    if output.exists() and output.read_bytes() != pack_bytes:
        raise PackError(f"content-address collision: {output.relative_to(PROJECT_ROOT)}")
    _write_if_changed(output, pack_bytes)

    if manifest is None:
        manifest = _manifest_for_update()
    resources = manifest.get("resources") if isinstance(manifest, dict) else None
    if not isinstance(resources, dict):
        raise PackError("pack manifest resources must be an object")
    resources[resource_id] = entry
    if write_manifest:
        serialized = _manifest_json(manifest)
        _write_if_changed(STATIC_MANIFEST, serialized)
        _write_if_changed(ANDROID_MANIFEST, serialized)
        _write_if_changed(PAGES_MANIFEST, serialized)
        verify_all()
    return manifest, output


def _active_payload(previous_manifest: Any, resource_id: str) -> Any:
    """Recover a routing payload from the active envelope after legacy inputs are removed."""
    resources = previous_manifest.get("resources") if isinstance(previous_manifest, dict) else None
    resource = resources.get(resource_id) if isinstance(resources, dict) else None
    relative_path = resource.get("path") if isinstance(resource, dict) else None
    if not isinstance(relative_path, str):
        raise PackError(f"no canonical source or active pack is available for {resource_id}")
    spec = SPECS[resource_id]
    expected = re.compile(
        rf"^packs/v1/states/{spec.state_code.lower()}/{spec.kind}-[0-9a-f]{{64}}\.json$"
    )
    if expected.fullmatch(relative_path) is None:
        raise PackError(f"active pack path is invalid for {resource_id}")
    envelope = _read_json(DOCS_ROOT / relative_path)
    if not isinstance(envelope, dict) or "payload" not in envelope:
        raise PackError(f"active pack for {resource_id} has no routing payload")
    return envelope["payload"]


def build_all() -> list[Path]:
    """Build every pack from the reviewed canonical source snapshots."""
    previous_manifest = _read_json(STATIC_MANIFEST) if STATIC_MANIFEST.exists() else None
    manifest = _base_manifest()
    outputs: list[Path] = []
    for resource_id in sorted(SPECS):
        spec = SPECS[resource_id]
        source = PROJECT_ROOT / spec.source_path
        payload = _read_json(source) if source.exists() else _active_payload(previous_manifest, resource_id)
        previous_resources = previous_manifest.get("resources") if isinstance(previous_manifest, dict) else None
        previous_resource = (
            previous_resources.get(resource_id, {}) if isinstance(previous_resources, dict) else {}
        )
        fallback = (
            previous_resource.get("source_retrieved_at", "2026-08-21")
            if spec.kind == "tenders"
            else None
        )
        if spec.kind == "tenders":
            payload = [row for row in payload if isinstance(row, dict) and row.get("b")]
        manifest, output = publish_resource(
            resource_id,
            payload,
            source_retrieved_at=fallback,
            manifest=manifest,
            write_manifest=False,
        )
        outputs.append(output)
    serialized = _manifest_json(manifest)
    _write_if_changed(STATIC_MANIFEST, serialized)
    _write_if_changed(ANDROID_MANIFEST, serialized)
    _write_if_changed(PAGES_MANIFEST, serialized)
    verify_all()
    return outputs


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise PackError(message)


def _validate_resource(resource_id: str, resource: Any) -> None:
    spec = SPECS[resource_id]
    required = {
        "pack_id", "state_code", "kind", "pack_version", "schema_version", "adapter",
        "path", "url", "bytes", "sha256", "coverage_scope", "statewide",
        "source_retrieved_at", "review_after", "licenses",
    }
    if spec.kind == "tenders":
        required.add("records")
    _expect(isinstance(resource, dict), f"{resource_id}: manifest resource is not an object")
    _expect(set(resource) == required, f"{resource_id}: manifest fields differ from the contract")
    expected_scalars = {
        "pack_id": spec.pack_id,
        "state_code": spec.state_code,
        "kind": spec.kind,
        "pack_version": PACK_VERSION,
        "schema_version": PACK_SCHEMA_VERSION,
        "adapter": spec.adapter,
        "coverage_scope": spec.coverage_scope,
        "statewide": spec.statewide,
        "licenses": list(spec.licenses),
    }
    for key, expected in expected_scalars.items():
        _expect(resource.get(key) == expected, f"{resource_id}: unexpected {key}")
    digest = resource.get("sha256")
    _expect(isinstance(digest, str) and SHA256_RE.fullmatch(digest) is not None,
            f"{resource_id}: sha256 must be 64 lowercase hex characters")
    expected_path = f"packs/v1/states/{spec.state_code.lower()}/{spec.kind}-{digest}.json"
    _expect(resource.get("path") == expected_path, f"{resource_id}: path is not content-addressed")
    _expect(resource.get("url") == PUBLIC_BASE_URL + expected_path,
            f"{resource_id}: URL is not the exact production GitHub Pages URL")
    _expect(type(resource.get("bytes")) is int and resource["bytes"] > 0,
            f"{resource_id}: bytes must be a positive integer")
    _expect(resource["bytes"] <= MAX_PACK_BYTES, f"{resource_id}: pack exceeds the runtime size limit")
    source_date = resource.get("source_retrieved_at")
    _expect(_is_date(source_date), f"{resource_id}: invalid source_retrieved_at")
    _expect(resource.get("review_after") == RESOURCE_REVIEW_AFTER.get(resource_id, REVIEW_AFTER),
            f"{resource_id}: unexpected review_after")

    pack_path = DOCS_ROOT / expected_path
    try:
        resolved = pack_path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise PackError(f"{resource_id}: hosted pack is missing: {expected_path}") from exc
    states_root = (DOCS_ROOT / "packs" / "v1" / "states").resolve()
    _expect(states_root in resolved.parents, f"{resource_id}: hosted path escapes the pack directory")
    pack_bytes = pack_path.read_bytes()
    _expect(resource.get("bytes") == len(pack_bytes), f"{resource_id}: byte length does not match")
    _expect(hashlib.sha256(pack_bytes).hexdigest() == digest, f"{resource_id}: SHA-256 does not match")
    try:
        envelope = json.loads(pack_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PackError(f"{resource_id}: hosted pack is not valid UTF-8 JSON: {exc}") from exc
    expected_envelope_keys = {
        "format", "schema_version", "pack_id", "pack_version", "state_code", "adapter", "generated_at",
        "authorities", "payload",
    } if spec.kind == "routing" else {
        "format", "schema_version", "pack_id", "pack_version", "state_code", "adapter", "generated_at",
        "tenders",
    }
    _expect(isinstance(envelope, dict) and set(envelope) == expected_envelope_keys,
            f"{resource_id}: pack envelope fields differ from the contract")
    expected_format = "pothole-routing-pack" if spec.kind == "routing" else "pothole-tender-pack"
    for key, expected in {
        "format": expected_format,
        "schema_version": PACK_SCHEMA_VERSION,
        "pack_id": spec.pack_id,
        "pack_version": PACK_VERSION,
        "state_code": spec.state_code,
        "adapter": spec.adapter,
        "generated_at": source_date,
    }.items():
        _expect(envelope.get(key) == expected, f"{resource_id}: pack has unexpected {key}")
    if spec.kind == "routing":
        authorities = envelope.get("authorities")
        _expect(authorities == _authority_snapshot(spec.state_code),
                f"{resource_id}: authority snapshot differs from data/state-authorities.json")
        _validate_raw_payload(
            spec,
            envelope.get("payload"),
            generated_at=source_date,
            authorities=authorities,
        )
    else:
        tenders = envelope.get("tenders")
        _validate_raw_payload(spec, tenders)
        _expect(type(resource.get("records")) is int and resource["records"] > 0,
                f"{resource_id}: records must be a positive integer")
        _expect(resource.get("records") == len(tenders), f"{resource_id}: records count does not match")


def verify_all() -> None:
    """Fail unless the manifests and all referenced hosted packs match exactly."""
    manifest_bytes = STATIC_MANIFEST.read_bytes() if STATIC_MANIFEST.exists() else b""
    if not manifest_bytes:
        raise PackError("missing bundled manifest: static/pack-manifest.json")
    _expect(ANDROID_MANIFEST.exists(), "missing Android manifest mirror: android-app/www/pack-manifest.json")
    _expect(ANDROID_MANIFEST.read_bytes() == manifest_bytes, "static and Android pack manifests differ")
    _expect(PAGES_MANIFEST.exists(), "missing Pages manifest mirror: docs/pack-manifest.json")
    _expect(PAGES_MANIFEST.read_bytes() == manifest_bytes, "static and Pages pack manifests differ")
    manifest = _read_json(STATIC_MANIFEST)
    _expect(isinstance(manifest, dict) and set(manifest) == MANIFEST_KEYS,
            "pack manifest top-level fields differ from the contract")
    _expect(manifest.get("format") == PACK_FORMAT, "unexpected pack manifest format")
    _expect(manifest.get("schema_version") == PACK_SCHEMA_VERSION, "unexpected manifest schema_version")
    _expect(manifest.get("catalog_version") == CATALOG_VERSION, "unexpected manifest catalog_version")
    _expect(manifest.get("cache") == CACHE_POLICY, "unexpected manifest cache policy")
    resources = manifest.get("resources")
    _expect(isinstance(resources, dict) and set(resources) == REQUIRED_RESOURCE_IDS,
            f"manifest must contain exactly the {len(REQUIRED_RESOURCE_IDS)} reviewed resource ids")
    for resource_id in sorted(REQUIRED_RESOURCE_IDS):
        _validate_resource(resource_id, resources[resource_id])
