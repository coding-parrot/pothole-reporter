#!/usr/bin/env python3
"""A portal that drops one connection must not lose its State for the week."""

import importlib.util
import pathlib
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("pull_india_gepnic", ROOT / "tools" / "pull-india-gepnic.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


class Crawler:
    class CrawlerError(RuntimeError):
        pass

    def __init__(self, failures):
        self.failures, self.calls = failures, 0

    def crawl_live(self, **kwargs):
        self.calls += 1
        if self.calls <= self.failures:
            raise ConnectionResetError(104, "Connection reset by peer")
        return {"notices": [], "rows_scanned": 0, "rows_excluded_by_scope": 0, "state_code": kwargs["state_code"]}


class PullRetryTest(unittest.TestCase):
    def test_a_reset_portal_is_asked_again_before_it_counts_as_failed(self):
        crawler = Crawler(failures=2)
        with mock.patch.object(MODULE.time, "sleep") as sleep:
            result = MODULE.crawl_with_retries(crawler, dict(source_url="u", source_name="n", state_code="OD",
                                                             allowlist=(), timeout=1, request_delay=0, retrieved_at="t"))
        self.assertEqual(result["state_code"], "OD")
        self.assertEqual(crawler.calls, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_a_portal_that_stays_down_still_fails(self):
        crawler = Crawler(failures=99)
        with mock.patch.object(MODULE.time, "sleep"):
            with self.assertRaises(ConnectionResetError):
                MODULE.crawl_with_retries(crawler, dict(source_url="u", source_name="n", state_code="OD",
                                                        allowlist=(), timeout=1, request_delay=0, retrieved_at="t"))
        self.assertEqual(crawler.calls, MODULE.CRAWL_ATTEMPTS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
