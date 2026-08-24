#!/usr/bin/env python3
"""Build the pinned structured-geocode routes for 35 Census top-50 centres.

Each Nominatim result supplies only a conservative search envelope. Runtime
matching must also require the exact reviewed state and city/municipality
aliases. These records neither describe municipal boundaries nor identify a
road owner, and their government links are neutral, user-selected handoffs.
"""

from __future__ import annotations

import argparse
import json
import math
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "data" / "top-50-cities.json"
OUTPUT = ROOT / "data" / "metro-coverage" / "top50.json"
RETRIEVED_AT = "2026-08-24"
SEARCH_ENDPOINT = "https://nominatim.openstreetmap.org/search"
USER_AGENT = (
    "PotholeReporter-coverage-builder/1.0 "
    "(https://github.com/coding-parrot/pothole-reporter; contact@aiengg.dev)"
)
SUPPORTED_ISSUE_TYPES = ["road_damage", "garbage", "open_manhole"]


class BuildError(RuntimeError):
    """Raised when a reviewed top-50 source contract is not satisfied."""


STATE = {
    "GJ": {
        "name": "Gujarat",
        "aliases": ["gujarat", "ગુજરાત"],
        "authority_id": "in-gj-enagar",
        "official_url": "https://enagar.gujarat.gov.in/enagar/login.jsp",
    },
    "RJ": {
        "name": "Rajasthan",
        "aliases": ["rajasthan", "राजस्थान"],
        "authority_id": "in-rj-sampark",
        "official_url": "https://sampark.rajasthan.gov.in/",
    },
    "UP": {
        "name": "Uttar Pradesh",
        "aliases": ["uttar pradesh", "उत्तर प्रदेश"],
        "authority_id": "in-up-jansunwai",
        "official_url": "https://www.jansunwai.up.nic.in/",
    },
    "MP": {
        "name": "Madhya Pradesh",
        "aliases": ["madhya pradesh", "मध्य प्रदेश"],
        "authority_id": "in-mp-cm-helpline",
        "official_url": "https://www.cmhelpline.mp.gov.in/",
    },
    "TN": {
        "name": "Tamil Nadu",
        "aliases": ["tamil nadu", "tamilnadu", "தமிழ்நாடு"],
        "authority_id": "in-tn-cm-helpline",
        "official_url": "https://cmhelpline.tnega.org/portal/en/home",
    },
    "KL": {
        "name": "Kerala",
        "aliases": ["kerala", "കേരളം"],
        "authority_id": "in-kl-ksmart",
        "official_url": "https://ksmart.lsgkerala.gov.in/ui/web-portal",
    },
    "BR": {
        "name": "Bihar",
        "aliases": ["bihar", "बिहार"],
        "authority_id": "in-br-lok-shikayat",
        "official_url": "https://lokshikayat.bihar.gov.in/",
    },
    "AP": {
        "name": "Andhra Pradesh",
        "aliases": ["andhra pradesh", "ఆంధ్ర ప్రదేశ్"],
        "authority_id": "in-ap-puramithra",
        "official_url": "https://cdma.ap.gov.in/services/grievances/",
    },
    "HR": {
        "name": "Haryana",
        "aliases": ["haryana", "हरियाणा"],
        "authority_id": "in-hr-nagar-darshan",
        "official_url": "https://nagardarshan.ulbharyana.gov.in/Default/CitizenEntry",
    },
    "JH": {
        "name": "Jharkhand",
        "aliases": ["jharkhand", "झारखंड", "झारखण्ड"],
        "authority_id": "in-jh-municipal-grievance",
        "official_url": (
            "https://municipalservices.jharkhand.gov.in/public/grievance_new/login"
        ),
    },
    "JK": {
        "name": "Jammu and Kashmir",
        "aliases": [
            "jammu and kashmir",
            "jammu & kashmir",
            "جموں و کشمیر",
            "जम्मू और कश्मीर",
        ],
        "authority_id": "in-jk-samadhan",
        "official_url": "https://samadhan.jk.gov.in/",
    },
    "CG": {
        "name": "Chhattisgarh",
        "aliases": ["chhattisgarh", "छत्तीसगढ़", "छत्तीसगढ़"],
        "authority_id": "in-cg-nidaan",
        "official_url": "https://crm.nidaan.cg.gov.in/",
    },
}


