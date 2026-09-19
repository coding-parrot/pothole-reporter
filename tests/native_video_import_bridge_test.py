# -*- coding: utf-8 -*-
"""The Web UI actually consumes the native VideoImport bridge contract.

This mocks Capacitor/Media3 at the JavaScript boundary: more than 15 extracted frames,
multiple clips in picker order, one open native decoder, close-before-consume, cold/warm
pending delivery, terminal quota failure and Back cancellation. No network is used.
"""
import json
import os
import sys

from playwright.sync_api import sync_playwright

# The data notice version is read from the bundle: a pinned copy that falls behind
# leaves every run of this suite stuck on the consent screen it thought it accepted.
from flow_harness import DATA_NOTICE_VERSION


APP = os.environ.get("POTHOLE_TEST_APP", "http://localhost:8765/")
fails = []
remote_leaks = []


with sync_playwright() as playwright:
    browser = playwright.chromium.launch()
    context = browser.new_context(viewport={"width": 390, "height": 844})
    context.add_init_script("""
      localStorage.setItem('vision_provider', 'shared');
      localStorage.setItem('data_notice_version', '__DATA_NOTICE_VERSION__');
    """.replace("__DATA_NOTICE_VERSION__", DATA_NOTICE_VERSION))

    def block_remote(route):
        url = route.request.url
        if url.startswith(APP) or url.startswith("blob:") or url.startswith("data:"):
            route.continue_()
        elif url == "https://ffjvg34k07.execute-api.ap-south-1.amazonaws.com/v1/health":
            route.fulfill(status=200, content_type="application/json", body=json.dumps({
                "ok": True, "shared_vision_configured": True,
            }))
        else:
            remote_leaks.append(url)
            route.abort()

    context.route("**/*", block_remote)
    page = context.new_page()
    page.goto(APP)
    page.wait_for_load_state("networkidle")
    page.wait_for_function("typeof analyseNativeImportedVideos === 'function'", timeout=30_000)

    result = page.evaluate(r"""async () => {
      const original = { api, loadReports, ensureDataConsent, ensureVisionAvailable,
        getPosition, alert: window.alert, confirm: window.confirm, Capacitor: window.Capacitor };
      const makeVideo = (id, name, duration = 10000) => ({
        id, display_name: name, mime_type: 'video/mp4', bytes: 1000000,
        created_at_ms: 1800000000000,
        file_uri: `content://test/video/${id}`,
        content_token: `content-${id}`,
        source_fingerprint: `source-${id}`,
        storage_mode: id === 'B' ? 'temporary_cache_copy' : 'persisted_content_uri',
        source: id === 'B' ? 'android_share' : 'android_picker',
        source_action: id === 'B' ? 'android.intent.action.SEND' : 'android.intent.action.OPEN_DOCUMENT',
        duration_ms: duration, recorded_at: null, recorded_at_ms: null,
        width: 1920, height: 1080, rotation_degrees: 0, codec_mime: 'video/avc',
        playback_support: 'direct',
        // A single file-level point must stay informational; this was a moving video.
        embedded_location: { lat: 12.97, lng: 77.59,
          source: 'video_metadata_static', routing_eligible: false },
      });
      // Deliberately identical visible metadata: the stable native handle + batch ordinal
      // must keep these sources distinct even when camera filenames collide.
      let pending = [makeVideo('A', 'same.mp4'), makeVideo('B', 'same.mp4')];
      let listener = null, pendingReads = 0, currentSession = null, maxSessions = 0;
      let sessionCounter = 0, positionCalls = 0, frameCalls = 0;
      const calls = [], consumed = [], discarded = [], alerts = [], frameForms = [], driveWrites = [];
      let clearCalls = 0;
      const snapshot = () => ({ imports: pending.slice(), pending_count: pending.length,
        pending_bytes: 0, error: null });
      const plugin = {
        addListener: async (name, callback) => {
          calls.push(`listen:${name}`); listener = callback;
          return { remove: async () => {} };
        },
        getPendingImports: async () => { pendingReads++; return snapshot(); },
        pickVideo: async () => ({ cancelled: false, videos: pending.slice(), ...snapshot() }),
        openAnalysis: async ({ id, seekMode, maxHeight }) => {
          if (currentSession) throw new Error('parallel native decoder');
          const video = pending.find((item) => item.id === id);
          if (!video) throw new Error('missing pending video');
          currentSession = `session-${++sessionCounter}-${id}`;
          maxSessions = Math.max(maxSessions, currentSession ? 1 : 0);
          calls.push(`open:${id}:${seekMode}:${maxHeight}`);
          return { session_id: currentSession, video_id: id, duration_ms: video.duration_ms,
            output_width: 1280, output_height: 720, max_pixels: 2073600 };
        },
        extractFrame: async ({ sessionId, positionMs, quality }) => {
          if (sessionId !== currentSession) throw new Error('wrong native session');
          calls.push(`extract:${sessionId}:${positionMs}:${quality}`);
          return { session_id: sessionId, requested_position_ms: positionMs,
            presentation_position_ms: positionMs, width: 320, height: 180,
            mime_type: 'image/jpeg', bytes: 4, jpeg_base64: '/9j/2Q==' };
        },
        closeAnalysis: async ({ sessionId }) => {
          if (sessionId !== currentSession) throw new Error('close wrong native session');
          const id = sessionId.split('-').pop();
          calls.push(`close:${id}`); currentSession = null;
          return { closed: true, session_id: sessionId };
        },
        consumePendingImport: async ({ id }) => {
          if (currentSession) throw new Error('consumed before decoder close');
          calls.push(`consume:${id}`); consumed.push(id);
          pending = pending.filter((item) => item.id !== id);
          const next = snapshot(); if (listener) listener(next); return next;
        },
        discardPendingImport: async ({ id }) => {
          if (currentSession) throw new Error('discarded before decoder close');
          calls.push(`discard:${id}`); discarded.push(id);
          pending = pending.filter((item) => item.id !== id);
          return snapshot();
        },
        clearAllImports: async () => {
          clearCalls++; calls.push('clearAll'); pending = [];
          return { cleared: true, ...snapshot() };
        },
      };
      try {
        window.Capacitor = {
          isNativePlatform: () => true,
          Plugins: { VideoImport: plugin },
          registerPlugin: () => plugin,
        };
        videoImportPluginCache = null;
        videoImportListener = null;
        ensureDataConsent = async () => true;
        ensureVisionAvailable = async () => true;
        getPosition = async () => { positionCalls++; return { lat: 1, lng: 2 }; };
        loadReports = async () => {};
        window.confirm = () => true;
        window.alert = (message) => alerts.push(String(message));
        api = async (path, options = {}) => {
          if (path === '/api/frame') {
            frameCalls++;
            const fd = options.body;
            frameForms.push({ drive: fd.get('drive_id'), key: fd.get('source_event_key'),
              source: fd.get('capture_source'), locationSource: fd.get('location_source'),
              lat: fd.get('lat'), lng: fd.get('lng'), captured: fd.get('captured_at_ms'),
              photoBytes: fd.get('photo') && fd.get('photo').size });
            return { analyzed: true, accepted: false, stored: false, found: false,
              duplicate: false, quota: { used: frameCalls, limit: 200 },
              observation: { assessment: 'undamaged' } };
          }
          if (path === '/api/drives' && (!options.method || options.method === 'GET')) return [];
          if (path === '/api/drives' && options.method === 'POST') {
            driveWrites.push(JSON.parse(options.body)); return { ok: true };
          }
          throw new Error(`unexpected native API path ${path}`);
        };
        IMPORT_VIDEO_MAX_SAMPLES = 18;
        await initNativeVideoImports();
        const coldPending = nativePendingVideos.map((item) => item.id);
        const analyseAllVisible = !!document.getElementById('nativeVideoAnalyseAll');
        const temporaryCopyVisible = document.getElementById('nativeVideoImports')
          .textContent.includes('24 hours');
        if (listener) listener(snapshot()); // retained warm event uses the same renderer
        const warmPending = nativePendingVideos.map((item) => item.id);
        const reselectOne = { ...makeVideo('random-pending-1', 'repeat.mp4'),
          content_token: 'same-durable-source' };
        const reselectTwo = { ...makeVideo('random-pending-2', 'repeat.mp4'),
          content_token: 'same-durable-source' };
        const reselectionStable = await stableNativeImportedDriveId([reselectOne], 'none')
          === await stableNativeImportedDriveId([reselectTwo], 'none');
        const full = await analyseNativeImportedVideos(pending.slice(), {
          gpx: null, useCurrentLocation: false,
        });
        await new Promise((resolve) => setTimeout(resolve, 0));
        const orderAfterFull = calls.slice();
        const consumedAfterFull = consumed.slice();

        // Terminal quota/auth/budget errors close but retain the pending handle, and do
        // not move on to frame 2 or consume the source.
        pending = [makeVideo('Q', 'quota.mp4', 20000)];
        if (listener) listener(snapshot());
        let terminalFrames = 0;
        api = async (path, options = {}) => {
          if (path === '/api/frame') {
            terminalFrames++;
            const error = new Error('daily shared limit');
            error.status = 429;
            throw error;
          }
          if (path === '/api/drives' && (!options.method || options.method === 'GET')) return [];
          if (path === '/api/drives' && options.method === 'POST') return { ok: true };
          throw new Error(`unexpected terminal native path ${path}`);
        };
        const consumedBeforeTerminal = consumed.length;
        const terminal = await analyseNativeImportedVideos(pending.slice(), {
          gpx: null, useCurrentLocation: false,
        });
        const retainedAfterTerminal = pending.map((item) => item.id);
        const consumedTerminal = consumed.length - consumedBeforeTerminal;

        // Android Back detaches UI, closes after the current extraction/request, and
        // retains the pending URI for an explicit retry or Discard.
        pending = [makeVideo('C', 'cancel.mp4', 20000)];
        if (listener) listener(snapshot());
        let cancelFrames = 0, releaseFrame;
        const gate = new Promise((resolve) => { releaseFrame = resolve; });
        api = async (path, options = {}) => {
          if (path === '/api/frame') {
            cancelFrames++; await gate;
            return { analyzed: true, accepted: false, stored: false, found: false,
              duplicate: false, quota: { used: 1, limit: 200 },
              observation: { assessment: 'undamaged' } };
          }
          if (path === '/api/drives' && (!options.method || options.method === 'GET')) return [];
          if (path === '/api/drives' && options.method === 'POST') return { ok: true };
          throw new Error(`unexpected cancel native path ${path}`);
        };
        const consumedBeforeCancel = consumed.length;
        const cancelling = analyseNativeImportedVideos(pending.slice(), {
          gpx: null, useCurrentLocation: false,
        });
        const until = Date.now() + 2000;
        while (!cancelFrames && Date.now() < until) await new Promise((resolve) => setTimeout(resolve, 5));
        const backHandled = handleAppBack();
        releaseFrame();
        const cancelled = await cancelling;
        const retainedAfterCancel = pending.map((item) => item.id);
        const consumedCancel = consumed.length - consumedBeforeCancel;

        // Settings wipe is a write barrier even after Back detached the import UI. It
        // must cancel and await the old job before asking native code to release grants
        // and delete direct-share copies.
        let releaseWipeJob;
        const wipeToken = { cancelled: false, silent: false };
        const wipeJob = { token: wipeToken, promise: new Promise((resolve) => {
          releaseWipeJob = () => { calls.push('late-write-finished'); resolve(); };
        }) };
        importedVideoJob = wipeJob;
        const wiping = clearImportedVideosBeforeWipe();
        await new Promise((resolve) => setTimeout(resolve, 0));
        const clearBeforeWipeJobSettled = clearCalls;
        releaseWipeJob();
        await wiping;
        const wipeOrdering = calls.slice(-2);
        const wipeHandlerUsesBarrier = String(document.getElementById('wipeBtn').onclick)
          .includes('clearImportedVideosBeforeWipe');

        return {
          pendingReads, coldPending, warmPending, analyseAllVisible, temporaryCopyVisible,
          reselectionStable,
          full, frameCalls, frameForms, driveWrites, maxSessions,
          calls: orderAfterFull, consumedAfterFull, positionCalls,
          terminal, terminalFrames, retainedAfterTerminal, consumedTerminal,
          cancelled, cancelFrames, retainedAfterCancel, consumedCancel, backHandled,
          currentSession, alerts, clearBeforeWipeJobSettled, wipeOrdering,
          wipeCancelled: wipeToken.cancelled && wipeToken.silent,
          clearCalls, wipeHandlerUsesBarrier,
        };
      } finally {
        api = original.api; loadReports = original.loadReports;
        ensureDataConsent = original.ensureDataConsent;
        ensureVisionAvailable = original.ensureVisionAvailable;
        getPosition = original.getPosition;
        window.alert = original.alert; window.confirm = original.confirm;
        window.Capacitor = original.Capacitor;
        videoImportPluginCache = null;
      }
    }""")
    browser.close()


