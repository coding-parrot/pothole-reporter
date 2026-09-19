# -*- coding: utf-8 -*-
"""Meta-glasses/dashcam imports stay bounded, routable only with explicit provenance.

The long-run harness uses deterministic fake decoders so it can cover more than the old
12--15-frame stall without uploading anything. A second case exercises Chromium's real
MediaRecorder/WebM decoder through the same import entry point. No external service is
contacted.
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
    browser = playwright.chromium.launch(args=[
        "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
    ])
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
    page.wait_for_function("typeof analyseImportedVideos === 'function'", timeout=30_000)

    print("  running bounded multi-clip import...", flush=True)
    result = page.evaluate(r"""async () => {
      const ui = {
        visible: !!document.getElementById('videoImportBtn')
          && getComputedStyle(document.getElementById('videoImportBtn')).display !== 'none',
        multiple: document.getElementById('videoInput').multiple,
        accept: document.getElementById('videoInput').accept,
        currentDefault: document.getElementById('videoUseCurrentLocation').checked,
        noGpsText: document.getElementById('gpxImportStatus').textContent,
        importHint: document.getElementById('videoImportHint').textContent,
        privacyLocal: document.getElementById('privacyLocal').textContent,
      };

      const t0 = Date.parse('2026-09-10T10:00:00Z');
      const gpx = parseGpxTrack(`<?xml version="1.0"?><gpx><trk><trkseg>
        <trkpt lat="12" lon="77"><time>2026-09-10T10:00:00Z</time></trkpt>
        <trkpt lat="13" lon="78"><time>2026-09-10T10:00:10Z</time></trkpt>
      </trkseg></trk></gpx>`);
      const midpoint = gpxPositionAt(gpx, t0 + 5000);
      let doctypeRejected = false;
      try {
        parseGpxTrack(`<!DOCTYPE x [<!ENTITY y "bad">]><gpx><trkpt lat="1" lon="2"><time>2026-09-10T10:00:00Z</time></trkpt></gpx>`);
      } catch (_) { doctypeRejected = true; }
      let timezoneRejected = false;
      try {
        parseGpxTrack(`<gpx><trk><trkseg>
          <trkpt lat="12" lon="77"><time>2026-09-10T10:00:00</time></trkpt>
          <trkpt lat="13" lon="78"><time>2026-09-10T10:00:10</time></trkpt>
        </trkseg></trk></gpx>`);
      } catch (_) { timezoneRejected = true; }

      const codecMessage = videoCodecError({ name: 'dashcam-hevc.mov' }).message;
      const collisionFileA = new File(['AAAA'], 'same.mp4', {
        type: 'video/mp4', lastModified: 1234,
      });
      const collisionFileB = new File(['BBBB'], 'same.mp4', {
        type: 'video/mp4', lastModified: 1234,
      });
      const collisionClipA = { file: collisionFileA, index: 0,
        contentFingerprint: await boundedVideoContentFingerprint(collisionFileA) };
      const collisionClipB = { file: collisionFileB, index: 0,
        contentFingerprint: await boundedVideoContentFingerprint(collisionFileB) };
      const collisionIdentity = {
        contentDiffers: collisionClipA.contentFingerprint !== collisionClipB.contentFingerprint,
        driveDiffers: await stableImportedDriveId([collisionClipA], 'none')
          !== await stableImportedDriveId([collisionClipB], 'none'),
        sourceDiffers: await browserImportedClipSourceId(collisionClipA, 'none')
          !== await browserImportedClipSourceId(collisionClipB, 'none'),
        duplicateOrdinalsDiffer: await browserImportedClipSourceId(collisionClipA, 'none')
          !== await browserImportedClipSourceId({ ...collisionClipA, index: 1 }, 'none'),
      };

      const mvhd = new ArrayBuffer(40);
      const mv = new DataView(mvhd);
      mv.setUint32(0, 32, false);
      [0x6d, 0x76, 0x68, 0x64].forEach((byte, index) => mv.setUint8(4 + index, byte));
      mv.setUint8(8, 0);
      mv.setUint32(12, Math.floor(t0 / 1000) + QUICKTIME_TO_UNIX_SECONDS, false);
      const parsedMovieTime = quickTimeCreationMsFromBuffer(mvhd);

      const original = {
        api, loadReports, ensureDataConsent, ensureVisionAvailable, getPosition,
        openImportedVideo, closeImportedVideo, grabImportedVideoFrame,
        embeddedVideoStartMs, confirm: window.confirm, alert: window.alert,
      };
      const alerts = [], frames = [], driveWrites = [];
      let positionCalls = 0, activeDecoders = 0, maxDecoders = 0;
      let networkInFlight = 0, maxNetworkInFlight = 0;
      let wakeRequests = 0, wakeReleases = 0;
      let openCount = 0, closeCount = 0;
      try {
        ensureDataConsent = async () => true;
        ensureVisionAvailable = async () => true;
        getPosition = async () => { positionCalls++; return { lat: 1, lng: 2 }; };
        loadReports = async () => {};
        window.confirm = () => true;
        window.alert = (message) => alerts.push(String(message));
        try {
          Object.defineProperty(navigator, 'wakeLock', { configurable: true, value: {
            request: async () => {
              wakeRequests++;
              return { release: async () => { wakeReleases++; } };
            },
          }});
        } catch (_) {}

        openImportedVideo = async (file) => {
          activeDecoders++; openCount++;
          maxDecoders = Math.max(maxDecoders, activeDecoders);
          return { video: { duration: file.name.startsWith('one') ? 12 : 10 },
            url: `fake:${openCount}`, duration: file.name.startsWith('one') ? 12 : 10 };
        };
        closeImportedVideo = (opened) => {
          if (!opened) return;
          activeDecoders--; closeCount++;
        };
        embeddedVideoStartMs = async () => null;
        grabImportedVideoFrame = async () =>
          new Blob([new Uint8Array([0xff, 0xd8, 0xff, 0xd9])], { type: 'image/jpeg' });
        api = async (path, options = {}) => {
          if (path === '/api/frame') {
            networkInFlight++;
            maxNetworkInFlight = Math.max(maxNetworkInFlight, networkInFlight);
            const fd = options.body;
            frames.push({
              drive: fd.get('drive_id'), source: fd.get('capture_source'),
              key: fd.get('source_event_key'), offset: Number(fd.get('source_offset_ms')),
              locationSource: fd.get('location_source'), lat: fd.get('lat'), lng: fd.get('lng'),
              photoType: fd.get('photo') && fd.get('photo').type,
            });
            await new Promise((resolve) => setTimeout(resolve, 1));
            networkInFlight--;
            return { analyzed: true, accepted: false, stored: false, found: false,
              duplicate: false, observation: { assessment: 'undamaged' } };
          }
          if (path === '/api/drives' && (!options.method || options.method === 'GET')) return [];
          if (path === '/api/drives' && options.method === 'POST') {
            driveWrites.push(JSON.parse(options.body)); return { ok: true };
          }
          throw new Error(`unexpected API path ${path}`);
        };
        IMPORT_VIDEO_MAX_SAMPLES = 20;
        IMPORT_VIDEO_SAMPLE_SECONDS = 1;
        const files = [
          new File(['file-handle-one'], 'one.mp4', { type: 'video/mp4', lastModified: 1 }),
          new File(['file-handle-two'], 'two.mov', { type: 'video/quicktime', lastModified: 2 }),
        ];
        const first = analyseImportedVideos(files, { gpx: null, useCurrentLocation: false });
        const duplicateStart = analyseImportedVideos(files, { gpx: null, useCurrentLocation: false });
        const samePromise = first === duplicateStart;
        const completed = await first;
        await new Promise((resolve) => setTimeout(resolve, 0));

        // Back cancels a second, deliberately gated import without a late completion
        // alert taking the Home screen over again.
        let releaseFrame;
        const frameGate = new Promise((resolve) => { releaseFrame = resolve; });
        let gatedCalls = 0;
        api = async (path, options = {}) => {
          if (path === '/api/frame') {
            gatedCalls++;
            await frameGate;
            return { analyzed: true, accepted: false, stored: false, found: false,
              duplicate: false, observation: { assessment: 'undamaged' } };
          }
          if (path === '/api/drives' && (!options.method || options.method === 'GET')) return [];
          if (path === '/api/drives' && options.method === 'POST') return { ok: true };
          throw new Error(`unexpected cancel API path ${path}`);
        };
        const cancelAlertsBefore = alerts.length;
        const cancelling = analyseImportedVideos([
          new File(['third'], 'three.webm', { type: 'video/webm', lastModified: 3 }),
        ], { gpx: null, useCurrentLocation: false });
        const until = Date.now() + 2000;
        while (!gatedCalls && Date.now() < until) await new Promise((resolve) => setTimeout(resolve, 5));
        const backHandled = handleAppBack();
        const homeImmediately = !document.getElementById('home').classList.contains('hidden');
        releaseFrame();
        const cancelled = await cancelling;
        await new Promise((resolve) => setTimeout(resolve, 0));
        const homeAfterSettle = !document.getElementById('home').classList.contains('hidden');
        const lateAlerts = alerts.slice(cancelAlertsBefore);

        // A shared daily-cap response is terminal: one refusal, not a retry plus every
        // remaining sampled frame.
        let terminalCalls = 0;
        api = async (path, options = {}) => {
          if (path === '/api/frame') {
            terminalCalls++;
            const error = new Error('Shared checking daily limit reached.');
            // Even an older/generic server response with only HTTP 429 must stop the
            // batch; it must not generate hundreds of doomed follow-up calls.
            error.status = 429;
            throw error;
          }
          if (path === '/api/drives' && (!options.method || options.method === 'GET')) return [];
          if (path === '/api/drives' && options.method === 'POST') return { ok: true };
          throw new Error(`unexpected terminal API path ${path}`);
        };
        const terminalAlertsBefore = alerts.length;
        const terminal = await analyseImportedVideos([
          new File(['quota'], 'quota.webm', { type: 'video/webm', lastModified: 5 }),
        ], { gpx: null, useCurrentLocation: false });
        const terminalAlerts = alerts.slice(terminalAlertsBefore);

        return {
          ui, midpoint, doctypeRejected, timezoneRejected, codecMessage,
          collisionIdentity, parsedMovieTime,
          frameCount: frames.length, frames, driveWrites,
          samePromise, completed, positionCalls,
          maxDecoders, openCount, closeCount, maxNetworkInFlight,
          wakeRequests, wakeReleases,
          backHandled, homeImmediately, homeAfterSettle, cancelled, gatedCalls, lateAlerts,
          terminal, terminalCalls, terminalAlerts,
          alerts,
        };
      } finally {
        api = original.api;
        loadReports = original.loadReports;
        ensureDataConsent = original.ensureDataConsent;
        ensureVisionAvailable = original.ensureVisionAvailable;
        getPosition = original.getPosition;
        openImportedVideo = original.openImportedVideo;
        closeImportedVideo = original.closeImportedVideo;
        grabImportedVideoFrame = original.grabImportedVideoFrame;
        embeddedVideoStartMs = original.embeddedVideoStartMs;
        window.confirm = original.confirm;
        window.alert = original.alert;
      }
    }""")

    print("  bounded multi-clip import complete", flush=True)
    # Real browser-decoder smoke test: generate a short WebM locally, then use the
    # production metadata/seek/canvas functions and only replace the detector boundary.
    print("  running real WebM import...", flush=True)
    decoder = page.evaluate(r"""async () => {
      const original = { api, loadReports, ensureDataConsent, ensureVisionAvailable,
        confirm: window.confirm, alert: window.alert };
      const alerts = [];
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { width: 320, height: 240 }, audio: false,
        });
        const mime = ['video/webm;codecs=vp8', 'video/webm']
          .find((type) => MediaRecorder.isTypeSupported(type));
        if (!mime) return { skipped: true, reason: 'no WebM MediaRecorder' };
        const parts = [];
        const recorder = new MediaRecorder(stream, { mimeType: mime });
        recorder.ondataavailable = (event) => { if (event.data && event.data.size) parts.push(event.data); };
        recorder.start();
        await new Promise((resolve) => setTimeout(resolve, 1100));
        await new Promise((resolve) => { recorder.onstop = resolve; recorder.stop(); });
        stream.getTracks().forEach((track) => track.stop());
        const file = new File(parts, 'meta-export.webm', { type: mime, lastModified: 4 });
        ensureDataConsent = async () => true;
        ensureVisionAvailable = async () => true;
        loadReports = async () => {};
        window.confirm = () => true;
        window.alert = (message) => alerts.push(String(message));
        let frames = 0;
        api = async (path, options = {}) => {
          if (path === '/api/frame') {
            frames++;
            const photo = options.body.get('photo');
            if (!photo || photo.type !== 'image/jpeg' || !photo.size) throw new Error('bad sampled frame');
            return { analyzed: true, accepted: false, stored: false, found: false,
              duplicate: false, observation: { assessment: 'undamaged' } };
          }
          if (path === '/api/drives' && (!options.method || options.method === 'GET')) return [];
          if (path === '/api/drives' && options.method === 'POST') return { ok: true };
          throw new Error(`unexpected decoder API path ${path}`);
        };
        IMPORT_VIDEO_MAX_SAMPLES = 2;
        const outcome = await analyseImportedVideos([file], { gpx: null, useCurrentLocation: false });
        return { skipped: false, frames, outcome, alerts };
      } finally {
        api = original.api; loadReports = original.loadReports;
        ensureDataConsent = original.ensureDataConsent;
        ensureVisionAvailable = original.ensureVisionAvailable;
        window.confirm = original.confirm; window.alert = original.alert;
      }
    }""")
    print("  real WebM import complete", flush=True)
    browser.close()


if remote_leaks:
    fails.append(f"real remote request escaped the deterministic test: {remote_leaks}")
ui = result["ui"]
if not ui["visible"] or not ui["multiple"]:
    fails.append(f"video import is not a visible multi-file action: {ui}")
for suffix in (".mp4", ".mov", ".webm", ".mkv", ".avi"):
    if suffix not in ui["accept"]:
        fails.append(f"video picker does not surface {suffix}: {ui['accept']}")
if ui["currentDefault"] or "no gps" not in ui["noGpsText"].lower():
    fails.append(f"import silently defaults to current GPS or does not disclose no-GPS: {ui}")
if "24 hours" not in ui["importHint"] or "24 hours" not in ui["privacyLocal"]:
    fails.append("direct Android Share temporary-copy retention is not disclosed in UI/privacy text")
if "H.264" not in result["codecMessage"] or "HEVC/H.265" not in result["codecMessage"]:
    fails.append(f"unsupported codec guidance is not actionable: {result['codecMessage']}")

mid = result["midpoint"]
if abs(mid["lat"] - 12.5) > 1e-6 or abs(mid["lng"] - 77.5) > 1e-6:
    fails.append(f"absolute GPX interpolation is wrong: {mid}")
if not result["doctypeRejected"]:
    fails.append("GPX parser accepted an XML doctype")
if not result["timezoneRejected"]:
    fails.append("GPX parser accepted timestamps without an explicit timezone")
if not all(result["collisionIdentity"].values()):
    fails.append(f"same-metadata clips collide or batch ordinals are missing: "
                 f"{result['collisionIdentity']}")
if result["parsedMovieTime"] != 1789034400000:
    fails.append(f"MP4 mvhd timestamp parser returned {result['parsedMovieTime']}")

if result["frameCount"] != 20 or result["completed"].get("checked") != 20:
    fails.append(f"long import did not pass the 15-frame boundary: {result['frameCount']}, {result['completed']}")
if not result["samePromise"]:
    fails.append("a duplicate import start did not join the active analysis job")
if result["positionCalls"]:
    fails.append(f"no-location import silently requested current GPS {result['positionCalls']} time(s)")
if result["maxDecoders"] != 1 or result["openCount"] != result["closeCount"]:
    fails.append("import did not keep exactly one decoder or leaked one: "
                 f"max={result['maxDecoders']} open/close={result['openCount']}/{result['closeCount']}")
if result["maxNetworkInFlight"] != 1:
    fails.append(f"import created an unbounded detector queue: {result['maxNetworkInFlight']}")
if not result["frames"] or any(frame["source"] != "imported_video" for frame in result["frames"]):
    fails.append(f"imported capture source was lost: {result['frames'][:3]}")
if any(frame["locationSource"] != "none" or frame["lat"] is not None or frame["lng"] is not None
       for frame in result["frames"]):
    fails.append("a no-location import attached coordinates or false provenance")
if len({frame["key"] for frame in result["frames"]}) != len(result["frames"]):
    fails.append("sampled imported frames do not have unique stable source keys")
if not any(frame["key"].startswith("import:clip-0-") for frame in result["frames"]) \
        or not any(frame["key"].startswith("import:clip-1-") for frame in result["frames"]):
    fails.append("browser observation identities omit the explicit clip batch ordinal")
if len({frame["drive"] for frame in result["frames"]}) != 1:
    fails.append("one segmented import did not share a stable drive id")
if not result["driveWrites"] or result["driveWrites"][0].get("capture_source") != "imported_video":
    fails.append(f"import drive identity was not persisted: {result['driveWrites']}")
if result["wakeRequests"] < 1 or result["wakeReleases"] < 1:
    fails.append(f"screen wake lock was not acquired/released: {result['wakeRequests']}/{result['wakeReleases']}")

if not result["backHandled"] or not result["homeImmediately"] or not result["homeAfterSettle"]:
    fails.append(f"Back did not detach/cancel the import cleanly: {result}")
if result["gatedCalls"] != 1 or not result["cancelled"].get("cancelled"):
    fails.append(f"cancel did not stop before the next sampled request: {result['gatedCalls']}, {result['cancelled']}")
if result["lateAlerts"]:
    fails.append(f"a cancelled import emitted stale UI after Back: {result['lateAlerts']}")
if result["terminalCalls"] != 1 or result["terminal"].get("failed") != 1:
    fails.append(f"terminal shared quota did not fail fast after one call: "
                 f"{result['terminalCalls']}, {result['terminal']}")
if not any("no further requests" in message for message in result["terminalAlerts"]):
    fails.append(f"terminal quota stop was not explained clearly: {result['terminalAlerts']}")

if not decoder.get("skipped") and (decoder.get("frames", 0) < 1
                                   or decoder.get("outcome", {}).get("error")):
    fails.append(f"real WebM import decoder failed: {decoder}")

print(f"  long import frames: {result['frameCount']} (max decoder/network {result['maxDecoders']}/{result['maxNetworkInFlight']})")
print(f"  wake lock request/release: {result['wakeRequests']}/{result['wakeReleases']}")
print(f"  real WebM sampled frames: {decoder.get('frames', 'skipped')}")
if fails:
    print("\nFAIL")
    for failure in fails:
        print("  -", failure)
    sys.exit(1)
print("\nVIDEO IMPORT TEST PASS")