def city(
    rank: int,
    city_id: str,
    name: str,
    state_code: str,
    osm_type: str,
    osm_id: int,
    category: str,
    feature_type: str,
    lat: float,
    lng: float,
    min_lng: float,
    min_lat: float,
    max_lng: float,
    max_lat: float,
    aliases: list[str],
    *,
    search_name: str | None = None,
) -> dict[str, Any]:
    return {
        "rank": rank,
        "id": city_id,
        "name": name,
        "state_code": state_code,
        "search_name": search_name or name,
        "osm_type": osm_type,
        "osm_id": osm_id,
        "category": category,
        "feature_type": feature_type,
        "center": {"lat": lat, "lng": lng},
        "envelope": {
            "min_lng": min_lng,
            "min_lat": min_lat,
            "max_lng": max_lng,
            "max_lat": max_lat,
        },
        "aliases": aliases,
    }


# Object identities, centres and search envelopes were manually reviewed on
# 2026-08-24. Any change fails closed until this list is deliberately updated.
CITIES = [
    city(9, "surat", "Surat", "GJ", "node", 10029899747, "place", "city", 21.2094892, 72.8317058, 72.6717058, 21.0494892, 72.9917058, 21.3694892, ["Surat", "સુરત"]),
    city(10, "jaipur", "Jaipur", "RJ", "node", 315734346, "place", "city", 26.9154576, 75.8189817, 75.6589817, 26.7554576, 75.9789817, 27.0754576, ["Jaipur", "Jaipur City", "Jaipur Municipal Corporation", "जयपुर"]),
    city(11, "kanpur", "Kanpur", "UP", "node", 1180652177, "place", "city", 26.4609135, 80.3217588, 80.1617588, 26.3009135, 80.4817588, 26.6209135, ["Kanpur", "Cawnpore", "कानपुर"]),
    city(12, "lucknow", "Lucknow", "UP", "node", 245753718, "place", "city", 26.8381, 80.9346001, 80.7746001, 26.6781, 81.0946001, 26.9981, ["Lucknow", "लखनऊ"]),
    city(14, "ghaziabad", "Ghaziabad", "UP", "node", 2521085873, "place", "city", 28.6711527, 77.4120356, 77.2520356, 28.5111527, 77.5720356, 28.8311527, ["Ghaziabad", "गाज़ियाबाद", "गाजियाबाद"]),
    city(15, "indore", "Indore", "MP", "node", 245709027, "place", "city", 22.7203616, 75.8681996, 75.7081996, 22.5603616, 76.0281996, 22.8803616, ["Indore", "इंदौर"]),
    city(16, "coimbatore", "Coimbatore", "TN", "node", 245589078, "place", "city", 11.0018115, 76.9628425, 76.8028425, 10.8418115, 77.1228425, 11.1618115, ["Coimbatore", "Kovai", "கோயம்புத்தூர்"]),
    city(17, "kochi", "Kochi", "KL", "node", 3862624198, "place", "city", 9.9679032, 76.2444378, 76.0844378, 9.8079032, 76.4044378, 10.1279032, ["Kochi", "Cochin", "Ernakulam", "കൊച്ചി"]),
    city(18, "patna", "Patna", "BR", "way", 383774533, "place", "city", 25.6093239, 85.1235252, 85.0118412, 25.5389546, 85.2641902, 25.6510479, ["Patna", "पटना"]),
    city(19, "kozhikode", "Kozhikode", "KL", "node", 1348192542, "place", "city", 11.2450558, 75.7754716, 75.6154716, 11.0850558, 75.9354716, 11.4050558, ["Kozhikode", "Calicut", "കോഴിക്കോട്"]),
    city(20, "bhopal", "Bhopal", "MP", "node", 245712627, "place", "city", 23.2584857, 77.401989, 77.241989, 23.0984857, 77.561989, 23.4184857, ["Bhopal", "भोपाल"]),
    city(21, "thrissur", "Thrissur", "KL", "node", 4430328343, "place", "city", 10.5270099, 76.214621, 76.054621, 10.3670099, 76.374621, 10.6870099, ["Thrissur", "Trichur", "തൃശ്ശൂർ"]),
    city(22, "vadodara", "Vadodara", "GJ", "node", 2022807192, "place", "city", 22.2973142, 73.1942567, 73.0342567, 22.1373142, 73.3542567, 22.4573142, ["Vadodara", "Baroda", "વડોદરા"]),
    city(23, "agra", "Agra", "UP", "node", 567267943, "place", "city", 27.1752554, 78.0098161, 77.8498161, 27.0152554, 78.1698161, 27.3352554, ["Agra", "आगरा"]),
    city(24, "visakhapatnam", "Visakhapatnam", "AP", "node", 245641840, "place", "city", 17.6935526, 83.2921297, 83.1321297, 17.5335526, 83.4521297, 17.8535526, ["Visakhapatnam", "Vizag", "Waltair", "విశాఖపట్నం"]),
    city(25, "malappuram", "Malappuram", "KL", "way", 84635269, "place", "city", 11.0428925, 76.0807838, 76.0338675, 11.0263125, 76.1033108, 11.0964078, ["Malappuram", "മലപ്പുറം"]),
    city(26, "thiruvananthapuram", "Thiruvananthapuram", "KL", "node", 245581432, "place", "city", 8.4882267, 76.947551, 76.787551, 8.3282267, 77.107551, 8.6482267, ["Thiruvananthapuram", "Trivandrum", "തിരുവനന്തപുരം"]),
    city(27, "kannur", "Kannur", "KL", "node", 290180981, "place", "city", 11.8763836, 75.3737973, 75.2137973, 11.7163836, 75.5337973, 12.0363836, ["Kannur", "Cannanore", "കണ്ണൂർ"]),
    city(30, "vijayawada", "Vijayawada", "AP", "node", 1880441437, "place", "city", 16.5115306, 80.6160469, 80.4560469, 16.3515306, 80.7760469, 16.6715306, ["Vijayawada", "Bezawada", "విజయవాడ"]),
    city(31, "madurai", "Madurai", "TN", "relation", 11268397, "boundary", "administrative", 9.9261153, 78.1140983, 78.0155927, 9.8245041, 78.2030091, 9.9933722, ["Madurai", "மதுரை"]),
    city(32, "varanasi", "Varanasi", "UP", "node", 287687798, "place", "city", 25.3356491, 83.0076292, 82.8476292, 25.1756491, 83.1676292, 25.4956491, ["Varanasi", "Banaras", "Benares", "Kashi", "वाराणसी"]),
    city(33, "meerut", "Meerut", "UP", "node", 571773704, "place", "city", 28.9963296, 77.7061915, 77.5461915, 28.8363296, 77.8661915, 29.1563296, ["Meerut", "मेरठ"]),
    city(34, "faridabad", "Faridabad", "HR", "node", 3582568815, "place", "city", 28.4031478, 77.3105561, 77.1505561, 28.2431478, 77.4705561, 28.5631478, ["Faridabad", "Faridabad Municipal Corporation", "फ़रीदाबाद", "फरीदाबाद"]),
    city(35, "rajkot", "Rajkot", "GJ", "node", 1393852189, "place", "city", 22.3053263, 70.8028377, 70.6428377, 22.1453263, 70.9628377, 22.4653263, ["Rajkot", "રાજકોટ"]),
    city(36, "jamshedpur", "Jamshedpur", "JH", "node", 566174729, "place", "city", 22.8015194, 86.2029579, 86.0429579, 22.6415194, 86.3629579, 22.9615194, ["Jamshedpur", "Tatanagar", "जमशेदपुर"]),
    city(37, "jabalpur", "Jabalpur", "MP", "relation", 3832427, "boundary", "administrative", 23.1701522, 79.9324505, 79.883083, 23.116068, 79.9762, 23.2110608, ["Jabalpur", "Jubbulpore", "जबलपुर"]),
    city(38, "srinagar", "Srinagar", "JK", "node", 273658993, "place", "city", 34.0747444, 74.8204443, 74.6604443, 33.9147444, 74.9804443, 34.2347444, ["Srinagar", "سری نگر", "श्रीनगर"]),
    city(41, "prayagraj", "Prayagraj", "UP", "node", 245733956, "place", "city", 25.4381302, 81.8338005, 81.6738005, 25.2781302, 81.9938005, 25.5981302, ["Prayagraj", "Allahabad", "Prayag", "प्रयागराज", "इलाहाबाद"]),
    city(42, "dhanbad", "Dhanbad", "JH", "node", 2516759396, "place", "city", 23.7952809, 86.4309638, 86.2709638, 23.6352809, 86.5909638, 23.9552809, ["Dhanbad", "धनबाद"]),
    city(45, "jodhpur", "Jodhpur", "RJ", "way", 31725312, "place", "city", 26.2967719, 73.0351433, 72.9485753, 26.2059842, 73.0888716, 26.3536661, ["Jodhpur", "जोधपुर"]),
    city(46, "ranchi", "Ranchi", "JH", "node", 2510123017, "place", "city", 23.3700501, 85.3250387, 85.1650387, 23.2100501, 85.4850387, 23.5300501, ["Ranchi", "रांची"]),
    city(47, "raipur", "Raipur", "CG", "node", 5308437250, "place", "city", 21.2380912, 81.6336993, 81.4736993, 21.0780912, 81.7936993, 21.3980912, ["Raipur", "रायपुर"]),
    city(48, "kollam", "Kollam", "KL", "node", 245582090, "place", "city", 8.8870533, 76.5906696, 76.4306696, 8.7270533, 76.7506696, 9.0470533, ["Kollam", "Quilon", "കൊല്ലം"]),
    city(49, "gwalior", "Gwalior", "MP", "node", 568412253, "place", "city", 26.2037247, 78.1573628, 77.9973628, 26.0437247, 78.3173628, 26.3637247, ["Gwalior", "ग्वालियर"]),
    city(50, "durg-bhilai", "Durg-Bhilai", "CG", "node", 3105817661, "place", "city", 21.2120677, 81.3732849, 81.2132849, 21.0520677, 81.5332849, 21.3720677, ["Durg-Bhilai", "Durg-Bhilainagar", "Durg-Bhilai Nagar", "Bhilai-Durg", "Bhilai", "Durg", "भिलाई", "दुर्ग"], search_name="Bhilai"),
]