if remote_leaks:
    fails.append(f"real remote request escaped the deterministic test: {remote_leaks}")
if result["pendingReads"] < 1 or result["coldPending"] != ["A", "B"] \
        or result["warmPending"] != ["A", "B"]:
    fails.append(f"cold/warm pending import delivery is not wired: {result}")
if not result["analyseAllVisible"]:
    fails.append("multiple native pending videos have no visible Analyse all action")
if not result["temporaryCopyVisible"]:
    fails.append("pending direct-share video does not disclose its private 24-hour copy")
if not result["reselectionStable"]:
    fails.append("a native SAF re-selection changed identity because of its random pending id")
if result["frameCalls"] != 18 or result["full"].get("checked") != 18:
    fails.append(f"native import stalled before/around 15 frames: {result['frameCalls']}, {result['full']}")
if result["maxSessions"] != 1 or result["currentSession"] is not None:
    fails.append(f"native decoder sessions overlapped or leaked: {result['maxSessions']}/{result['currentSession']}")
if result["consumedAfterFull"] != ["A", "B"]:
    fails.append(f"successful picker-order clips were not consumed in order: {result['consumedAfterFull']}")
calls = result["calls"]
for video_id in ("A", "B"):
    try:
        close_index = calls.index(f"close:{video_id}")
        consume_index = calls.index(f"consume:{video_id}")
    except ValueError:
        fails.append(f"native {video_id} lacks close/consume lifecycle: {calls}")
    else:
        if close_index > consume_index:
            fails.append(f"native {video_id} was consumed before decoder close: {calls}")
