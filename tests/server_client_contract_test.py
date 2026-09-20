# -*- coding: utf-8 -*-
"""Browser contract for shared/personal routing, signed writes and central dedupe."""

import base64
import hashlib
import json
import os
import sys
import time
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright


APP = os.environ.get("POTHOLE_TEST_APP", "http://localhost:8765/")
SERVICE = "https://server.test"
ACCEPTED = {
    "image_quality": "acceptable",
    "assessment": "damaged",
    "damage_type": "pothole_cavity",
    "size": "medium",
    "description": "A cavity with a broken rim is visible on the travelled surface.",
}
REJECTED = {
    "image_quality": "acceptable",
    "assessment": "undamaged",
    "damage_type": None,
    "size": None,
    "description": "The visible road surface is intact.",
}

# Keep this contract test dependency-free: verify WebCrypto's P-256 signatures with
# the curve arithmetic directly instead of requiring a platform crypto wheel.
P256_P = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF
P256_A = P256_P - 3
P256_N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
P256_G = (
    0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296,
    0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5,
)


def point_add(left, right):
    if left is None:
        return right
    if right is None:
        return left
    x1, y1 = left
    x2, y2 = right
    if x1 == x2 and (y1 + y2) % P256_P == 0:
        return None
    if left == right:
        slope = (3 * x1 * x1 + P256_A) * pow(2 * y1, -1, P256_P) % P256_P
    else:
        slope = (y2 - y1) * pow(x2 - x1, -1, P256_P) % P256_P
    x3 = (slope * slope - x1 - x2) % P256_P
    return x3, (slope * (x1 - x3) - y1) % P256_P


def scalar_multiply(value, point):
    result = None
    addend = point
    while value:
        if value & 1:
            result = point_add(result, addend)
        addend = point_add(addend, addend)
        value >>= 1
    return result


def decode_signature(raw):
    if len(raw) == 64:
        return int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")
    # Be liberal if a native bridge supplies ASN.1 DER instead of WebCrypto's P1363.
    if len(raw) < 8 or raw[0] != 0x30:
        raise ValueError("unknown signature encoding")
    cursor = 2
    if raw[1] & 0x80:
        size_bytes = raw[1] & 0x7F
        cursor = 2 + size_bytes
    if raw[cursor] != 0x02:
        raise ValueError("missing DER r")
    r_length = raw[cursor + 1]
    cursor += 2
    r = int.from_bytes(raw[cursor:cursor + r_length], "big")
    cursor += r_length
    if raw[cursor] != 0x02:
        raise ValueError("missing DER s")
    s_length = raw[cursor + 1]
    cursor += 2
    return r, int.from_bytes(raw[cursor:cursor + s_length], "big")


def verify_p256(public_raw, signature_raw, message):
    if len(public_raw) != 65 or public_raw[0] != 4:
        return False
    public_point = (int.from_bytes(public_raw[1:33], "big"),
                    int.from_bytes(public_raw[33:], "big"))
    r, s = decode_signature(signature_raw)
    if not (1 <= r < P256_N and 1 <= s < P256_N):
        return False
    digest = int.from_bytes(hashlib.sha256(message).digest(), "big")
    inverse = pow(s, -1, P256_N)
    result = point_add(scalar_multiply(digest * inverse % P256_N, P256_G),
                       scalar_multiply(r * inverse % P256_N, public_point))
    return result is not None and result[0] % P256_N == r


def response_envelope(route, payload, status=200, request_id="req-test"):
    route.fulfill(
        status=status,
        headers={"content-type": "application/json", "x-request-id": request_id},
        body=json.dumps({"request_id": request_id, **payload}),
    )


