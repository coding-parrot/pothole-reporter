import assert from "node:assert/strict";
import test from "node:test";

import { createDynamoRepository } from "../service/dynamo-repository.mjs";

// Every shared detection writes the same minute, day and month counters in one
// transaction, so two concurrent detections can cancel each other with
// TransactionConflict while every counter is far below its cap. That used to come back
// as shared_rate_limit, which the app treats as terminal and stops the drive on.

const limits = { perInstallDay: 50, globalMinute: 60, globalDay: 2000, globalMonth: 20000 };

function cancelled(...codes) {
  return Object.assign(new Error("Transaction cancelled"), {
    name: "TransactionCanceledException",
    CancellationReasons: codes.map((Code) => ({ Code })),
  });
}

function repositoryWith(transact, used = 3) {
  const sent = [];
  const client = {
    async send(command) {
      sent.push(command);
      const kind = command.constructor.name;
      if (kind === "TransactWriteCommand") return transact(sent.filter(
        (item) => item.constructor.name === "TransactWriteCommand").length);
      if (kind === "GetCommand") return { Item: { used } };
      return {};
    },
  };
  return {
    sent,
    repository: createDynamoRepository({ client, tables: { usage: "usage" }, quota: limits }),
  };
}

test("a transaction conflict is retried and then succeeds", async () => {
  const { repository } = repositoryWith((attempt) => {
    if (attempt === 1) throw cancelled("TransactionConflict", "None", "None", "None");
    return {};
  });
  assert.deepEqual(await repository.takeVisionQuota("abc"), { ok: true, limit: 50 });
});

test("a conflict that never clears is retryable, not a rate limit", async () => {
  const { repository, sent } = repositoryWith(() => {
    throw cancelled("TransactionConflict", "None", "None", "None");
  });
  const result = await repository.takeVisionQuota("abc");
  assert.equal(result.ok, false);
  assert.equal(result.code, "vision_counters_busy");
  assert.equal(result.retryable, true);
  assert.ok(sent.filter((item) => item.constructor.name === "TransactWriteCommand").length > 1);
  // The app stops a drive on any code naming credit, budget or quota.
  assert.doesNotMatch(result.code, /credit|budget|quota/i);
});

test("a counter at its cap is still reported by name", async () => {
  const { repository } = repositoryWith(() => {
    throw cancelled("ConditionalCheckFailed", "None", "None", "None");
  }, 60);
  const result = await repository.takeVisionQuota("abc");
  assert.equal(result.ok, false);
  assert.equal(result.code, "shared_rate_limit");
});

test("a refund decrements the four counters the detection took", async () => {
  const { repository, sent } = repositoryWith(() => ({}));
  const at = Date.parse("2026-09-21T10:15:30Z");
  await repository.refundVisionQuota("abc", at);
  const keys = sent.map((command) => command.input.Key.id).sort();
  assert.deepEqual(keys, [
    "day#2026-09-21", "install#abc#2026-09-21", "minute#2026-09-21T10:15", "month#2026-09",
  ]);
  for (const command of sent) {
    assert.equal(command.input.ExpressionAttributeValues[":minusOne"], -1);
    assert.match(command.input.ConditionExpression, /used > :zero/);
  }
});
