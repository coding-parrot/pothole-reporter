#!/usr/bin/env python3
"""Lock nationwide coverage claims and the top-50 catalog to the accessible map."""

import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "data" / "top-50-cities.json"
MAP_PATH = ROOT / "docs" / "coverage-overview.svg"
SVG_NS = "{http://www.w3.org/2000/svg}"

# Census of India 2011 A-04 (I), first 2011 Persons row for each ranked
# Urban Agglomeration/City entry. Current names and current state/UT codes are
# separate from the frozen Census labels.
EXPECTED = (
    (1, "mumbai", "Greater Mumbai U.A.", "Mumbai", "MH", 18394912),
    (2, "delhi", "Delhi U.A.", "Delhi", "DL", 16349831),
    (3, "kolkata", "Kolkata U.A.", "Kolkata", "WB", 14057991),
    (4, "chennai", "Chennai U.A.", "Chennai", "TN", 8653521),
    (5, "bengaluru", "Bruhat Bangalore U.A.", "Bengaluru", "KA", 8520435),
    (6, "hyderabad", "Hyderabad U.A.", "Hyderabad", "TG", 7677018),
    (7, "ahmedabad", "Ahmadabad U.A.", "Ahmedabad", "GJ", 6357693),
    (8, "pune", "Pune U.A.", "Pune", "MH", 5057709),
    (9, "surat", "Surat U.A.", "Surat", "GJ", 4591246),
    (10, "jaipur", "Jaipur", "Jaipur", "RJ", 3046163),
    (11, "kanpur", "Kanpur U.A.", "Kanpur", "UP", 2920496),
    (12, "lucknow", "Lucknow U.A.", "Lucknow", "UP", 2902920),
    (13, "nagpur", "Nagpur U.A.", "Nagpur", "MH", 2497870),
    (14, "ghaziabad", "Ghaziabad U.A.", "Ghaziabad", "UP", 2375820),
    (15, "indore", "Indore U.A.", "Indore", "MP", 2170295),
    (16, "coimbatore", "Coimbatore U.A.", "Coimbatore", "TN", 2136916),
    (17, "kochi", "Kochi U.A.", "Kochi", "KL", 2119724),
    (18, "patna", "Patna U.A.", "Patna", "BR", 2049156),
    (19, "kozhikode", "Kozhikode U.A.", "Kozhikode", "KL", 2028399),
    (20, "bhopal", "Bhopal U.A.", "Bhopal", "MP", 1886100),
    (21, "thrissur", "Thrissur U.A.", "Thrissur", "KL", 1861269),
    (22, "vadodara", "Vadodara U.A.", "Vadodara", "GJ", 1822221),
    (23, "agra", "Agra U.A.", "Agra", "UP", 1760285),
    (24, "visakhapatnam", "Visakhapatnam", "Visakhapatnam", "AP", 1728128),
    (25, "malappuram", "Malappuram U.A.", "Malappuram", "KL", 1699060),
    (26, "thiruvananthapuram", "Thiruvananthapuram U.A.", "Thiruvananthapuram", "KL", 1679754),
    (27, "kannur", "Kannur U.A.", "Kannur", "KL", 1640986),
    (28, "ludhiana", "Ludhiana", "Ludhiana", "PB", 1618879),
    (29, "nashik", "Nashik U.A.", "Nashik", "MH", 1561809),
    (30, "vijayawada", "Vijayawada U.A.", "Vijayawada", "AP", 1476931),
    (31, "madurai", "Madurai U.A.", "Madurai", "TN", 1465625),
    (32, "varanasi", "Varanasi U.A.", "Varanasi", "UP", 1432280),
    (33, "meerut", "Meerut U.A.", "Meerut", "UP", 1420902),
    (34, "faridabad", "Faridabad", "Faridabad", "HR", 1414050),
    (35, "rajkot", "Rajkot U.A.", "Rajkot", "GJ", 1390640),
    (36, "jamshedpur", "Jamshedpur U.A.", "Jamshedpur", "JH", 1339438),
    (37, "jabalpur", "Jabalpur U.A.", "Jabalpur", "MP", 1268848),
    (38, "srinagar", "Srinagar U.A.", "Srinagar", "JK", 1264202),
    (39, "asansol", "Asansol U.A.", "Asansol", "WB", 1243414),
    (40, "vasai-virar", "Vasai-Virar City", "Vasai-Virar", "MH", 1222390),
    (41, "prayagraj", "Allahabad U.A.", "Prayagraj", "UP", 1212395),
    (42, "dhanbad", "Dhanbad U.A.", "Dhanbad", "JH", 1196214),
    (43, "chhatrapati-sambhajinagar", "Aurangabad U.A.", "Chhatrapati Sambhajinagar", "MH", 1193167),
    (44, "amritsar", "Amritsar U.A.", "Amritsar", "PB", 1183549),
    (45, "jodhpur", "Jodhpur U.A.", "Jodhpur", "RJ", 1138300),
    (46, "ranchi", "Ranchi U.A.", "Ranchi", "JH", 1126720),
    (47, "raipur", "Raipur U.A.", "Raipur", "CG", 1123558),
    (48, "kollam", "Kollam U.A.", "Kollam", "KL", 1110668),
    (49, "gwalior", "Gwalior U.A.", "Gwalior", "MP", 1102884),
    (50, "durg-bhilai", "Durg-Bhilainagar U.A.", "Durg-Bhilai", "CG", 1064222),
)

