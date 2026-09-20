# -*- coding: utf-8 -*-
"""A report has one complaint action: open a fully addressed email in one tap.

This stays deterministic: the detector, central service, geocoder and Karnataka GIS are
all intercepted, while a tiny Capacitor mock records the exact native composer payload.
The mail app still owns the final Send tap; "one click" here means no second in-app
confirmation or choice of complaint channel.
"""

import hashlib
import json
import os
import sys
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

# The data notice version is read from the bundle: a pinned copy that falls behind
# leaves every run of this suite stuck on the consent screen it thought it accepted.
from flow_harness import DATA_NOTICE_VERSION


APP = os.environ.get("POTHOLE_TEST_APP", "http://localhost:8765/")
SERVICE = "https://email-flow.test"
RECIPIENT = "ka.kalaburagi.cc@gmail.com"
TENDER_NUMBER = "TEST-TENDER-42"
NATIVE_TENDER_NUMBER = "NATIVE-TENDER-77"

ACCEPTED = {
    "image_quality": "acceptable",
    "assessment": "damaged",
    "damage_type": "pothole_cavity",
    "size": "medium",
    "description": "A cavity with a broken rim is visible on the travelled surface.",
}


def envelope(route, payload, status=200, request_id="req-email-flow"):
    route.fulfill(
        status=status,
        headers={"content-type": "application/json", "x-request-id": request_id},
        body=json.dumps({"request_id": request_id, **payload}),
    )


def central_service(route, request):
    path = urlparse(request.url).path
    body = json.loads(request.post_data or "{}") if request.method == "POST" else {}
    if path == "/v1/health":
        envelope(route, {"ok": True, "shared_vision_configured": True}, request_id="req-health")
    elif path == "/v1/installations":
        envelope(route, {"install_id": "email-flow-install"}, 201, "req-install")
    elif path == "/v1/vision/detect":
        envelope(route, {
            **ACCEPTED,
            "detection_receipt": hashlib.sha256(
                f"receipt:{body.get('client_observation_id')}".encode("utf-8")
            ).hexdigest(),
            "detector": {
                "provider": "shared_server",
                "model": body.get("model", "gpt-5-mini"),
                "prompt_version": "road-damage-v5",
                "schema_version": 4,
                "evidence_count": len(body.get("images", [])),
            },
        }, request_id="req-detect")
    elif path == "/v1/tenders/resolve":
        envelope(route, {
            "jurisdiction": {
                "lat": body.get("lat"), "lng": body.get("lng"),
                "address": "Test Road, Central Ward, Test City, 560001",
                "lgd": "248127", "town": "Kalaburagi",
                "source": "kgis", "address_source": "nominatim",
                "road_ownership": "municipal",
            },
            "tender": {
                "tender_number": TENDER_NUMBER,
                "title": "Repair and maintenance of Test Road",
                "location": "Test Road", "contractor": "Road Works Example Ltd",
                "published": "01-08-2026", "confidence": 0.94,
                "reason": "Road and body match", "match_method": "model_adjudicated",
            },
            "reason": None,
        }, request_id="req-tender")
    elif path == "/v1/potholes/report":
        envelope(route, {
            "duplicate": False, "dedupe": None,
            "pothole": {
                "id": 4242, "lat": body.get("lat"), "lng": body.get("lng"),
                "damage_type": body.get("damage_type"), "size": body.get("size"),
                "first_seen_at": body.get("observed_at"),
                "last_seen_at": body.get("observed_at"), "seen_count": 1,
                "lgd": "248127", "town": "Kalaburagi",
            },
        }, 201, "req-report")
    elif path == "/v1/map":
        envelope(route, {"type": "FeatureCollection", "total": 0, "features": []})
    else:
        envelope(route, {"error": "not_mocked", "message": path}, 404, "req-error")


