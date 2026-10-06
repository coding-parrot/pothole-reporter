# -*- coding: utf-8 -*-
"""The phone side of the drive loop: how long a frame takes from capture to the detect
request leaving the phone, and how many frames the loop lets out at once.

A car tester wrote "very slow and the potholes were not detected in time". The server's
detect is measured separately (p50 1.8 s, p90 2.6 s); this suite times only what the
phone does before the request is sent: the preview grab, the wait for an in-flight slot,
and the engine's decode, resize, enhance, encode and sign. Every frame goes through the
real engine to the scripted central service; the detector answer is held back for the
production p50/p90 so slots stay busy for as long as they do on the road.

Measured on 6 October 2026 (Apple Silicon, desktop Chromium; a phone is slower by a
constant factor, the shape is the same): preview grab 10 to 12 ms, engine
decode/resize/enhance/encode/sign 13 to 15 ms, 26 to 28 ms from capture to request at
p50 and 31 to 38 ms at p90, at both 30 and 60 km/h. Capture never waits for an upload
and the encode never holds the next capture. The only wait is for one of the six
in-flight slots, at 60 km/h only, bounded by one server answer; see the gate below for
why the cap stays at six.

Run it on its own to print the numbers:
  node tools/harness/serve-docs.mjs &  .venv/bin/python tests/drive_capture_latency_test.py
"""

import hashlib
import json
import statistics
import sys
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

import flow_harness as fh
from web_drive_harness import open_web_drive

SECONDS = 20
# Production detect latency (p50 1.8 s, p90 2.6 s) plus the upload of a 300 KB frame on
# a 4G link, held back in the page so the request itself is timed the moment it is
# handed to fetch. Every fourth frame is accepted so the slot is also held through the
# tender lookup, the shared-map write and the IndexedDB commit.
HOLD = "((n % 5 === 0 ? 2600 : 1800) + 600)"
UNDAMAGED = {
    "image_quality": "acceptable", "assessment": "undamaged",
    "damage_type": None, "size": None, "description": "Plain asphalt.",
}


def service(route, request):
    path = urlparse(request.url).path
    if path != "/v1/vision/detect":
        return fh.central_service(route, request)
    body = json.loads(request.post_data or "{}")
    seq = int(hashlib.sha256(body.get("client_observation_id", "").encode()).hexdigest()[:2], 16)
    if seq % 4 == 0:
        return fh.central_service(route, request)
    fh.envelope(route, {
        **UNDAMAGED,
        "detector": {"provider": "shared_server", "model": body.get("model", "gpt-5-mini"),
                     "prompt_version": "road-damage-v5", "schema_version": 4,
                     "evidence_count": 1},
    })


# A car at the given speed, north along one street. The harness's own stub drives at
# 30 km/h; the loop is also timed at 60 km/h, where the cadence floor of 500 ms applies.
def geo(speed_mps):
    return """
(() => {
  const start = Date.now(), lat0 = 12.9716, lng0 = 77.5946, v = %s;
  const pos = () => { const t = (Date.now() - start) / 1000;
    return { coords: { latitude: lat0 + v * t / 111320, longitude: lng0, accuracy: 5,
      speed: v, heading: 0, altitude: null, altitudeAccuracy: null }, timestamp: Date.now() }; };
  const proto = Object.getPrototypeOf(navigator.geolocation);
  proto.watchPosition = function (ok) { ok(pos()); return setInterval(() => ok(pos()), 500); };
  proto.clearWatch = function (id) { clearInterval(id); };
  proto.getCurrentPosition = function (ok) { setTimeout(() => ok(pos()), 5); };
})();
""" % speed_mps


PROBE = r"""
() => {
  window.__lat = { frames: {}, sent: {}, inFlight: [], cap: MAX_IN_FLIGHT };
  const realEnqueue = enqueueDriveEvent;
  window.enqueueDriveEvent = (ctx, job) => {
    __lat.frames[`live:${ctx.sessionId}:${job.captureSeq}`] = {
      capturedAt: job.capturedAt, enqueuedAt: Date.now(),
      queued: ctx.queue.length, inFlight: ctx.inFlight,
    };
    return realEnqueue(ctx, job);
  };
  const realApi = window.api;
  window.api = async (path, opts) => {
    if (path === "/api/frame") {
      const frame = __lat.frames[opts.body.get("source_event_key")];
      if (frame) frame.drainedAt = Date.now();
    }
    return realApi(path, opts);
  };
  const realFetch = window.fetch;
  let n = 0;
  window.fetch = async function (input, init) {
    const url = String(input && input.url || input);
    if (!url.includes("/v1/vision/detect")) return realFetch.call(this, input, init);
    const id = JSON.parse(init.body).client_observation_id;
    __lat.sent[id] = Date.now();
    const hold = __HOLD__;
    const response = await realFetch.call(this, input, init);
    await new Promise((resolve) => setTimeout(resolve, hold));
    return response;
  };
  setInterval(() => {
    if (drive) __lat.inFlight.push([drive.inFlight, drive.queue.length]);
  }, 100);
}
""".replace("__HOLD__", HOLD.replace("n", "++n"))


