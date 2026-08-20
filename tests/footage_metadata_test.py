# -*- coding: utf-8 -*-
"""Recorded-footage identity and timeline metadata survive storage and VOD analysis.

No external service is contacted.  One real local WebM clip is stored twice with a
missing sequence number and a deliberate 30-second recorder gap.  The detector call is
replaced at the page API boundary so the test can inspect the exact FormData produced by
``analyseFootage`` without calling a model.
"""
import pathlib
import sys

from playwright.sync_api import sync_playwright


APP = "http://localhost:8765/"
fails = []
remote_leaks = []


with sync_playwright() as p:
    browser = p.chromium.launch(args=[
        "--disable-web-security", "--allow-running-insecure-content",
        "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
    ])
    context = browser.new_context(viewport={"width": 390, "height": 844})

    def block_real_remote(route):
        url = route.request.url
        if url.startswith(APP) or url.startswith("blob:") or url.startswith("data:"):
            route.continue_()
        else:
            remote_leaks.append(url)
            route.abort()

    context.route("**/*", block_real_remote)
    page = context.new_page()
    page.goto(APP)
    page.wait_for_load_state("networkidle")
    page.wait_for_function("typeof StandaloneAPI !== 'undefined'", timeout=30000)

    result = page.evaluate("""async () => {
      await StandaloneAPI.handle("/api/reports", { method: "DELETE" });
      localStorage.setItem("debug_mode", "1"); // retain the two clips after analysis
      localStorage.removeItem("keep_frames");

      const stream = await navigator.mediaDevices.getUserMedia({
        video: { width: 320, height: 240 }, audio: false,
      });
      const mime = ["video/webm;codecs=vp8", "video/webm"]
        .find((type) => MediaRecorder.isTypeSupported(type));
      if (!mime) throw new Error("Chromium exposes no WebM MediaRecorder for the test");
      const recorder = new MediaRecorder(stream, { mimeType: mime });
      const parts = [];
      recorder.ondataavailable = (event) => {
        if (event.data && event.data.size) parts.push(event.data);
      };
      recorder.start();
      await new Promise((resolve) => setTimeout(resolve, 1200));
      await new Promise((resolve) => { recorder.onstop = resolve; recorder.stop(); });
      stream.getTracks().forEach((track) => track.stop());
      const blob = new Blob(parts, { type: mime });
      if (!blob.size) throw new Error("MediaRecorder produced an empty regression clip");

      const driveId = "metadata-gap-drive";
      const base = 1800000000000;
      async function storeClip(seq, recordingStartedAtMs, sourceOffsetMs) {
        const fd = new FormData();
        fd.append("segment", blob, `clip-${seq}.webm`);
        fd.append("drive_id", driveId);
        fd.append("seq", String(seq));
        fd.append("recording_started_at_ms", String(recordingStartedAtMs));
        fd.append("source_offset_ms", String(sourceOffsetMs));
        await StandaloneAPI.handle("/api/footage", { method: "POST", body: fd });
      }

      // Sequence 1 is deliberately absent.  The second surviving clip began 30 seconds
      // after the first, much later than the duration of the reused short WebM blob.
      await storeClip(0, base + 2000, 2000);
      await storeClip(2, base + 32000, 32000);
      const stored = await StandaloneAPI.handle(`/api/footage/${driveId}/blobs`);
      const metadata = (stored.clips || []).map((clip) => ({
        seq: clip.seq,
        recording_started_at_ms: clip.recording_started_at_ms,
        source_offset_s: clip.source_offset_s,
        bytes: clip.blob && clip.blob.size,
      }));

      const originalApi = window.api;
      const frames = [];
      window.api = async (path, opts) => {
        if (path === "/api/frame") {
          const fd = opts.body;
          const row = {};
          for (const key of ["drive_id", "capture_source", "source_event_key",
                             "source_offset_ms", "captured_at_ms", "lat", "lng",
                             "gps_accuracy", "speed", "heading"]) {
            row[key] = fd.get(key);
          }
          frames.push(row);
          const observation = {
            reportable: false, assessment: "absent", image_quality: "usable",
            damage_type: "none", on_drivable_surface: true,
            has_broken_edge_or_rim: false, has_depth_or_surface_loss: false,
            temporal_consistency: "consistent", size: null, description: "No damage.",
          };
          return {
            analyzed: true, accepted: false, stored: false, found: false,
            duplicate: false, duplicate_of: null, decision: "reject", review: false,
            ...observation, observation,
            detector: { model: "test", detail: "high", prompt_version: "test" },
          };
        }
        return originalApi(path, opts);
      };
      window.alert = (message) => { window.__metadataAlert = String(message); };
      window.confirm = () => false;
      VOD_STEP_S = 10; // exactly one sample from each short clip
      await analyseFootage(driveId, { started_at: base / 1000, gps_track: [
        [0, 12.900001, 77.600001, 5, 8, 90],
        [30, 13.100001, 77.800001, 6, 7, 95],
      ] });
      window.api = originalApi;

      // History must retain the unique union, not max(2 live, 2 VOD) == 2.
      await StandaloneAPI.handle("/api/drives", { method: "POST", body: JSON.stringify({
        id: "known-union", started_at: base / 1000, checked: 2, found: 0,
        already: 2, already_ids: ["A", "B"], gps_track: [],
      }) });
      await StandaloneAPI.handle("/api/drives/known-union/analysis", {
        method: "POST", body: JSON.stringify({
          checked: 2, found: 0, already: 2, already_ids: ["B", "C"],
        }),
      });
      const unionDrive = (await StandaloneAPI.handle("/api/drives"))
        .find((drive) => drive.id === "known-union");
      return {
        blob_count: stored.blobs.length,
        metadata,
        frames,
        union_ids: unionDrive && unionDrive.already_ids,
        union_count: unionDrive && unionDrive.already,
        message: window.__metadataAlert || "",
      };
    }""")
    browser.close()


