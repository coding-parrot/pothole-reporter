#!/usr/bin/env python3
"""Reviewed pins for the State/UT coverage added in the v1.35 catalog."""

from __future__ import annotations


RETRIEVED_AT = "2026-08-26"


def _authority(
    code: str,
    label: str,
    handoff_name: str,
    handoff_url: str,
    aliases: list[str],
    **optional: str,
) -> dict[str, object]:
    value: dict[str, object] = {
        "id": f"{code.lower()}-statewide-unverified",
        "name": f"{label} authority (select in {handoff_name})",
        "aliases": aliases,
        "handoff_name": handoff_name,
        "handoff_url": handoff_url,
    }
    value.update(optional)
    return value


# The relation, geometry digest and bounding boxes were reviewed together. A builder
# refuses to publish if Nominatim returns another object or any coordinate changes.
JURISDICTIONS: tuple[dict[str, object], ...] = (
    {
        "code": "AR", "kind": "state", "label": "Arunachal Pradesh",
        "relation_id": 2027346, "region_id": "arunachal-pradesh-state",
        "geometry_sha256": "cf8ed446e02b71170c565a43fec2ac4dc730f10f13adf34ab6e95ea4e898134d",
        "bbox": {"min_lng": 91.5623082, "min_lat": 26.650863, "max_lng": 97.3950905, "max_lat": 29.3745566},
        "source_bbox": [26.650863, 29.3745566, 91.5623082, 97.3950905],
        "inside": {"itanagar": (27.0844, 93.6053)},
        "authority": _authority("AR", "Arunachal Pradesh", "CM e-Jan Sunwai", "https://cmejansunwai.arunachal.gov.in/", ["arunachal pradesh"], helpline="1076"),
    },
    {
        "code": "AS", "kind": "state", "label": "Assam",
        "relation_id": 2025886, "region_id": "assam-state",
        "geometry_sha256": "fd8ac250fc17bfb9c07ab2035d84d874195bd98db60e5bc98731c136a83a59f4",
        "bbox": {"min_lng": 89.6986005, "min_lat": 24.136033, "max_lng": 96.0124397, "max_lat": 27.9712428},
        "source_bbox": [24.136033, 27.9712428, 89.6986005, 96.0124397],
        "inside": {"guwahati": (26.1445, 91.7362)},
        "authority": _authority("AS", "Assam", "CPGRAMS", "https://pgportal.gov.in/", ["assam", "অসম"]),
    },
    {
        "code": "GJ", "kind": "state", "label": "Gujarat", "pack_id": "in-gj-state-routing",
        "relation_id": 1949080, "region_id": "gujarat-state",
        "geometry_sha256": "11539b5cd872f93d70f7f1ba5fad4ef6fa237e808ff9721d0d6523ec5850024f",
        "bbox": {"min_lng": 68.1756585, "min_lat": 20.1195321, "max_lng": 74.4764325, "max_lat": 24.7118932},
        "source_bbox": [20.1195321, 24.7118932, 68.1756585, 74.4764325],
        "inside": {"gandhinagar": (23.2156, 72.6369)},
        "authority": _authority("GJ", "Gujarat", "SWAGAT", "https://swagat.gujarat.gov.in/", ["gujarat", "ગુજરાત"]),
    },
    {
        "code": "HR", "kind": "state", "label": "Haryana",
        "relation_id": 1942601, "region_id": "haryana-state",
        "geometry_sha256": "3c0c4ddb274c2794ae25aaae3a827c2cbf3d846a49a81cf57ede9161ea5aab14",
        "bbox": {"min_lng": 74.4735074, "min_lat": 27.6526273, "max_lng": 77.6021432, "max_lat": 30.9287706},
        "source_bbox": [27.6526273, 30.9287706, 74.4735074, 77.6021432],
        "inside": {"gurugram": (28.4595, 77.0266)},
        "authority": _authority("HR", "Haryana", "CPGRAMS", "https://pgportal.gov.in/", ["haryana", "हरियाणा"]),
    },
    {
        "code": "HP", "kind": "state", "label": "Himachal Pradesh",
        "relation_id": 364186, "region_id": "himachal-pradesh-state",
        "geometry_sha256": "6dd4a1580243a28e582a9eed78d8c005c07654602c3318c846c1077b13221b66",
        "bbox": {"min_lng": 75.5940055, "min_lat": 30.3771701, "max_lng": 79.0123843, "max_lat": 33.2556686},
        "source_bbox": [30.3771701, 33.2556686, 75.5940055, 79.0123843],
        "inside": {"shimla": (31.1048, 77.1734)},
        "authority": _authority("HP", "Himachal Pradesh", "eSamadhan", "https://esamadhan.nic.in/welcome.aspx", ["himachal pradesh", "हिमाचल प्रदेश"], helpline="01772880490"),
    },
    {
        "code": "JH", "kind": "state", "label": "Jharkhand",
        "relation_id": 1960191, "region_id": "jharkhand-state",
        "geometry_sha256": "14279df76d745224f5bb9c4076c73a7b5bdaa6f48aa56253f41507500ee46fd7",
        "bbox": {"min_lng": 83.3281137, "min_lat": 21.9700317, "max_lng": 87.9628253, "max_lat": 25.3489225},
        "source_bbox": [21.9700317, 25.3489225, 83.3281137, 87.9628253],
        "inside": {"ranchi": (23.3441, 85.3096)},
        "authority": _authority("JH", "Jharkhand", "CPGRAMS", "https://pgportal.gov.in/", ["jharkhand", "झारखंड", "झारखण्ड"], alternate_handoff_name="Jharkhand municipal PGMS", alternate_handoff_url="https://pgms.dmajharkhand.in/index.aspx"),
    },
    {
        "code": "MN", "kind": "state", "label": "Manipur",
        "relation_id": 2027869, "region_id": "manipur-state",
        "geometry_sha256": "1e1fac685f3309d7692c2b3164a5e6b69d26b51e7dd57cae5399ed21cbd984e3",
        "bbox": {"min_lng": 92.9707074, "min_lat": 23.8336205, "max_lng": 94.745244, "max_lat": 25.6921015},
        "source_bbox": [23.8336205, 25.6921015, 92.9707074, 94.745244],
        "inside": {"imphal": (24.8170, 93.9368)},
        "authority": _authority("MN", "Manipur", "GovConnect Manipur", "https://govconnectmanipur.mn.gov.in/", ["manipur", "মণিপুর"]),
    },
    {
        "code": "ML", "kind": "state", "label": "Meghalaya",
        "relation_id": 2027521, "region_id": "meghalaya-state",
        "geometry_sha256": "db598e097615cee41266f4002c503a0b26ca6926eb470360dc38bd6a1d563bcf",
        "bbox": {"min_lng": 89.814444, "min_lat": 25.0306475, "max_lng": 92.8027367, "max_lat": 26.1181651},
        "source_bbox": [25.0306475, 26.1181651, 89.814444, 92.8027367],
        "inside": {"shillong": (25.5788, 91.8933)},
        "authority": _authority("ML", "Meghalaya", "CM Connect", "https://cmconnect.meghalaya.gov.in/", ["meghalaya"], helpline="1971"),
    },
    {
        "code": "MZ", "kind": "state", "label": "Mizoram",
        "relation_id": 2029046, "region_id": "mizoram-state",
        "geometry_sha256": "1b968536683a5c4940b0da3365c8bb682a145be2f11aff152c498edbd6af2802",
        "bbox": {"min_lng": 92.2602224, "min_lat": 21.9400528, "max_lng": 93.4373696, "max_lat": 24.5231304},
        "source_bbox": [21.9400528, 24.5231304, 92.2602224, 93.4373696],
        "inside": {"aizawl": (23.7271, 92.7176)},
        "authority": _authority("MZ", "Mizoram", "Mipui Aw", "https://mipuiaw.mizoram.gov.in/", ["mizoram"]),
    },
    {
        "code": "NL", "kind": "state", "label": "Nagaland",
        "relation_id": 2027973, "region_id": "nagaland-state",
        "geometry_sha256": "af89c3e42da64a065bf363339818b8d547aec93073ffd6e2f08680abc3777949",
        "bbox": {"min_lng": 93.3267005, "min_lat": 25.1984274, "max_lng": 95.2423775, "max_lat": 27.035801},
        "source_bbox": [25.1984274, 27.035801, 93.3267005, 95.2423775],
        "inside": {"kohima": (25.6751, 94.1086)},
        "authority": _authority("NL", "Nagaland", "CPGRAMS", "https://pgportal.gov.in/", ["nagaland"]),
    },
    {
        "code": "SK", "kind": "state", "label": "Sikkim",
        "relation_id": 1791324, "region_id": "sikkim-state",
        "geometry_sha256": "4eaaf81eec2214272b63a7285f8a02bb6ccf008a3f51ef57c4f888574b3c04e8",
        "bbox": {"min_lng": 88.0120333, "min_lat": 27.0792596, "max_lng": 88.9211683, "max_lat": 28.1240465},
        "source_bbox": [27.0792596, 28.1240465, 88.0120333, 88.9211683],
        "inside": {"gangtok": (27.3314, 88.6138)},
        "authority": _authority("SK", "Sikkim", "CPGRAMS", "https://pgportal.gov.in/", ["sikkim"], alternate_handoff_name="Sikkim State Portal", alternate_handoff_url="https://www.sikkim.gov.in/"),
    },
    {
        "code": "TR", "kind": "state", "label": "Tripura",
        "relation_id": 2026458, "region_id": "tripura-state",
        "geometry_sha256": "83f92257ffa7e8b496f3974b671fb68c29c212c0780c9170a189d50a7cd1fde8",
        "bbox": {"min_lng": 91.1508098, "min_lat": 22.9376106, "max_lng": 92.33585, "max_lat": 24.530878},
        "source_bbox": [22.9376106, 24.530878, 91.1508098, 92.33585],
        "inside": {"agartala": (23.8315, 91.2868)},
        "authority": _authority("TR", "Tripura", "CM Helpline", "https://cmhelpline.tripura.gov.in/", ["tripura"], alternate_handoff_name="Tripura grievance portal", alternate_handoff_url="https://grievance.tripura.gov.in/", whatsapp_url="https://wa.me/916033374544", helpline="1905"),
    },
    {
        "code": "UK", "kind": "state", "label": "Uttarakhand",
        "relation_id": 9987086, "region_id": "uttarakhand-state",
        "geometry_sha256": "b80016e74aee194fe734062b237855b3a3c8458a74dc345c07657fbc9cdd620a",
        "bbox": {"min_lng": 77.57133, "min_lat": 28.7243243, "max_lng": 81.044789, "max_lat": 31.459016},
        "source_bbox": [28.7243243, 31.459016, 77.57133, 81.044789],
        "inside": {"dehradun": (30.3165, 78.0322)},
        "authority": _authority("UK", "Uttarakhand", "CM Helpline", "https://cmhelpline.uk.gov.in/", ["uttarakhand", "उत्तराखण्ड", "उत्तराखंड"], helpline="1905"),
    },
    {
        "code": "AN", "kind": "union_territory", "label": "Andaman and Nicobar Islands",
        "relation_id": 2025855, "region_id": "andaman-and-nicobar-islands-ut",
        "geometry_sha256": "8fef75a710cb1c1f3b33f5c13253870a2c9d077768c354f65d4558bac0a2b090",
        "bbox": {"min_lng": 92.2042072, "min_lat": 6.7562674, "max_lng": 94.2773214, "max_lat": 13.6753133},
        "source_bbox": [6.7562674, 13.6753133, 92.2042072, 94.2773214],
        "inside": {"port-blair": (11.6234, 92.7265), "car-nicobar": (9.1667, 92.75)},
        "authority": _authority("AN", "Andaman and Nicobar Islands", "CPGRAMS", "https://pgportal.gov.in/", ["andaman and nicobar islands", "andaman & nicobar islands"]),
    },
    {
        "code": "CH", "kind": "union_territory", "label": "Chandigarh",
        "relation_id": 1942809, "region_id": "chandigarh-ut",
        "geometry_sha256": "937c65d7f4fb34ddace22a55f32cc87fb6ca409b1f2586babbe4ca185cc6074d",
        "bbox": {"min_lng": 76.7049857, "min_lat": 30.664974, "max_lng": 76.849028, "max_lat": 30.7949512},
        "source_bbox": [30.664974, 30.7949512, 76.7049857, 76.849028],
        "inside": {"chandigarh": (30.7333, 76.7794)},
        "authority": _authority("CH", "Chandigarh", "CPGRAMS", "https://pgportal.gov.in/", ["chandigarh", "ਚੰਡੀਗੜ੍ਹ", "चंडीगढ़"]),
    },
    {
        "code": "DH", "kind": "union_territory", "label": "Dadra and Nagar Haveli and Daman and Diu",
        "relation_id": 1952530, "region_id": "dadra-nagar-haveli-daman-diu-ut",
        "geometry_sha256": "64dd2ec5067967a41915ba7e3ea1f7c6f29da0efd8fb91a1ad05ecf750d659bb",
        "bbox": {"min_lng": 70.8734588, "min_lat": 20.0473907, "max_lng": 73.2178258, "max_lat": 20.7677936},
        "source_bbox": [20.0473907, 20.7677936, 70.8734588, 73.2178258],
        "inside": {"silvassa": (20.2766, 73.0083), "daman": (20.3974, 72.8328), "diu": (20.7144, 70.9874)},
        "authority": _authority("DH", "Dadra and Nagar Haveli and Daman and Diu", "CPGRAMS", "https://pgportal.gov.in/", ["dadra and nagar haveli and daman and diu", "dnhdd"]),
    },
    {
        "code": "JK", "kind": "union_territory", "label": "Jammu and Kashmir",
        "relation_id": 1943188, "region_id": "jammu-and-kashmir-ut",
        "geometry_sha256": "50af753f0a7a0ddcc52151bba8abb22024bf1d3629a5b2ead431524f800b9754",
        "bbox": {"min_lng": 73.7500338, "min_lat": 32.2763569, "max_lng": 76.7803165, "max_lat": 34.7871414},
        "source_bbox": [32.2763569, 34.7871414, 73.7500338, 76.7803165],
        "inside": {"jammu": (32.7266, 74.8570), "srinagar": (34.0837, 74.7973)},
        "authority": _authority("JK", "Jammu and Kashmir", "JK Samadhan", "https://samadhan.jk.gov.in/", ["jammu and kashmir", "jammu & kashmir", "जम्मू और कश्मीर"]),
    },
    {
        "code": "LA", "kind": "union_territory", "label": "Ladakh",
        "relation_id": 5515045, "region_id": "ladakh-ut",
        "geometry_sha256": "816c039eb05986e1a75f53e7f4812b334bba73b32b73196b8813c8e90c9ac28c",
        "bbox": {"min_lng": 75.3269726, "min_lat": 32.33574, "max_lng": 79.460728, "max_lat": 35.6729307},
        "source_bbox": [32.33574, 35.6729307, 75.3269726, 79.460728],
        "inside": {"leh": (34.1526, 77.5771)},
        "authority": _authority("LA", "Ladakh", "Ladakh grievance portal", "https://grievance.ladakh.gov.in/", ["ladakh"]),
    },
    {
        "code": "LD", "kind": "union_territory", "label": "Lakshadweep",
        "relation_id": 2027460, "region_id": "lakshadweep-ut",
        "geometry_sha256": "d58545bdcecaa056484fb3ea7c6b3531ce744b5b120f6bac0185c3346cfd3a04",
        "bbox": {"min_lng": 71.5180377, "min_lat": 8.0648198, "max_lng": 73.9061436, "max_lat": 12.6010064},
        "source_bbox": [8.0648198, 12.6010064, 71.5180377, 73.9061436],
        "inside": {"kavaratti": (10.5667, 72.6417)},
        "authority": _authority("LD", "Lakshadweep", "CPGRAMS", "https://pgportal.gov.in/", ["lakshadweep"]),
    },
    {
        "code": "PY", "kind": "union_territory", "label": "Puducherry",
        "relation_id": 107001, "region_id": "puducherry-ut",
        "geometry_sha256": "8bbb3f321588dacbce22ab379d9ba187d8c97a6a61b553f076a6da06a54c7c2d",
        "bbox": {"min_lng": 75.5265863, "min_lat": 10.827721, "max_lng": 82.3137136, "max_lat": 16.7617112},
        "source_bbox": [10.827721, 16.7617112, 75.5265863, 82.3137136],
        "inside": {"puducherry": (11.9416, 79.8083), "karaikal": (10.9254, 79.8380), "mahe": (11.7011, 75.5367), "yanam": (16.7333, 82.2167)},
        "authority": _authority("PY", "Puducherry", "CPGRAMS", "https://pgportal.gov.in/", ["puducherry", "pondicherry", "புதுச்சேரி"]),
    },
)


def pack_id(item: dict[str, object]) -> str:
    return str(item.get("pack_id") or f"in-{str(item['code']).lower()}-routing")


def source_path(item: dict[str, object]) -> str:
    suffix = "state" if item["kind"] == "state" else "ut"
    return f"data/metro-coverage/{str(item['code']).lower()}-{suffix}.json"
