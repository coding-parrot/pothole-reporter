import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

// One emulator drive keeps MAX_IN_FLIGHT frames at the detector at once. With four
// reserved Lambda slots the fifth request was throttled, API Gateway answered a bare 503,
// and the app ended the drive with "The project server had a problem." Capacity has to
// hold a few simultaneous drivers plus the health checks around them. Spend is bounded by
// the detection caps, not by concurrency, so these numbers cost nothing extra.
//
// Sized for 3 drivers until 7 Oct 2026. On 6 Oct two phones in Drive Mode made 118
// detections in one minute between them, so the 300 a minute cap, 20 reserved slots and
// 20 requests a second would all have refused the sixth simultaneous driver, the day
// before the app was shown in public. Sized for 25 since.

const template = readFileSync(new URL("../template.yaml", import.meta.url), "utf8");
const client = readFileSync(new URL("../../../static/index.html", import.meta.url), "utf8");
const DRIVERS = 25;
// What one phone in Drive Mode was measured to send: 118 a minute from two phones.
const DETECTIONS_PER_DRIVER_MINUTE = 60;

function number(pattern, source, label) {
  const match = source.match(pattern);
  assert.ok(match, `${label} not found`);
  return Number(match[1]);
}

test("reserved concurrency holds several drivers at the client's in-flight limit", () => {
  const inFlight = number(/const MAX_IN_FLIGHT = (\d+);/, client, "MAX_IN_FLIGHT in static/index.html");
  const reserved = number(/ReservedConcurrency:\s*\n\s*Type: Number\s*\n\s*Default: (\d+)/, template,
    "ReservedConcurrency default");
  assert.ok(reserved >= DRIVERS * inFlight + 2,
    `ReservedConcurrency ${reserved} throttles ${DRIVERS} drivers at ${inFlight} frames in flight`);
});

test("the stage throttle holds several drivers' bursts", () => {
  const inFlight = number(/const MAX_IN_FLIGHT = (\d+);/, client, "MAX_IN_FLIGHT in static/index.html");
  const burst = number(/ThrottlingBurstLimit: (\d+)/, template, "ThrottlingBurstLimit");
  const rate = number(/ThrottlingRateLimit: (\d+)/, template, "ThrottlingRateLimit");
  assert.ok(burst >= DRIVERS * inFlight * 2, `burst ${burst} is below ${DRIVERS} drivers' bursts`);
  assert.ok(rate >= DRIVERS * inFlight, `rate ${rate} per second is below ${DRIVERS} drivers`);
});

test("the shared per-minute detection cap holds that many drivers", () => {
  const minute = number(/GlobalVisionMinuteCap:\s*\n\s*Type: Number\s*\n\s*Default: (\d+)/, template,
    "GlobalVisionMinuteCap default");
  assert.ok(minute >= DRIVERS * DETECTIONS_PER_DRIVER_MINUTE,
    `GlobalVisionMinuteCap ${minute} refuses ${DRIVERS} drivers at ${DETECTIONS_PER_DRIVER_MINUTE} a minute each`);
});
