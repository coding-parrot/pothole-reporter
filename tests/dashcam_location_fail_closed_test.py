#!/usr/bin/env python3
"""Uncalibrated RTSP timing must never become a precise authority/tender claim."""

import pathlib
import sys

from playwright.sync_api import sync_playwright


APP = "http://localhost:8765/"
failures = []


with sync_playwright() as playwright:
    options = {"args": ["--disable-web-security"]}
    chrome = pathlib.Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if chrome.is_file():
        options["executable_path"] = str(chrome)
    browser = playwright.chromium.launch(**options)
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    pack_requests = []
    page.on(
        "request",
        lambda request: pack_requests.append(request.url)
        if "/packs/" in request.url
        else None,
    )
    page.route("**/nominatim.openstreetmap.org/reverse**", lambda route: route.abort())
    page.goto(APP)
    page.wait_for_load_state("networkidle")
    pack_requests.clear()

    result = page.evaluate(
        r"""async () => {
          const P = StandaloneAPI.__pure;
          const nullAccuracy = await P.routeOfficer(
            null, 28.6129, 77.2295, null, 0, 8, "road_damage");
          const missingFormAccuracy = await P.routeOfficer(
            null, 28.6129, 77.2295, Number.NaN, 0, 8, "road_damage");
          const native = {
            id: 66001, created_at: Date.now() / 1000,
            lat: 28.6129, lng: 77.2295,
            photo_data_url: "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==",
            photo_full_data_url: "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==",
            is_reportable: 1, is_pothole: 1, damage_type: "pothole_cavity",
            assessment: "clear", looks_like_speed_breaker: false,
            image_quality: "usable", surface_type: "temporary_drivable_surface",
            defect_type: "pothole", measurement_provenance: "visual_estimate_no_scale",
            measurement_confidence: "low", on_drivable_surface: true,
            has_localized_cavity: true, has_broken_edge_or_rim: true,
            has_depth_or_surface_loss: true, temporal_consistency: "consistent",
            size: "medium", decision: "accept",
            description: "Uncalibrated dashcam timing regression",
            detection_model: "gpt-5.6", image_detail: "original",
            prompt_version: "pothole-binary-v15", schema_version: 7,
            evidence_count: 3, drive_id: "dashcam-uncalibrated",
            capture_source: "drive_live",
            source_event_key: "live:dashcam-uncalibrated:1",
            captured_at: Date.now() / 1000, source_offset_s: 4,
            gps_accuracy: null, speed_mps: 8, heading: 0,
          };
          const imported = await api("/api/native-report", {
            method: "POST", body: JSON.stringify(native),
          });
          const reports = await api("/api/reports");
          const saved = reports.find((report) =>
            report.source_event_key === native.source_event_key);
          return {nullAccuracy, missingFormAccuracy, imported, saved};
        }"""
    )

    for name in ("nullAccuracy", "missingFormAccuracy"):
        route = result[name]
        if route.get("routed") is not False or route.get("unrouted_reason") != "location_uncertain":
            failures.append(f"{name} did not fail closed: {route}")
        if route.get("authority_id") or route.get("officer_email"):
            failures.append(f"{name} invented a recipient: {route}")

    saved = result.get("saved") or {}
    if saved.get("status") != "unrouted" or saved.get("unrouted_reason") != "location_uncertain":
        failures.append(f"native null accuracy was not preserved as unrouted: {saved}")
    for field in (
        "authority_id",
        "officer_email",
        "tender_number",
        "tender_contractor",
        "tender_match_basis",
    ):
        if saved.get(field):
            failures.append(f"uncalibrated dashcam report populated {field}: {saved.get(field)}")
    if saved.get("gps_accuracy") is not None:
        failures.append(f"native null accuracy became {saved.get('gps_accuracy')!r}")
    if pack_requests:
        failures.append(f"unknown dashcam location requested routing/tender packs: {pack_requests}")

    phone_route = page.evaluate(
        """async () => StandaloneAPI.__pure.delhiRouteFromGeocode(
          null, 28.6129, 77.2295, 12)"""
    )
    if phone_route.get("routed") is not True:
        failures.append(f"finite-accuracy phone routing regressed: {phone_route}")

    context.close()
    browser.close()


if failures:
    print("FAIL")
    for failure in failures:
        print("  -", failure)
    sys.exit(1)
print("DASHCAM LOCATION FAIL-CLOSED TEST PASS")
