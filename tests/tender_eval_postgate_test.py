"""Evaluate the selection users receive, while preserving raw model mistakes."""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "eval"))
from run_tender_eval import apply_production_postgates


class TenderPostgateTest(unittest.TestCase):
    def test_scope_and_confidence_use_production_validator(self):
        fixtures = [
            ("Construction of footpath on MG Road", .99, "non_road_work_scope"),
            ("Asphalting of MG Road", .1, "no_confident_match"),
            ("Asphalting of MG Road", .99, None),
        ]
        cases, rows = [], []
        for i, (title, confidence, _) in enumerate(fixtures):
            cases.append({"id": str(i), "address": "MG Road, Bengaluru",
                          "candidates": [{"tender_number": str(i), "title": title}]})
            rows.append({"case_id": str(i), "shortlist": [str(i)], "match_index": 0,
                         "confidence": confidence, "predicted_tender_number": str(i)})
        apply_production_postgates(rows, cases, .8)
        for i, (_, _, reason) in enumerate(fixtures):
            with self.subTest(i=i):
                self.assertEqual(rows[i]["raw_model_tender_number"], str(i))
                self.assertEqual(rows[i]["postgate_reason"], reason)
                self.assertEqual(rows[i]["predicted_tender_number"], None if reason else str(i))


if __name__ == "__main__":
    unittest.main()