class CentralHarness:
    def __init__(self):
        self.installations = {}
        self.requests = []
        self.report_count = 0
        self.tender_road_ownership = "municipal"
        self.fail_shared_vision = False
        self.fail_tender_resolution = False
        self.reject_shared_vision = False
        self.fail_next_report = False

    def handle(self, route, request):
        parsed = urlparse(request.url)
        path = parsed.path
        raw = request.post_data_buffer or b""
        headers = {key.lower(): value for key, value in request.headers.items()}
        body = json.loads(raw.decode("utf-8") or "{}") if request.method == "POST" else {}
        captured = {
            "method": request.method,
            "url": request.url,
            "path": path,
            "headers": headers,
            "body": body,
            "raw": raw,
        }
        self.requests.append(captured)

        if path == "/v1/health":
            response_envelope(route, {
                "ok": True,
                "shared_vision_configured": True,
            }, request_id="req-health")
            return

        if path == "/v1/installations":
            install_id = f"install-{len(self.installations) + 1}"
            self.installations[install_id] = body["public_key"]
            response_envelope(route, {"install_id": install_id}, 201, f"req-{install_id}")
            return

        if path == "/v1/vision/detect":
            if self.fail_shared_vision:
                response_envelope(route, {
                    "error": "shared_credits_exhausted",
                    "message": "Shared vision credits are exhausted. Try personal-key mode.",
                }, 503, "req-shared-credit")
                return
            verdict = REJECTED if self.reject_shared_vision else ACCEPTED
            receipt = None if self.reject_shared_vision else hashlib.sha256(
                f"receipt:{body['client_observation_id']}".encode("utf-8")
            ).hexdigest()
            response_envelope(route, {
                **verdict,
                "detection_receipt": receipt,
                "detector": {"provider": "shared_server", "model": body["model"],
                             "prompt_version": "road-damage-v5", "schema_version": 4,
                             "evidence_count": len(body["images"])},
            }, request_id="req-shared-vision")
            return

        if path == "/v1/activity":
            response_envelope(route, {"accepted": True, "event": "vision_check"},
                              202, "req-activity")
            return

        if path == "/v1/tenders/resolve":
            if self.fail_tender_resolution:
                response_envelope(route, {
                    "error": "road_ownership_unavailable",
                    "message": "Road ownership could not be verified. Retry later.",
                    "details": {"retryable": True},
                }, 503, "req-tender-unavailable")
                return
            highway_names = {
                "state_highway": "SH 17",
                "district_highway": "MDR 42",
            }
            ownership = self.tender_road_ownership
            highway_name = highway_names.get(ownership)
            response_envelope(route, {
                "jurisdiction": {"lat": body["lat"], "lng": body["lng"],
                    "address": "Test Road, Kalaburagi", "lgd": "248127",
                    "town": "Kalaburagi", "source": "kgis",
                    "address_source": "nominatim", "road_ownership": ownership,
                    "highway_name": highway_name, "rural_body": None},
                "tender": None if highway_name else {
                    "tender_number": "TEST-2026-1", "title": "Repair of Test Road",
                    "location": "Kalaburagi", "contractor": "Example Roads Ltd",
                    "published": "01-08-2026", "confidence": 0.91,
                    "reason": "Road and body match", "match_method": "model_adjudicated"},
                "reason": ownership if highway_name else None,
            }, request_id="req-tender")
            return

        if path == "/v1/potholes/report":
            if self.fail_next_report:
                self.fail_next_report = False
                response_envelope(route, {
                    "error": "service_temporarily_unavailable",
                    "message": "The shared map is temporarily unavailable.",
                    "details": {"retryable": True},
                }, 503, "req-report-unavailable")
                return
            self.report_count += 1
            duplicate = self.report_count > 1
            response_envelope(route, {
                "duplicate": duplicate,
                "dedupe": {"kind": "nearby", "distance_m": 2.4} if duplicate else None,
                "pothole": {"id": 101, "lat": 12.9716, "lng": 77.5946,
                    "damage_type": body["damage_type"], "size": body["size"],
                    "first_seen_at": body["observed_at"],
                    "last_seen_at": body["observed_at"], "seen_count": self.report_count,
                    "lgd": None if self.tender_road_ownership != "municipal" else "248127",
                    "town": None if self.tender_road_ownership != "municipal"
                        else "Kalaburagi"},
            }, 200 if duplicate else 201, f"req-report-{self.report_count}")
            return

        if path == "/v1/map":
            response_envelope(route, {
                "type": "FeatureCollection", "total": 1,
                "features": [{"type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [77.5946, 12.9716]},
                    "properties": {"id": 101, "damage_type": "pothole_cavity",
                        "size": "medium",
                        "first_seen_at": 1788500000000, "last_seen_at": 1788500010000,
                        "seen_count": 3, "town": "Kalaburagi", "lgd": "248127"}}],
            }, request_id="req-map-sync")
            return

        response_envelope(route, {"error": "not_mocked", "message": path}, 404, "req-error")


def route_support(route, request):
    target = request.url
    if target.endswith("/karnataka-bodies.json"):
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"bodies": {
            "248127": {"name": "Kalaburagi", "type": "CC", "officer": "Commissioner",
                       "email": "test@example.gov.in"}
        }}))
    elif "nominatim.openstreetmap.org" in target:
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "display_name": "Test Road, Kalaburagi, Karnataka, India",
            "address": {"road": "Test Road", "city": "Kalaburagi", "postcode": "585101"},
        }))
    elif "State_Basemap" in target:
        route.fulfill(status=200, content_type="application/json", body='{"features":[]}')
    elif "Admin_Dynamic_New" in target:
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"features": [{
            "attributes": {"KGISTownName": "Kalaburagi", "Town_Type": "CC",
                           "LGD_TownCode": "248127"}
        }]}))
    elif "GP_Boundary" in target:
        route.fulfill(status=200, content_type="application/json", body='{"features":[]}')
    else:
        route.abort("blockedbyclient")


