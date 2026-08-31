# -*- coding: utf-8 -*-
"""A parseable but unterminated model stream must never become a durable verdict."""
import os
import pathlib
import sys

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from browser_test_utils import open_app

ROOT = pathlib.Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
KEY = os.environ["OPENAI_API_KEY"]

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(args=["--disable-web-security"])
    page = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
    open_app(page, KEY)
    page.wait_for_function("typeof StandaloneAPI !== 'undefined'", timeout=30000)
    result = page.evaluate("""async () => {
      const verdict = JSON.stringify({
        is_pothole: true,
        looks_like_speed_breaker: false,
        image_quality: "usable",
        surface_type: "bituminous_asphalt",
        on_drivable_surface: true,
        has_localized_cavity: true,
        has_unambiguous_lower_interior: true,
        has_broken_edge_or_rim: true,
        has_depth_or_surface_loss: true,
        temporal_consistency: "consistent",
        size: "medium",
        description: "A localized cavity is visible."
      });
      const realFetch = window.fetch;
      window.fetch = (url, init) => {
        if (!String(url).includes("api.openai.com")) return realFetch(url, init);
        const event = JSON.stringify({type: "response.output_text.delta", delta: verdict});
        // The body closes cleanly after a complete-looking delta, but deliberately omits
        // both response.completed and [DONE]. This is a real truncation mode.
        return Promise.resolve(new Response(`data: ${event}\n\n`, {
          status: 200,
          headers: {"content-type": "text/event-stream"},
        }));
      };
      const canvas = document.createElement("canvas");
      canvas.width = 64; canvas.height = 64;
      const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", .8));
      const form = new FormData();
      form.append("photo", blob, "one.jpg");
      form.append("photo", blob, "two.jpg");
      form.append("drive_id", "truncated-stream");
      let outcome = "resolved";
      try {
        await StandaloneAPI.handle("/api/frame", {method: "POST", body: form});
      } catch (error) {
        outcome = String(error && error.message || error);
      } finally {
        window.fetch = realFetch;
      }
      return outcome;
    }""")
    browser.close()

print("  outcome:", result)
if "confirmed completion" not in result:
    print("FAIL: unterminated parseable stream was not rejected explicitly")
    sys.exit(1)
print("STREAM COMPLETION TEST PASS")
