"""Regression checks for current-schema scoring and Responses transport parsing."""
import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "eval"))
import run_eval


class EvalResponseRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.assessment = dict(image_quality="acceptable", assessment="damaged",
                               damage_type="pothole_cavity", size="medium",
                               description="Visible road cavity.")
        self.payload = dict(id="resp_fixture", status="completed", output=[
            dict(type="message", content=[dict(type="output_text",
                 text=json.dumps(self.assessment))])])

    def test_current_schema_is_not_scored_as_legacy(self):
        for mode in ("manual", "drive"):
            outcome = run_eval.run_bounded_detection_policy(lambda: self.assessment, mode, 1)
            self.assertEqual(outcome.decision, "accept")
            self.assertEqual(outcome.attempts_started, 1)

    def test_json_response(self):
        self.assertEqual(run_eval.parse_api_response(json.dumps(self.payload).encode()),
                         (self.assessment, "resp_fixture"))

    def test_completed_stream(self):
        raw = ('event: response.completed\ndata: ' + json.dumps(dict(
            type="response.completed", response=self.payload)) + '\n\ndata: [DONE]\n').encode()
        self.assertEqual(run_eval.parse_api_response(raw, True), (self.assessment, "resp_fixture"))

    def test_partial_stream_fails_closed(self):
        with self.assertRaises(ValueError):
            run_eval.parse_api_response(b'data: {"type":"response.output_text.delta","delta":"{}"}\n', True)

    def test_incomplete_stream_fails_closed(self):
        with self.assertRaises(ValueError):
            run_eval.parse_api_response(b'data: {"type":"response.incomplete"}\n', True)


if __name__ == "__main__":
    unittest.main()