def open_context(browser, harness, personal=False, geolocation_handler=route_support):
    context = browser.new_context(viewport={"width": 390, "height": 844})
    settings = json.dumps({"service": SERVICE, "personal": personal})
    context.add_init_script(script="""(() => {
      const {service, personal} = """ + settings + """;
      localStorage.setItem('service_url', service);
      localStorage.setItem('data_notice_version', '2026-09-04-v2');
      if (personal) {
        localStorage.setItem('vision_provider', 'personal');
        localStorage.setItem('openai_key', 'sk-personal-test-secret');
        localStorage.removeItem('detection_model');
        localStorage.removeItem('image_detail');
      } else {
        localStorage.setItem('vision_provider', 'shared');
        localStorage.removeItem('openai_key');
        // Exercise the user-selectable original-detail arm through shared mode. This
        // regresses the bug where the browser sent the choice but the server forced high.
        localStorage.setItem('detection_model', 'gpt-5.6');
        localStorage.setItem('image_detail', 'original');
      }
    })();""")
    context.route(f"{SERVICE}/**", harness.handle)
    context.route("**/karnataka-bodies.json", route_support)
    context.route("https://nominatim.openstreetmap.org/**", geolocation_handler)
    context.route("https://kgis.ksrsac.in/**", geolocation_handler)

    openai_calls = []

    def openai(route, request):
        openai_calls.append({"headers": request.headers, "body": request.post_data or ""})
        text = json.dumps(ACCEPTED, separators=(",", ":"))
        event = json.dumps({"type": "response.output_text.delta", "delta": text})
        route.fulfill(status=200, headers={"content-type": "text/event-stream"},
                      body=f"data: {event}\n\ndata: [DONE]\n\n")

    context.route("https://api.openai.com/v1/responses", openai)
    context.route("https://api.openai.com/v1/models**", lambda route: route.fulfill(
        status=200, content_type="application/json", body='{"data":[]}'))
    page = context.new_page()
    page.goto(APP)
    page.wait_for_load_state("networkidle")
    page.wait_for_function("() => !!(window.StandaloneAPI && StandaloneAPI.handle)")
    return context, page, openai_calls


CREATE_REPORT = """async () => {
  const canvas = document.createElement('canvas');
  canvas.width = 160; canvas.height = 120;
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#555'; ctx.fillRect(0, 0, 160, 120);
  ctx.fillStyle = '#111'; ctx.fillRect(55, 55, 50, 35);
  const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', .85));
  const fd = new FormData();
  fd.append('photo', blob, 'road.jpg');
  fd.append('lat', '12.9716'); fd.append('lng', '77.5946');
  fd.append('gps_accuracy', '5'); fd.append('heading', '90'); fd.append('speed', '4');
  fd.append('captured_at_ms', String(Date.now()));
  const report = await StandaloneAPI.handle('/api/report', {method:'POST', body:fd});
  window.__contractReport = report;
  return {id:report.id, status:report.status, decision:report.decision,
    server_pothole_id:report.server_pothole_id,
    client_observation_id:report.client_observation_id,
    server_duplicate:report.server_duplicate, seen_count:report.seen_count,
    central_sync_pending:report.central_sync_pending,
    server_sync_error:report.server_sync_error,
    has_photo:!!report.photo,
    has_photo_full:!!report.photo_full, email_subject:report.email_subject,
    email_body:report.email_body, tender_number:report.tender_number,
    tender_title:report.tender_title, tender_note:report.tender_note,
    contractor:report.contractor, vision_provider:report.vision_provider,
    officer_name:report.officer_name, officer_email:report.officer_email,
    unrouted_reason:report.unrouted_reason, unrouted_body:report.unrouted_body};
}"""

READ_REPORT = """async (id) => {
  const report = (await StandaloneAPI.handle('/api/reports'))
    .find((row) => row.id === id);
  if (!report) return null;
  window.__contractReport = report;
  return {id:report.id, status:report.status, decision:report.decision,
    server_pothole_id:report.server_pothole_id,
    client_observation_id:report.client_observation_id,
    server_duplicate:report.server_duplicate, seen_count:report.seen_count,
    central_sync_pending:report.central_sync_pending,
    server_sync_error:report.server_sync_error,
    has_photo:!!report.photo,
    has_photo_full:!!report.photo_full, email_subject:report.email_subject,
    email_body:report.email_body, tender_number:report.tender_number,
    tender_title:report.tender_title, tender_note:report.tender_note,
    contractor:report.contractor, vision_provider:report.vision_provider,
    officer_name:report.officer_name, officer_email:report.officer_email,
    unrouted_reason:report.unrouted_reason, unrouted_body:report.unrouted_body};
}"""

READ_OUTBOX = """async () => await new Promise((resolve, reject) => {
  const open = indexedDB.open('potholes');
  open.onerror = () => reject(open.error);
  open.onsuccess = () => {
    const db = open.result;
    const tx = db.transaction('central_outbox', 'readonly');
    const get = tx.objectStore('central_outbox').getAll();
    get.onerror = () => reject(get.error);
    get.onsuccess = () => resolve(get.result);
    tx.oncomplete = () => db.close();
  };
})"""


def verify_signatures(harness):
    failures = []
    signed = [request for request in harness.requests
              if request["method"] == "POST" and request["path"] != "/v1/installations"]
    for request in signed:
        headers = request["headers"]
        install_id = headers.get("x-install-id")
        timestamp = headers.get("x-timestamp")
        idempotency = headers.get("idempotency-key")
        signature = headers.get("x-signature")
        if not all((install_id, timestamp, idempotency, signature)):
            failures.append(f"unsigned request: {request['path']}")
            continue
        if install_id not in harness.installations:
            failures.append(f"unknown installation on {request['path']}: {install_id}")
            continue
        if abs(int(timestamp) - int(time.time() * 1000)) > 300_000:
            failures.append(f"stale timestamp on {request['path']}")
        canonical = "\n".join([
            request["method"].upper(), request["path"], timestamp, idempotency,
            hashlib.sha256(request["raw"]).hexdigest(),
        ]).encode("utf-8")
        try:
            if not verify_p256(base64.b64decode(harness.installations[install_id]),
                               base64.b64decode(signature), canonical):
                raise ValueError("ECDSA verification failed")
        except Exception as error:  # pragma: no cover - diagnostic path
            failures.append(f"bad signature on {request['path']}: {error}")
    return failures