def support_services(route, request):
    target = request.url
    if target.endswith("/karnataka-bodies.json"):
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"bodies": {
            "248127": {
                "name": "Kalaburagi", "type": "CC",
                "officer": "Commissioner", "email": RECIPIENT,
            }
        }}))
    elif "nominatim.openstreetmap.org" in target:
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "display_name": "Test Road, Central Ward, Test City, Karnataka, 560001, India",
            "address": {
                "road": "Test Road", "neighbourhood": "Central Ward",
                "city": "Test City", "postcode": "560001",
            },
        }))
    elif "State_Basemap" in target:
        route.fulfill(status=200, content_type="application/json", body='{"features":[]}')
    elif "Admin_Dynamic_New" in target:
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"features": [{
            "attributes": {
                "KGISTownName": "Kalaburagi", "Town_Type": "CC",
                "LGD_TownCode": "248127",
            }
        }]}))
    elif "GP_Boundary" in target:
        route.fulfill(status=200, content_type="application/json", body='{"features":[]}')
    else:
        route.abort("blockedbyclient")


CREATE_AND_OPEN = r"""
async () => {
  const canvas = document.createElement("canvas");
  canvas.width = 160; canvas.height = 120;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#777"; ctx.fillRect(0, 0, 160, 120);
  ctx.fillStyle = "#111"; ctx.fillRect(50, 52, 60, 38);
  const photo = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", .88));
  const form = new FormData();
  form.append("photo", photo, "test-road.jpg");
  form.append("lat", "12.9716"); form.append("lng", "77.5946");
  form.append("gps_accuracy", "4"); form.append("captured_at_ms", String(Date.now()));
  const report = await StandaloneAPI.handle("/api/report", { method: "POST", body: form });
  window.__emailFlowReport = report;
  openDetail(report, [report]);
  return {
    id: report.id,
    status: report.status,
    officer_name: report.officer_name,
    officer_email: report.officer_email,
    tender_number: report.tender_number,
    send_buttons: document.querySelectorAll("#detail #sendBtn").length,
    complaint_actions: [...document.querySelectorAll("#detail [data-complaint-action]")]
      .map((element) => element.dataset.complaintAction),
    detail_text: document.getElementById("detail").innerText,
  };
}
"""