if result["positionCalls"]:
    fails.append("native import requested current GPS without the explicit checkbox")
forms = result["frameForms"]
if not forms or any(row["source"] != "imported_video" or row["locationSource"] != "none"
                    or row["lat"] is not None or row["lng"] is not None
                    or row["captured"] is not None for row in forms):
    fails.append("static embedded metadata was incorrectly treated as a timed route, or provenance was lost")
if len({row["drive"] for row in forms}) != 1 or len({row["key"] for row in forms}) != len(forms):
    fails.append("native multi-clip import lost its combined drive or stable per-frame source identity")
if not any(row["key"].startswith("import:clip-0-") for row in forms) \
        or not any(row["key"].startswith("import:clip-1-") for row in forms):
    fails.append("native observation identities omit the explicit clip batch ordinal")
if not result["driveWrites"] or result["driveWrites"][0].get("capture_source") != "imported_video":
    fails.append(f"native import drive was not persisted: {result['driveWrites']}")

if result["terminalFrames"] != 1 or result["consumedTerminal"] != 0 \
        or result["retainedAfterTerminal"] != ["Q"]:
    fails.append("terminal native failure did not fail fast/retain the source: "
                 f"{result['terminalFrames']}/{result['consumedTerminal']}/{result['retainedAfterTerminal']}")