def percentile(values, p):
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * p)))
    return ordered[index]


def measure(playwright, speed_mps):
    browser, page, dialogs, errors = open_web_drive(playwright, service=service, stub_frames=False)
    try:
        page.context.add_init_script(script=geo(speed_mps))
        page.reload()
        page.wait_for_function("() => !!window.StandaloneAPI", timeout=30_000)
        page.locator("#home").wait_for(state="visible", timeout=30_000)
        page.evaluate(PROBE)
        page.locator("#driveBtn").click()
        page.locator("#driveStop").wait_for(state="visible", timeout=30_000)
        page.wait_for_timeout(SECONDS * 1000)
        page.locator("#driveStop").click()
        page.locator("#home").wait_for(state="visible", timeout=60_000)
        page.wait_for_timeout(500)
        data = page.evaluate("() => window.__lat")
        return data, errors
    finally:
        browser.close()


def summarise(data):
    frames = []
    for key, frame in data["frames"].items():
        observation = "capture-" + hashlib.sha256(key.encode()).hexdigest()
        sent = data["sent"].get(observation)
        if not sent or "drainedAt" not in frame:
            continue
        frames.append({
            "grab": frame["enqueuedAt"] - frame["capturedAt"],
            "slot": frame["drainedAt"] - frame["enqueuedAt"],
            "prepare": sent - frame["drainedAt"],
            "total": sent - frame["capturedAt"],
            "queued": frame["queued"], "inFlight": frame["inFlight"],
        })
    in_flight = [sample[0] for sample in data["inFlight"]]
    queued = [sample[1] for sample in data["inFlight"]]
    stats = {"frames": len(frames), "captured": len(data["frames"]), "cap": data["cap"],
             "max_in_flight": max(in_flight or [0]),
             "at_cap_share": (sum(1 for v in in_flight if v >= data["cap"]) / len(in_flight))
             if in_flight else 0,
             "max_queued": max(queued or [0])}
    for name in ("grab", "slot", "prepare", "total"):
        values = [frame[name] for frame in frames]
        stats[name] = {"p50": percentile(values, .5), "p90": percentile(values, .9),
                       "max": max(values) if values else None}
    return stats


fails = []
with sync_playwright() as playwright:
    for label, speed in (("30 km/h", 8.33), ("60 km/h", 16.67)):
        data, errors = measure(playwright, speed)
        stats = summarise(data)
        print(f"{label}: {json.dumps(stats)}")
        if stats["frames"] < 8:
            fails.append(f"{label}: only {stats['frames']} frames went out in {SECONDS} s")
        # Once a frame has a slot, the phone's own work before the request leaves must
        # not take longer than a GPS fix stays fresh for; the road has moved on by then.
        if stats["prepare"]["p90"] is not None and stats["prepare"]["p90"] > 1500:
            fails.append(f"{label}: p90 prepare {stats['prepare']['p90']} ms before the request left")
        # The cap is sized by infra/aws-central/test/template-capacity.test.mjs: three
        # simultaneous drivers at MAX_IN_FLIGHT must fit the service's 20 reserved
        # executions and its 20 req/s, burst 40 gateway, so it stays at six until those
        # numbers move. Measured here at 60 km/h with six slots: at the cap 26% of the
        # time, a frame waited at most 230 ms (one server answer), p90 1 ms. Ten slots
        # took that to 2 ms, which is not worth letting two drivers starve a third. The
        # gate is that a wait stays a fraction of one server answer, never a queue.
        if (stats["slot"]["p90"] or 0) > 250 or (stats["slot"]["max"] or 0) > 1000:
            fails.append(f"{label}: frames queued behind the cap: p90 {stats['slot']['p90']} ms, "
                         f"max {stats['slot']['max']} ms, at cap {stats['at_cap_share']:.0%} "
                         f"of the time (cap {stats['cap']})")
        fails += [f"{label}: page error {e}" for e in errors[:3]]

if fails:
    print("FAIL drive capture latency")
    for failure in fails:
        print(" -", failure)
    sys.exit(1)
print("PASS drive capture latency")
