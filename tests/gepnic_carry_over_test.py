#!/usr/bin/env python3
"""A portal that is down for one crawl keeps the receipt of the crawl before it.

Run 37573977613 (7 Oct 2026) could not reach West Bengal's main portal and the State
fell from 874 notices to 55, though the receipt fetched on 5 Oct was two days into a
seven-day review window. These tests drive the real orchestrator and the real pack
builder against portals that answer or stay down; nothing touches the network.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, TOOLS / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


PULLER = _load("pull_india_gepnic_carry_over", "pull-india-gepnic.py")
BUILDER = _load("gepnic_road_notice_pack_builder_carry_over", "build-gepnic-road-notice-packs.py")

FIRST_CRAWL = "2026-10-05T05:42:16Z"
SECOND_CRAWL = "2026-10-07T04:59:04Z"
WB_MAIN = "in-wb-gepnic-current"

# source id -> (jurisdiction, State code, tender ids the portal lists)
PORTALS = {
    "in-as-gepnic": ("Assam", "AS", ["2026_ASPWD_1", "2026_ASPWD_2"]),
    "in-la-gepnic": ("Ladakh", "LA", ["2026_LAPWD_1"]),
    WB_MAIN: ("West Bengal", "WB", ["2026_WBPWD_1", "2026_WBPWD_2", "2026_WBPWD_3"]),
    "in-wb-gepnic-legacy": ("West Bengal", "WB", ["2026_WBPWD_9"]),
}


def listing_url(source_id: str) -> str:
    return f"https://{source_id}.example.gov.in/nicgep/app"


def registry(portals: dict = PORTALS) -> dict:
    jurisdictions: dict[str, dict] = {}
    for source_id, (name, state_code, _tender_ids) in sorted(portals.items()):
        jurisdiction = jurisdictions.setdefault(
            state_code, {"code": f"IN-{state_code}", "name": name, "sources": []}
        )
        jurisdiction["sources"].append({
            "id": source_id,
            "portal_family": "nic_gepnic",
            "listing_url": listing_url(source_id),
        })
    return {"jurisdictions": list(jurisdictions.values())}


class Portals:
    """Stands in for pull-gepnic-tenders.py: each portal answers, or stays down."""

    class CrawlerError(RuntimeError):
        pass

    def __init__(self, portals: dict = PORTALS, down: tuple[str, ...] = ()) -> None:
        self.by_url = {
            listing_url(source_id)
            + "?component=clear&page=FrontEndTendersByOrganisation&service=direct": (
                source_id, tender_ids
            )
            for source_id, (_name, _state_code, tender_ids) in portals.items()
        }
        self.down = set(down)

    def compile_allowlist(self, patterns):
        return tuple(patterns)

    def crawl_live(self, *, source_url, source_name, state_code, retrieved_at, **_):
        source_id, tender_ids = self.by_url[source_url]
        if source_id in self.down:
            raise self.CrawlerError(f"failed to fetch {source_url}: <urlopen error timed out>")
        portal = listing_url(source_id)
        notices = []
        for tender_id in tender_ids:
            detail = portal + f"?page=FrontEndViewTender&service=direct&id={tender_id}"
            notices.append({
                "closing_at": "2026-10-20T17:00:00+05:30",
                "detail_url": detail,
                "lifecycle": "procurement_notice",
                "listing_url": portal + "?page=FrontEndTendersByOrganisation&service=direct",
                "opening_at": "2026-10-21T11:00:00+05:30",
                "organisation_chain": "Public Works Department||Road Division",
                "organisation_path": ["Public Works Department", "Road Division"],
                "published_at": "2026-10-01T10:00:00+05:30",
                "retrieved_at": retrieved_at,
                "scope": "road_surface",
                "source_name": source_name,
                "source_url": detail,
                "state_code": state_code,
                "tender_id": tender_id,
                "tender_reference": f"NIT/{tender_id}",
                "title": f"Special repairs and resurfacing of NH 44 for {tender_id}",
            })
        return {
            "format": "gepnic-road-surface-procurement-notices",
            "schema_version": 1,
            "source_name": source_name,
            "source_url": source_url,
            "retrieved_at": retrieved_at,
            "state_code": state_code,
            "lifecycle": "procurement_notice",
            "organisations": ["Public Works Department"],
            "rows_scanned": len(notices) + 4,
            "rows_excluded_by_scope": 4,
            "notices": notices,
        }


def crawl(
    root: Path,
    retrieved_at: str,
    *,
    previous: Path | None = None,
    down: tuple[str, ...] = (),
    portals: dict = PORTALS,
    log: io.StringIO | None = None,
) -> tuple[int, dict]:
    """Run the orchestrator's main() as the workflow does; return its exit and ledger."""
    registry_path = root / BUILDER.REGISTRY_PATH
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(json.dumps(registry(portals), indent=2) + "\n", encoding="utf-8")
    output = root / "data" / "gepnic-road-notices"
    argv = [
        "pull-india-gepnic.py", "--output-dir", str(output),
        "--retrieved-at", retrieved_at, "--allow-partial",
    ]
    if previous is not None:
        argv += ["--previous-sources", str(previous)]
    with mock.patch.object(sys, "argv", argv), \
            mock.patch.object(PULLER, "_load_crawler", return_value=Portals(portals, down)), \
            mock.patch.object(PULLER, "_read_registry", return_value=registry(portals)), \
            mock.patch.object(PULLER.time, "sleep"), \
            contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(log if log is not None else io.StringIO()):
        status = PULLER.main()
    return status, json.loads((output / "crawl-report.json").read_text(encoding="utf-8"))