def compact_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BuildError(f"cannot read {path}: {exc}") from exc


def alias_key(value: str) -> str:
    return " ".join(value.casefold().replace("-", " ").split())


def rounded(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise BuildError(f"{label} is not numeric") from exc
    if not math.isfinite(number):
        raise BuildError(f"{label} is not finite")
    return round(number, 7)


def search_url(spec: dict[str, Any]) -> str:
    state_name = STATE[spec["state_code"]]["name"]
    params = [
        ("q", f'{spec["search_name"]}, {state_name}, India'),
        ("format", "jsonv2"),
        ("addressdetails", "1"),
        ("limit", "10"),
        ("countrycodes", "in"),
        ("accept-language", "en"),
    ]
    return f"{SEARCH_ENDPOINT}?{urllib.parse.urlencode(params)}"


def download(url: str) -> Any:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)
    except (OSError, json.JSONDecodeError) as exc:
        raise BuildError(f"cannot download Nominatim search result {url}: {exc}") from exc


def load_searches(input_dir: Path | None) -> dict[str, Any]:
    searches: dict[str, Any] = {}
    previous_request = 0.0
    for spec in CITIES:
        if input_dir:
            searches[spec["id"]] = read_json(input_dir / f'{spec["id"]}.json')
            continue
        elapsed = time.monotonic() - previous_request
        if previous_request and elapsed < 1.1:
            time.sleep(1.1 - elapsed)
        searches[spec["id"]] = download(search_url(spec))
        previous_request = time.monotonic()
    return searches