fails = []
with sync_playwright() as p:
    browser = p.chromium.launch(args=["--disable-web-security"])
    context = browser.new_context(viewport={"width": 390, "height": 844})
    context.add_init_script(script=f"""(() => {{
      localStorage.setItem("service_url", {json.dumps(SERVICE)});
      localStorage.setItem("data_notice_version", "{DATA_NOTICE_VERSION}");
      localStorage.setItem("sender_name", "Test Citizen");
      localStorage.setItem("vision_provider", "shared");
      localStorage.setItem("provider_default_migration", "standalone-personal-v1");
      window.__emailComposerCalls = [];
      window.__confirmCalls = [];
      window.confirm = (message) => {{ window.__confirmCalls.push(String(message)); return true; }};
      const EmailComposer = {{ open: async (options) => {{
        window.__emailComposerCalls.push(JSON.parse(JSON.stringify(options)));
        return {{ value: true }};
      }} }};
      window.Capacitor = {{
        isNativePlatform: () => true,
        registerPlugin: (name) => window.Capacitor.Plugins[name],
        Plugins: {{ EmailComposer, App: {{ addListener: () => null, exitApp: () => null }} }},
      }};
    }})();""")
    context.route(f"{SERVICE}/**", central_service)
    context.route("**/karnataka-bodies.json", support_services)
    context.route("https://nominatim.openstreetmap.org/**", support_services)
    context.route("https://kgis.ksrsac.in/**", support_services)
    page = context.new_page()
    page.goto(APP)
    page.wait_for_load_state("networkidle")
    page.wait_for_function("() => !!(window.StandaloneAPI && window.openDetail)")

    report = page.evaluate(CREATE_AND_OPEN)
    if report["status"] != "draft":
        fails.append(f"accepted routed report was not a draft: {report}")
    if report["officer_name"] != "Commissioner, Kalaburagi":
        fails.append(f"wrong authority title/name: {report['officer_name']!r}")
    if report["officer_email"] != RECIPIENT:
        fails.append(f"wrong routed recipient: {report['officer_email']!r}")
    if report["tender_number"] != TENDER_NUMBER:
        fails.append(f"matched tender was not retained: {report['tender_number']!r}")
    if report["send_buttons"] != 1:
        fails.append(f"sendable detail has {report['send_buttons']} Email actions instead of one")
    if report["complaint_actions"] != ["email"]:
        fails.append(f"complaint channel choices were {report['complaint_actions']!r}, expected only email")
    for confusing_channel in ("Sahaaya", "GBA complaint", "WhatsApp", "SMS", "Call authority"):
        if confusing_channel.lower() in report["detail_text"].lower():
            fails.append(f"detail offers another complaint channel: {confusing_channel}")

    pending_guard = page.evaluate(r"""async (originalId) => {
      const db = await new Promise((resolve, reject) => {
        const request = indexedDB.open("potholes");
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
      });
      const pendingId = await new Promise((resolve, reject) => {
        const tx = db.transaction("reports", "readwrite");
        const store = tx.objectStore("reports");
        const get = store.get(Number(originalId));
        let added;
        get.onsuccess = () => {
          const pending = { ...get.result };
          delete pending.id;
          pending.server_pothole_id = null;
          pending.server_duplicate = false;
          pending.central_sync_pending = true;
          added = store.add(pending);
        };
        tx.oncomplete = () => resolve(added.result);
        tx.onerror = tx.onabort = () => reject(tx.error);
      });
      db.close();
      const rows = await StandaloneAPI.handle("/api/reports", { method: "GET" });
      const pending = rows.find((item) => item.id === pendingId);
      openDetail(pending, rows);
      const buttons = document.querySelectorAll("#detail #sendBtn").length;
      let error = null;
      try {
        await StandaloneAPI.handle(`/api/reports/${pendingId}/send`, { method: "POST" });
      } catch (failure) {
        error = String(failure && failure.message || failure);
      }
      await StandaloneAPI.handle(`/api/reports/${pendingId}`, { method: "DELETE" });
      const original = rows.find((item) => item.id === originalId);
      openDetail(original, rows);
      return { buttons, error };
    }""", report["id"])
    if pending_guard["buttons"] != 0:
        fails.append("pending browser report exposed Email before the shared map confirmed it")
    if "has not confirmed" not in (pending_guard["error"] or ""):
        fails.append(f"pending browser send did not fail closed: {pending_guard}")

    page.locator("#sendBtn").click()
    page.wait_for_function("() => window.__emailComposerCalls.length === 1")
    outcome = page.evaluate("""() => ({
      composer: window.__emailComposerCalls,
      confirms: window.__confirmCalls,
      statusText: document.getElementById("detail").innerText,
    })""")

    # Background Drive Mode stores reports in Room rather than IndexedDB. Mock that
    # bridge after the ordinary flow so both paths exercise the same real detail UI and
    # EmailComposer adapter without needing an emulator or an installed mail account.
    native_ui = page.evaluate("""async ([recipient, tenderNumber]) => {
      window.__nativePhotoRequests = [];
      const jpeg = "/9j/" + "A".repeat(240);
      Capacitor.Plugins.DriveMode = {
        listReports: async () => ({ reports: [{
          id: 77, status: "queued", assessment: "damaged",
          image_quality: "acceptable", damage_type: "pothole_cavity",
          size: "medium", description: "A pothole is visible in the traffic lane.",
          decision: "accept", lat: 12.9716, lng: 77.5946, gps_accuracy: 4,
          address: "Native Test Road, Central Ward, Test City, 560001",
          body_lgd: "248127", body_name: "Kalaburagi",
          road_ownership: "municipal",
          tender_number: tenderNumber, contractor: "Native Roads Example Ltd",
          tender_resolution_checked_at: 1788500000, has_photo: true,
          server_pothole_id: 7077, server_duplicate: false,
          created_at: 1788500000, seen_count: 1
        }] }),
        listDriveSessions: async () => ({ sessions: [] }),
        saveComplaintPreparation: async (options) => ({
          id: options.id, decision: "accept", status: "queued",
          server_pothole_id: 7077, server_duplicate: false,
          road_ownership: "municipal",
          tender_resolution_checked_at: 1788500000,
          tender_number: options.tenderNumber,
          contractor: options.contractor, tender_note: options.tenderNote,
          address: options.address, body_lgd: options.bodyLgd,
          body_name: options.bodyName, email_to: options.emailTo,
          officer_title: options.officerTitle,
          email_subject: options.emailSubject, email_body: options.emailBody,
        }),
        getReportPhoto: async (options) => {
          window.__nativePhotoRequests.push(JSON.parse(JSON.stringify(options)));
          return { dataUrl: "data:image/jpeg;base64," + jpeg };
        },
      };
      await loadReports();
      const native = loadReports.latest.find((row) => row._native);
      openDetail(native, [native]);
      return {
        status: native.status, stored_status: native._nativeStoredStatus,
        send_buttons: document.querySelectorAll("#detail #sendBtn").length,
        action: document.querySelector("#detail #sendBtn")?.dataset.complaintAction || null,
        detail_text: document.getElementById("detail").innerText,
      };
    }""", [RECIPIENT, NATIVE_TENDER_NUMBER])
    if native_ui["status"] != "draft" or native_ui["stored_status"] != "queued":
        fails.append(f"native ready report was not exposed as a sendable draft: {native_ui}")
    if native_ui["send_buttons"] != 1 or native_ui["action"] != "email":
        fails.append(f"native report does not expose exactly one Email action: {native_ui}")
    page.locator("#sendBtn").click()
    page.wait_for_function("() => window.__emailComposerCalls.length === 2")
    native_outcome = page.evaluate("""() => ({
      composer: window.__emailComposerCalls[1],
      confirms: window.__confirmCalls,
      photoRequests: window.__nativePhotoRequests,
    })""")

    # Older builds wrote `sent` as soon as the composer opened. That never proved
    # delivery. A legacy row must now appear as an opened, reusable email draft and
    # remain deletable after it is reopened.
    legacy_ui = page.evaluate("""async () => {
      const id = window.__emailFlowReport.id;
      const db = await new Promise((resolve, reject) => {
        const request = indexedDB.open("potholes");
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
      });
      await new Promise((resolve, reject) => {
        const tx = db.transaction("reports", "readwrite");
        const store = tx.objectStore("reports");
        const get = store.get(Number(id));
        get.onsuccess = () => {
          const row = get.result;
          row.status = "sent";
          row.sent_at = 1700000000;
          store.put(row);
        };
        tx.oncomplete = resolve;
        tx.onerror = () => reject(tx.error);
        tx.onabort = () => reject(tx.error);
      });
      db.close();
      const reports = await StandaloneAPI.handle("/api/reports", { method: "GET" });
      const legacy = reports.find((row) => row.id === id);
      openDetail(legacy, [legacy]);
      return {
        id,
        status: legacy.status,
        actions: [...document.querySelectorAll("#detail [data-complaint-action]")]
          .map((element) => element.dataset.complaintAction),
        delete_buttons: document.querySelectorAll("#detail #discardBtn").length,
        detail_text: document.getElementById("detail").innerText,
      };
    }""")
    page.locator("#sendBtn").click()
    page.wait_for_function("() => window.__emailComposerCalls.length === 3")
    legacy_state = page.evaluate("""async (id) => {
      const reports = await StandaloneAPI.handle("/api/reports", { method: "GET" });
      const row = reports.find((item) => item.id === id);
      await StandaloneAPI.handle(`/api/reports/${id}`, { method: "DELETE" });
      const remaining = await StandaloneAPI.handle("/api/reports", { method: "GET" });
      return {
        status: row && row.status,
        email_opened_at: row && row.email_opened_at,
        sent_at: row && row.sent_at,
        deleted: !remaining.some((item) => item.id === id),
      };
    }""", legacy_ui["id"])
    browser.close()

