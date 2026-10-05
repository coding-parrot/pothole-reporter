#!/usr/bin/env python3
"""The packaged app must not be frozen to the catalogue lists it was built with.

Road-notice packs carry a seven-day review date and contract packs thirty. The packaged
app read their manifests from its own assets, so every build lost contractor matching on
a timer while the hosted site kept being refreshed. Native now asks the hosted site first
and keeps the bundled copy as the fallback; the browser build still reads its own origin.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright

APP = os.environ.get("POTHOLE_TEST_APP", "http://localhost:8765/")
ROOT = Path(__file__).resolve().parents[1]
SITE = "https://coding-parrot.github.io/pothole-reporter/"
CATALOGS = {
    "road-notice-manifest-v1.36.json": "getRoadNoticeManifest",
    "contract-manifest-v1.36.json": "getContractPackManifest",
    "road-agreement-manifest-v1.36.json": "getRoadAgreementManifest",
}
HOSTED_MARK = "2099-01-01"
READ = """async (getter) => {
  const manifest = await StandaloneAPI.__pure[getter]();
  if (!manifest) return null;
  return Object.values(manifest.resources).map((r) => r.review_after).sort().at(-1);
}"""


def hosted_copy(filename: str) -> str:
    manifest = json.loads((ROOT / "static" / filename).read_text(encoding="utf-8"))
    for resource in manifest["resources"].values():
        resource["review_after"] = HOSTED_MARK
    return json.dumps(manifest)


def bundled_latest(filename: str) -> str:
    manifest = json.loads((ROOT / "static" / filename).read_text(encoding="utf-8"))
    return sorted(r["review_after"] for r in manifest["resources"].values())[-1]


def run(browser, *, native: bool, hosted_up: bool) -> tuple[dict, list]:
    context = browser.new_context()
    if native:
        context.add_init_script("window.Capacitor={isNativePlatform:()=>true,Plugins:{}};")
    asked: list[str] = []

    def hosted(route):
        name = route.request.url.rsplit("/", 1)[-1]
        asked.append(name)
        if hosted_up:
            route.fulfill(status=200, content_type="application/json",
                          headers={"access-control-allow-origin": "*"},
                          body=hosted_copy(name))
        else:
            route.abort()

    for name in CATALOGS:
        context.route(SITE + name, hosted)
    page = context.new_page()
    page.goto(APP)
    page.wait_for_function("window.StandaloneAPI && StandaloneAPI.__pure")
    seen = {name: page.evaluate(READ, getter) for name, getter in CATALOGS.items()}
    context.close()
    return seen, asked


def main() -> None:
    failures: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        seen, asked = run(browser, native=True, hosted_up=True)
        for name in CATALOGS:
            if seen[name] != HOSTED_MARK:
                failures.append(f"native ignored the hosted {name}: saw {seen[name]!r}")
        seen, asked = run(browser, native=True, hosted_up=False)
        for name in CATALOGS:
            if seen[name] != bundled_latest(name):
                failures.append(
                    f"native did not fall back to the bundled {name}: saw {seen[name]!r}")
        seen, asked = run(browser, native=False, hosted_up=True)
        if asked:
            failures.append(f"the browser build asked the hosted site for {asked}")
        for name in CATALOGS:
            if seen[name] != bundled_latest(name):
                failures.append(f"the browser build did not read its own {name}")
        browser.close()
    if failures:
        print("FAIL: native hosted catalogue manifests")
        for failure in failures:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("ok: native prefers hosted catalogue manifests, falls back to bundled; "
          "the browser build reads its own origin")


if __name__ == "__main__":
    main()
