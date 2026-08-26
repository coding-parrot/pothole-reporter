# -*- coding: utf-8 -*-
"""Focused structural checks for the official India tender-source registry."""

import json
import pathlib
import unittest
from urllib.parse import urlparse


ROOT = pathlib.Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "data" / "tender-sources-india.json"

EXPECTED_STATES = {
    "IN-AP": "Andhra Pradesh",
    "IN-AR": "Arunachal Pradesh",
    "IN-AS": "Assam",
    "IN-BR": "Bihar",
    "IN-CT": "Chhattisgarh",
    "IN-GA": "Goa",
    "IN-GJ": "Gujarat",
    "IN-HR": "Haryana",
    "IN-HP": "Himachal Pradesh",
    "IN-JH": "Jharkhand",
    "IN-KA": "Karnataka",
    "IN-KL": "Kerala",
    "IN-MP": "Madhya Pradesh",
    "IN-MH": "Maharashtra",
    "IN-MN": "Manipur",
    "IN-ML": "Meghalaya",
    "IN-MZ": "Mizoram",
    "IN-NL": "Nagaland",
    "IN-OD": "Odisha",
    "IN-PB": "Punjab",
    "IN-RJ": "Rajasthan",
    "IN-SK": "Sikkim",
    "IN-TN": "Tamil Nadu",
    "IN-TG": "Telangana",
    "IN-TR": "Tripura",
    "IN-UP": "Uttar Pradesh",
    "IN-UT": "Uttarakhand",
    "IN-WB": "West Bengal",
}

EXPECTED_UNION_TERRITORIES = {
    "IN-AN": "Andaman and Nicobar Islands",
    "IN-CH": "Chandigarh",
    "IN-DH": "Dadra and Nagar Haveli and Daman and Diu",
    "IN-DL": "National Capital Territory of Delhi",
    "IN-JK": "Jammu and Kashmir",
    "IN-LA": "Ladakh",
    "IN-LD": "Lakshadweep",
    "IN-PY": "Puducherry",
}

REQUIRED_CENTRAL_SOURCE_IDS = {
    "cppp-gepnic",
    "nhai-etenders",
    "nhidcl-tenders",
    "pmgsy-ommas",
    "pmgsy-emarg",
    "pmgsy-data-portal",
}

REQUIRED_SUPPORTED_FIELDS = {
    "tender_id",
    "tender_reference",
    "organisation",
    "title",
    "work_description",
    "location",
    "road_segment_or_chainage",
    "source_documents",
    "contractor",
    "award",
    "work_order_or_agreement",
    "dlp_or_warranty",
}


def iter_urls(value, path="root"):
    """Yield every value stored in a *_url or *_urls field."""
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key.endswith("_url") and child is not None:
                yield child_path, child
            elif key.endswith("_urls"):
                if not isinstance(child, list):
                    raise AssertionError(f"{child_path} must be a list")
                for index, url in enumerate(child):
                    yield f"{child_path}[{index}]", url
            yield from iter_urls(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from iter_urls(child, f"{path}[{index}]")


class TenderSourceRegistryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with REGISTRY_PATH.open(encoding="utf-8") as handle:
            cls.registry = json.load(handle)

    def test_exactly_28_states_and_8_union_territories(self):
        jurisdictions = self.registry["jurisdictions"]
        self.assertEqual(len(jurisdictions), 36)

        states = {
            item["code"]: item["name"]
            for item in jurisdictions
            if item["type"] == "state"
        }
        union_territories = {
            item["code"]: item["name"]
            for item in jurisdictions
            if item["type"] == "union_territory"
        }

        self.assertEqual(states, EXPECTED_STATES)
        self.assertEqual(union_territories, EXPECTED_UNION_TERRITORIES)
        self.assertEqual(len({item["code"] for item in jurisdictions}), 36)

    def test_every_jurisdiction_has_an_official_resolvable_source_profile(self):
        profiles = self.registry["portal_families"]
        source_ids = set()

        for jurisdiction in self.registry["jurisdictions"]:
            self.assertTrue(jurisdiction["sources"], jurisdiction["code"])
            for source in jurisdiction["sources"]:
                self.assertTrue(source["official"], source["id"])
                self.assertIn(source["portal_family"], profiles, source["id"])
                self.assertTrue(source["scope"], source["id"])
                self.assertIn("listing_url", source, source["id"])
                self.assertIn("award_url", source, source["id"])
                self.assertNotIn(source["id"], source_ids)
                source_ids.add(source["id"])

    def test_portal_profiles_document_access_fields_and_limits(self):
        for profile_id, profile in self.registry["portal_families"].items():
            self.assertIn("listing_access", profile, profile_id)
            self.assertIn("award_access", profile, profile_id)
            self.assertIn("supported_fields", profile, profile_id)
            self.assertIn("ingestion_limits", profile, profile_id)
            self.assertIn(
                "documented_api", profile["listing_access"], profile_id
            )
            self.assertEqual(
                set(profile["supported_fields"]),
                REQUIRED_SUPPORTED_FIELDS,
                profile_id,
            )
            self.assertTrue(profile["ingestion_limits"], profile_id)

    def test_required_central_sources_exist(self):
        central_sources = self.registry["central_sources"]
        ids = {source["id"] for source in central_sources}
        self.assertTrue(REQUIRED_CENTRAL_SOURCE_IDS.issubset(ids))

        profiles = self.registry["portal_families"]
        for source in central_sources:
            self.assertTrue(source["official"], source["id"])
            self.assertIn(source["portal_family"], profiles, source["id"])
            self.assertTrue(source["scope"], source["id"])
            self.assertIn("listing_url", source, source["id"])
            self.assertIn("award_url", source, source["id"])

    def test_all_registry_urls_are_https(self):
        urls = list(iter_urls(self.registry))
        self.assertTrue(urls)
        for path, url in urls:
            self.assertIsInstance(url, str, path)
            parsed = urlparse(url)
            self.assertEqual(parsed.scheme, "https", path)
            self.assertTrue(parsed.netloc, path)
            self.assertEqual(url, url.strip(), path)

    def test_candidate_and_verified_semantics_are_explicit(self):
        semantics = self.registry["match_semantics"]
        self.assertEqual(semantics["tender_candidate"]["status"], "candidate")
        self.assertEqual(
            semantics["verified_contract_match"]["status"], "verified"
        )
        self.assertGreaterEqual(
            len(semantics["verified_contract_match"]["required_evidence"]), 6
        )
        self.assertIn("never", semantics["publication_date_rule"].lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