def validate_catalog() -> None:
    raw = read_json(CATALOG)
    cities = raw.get("cities") if isinstance(raw, dict) else None
    if not isinstance(cities, list) or len(cities) != 50:
        raise BuildError("top-50 catalog must contain exactly 50 cities")
    if {item.get("rank") for item in cities if isinstance(item, dict)} != set(range(1, 51)):
        raise BuildError("top-50 catalog ranks must be exactly 1 through 50")
    by_id = {item.get("id"): item for item in cities if isinstance(item, dict)}
    if len(by_id) != 50 or None in by_id:
        raise BuildError("top-50 catalog city ids must be present and unique")
    expected_ids = {spec["id"] for spec in CITIES}
    routed_ids = {
        item.get("id") for item in cities
        if isinstance(item, dict) and item.get("pack_id") == "in-top50-routing"
    }
    if routed_ids != expected_ids:
        raise BuildError("top-50 pack inventory does not match the 35 reviewed centres")
    for spec in CITIES:
        item = by_id.get(spec["id"])
        actual = (item.get("rank"), item.get("current_name"), item.get("state_code"))
        expected = (spec["rank"], spec["name"], spec["state_code"])
        if actual != expected:
            raise BuildError(f'top-50 catalog identity changed for {spec["id"]}: {actual}')


