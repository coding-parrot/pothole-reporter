#!/usr/bin/env python3
"""Focused offline checks for the public GePNIC organisation crawler."""

from __future__ import annotations

import http.cookiejar
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
FIXTURES = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(TOOLS))

SPEC = importlib.util.spec_from_file_location(
    "pull_gepnic_tenders", TOOLS / "pull-gepnic-tenders.py"
)
assert SPEC and SPEC.loader
CRAWLER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = CRAWLER
SPEC.loader.exec_module(CRAWLER)

SOURCE_URL = CRAWLER.ORGANISATION_URL
ORGANISATION = "Ministry of Road Transport and Highways"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def object_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        keys = set(value)
        for child in value.values():
            keys.update(object_keys(child))
        return keys
    if isinstance(value, list):
        keys: set[str] = set()
        for child in value:
            keys.update(object_keys(child))
        return keys
    return set()


class GePNICTenderCrawlerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.allowlist = CRAWLER.compile_allowlist([
            r"^Ministry of Road Transport and Highways$"
        ])

    def test_public_organisation_links_and_explicit_allowlist(self) -> None:
        links = CRAWLER.parse_organisation_index(
            fixture("gepnic-organisations.html"), SOURCE_URL
        )
        self.assertEqual([link.name for link in links], [
            ORGANISATION,
            "Unrelated Department",
        ])
        self.assertEqual([link.tender_count for link in links], [4, 1])
        self.assertIn("sp=fixture-one", links[0].url)
        self.assertTrue(CRAWLER.organisation_allowed(ORGANISATION, self.allowlist))
        self.assertFalse(CRAWLER.organisation_allowed(
            ORGANISATION, CRAWLER.compile_allowlist([r"Road Transport"])
        ))

    def test_listing_parses_every_row_then_filters_strict_scope(self) -> None:
        rows = CRAWLER.parse_tender_listing(
            fixture("gepnic-organisation-tenders.html"), SOURCE_URL
        )
        self.assertEqual(len(rows), 4)
        self.assertEqual([row.tender_id for row in rows], [
            "2026_MoRTH_923510_1",
            "2026_MoRTH_923615_1",
            "2026_MoRTH_923417_1",
            "2026_MoRTH_923598_1",
        ])
        self.assertEqual([row.road_surface_scope for row in rows], [
            True, False, True, False,
        ])
        self.assertEqual(rows[0].published_at, "2026-08-25T18:15:00+05:30")
        self.assertEqual(rows[0].tender_reference, "Bid Pkg No. 03/2026-27")
        self.assertEqual(rows[0].organisation_path[-1], "RO Kolkata - MoRTH")
        self.assertIn("page=FrontEndViewTender", rows[0].detail_url)
        self.assertIn("Karnataka & adjoining", rows[2].title)

        output = CRAWLER.normalise_output(
            rows,
            source_url=SOURCE_URL,
            retrieved_at="2026-08-26T04:30:00Z",
            state_code="IN",
            allowlist=self.allowlist,
        )
        self.assertEqual(output["rows_scanned"], 4)
        self.assertEqual(output["rows_excluded_by_scope"], 2)
        self.assertEqual(len(output["notices"]), 2)
        self.assertEqual(output["lifecycle"], "procurement_notice")
        self.assertEqual(output["source_name"], "GePNIC public procurement portal")
        self.assertEqual(output["organisations"], [ORGANISATION])
        for notice in output["notices"]:
            self.assertEqual(notice["lifecycle"], "procurement_notice")
            self.assertEqual(notice["source_name"], "GePNIC public procurement portal")
            self.assertEqual(notice["scope"], "road_surface")
            self.assertEqual(notice["state_code"], "IN")
            self.assertEqual(notice["source_url"], notice["detail_url"])
        forbidden_inference_fields = {
            "active_contract", "contractor", "winning_bidder", "award_status",
            "defect_liability_period", "warranty_status",
        }
        self.assertFalse(object_keys(output) & forbidden_inference_fields)

    def test_live_crawl_reuses_one_session_for_index_and_listing(self) -> None:
        calls: list[tuple[str, str | None]] = []
        instances: list[object] = []

        class FakeSession:
            def __init__(self, source_url: str, timeout: float) -> None:
                self.source_url = source_url
                self.timeout = timeout
                instances.append(self)

            def get(self, url: str, *, referer: str | None = None) -> str:
                calls.append((url, referer))
                if url == SOURCE_URL:
                    return fixture("gepnic-organisations.html")
                if "sp=fixture-one" in url:
                    return fixture("gepnic-organisation-tenders.html")
                self.fail_unexpected(url)
                return ""

            @staticmethod
            def fail_unexpected(url: str) -> None:
                raise AssertionError(f"unexpected fetch: {url}")

        output = CRAWLER.crawl_live(
            source_url=SOURCE_URL,
            state_code="IN",
            allowlist=self.allowlist,
            timeout=12.0,
            request_delay=0,
            session_factory=FakeSession,
        )
        self.assertEqual(len(instances), 1)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], (SOURCE_URL, None))
        self.assertEqual(calls[1][1], SOURCE_URL)
        self.assertEqual(len(output["notices"]), 2)
        self.assertEqual(output["source_name"], "GePNIC public procurement portal")

        real_session = CRAWLER.GePNICSession(SOURCE_URL)
        cookie_handlers = [
            handler for handler in real_session.opener.handlers
            if isinstance(handler, urllib.request.HTTPCookieProcessor)
        ]
        self.assertEqual(len(cookie_handlers), 1)
        self.assertIsInstance(real_session.cookie_jar, http.cookiejar.CookieJar)
        self.assertIs(cookie_handlers[0].cookiejar, real_session.cookie_jar)

    def test_offline_cli_never_enters_live_crawl(self) -> None:
        original = CRAWLER.crawl_live

        def forbidden_live_crawl(**_kwargs: object) -> object:
            raise AssertionError("offline parsing attempted a network crawl")

        CRAWLER.crawl_live = forbidden_live_crawl
        try:
            with tempfile.TemporaryDirectory() as directory:
                destination = Path(directory) / "gepnic.json"
                result = CRAWLER.main([
                    "--organisation-regex", r"^Ministry of Road Transport and Highways$",
                    "--state-code", "in",
                    "--source-name", "West Bengal e-Procurement Portal",
                    "--retrieved-at", "2026-08-26T05:00:00Z",
                    "--input-organisation-html",
                    str(FIXTURES / "gepnic-organisation-tenders.html"),
                    "--output", str(destination),
                ])
                self.assertEqual(result, 0)
                output = json.loads(destination.read_text(encoding="utf-8"))
                self.assertEqual(output["state_code"], "IN")
                self.assertEqual(
                    output["source_name"], "West Bengal e-Procurement Portal"
                )
                self.assertEqual(output["rows_scanned"], 4)
                self.assertEqual(output["retrieved_at"], "2026-08-26T05:00:00Z")
                self.assertTrue(all(
                    notice["retrieved_at"] == "2026-08-26T05:00:00Z"
                    for notice in output["notices"]
                ))
                self.assertTrue(all(
                    notice["source_name"] == "West Bengal e-Procurement Portal"
                    for notice in output["notices"]
                ))
        finally:
            CRAWLER.crawl_live = original

    def test_captcha_challenge_fails_closed(self) -> None:
        with self.assertRaisesRegex(CRAWLER.CrawlerError, "CAPTCHA.*will not bypass"):
            CRAWLER.parse_tender_listing(
                "<html><body><p>Provide Captcha and click Search</p></body></html>",
                SOURCE_URL,
            )

    def test_cross_origin_public_link_is_rejected(self) -> None:
        document = fixture("gepnic-organisations.html").replace(
            "/eprocure/app?component=%24DirectLink&amp;page=FrontEndTendersByOrganisation",
            "https://example.invalid/steal?page=FrontEndTendersByOrganisation",
            1,
        )
        with self.assertRaisesRegex(CRAWLER.CrawlerError, "cross-origin"):
            CRAWLER.parse_organisation_index(document, SOURCE_URL)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(GePNICTenderCrawlerTest)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if result.wasSuccessful():
        print("GePNIC TENDER CRAWLER TEST PASS")
    raise SystemExit(0 if result.wasSuccessful() else 1)