def verify_shared_highway_refusal(browser, ownership):
    """The central ownership gate must outrank the browser's municipal-only GIS."""
    failures = []
    highway_name = {
        "state_highway": "SH 17",
        "district_highway": "MDR 42",
    }[ownership]
    harness = CentralHarness()
    harness.tender_road_ownership = ownership
    context, page, openai_calls = open_context(browser, harness, personal=False)
    try:
        no_personal_key = page.evaluate("() => localStorage.getItem('openai_key')")
        report_start = len(harness.requests)
        created = page.evaluate(CREATE_REPORT)
        stored = page.evaluate(READ_REPORT, created["id"])

        prepared = page.evaluate("""async (id) => {
          const report = (await StandaloneAPI.handle('/api/reports'))
            .find((row) => row.id === id);
          try {
            const draft = await StandaloneAPI.prepareComplaint(report);
            return {failed:false, to:draft && draft.to || null,
              subject:draft && draft.subject || null};
          } catch (error) {
            return {failed:true, code:error.code || null,
              reason:error.unroutedReason || null,
              body:error.unroutedBody || null, message:error.message};
          }
        }""", created["id"])
        sent = page.evaluate("""async (id) => {
          try {
            await StandaloneAPI.handle(`/api/reports/${id}/send`, {method:'POST'});
            return {failed:false};
          } catch (error) {
            return {failed:true, code:error.code || null,
              reason:error.unroutedReason || null, message:error.message};
          }
        }""", created["id"])
        stored_after_attempts = page.evaluate(READ_REPORT, created["id"])

        report_paths = [request["path"] for request in harness.requests[report_start:]
                        if request["path"] in ("/v1/vision/detect",
                                               "/v1/tenders/resolve",
                                               "/v1/potholes/report")]
        if report_paths != ["/v1/vision/detect", "/v1/tenders/resolve",
                            "/v1/potholes/report"]:
            failures.append(f"{ownership} shared report used the wrong service flow: "
                            f"{report_paths}")
        tender_resolves = [request for request in harness.requests
                           if request["path"] == "/v1/tenders/resolve"]
        if len(tender_resolves) != 1:
            failures.append(f"{ownership} resolved its tender "
                            f"{len(tender_resolves)} times")

        safe_fields = ("officer_name", "officer_email", "email_subject", "email_body",
                       "tender_number", "tender_title", "tender_note", "contractor")
        if not (stored
                and created["decision"] == stored["decision"] == "accept"
                and created["status"] == stored["status"] == "unrouted"
                and stored["unrouted_reason"] == ownership
                and stored["unrouted_body"] == highway_name
                and stored["vision_provider"] == "shared_server"
                and stored["server_pothole_id"] == "101"
                and not stored["central_sync_pending"]
                and stored["server_sync_error"] is None
                and not stored["server_duplicate"]
                and all(stored[field] is None for field in safe_fields)):
            failures.append(f"{ownership} was not durably stored as a safe unrouted "
                            f"accepted report: created={created}, stored={stored}")

        if prepared.get("failed") is not True \
                or prepared.get("code") != "complaint_unrouted" \
                or prepared.get("reason") != ownership:
            failures.append(f"{ownership} complaint preparation did not fail closed: "
                            f"{prepared}")
        if sent.get("failed") is not True \
                or sent.get("code") != "complaint_unrouted" \
                or sent.get("reason") != ownership:
            failures.append(f"{ownership} send endpoint did not fail closed: {sent}")
        if stored_after_attempts != stored:
            failures.append(f"{ownership} complaint attempts changed the safe stored "
                            f"record: before={stored}, after={stored_after_attempts}")
        if no_personal_key is not None or openai_calls:
            failures.append(f"{ownership} contract was not shared/no-personal-key: "
                            f"key={no_personal_key!r}, openai_calls={len(openai_calls)}")
    finally:
        context.close()

    if len(harness.installations) != 1:
        failures.append(f"{ownership} expected one device identity, got "
                        f"{len(harness.installations)}")
    failures.extend(f"{ownership}: {failure}" for failure in verify_signatures(harness))
    return failures


