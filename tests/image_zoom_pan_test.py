#!/usr/bin/env python3
"""A pinch-zoom must hand off cleanly to one-finger panning."""

from __future__ import annotations

import pathlib
import sys
import threading
from http.server import ThreadingHTTPServer

from playwright.sync_api import sync_playwright


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from serve_app import AppHandler  # noqa: E402


server = ThreadingHTTPServer(("127.0.0.1", 0), AppHandler)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()

try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.goto(f"http://127.0.0.1:{server.server_port}/")
        page.wait_for_load_state("networkidle")
        result = page.evaluate(
            """async () => {
              const viewer = document.getElementById("viewer");
              const img = document.getElementById("viewerImg");
              viewer.classList.remove("hidden");
              img.src = "data:image/svg+xml," + encodeURIComponent(
                '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="1200">' +
                '<rect width="1200" height="1200" fill="black"/></svg>');
              await img.decode();

              const fire = (type, id, x, y) => viewer.dispatchEvent(new PointerEvent(type, {
                pointerId: id, pointerType: "touch", isPrimary: id === 1,
                clientX: x, clientY: y, buttons: type === "pointerup" ? 0 : 1,
                bubbles: true, cancelable: true,
              }));
              const values = () => {
                const match = img.style.transform.match(
                  /translate\(([-0-9.]+)px, ([-0-9.]+)px\) scale\(([-0-9.]+)\)/);
                return match ? match.slice(1).map(Number) : null;
              };

              // Pinch, lift one finger, then immediately drag the remaining finger.
              fire("pointerdown", 1, 170, 420);
              fire("pointerdown", 2, 220, 420);
              fire("pointermove", 1, 70, 360);
              fire("pointermove", 2, 320, 480);
              const zoomed = values();
              fire("pointerup", 2, 320, 480);
              fire("pointermove", 1, 120, 410);
              const panned = values();
              fire("pointerup", 1, 120, 410);

              // Starting another drag immediately after the pinch must not be mistaken
              // for a double tap and reset the zoom.
              fire("pointerdown", 3, 170, 420);
              fire("pointermove", 3, 120, 370);
              const pannedAgain = values();
              fire("pointerup", 3, 120, 370);
              return {zoomed, panned, pannedAgain, transform: img.style.transform};
            }"""
        )
        browser.close()

    zoomed = result["zoomed"]
    panned = result["panned"]
    again = result["pannedAgain"]
    failures = []
    if not zoomed or zoomed[2] <= 2:
        failures.append(f"pinch did not zoom: {result}")
    if not panned or abs(panned[0] - zoomed[0]) < 30 or abs(panned[1] - zoomed[1]) < 30:
        failures.append(f"remaining finger did not pan after pinch: {result}")
    if not again or abs(again[2] - panned[2]) > 0.001:
        failures.append(f"new pan falsely reset zoom as a double tap: {result}")
    if "NaN" in result["transform"]:
        failures.append(f"gesture produced an invalid transform: {result['transform']}")
    if failures:
        print("FAIL")
        for failure in failures:
            print("  -", failure)
        raise SystemExit(1)
    print("IMAGE ZOOM PAN TEST PASS")
finally:
    server.shutdown()
    server.server_close()
