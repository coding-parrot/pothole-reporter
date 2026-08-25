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

from tender_scope import is_road_surface_contract


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOCS_ROOT = PROJECT_ROOT / "docs"
STATIC_MANIFEST = PROJECT_ROOT / "static" / "pack-manifest-v1.34.json"
ANDROID_MANIFEST = PROJECT_ROOT / "android-app" / "www" / "pack-manifest-v1.34.json"
PAGES_MANIFEST = DOCS_ROOT / "pack-manifest-v1.34.json"
PREVIOUS_STATIC_MANIFEST = PROJECT_ROOT / "static" / "pack-manifest-v1.33.json"
AUTHORITIES_SOURCE = PROJECT_ROOT / "data" / "state-authorities.json"
PUBLIC_BASE_URL = "https://coding-parrot.github.io/pothole-reporter/"
PACK_FORMAT = "pothole-pack-manifest"
PACK_SCHEMA_VERSION = 1
CATALOG_VERSION = 1
PACK_VERSION = 1
REVIEW_AFTER = "2026-11-21"
RESOURCE_REVIEW_AFTER = {
    # Hyderabad's 2026 three-corporation reorganisation and Ahmedabad's secondary
    # ward-boundary snapshot deserve a much shorter re-review interval.
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
MAHARASHTRA_STATE_GEOMETRY_SHA256 = (
    "1f5555fede30d19d58ffafabb7d38c8cba0af7b27f7c7129d10480351a0304ce"
)
WEST_BENGAL_STATE_GEOMETRY_SHA256 = (
    "aa4ab13c3064be2e168889f6eb02e87c59e01bc709d36b66bece534dfea23015"
)
PUNJAB_STATE_GEOMETRY_SHA256 = (
    "e113eb774f4f353d3c7a9c98830f4b665f9bd4d166ed3b84e90855bdf38f5782"
)
TAMIL_NADU_STATE_GEOMETRY_SHA256 = (
    "b3034527326b1120366adaf4b7c3df4bd0b8c7aab4d82b28e3dde189b39c313e"
)
ANDHRA_PRADESH_STATE_GEOMETRY_SHA256 = (
    "4e36d9c16fda044dceab7a5b08955cb19046bb1bddd052b7671a8311e90cd71c"
)
TELANGANA_STATE_GEOMETRY_SHA256 = (
    "77183815e4b698ec1e823f4a94a6f213d1d827ea35de8fec8c0ab3b6a9d15175"
)
KARNATAKA_STATE_GEOMETRY_SHA256 = (
    "9d7fe3f01a80cb41712c09139efcd43e0e11a644849d5f3bffe125cc0bc1c5ad"
)
KERALA_STATE_GEOMETRY_SHA256 = (
    "51e226750b1d6c08a5030e6074e2641282e01c328f46d8aee741de664bef705c"
)
UTTAR_PRADESH_STATE_GEOMETRY_SHA256 = (
    "2dbb5237cab5eb029f517c1d79451663c1fc49affe0e0789b11f0565180db015"
)
CHHATTISGARH_STATE_GEOMETRY_SHA256 = (
    "827e89a598571ade84db77390bca5daf98c9f67fbae716b17193f4ccdc2876eb"
)
RAJASTHAN_STATE_GEOMETRY_SHA256 = (
    "dcde670675d0fc50e292c6b306b1f80d9d68a1323250c29d6eddc97992491a36"
)
GOA_STATE_GEOMETRY_SHA256 = (
    "f4c47a79a3671d333d47f66a597d66b6295a78b1cd7cd3cba7bc2db472190e4f"
)
MADHYA_PRADESH_STATE_GEOMETRY_SHA256 = (
    "24f0c93ed8bd40c4c6b4e1f650c3b9870b1e65ccd5d7b00ea0193a8a5aedc357"
)
BIHAR_STATE_GEOMETRY_SHA256 = (
    "3d846e20cfee28a656d6dd808c4dad37a4f1c95852f9f292b0acefde708f4b24"
)
ODISHA_STATE_GEOMETRY_SHA256 = (
    "af0fe4941b6cdd2abe5dc5717db8875bec6b68a2d6671002d2afc9c7d37d5179"
)
KMC_GEOMETRY_SHA256 = (
    "fa9e157d8cdc8d918dd934a77a5dcde375d3108598412cb8ca3e19ca2d916bf5"
)
MAHARASHTRA_STATE_REGION_KEYS = {
    "name", "scope", "authority_id", "source", "source_lookup",
    "source_relation_id", "retrieved_at", "licence", "coordinate_precision",
    "bbox", "geometry_sha256", "routing_note", "limitations", "geometry",
}
WEST_BENGAL_STATE_REGION_KEYS = set(MAHARASHTRA_STATE_REGION_KEYS)
PUNJAB_STATE_REGION_KEYS = {
    "id", "authority_id", "name", "scope", "osm_relation_id",
    "source_name", "source_home_url", "source_url", "source_license",
    "attribution", "routing_note", "limitations", "coordinate_precision",
    "bbox", "geometry_sha256", "geometry",
}
TAMIL_NADU_STATE_REGION_KEYS = set(PUNJAB_STATE_REGION_KEYS)
ANDHRA_PRADESH_STATE_REGION_KEYS = set(PUNJAB_STATE_REGION_KEYS)
TELANGANA_STATE_REGION_KEYS = set(PUNJAB_STATE_REGION_KEYS)
KARNATAKA_STATE_REGION_KEYS = set(PUNJAB_STATE_REGION_KEYS)
KERALA_STATE_REGION_KEYS = set(PUNJAB_STATE_REGION_KEYS)
UTTAR_PRADESH_STATE_REGION_KEYS = set(PUNJAB_STATE_REGION_KEYS)
CHHATTISGARH_STATE_REGION_KEYS = set(PUNJAB_STATE_REGION_KEYS)
RAJASTHAN_STATE_REGION_KEYS = set(PUNJAB_STATE_REGION_KEYS)
GOA_STATE_REGION_KEYS = set(PUNJAB_STATE_REGION_KEYS)
MADHYA_PRADESH_STATE_REGION_KEYS = set(PUNJAB_STATE_REGION_KEYS)
BIHAR_STATE_REGION_KEYS = set(PUNJAB_STATE_REGION_KEYS)
ODISHA_STATE_REGION_KEYS = set(PUNJAB_STATE_REGION_KEYS)
KMC_REGION_KEYS = {
    "authority_id", "authority_name", "scope", "ulb_code", "mun_id",
    "retrieved_at",
    "source_feature_id", "source_name", "source_home_url", "source_url",
    "source_filter", "source_access", "attribution", "repair",
    "repaired_source_sha256", "area_km2", "geometry_sha256", "geometry",
}


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
        "maharashtra-statewide-v1",
        "Full State of Maharashtra; exact MMR and Pune Municipal Corporation routes where verified",
        True,
        (
            "OpenStreetMap data: ODbL 1.0",
            "Official Maharashtra GIS and public-authority sources: respective source terms",
        ),
        "data/metro-coverage/mh.json",
    ),
    "in-wb-routing": ResourceSpec(
        "in-wb-routing",
        "WB",
        "routing",
        "west-bengal-statewide-v1",
        "Full State of West Bengal; exact Kolkata Municipal Corporation route where verified",
        True,
        (
            "OpenStreetMap data: ODbL 1.0",
            "Official West Bengal UDMA and KMC sources: respective source terms",
        ),
        "data/metro-coverage/wb.json",
    ),
    "in-pb-routing": ResourceSpec(
        "in-pb-routing",
        "PB",
        "routing",
        "statewide-general-v1",
        "Full State of Punjab; neutral Connect Punjab grievance handoff",
        True,
        (
            "OpenStreetMap data: ODbL 1.0",
            "Official Punjab grievance sources: respective source terms",
        ),
        "data/metro-coverage/pb.json",
    ),
    "in-tn-state-routing": ResourceSpec(
        "in-tn-state-routing",
        "TN",
        "routing",
        "statewide-general-v1",
        (
            "Full State of Tamil Nadu; neutral Mudhalvarin Mugavari grievance "
            "handoff; excludes Puducherry Union Territory"
        ),
        True,
        (
            "OpenStreetMap data: ODbL 1.0",
            "Official Tamil Nadu grievance sources: respective source terms",
        ),
        "data/metro-coverage/tn-state.json",
    ),
    "in-ap-routing": ResourceSpec(
        "in-ap-routing",
        "AP",
        "routing",
        "statewide-general-v1",
        (
            "Full State of Andhra Pradesh; neutral PGRS grievance handoff; "
            "excludes Yanam, Puducherry Union Territory"
        ),
        True,
        (
            "OpenStreetMap data: ODbL 1.0",
            "Official Andhra Pradesh grievance sources: respective source terms",
        ),
        "data/metro-coverage/ap-state.json",
    ),
    "in-tg-state-routing": ResourceSpec(
        "in-tg-state-routing",
        "TG",
        "routing",
        "statewide-general-v1",
        (
            "Full State of Telangana; neutral Prajavani grievance handoff; "
            "exact Hyderabad CURE route remains more specific"
        ),
        True,
        (
            "OpenStreetMap data: ODbL 1.0",
            "Official Telangana grievance sources: respective source terms",
        ),
        "data/metro-coverage/tg-state.json",
    ),
    "in-ka-state-routing": ResourceSpec(
        "in-ka-state-routing",
        "KA",
        "routing",
        "statewide-general-v1",
        (
            "Full State of Karnataka; neutral Janaspandana grievance handoff; "
            "exact KGIS urban-body routes remain more specific"
        ),
        True,
        (
            "OpenStreetMap data: ODbL 1.0",
            "Official Karnataka grievance sources: respective source terms",
        ),
        "data/metro-coverage/ka-state.json",
    ),
    "in-kl-routing": ResourceSpec(
        "in-kl-routing",
        "KL",
        "routing",
        "statewide-general-v1",
        (
            "Full State of Kerala; neutral CMO grievance handoff with K-SMART "
            "local-body alternative; excludes Mahe, Puducherry Union Territory"
        ),
        True,
        (
            "OpenStreetMap data: ODbL 1.0",
            "Official Kerala grievance sources: respective source terms",
        ),
        "data/metro-coverage/kl-state.json",
    ),
    "in-up-routing": ResourceSpec(
        "in-up-routing",
        "UP",
        "routing",
        "statewide-general-v1",
        (
            "Full State of Uttar Pradesh; neutral Jansunwai grievance handoff; "
            "excludes Delhi National Capital Territory"
        ),
        True,
        (
            "OpenStreetMap data: ODbL 1.0",
            "Official Uttar Pradesh grievance sources: respective source terms",
        ),
        "data/metro-coverage/up-state.json",
    ),
    "in-cg-routing": ResourceSpec(
        "in-cg-routing",
        "CG",
        "routing",
        "statewide-general-v1",
        (
            "Full State of Chhattisgarh; neutral CM Helpline grievance handoff "
            "with NIDAAN 1100 as an urban civic alternative"
        ),
        True,
        (
            "OpenStreetMap data: ODbL 1.0",
            "Official Chhattisgarh grievance sources: respective source terms",
        ),
        "data/metro-coverage/cg-state.json",
    ),
    "in-rj-routing": ResourceSpec(
        "in-rj-routing",
        "RJ",
        "routing",
        "statewide-general-v1",
        "Full State of Rajasthan; neutral Rajasthan Sampark grievance handoff",
        True,
        (
            "OpenStreetMap data: ODbL 1.0",
            "Official Rajasthan grievance sources: respective source terms",
        ),
        "data/metro-coverage/rj-state.json",
    ),
    "in-ga-routing": ResourceSpec(
        "in-ga-routing",
        "GA",
        "routing",
        "statewide-general-v1",
        "Full State of Goa; neutral CM Helpline grievance handoff",
        True,
        (
            "OpenStreetMap data: ODbL 1.0",
            "Official Goa grievance sources: respective source terms",
        ),
        "data/metro-coverage/ga-state.json",
    ),
    "in-mp-routing": ResourceSpec(
        "in-mp-routing",
        "MP",
        "routing",
        "statewide-general-v1",
        "Full State of Madhya Pradesh; neutral CM Helpline grievance handoff",
        True,
        (
            "OpenStreetMap data: ODbL 1.0",
            "Official Madhya Pradesh grievance sources: respective source terms",
        ),
        "data/metro-coverage/mp-state.json",
    ),
    "in-br-routing": ResourceSpec(
        "in-br-routing",
        "BR",
        "routing",
        "statewide-general-v1",
        "Full State of Bihar; neutral Lok Shikayat grievance handoff",
        True,
        (
            "OpenStreetMap data: ODbL 1.0",
            "Official Bihar grievance sources: respective source terms",
        ),
        "data/metro-coverage/br-state.json",
    ),
    "in-od-routing": ResourceSpec(
        "in-od-routing",
        "OD",
        "routing",
        "statewide-general-v1",
        "Full State of Odisha; neutral Jana Sunani grievance handoff",
        True,
        (
            "OpenStreetMap data: ODbL 1.0",
            "Official Odisha grievance sources: respective source terms",
        ),
        "data/metro-coverage/od-state.json",
    ),
    "in-top50-routing": ResourceSpec(
        "in-top50-routing",
        "IN",
        "routing",
        "major-city-structured-v1",
        "35 additional Census 2011 top-50 population centres; conservative structured city/state matching",
        False,
        (
            "OpenStreetMap data: ODbL 1.0",
            "Official state and urban grievance sources: respective source terms",
        ),
        "data/metro-coverage/top50.json",
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
        "karnataka-carriageway-indexed-v2",
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
        "Official Hyderabad 2,053 km² CURE point-query coverage; shared grievance intake",
        False,
        ("Official TGRAC public query service; no boundary geometry redistributed",),
        "data/metro-coverage/tg.json",
    ),
    "in-gj-routing": ResourceSpec(
        "in-gj-routing",
        "GJ",
        "routing",
        "municipal-city-v1",
        "Reviewed Ahmedabad 48-ward footprint; excludes wider AUDA",
        False,
        ("OpenCity / Oorvani Foundation data via Bharatlas: ODbL 1.0",),
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
MUNICIPAL_OFFICIAL_POINT_REGION_KEYS = {
    "query_url", "query_where", "query_geometry_type", "query_in_sr",
    "query_spatial_rel", "official_area_km2", "legal_references",
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
MUNICIPAL_POINT_EXCLUSION_KEYS = MUNICIPAL_EXCLUSION_KEYS | {
    "query_url", "query_where", "query_geometry_type", "query_in_sr",
    "query_spatial_rel", "source_object_id",
}
MUNICIPAL_LEGAL_REFERENCE_KEYS = {"title", "date", "url"}
MUNICIPAL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,100}$")
HTTPS_RE = re.compile(r"^https://\S+$")
TOP50_PAYLOAD_KEYS = {"version", "retrieved_at", "regions"}
TOP50_REGION_KEYS = {
    "rank", "id", "authority_id", "name", "state_code", "scope",
    "routing_mode", "routing_source", "match_value", "state_aliases",
    "place_aliases", "envelope", "source_name", "source_home_url",
    "source_url", "source_license", "attribution", "official_scope_reference",
    "routing_note", "limitations", "exclusions", "source_object_id",
    "supported_issue_types",
}
TOP50_SUPPORTED_ISSUES = ["road_damage", "garbage", "open_manhole"]
TOP50_REGION_PINS = [
    (9, "surat", "GJ", "in-gj-enagar", "osm:node:10029899747"),
    (10, "jaipur", "RJ", "in-rj-sampark", "osm:node:315734346"),
    (11, "kanpur", "UP", "in-up-jansunwai", "osm:node:1180652177"),
    (12, "lucknow", "UP", "in-up-jansunwai", "osm:node:245753718"),
    (14, "ghaziabad", "UP", "in-up-jansunwai", "osm:node:2521085873"),
    (15, "indore", "MP", "in-mp-cm-helpline", "osm:node:245709027"),
    (16, "coimbatore", "TN", "in-tn-cm-helpline", "osm:node:245589078"),
    (17, "kochi", "KL", "in-kl-ksmart", "osm:node:3862624198"),
    (18, "patna", "BR", "in-br-lok-shikayat", "osm:way:383774533"),
    (19, "kozhikode", "KL", "in-kl-ksmart", "osm:node:1348192542"),
    (20, "bhopal", "MP", "in-mp-cm-helpline", "osm:node:245712627"),
    (21, "thrissur", "KL", "in-kl-ksmart", "osm:node:4430328343"),
    (22, "vadodara", "GJ", "in-gj-enagar", "osm:node:2022807192"),
    (23, "agra", "UP", "in-up-jansunwai", "osm:node:567267943"),
    (24, "visakhapatnam", "AP", "in-ap-puramithra", "osm:node:245641840"),
    (25, "malappuram", "KL", "in-kl-ksmart", "osm:way:84635269"),
    (26, "thiruvananthapuram", "KL", "in-kl-ksmart", "osm:node:245581432"),
    (27, "kannur", "KL", "in-kl-ksmart", "osm:node:290180981"),
    (30, "vijayawada", "AP", "in-ap-puramithra", "osm:node:1880441437"),
    (31, "madurai", "TN", "in-tn-cm-helpline", "osm:relation:11268397"),
    (32, "varanasi", "UP", "in-up-jansunwai", "osm:node:287687798"),
    (33, "meerut", "UP", "in-up-jansunwai", "osm:node:571773704"),
    (34, "faridabad", "HR", "in-hr-nagar-darshan", "osm:node:3582568815"),
    (35, "rajkot", "GJ", "in-gj-enagar", "osm:node:1393852189"),
    (36, "jamshedpur", "JH", "in-jh-municipal-grievance", "osm:node:566174729"),
    (37, "jabalpur", "MP", "in-mp-cm-helpline", "osm:relation:3832427"),
    (38, "srinagar", "JK", "in-jk-samadhan", "osm:node:273658993"),
    (41, "prayagraj", "UP", "in-up-jansunwai", "osm:node:245733956"),
    (42, "dhanbad", "JH", "in-jh-municipal-grievance", "osm:node:2516759396"),
    (45, "jodhpur", "RJ", "in-rj-sampark", "osm:way:31725312"),
    (46, "ranchi", "JH", "in-jh-municipal-grievance", "osm:node:2510123017"),
    (47, "raipur", "CG", "in-cg-nidaan", "osm:node:5308437250"),
    (48, "kollam", "KL", "in-kl-ksmart", "osm:node:245582090"),
    (49, "gwalior", "MP", "in-mp-cm-helpline", "osm:node:568412253"),
    (50, "durg-bhilai", "CG", "in-cg-nidaan", "osm:node:3105817661"),
]
TOP50_AUTHORITY_URL_PINS = {
    "in-gj-enagar": "https://enagar.gujarat.gov.in/enagar/login.jsp",
    "in-rj-sampark": "https://sampark.rajasthan.gov.in/",
    "in-up-jansunwai": "https://www.jansunwai.up.nic.in/",
    "in-mp-cm-helpline": "https://www.cmhelpline.mp.gov.in/",
    "in-tn-cm-helpline": "https://cmhelpline.tnega.org/portal/en/home",
    "in-kl-ksmart": "https://ksmart.lsgkerala.gov.in/ui/web-portal",
    "in-br-lok-shikayat": "https://lokshikayat.bihar.gov.in/",
    "in-ap-puramithra": "https://cdma.ap.gov.in/services/grievances/",
    "in-hr-nagar-darshan": "https://nagardarshan.ulbharyana.gov.in/Default/CitizenEntry",
    "in-jh-municipal-grievance": (
        "https://municipalservices.jharkhand.gov.in/public/grievance_new/login"
    ),
    "in-jk-samadhan": "https://samadhan.jk.gov.in/",
    "in-cg-nidaan": "https://crm.nidaan.cg.gov.in/",
}

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
        "id": "hyderabad-cure-2053",
        "authority_id": "tg-cure-shared",
        "name": "Hyderabad Core Urban Region official service coverage",
        "scope": (
            "Official 2,053 km² CURE point-query coverage; shared My Cure intake "
            "without per-corporation attribution"
        ),
        "routing_mode": "official_point_query",
        "routing_source": "tgrac_cure_2053_point_query",
        "match_value": (
            "TGRAC CURE layer 22; GPS-accuracy envelope within the official "
            "2,053 km² coverage"
        ),
        "state_aliases": ["telangana", "తెలంగాణ"],
        "place_aliases": ["hyderabad", "secunderabad", "హైదరాబాద్", "హైదరాబాదు"],
        "envelope": {
            "min_lng": 78.15,
            "min_lat": 17.1,
            "max_lng": 78.82,
            "max_lat": 17.72,
        },
        "source_name": "Telangana Remote Sensing Applications Centre (TGRAC)",
        "source_home_url": (
            "https://tgrac.telangana.gov.in/arcgis/rest/services/TCUR_Folder/"
            "TCUR_Telangana_Core_Urban_Region_V2/MapServer"
        ),
        "source_url": (
            "https://tgrac.telangana.gov.in/arcgis/rest/services/TCUR_Folder/"
            "TCUR_Telangana_Core_Urban_Region_V2/MapServer/22"
        ),
        "source_license": (
            "Official public query service; no boundary geometry is redistributed"
        ),
        "attribution": (
            "Telangana Remote Sensing Applications Centre (TGRAC), Government of Telangana"
        ),
        "official_scope_reference": (
            "https://tg-bn-website-assets.flowwlabs.tech/GOs-and-ACTs/"
            "GO.Ms.No.55_11-02-2026.pdf"
        ),
        "routing_note": (
            "The Android app asks the official TGRAC service whether the complete "
            "GPS-accuracy envelope is within CURE layer 22. G.O.Ms.No.292 reorganised "
            "the expanded area into 12 zones and 60 circles; G.O.Ms.No.55 later "
            "constituted three corporations. The app deliberately uses the shared My "
            "Cure intake instead of guessing one corporation."
        ),
        "limitations": [
            "A live response from the official TGRAC service is required; browser/PWA use and service failures fail closed.",
            "The shared My Cure handoff does not identify which of Greater Hyderabad, Cyberabad or Malkajgiri Municipal Corporation owns the issue.",
            "The exact official Secunderabad Cantonment layer is queried and any intersecting accuracy envelope is refused.",
            "NHAI, TG R&B, HMDA, airport, railway, defence, private and other roads may have a different maintainer.",
        ],
        "exclusions": [
            {
                "id": "secunderabad-cantonment",
                "name": "Secunderabad Cantonment official boundary",
                "mode": "official_point_query",
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
                    "The Android app refuses any GPS-accuracy envelope intersecting the "
                    "exact official layer; no Cantonment polygon is redistributed."
                ),
                "query_url": (
                    "https://tgrac.telangana.gov.in/arcgis/rest/services/"
                    "Hydra_Folder/Administrative_Layer/MapServer/1/query"
                ),
                "query_where": "1=1",
                "query_geometry_type": "esriGeometryEnvelope",
                "query_in_sr": 4326,
                "query_spatial_rel": "esriSpatialRelIntersects",
                "source_object_id": "tgrac:Hydra_Folder:Administrative_Layer:MapServer:1",
            }
        ],
        "source_object_id": (
            "tgrac:TCUR_Folder:TCUR_Telangana_Core_Urban_Region_V2:MapServer:22"
        ),
        "query_url": (
            "https://tgrac.telangana.gov.in/arcgis/rest/services/TCUR_Folder/"
            "TCUR_Telangana_Core_Urban_Region_V2/MapServer/22/query"
        ),
        "query_where": "1=1",
        "query_geometry_type": "esriGeometryEnvelope",
        "query_in_sr": 4326,
        "query_spatial_rel": "esriSpatialRelWithin",
        "official_area_km2": 2053,
        "legal_references": [
            {
                "title": (
                    "G.O.Ms.No.292, MA&UD (GHMC-1): reorganisation into 12 zones "
                    "and 60 circles"
                ),
                "date": "2025-12-24",
                "url": "https://goir.telangana.gov.in/",
            },
            {
                "title": (
                    "G.O.Ms.No.55, MA&UD (GHMC-1): constitution of three municipal "
                    "corporations"
                ),
                "date": "2026-02-11",
                "url": (
                    "https://tg-bn-website-assets.flowwlabs.tech/GOs-and-ACTs/"
                    "GO.Ms.No.55_11-02-2026.pdf"
                ),
            },
        ],
    },
    "in-gj-routing": {
        "id": "ahmedabad-amc",
        "authority_id": "gj-amc",
        "name": "Ahmedabad Municipal Corporation 48-ward coverage",
        "scope": "Reviewed Ahmedabad 48-ward coverage footprint; not the wider AUDA area",
        "routing_mode": "boundary",
        "routing_source": "opencity_amc_wards_union",
        "match_value": "OpenCity AMC 48-ward union, snapshot 2026-05-26",
        "state_aliases": ["gujarat", "ગુજરાત"],
        "place_aliases": ["ahmedabad", "amdavad", "અમદાવાદ", "अहमदाबाद"],
        "envelope": {
            "min_lng": 72.4,
            "min_lat": 22.85,
            "max_lng": 72.75,
            "max_lat": 23.2,
        },
        "source_name": "OpenCity / Oorvani Foundation via Bharatlas",
        "source_home_url": "https://bharatlas.com/view/wards_ahmedabad",
        "source_url": (
            "https://pub-0429b8e3b5a946e69ea007df844a6f1c.r2.dev/"
            "admin/wards-ahmedabad/wards_ahmedabad.geojson"
        ),
        "source_license": "Open Data Commons Open Database License (ODbL) 1.0",
        "attribution": "© OpenCity / Oorvani Foundation contributors",
        "official_scope_reference": "https://ahmedabadcity.gov.in/Home/AboutTheCorporation",
        "routing_note": (
            "The 48 reviewed ward polygons are dissolved into one coverage boundary. "
            "Containment supports AMC complaint intake and does not prove road ownership."
        ),
        "limitations": [
            (
                "The ward snapshot is an ODbL secondary-source copy, checked against AMC's "
                "current 48-ward inventory."
            ),
            (
                "The 439.397 km² union is not proven to include every current outer AMC "
                "expansion; AMC materials publish larger total areas."
            ),
            "AUDA and neighbouring municipal areas outside AMC are not covered.",
            "NHAI, state, railway, airport, private and other roads may have a different maintainer.",
        ],
        "exclusions": [],
        "source_object_id": "opencity:wards-ahmedabad:2026-05-26",
        "coordinate_precision": 7,
        "area_km2": 439.397,
        "bbox": {
            "min_lng": 72.4472434,
            "min_lat": 22.9121407,
            "max_lng": 72.7036946,
            "max_lat": 23.1386475,
        },
        "geometry_sha256": "48de18a521d2ece507ebda91976064353b477283a251bf45d271fdd0c7b82cb7",
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


def _authority_snapshot(spec: ResourceSpec) -> list[dict[str, Any]]:
    state_code = spec.state_code
    if spec.pack_id == "in-ka-routing":
        return []
    source = _read_json(AUTHORITIES_SOURCE)
    state = source.get(state_code) if isinstance(source, dict) else None
    if not isinstance(state, dict) or not isinstance(state.get("authorities"), list):
        raise PackError(f"state-authorities.json has no authority list for {state_code}")
    authorities = list(state["authorities"])
    if state_code == "TN":
        required_id = (
            "tn-statewide-unverified"
            if spec.pack_id == "in-tn-state-routing"
            else "tn-gcc"
        )
        authorities = [
            authority for authority in authorities
            if isinstance(authority, dict) and authority.get("id") == required_id
        ]
        if len(authorities) != 1:
            raise PackError(
                f"state-authorities.json has no unique TN authority for {spec.pack_id}"
            )
    if state_code == "TG":
        required_id = (
            "tg-statewide-unverified"
            if spec.pack_id == "in-tg-state-routing"
            else "tg-cure-shared"
        )
        authorities = [
            authority for authority in authorities
            if isinstance(authority, dict) and authority.get("id") == required_id
        ]
        if len(authorities) != 1:
            raise PackError(
                f"state-authorities.json has no unique TG authority for {spec.pack_id}"
            )
    if state_code == "MH":
        for key in ("pmc", "fallback", "statewide"):
            if not isinstance(state.get(key), dict):
                raise PackError(f"state-authorities.json has no MH {key} authority")
            authorities.append(state[key])
    if state_code == "WB":
        if not isinstance(state.get("statewide"), dict):
            raise PackError("state-authorities.json has no WB statewide authority")
        authorities.append(state["statewide"])
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


def _validate_maharashtra_payload(
    payload: Any,
    *,
    generated_at: str | None = None,
    authorities: Any = None,
) -> None:
    _expect(
        isinstance(payload, dict) and set(payload) == {"version", "retrieved_at", "regions"},
        "in-mh-routing payload fields differ from the statewide contract",
    )
    _expect(type(payload.get("version")) is int and payload["version"] == 2,
            "in-mh-routing payload version must be 2")
    retrieved_at = payload.get("retrieved_at")
    _expect(_is_date(retrieved_at), "in-mh-routing retrieved_at is invalid")
    if generated_at is not None:
        _expect(retrieved_at == generated_at,
                "in-mh-routing retrieved_at differs from the pack date")
    regions = payload.get("regions")
    _expect(
        isinstance(regions, dict) and set(regions) == {"maharashtra", "mmr", "pmc"},
        "in-mh-routing must contain the statewide, MMR and PMC regions",
    )
    _expect(isinstance(regions["mmr"], dict) and isinstance(regions["pmc"], dict),
            "in-mh-routing MMR or PMC region is invalid")
    _validate_municipal_geometry(regions["mmr"].get("geometry"), "in-mh-routing.mmr")
    _validate_municipal_geometry(regions["pmc"].get("geometry"), "in-mh-routing.pmc")

    state = regions["maharashtra"]
    _expect(isinstance(state, dict) and set(state) == MAHARASHTRA_STATE_REGION_KEYS,
            "in-mh-routing Maharashtra region fields differ from the contract")
    expected = {
        "name": "Maharashtra",
        "scope": "Full State of Maharashtra",
        "authority_id": "mh-statewide-unverified",
        "source": "https://www.openstreetmap.org/relation/1950884",
        "source_relation_id": 1_950_884,
        "licence": "OpenStreetMap contributors, ODbL 1.0",
        "coordinate_precision": 7,
        "geometry_sha256": MAHARASHTRA_STATE_GEOMETRY_SHA256,
    }
    for field, value in expected.items():
        _expect(state.get(field) == value,
                f"in-mh-routing Maharashtra {field} differs from its reviewed pin")
    _expect(state.get("retrieved_at") == retrieved_at,
            "in-mh-routing Maharashtra retrieval date differs from the payload")
    _expect(
        isinstance(state.get("source_lookup"), str)
        and state["source_lookup"].startswith("https://nominatim.openstreetmap.org/lookup?")
        and "osm_ids=R1950884" in state["source_lookup"],
        "in-mh-routing Maharashtra lookup URL is invalid",
    )
    _expect(isinstance(state.get("routing_note"), str) and state["routing_note"],
            "in-mh-routing Maharashtra routing note is missing")
    limitations = state.get("limitations")
    _expect(
        isinstance(limitations, list) and 1 <= len(limitations) <= 10
        and all(isinstance(item, str) and item and len(item) <= 500 for item in limitations),
        "in-mh-routing Maharashtra limitations are invalid",
    )
    bounds = _validate_municipal_geometry(
        state.get("geometry"), "in-mh-routing.maharashtra"
    )
    _validate_municipal_envelope(state.get("bbox"), "in-mh-routing.maharashtra.bbox")
    _expect(bounds == state["bbox"],
            "in-mh-routing Maharashtra geometry does not match its bounding box")
    digest = hashlib.sha256(json.dumps(
        state["geometry"], ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    _expect(digest == MAHARASHTRA_STATE_GEOMETRY_SHA256,
            "in-mh-routing Maharashtra geometry digest does not match")
    _expect(
        isinstance(authorities, list)
        and len(authorities) == 22
        and sum(item.get("id") == "mh-statewide-unverified"
                for item in authorities if isinstance(item, dict)) == 1,
        "in-mh-routing statewide authority registry is incomplete",
    )


def _validate_west_bengal_payload(
    payload: Any,
    *,
    generated_at: str | None = None,
    authorities: Any = None,
) -> None:
    _expect(
        isinstance(payload, dict) and set(payload) == {"version", "retrieved_at", "regions"},
        "in-wb-routing payload fields differ from the statewide contract",
    )
    _expect(type(payload.get("version")) is int and payload["version"] == 2,
            "in-wb-routing payload version must be 2")
    retrieved_at = payload.get("retrieved_at")
    _expect(_is_date(retrieved_at), "in-wb-routing retrieved_at is invalid")
    if generated_at is not None:
        _expect(retrieved_at == generated_at,
                "in-wb-routing retrieved_at differs from the pack date")
    regions = payload.get("regions")
    _expect(
        isinstance(regions, dict) and set(regions) == {"west_bengal", "kmc"},
        "in-wb-routing must contain the statewide and KMC regions",
    )

    kmc = regions["kmc"]
    _expect(isinstance(kmc, dict) and set(kmc) == KMC_REGION_KEYS,
            "in-wb-routing KMC region fields differ from the reviewed contract")
    expected_kmc = {
        "authority_id": "wb-kmc",
        "authority_name": "Kolkata Municipal Corporation",
        "ulb_code": "250299",
        "mun_id": "250299_0000001",
        "retrieved_at": "2026-08-21",
        "source_feature_id": "wb_municipal_boundary.250299_0000001",
        "source_home_url": "https://nagargispariseva.wb.gov.in/",
        "source_filter": "ULB_Code='250299'",
        "repaired_source_sha256": (
            "6a0bc369e6bd66cab1f9345d6effd7139ae6fb57fffc256ccb4579c4314b0562"
        ),
        "geometry_sha256": KMC_GEOMETRY_SHA256,
    }
    for field, value in expected_kmc.items():
        _expect(kmc.get(field) == value,
                f"in-wb-routing KMC {field} differs from its reviewed pin")
    _expect(
        isinstance(kmc.get("source_url"), str)
        and kmc["source_url"].startswith("https://nagargispariseva.wb.gov.in/")
        and "wb_municipal_boundary" in kmc["source_url"]
        and "250299" in kmc["source_url"],
        "in-wb-routing KMC source URL is invalid",
    )
    _expect(_is_finite_number(kmc.get("area_km2")) and 199 < kmc["area_km2"] < 201,
            "in-wb-routing KMC area differs materially from the reviewed source")
    _validate_municipal_geometry(kmc.get("geometry"), "in-wb-routing.kmc")
    kmc_digest = hashlib.sha256(json.dumps(
        kmc["geometry"], ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    _expect(kmc_digest == KMC_GEOMETRY_SHA256,
            "in-wb-routing KMC geometry digest does not match")

    state = regions["west_bengal"]
    _expect(isinstance(state, dict) and set(state) == WEST_BENGAL_STATE_REGION_KEYS,
            "in-wb-routing West Bengal region fields differ from the contract")
    expected_state = {
        "name": "West Bengal",
        "scope": "Full State of West Bengal",
        "authority_id": "wb-statewide-unverified",
        "source": "https://www.openstreetmap.org/relation/1960177",
        "source_relation_id": 1_960_177,
        "licence": "OpenStreetMap contributors, ODbL 1.0",
        "coordinate_precision": 7,
        "geometry_sha256": WEST_BENGAL_STATE_GEOMETRY_SHA256,
    }
    for field, value in expected_state.items():
        _expect(state.get(field) == value,
                f"in-wb-routing West Bengal {field} differs from its reviewed pin")
    _expect(state.get("retrieved_at") == retrieved_at,
            "in-wb-routing West Bengal retrieval date differs from the payload")
    _expect(
        isinstance(state.get("source_lookup"), str)
        and state["source_lookup"].startswith("https://nominatim.openstreetmap.org/lookup?")
        and "osm_ids=R1960177" in state["source_lookup"],
        "in-wb-routing West Bengal lookup URL is invalid",
    )
    _expect(isinstance(state.get("routing_note"), str) and state["routing_note"],
            "in-wb-routing West Bengal routing note is missing")
    limitations = state.get("limitations")
    _expect(
        isinstance(limitations, list) and 1 <= len(limitations) <= 10
        and all(isinstance(item, str) and item and len(item) <= 500 for item in limitations),
        "in-wb-routing West Bengal limitations are invalid",
    )
    bounds = _validate_municipal_geometry(
        state.get("geometry"), "in-wb-routing.west_bengal"
    )
    _validate_municipal_envelope(
        state.get("bbox"), "in-wb-routing.west_bengal.bbox"
    )
    _expect(bounds == state["bbox"],
            "in-wb-routing West Bengal geometry does not match its bounding box")
    state_digest = hashlib.sha256(json.dumps(
        state["geometry"], ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    _expect(state_digest == WEST_BENGAL_STATE_GEOMETRY_SHA256,
            "in-wb-routing West Bengal geometry digest does not match")

    by_id = {
        item.get("id"): item for item in authorities or [] if isinstance(item, dict)
    }
    statewide = by_id.get("wb-statewide-unverified")
    _expect(
        isinstance(authorities, list) and len(authorities) == 2
        and set(by_id) == {"wb-kmc", "wb-statewide-unverified"}
        and isinstance(statewide, dict)
        and statewide.get("handoff_name") == "West Bengal PGRS"
        and statewide.get("handoff_url")
            == "https://finance.wb.gov.in/pgrs/page/PGMS_Lodge_Greivance.aspx",
        "in-wb-routing statewide authority registry differs from its reviewed pin",
    )


def _validate_punjab_payload(
    payload: Any,
    *,
    generated_at: str | None = None,
    authorities: Any = None,
) -> None:
    _expect(
        isinstance(payload, dict) and set(payload) == {"version", "retrieved_at", "region"},
        "in-pb-routing payload fields differ from the statewide contract",
    )
    _expect(type(payload.get("version")) is int and payload["version"] == 1,
            "in-pb-routing payload version must be 1")
    retrieved_at = payload.get("retrieved_at")
    _expect(_is_date(retrieved_at), "in-pb-routing retrieved_at is invalid")
    if generated_at is not None:
        _expect(retrieved_at == generated_at,
                "in-pb-routing retrieved_at differs from the pack date")

    region = payload.get("region")
    _expect(isinstance(region, dict) and set(region) == PUNJAB_STATE_REGION_KEYS,
            "in-pb-routing Punjab region fields differ from the contract")
    expected = {
        "id": "punjab-state",
        "authority_id": "pb-statewide-unverified",
        "name": "Punjab",
        "scope": "Full State of Punjab; excludes Chandigarh Union Territory",
        "osm_relation_id": 1_942_686,
        "source_name": "OpenStreetMap contributors",
        "source_home_url": "https://www.openstreetmap.org/relation/1942686",
        "source_license": "Open Data Commons Open Database License (ODbL) 1.0",
        "attribution": "© OpenStreetMap contributors",
        "coordinate_precision": 7,
        "geometry_sha256": PUNJAB_STATE_GEOMETRY_SHA256,
    }
    for field, value in expected.items():
        _expect(region.get(field) == value,
                f"in-pb-routing Punjab {field} differs from its reviewed pin")
    _expect(
        isinstance(region.get("source_url"), str)
        and region["source_url"].startswith("https://nominatim.openstreetmap.org/lookup?")
        and "osm_ids=R1942686" in region["source_url"],
        "in-pb-routing Punjab lookup URL is invalid",
    )
    _expect(isinstance(region.get("routing_note"), str) and region["routing_note"],
            "in-pb-routing Punjab routing note is missing")
    limitations = region.get("limitations")
    _expect(
        isinstance(limitations, list) and 1 <= len(limitations) <= 10
        and all(isinstance(item, str) and item and len(item) <= 500 for item in limitations),
        "in-pb-routing Punjab limitations are invalid",
    )
    bounds = _validate_municipal_geometry(
        region.get("geometry"), "in-pb-routing.punjab"
    )
    _validate_municipal_envelope(region.get("bbox"), "in-pb-routing.punjab.bbox")
    _expect(bounds == region["bbox"],
            "in-pb-routing Punjab geometry does not match its bounding box")
    geometry_digest = hashlib.sha256(json.dumps(
        region["geometry"], ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    _expect(geometry_digest == PUNJAB_STATE_GEOMETRY_SHA256,
            "in-pb-routing Punjab geometry digest does not match")

    expected_authority = {
        "id": "pb-statewide-unverified",
        "name": "Punjab authority (select in Connect Punjab)",
        "aliases": ["punjab", "ਪੰਜਾਬ"],
        "handoff_name": "Connect Punjab PGRS",
        "handoff_url": "https://connect.punjab.gov.in/",
        "alternate_handoff_name": "Punjab mSeva (urban areas)",
        "alternate_handoff_url": "https://mseva.lgpunjab.gov.in/",
        "helpline": "1100",
    }
    _expect(
        authorities == [expected_authority],
        "in-pb-routing statewide authority registry differs from its reviewed pin",
    )


def _validate_tamil_nadu_state_payload(
    payload: Any,
    *,
    generated_at: str | None = None,
    authorities: Any = None,
) -> None:
    _expect(
        isinstance(payload, dict) and set(payload) == {"version", "retrieved_at", "region"},
        "in-tn-state-routing payload fields differ from the statewide contract",
    )
    _expect(type(payload.get("version")) is int and payload["version"] == 1,
            "in-tn-state-routing payload version must be 1")
    retrieved_at = payload.get("retrieved_at")
    _expect(_is_date(retrieved_at), "in-tn-state-routing retrieved_at is invalid")
    if generated_at is not None:
        _expect(retrieved_at == generated_at,
                "in-tn-state-routing retrieved_at differs from the pack date")

    region = payload.get("region")
    _expect(
        isinstance(region, dict) and set(region) == TAMIL_NADU_STATE_REGION_KEYS,
        "in-tn-state-routing Tamil Nadu region fields differ from the contract",
    )
    expected = {
        "id": "tamil-nadu-state",
        "authority_id": "tn-statewide-unverified",
        "name": "Tamil Nadu",
        "scope": (
            "Full State of Tamil Nadu; excludes Puducherry Union Territory enclaves"
        ),
        "osm_relation_id": 96_905,
        "source_name": "OpenStreetMap contributors",
        "source_home_url": "https://www.openstreetmap.org/relation/96905",
        "source_license": "Open Data Commons Open Database License (ODbL) 1.0",
        "attribution": "© OpenStreetMap contributors",
        "coordinate_precision": 7,
        "geometry_sha256": TAMIL_NADU_STATE_GEOMETRY_SHA256,
    }
    for field, value in expected.items():
        _expect(
            region.get(field) == value,
            f"in-tn-state-routing Tamil Nadu {field} differs from its reviewed pin",
        )
    _expect(
        isinstance(region.get("source_url"), str)
        and region["source_url"].startswith("https://nominatim.openstreetmap.org/lookup?")
        and "osm_ids=R96905" in region["source_url"],
        "in-tn-state-routing Tamil Nadu lookup URL is invalid",
    )
    _expect(
        isinstance(region.get("routing_note"), str)
        and "Greater Chennai Corporation" in region["routing_note"]
        and "does not identify" in region["routing_note"],
        "in-tn-state-routing Tamil Nadu routing note is invalid",
    )
    limitations = region.get("limitations")
    _expect(
        isinstance(limitations, list) and 1 <= len(limitations) <= 10
        and all(isinstance(item, str) and item and len(item) <= 500 for item in limitations)
        and any("Puducherry" in item and "Karaikal" in item for item in limitations),
        "in-tn-state-routing Tamil Nadu limitations are invalid",
    )
    bounds = _validate_municipal_geometry(
        region.get("geometry"), "in-tn-state-routing.tamil_nadu"
    )
    _validate_municipal_envelope(
        region.get("bbox"), "in-tn-state-routing.tamil_nadu.bbox"
    )
    _expect(
        bounds == region["bbox"],
        "in-tn-state-routing Tamil Nadu geometry does not match its bounding box",
    )
    geometry_digest = hashlib.sha256(json.dumps(
        region["geometry"], ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    _expect(
        geometry_digest == TAMIL_NADU_STATE_GEOMETRY_SHA256,
        "in-tn-state-routing Tamil Nadu geometry digest does not match",
    )

    expected_authority = {
        "id": "tn-statewide-unverified",
        "name": "Tamil Nadu authority (select in grievance portal)",
        "aliases": ["tamil nadu", "tamilnadu", "தமிழ்நாடு"],
        "handoff_name": "Mudhalvarin Mugavari",
        "handoff_url": "https://cmhelpline.tnega.org/portal/en/home",
        "handoff_package": "org.tnega.cmhelpline.citizen",
        "helpline": "1100",
    }
    _expect(
        authorities == [expected_authority],
        "in-tn-state-routing authority registry differs from its reviewed pin",
    )


def _validate_andhra_pradesh_payload(
    payload: Any,
    *,
    generated_at: str | None = None,
    authorities: Any = None,
) -> None:
    _expect(
        isinstance(payload, dict) and set(payload) == {"version", "retrieved_at", "region"},
        "in-ap-routing payload fields differ from the statewide contract",
    )
    _expect(type(payload.get("version")) is int and payload["version"] == 1,
            "in-ap-routing payload version must be 1")
    retrieved_at = payload.get("retrieved_at")
    _expect(_is_date(retrieved_at), "in-ap-routing retrieved_at is invalid")
    if generated_at is not None:
        _expect(retrieved_at == generated_at,
                "in-ap-routing retrieved_at differs from the pack date")

    region = payload.get("region")
    _expect(
        isinstance(region, dict) and set(region) == ANDHRA_PRADESH_STATE_REGION_KEYS,
        "in-ap-routing Andhra Pradesh region fields differ from the contract",
    )
    expected = {
        "id": "andhra-pradesh-state",
        "authority_id": "ap-statewide-unverified",
        "name": "Andhra Pradesh",
        "scope": (
            "Full State of Andhra Pradesh; excludes Yanam, Puducherry Union Territory"
        ),
        "osm_relation_id": 2_022_095,
        "source_name": "OpenStreetMap contributors",
        "source_home_url": "https://www.openstreetmap.org/relation/2022095",
        "source_license": "Open Data Commons Open Database License (ODbL) 1.0",
        "attribution": "© OpenStreetMap contributors",
        "coordinate_precision": 7,
        "geometry_sha256": ANDHRA_PRADESH_STATE_GEOMETRY_SHA256,
    }
    for field, value in expected.items():
        _expect(
            region.get(field) == value,
            f"in-ap-routing Andhra Pradesh {field} differs from its reviewed pin",
        )
    _expect(
        isinstance(region.get("source_url"), str)
        and region["source_url"].startswith("https://nominatim.openstreetmap.org/lookup?")
        and "osm_ids=R2022095" in region["source_url"],
        "in-ap-routing Andhra Pradesh lookup URL is invalid",
    )
    _expect(
        isinstance(region.get("routing_note"), str)
        and "Andhra Pradesh PGRS" in region["routing_note"]
        and "does not identify" in region["routing_note"],
        "in-ap-routing Andhra Pradesh routing note is invalid",
    )
    limitations = region.get("limitations")
    _expect(
        isinstance(limitations, list) and 1 <= len(limitations) <= 10
        and all(isinstance(item, str) and item and len(item) <= 500 for item in limitations)
        and any("Yanam" in item and "Puducherry" in item for item in limitations),
        "in-ap-routing Andhra Pradesh limitations are invalid",
    )
    bounds = _validate_municipal_geometry(
        region.get("geometry"), "in-ap-routing.andhra_pradesh"
    )
    _validate_municipal_envelope(
        region.get("bbox"), "in-ap-routing.andhra_pradesh.bbox"
    )
    _expect(
        bounds == region["bbox"],
        "in-ap-routing Andhra Pradesh geometry does not match its bounding box",
    )
    geometry_digest = hashlib.sha256(json.dumps(
        region["geometry"], ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    _expect(
        geometry_digest == ANDHRA_PRADESH_STATE_GEOMETRY_SHA256,
        "in-ap-routing Andhra Pradesh geometry digest does not match",
    )

    expected_authority = {
        "id": "ap-statewide-unverified",
        "name": "Andhra Pradesh authority (select in PGRS)",
        "aliases": ["andhra pradesh", "ఆంధ్ర ప్రదేశ్"],
        "handoff_name": "Andhra Pradesh PGRS",
        "handoff_url": "https://pgrs.ap.gov.in/",
        "alternate_handoff_name": "AP CDMA / Puramithra (urban areas)",
        "alternate_handoff_url": "https://cdma.ap.gov.in/auth/login/",
        "helpline": "1902",
    }
    _expect(
        authorities == [expected_authority],
        "in-ap-routing authority registry differs from its reviewed pin",
    )


def _validate_telangana_state_payload(
    payload: Any,
    *,
    generated_at: str | None = None,
    authorities: Any = None,
) -> None:
    _expect(
        isinstance(payload, dict) and set(payload) == {"version", "retrieved_at", "region"},
        "in-tg-state-routing payload fields differ from the statewide contract",
    )
    _expect(type(payload.get("version")) is int and payload["version"] == 1,
            "in-tg-state-routing payload version must be 1")
    retrieved_at = payload.get("retrieved_at")
    _expect(_is_date(retrieved_at), "in-tg-state-routing retrieved_at is invalid")
    if generated_at is not None:
        _expect(retrieved_at == generated_at,
                "in-tg-state-routing retrieved_at differs from the pack date")

    region = payload.get("region")
    _expect(
        isinstance(region, dict) and set(region) == TELANGANA_STATE_REGION_KEYS,
        "in-tg-state-routing Telangana region fields differ from the contract",
    )
    expected = {
        "id": "telangana-state",
        "authority_id": "tg-statewide-unverified",
        "name": "Telangana",
        "scope": "Full State of Telangana",
        "osm_relation_id": 3_250_963,
        "source_name": "OpenStreetMap contributors",
        "source_home_url": "https://www.openstreetmap.org/relation/3250963",
        "source_license": "Open Data Commons Open Database License (ODbL) 1.0",
        "attribution": "© OpenStreetMap contributors",
        "coordinate_precision": 7,
        "geometry_sha256": TELANGANA_STATE_GEOMETRY_SHA256,
    }
    for field, value in expected.items():
        _expect(
            region.get(field) == value,
            f"in-tg-state-routing Telangana {field} differs from its reviewed pin",
        )
    _expect(
        isinstance(region.get("source_url"), str)
        and region["source_url"].startswith("https://nominatim.openstreetmap.org/lookup?")
        and "osm_ids=R3250963" in region["source_url"],
        "in-tg-state-routing Telangana lookup URL is invalid",
    )
    _expect(
        isinstance(region.get("routing_note"), str)
        and "Telangana Prajavani" in region["routing_note"]
        and "Hyderabad CURE" in region["routing_note"]
        and "does not identify" in region["routing_note"],
        "in-tg-state-routing Telangana routing note is invalid",
    )
    limitations = region.get("limitations")
    _expect(
        isinstance(limitations, list) and 1 <= len(limitations) <= 10
        and all(isinstance(item, str) and item and len(item) <= 500 for item in limitations)
        and any("user must select" in item for item in limitations)
        and any("does not submit" in item for item in limitations),
        "in-tg-state-routing Telangana limitations are invalid",
    )
    bounds = _validate_municipal_geometry(
        region.get("geometry"), "in-tg-state-routing.telangana"
    )
    _validate_municipal_envelope(
        region.get("bbox"), "in-tg-state-routing.telangana.bbox"
    )
    _expect(
        bounds == region["bbox"],
        "in-tg-state-routing Telangana geometry does not match its bounding box",
    )
    geometry_digest = hashlib.sha256(json.dumps(
        region["geometry"], ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    _expect(
        geometry_digest == TELANGANA_STATE_GEOMETRY_SHA256,
        "in-tg-state-routing Telangana geometry digest does not match",
    )

    expected_authority = {
        "id": "tg-statewide-unverified",
        "name": "Telangana authority (select in Prajavani)",
        "aliases": ["telangana", "telengana", "తెలంగాణ"],
        "handoff_name": "Telangana Prajavani",
        "handoff_url": "https://prajavani.cgg.gov.in/",
        "alternate_handoff_name": "Citizen Buddy (municipal areas outside Hyderabad)",
        "alternate_handoff_url": (
            "https://play.google.com/store/apps/details?id=vmax.com.citizenbuddy"
        ),
    }
    _expect(
        authorities == [expected_authority],
        "in-tg-state-routing authority registry differs from its reviewed pin",
    )


def _validate_new_statewide_payload(
    pack_id: str,
    payload: Any,
    *,
    generated_at: str | None = None,
    authorities: Any = None,
) -> None:
    pins = {
        "in-ka-state-routing": {
            "label": "Karnataka",
            "region_id": "karnataka-state",
            "authority_id": "ka-statewide-unverified",
            "scope": "Full State of Karnataka",
            "relation_id": 2_019_939,
            "geometry_sha256": KARNATAKA_STATE_GEOMETRY_SHA256,
            "routing_terms": ("Karnataka Janaspandana", "Exact KGIS", "does not identify"),
            "authority": {
                "id": "ka-statewide-unverified",
                "name": "Karnataka authority (select in Janaspandana)",
                "aliases": ["karnataka", "ಕರ್ನಾಟಕ"],
                "handoff_name": "Karnataka Janaspandana",
                "handoff_url": "https://ipgrs.karnataka.gov.in/",
                "alternate_handoff_name": "Janahitha (urban areas outside Bengaluru)",
                "alternate_handoff_url": "https://www.mrc.gov.in/janahita/login",
                "helpline": "1902",
            },
        },
        "in-kl-routing": {
            "label": "Kerala",
            "region_id": "kerala-state",
            "authority_id": "kl-statewide-unverified",
            "scope": "Full State of Kerala; excludes Mahe, Puducherry Union Territory",
            "relation_id": 2_018_151,
            "geometry_sha256": KERALA_STATE_GEOMETRY_SHA256,
            "routing_terms": ("Kerala CMO", "K-SMART", "does not identify"),
            "authority": {
                "id": "kl-statewide-unverified",
                "name": "Kerala authority (select in CMO grievance portal)",
                "aliases": ["kerala", "കേരളം"],
                "handoff_name": "Kerala CMO Grievance",
                "handoff_url": "https://complaints.cmo.kerala.gov.in/cmoportal/login.htm?lang=en",
                "alternate_handoff_name": "K-SMART (local-body issues)",
                "alternate_handoff_url": "https://ksmart.lsgkerala.gov.in/ui/web-portal/services",
                "helpline": "1076",
            },
        },
        "in-up-routing": {
            "label": "Uttar Pradesh",
            "region_id": "uttar-pradesh-state",
            "authority_id": "up-statewide-unverified",
            "scope": "Full State of Uttar Pradesh; excludes Delhi National Capital Territory",
            "relation_id": 1_942_587,
            "geometry_sha256": UTTAR_PRADESH_STATE_GEOMETRY_SHA256,
            "routing_terms": ("Uttar Pradesh Jansunwai", "Delhi NCT", "does not identify"),
            "authority": {
                "id": "up-statewide-unverified",
                "name": "Uttar Pradesh authority (select in Jansunwai)",
                "aliases": ["uttar pradesh", "उत्तर प्रदेश"],
                "handoff_name": "UP Jansunwai–Samadhan",
                "handoff_url": "https://jansunwai.up.nic.in/?language=en_US",
                "handoff_package": "in.nic.up.jansunwai.upjansunwai",
                "helpline": "1076",
            },
        },
        "in-cg-routing": {
            "label": "Chhattisgarh",
            "region_id": "chhattisgarh-state",
            "authority_id": "cg-statewide-unverified",
            "scope": "Full State of Chhattisgarh",
            "relation_id": 1_972_004,
            "geometry_sha256": CHHATTISGARH_STATE_GEOMETRY_SHA256,
            "routing_terms": ("Chhattisgarh CM Helpline", "NIDAAN 1100", "does not identify"),
            "authority": {
                "id": "cg-statewide-unverified",
                "name": "Chhattisgarh authority (select in CM Helpline)",
                "aliases": ["chhattisgarh", "छत्तीसगढ़", "छत्तीसगढ़"],
                "handoff_name": "Chhattisgarh CM Helpline",
                "handoff_url": "https://cmhelpline.cg.gov.in/Home/VerifyOTPBeforeOnlineComplaint",
                "alternate_handoff_name": "NIDAAN 1100 (urban civic issues)",
                "alternate_handoff_url": "https://crm.nidaan.cg.gov.in/",
                "helpline": "1076",
            },
        },
        "in-rj-routing": {
            "label": "Rajasthan",
            "region_id": "rajasthan-state",
            "authority_id": "rj-statewide-unverified",
            "scope": "Full State of Rajasthan",
            "relation_id": 1_942_920,
            "geometry_sha256": RAJASTHAN_STATE_GEOMETRY_SHA256,
            "routing_terms": ("Rajasthan Sampark", "National Highways", "does not identify"),
            "authority": {
                "id": "rj-statewide-unverified",
                "name": "Rajasthan authority (select in Sampark)",
                "aliases": ["rajasthan", "राजस्थान"],
                "handoff_name": "Rajasthan Sampark 2.0",
                "handoff_url": "https://sampark.rajasthan.gov.in/grievanceForm",
                "handoff_package": "com.rajsampark.versiontwo",
                "helpline": "181",
            },
        },
        "in-ga-routing": {
            "label": "Goa",
            "region_id": "goa-state",
            "authority_id": "ga-statewide-unverified",
            "scope": "Full State of Goa",
            "relation_id": 11_251_493,
            "geometry_sha256": GOA_STATE_GEOMETRY_SHA256,
            "routing_terms": ("CM Helpline Goa", "National Highways", "does not identify"),
            "authority": {
                "id": "ga-statewide-unverified",
                "name": "Goa authority (select in CM Helpline)",
                "aliases": ["goa", "गोवा", "गोंय"],
                "handoff_name": "CM Helpline Goa",
                "handoff_url": "https://cmhelpline.dpg.goa.gov.in/",
                "handoff_package": "in.gov.dpg.cmhelpline",
                "helpline": "1905",
            },
        },
        "in-mp-routing": {
            "label": "Madhya Pradesh",
            "region_id": "madhya-pradesh-state",
            "authority_id": "mp-statewide-unverified",
            "scope": "Full State of Madhya Pradesh",
            "relation_id": 1_950_071,
            "geometry_sha256": MADHYA_PRADESH_STATE_GEOMETRY_SHA256,
            "routing_terms": ("Madhya Pradesh CM Helpline", "National Highways", "does not identify"),
            "authority": {
                "id": "mp-statewide-unverified",
                "name": "Madhya Pradesh authority (select in CM Helpline)",
                "aliases": ["madhya pradesh", "मध्य प्रदेश"],
                "handoff_name": "Madhya Pradesh CM Helpline",
                "handoff_url": "https://www.cmhelpline.mp.gov.in/Public/VerifyOTPBeforeOnlineComplaint.aspx",
                "handoff_package": "com.magnum.helpline",
                "helpline": "181",
            },
        },
        "in-br-routing": {
            "label": "Bihar",
            "region_id": "bihar-state",
            "authority_id": "br-statewide-unverified",
            "scope": "Full State of Bihar",
            "relation_id": 1_958_982,
            "geometry_sha256": BIHAR_STATE_GEOMETRY_SHA256,
            "routing_terms": ("Bihar Lok Shikayat", "National Highways", "does not identify"),
            "authority": {
                "id": "br-statewide-unverified",
                "name": "Bihar authority (select in Lok Shikayat)",
                "aliases": ["bihar", "बिहार"],
                "handoff_name": "Bihar Lok Shikayat",
                "handoff_url": "https://lokshikayat.bihar.gov.in/Default.aspx",
                "handoff_package": "com.bpsms.jansamadhan",
                "helpline": "18003456284",
            },
        },
        "in-od-routing": {
            "label": "Odisha",
            "region_id": "odisha-state",
            "authority_id": "od-statewide-unverified",
            "scope": "Full State of Odisha",
            "relation_id": 1_984_022,
            "geometry_sha256": ODISHA_STATE_GEOMETRY_SHA256,
            "routing_terms": ("Odisha Jana Sunani", "National Highways", "does not identify"),
            "authority": {
                "id": "od-statewide-unverified",
                "name": "Odisha authority (select in Jana Sunani)",
                "aliases": ["odisha", "orissa", "ଓଡ଼ିଶା", "ଓଡିଶା"],
                "handoff_name": "Odisha Jana Sunani",
                "handoff_url": "https://janasunani.odisha.gov.in/grievance-details",
                "handoff_package": "com.sociomatic.janasunani",
                "whatsapp_url": "https://wa.me/916370951930",
                "helpline": "155335",
            },
        },
    }
    pin = pins.get(pack_id)
    _expect(pin is not None, f"unsupported statewide payload validator: {pack_id}")
    _expect(
        isinstance(payload, dict) and set(payload) == {"version", "retrieved_at", "region"},
        f"{pack_id} payload fields differ from the statewide contract",
    )
    _expect(type(payload.get("version")) is int and payload["version"] == 1,
            f"{pack_id} payload version must be 1")
    retrieved_at = payload.get("retrieved_at")
    _expect(_is_date(retrieved_at), f"{pack_id} retrieved_at is invalid")
    if generated_at is not None:
        _expect(retrieved_at == generated_at, f"{pack_id} retrieved_at differs from pack date")

    region = payload.get("region")
    _expect(
        isinstance(region, dict) and set(region) == PUNJAB_STATE_REGION_KEYS,
        f"{pack_id} {pin['label']} region fields differ from the contract",
    )
    relation_id = pin["relation_id"]
    expected = {
        "id": pin["region_id"],
        "authority_id": pin["authority_id"],
        "name": pin["label"],
        "scope": pin["scope"],
        "osm_relation_id": relation_id,
        "source_name": "OpenStreetMap contributors",
        "source_home_url": f"https://www.openstreetmap.org/relation/{relation_id}",
        "source_license": "Open Data Commons Open Database License (ODbL) 1.0",
        "attribution": "© OpenStreetMap contributors",
        "coordinate_precision": 7,
        "geometry_sha256": pin["geometry_sha256"],
    }
    for field, value in expected.items():
        _expect(region.get(field) == value,
                f"{pack_id} {pin['label']} {field} differs from its reviewed pin")
    _expect(
        isinstance(region.get("source_url"), str)
        and region["source_url"].startswith("https://nominatim.openstreetmap.org/lookup?")
        and f"osm_ids=R{relation_id}" in region["source_url"],
        f"{pack_id} {pin['label']} lookup URL is invalid",
    )
    _expect(
        isinstance(region.get("routing_note"), str)
        and all(term in region["routing_note"] for term in pin["routing_terms"]),
        f"{pack_id} {pin['label']} routing note is invalid",
    )
    limitations = region.get("limitations")
    _expect(
        isinstance(limitations, list) and 1 <= len(limitations) <= 10
        and all(isinstance(item, str) and item and len(item) <= 500 for item in limitations)
        and any("user must select" in item for item in limitations)
        and any("does not submit" in item for item in limitations),
        f"{pack_id} {pin['label']} limitations are invalid",
    )
    if pack_id == "in-kl-routing":
        _expect(any("Mahe" in item for item in limitations),
                "in-kl-routing must explicitly exclude Mahe")
    bounds = _validate_municipal_geometry(region.get("geometry"), f"{pack_id}.state")
    _validate_municipal_envelope(region.get("bbox"), f"{pack_id}.state.bbox")
    _expect(bounds == region["bbox"], f"{pack_id} geometry does not match its bounding box")
    geometry_digest = hashlib.sha256(json.dumps(
        region["geometry"], ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    _expect(geometry_digest == pin["geometry_sha256"],
            f"{pack_id} geometry digest does not match")
    _expect(authorities == [pin["authority"]],
            f"{pack_id} authority registry differs from its reviewed pin")


def _top50_alias_key(value: str) -> str:
    return " ".join(value.casefold().replace("-", " ").split())


def _validate_top50_payload(
    payload: Any,
    *,
    generated_at: str | None = None,
    authorities: Any = None,
) -> None:
    _expect(
        isinstance(payload, dict) and set(payload) == TOP50_PAYLOAD_KEYS,
        "in-top50-routing payload fields differ from the structured-city contract",
    )
    _expect(type(payload.get("version")) is int and payload["version"] == 1,
            "in-top50-routing payload version must be 1")
    retrieved_at = payload.get("retrieved_at")
    _expect(_is_date(retrieved_at), "in-top50-routing retrieved_at is invalid")
    if generated_at is not None:
        _expect(retrieved_at == generated_at,
                "in-top50-routing retrieved_at differs from the pack date")

    regions = payload.get("regions")
    _expect(isinstance(regions, list) and len(regions) == len(TOP50_REGION_PINS),
            "in-top50-routing must contain exactly 35 reviewed regions")
    actual_identity: list[tuple[Any, Any, Any, Any, Any]] = []
    authority_state_aliases: dict[str, list[str]] = {}
    aliases_by_state: dict[str, dict[str, str]] = {}
    for index, region in enumerate(regions):
        label = f"in-top50-routing.regions[{index}]"
        _expect(isinstance(region, dict) and set(region) == TOP50_REGION_KEYS,
                f"{label} fields differ from the structured-city contract")
        identity = (
            region.get("rank"), region.get("id"), region.get("state_code"),
            region.get("authority_id"), region.get("source_object_id"),
        )
        actual_identity.append(identity)
        expected = TOP50_REGION_PINS[index]
        _expect(identity == expected,
                f"{label} identity differs from reviewed pin {expected!r}")

        _expect(type(region.get("rank")) is int and 1 <= region["rank"] <= 50,
                f"{label}.rank is invalid")
        _expect(
            isinstance(region.get("id"), str)
            and MUNICIPAL_ID_RE.fullmatch(region["id"]) is not None,
            f"{label}.id is invalid",
        )
        _expect(
            isinstance(region.get("name"), str) and region["name"]
            and len(region["name"]) <= 100,
            f"{label}.name is invalid",
        )
        _expect(region.get("routing_mode") == "structured_geocode",
                f"{label}.routing_mode must be structured_geocode")
        _expect(region.get("routing_source") == "nominatim_structured_city",
                f"{label}.routing_source differs from its reviewed pin")
        _expect(region.get("supported_issue_types") == TOP50_SUPPORTED_ISSUES,
                f"{label}.supported_issue_types differs from the reviewed set")
        _expect(region.get("exclusions") == [], f"{label}.exclusions must be empty")

        for field in (
            "scope", "match_value", "source_name", "source_license", "attribution",
            "routing_note",
        ):
            value = region.get(field)
            _expect(isinstance(value, str) and value and len(value) <= 1000,
                    f"{label}.{field} is invalid")
        _expect(
            region.get("source_name") == "Nominatim search over OpenStreetMap data"
            and region.get("source_license")
                == "Open Data Commons Open Database License (ODbL) 1.0"
            and region.get("attribution") == "© OpenStreetMap contributors",
            f"{label} source attribution differs from its reviewed pin",
        )
        for field in ("source_home_url", "source_url", "official_scope_reference"):
            value = region.get(field)
            _expect(isinstance(value, str) and HTTPS_RE.fullmatch(value) is not None,
                    f"{label}.{field} must be HTTPS")

        source_parts = str(region["source_object_id"]).split(":")
        _expect(
            len(source_parts) == 3 and source_parts[0] == "osm"
            and source_parts[1] in {"node", "way", "relation"}
            and source_parts[2].isdigit(),
            f"{label}.source_object_id is invalid",
        )
        object_type, object_id = source_parts[1], source_parts[2]
        _expect(
            region["source_home_url"]
                == f"https://www.openstreetmap.org/{object_type}/{object_id}"
            and region["match_value"] == f"OpenStreetMap {object_type} {object_id}",
            f"{label} OpenStreetMap source identity is inconsistent",
        )
        _expect(
            region["source_url"].startswith("https://nominatim.openstreetmap.org/search?")
            and "format=jsonv2" in region["source_url"]
            and "addressdetails=1" in region["source_url"]
            and "countrycodes=in" in region["source_url"],
            f"{label}.source_url is not the reviewed Nominatim search contract",
        )

        _validate_municipal_aliases(region.get("state_aliases"), f"{label}.state_aliases")
        _validate_municipal_aliases(region.get("place_aliases"), f"{label}.place_aliases")
        authority_id = region["authority_id"]
        previous_state_aliases = authority_state_aliases.setdefault(
            authority_id, region["state_aliases"]
        )
        _expect(previous_state_aliases == region["state_aliases"],
                f"{label}.state_aliases disagree within one reviewed authority")
        state_aliases = aliases_by_state.setdefault(region["state_code"], {})
        for alias in region["place_aliases"]:
            key = _top50_alias_key(alias)
            _expect(key and "urban agglomeration" not in key,
                    f"{label}.place_aliases contains an unsafe alias")
            previous = state_aliases.get(key)
            _expect(previous in {None, region["id"]},
                    f"{label}.place_aliases collides with region {previous}")
            state_aliases[key] = region["id"]

        _validate_municipal_envelope(region.get("envelope"), f"{label}.envelope")
        envelope = region["envelope"]
        _expect(
            envelope["max_lng"] - envelope["min_lng"] <= 0.33
            and envelope["max_lat"] - envelope["min_lat"] <= 0.33,
            f"{label}.envelope is not conservative",
        )
        limitations = region.get("limitations")
        _expect(
            isinstance(limitations, list) and 1 <= len(limitations) <= 10
            and all(isinstance(item, str) and item and len(item) <= 500
                    for item in limitations),
            f"{label}.limitations are invalid",
        )

    _expect(actual_identity == TOP50_REGION_PINS,
            "in-top50-routing region inventory or order differs from reviewed pins")

    _expect(isinstance(authorities, list) and len(authorities) == 12,
            "in-top50-routing must contain exactly 12 reviewed authorities")
    by_id = {
        item.get("id"): item for item in authorities or [] if isinstance(item, dict)
    }
    _expect(set(by_id) == set(TOP50_AUTHORITY_URL_PINS),
            "in-top50-routing authority ids differ from reviewed pins")
    for authority_id, expected_url in TOP50_AUTHORITY_URL_PINS.items():
        authority = by_id[authority_id]
        expected_keys = {"id", "name", "aliases", "handoff_name", "handoff_url"}
        if authority_id == "in-kl-ksmart":
            expected_keys.update({"alternate_handoff_name", "alternate_handoff_url"})
        _expect(set(authority) == expected_keys,
                f"in-top50-routing authority {authority_id} fields differ from contract")
        _expect(
            isinstance(authority.get("name"), str) and authority["name"]
            and isinstance(authority.get("handoff_name"), str)
            and authority["handoff_name"],
            f"in-top50-routing authority {authority_id} labels are invalid",
        )
        _expect(authority.get("handoff_url") == expected_url,
                f"in-top50-routing authority {authority_id} URL differs from its pin")
        _validate_municipal_aliases(
            authority.get("aliases"), f"in-top50-routing.authorities.{authority_id}.aliases"
        )
        _expect(authority.get("aliases") == authority_state_aliases.get(authority_id),
                f"in-top50-routing authority {authority_id} aliases differ from its regions")
        if authority_id == "in-kl-ksmart":
            _expect(
                authority.get("alternate_handoff_name") == "Kerala CMO Grievance"
                and authority.get("alternate_handoff_url") == "https://cmo.kerala.gov.in/",
                "in-top50-routing Kerala alternate differs from its reviewed pin",
            )
        for region in regions:
            if region["authority_id"] == authority_id:
                _expect(region["official_scope_reference"] == expected_url,
                        f"in-top50-routing {region['id']} official reference differs")


def _validate_municipal_authorities(spec: ResourceSpec, authorities: Any) -> None:
    expected = MUNICIPAL_AUTHORITY_PINS.get(spec.pack_id)
    _expect(expected is not None, f"{spec.pack_id} has no reviewed authority pin")
    _expect(
        authorities == expected,
        f"{spec.pack_id} complaint authority differs from its reviewed release pin",
    )


def _validate_official_point_query(value: dict[str, Any], label: str, spatial_rel: str) -> None:
    query_url = value.get("query_url")
    _expect(
        isinstance(query_url, str) and HTTPS_RE.fullmatch(query_url) is not None,
        f"{label}.query_url must be HTTPS",
    )
    _expect(value.get("query_where") == "1=1", f"{label}.query_where must be 1=1")
    _expect(
        value.get("query_geometry_type") == "esriGeometryEnvelope",
        f"{label}.query_geometry_type must be esriGeometryEnvelope",
    )
    _expect(type(value.get("query_in_sr")) is int and value["query_in_sr"] == 4326,
            f"{label}.query_in_sr must be integer EPSG:4326")
    _expect(value.get("query_spatial_rel") == spatial_rel,
            f"{label}.query_spatial_rel must be {spatial_rel}")


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
    _expect(routing_mode in {"boundary", "structured_geocode", "official_point_query"},
            f"{spec.pack_id} municipal routing_mode is invalid")
    expected_fields = set(MUNICIPAL_COMMON_REGION_KEYS)
    if routing_mode == "boundary":
        expected_fields.update(MUNICIPAL_BOUNDARY_REGION_KEYS)
    elif routing_mode == "official_point_query":
        expected_fields.update(MUNICIPAL_OFFICIAL_POINT_REGION_KEYS)
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
        _expect(isinstance(exclusion, dict), f"{label} must be an object")
        exclusion_mode = exclusion.get("mode")
        expected_exclusion_keys = (
            MUNICIPAL_POINT_EXCLUSION_KEYS
            if exclusion_mode == "official_point_query"
            else MUNICIPAL_EXCLUSION_KEYS
        )
        _expect(set(exclusion) == expected_exclusion_keys,
                f"{label} fields differ from the {exclusion_mode} contract")
        exclusion_id = exclusion.get("id")
        _expect(
            isinstance(exclusion_id, str)
            and MUNICIPAL_ID_RE.fullmatch(exclusion_id) is not None
            and exclusion_id not in exclusion_ids,
            f"{label} id is invalid or duplicated",
        )
        exclusion_ids.add(exclusion_id)
        _expect(exclusion_mode in {"bbox", "official_point_query"},
                f"{label} mode is invalid")
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
        if exclusion_mode == "official_point_query":
            source_object_id = exclusion.get("source_object_id")
            _expect(
                isinstance(source_object_id, str) and source_object_id
                and len(source_object_id) <= 200,
                f"{label}.source_object_id is invalid",
            )
            _validate_official_point_query(exclusion, label, "esriSpatialRelIntersects")

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
    elif routing_mode == "official_point_query":
        _validate_official_point_query(region, spec.pack_id, "esriSpatialRelWithin")
        area = region.get("official_area_km2")
        _expect(_is_finite_number(area) and 1 < area <= 10_000,
                f"{spec.pack_id} official_area_km2 is outside the runtime range")
        legal_references = region.get("legal_references")
        _expect(
            isinstance(legal_references, list) and 1 <= len(legal_references) <= 10,
            f"{spec.pack_id} legal_references must contain between 1 and 10 entries",
        )
        for index, reference in enumerate(legal_references):
            label = f"{spec.pack_id}.legal_references[{index}]"
            _expect(
                isinstance(reference, dict)
                and set(reference) == MUNICIPAL_LEGAL_REFERENCE_KEYS,
                f"{label} fields differ from the contract",
            )
            _expect(
                isinstance(reference.get("title"), str) and reference["title"]
                and len(reference["title"]) <= 500,
                f"{label}.title is invalid",
            )
            _expect(_is_date(reference.get("date")), f"{label}.date is invalid")
            _expect(
                isinstance(reference.get("url"), str)
                and HTTPS_RE.fullmatch(reference["url"]) is not None,
                f"{label}.url must be HTTPS",
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
            if not is_road_surface_contract(row["t"], row["tn"]):
                raise PackError(
                    f"{spec.pack_id} tender {index} ({row['tn']}) has no verified "
                    "carriageway-work scope"
                )
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
        _validate_maharashtra_payload(
            payload,
            generated_at=generated_at,
            authorities=authorities,
        )
    elif spec.pack_id == "in-wb-routing":
        _validate_west_bengal_payload(
            payload,
            generated_at=generated_at,
            authorities=authorities,
        )
    elif spec.pack_id == "in-pb-routing":
        _validate_punjab_payload(
            payload,
            generated_at=generated_at,
            authorities=authorities,
        )
    elif spec.pack_id == "in-tn-state-routing":
        _validate_tamil_nadu_state_payload(
            payload,
            generated_at=generated_at,
            authorities=authorities,
        )
    elif spec.pack_id == "in-ap-routing":
        _validate_andhra_pradesh_payload(
            payload,
            generated_at=generated_at,
            authorities=authorities,
        )
    elif spec.pack_id == "in-tg-state-routing":
        _validate_telangana_state_payload(
            payload,
            generated_at=generated_at,
            authorities=authorities,
        )
    elif spec.pack_id in {
        "in-ka-state-routing", "in-kl-routing", "in-up-routing", "in-cg-routing",
        "in-rj-routing", "in-ga-routing", "in-mp-routing", "in-br-routing",
        "in-od-routing",
    }:
        _validate_new_statewide_payload(
            spec.pack_id,
            payload,
            generated_at=generated_at,
            authorities=authorities,
        )
    elif spec.pack_id == "in-top50-routing":
        _validate_top50_payload(
            payload,
            generated_at=generated_at,
            authorities=authorities,
        )
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
    authorities = _authority_snapshot(spec) if spec.kind == "routing" else None
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
    previous_manifest_path = (
        STATIC_MANIFEST if STATIC_MANIFEST.exists() else PREVIOUS_STATIC_MANIFEST
    )
    previous_manifest = (
        _read_json(previous_manifest_path) if previous_manifest_path.exists() else None
    )
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
            payload = [
                row for row in payload
                if isinstance(row, dict) and row.get("b")
                and is_road_surface_contract(row.get("t"), row.get("tn"))
            ]
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
        _expect(authorities == _authority_snapshot(spec),
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
        raise PackError("missing bundled manifest: static/pack-manifest-v1.34.json")
    _expect(ANDROID_MANIFEST.exists(), "missing Android manifest mirror: android-app/www/pack-manifest-v1.34.json")
    _expect(ANDROID_MANIFEST.read_bytes() == manifest_bytes, "static and Android pack manifests differ")
    _expect(PAGES_MANIFEST.exists(), "missing Pages manifest mirror: docs/pack-manifest-v1.34.json")
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
