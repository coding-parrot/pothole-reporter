import assert from "node:assert/strict";
import test from "node:test";

import { createDynamoRepository } from "../service/dynamo-repository.mjs";

// DynamoDB applies Limit before FilterExpression and stops each page at Limit items or
// 1 MB. A map read that asked each shard for its newest `limit` rows and then filtered
// by bbox came back empty for a city whose potholes were older than everyone else's.

// A Query that behaves like DynamoDB's: newest first, Limit counts items read, the
// filter runs afterwards, and LastEvaluatedKey says there is more.
function shardedTable(rowsByShard, { pageCap = Infinity } = {}) {
  const reads = [];
  const client = {
    async send(command) {
      const input = command.input;
      const values = input.ExpressionAttributeValues;
      const rows = (rowsByShard[values[":shard"]] || [])
        .filter((row) => row.last_seen_at >= values[":since"])
        .sort((left, right) => right.last_seen_at - left.last_seen_at);
      const start = input.ExclusiveStartKey
        ? rows.findIndex((row) => row.id === input.ExclusiveStartKey.id) + 1 : 0;
      const read = rows.slice(start, start + Math.min(input.Limit ?? Infinity, pageCap));
      reads.push(read.length);
      const inBox = (row) => !input.FilterExpression || (
        row.lat >= values[":south"] && row.lat <= values[":north"]
        && row.lng >= values[":west"] && row.lng <= values[":east"]);
      const more = start + read.length < rows.length;
      return {
        Items: read.filter(inBox),
        ...(more ? { LastEvaluatedKey: { id: read.at(-1).id } } : {}),
      };
    },
  };
  return {
    reads,
    repository: createDynamoRepository({ client, tables: { potholes: "potholes" } }),
  };
}

const hubballi = [75.0, 15.2, 75.3, 15.5];

function crowdedShard() {
  const rows = [];
  for (let index = 0; index < 1_001; index += 1) {
    rows.push({ id: index + 10, lat: 12.97, lng: 77.59, last_seen_at: 10_000 + index });
  }
  rows.push({ id: 1, lat: 15.36, lng: 75.12, last_seen_at: 5_000 });
  return { "03": rows };
}

test("an older pothole inside the bbox is found behind newer ones outside it", async () => {
  const { repository } = shardedTable(crowdedShard());
  const found = await repository.listPotholes({ since: 0, bbox: hubballi, limit: 1_000 });
  assert.deepEqual(found.map((item) => item.id), [1]);
});

test("a shard that answers in 1 MB pages is read to the end", async () => {
  const { repository } = shardedTable(crowdedShard(), { pageCap: 300 });
  const found = await repository.listPotholes({ since: 0, bbox: hubballi, limit: 1_000 });
  assert.deepEqual(found.map((item) => item.id), [1]);
});

test("a map read stops paging once it has the rows it needs", async () => {
  const { repository, reads } = shardedTable(crowdedShard());
  const found = await repository.listPotholes({ since: 0, bbox: null, limit: 10 });
  assert.equal(found.length, 10);
  assert.equal(found[0].id, 1_010);
  assert.ok(reads.reduce((sum, count) => sum + count, 0) <= 16 * 10, `read ${reads}`);
});

test("impact counts a day whose metric rows run past one page", async () => {
  const rows = [];
  for (let index = 0; index < 250; index += 1) rows.push({ metric: `active#install-${index}` });
  rows.push({ metric: "summary", observations: 4, new_potholes: 3 });
  const client = {
    async send({ input }) {
      const start = input.ExclusiveStartKey?.at ?? 0;
      const page = rows.slice(start, start + 100);
      const next = start + page.length;
      return { Items: page, ...(next < rows.length ? { LastEvaluatedKey: { at: next } } : {}) };
    },
  };
  const repository = createDynamoRepository({ client, tables: { metrics: "metrics" } });
  const impact = await repository.impact({ from: "2026-09-21", to: "2026-09-21" });
  assert.equal(impact.activeInstallations, 250);
  assert.equal(impact.summary.observations, 4);
});
