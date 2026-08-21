#!/usr/bin/env python3
"""Capture genuine, deterministic Play Store screenshots from the local client."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "store-assets" / "phone-screenshots"
PORT = 8767


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    server = subprocess.Popen(
        ["python3", "-m", "http.server", str(PORT), "--bind", "127.0.0.1"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(0.5)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={"width": 360, "height": 640},
                device_scale_factor=3,
                is_mobile=True,
                has_touch=True,
                locale="en-IN",
                timezone_id="Asia/Kolkata",
            )
            context.add_init_script(
                """
                localStorage.setItem('openai_key', 'store-preview-key');
                localStorage.setItem('sender_name', 'Road volunteer');
                localStorage.setItem('data_notice_version', '2026-08-21-v4-kmc');
                localStorage.setItem('record_video', '0');
                """
            )
            page = context.new_page()
            page.goto(f"http://127.0.0.1:{PORT}/static/index.html", wait_until="networkidle")
            page.evaluate(
                """async () => {
                  const photo = await fetch('/docs/example-pothole-thumb.jpg').then(r => r.blob());
                  const route = await StandaloneAPI.__pure.kolkataRouteFromGeocode(
                    null, 22.5726, 88.3639, 12
                  );
                  const [subject, body] = StandaloneAPI.__pure.draftEmail(
                    {damage_type: 'pothole_cavity', assessment: 'clear', size: 'medium'},
                    22.5726, 88.3639, 'Esplanade, Kolkata', route.officer_name, null, route
                  );
                  const previewTime = Date.UTC(2026, 7, 21, 11, 45, 0) / 1000;
                  const db = await new Promise((resolve, reject) => {
                    const req = indexedDB.open('potholes');
                    req.onsuccess = () => resolve(req.result);
                    req.onerror = () => reject(req.error);
                  });
                  const existing = await new Promise((resolve, reject) => {
                    const tx = db.transaction('reports', 'readonly');
                    const req = tx.objectStore('reports').getAll();
                    req.onsuccess = () => resolve(req.result);
                    req.onerror = () => reject(req.error);
                  });
                  if (!existing.length) await new Promise((resolve, reject) => {
                    const tx = db.transaction('reports', 'readwrite');
                    tx.objectStore('reports').add({
                      created_at: previewTime, captured_at: previewTime,
                      status: 'draft', decision: 'accept', is_reportable: true,
                      damage_type: 'pothole_cavity', assessment: 'clear',
                      image_quality: 'usable', on_drivable_surface: true,
                      has_broken_edge_or_rim: true, has_depth_or_surface_loss: true,
                      temporal_consistency: 'single_view', size: 'medium',
                      description: 'Open cavity with a broken rim and visible material loss in the travelled lane.',
                      address: 'Esplanade, Kolkata', lat: 22.5726, lng: 88.3639,
                      email_subject: subject, email_body: body, officer_email: null,
                      officer_name: route.officer_name, authority_id: route.authority_id,
                      authority_name: route.authority_name, authority_registry_version: 2,
                      delivery_channel: 'official_handoff', region: 'kolkata',
                      routing_source: route.routing_source, routing_match_field: 'boundary',
                      routing_match_value: route.routing_match_value, ownership_unverified: true,
                      handoff_name: route.handoff_name, handoff_url: route.handoff_url,
                      alternate_handoff_name: route.alternate_handoff_name,
                      alternate_handoff_url: route.alternate_handoff_url,
                      whatsapp_url: route.whatsapp_url, helpline: route.helpline,
                      requires_official_reference: true, official_grievance_id: null, photo
                    });
                    tx.oncomplete = resolve;
                    tx.onerror = () => reject(tx.error);
                  });
                  await loadReports();
                  show('home');
                }"""
            )
            page.screenshot(path=OUT / "01-home-and-history.png")

            page.evaluate("localStorage.setItem('app_lang', 'bn')")
            page.reload(wait_until="networkidle")
            page.wait_for_function("document.documentElement.lang === 'bn'")
            page.locator("[data-id]").first.click()
            page.evaluate("window.scrollTo(0, 0)")
            page.screenshot(path=OUT / "02-detection-detail.png")

            page.locator("#backBtn").click()
            page.locator("#dashBtn").click()
            page.wait_for_timeout(800)
            page.evaluate(
                "document.getElementById('map').style.height = '30vh'; "
                "if (mapObj) mapObj.invalidateSize(); window.scrollTo(0, 0)"
            )
            page.wait_for_timeout(200)
            page.locator(".leaflet-control-attribution").wait_for(state="visible")
            page.screenshot(path=OUT / "03-contribution-dashboard.png")

            page.evaluate("show('dataConsent'); window.scrollTo(0, 0)")
            page.screenshot(path=OUT / "04-privacy-disclosure.png")
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=5)

    for path in sorted(OUT.glob("*.png")):
        print(path.relative_to(ROOT))


if __name__ == "__main__":
    main()