if result["blob_count"] != 2:
    fails.append(f"legacy blobs compatibility returned {result['blob_count']} clips, expected 2")

metadata = result["metadata"]
if [item.get("seq") for item in metadata] != [0, 2]:
    fails.append(f"stored clip sequence was compacted or reordered: {metadata}")
if [item.get("recording_started_at_ms") for item in metadata] != [
        1800000002000, 1800000032000]:
    fails.append(f"recording start timestamps did not round-trip: {metadata}")
if [item.get("source_offset_s") for item in metadata] != [2, 32]:
    fails.append(f"source offsets did not round-trip in seconds: {metadata}")
if any(not item.get("bytes") for item in metadata):
    fails.append(f"clip metadata lost its Blob payload: {metadata}")

if result.get("union_ids") != ["A", "B", "C"] or result.get("union_count") != 3:
    fails.append("live/VOD already-reported IDs did not merge to one unique union: "
                 f"{result.get('union_ids')}, count={result.get('union_count')}")

frames = result["frames"]
if len(frames) != 2:
    fails.append(f"expected one VOD sample from each stored clip, got {len(frames)}: {frames}")
else:
    by_seq = {}
    for frame in frames:
        key = frame.get("source_event_key") or ""
        parts = key.split(":")
        if len(parts) < 4 or not key.startswith("vod:metadata-gap-drive:"):
            fails.append(f"VOD source key has an unexpected shape: {key!r}")
            continue
        try:
            seq, at_ms = int(parts[-2]), int(parts[-1])
        except ValueError:
            fails.append(f"VOD source key lacks numeric sequence/time: {key!r}")
            continue
        by_seq[seq] = (frame, at_ms)

    if sorted(by_seq) != [0, 2]:
        fails.append(f"VOD used compacted blob indexes instead of stored sequence IDs: {frames}")
    expected = {
        0: {"offset": 2000, "start": 1800000002000,
            "lat": "12.900001", "lng": "77.600001", "accuracy": "5", "speed": "8", "heading": "90"},
        2: {"offset": 32000, "start": 1800000032000,
            "lat": "13.100001", "lng": "77.800001", "accuracy": "6", "speed": "7", "heading": "95"},
    }
    for seq, want in expected.items():
        if seq not in by_seq:
            continue
        frame, at_ms = by_seq[seq]
        if frame.get("drive_id") != "metadata-gap-drive" or frame.get("capture_source") != "drive_vod":
            fails.append(f"clip {seq} lost its Drive/VOD identity: {frame}")
        if int(frame.get("source_offset_ms") or -1) != want["offset"] + at_ms:
            fails.append(f"clip {seq} used compacted duration instead of real source offset: {frame}")
        if int(frame.get("captured_at_ms") or -1) != want["start"] + at_ms:
            fails.append(f"clip {seq} did not derive capture time from its recorder start: {frame}")
        for field, value in (("lat", want["lat"]), ("lng", want["lng"]),
                             ("gps_accuracy", want["accuracy"]), ("speed", want["speed"]),
                             ("heading", want["heading"])):
            if frame.get(field) != value:
                fails.append(f"clip {seq} has wrong GPS {field}: {frame.get(field)!r}, expected {value!r}")

if remote_leaks:
    fails.append("real remote request escaped the deterministic test: " + ", ".join(remote_leaks[:3]))

print(f"  stored sequences : {[item.get('seq') for item in metadata]}")
print(f"  analysed keys    : {[frame.get('source_event_key') for frame in frames]}")
if fails:
    print("\nFAIL")
    for failure in fails:
        print("  -", failure)
    sys.exit(1)
print("\nFOOTAGE METADATA TEST PASS")