def stage_aside(root: Path, staging: Path) -> Path:
    """What the workflow does before a crawl: move the last receipts out of the tree."""
    sources = root / BUILDER.SOURCE_DIRECTORY
    staging.mkdir(parents=True, exist_ok=True)
    for path in sorted(sources.glob("*.json")):
        path.rename(staging / path.name)
    return staging


def resource(root: Path, state_code: str) -> dict | None:
    manifest = json.loads((root / BUILDER.MANIFEST_PATHS[0]).read_text(encoding="utf-8"))
    return manifest["resources"].get(f"in-road-notices-{state_code.lower()}")


class GePNICCarryOverTest(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name) / "checkout"
        self.staging = Path(directory.name) / "previous-sources"
        self.sources = self.root / BUILDER.SOURCE_DIRECTORY

    def first_crawl(self, retrieved_at: str = FIRST_CRAWL) -> dict[str, bytes]:
        status, _ = crawl(self.root, retrieved_at)
        self.assertEqual(status, 0)
        BUILDER.build_all(self.root)
        receipts = {path.stem: path.read_bytes() for path in self.sources.glob("*.json")}
        stage_aside(self.root, self.staging)
        return receipts

    def test_failed_portal_keeps_its_two_day_old_receipt_and_its_older_dates(self) -> None:
        before = self.first_crawl()
        self.assertEqual(resource(self.root, "WB")["records"], 4)

        log = io.StringIO()
        status, ledger = crawl(
            self.root, SECOND_CRAWL, previous=self.staging, down=(WB_MAIN,), log=log
        )
        self.assertEqual(status, 3)
        self.assertIn(
            f"::warning::{WB_MAIN}: portal down, kept the receipt of {FIRST_CRAWL}",
            log.getvalue(),
        )
        # Kept as it was: the same bytes, so the same retrieved_at as on 5 Oct.
        self.assertEqual((self.sources / f"{WB_MAIN}.json").read_bytes(), before[WB_MAIN])
        self.assertEqual(ledger["carried_over"], [{
            "retrieved_at": FIRST_CRAWL,
            "sha256": hashlib.sha256(before[WB_MAIN]).hexdigest(),
            "source_id": WB_MAIN,
            "state_code": "WB",
        }])
        # It is still a failure of this crawl, and this crawl's own counts leave it out.
        self.assertEqual([item["source_id"] for item in ledger["failures"]], [WB_MAIN])
        self.assertEqual(ledger["source_count_failed"], 1)
        self.assertEqual(ledger["source_count_succeeded"], 3)
        self.assertEqual(ledger["states"]["WB"], {
            "sources": 1, "rows_scanned": 5, "rows_excluded_by_scope": 4, "notices": 1,
        })

        BUILDER.build_all(self.root)
        BUILDER.verify_all(self.root)
        west_bengal = resource(self.root, "WB")
        self.assertEqual(west_bengal["records"], 4)
        self.assertEqual(west_bengal["sources"], 2)
        self.assertEqual(west_bengal["source_retrieved_at"], "2026-10-05")
        self.assertEqual(west_bengal["review_after"], "2026-10-12")
        assam = resource(self.root, "AS")
        self.assertEqual(assam["source_retrieved_at"], "2026-10-07")
        self.assertEqual(assam["review_after"], "2026-10-14")

    def test_receipt_older_than_the_review_window_is_dropped(self) -> None:
        # Eight days before the second crawl, to the second.
        self.first_crawl("2026-09-29T04:59:04Z")
        status, ledger = crawl(
            self.root, SECOND_CRAWL, previous=self.staging, down=(WB_MAIN, "in-la-gepnic")
        )
        self.assertEqual(status, 3)
        self.assertEqual(ledger["carried_over"], [])
        self.assertFalse((self.sources / f"{WB_MAIN}.json").exists())
        self.assertFalse((self.sources / "in-la-gepnic.json").exists())
        BUILDER.build_all(self.root)
        west_bengal = resource(self.root, "WB")
        self.assertEqual(west_bengal["records"], 1)
        self.assertEqual(west_bengal["source_retrieved_at"], "2026-10-07")
        self.assertIsNone(resource(self.root, "LA"))

    def test_receipt_is_kept_to_the_last_second_of_the_window_and_no_longer(self) -> None:
        for first, kept in (("2026-09-30T04:59:04Z", True), ("2026-09-30T04:59:03Z", False)):
            with self.subTest(first=first):
                with tempfile.TemporaryDirectory() as directory:
                    self.root = Path(directory) / "checkout"
                    self.staging = Path(directory) / "previous-sources"
                    self.sources = self.root / BUILDER.SOURCE_DIRECTORY
                    self.first_crawl(first)
                    _, ledger = crawl(
                        self.root, SECOND_CRAWL, previous=self.staging, down=(WB_MAIN,)
                    )
                    self.assertEqual(
                        [item["retrieved_at"] for item in ledger["carried_over"]],
                        [first] if kept else [],
                    )
                    BUILDER.build_all(self.root)

    def test_window_is_the_builders_review_window(self) -> None:
        self.assertEqual(PULLER.CARRY_OVER_DAYS, BUILDER.REVIEW_DAYS)

    def test_only_the_failed_source_s_own_dated_receipt_is_carried(self) -> None:
        def another_source(value: dict) -> bytes:
            value["source_id"] = "in-wb-gepnic-legacy"
            return json.dumps(value).encode()

        def another_state(value: dict) -> bytes:
            value["state_code"] = "AS"
            return json.dumps(value).encode()

        def newer_than_the_crawl(value: dict) -> bytes:
            value["retrieved_at"] = "2026-10-08T00:00:00Z"
            return json.dumps(value).encode()

        def fetched_by_this_crawl(value: dict) -> bytes:
            value["retrieved_at"] = SECOND_CRAWL
            return json.dumps(value).encode()

        def undated(value: dict) -> bytes:
            value["retrieved_at"] = "the day before yesterday"
            return json.dumps(value).encode()

        def not_json(value: dict) -> bytes:
            return b"<html>503 Service Unavailable</html>"

        def not_a_receipt(value: dict) -> bytes:
            return b"[]"

        for change in (
            another_source, another_state, newer_than_the_crawl, fetched_by_this_crawl,
            undated, not_json, not_a_receipt,
        ):
            with self.subTest(change=change.__name__):
                with tempfile.TemporaryDirectory() as directory:
                    self.root = Path(directory) / "checkout"
                    self.staging = Path(directory) / "previous-sources"
                    self.sources = self.root / BUILDER.SOURCE_DIRECTORY
                    before = self.first_crawl()
                    (self.staging / f"{WB_MAIN}.json").write_bytes(
                        change(json.loads(before[WB_MAIN]))
                    )
                    status, ledger = crawl(
                        self.root, SECOND_CRAWL, previous=self.staging, down=(WB_MAIN,)
                    )
                    self.assertEqual(status, 3)
                    self.assertEqual(ledger["carried_over"], [])
                    self.assertFalse((self.sources / f"{WB_MAIN}.json").exists())
                    BUILDER.build_all(self.root)

    def test_builder_refuses_a_carried_receipt_changed_after_the_crawl(self) -> None:
        self.first_crawl()
        crawl(self.root, SECOND_CRAWL, previous=self.staging, down=(WB_MAIN,))
        path = self.sources / f"{WB_MAIN}.json"
        tampered = json.loads(path.read_bytes())
        tampered["notices"][0]["title"] = "Resurfacing of NH 44 at another place"
        path.write_text(json.dumps(tampered, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(BUILDER.BuildError, "sha256 differs from its receipt"):
            BUILDER.build_all(self.root)

    def test_source_that_left_the_registry_cannot_linger(self) -> None:
        before = self.first_crawl()
        remaining = {key: value for key, value in PORTALS.items() if key != "in-la-gepnic"}
        self.assertIn("in-la-gepnic", before)
        status, ledger = crawl(
            self.root, SECOND_CRAWL, previous=self.staging, down=(WB_MAIN,),
            portals=remaining,
        )
        self.assertEqual(status, 3)
        self.assertEqual(
            sorted(path.stem for path in self.sources.glob("*.json")), sorted(remaining)
        )
        self.assertEqual([item["source_id"] for item in ledger["carried_over"]], [WB_MAIN])
        BUILDER.build_all(self.root)
        self.assertIsNone(resource(self.root, "LA"))

    def test_previous_receipts_must_be_staged_outside_the_output_directory(self) -> None:
        self.first_crawl()
        for path in self.staging.glob("*.json"):
            path.rename(self.sources / path.name)
        report_before = (self.root / BUILDER.CRAWL_REPORT_PATH).read_bytes()
        for inside in (self.sources, self.sources.parent, self.sources.parent / "previous"):
            with self.subTest(inside=inside.name):
                log = io.StringIO()
                with self.assertRaises(SystemExit) as refused:
                    crawl(self.root, SECOND_CRAWL, previous=inside, down=(WB_MAIN,), log=log)
                self.assertEqual(refused.exception.code, 2)
                self.assertIn("must be staged outside --output-dir", log.getvalue())
        self.assertEqual(
            (self.root / BUILDER.CRAWL_REPORT_PATH).read_bytes(), report_before
        )

    def test_clean_crawl_carries_nothing_and_overwrites_every_receipt(self) -> None:
        self.first_crawl()
        status, ledger = crawl(self.root, SECOND_CRAWL, previous=self.staging)
        self.assertEqual(status, 0)
        self.assertEqual(ledger["carried_over"], [])
        self.assertEqual(ledger["failures"], [])
        self.assertEqual(
            {json.loads(path.read_bytes())["retrieved_at"] for path in self.sources.glob("*.json")},
            {SECOND_CRAWL},
        )
        BUILDER.build_all(self.root)
        self.assertEqual(resource(self.root, "WB")["source_retrieved_at"], "2026-10-07")

    def test_without_previous_receipts_a_failed_portal_costs_its_notices(self) -> None:
        self.first_crawl()
        status, ledger = crawl(self.root, SECOND_CRAWL, down=(WB_MAIN,))
        self.assertEqual(status, 3)
        self.assertEqual(ledger["carried_over"], [])
        BUILDER.build_all(self.root)
        self.assertEqual(resource(self.root, "WB")["records"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
