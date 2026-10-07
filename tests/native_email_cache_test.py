# -*- coding: utf-8 -*-
"""Native Email taps reuse and persist one prepared complaint draft."""
import json
import os
import sys
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright


APP = os.environ.get("POTHOLE_TEST_APP", "http://localhost:8765/")
SERVICE = "https://native-email-authority.test"
fails = []
remote_leaks = []
central_calls = []


with sync_playwright() as playwright:
    browser = playwright.chromium.launch()
    context = browser.new_context(viewport={"width": 390, "height": 844})
    context.add_init_script(script=f"""(() => {{
      localStorage.setItem("service_url", {json.dumps(SERVICE)});
      localStorage.setItem("vision_provider", "shared");
    }})();""")

    def central_service(route):
        request = route.request
        path = urlparse(request.url).path
        body = json.loads(request.post_data or "{}") if request.method == "POST" else {}
        if path == "/v1/health":
            payload, status = {"ok": True, "shared_vision_configured": True}, 200
        elif path == "/v1/installations":
            payload, status = {"install_id": "native-email-cache-test"}, 201
        elif path == "/v1/tenders/resolve":
            central_calls.append(body)
            same_point_count = sum(1 for item in central_calls if item.get("lat") == 13.03)
            if body.get("lat") == 13.03 and same_point_count == 1:
                payload, status = {
                    "jurisdiction": {
                        "lat": body.get("lat"), "lng": body.get("lng"),
                        "address": "Cache Test Road, Test City", "road_ownership": "municipal",
                        "lgd": "248127", "town": "Kalaburagi", "town_type": "CC",
                    },
                    "tender": None, "reason": "no_tender_match",
                }, 200
            elif body.get("lat") == 13.03:
                payload, status = {"error": "temporary_failure", "message": "synthetic outage"}, 503
            else:
                payload, status = {
                    "jurisdiction": {
                        "lat": body.get("lat"), "lng": body.get("lng"),
                        "road_ownership": "national_highway", "highway_name": "NH 48",
                    },
                    "tender": None, "reason": "national_highway",
                }, 200
        else:
            payload, status = {"error": "not_mocked", "message": path}, 404
        route.fulfill(status=status, content_type="application/json", body=json.dumps(payload))

    def block_remote(route):
        url = route.request.url
        if url == APP + "karnataka-bodies.json":
            route.fulfill(status=200, content_type="application/json", body=json.dumps({
                "bodies": {"248127": {"name": "Kalaburagi", "type": "CC",
                    "officer": "Commissioner", "email": "ka.kalaburagi.cc@gmail.com"}}
            }))
        elif url.startswith(APP) or url.startswith("data:") or url.startswith("blob:"):
            route.continue_()
        elif url.startswith(SERVICE):
            central_service(route)
        else:
            remote_leaks.append(url)
            route.abort()

    context.route("**/*", block_remote)
    page = context.new_page()
    page.goto(APP)
    page.wait_for_load_state("networkidle")
    page.wait_for_function("typeof sendReport === 'function'", timeout=30_000)
    result = page.evaluate(r"""async () => {
      const originalPrepare = StandaloneAPI.prepareComplaint;
      const preparationCalls = [];
      const saved = [];
      const saveConflicts = [];
      const persistedAt = 1900000077;
      const photos = [];
      const composers = [];
      const alerts = [];
      window.alert = (message) => alerts.push(String(message));
      StandaloneAPI.prepareComplaint = async (report) => {
        preparationCalls.push(report.id);
        if (report.id === "native_88") throw new Error("synthetic ownership rejection");
        return {
          to: "ka.kalaburagi.cc@gmail.com",
          officer_name: "Commissioner, Test City",
          subject: "Road damage on Test Road",
          body: "Please inspect the attached road damage.",
          address: "Test Road, Test City",
          body_lgd: "248127",
          body_name: "Test City",
          road_ownership: "municipal",
          road_ownership_source: "central_v1",
          tender: { tender_number: "T-77", contractor: "Road Works Ltd", note: "Probable match" },
        };
      };
      const driveMode = {
        saveComplaintPreparation: async (payload) => {
          if (payload.id === 89) {
            saveConflicts.push({ ...payload });
            throw new Error("Road ownership changed before email preparation");
          }
          saved.push({ ...payload });
          return {
            id: payload.id, decision: "accept", status: "queued",
            server_pothole_id: 7077, server_duplicate: false,
            road_ownership: "municipal",
            tender_resolution_checked_at: persistedAt,
            tender_number: payload.tenderNumber,
            contractor: payload.contractor, tender_note: payload.tenderNote,
            address: payload.address, body_lgd: payload.bodyLgd,
            body_name: payload.bodyName, email_to: payload.emailTo,
            officer_title: payload.officerTitle,
            email_subject: payload.emailSubject, email_body: payload.emailBody,
          };
        },
        getReportPhoto: async (payload) => {
          photos.push({ ...payload });
          return { dataUrl: "data:image/jpeg;base64,/9j/AAAA" };
        },
      };
      const emailComposer = {
        open: async (payload) => { composers.push({ ...payload }); return { value: true }; },
      };
      window.Capacitor = {
        isNativePlatform: () => true,
        Plugins: { DriveMode: driveMode, EmailComposer: emailComposer },
        registerPlugin: (name) => name === "EmailComposer" ? emailComposer : driveMode,
      };
      const nativeReport = (id) => ({
        id: `native_${id}`, _nativeId: id, _native: true,
        _nativeStoredStatus: "queued", status: "draft", decision: "accept",
        assessment: "damaged", image_quality: "acceptable",
        damage_type: "pothole_cavity", size: "medium", description: "A pothole.",
        lat: 12.9, lng: 77.6, created_at: Date.now() / 1000,
        server_pothole_id: 7077, server_duplicate: false,
        detection_provider: "shared_server", photo_url: "",
      });
      try {
        const first = nativeReport(77);
        openDetail(first, [first]);
        await sendReport(first);
        await sendReport(first);

        // Simulate a fresh Activity/history load containing what the bridge persisted.
        const reloaded = {
          ...nativeReport(77), email_to: "ka.kalaburagi.cc@gmail.com",
          officer_title: "Commissioner, Test City",
          email_subject: "Road damage on Test Road",
          email_body: "Please inspect the attached road damage.",
          tender_resolution_checked_at: persistedAt,
          road_ownership: "municipal",
          address: "Test Road, Test City", body_lgd: "248127", body_name: "Test City",
          tender_number: "T-77", contractor: "Road Works Ltd",
          tender_note: "Probable match",
        };
        openDetail(reloaded, [reloaded]);
        await sendReport(reloaded);

        // A legacy shared row may contain stale municipal recipient/contractor text but
        // no authoritative ownership. It must call the resolver and must not compose if
        // that revalidation rejects it.
        const unsafeLegacy = {
          ...nativeReport(88), email_to: "stale@example.gov.in",
          email_subject: "Stale municipal draft", email_body: "Stale body",
          tender_number: "STALE-TENDER", contractor: "Stale Contractor",
          tender_note: "Stale probable contract",
          tender_resolution_checked_at: 1700000000,
        };
        openDetail(unsafeLegacy, [unsafeLegacy]);
        const unsafeLegacyDisplayedStale = document.getElementById("detail").innerText
          .includes("Stale probable contract");
        await sendReport(unsafeLegacy);

        // UploadWorker may finish a central ownership check after JS prepares a draft
        // but before Room persists it. A native ownership-conflict rejection must stop
        // the composer instead of sending the now-stale municipal draft.
        const ownershipRace = nativeReport(89);
        openDetail(ownershipRace, [ownershipRace]);
        await sendReport(ownershipRace);

        const pendingCentral = { ...nativeReport(90), server_pothole_id: null };
        openDetail(pendingCentral, [pendingCentral]);
        const pendingSendButtons = document.querySelectorAll("#detail #sendBtn").length;

        StandaloneAPI.prepareComplaint = originalPrepare;
        const providerToggle = async (report, provider, key) => {
          localStorage.setItem("vision_provider", provider);
          if (key) localStorage.setItem("openai_key", key);
          else localStorage.removeItem("openai_key");
          try {
            const complaint = await originalPrepare(report);
            return { composed: true, to: complaint.to, road_ownership: complaint.road_ownership };
          } catch (error) {
            return { composed: false, code: error.unroutedReason || error.code || null,
              body: error.unroutedBody || null };
          }
        };
        // Provenance wins over Settings: a shared record remains centrally routed after
        // the user switches to a personal key.
        const sharedToPersonal = await providerToggle({
          ...nativeReport(91), lat: 13.01, lng: 77.61,
          email_to: "stale-city@example.gov.in", officer_title: "Stale Commissioner",
          email_subject: "Stale", email_body: "Stale", contractor: "Stale Contractor",
          tender_resolution_checked_at: 1700000000,
        }, "personal", "test-personal-key");
        // Effective provider wins for old personal rows: once its key is gone, shared
        // central ownership must replace the old phone-side municipal assumption.
        const personalToShared = await providerToggle({
          ...nativeReport(92), detection_provider: "personal_openai",
          lat: 13.02, lng: 77.62, email_to: "stale-city@example.gov.in",
          officer_title: "Stale Commissioner", email_subject: "Stale", email_body: "Stale",
          contractor: "Stale Contractor", tender_resolution_checked_at: 1700000000,
        }, "personal", null);

        // The same coordinates first resolve municipally and then time out. The second
        // record must not inherit the first call's centralResolutionCache entry.
        localStorage.setItem("vision_provider", "shared");
        localStorage.removeItem("openai_key");
        const cacheSuccess = await originalPrepare({
          id: "cache-success", vision_provider: "shared_server",
          lat: 13.03, lng: 77.63, assessment: "damaged", damage_type: "pothole_cavity",
          size: "medium", description: "A pothole.",
        });
        let cacheFailure;
        try {
          const unsafe = await originalPrepare({
            id: "cache-failure", vision_provider: "shared_server",
            lat: 13.03, lng: 77.63, assessment: "damaged", damage_type: "pothole_cavity",
            size: "medium", description: "A pothole.",
            email_to: "stale@example.gov.in", officer_title: "Stale Commissioner",
          });
          cacheFailure = { composed: true, to: unsafe.to };
        } catch (error) {
          cacheFailure = { composed: false, code: error.unroutedReason || error.code || null };
        }
        return {
          preparationCalls, saved, saveConflicts, photos, composers, alerts,
          persistedAt, unsafeLegacyDisplayedStale, pendingSendButtons,
          sharedToPersonal, personalToShared, cacheSuccess, cacheFailure,
        };
      } finally {
        StandaloneAPI.prepareComplaint = originalPrepare;
      }
    }""")
    browser.close()


