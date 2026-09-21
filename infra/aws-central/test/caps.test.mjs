import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

// The live stack runs 50 detections per install per day, 60 a minute, 2000 a day and
// 20000 a month. The template defaults and the handler's fallbacks once said 200, 120,
// 5000 and 50000, so a fresh stack or a missing variable quietly quadrupled the spend.
// Raising a cap is a decision made in the stack parameters, never by a default.

const deployed = {
  DailyVisionCap: ["DAILY_VISION_CAP", "perInstallDay", 50],
  GlobalVisionMinuteCap: ["GLOBAL_VISION_MINUTE_CAP", "globalMinute", 60],
  GlobalVisionDailyCap: ["GLOBAL_VISION_DAILY_CAP", "globalDay", 2_000],
  MonthlyVisionCap: ["MONTHLY_VISION_CAP", "globalMonth", 20_000],
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