if outcome["confirms"]:
    fails.append(f"Email tap still opened an in-app confirmation: {outcome['confirms']}")
if len(outcome["composer"]) != 1:
    fails.append(f"one Email tap opened {len(outcome['composer'])} composers")
else:
    draft = outcome["composer"][0]
    if draft.get("to") != [RECIPIENT]:
        fails.append(f"composer recipient was {draft.get('to')!r}, expected {[RECIPIENT]!r}")
    subject = draft.get("subject") or ""
    body = draft.get("body") or ""
    if "Pothole complaint" not in subject or "Test Road" not in subject:
        fails.append(f"composer subject omits the defect or the road: {subject!r}")
    for token in (
        "Commissioner, Kalaburagi",
        "Address / landmark: Test Road, Central Ward, Test City, 560001",
        "Coordinates: 12.971600, 77.594600",
        "https://maps.google.com/?q=12.971600,77.594600",
        "Defect decision: Pothole",
        "App visual size class: medium",
        "No verified exact-road public contract found",
    ):
        if token not in body:
            fails.append(f"composer body omits {token!r}")
    if TENDER_NUMBER in body:
        fails.append("an unverified tender number reached the complaint body")
    attachments = draft.get("attachments") or []
    if len(attachments) != 1:
        fails.append(f"composer has {len(attachments)} attachments instead of one")
    else:
        attachment = attachments[0]
        if attachment.get("name") != "road-damage.jpg" or attachment.get("type") != "base64":
            fails.append(f"unexpected photo attachment metadata: {attachment}")
        path = attachment.get("path") or ""
        if not path.startswith("/9j/") or len(path) < 100:
            fails.append("attached evidence is not a non-empty base64 JPEG")