def verify_shared_capture_uses_central_enrichment_only(browser):
    """A no-key shared capture must never repeat the server's GIS/geocoder work."""
    failures = []
    harness = CentralHarness()
    local_upstream_requests = []

    def delayed_failure(route, request):
        # These routes are deliberately unusable. If shared capture regresses to the
        # legacy client-side path, make the dependency both visible and slow before it
        # fails; the accepted report must complete without touching either endpoint.
        local_upstream_requests.append(request.url)
        time.sleep(0.25)
        route.abort("timedout")

    context, page, openai_calls = open_context(
        browser,
        harness,
        personal=False,
        geolocation_handler=delayed_failure,
    )
    try:
        no_personal_key = page.evaluate("() => localStorage.getItem('openai_key')")
        request_start = len(harness.requests)
        created = page.evaluate(CREATE_REPORT)
        central_paths = [
            request["path"] for request in harness.requests[request_start:]
            if request["path"] in (
                "/v1/vision/detect",
                "/v1/tenders/resolve",
                "/v1/potholes/report",
            )
        ]
        tender_requests = [
            request for request in harness.requests[request_start:]
            if request["path"] == "/v1/tenders/resolve"
        ]

        if local_upstream_requests:
            failures.append(
                "shared capture contacted client-side GIS/geocoder endpoints: "
                f"{local_upstream_requests}"
            )
        if central_paths != [
                "/v1/vision/detect",
                "/v1/tenders/resolve",
                "/v1/potholes/report",
        ]:
            failures.append(f"shared capture did not use the central-only flow: {central_paths}")
        if len(tender_requests) != 1 or set(tender_requests[0]["body"]) != {"lat", "lng"}:
            failures.append(
                "shared capture sent client-derived jurisdiction/address enrichment: "
                f"{[request['body'] for request in tender_requests]}"
            )
        if not (
                created["status"] == "draft"
                and created["decision"] == "accept"
                and created["vision_provider"] == "shared_server"
                and created["server_pothole_id"] == "101"
                # The recipient comes from the signed state pack for the LGD code the
                # central service returned, never from a client-side lookup.
                and created["officer_email"] == "ka.kalaburagi.cc@gmail.com"
                and created["tender_number"] == "TEST-2026-1"
                and created["contractor"] == "Example Roads Ltd"
                and not created["central_sync_pending"]
                and created["server_sync_error"] is None
        ):
            failures.append(
                "shared capture did not complete from central enrichment while local "
                f"upstreams were poisoned: {created}"
            )
        if no_personal_key is not None or openai_calls:
            failures.append(
                "central-only contract was not a no-personal-key shared capture: "
                f"key={no_personal_key!r}, direct_openai_calls={len(openai_calls)}"
            )
    finally:
        context.close()

    if len(harness.installations) != 1:
        failures.append(
            f"central-only flow expected one device identity, got {len(harness.installations)}"
        )
    failures.extend(
        f"central-only enrichment: {failure}" for failure in verify_signatures(harness)
    )
    return failures


def verify_shared_tender_failure_never_uses_local_enrichment(browser):
    """A central tender outage must not reopen the browser's old GIS fallback."""
    failures = []
    harness = CentralHarness()
    harness.fail_tender_resolution = True
    local_upstream_requests = []

    def delayed_failure(route, request):
        local_upstream_requests.append(request.url)
        time.sleep(0.25)
        route.abort("timedout")

    context, page, openai_calls = open_context(
        browser,
        harness,
        personal=False,
        geolocation_handler=delayed_failure,
    )
    try:
        no_personal_key = page.evaluate("() => localStorage.getItem('openai_key')")
        request_start = len(harness.requests)
        created = page.evaluate(CREATE_REPORT)
        central_paths = [
            request["path"] for request in harness.requests[request_start:]
            if request["path"] in (
                "/v1/vision/detect",
                "/v1/tenders/resolve",
                "/v1/potholes/report",
            )
        ]
        allowed_paths = [
            ["/v1/vision/detect", "/v1/tenders/resolve"],
            ["/v1/vision/detect", "/v1/tenders/resolve", "/v1/potholes/report"],
        ]
        safe_fields = (
            "officer_name",
            "officer_email",
            "email_subject",
            "email_body",
            "tender_number",
            "tender_title",
            "tender_note",
            "contractor",
        )

        if local_upstream_requests:
            failures.append(
                "failed central tender lookup contacted client-side GIS/geocoder: "
                f"{local_upstream_requests}"
            )
        if central_paths not in allowed_paths:
            failures.append(
                "failed central tender lookup used an unexpected request flow: "
                f"{central_paths}"
            )
        if not (
                created["status"] == "unrouted"
                and created["decision"] == "accept"
                and created["vision_provider"] == "shared_server"
                and created["unrouted_reason"] == "road_class_unknown"
                and created["unrouted_body"] is None
                and all(created[field] is None for field in safe_fields)
        ):
            failures.append(
                "central tender failure did not remain a safe unsendable observation: "
                f"{created}"
            )
        report_attempted = "/v1/potholes/report" in central_paths
        if report_attempted and created["server_pothole_id"] != "101":
            failures.append(
                "successful optional central report was not retained after tender failure: "
                f"{created}"
            )
        if no_personal_key is not None or openai_calls:
            failures.append(
                "tender-failure contract was not no-key shared mode: "
                f"key={no_personal_key!r}, direct_openai_calls={len(openai_calls)}"
            )
    finally:
        context.close()

    if len(harness.installations) != 1:
        failures.append(
            "tender-failure flow expected one device identity, got "
            f"{len(harness.installations)}"
        )
    failures.extend(
        f"central tender failure: {failure}" for failure in verify_signatures(harness)
    )
    return failures


