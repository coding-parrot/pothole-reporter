#!/usr/bin/env python3
"""Assert the Andhra source blocker stays explicit and cannot masquerade as data."""

import json
import pathlib
import unittest
from datetime import datetime


ROOT = pathlib.Path(__file__).resolve().parent.parent
BLOCKER = (
    ROOT
    / "data"
    / "custom-road-tenders"
    / "ap"
    / "in-ap-eprocurement-blocker.json"
)


class AndhraPradeshRoadTenderSourceTest(unittest.TestCase):
    def test_blocker_is_source_specific_and_publishes_no_records(self):
        payload = json.loads(BLOCKER.read_text(encoding="utf-8"))
        self.assertEqual(payload["format"], "official-road-tender-source-blocker")
        self.assertEqual(payload["source_id"], "in-ap-eprocurement")
        self.assertEqual(payload["state_code"], "AP")
        self.assertEqual(payload["status"], "blocked_without_bypassing_portal_controls")
        self.assertEqual(payload["records_published"], 0)
        self.assertTrue(payload["public_access_verified"])
        self.assertFalse(payload["captcha_encountered"])
        self.assertNotIn("notices", payload)
        self.assertNotIn("contractor", payload)
        datetime.fromisoformat(payload["checked_at"].replace("Z", "+00:00"))

    def test_blocker_names_both_missing_machine_contracts(self):
        payload = json.loads(BLOCKER.read_text(encoding="utf-8"))
        reason = payload["reason"].casefold()
        self.assertIn("encrypted", reason)
        self.assertIn("publication timestamp", reason)
        self.assertIn("must not fabricate", reason)
        self.assertIn("documented public export/api", payload["required_resolution"].casefold())


if __name__ == "__main__":
    unittest.main(verbosity=2)