def validate_specs() -> None:
    if len(CITIES) != 35:
        raise BuildError("the reviewed top-50 routing inventory must contain 35 centres")
    ids = [spec["id"] for spec in CITIES]
    ranks = [spec["rank"] for spec in CITIES]
    object_ids = [(spec["osm_type"], spec["osm_id"]) for spec in CITIES]
    if len(set(ids)) != len(ids) or len(set(ranks)) != len(ranks):
        raise BuildError("region ids and ranks must be unique")
    if len(set(object_ids)) != len(object_ids):
        raise BuildError("pinned OpenStreetMap object identities must be unique")
    aliases_by_state: dict[str, dict[str, str]] = {}
    for spec in CITIES:
        if spec["state_code"] not in STATE:
            raise BuildError(f'unknown state for {spec["id"]}')
        envelope = spec["envelope"]
        if not (
            envelope["min_lng"] < spec["center"]["lng"] < envelope["max_lng"]
            and envelope["min_lat"] < spec["center"]["lat"] < envelope["max_lat"]
        ):
            raise BuildError(f'pinned centre is outside its envelope: {spec["id"]}')
        if (
            envelope["max_lng"] - envelope["min_lng"] > 0.33
            or envelope["max_lat"] - envelope["min_lat"] > 0.33
        ):
            raise BuildError(f'pinned envelope is not conservative: {spec["id"]}')
        state_aliases = aliases_by_state.setdefault(spec["state_code"], {})
        for alias in spec["aliases"]:
            key = alias_key(alias)
            if not key or "urban agglomeration" in key:
                raise BuildError(f'unsafe city alias for {spec["id"]}: {alias!r}')
            previous = state_aliases.get(key)
            if previous and previous != spec["id"]:
                raise BuildError(
                    f'city alias collision in {spec["state_code"]}: {alias!r}'
                )
            state_aliases[key] = spec["id"]

    for spec in CITIES:
        lat = spec["center"]["lat"]
        lng = spec["center"]["lng"]
        for other in CITIES:
            if other["id"] == spec["id"] or other["state_code"] != spec["state_code"]:
                continue
            envelope = other["envelope"]
            if (
                envelope["min_lng"] <= lng <= envelope["max_lng"]
                and envelope["min_lat"] <= lat <= envelope["max_lat"]
            ):
                raise BuildError(
                    f'centre fixture for {spec["id"]} also matches {other["id"]}'
                )


def selected_feature(spec: dict[str, Any], raw: Any) -> dict[str, Any]:
    if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
        raise BuildError(f'Nominatim search is not a result list: {spec["id"]}')
    matches = [
        item for item in raw
        if item.get("osm_type") == spec["osm_type"]
        and int(item.get("osm_id", 0)) == spec["osm_id"]
    ]
    if len(matches) != 1:
        raise BuildError(
            f'Nominatim did not return exactly one pinned object for {spec["id"]}'
        )
    feature = matches[0]
    if (
        feature.get("category") != spec["category"]
        or feature.get("type") != spec["feature_type"]
        or "OpenStreetMap" not in str(feature.get("licence", ""))
        or "ODbL" not in str(feature.get("licence", ""))
    ):
        raise BuildError(f'pinned object classification changed for {spec["id"]}')

    center = {
        "lat": rounded(feature.get("lat"), f'{spec["id"]} latitude'),
        "lng": rounded(feature.get("lon"), f'{spec["id"]} longitude'),
    }
    if center != spec["center"]:
        raise BuildError(f'pinned object centre changed for {spec["id"]}: {center}')
    source_bbox = feature.get("boundingbox")
    if not isinstance(source_bbox, list) or len(source_bbox) != 4:
        raise BuildError(f'pinned object has no bounding box: {spec["id"]}')
    south, north, west, east = [
        rounded(value, f'{spec["id"]} bounding box') for value in source_bbox
    ]
    envelope = {
        "min_lng": west,
        "min_lat": south,
        "max_lng": east,
        "max_lat": north,
    }
    if envelope != spec["envelope"]:
        raise BuildError(f'pinned object envelope changed for {spec["id"]}: {envelope}')

    address = feature.get("address")
    if not isinstance(address, dict):
        raise BuildError(f'pinned object has no structured address: {spec["id"]}')
    if alias_key(str(address.get("country_code", ""))) != "in":
        raise BuildError(f'pinned object is no longer in India: {spec["id"]}')
    state = STATE[spec["state_code"]]
    if alias_key(str(address.get("state", ""))) != alias_key(state["name"]):
        raise BuildError(f'pinned object state changed for {spec["id"]}')
    iso_values = {
        str(value).upper() for key, value in address.items()
        if str(key).startswith("ISO3166-2")
    }
    if f'IN-{spec["state_code"]}' not in iso_values:
        raise BuildError(f'pinned object has no reviewed state code: {spec["id"]}')
    locality_values = {
        alias_key(str(address.get(key, "")))
        for key in ("city", "town", "municipality", "city_district")
        if address.get(key)
    }
    reviewed_aliases = {alias_key(alias) for alias in spec["aliases"]}
    if not locality_values.intersection(reviewed_aliases):
        raise BuildError(
            f'pinned object has no reviewed structured locality for {spec["id"]}: '
            f'{sorted(locality_values)}'
        )
    return feature


