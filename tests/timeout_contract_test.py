# -*- coding: utf-8 -*-
"""Keep shared-vision clients alive longer than the Worker's fallback chain."""

import json
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parent.parent
SERVER = (ROOT / "server/src/index.js").read_text(encoding="utf-8")
WEB = (ROOT / "static/standalone.js").read_text(encoding="utf-8")
ANDROID_WEB = (ROOT / "android-app/www/standalone.js").read_text(encoding="utf-8")
NATIVE = (
    ROOT
    / "android-app/android/app/src/main/java/com/gauravsen/potholereporter/drivemode/DetectionDispatcher.kt"
).read_text(encoding="utf-8")
SERVER_README = (ROOT / "server/README.md").read_text(encoding="utf-8")
CONTRACT = json.loads(
    (ROOT / "llm/generated/contract.json").read_text(encoding="utf-8")
)
TIMEOUTS = CONTRACT["config"]["runtime"]["timeoutsMs"]


class TimeoutContractTests(unittest.TestCase):
    def test_shared_clients_outlive_sequential_upstreams_with_overhead(self):
        openai = TIMEOUTS["serverOpenAIMax"]
        yolo = TIMEOUTS["serverYoloMax"]
        browser = TIMEOUTS["sharedVisionClient"]
        native = TIMEOUTS["sharedVisionClient"]

        self.assertEqual(55_000, openai)
        self.assertEqual(30_000, yolo)
        self.assertEqual(browser, native)
        self.assertGreaterEqual(browser - openai - yolo, 15_000)

    def test_worker_clamps_each_upstream_to_the_documented_ceiling(self):
        self.assertIn("Math.min(MAX_OPENAI_UPSTREAM_TIMEOUT_MS", SERVER)
        self.assertIn("Math.min(MAX_YOLO_UPSTREAM_TIMEOUT_MS", SERVER)
        self.assertIn("RUNTIME_CONFIG.timeoutsMs.serverOpenAIMax", SERVER)
        self.assertIn("RUNTIME_CONFIG.timeoutsMs.serverYoloMax", SERVER)
        self.assertEqual(
            2,
            SERVER.count("RUNTIME_CONFIG.timeoutsMs.serverUpstreamMin"),
            "OpenAI and YOLO must share the canonical upstream timeout floor",
        )
        self.assertIn("85 seconds for upstreams plus 15 seconds", SERVER_README)

    def test_browser_detection_uses_the_shared_deadline(self):
        detect = WEB.split("async function analyzeViaService", 1)[1].split(
            "let streamBroken", 1
        )[0]
        # Photos and footage keep the full fallback-chain deadline. Drive frames alone get a
        # shorter one, because API Gateway drops the connection at 29 s and a frame held
        # past that only blocks a slot while later frames are dropped.
        self.assertIn(
            'timeout: captureMode === "drive" ? DRIVE_SHARED_VISION_TIMEOUT_MS : SHARED_VISION_TIMEOUT_MS',
            detect,
        )
        drive_deadline = int(
            WEB.split("const DRIVE_SHARED_VISION_TIMEOUT_MS = ", 1)[1].split(";", 1)[0]
        )
        self.assertGreater(drive_deadline, 29_000)
        self.assertLess(drive_deadline, TIMEOUTS["sharedVisionClient"])
        self.assertIn("image_detail: selectedDetail", detect)
        self.assertNotIn("/v1/vision/repair", WEB)
        self.assertNotIn("REPAIR_PROMPT", WEB)
        self.assertEqual(WEB, ANDROID_WEB)

    def test_native_shared_calls_have_a_dedicated_call_deadline(self):
        self.assertIn("client.newBuilder()", NATIVE)
        self.assertIn(
            ".callTimeout(LlmContractGenerated.SHARED_VISION_TIMEOUT_MS, TimeUnit.MILLISECONDS)",
            NATIVE,
        )
        execute_shared = NATIVE.split("private fun executeShared", 1)[1].split(
            "private fun jpegDataUrl", 1
        )[0]
        self.assertIn("sharedClient.newCall(request)", execute_shared)


if __name__ == "__main__":
    unittest.main(verbosity=2)
