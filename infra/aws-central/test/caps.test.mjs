import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

// The live stack runs 10000 detections per install per day (50 until 21 Sept 2026, 500
// until 6 Oct 2026, when the cap was hit 74 times and the owner ordered it raised, 2000
// until 7 Oct 2026, when one phone on a long drive used all 2000 and was refused), 1500 a
// minute (60 until 6 Oct, hit 10 times; 300 until 7 Oct, when two phones alone reached
// 118), 100000 a day and 1000000 a month. The day and the month were 30000 and 200000 until
// 7 Oct 2026: 30000 is about 8.5 hours of Drive Mode across every user, and the owner set
// the day at what USD 50 buys (a detection measured USD 0.00051: USD 11.51 for 22,480
// calls). The month is ten such days, so two busy days cannot close the month. The template
// defaults and the handler's fallbacks once disagreed with the stack, so a fresh stack or
// a missing variable quietly changed the spend. Raising a cap is a decision made here,
// in the stack parameters and in the fallbacks together, never by a stray default.

const deployed = {
  DailyVisionCap: ["DAILY_VISION_CAP", "perInstallDay", 10000],
  GlobalVisionMinuteCap: ["GLOBAL_VISION_MINUTE_CAP", "globalMinute", 1500],
  GlobalVisionDailyCap: ["GLOBAL_VISION_DAILY_CAP", "globalDay", 100_000],
  MonthlyVisionCap: ["MONTHLY_VISION_CAP", "globalMonth", 1_000_000],
};

const template = readFileSync(new URL("../template.yaml", import.meta.url), "utf8");
const handler = readFileSync(new URL("../service/handler.mjs", import.meta.url), "utf8");
const repository = readFileSync(
  new URL("../service/dynamo-repository.mjs", import.meta.url), "utf8");
const number = (text) => Number(text.replaceAll("_", ""));

for (const [parameter, [variable, option, cap]] of Object.entries(deployed)) {
  test(`${parameter} defaults to the deployed ${cap}`, () => {
    const declared = template.match(
      new RegExp(`\\n  ${parameter}:\\n    Type: Number\\n    Default: (\\d+)`));
    assert.ok(declared, `${parameter} is not declared with a default`);
    assert.equal(Number(declared[1]), cap);
    const fallback = handler.match(
      new RegExp(`process\\.env\\.${variable} \\|\\| ([\\d_]+)`));
    assert.ok(fallback, `handler.mjs has no fallback for ${variable}`);
    assert.equal(number(fallback[1]), cap);
    const unset = repository.match(new RegExp(`quota\\.${option} \\?\\? ([\\d_]+)`));
    assert.ok(unset, `dynamo-repository.mjs has no default for ${option}`);
    assert.equal(number(unset[1]), cap);
  });
}
