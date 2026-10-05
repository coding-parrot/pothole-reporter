import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('verdicts', ROOT / 'tools/harness/verdicts.py')
verdicts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verdicts)


class HarnessVerdicts(unittest.TestCase):
    def test_previous_false_green_export_is_rejected(self):
        alerts = ['Footage analysed: 19 new road-damage events from 19 frames. '
                  '1 frames written, then writing stopped, so the export is incomplete.']
        self.assertIn('Evidence export was incomplete',
                      verdicts.drive_failures(True, alerts, [], []))

    def test_console_error_is_not_hidden_by_success_alert(self):
        self.assertIn('Console errors', verdicts.drive_failures(
            True, ['Footage analysed: 19 frames.'],
            ["error: TypeError: Cannot read properties of null (reading 'style')"], []))

    def test_offer_and_completed_analysis_required(self):
        self.assertTrue(verdicts.drive_failures(False, ['Footage analysed'], [], []))
        self.assertTrue(verdicts.drive_failures(True, ['Could not finish'], [], []))
        self.assertTrue(verdicts.drive_failures(True, [], [], []))

    def test_clean_completion(self):
        self.assertEqual([], verdicts.drive_failures(True, ['Footage analysed: 19 frames.'],
                                                   ['timeEnd: 400 ms'], []))

    def test_uncaught_error_is_failure(self):
        self.assertIn('Uncaught page errors', verdicts.drive_failures(
            True, ['Footage analysed'], [], ['TypeError']))


if __name__ == '__main__':
    unittest.main()