def main():
    failures = []
    harness = CentralHarness()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()

        failures.extend(verify_shared_capture_uses_central_enrichment_only(browser))
        failures.extend(verify_shared_tender_failure_never_uses_local_enrichment(browser))

        shared_context, shared_page, shared_openai = open_context(browser, harness, personal=False)
        # A failed accepted-pothole upload is committed with its exact body and stable
        # observation ID. Reloading the document must run only that central report again.
        harness.fail_next_report = True
        shared_pending = shared_page.evaluate(CREATE_REPORT)
        queued = shared_page.evaluate(READ_OUTBOX)
        if not (shared_pending["status"] == "draft"
                and shared_pending["central_sync_pending"] is True
                and shared_pending["server_pothole_id"] is None
                and shared_pending["server_sync_error"] ==
                    "The shared map is temporarily unavailable."
                and len(queued) == 1
                and queued[0]["client_observation_id"] ==
                    shared_pending["client_observation_id"]
                and queued[0]["report_id"] == shared_pending["id"]):
            failures.append(f"failed central report was not durably queued: "
                            f"report={shared_pending}, outbox={queued}")
        startup_at = len(harness.requests)
        shared_page.reload()
        shared_page.wait_for_load_state("networkidle")
        shared_page.wait_for_function("""id => StandaloneAPI.handle('/api/reports')
          .then((rows) => rows.some((row) => row.id === id && row.server_pothole_id
            && row.central_sync_pending === false))""", arg=shared_pending["id"])
        shared = shared_page.evaluate(READ_REPORT, shared_pending["id"])
        startup_delta = [request["path"] for request in harness.requests[startup_at:]
                         if request["path"] != "/v1/health"]
        if startup_delta != ["/v1/potholes/report"]:
            failures.append(f"startup retry repeated vision, tender, or complaint work: "
                            f"{startup_delta}")
        if shared_page.evaluate(READ_OUTBOX):
            failures.append("successful startup retry did not clear its outbox row")
        first_attempts = [request for request in harness.requests
                          if request["path"] == "/v1/potholes/report"
                          and request["body"].get("client_observation_id") ==
                          shared_pending["client_observation_id"]]
        if not (len(first_attempts) == 2
                and first_attempts[0]["raw"] == first_attempts[1]["raw"]
                and first_attempts[0]["headers"].get("idempotency-key") ==
                    first_attempts[1]["headers"].get("idempotency-key") ==
                    shared_pending["client_observation_id"]):
            failures.append("startup retry changed the exact body or idempotency key")

        # The online signal exercises the same durable path without a reload. A later
        # cross-device duplicate updates the local report and suppresses its complaint.
        harness.fail_next_report = True
        online_pending = shared_page.evaluate(CREATE_REPORT)
        online_at = len(harness.requests)
        shared_page.evaluate("window.dispatchEvent(new Event('online'))")
        # The reconnect first probes health after a retryable 503 trips the service
        # circuit, then flushes the durable row. Do not treat the Promise returned from
        # an IndexedDB predicate as completion before the POST has actually committed.
        online_synced = None
        for _ in range(120):
            online_synced = shared_page.evaluate(READ_REPORT, online_pending["id"])
            if online_synced and online_synced["central_sync_pending"] is False:
                break
            shared_page.wait_for_timeout(25)
        online_delta = [request["path"] for request in harness.requests[online_at:]]
        if online_delta not in (["/v1/potholes/report"],
                                ["/v1/health", "/v1/potholes/report"]):
            failures.append(f"online retry repeated vision, tender, or complaint work: "
                            f"{online_delta}")
        online_attempts = [request for request in harness.requests
                           if request["path"] == "/v1/potholes/report"
                           and request["body"].get("client_observation_id") ==
                           online_pending["client_observation_id"]]
        if not (len(online_attempts) == 2
                and online_attempts[0]["raw"] == online_attempts[1]["raw"]
                and online_attempts[0]["headers"].get("idempotency-key") ==
                    online_attempts[1]["headers"].get("idempotency-key") ==
                    online_pending["client_observation_id"]
                # The retry is byte-identical and idempotent. The shared map may answer
                # that it already knows this pothole, which is recorded, but repeat
                # detection no longer cancels this reporter's own complaint.
                and online_synced["status"] == "draft"
                and online_synced["server_duplicate"] is True
                and online_synced["email_subject"] is not None):
            failures.append(f"online retry was not exact, or a repeat sighting lost its "
                            f"complaint: attempts={len(online_attempts)}, "
                            f"report={online_synced}")

        # Deleting a locally saved report atomically cancels its pending upload. A later
        # reconnect must not recreate the record or send its coordinates.
        harness.fail_next_report = True
        doomed = shared_page.evaluate(CREATE_REPORT)
        reports_before_delete_retry = len([request for request in harness.requests
                                           if request["path"] == "/v1/potholes/report"])
        shared_page.evaluate("""id => StandaloneAPI.handle(`/api/reports/${id}`,
          {method:'DELETE'})""", doomed["id"])
        if any(row["client_observation_id"] == doomed["client_observation_id"]
               for row in shared_page.evaluate(READ_OUTBOX)):
            failures.append("deleting a report left its central retry queued")
        shared_page.evaluate("window.dispatchEvent(new Event('online'))")
        shared_page.wait_for_timeout(150)
        reports_after_delete_retry = len([request for request in harness.requests
                                          if request["path"] == "/v1/potholes/report"])
        deleted = shared_page.evaluate(READ_REPORT, doomed["id"])
        if reports_after_delete_retry != reports_before_delete_retry or deleted is not None:
            failures.append("reconnect uploaded or resurrected a deleted pending report")

        # A genuine negative verdict may still be kept locally for review, but it must
        # not disclose exact coordinates to or spend quota on the project tender route.
        harness.reject_shared_vision = True
        before_reject = [request["path"] for request in harness.requests]
        rejected = shared_page.evaluate(CREATE_REPORT)
        harness.reject_shared_vision = False
        reject_delta = [request["path"] for request in harness.requests[len(before_reject):]]
        if (reject_delta != ["/v1/vision/detect"]
                or rejected["status"] != "rejected"
                or rejected["server_pothole_id"] is not None):
            failures.append(f"rejected capture reached central tender/report: "
                            f"calls={reject_delta}, report={rejected}")

        if shared_openai:
            failures.append("shared mode called OpenAI directly")
        harness.fail_shared_vision = True
        before_failed_vision = len(harness.requests)
        failed_shared = shared_page.evaluate("""async () => {
          try {
            await (""" + CREATE_REPORT + """)();
            return {failed:false};
          } catch (error) {
            return {failed:true, message:error.message, request_id:error.requestId || null};
          }
        }""")
        harness.fail_shared_vision = False
        failed_vision_delta = [request["path"]
                               for request in harness.requests[before_failed_vision:]]
        shared_saved = shared_page.evaluate(
            "() => StandaloneAPI.handle('/api/reports').then((rows) => rows.length)")
        if not (failed_shared == {"failed": True,
                                  "message": "Shared vision credits are exhausted. Try personal-key mode.",
                                  "request_id": "req-shared-credit"}
                and failed_vision_delta == ["/v1/vision/detect"]
                and shared_saved == 3):
            failures.append(f"shared credit failure became a verdict or local report: "
                            f"{failed_shared}, calls={failed_vision_delta}, saved={shared_saved}")
        shared_context.close()

        personal_context, personal_page, personal_openai = open_context(browser, harness, personal=True)
        personal_initial = personal_page.evaluate(CREATE_REPORT)
        # Personal mode returns the local detector result first. Central map/dedupe is
        # an outbox delivery and must not gate that result, even when the server is up.
        if not (personal_initial["status"] == "draft"
                and personal_initial["central_sync_pending"] is True
                and personal_initial["server_pothole_id"] is None):
            failures.append(f"personal capture did not return its local result first: {personal_initial}")
        personal_page.wait_for_function("""id => StandaloneAPI.handle('/api/reports')
          .then((rows) => rows.some((row) => row.id === id
            && row.central_sync_pending === false))""", arg=personal_initial["id"])
        personal = personal_page.evaluate(READ_REPORT, personal_initial["id"])
        # The anonymous personal-mode counter is deliberately fire-and-forget. Give its
        # signed request a bounded moment to finish without coupling it to the verdict.
        for _ in range(40):
            if any(request["path"] == "/v1/activity" for request in harness.requests):
                break
            personal_page.wait_for_timeout(25)

        if len(personal_openai) != 1:
            failures.append(f"personal mode made {len(personal_openai)} direct OpenAI calls")
        elif personal_openai[0]["headers"].get("authorization") != "Bearer sk-personal-test-secret":
            failures.append("personal OpenAI call did not use the personal key")
        else:
            personal_body = json.loads(personal_openai[0]["body"])
            prompt_items = [item for item in personal_body["input"][0]["content"]
                            if item.get("type") == "input_text"]
            expected_prompt = ("Inspect the single supplied road image for a civic complaint app."
                               in prompt_items[0].get("text", "")
                               and "Capture source: one user-framed image."
                               in prompt_items[0].get("text", "")) if len(prompt_items) == 1 else False
            if not expected_prompt:
                failures.append(f"personal request omitted the canonical manual capture layout: "
                                f"{prompt_items}")

        paths = [request["path"] for request in harness.requests]
        if paths.count("/v1/vision/detect") != 5:
            failures.append(f"shared detection count was {paths.count('/v1/vision/detect')}")
        shared_detections = [request["body"] for request in harness.requests
                             if request["path"] == "/v1/vision/detect"]
        if any(body.get("image_detail") != "original"
               or body.get("model") != "gpt-5.6"
               or len(body.get("images") or []) != 1
               or set(body["images"][0]) != {"data_url"}
               or body.get("capture_source") != "manual"
               or body.get("location_source") != "device_gps"
               or not body.get("client_observation_id")
               or body.get("lat") != 12.9716
               or body.get("lng") != 77.5946
               for body in shared_detections):
            failures.append(f"shared detection omitted detail, observation, location, or image: "
                            f"{shared_detections}")
        detection_by_observation = {
            body["client_observation_id"]: body
            for body in shared_detections if body.get("client_observation_id")
        }
        shared_reports = [request["body"] for request in harness.requests
                          if request["path"] == "/v1/potholes/report"
                          and request["body"].get("detector", {}).get("provider") ==
                          "shared_server"]
        for report_body in shared_reports:
            observation_id = report_body.get("client_observation_id")
            detection_body = detection_by_observation.get(observation_id)
            if not detection_body:
                failures.append(f"shared report has no matching detection observation: {report_body}")
                continue
            data_url = detection_body["images"][0]["data_url"]
            encoded = data_url.split(",", 1)[1]
            expected_hash = hashlib.sha256(base64.b64decode(encoded)).hexdigest()
            expected_receipt = hashlib.sha256(
                f"receipt:{observation_id}".encode("utf-8")
            ).hexdigest()
            if (report_body.get("detection_receipt") != expected_receipt
                    or report_body.get("image_hash") != expected_hash
                    or report_body.get("lat") != detection_body.get("lat")
                    or report_body.get("lng") != detection_body.get("lng")):
                failures.append(
                    "shared report did not preserve its receipt/image/location binding: "
                    f"detect={detection_body}, report={report_body}"
                )
        if paths.count("/v1/activity") != 1:
            failures.append(f"personal activity count was {paths.count('/v1/activity')}")
        activities = [request["body"] for request in harness.requests
                      if request["path"] == "/v1/activity"]
        if activities != [{"event": "vision_check", "vision_provider": "personal_openai",
                           "capture_mode": "manual", "capture_source": "manual",
                           "location_source": "device_gps"}]:
            failures.append(f"personal activity leaked extra data: {activities}")
        if paths.count("/v1/tenders/resolve") != 3 or paths.count("/v1/potholes/report") != 6:
            failures.append(f"shared tender or central report count changed: {paths}")
        tenders = [request for request in harness.requests
                   if request["path"] == "/v1/tenders/resolve"]
        if any(not request["headers"].get("idempotency-key", "").startswith("tender-")
               for request in tenders):
            failures.append("tender resolution did not send a stable idempotency key")
        tender_keys = [request["headers"].get("idempotency-key") for request in tenders]
        expected_shared_key = "tender-" + hashlib.sha256(
            shared["client_observation_id"].encode("utf-8")).hexdigest()
        if tender_keys[0] != expected_shared_key or len(set(tender_keys)) != len(tender_keys):
            failures.append("tender idempotency is not scoped to its client observation")
        for request in harness.requests:
            if request["path"].startswith("/v1/") and "sk-personal-test-secret" in (
                    json.dumps(request["body"]) + json.dumps(request["headers"])):
                failures.append(f"personal key leaked to {request['path']}")

        if not (shared["status"] == "draft" and not shared["server_duplicate"]
                and shared["server_pothole_id"] == "101"
                and shared["central_sync_pending"] is False
                and shared["server_sync_error"] is None
                and shared["tender_number"] == "TEST-2026-1"
                and shared["contractor"] == "Example Roads Ltd"
                and shared["vision_provider"] == "shared_server"):
            failures.append(f"first central report was not a new draft: {shared}")
        # A second device photographing the same pothole keeps its evidence and keeps its
        # complaint. The shared map still counts both sightings against one pothole.
        if not (personal["status"] == "draft" and personal["server_duplicate"]
                and personal["server_pothole_id"] == "101" and personal["seen_count"] == 3
                and personal["has_photo"]
                and personal_initial["has_photo_full"] and personal["email_subject"] is not None
                and personal["email_body"] is not None):
            failures.append(f"a second device's sighting lost its evidence or its "
                            f"complaint: {personal}")

        ui = personal_page.evaluate("""() => {
          openDetail(window.__contractReport);
          return {text:document.getElementById('detail').innerText,
            photos:document.querySelectorAll('#detail img').length,
            send:!!document.getElementById('sendBtn'),
            condition:!!document.getElementById('conditionBtn')};
        }""")
        if ui["photos"] != 1 or not ui["send"] or "Already reported" in ui["text"]:
            failures.append(f"a repeat sighting's detail screen withheld its complaint "
                            f"or still called it already reported: {ui}")

        # Repair/status updating is intentionally absent: the app records road damage
        # and opens an email complaint, but never tries to infer or mutate "fixed" state.
        if any(request["path"].endswith("/condition") for request in harness.requests):
            failures.append("browser sent a removed pothole-condition request")

        personal_context.close()

        # The browser's older local ownership gate only knows the national-highway
        # layer, so its mocked KGIS answer deliberately looks municipal here. In shared
        # mode the server's richer signed resolution is authoritative: state and district
        # highways must remain useful map observations without becoming city complaints.
        for ownership in ("state_highway", "district_highway"):
            failures.extend(verify_shared_highway_refusal(browser, ownership))
        browser.close()

    if len(harness.installations) != 2:
        failures.append(f"expected two device identities, got {len(harness.installations)}")
    for install_id in harness.installations:
        used = {request["headers"].get("x-install-id") for request in harness.requests
                if request["path"] != "/v1/installations"
                and request["headers"].get("x-install-id") == install_id}
        if used != {install_id}:
            failures.append(f"installation identity was not reused: {install_id}")
    failures.extend(verify_signatures(harness))

    if failures:
        print("FAIL")
        for failure in failures:
            print("  -", failure)
        sys.exit(1)
    print("SERVER CLIENT CONTRACT TEST PASS")


if __name__ == "__main__":
    main()
