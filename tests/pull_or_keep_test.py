#!/usr/bin/env python3

import importlib.util
import json
import pathlib
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("pull_or_keep", ROOT / "tools" / "pull-or-keep.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)

NOW = datetime(2026, 10, 7, 3, 0, tzinfo=timezone.utc)


class PullOrKeepTest(unittest.TestCase):
    def run_wrapper(self, statuses, retrieved_at, restored=True, **options):
        with tempfile.TemporaryDirectory() as directory:
            output = pathlib.Path(directory) / "snapshot.json"
            calls, lines = [], []

            def run(command):
                calls.append(command)
                return statuses[len(calls) - 1]

            def restore(path):
                if restored:
                    path.write_text(json.dumps({"retrieved_at": retrieved_at}), encoding="utf-8")
                return restored

            status = MODULE.pull_or_keep(
                output, ["puller"], run=run, restore=restore, sleep=lambda seconds: None,
                now=lambda: NOW, log=lines.append, **options,
            )
            return status, len(calls), lines

    def test_a_working_portal_is_pulled_once(self):
        status, calls, lines = self.run_wrapper([0], "2026-10-05T05:42:16Z")
        self.assertEqual((status, calls, lines), (0, 1, []))

    def test_a_portal_that_recovers_is_retried(self):
        status, calls, lines = self.run_wrapper([1, 0], "2026-10-05T05:42:16Z")
        self.assertEqual((status, calls), (0, 2))
        self.assertIn("attempt 1 of 3 exited 1", lines[0])

    def test_a_dead_portal_keeps_a_snapshot_inside_the_review_window(self):
        status, calls, lines = self.run_wrapper([1, 1, 1], "2026-10-05T05:42:16Z")
        self.assertEqual((status, calls), (0, 3))
        self.assertIn("kept the snapshot from 1 day(s) ago", lines[-1])

    def test_a_snapshot_past_the_review_window_is_never_republished(self):
        old = (NOW - timedelta(days=8)).strftime("%Y-%m-%dT%H:%M:%SZ")
        status, _, lines = self.run_wrapper([1, 1, 1], old)
        self.assertEqual(status, 1)
        self.assertIn("past the review window", lines[-1])

    def test_a_dead_portal_with_no_committed_snapshot_fails(self):
        status, _, lines = self.run_wrapper([1, 1, 1], None, restored=False)
        self.assertEqual(status, 1)
        self.assertIn("no committed snapshot", lines[-1])

    def test_an_undated_snapshot_fails(self):
        status, _, lines = self.run_wrapper([1, 1, 1], "yesterday")
        self.assertEqual(status, 1)
        self.assertIn("cannot be dated", lines[-1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