if remote_leaks:
    fails.append(f"real network request escaped the deterministic test: {remote_leaks}")
if result["preparationCalls"] != ["native_77", "native_88", "native_89"]:
    fails.append(f"native complaint enrichment repeated: {result['preparationCalls']}")
if len(result["saved"]) != 3:
    fails.append(f"prepared native complaint was persisted {len(result['saved'])} times")
elif any(item.get("id") != 77 or item.get("emailTo") != "ka.kalaburagi.cc@gmail.com"
         for item in result["saved"]):
    fails.append(f"native persistence payload was incomplete: {result['saved'][0]}")
elif result["saved"][0].get("roadOwnership") != "municipal":
    fails.append(f"authoritative ownership was not persisted: {result['saved'][0]}")
elif result["saved"][1].get("expectedTenderResolutionCheckedAt") != result["persistedAt"]:
    fails.append(f"second tap did not use the Room timestamp returned by the first: {result['saved']}")
elif any(item.get("expectedServerPotholeId") != 7077 for item in result["saved"]):
    fails.append(f"native bridge did not lock the confirmed central pothole ID: {result['saved']}")
if len(result["composers"]) != 3 or len(result["photos"]) != 3:
    fails.append("reusing preparation prevented an email/photo open or opened extras: "
                 f"composer={len(result['composers'])}, photos={len(result['photos'])}")