EXISTING_ROUTES = {
    "mumbai": ("reviewed-specific", "in-mh-routing"),
    "delhi": ("reviewed-specific", "in-dl-routing"),
    "kolkata": ("reviewed-specific", "in-wb-routing"),
    "chennai": ("reviewed-specific", "in-tn-routing"),
    "bengaluru": ("reviewed-specific", "in-ka-routing"),
    "hyderabad": ("reviewed-specific", "in-tg-routing"),
    "ahmedabad": ("reviewed-specific", "in-gj-routing"),
    "pune": ("reviewed-specific", "in-mh-routing"),
    "jaipur": ("statewide-neutral", "in-rj-routing"),
    "nagpur": ("statewide-neutral", "in-mh-routing"),
    "indore": ("statewide-neutral", "in-mp-routing"),
    "kanpur": ("statewide-neutral", "in-up-routing"),
    "lucknow": ("statewide-neutral", "in-up-routing"),
    "ghaziabad": ("statewide-neutral", "in-up-routing"),
    "agra": ("statewide-neutral", "in-up-routing"),
    "varanasi": ("statewide-neutral", "in-up-routing"),
    "meerut": ("statewide-neutral", "in-up-routing"),
    "prayagraj": ("statewide-neutral", "in-up-routing"),
    "coimbatore": ("statewide-neutral", "in-tn-state-routing"),
    "kochi": ("statewide-neutral", "in-kl-routing"),
    "patna": ("statewide-neutral", "in-br-routing"),
    "kozhikode": ("statewide-neutral", "in-kl-routing"),
    "thrissur": ("statewide-neutral", "in-kl-routing"),
    "malappuram": ("statewide-neutral", "in-kl-routing"),
    "thiruvananthapuram": ("statewide-neutral", "in-kl-routing"),
    "kannur": ("statewide-neutral", "in-kl-routing"),
    "ludhiana": ("statewide-neutral", "in-pb-routing"),
    "nashik": ("statewide-neutral", "in-mh-routing"),
    "visakhapatnam": ("statewide-neutral", "in-ap-routing"),
    "vijayawada": ("statewide-neutral", "in-ap-routing"),
    "madurai": ("statewide-neutral", "in-tn-state-routing"),
    "bhopal": ("statewide-neutral", "in-mp-routing"),
    "asansol": ("statewide-neutral", "in-wb-routing"),
    "vasai-virar": ("reviewed-specific", "in-mh-routing"),
    "chhatrapati-sambhajinagar": ("statewide-neutral", "in-mh-routing"),
    "amritsar": ("statewide-neutral", "in-pb-routing"),
    "jabalpur": ("statewide-neutral", "in-mp-routing"),
    "jodhpur": ("statewide-neutral", "in-rj-routing"),
    "raipur": ("statewide-neutral", "in-cg-routing"),
    "durg-bhilai": ("statewide-neutral", "in-cg-routing"),
    "kollam": ("statewide-neutral", "in-kl-routing"),
    "gwalior": ("statewide-neutral", "in-mp-routing"),
}


def check(condition, message, errors):
    if not condition:
        errors.append(message)