def region(spec: dict[str, Any], raw: Any) -> dict[str, Any]:
    selected_feature(spec, raw)
    state = STATE[spec["state_code"]]
    osm_type = spec["osm_type"]
    osm_id = spec["osm_id"]
    return {
        "rank": spec["rank"],
        "id": spec["id"],
        "authority_id": state["authority_id"],
        "name": spec["name"],
        "state_code": spec["state_code"],
        "scope": (
            f'{spec["name"]} only through exact structured geocoding inside a '
            "pinned search envelope; no municipal-boundary or whole-urban-"
            "agglomeration claim."
        ),
        "routing_mode": "structured_geocode",
        "routing_source": "nominatim_structured_city",
        "match_value": f"OpenStreetMap {osm_type} {osm_id}",
        "state_aliases": list(state["aliases"]),
        "place_aliases": list(spec["aliases"]),
        "envelope": dict(spec["envelope"]),
        "source_name": "Nominatim search over OpenStreetMap data",
        "source_home_url": f"https://www.openstreetmap.org/{osm_type}/{osm_id}",
        "source_url": search_url(spec),
        "source_license": "Open Data Commons Open Database License (ODbL) 1.0",
        "attribution": "© OpenStreetMap contributors",
        "official_scope_reference": state["official_url"],
        "routing_note": (
            "The envelope only selects a candidate. Exact structured state and "
            "city/municipality aliases are required; the neutral government portal "
            "does not prove ownership or category acceptance."
        ),
        "limitations": [
            "The Nominatim search envelope is a routing aid, not a legal or municipal boundary.",
            "Only an exact reviewed state and city/municipality structured-geocode match is accepted.",
            "The user must select and verify the responsible department and complaint category.",
            "No road ownership, category acceptance, submission, or resolution is inferred.",
        ],
        "exclusions": [],
        "source_object_id": f"osm:{osm_type}:{osm_id}",
        "supported_issue_types": list(SUPPORTED_ISSUE_TYPES),
    }


def build(searches: dict[str, Any]) -> dict[str, Any]:
    validate_specs()
    validate_catalog()
    if set(searches) != {spec["id"] for spec in CITIES}:
        raise BuildError("search response inventory does not match the reviewed centres")
    return {
        "version": 1,
        "retrieved_at": RETRIEVED_AT,
        "regions": [region(spec, searches[spec["id"]]) for spec in CITIES],
    }


def encoded_payload(payload: dict[str, Any]) -> bytes:
    return compact_json(payload) + b"\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-dir",
        type=Path,
        help="directory of <region-id>.json saved Nominatim search responses",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify that the canonical output already matches without writing it",
    )
    args = parser.parse_args()

    searches = load_searches(args.input_dir)
    encoded = encoded_payload(build(searches))
    if args.check:
        try:
            current = OUTPUT.read_bytes()
        except OSError as exc:
            raise BuildError(f"cannot read canonical output {OUTPUT}: {exc}") from exc
        if current != encoded:
            raise BuildError("canonical top-50 output is stale; rerun the builder")
        print(f"OK {OUTPUT.relative_to(ROOT)} ({len(encoded)} bytes)")
        return

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_bytes(encoded)
    print(f"Wrote {OUTPUT.relative_to(ROOT)} ({len(encoded)} bytes)")


if __name__ == "__main__":
    main()