if not any("synthetic ownership rejection" in message for message in result["alerts"]):
    fails.append(f"unsafe legacy cached draft was not rejected: {result['alerts']}")
if len(result["saveConflicts"]) != 1 or result["saveConflicts"][0].get("id") != 89:
    fails.append(f"native ownership race did not reach the persistence guard: {result['saveConflicts']}")
if not any("Road ownership changed" in message for message in result["alerts"]):
    fails.append(f"native ownership conflict did not surface before composition: {result['alerts']}")
if result["unsafeLegacyDisplayedStale"]:
    fails.append("native detail displayed a stale personal/pre-policy contractor")
if result["pendingSendButtons"] != 0:
    fails.append("native detail offered Email before the central duplicate check completed")
for name in ("sharedToPersonal", "personalToShared"):
    outcome = result[name]
    if outcome.get("composed") or outcome.get("code") != "national_highway":
        fails.append(f"{name} trusted a stale municipal cache instead of central highway ownership: {outcome}")
# Four ownership checks, and one ward lookup: the second Email tap on report 77 finds its
# owner proven and asks, once, which road works were tendered for its ward. The tap after
# the reload reuses that answer (ward_tenders_on_demand_test holds the rest).
if len(central_calls) != 5:
    fails.append(f"authority cases made {len(central_calls)} central calls, expected 4 ownership "
                 "checks and 1 ward lookup")
elif sum(1 for call in central_calls if call.get("lat") == 12.9) != 1:
    fails.append(f"the Drive Mode report was asked about its ward more than once: {central_calls}")
if result["cacheSuccess"].get("road_ownership") != "municipal":
    fails.append(f"central municipal success was not usable: {result['cacheSuccess']}")
if result["cacheFailure"].get("composed") or result["cacheFailure"].get("code") != "road_class_unknown":
    fails.append(f"same-coordinate central failure reused a stale success: {result['cacheFailure']}")

print(f"  preparation/persist/composer calls: {len(result['preparationCalls'])}/{len(result['saved'])}/{len(result['composers'])}")
if fails:
    print("\nFAIL")
    for failure in fails:
        print("  -", failure)
    sys.exit(1)
print("\nNATIVE EMAIL CACHE TEST PASS")