if result["cancelFrames"] != 1 or result["consumedCancel"] != 0 \
        or result["retainedAfterCancel"] != ["C"] or not result["backHandled"] \
        or not result["cancelled"].get("cancelled"):
    fails.append("native Back cancellation did not close and retain without another request: "
                 f"{result['cancelFrames']}/{result['consumedCancel']}/{result['retainedAfterCancel']}/{result['cancelled']}")
if result["clearBeforeWipeJobSettled"] != 0 or result["wipeOrdering"] != [
        "late-write-finished", "clearAll"] or not result["wipeCancelled"] \
        or result["clearCalls"] != 1 or not result["wipeHandlerUsesBarrier"]:
    fails.append("Settings wipe did not cancel/await the detached import before native cleanup: "
                 f"{result['clearBeforeWipeJobSettled']}/{result['wipeOrdering']}/"
                 f"{result['wipeCancelled']}/{result['clearCalls']}/{result['wipeHandlerUsesBarrier']}")

print(f"  native sampled frames: {result['frameCalls']} (one decoder: {result['maxSessions']})")
print(f"  successful consumes: {result['consumedAfterFull']}")
print(f"  terminal/cancel requests: {result['terminalFrames']}/{result['cancelFrames']}")
if fails:
    print("\nFAIL")
    for failure in fails:
        print("  -", failure)
    sys.exit(1)
print("\nNATIVE VIDEO IMPORT BRIDGE TEST PASS")
