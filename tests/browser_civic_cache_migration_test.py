# -*- coding: utf-8 -*-
"""IndexedDB v8 invalidates every pre-policy civic cache without losing evidence."""
import json
import os
import sys

from playwright.sync_api import sync_playwright


APP = os.environ.get("POTHOLE_TEST_APP", "http://localhost:8765/")
fails = []


with sync_playwright() as playwright:
    browser = playwright.chromium.launch()
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    # Establish the app origin without loading its scripts/opening the production DB.
    page.goto(APP + "vendor/leaflet.css")
    page.evaluate(r"""async () => {
      await new Promise((resolve) => {
        const request = indexedDB.deleteDatabase("potholes");
        request.onsuccess = request.onerror = request.onblocked = resolve;
      });
      const db = await new Promise((resolve, reject) => {
        const request = indexedDB.open("potholes", 7);
        request.onupgradeneeded = () => {
          request.result.createObjectStore("reports", { keyPath: "id", autoIncrement: true });
        };
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
      });
      const stale = {
        decision: "accept", assessment: "damaged", image_quality: "acceptable",
        damage_type: "pothole_cavity", lat: 12.9, lng: 77.6,
        address: "Stale Road", body_lgd: "999001", body_name: "Stale City",
        road_ownership: "municipal", road_ownership_detail: "old partial policy",
        road_ownership_source: "central_v0", officer_name: "Stale Commissioner",
        officer_email: "stale@example.gov.in", officer_title: "Stale Commissioner",
        email_to: "stale@example.gov.in", email_subject: "Stale draft",
        email_body: "Stale body", email_opened_at: 1, sent_at: 1,
        tender_number: "STALE-TENDER", contractor: "Stale Contractor",
        tender_note: "Stale probable match", tender_title: "Stale title",
        tender_published: "01-01-2020", tender_resolution_reason: "matched",
        tender_resolution_checked_at: 1, unrouted_reason: "stale",
        unrouted_body: "stale", photo: new Blob(["thumbnail"]),
        photo_full: new Blob(["full-evidence"]), created_at: 1,
      };
      await new Promise((resolve, reject) => {
        const tx = db.transaction("reports", "readwrite");
        const store = tx.objectStore("reports");
        store.put({ ...stale, id: 1, status: "sent", vision_provider: "shared_server" });
        store.put({ ...stale, id: 2, status: "duplicate", vision_provider: "shared_server",
          server_duplicate: true });
        store.put({ ...stale, id: 3, status: "queued", vision_provider: "personal_openai" });
        store.put({ ...stale, id: 4, status: "rejected", decision: "reject",
          vision_provider: "shared_server" });
        tx.oncomplete = resolve;
        tx.onerror = tx.onabort = () => reject(tx.error);
      });
      db.close();
    }""")

    page.goto(APP)
    page.wait_for_function("typeof StandaloneAPI !== 'undefined'", timeout=30_000)
    result = page.evaluate(r"""async () => {
      const db = await new Promise((resolve, reject) => {
        const request = indexedDB.open("potholes");
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
      });
      const rows = await new Promise((resolve, reject) => {
        const request = db.transaction("reports").objectStore("reports").getAll();
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
      });
      const version = db.version;
      db.close();
      return { version, rows: rows.map((row) => ({
        ...row, photo_size: row.photo && row.photo.size,
        photo_full_size: row.photo_full && row.photo_full.size,
        photo: undefined, photo_full: undefined,
      })) };
    }""")
    browser.close()


if result["version"] != 8:
    fails.append(f"database remained at version {result['version']}")
rows = {row["id"]: row for row in result["rows"]}
cleared = (
    "address", "body_lgd", "body_name", "road_ownership", "road_ownership_detail",
    "road_ownership_source", "officer_name", "officer_email", "officer_title", "email_to",
    "email_subject", "email_body", "email_opened_at", "sent_at", "tender_number",
    "contractor", "tender_note", "tender_title", "tender_published",
    "tender_resolution_reason", "tender_resolution_checked_at", "unrouted_reason",
    "unrouted_body",
)
# Row 2 was parked in the old duplicate state, which no longer exists: repeat-detection
# dedupe was removed, so an upgrade releases it as an ordinary draft the owner can send.
for row_id, expected_status in ((1, "draft"), (2, "draft"), (3, "draft")):
    row = rows[row_id]
    dirty = [field for field in cleared if row.get(field) is not None]
    if dirty or row.get("status") != expected_status:
        fails.append(f"accepted row {row_id} retained unsafe cache/status: dirty={dirty}, status={row.get('status')}")
    if row.get("photo_size") != 9 or row.get("photo_full_size") != 13:
        fails.append(f"accepted row {row_id} lost evidence during migration: {row}")
    if row.get("lat") != 12.9 or row.get("lng") != 77.6:
        fails.append(f"accepted row {row_id} lost its observation coordinates")
if rows[4].get("email_to") != "stale@example.gov.in" or rows[4].get("status") != "rejected":
    fails.append("migration changed a rejected shared row")

print(f"  migrated DB version/statuses: {result['version']}/{[rows[i]['status'] for i in sorted(rows)]}")
if fails:
    print("\nFAIL")
    for failure in fails:
        print("  -", failure)
    sys.exit(1)
print("\nBROWSER CIVIC CACHE MIGRATION TEST PASS")