if native_outcome["confirms"]:
    fails.append(f"native Email tap opened an in-app confirmation: {native_outcome['confirms']}")
native_draft = native_outcome["composer"] or {}
if native_draft.get("to") != [RECIPIENT]:
    fails.append(f"native composer recipient was {native_draft.get('to')!r}")
native_subject = native_draft.get("subject") or ""
native_body = native_draft.get("body") or ""
if "Pothole complaint" not in native_subject or "Native Test Road" not in native_subject:
    fails.append(f"native composer subject omits the defect or the road: {native_subject!r}")
for token in (
    "Commissioner, Kalaburagi",
    "Address / landmark: Native Test Road, Central Ward, Test City, 560001",
    "Coordinates: 12.971600, 77.594600",
    "Defect decision: Pothole",
    "App visual size class: medium",
    "No verified exact-road public contract found",
):
    if token not in native_body:
        fails.append(f"native composer body omits {token!r}")
if NATIVE_TENDER_NUMBER in native_body:
    fails.append("an unverified tender number reached the native complaint body")
native_attachments = native_draft.get("attachments") or []
if len(native_attachments) != 1 or native_attachments[0].get("name") != "road-damage.jpg":
    fails.append(f"native composer did not attach one road photo: {native_attachments}")
elif not (native_attachments[0].get("path") or "").startswith("/9j/"):
    fails.append("native composer attachment is not base64 JPEG evidence")
if not any(request.get("id") == 77 and request.get("full") is True
           for request in native_outcome["photoRequests"]):
    fails.append(f"native email did not request full evidence: {native_outcome['photoRequests']}")

if legacy_ui["status"] != "queued":
    fails.append(f"legacy sent state still claims delivery: {legacy_ui}")
if legacy_ui["actions"] != ["email"] or legacy_ui["delete_buttons"] != 1:
    fails.append(f"legacy email draft is not reopenable/deletable: {legacy_ui}")
if "complaint sent" in legacy_ui["detail_text"].lower():
    fails.append(f"legacy row displays an unverified delivery claim: {legacy_ui['detail_text']!r}")
if legacy_state["status"] != "queued" or not legacy_state["email_opened_at"]:
    fails.append(f"legacy row did not migrate to email-opened state: {legacy_state}")
if legacy_state["sent_at"] is not None:
    fails.append(f"legacy false send timestamp was not cleared: {legacy_state}")
if not legacy_state["deleted"]:
    fails.append(f"reopened legacy email draft could not be deleted: {legacy_state}")

if fails:
    print("FAIL")
    for failure in fails:
        print("  -", failure)
    sys.exit(1)
print("EMAIL-ONLY FLOW TEST PASS")