def main():
    errors = []
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))

    check(
        set(catalog) == {
            "format",
            "schema_version",
            "source",
            "coverage_tiers",
            "coverage_summary",
            "cities",
        },
        "catalog top-level shape changed",
        errors,
    )
    check(catalog.get("format") == "pothole-reporter-top-50-cities", "catalog format changed", errors)
    check(catalog.get("schema_version") == 1, "catalog schema_version must be 1", errors)

    source = catalog.get("source", {})
    expected_source = {
        "catalog_id": "PC11_A04-I",
        "table": "A-04 (I)",
        "year": 2011,
        "catalog_url": "https://censusindia.gov.in/nada/index.php/catalog/42876",
        "workbook_url": "https://censusindia.gov.in/nada/index.php/catalog/42876/download/46544/CLASS_I.xlsx",
    }
    for key, value in expected_source.items():
        check(source.get(key) == value, f"source.{key} changed", errors)

    check(
        set(catalog.get("coverage_tiers", {}))
        == {"reviewed-specific", "statewide-neutral", "major-city-neutral"},
        "coverage tier catalog changed",
        errors,
    )
    check(
        catalog.get("coverage_summary") == {"total": 50, "available": 50, "pending": 0},
        "coverage summary must be exactly 50 available and 0 pending",
        errors,
    )

    cities = catalog.get("cities", [])
    check(len(cities) == 50, f"catalog has {len(cities)} cities instead of 50", errors)
    expected_city_keys = {
        "rank",
        "id",
        "census_name",
        "current_name",
        "aliases",
        "state_code",
        "population",
        "coverage_status",
        "coverage_tier",
        "pack_id",
    }
    for city in cities:
        city_id = city.get("id", "<missing-id>")
        check(set(city) == expected_city_keys, f"{city_id}: city object shape changed", errors)
        check(bool(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", city_id)), f"{city_id}: invalid stable id", errors)
        aliases = city.get("aliases")
        check(
            isinstance(aliases, list)
            and bool(aliases)
            and all(isinstance(alias, str) and alias.strip() for alias in aliases)
            and len(aliases) == len(set(aliases)),
            f"{city_id}: aliases must be a non-empty unique string list",
            errors,
        )

    actual = tuple(
        (
            city.get("rank"),
            city.get("id"),
            city.get("census_name"),
            city.get("current_name"),
            city.get("state_code"),
            city.get("population"),
        )
        for city in cities
    )
    check(actual == EXPECTED, "official A-04 (I) rank/name/state/population catalog changed", errors)
    check([city.get("rank") for city in cities] == list(range(1, 51)), "ranks must be exactly 1..50", errors)
    check(len({city.get("id") for city in cities}) == 50, "stable city ids must be unique", errors)
    check(
        all(cities[index]["population"] > cities[index + 1]["population"] for index in range(len(cities) - 1)),
        "populations must be strictly descending",
        errors,
    )
    check(all(city.get("coverage_status") == "available" for city in cities), "every city must be available", errors)

    major_city_ids = []
    for city in cities:
        city_id = city.get("id")
        expected_tier, expected_pack = EXISTING_ROUTES.get(
            city_id, ("major-city-neutral", "in-top50-routing")
        )
        check(city.get("coverage_tier") == expected_tier, f"{city_id}: wrong coverage tier", errors)
        check(city.get("pack_id") == expected_pack, f"{city_id}: wrong pack id", errors)
        if expected_tier == "major-city-neutral":
            major_city_ids.append(city_id)
    check(len(major_city_ids) == 8, "exactly 8 cities must use the conservative top-50 route", errors)

    root = ET.parse(MAP_PATH).getroot()
    check(root.tag == SVG_NS + "svg", "coverage map root must be SVG", errors)
    check(root.get("role") == "img", "coverage map must expose role=img", errors)
    check(root.get("aria-labelledby") == "title description", "coverage map accessible labels changed", errors)

    check(root.get("width") == "320" and root.get("height") == "430", "coverage map dimensions changed", errors)
    check(root.get("viewBox") == "0 0 320 430", "coverage map viewBox changed", errors)

    svg_title = root.find(SVG_NS + "title")
    svg_desc = root.find(SVG_NS + "desc")
    check(
        svg_title is not None and (svg_title.text or "") == "Pothole Reporter India coverage map",
        "coverage map title missing",
        errors,
    )
    description = "" if svg_desc is None else " ".join("".join(svg_desc.itertext()).split())
    check(
        "all 28 states and 8 Union Territories" in description,
        "coverage map description must claim exactly 28 states and 8 Union Territories",
        errors,
    )
    check(
        "CC0 boundary follows the Survey of India boundary standard" in description,
        "coverage map description must identify the reusable boundary and official standard",
        errors,
    )
    check("No government endorsement is implied" in description, "coverage map must disclaim endorsement", errors)

    metadata = root.find(SVG_NS + "metadata")
    metadata_text = "" if metadata is None else " ".join("".join(metadata.itertext()).split())
    check("5ed214bf77788f99066e3542cccd4a52cb042896" in metadata_text, "boundary commit is not pinned", errors)
    check(
        "5e44c39b18aa8fe57267d8018fa4ad4a10eaa3aa4cb7cb7382a1813ef8eb8c53" in metadata_text,
        "boundary input checksum is not pinned",
        errors,
    )
    check("simplified to 0.025 degrees" in metadata_text, "boundary display transform is undocumented", errors)

    element_ids = {element.get("id") for element in root.iter() if element.get("id")}
    for geometry_id in (
        "india-mainland-outline",
        "lakshadweep-outline",
        "andaman-nicobar-outline",
    ):
        check(geometry_id in element_ids, f"coverage map lost {geometry_id}", errors)

    visible_text = [" ".join("".join(element.itertext()).split()) for element in root.iter(SVG_NS + "text")]
    check(visible_text == ["Covers all states and UTs."], "coverage image must contain only its one-line heading", errors)
    check(not list(root.iter(SVG_NS + "circle")), "coverage image must not contain city dots", errors)
    check(
        not any(element.get("data-city-rank") for element in root.iter()),
        "coverage image must not contain city marker metadata",
        errors,
    )

    if errors:
        print("COVERAGE MAP TEST FAIL")
        for error in errors:
            print(f"- {error}")
        return 1

    print("COVERAGE MAP TEST PASS (single heading, India outline, no dots)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
