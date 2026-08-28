#!/usr/bin/env python3
"""Prove Android-authoritative enhancement pixels match Web and evaluator runtimes."""

import importlib.util
import json
from pathlib import Path
import sys

from PIL import Image
from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parent.parent
FIXTURE = (ROOT / "android-app" / "android" / "app" / "src" / "test" /
           "resources" / "detection-image-enhancement-v1.json")
spec = importlib.util.spec_from_file_location("road_eval", ROOT / "eval" / "run_eval.py")
road_eval = importlib.util.module_from_spec(spec)
spec.loader.exec_module(road_eval)
cases = json.loads(FIXTURE.read_text())["cases"]
failures = []


def csv_values(value):
    return [int(part) for part in value.split(",")]


def expected_plan(case):
    return {
        field: case[field]
        for field in ("enhanced", "sample_count", "luminance_sum", "dark_count",
                      "bright_count", "gain_numerator", "gain_denominator")
    }


def check(name, got, expected):
    if got == expected:
        print(f"  ok   {name}")
    else:
        failures.append(name)
        print(f"  FAIL {name}\n         got  {got}\n         want {expected}")


for case in cases:
    input_rgb = csv_values(case["input_rgb"])
    image = Image.new("RGB", (case["width"], case["height"]))
    image.putdata([tuple(input_rgb[index:index + 3])
                   for index in range(0, len(input_rgb), 3)])
    output, plan = road_eval.adaptive_lift(image)
    output_rgb = [channel for pixel in output.getdata() for channel in pixel]
    check(f"evaluator plan: {case['name']}",
          {field: plan[field] for field in expected_plan(case)}, expected_plan(case))
    check(f"evaluator pixels: {case['name']}", output_rgb,
          csv_values(case["expected_rgb"]))


browser_cases = [{**case, "input_rgb": csv_values(case["input_rgb"]),
                  "expected_rgb": csv_values(case["expected_rgb"])} for case in cases]
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(args=["--disable-web-security"])
    page = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
    page.goto("http://localhost:8765/")
    page.wait_for_load_state("networkidle")
    page.wait_for_function("typeof StandaloneAPI !== 'undefined' && StandaloneAPI.__pure",
                           timeout=30000)
    browser_results = page.evaluate(
        """fixtures => fixtures.map((fixture) => {
          const P = StandaloneAPI.__pure;
          const rgba = new Uint8ClampedArray(fixture.width * fixture.height * 4);
          for (let pixel = 0; pixel < fixture.width * fixture.height; pixel++) {
            rgba[pixel * 4] = fixture.input_rgb[pixel * 3];
            rgba[pixel * 4 + 1] = fixture.input_rgb[pixel * 3 + 1];
            rgba[pixel * 4 + 2] = fixture.input_rgb[pixel * 3 + 2];
            rgba[pixel * 4 + 3] = 255;
          }
          const plan = P.detectionEnhancementPlan(rgba, fixture.width, fixture.height);
          P.applyDetectionEnhancement(rgba, plan);
          const output = [];
          for (let index = 0; index < rgba.length; index += 4) {
            output.push(rgba[index], rgba[index + 1], rgba[index + 2]);
          }
          return {
            name: fixture.name,
            plan: {
              enhanced: plan.enhanced,
              sample_count: plan.sampleCount,
              luminance_sum: plan.luminanceSum,
              dark_count: plan.darkCount,
              bright_count: plan.brightCount,
              gain_numerator: plan.gainNumerator,
              gain_denominator: plan.gainDenominator,
            },
            output,
          };
        })""",
        browser_cases,
    )
    browser.close()

for result, case in zip(browser_results, cases):
    check(f"Web plan: {case['name']}", result["plan"], expected_plan(case))
    check(f"Web pixels: {case['name']}", result["output"],
          csv_values(case["expected_rgb"]))

native_source = (ROOT / "android-app" / "android" / "app" / "src" / "main" / "java" /
                 "dev" / "aiengg" / "potholereporter" / "drive" /
                 "FrameQualityEvaluator.kt").read_text()
web_source = (ROOT / "static" / "standalone.js").read_text()
eval_source = (ROOT / "eval" / "run_eval.py").read_text()
check("production adapters use the canonical kernel",
      all(("applyDetectionEnhancement(scaled)" in native_source,
           "applyDetectionEnhancement(imageData.data, light)" in web_source,
           "apply_detection_enhancement(image, plan)" in eval_source)), True)
check("renderer-specific enhancement paths are absent",
      all(("ColorMatrix" not in native_source,
           "ctx.filter =" not in web_source,
           "ImageEnhance" not in eval_source)), True)
check("all shipped standalone mirrors are exact",
      (ROOT / "static" / "standalone.js").read_bytes()
      == (ROOT / "docs" / "standalone.js").read_bytes()
      == (ROOT / "android-app" / "www" / "standalone.js").read_bytes(), True)

if failures:
    raise SystemExit(f"\n{len(failures)} image-enhancement parity check(s) failed")
print("\nIMAGE ENHANCEMENT PARITY TEST PASS")
