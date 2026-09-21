import assert from "node:assert/strict";
import test from "node:test";

import { DETECT_PROMPT_VERSION, DETECT_SCHEMA_VERSION } from "../../../llm/generated/contract.mjs";
import { createDynamoRepository } from "../service/dynamo-repository.mjs";
import { harness, memoryRepository } from "./support.mjs";

// The phone queues reports while offline and sends them later, so observations reach
// the server out of order and sometimes twice. last_seen_at orders the public map: an
// old observation must not pull a pothole back in time, a phone whose clock runs ahead
// must not pin one to the top for good, and a re-send is not a second observation.

// Just enough of DynamoDB for attachObservation: conditional Put, ADD and SET updates,
// and the comparisons the repository writes.
function memoryDynamo() {
  const tables = new Map();
  const table = (name) => {
    if (!tables.has(name)) tables.set(name, new Map());
    return tables.get(name);
  };
  const keyOf = (key) => JSON.stringify(Object.entries(key).sort());
  const holds = (condition, item, values) => {
    if (!condition) return true;
    return condition.split(" AND ").every((part) => {
      const exists = part.match(/^attribute_(not_)?exists\((\w+)\)$/);
      if (exists) return (item?.[exists[2]] !== undefined) === !exists[1];
      const compare = part.match(/^(\w+) ([<>]) (:\w+)$/);
      if (!compare) throw new Error(`fake cannot evaluate ${part}`);
      const current = item?.[compare[1]];
      if (current === undefined) return false;
      return compare[2] === "<" ? current < values[compare[3]] : current > values[compare[3]];
    });
  };
  const applyUpdate = (item, expression, values) => {
    const next = { ...item };
    for (const clause of expression.split(/ (?=SET |ADD )/)) {
      const [verb, rest] = [clause.slice(0, 3), clause.slice(4)];
      for (const piece of rest.split(",").map((text) => text.trim())) {
        if (verb === "SET") {
          const [name, value] = piece.split("=");
          next[name] = values[value];
        } else {
          const [name, value] = piece.split(" ");
          next[name] = (next[name] || 0) + values[value];
        }
      }
    }
    return next;
  };
  const failed = () => Object.assign(new Error("condition failed"),
    { name: "ConditionalCheckFailedException" });
  const plan = (operation) => {
    if (operation.Put) {
      const { TableName, Item, ConditionExpression } = operation.Put;
      const key = keyOf(Item.pk ? { pk: Item.pk, sk: Item.sk } : { id: Item.id });
      const ok = holds(ConditionExpression, table(TableName).get(key), {});
      return { ok, apply: () => table(TableName).set(key, Item) };
    }
    const { TableName, Key, UpdateExpression, ConditionExpression,
      ExpressionAttributeValues: values = {} } = operation.Update;
    const current = table(TableName).get(keyOf(Key));
    return {
      ok: holds(ConditionExpression, current, values),
      apply: () => table(TableName).set(keyOf(Key),
        applyUpdate({ ...Key, ...current }, UpdateExpression, values)),
    };
  };
  return {
    get: (name, key) => table(name).get(keyOf(key)),
    put: (name, key, item) => table(name).set(keyOf(key), { ...key, ...item }),
    async send(command) {
      const kind = command.constructor.name;
      const input = command.input;
      if (kind === "GetCommand") return { Item: table(input.TableName).get(keyOf(input.Key)) };
      if (kind === "UpdateCommand") {
        const step = plan({ Update: input });
        if (!step.ok) throw failed();
        step.apply();
        return {};
      }
      if (kind === "TransactWriteCommand") {
        const steps = input.TransactItems.map(plan);
        if (steps.some((step) => !step.ok)) {
          throw Object.assign(new Error("cancelled"), {
            name: "TransactionCanceledException",
            CancellationReasons: steps.map((step) => ({
              Code: step.ok ? "None" : "ConditionalCheckFailed",
            })),
          });
        }
        steps.forEach((step) => step.apply());
        return {};
      }
      throw new Error(`fake does not handle ${kind}`);
    },
  };
}

function seeded(firstSeen, lastSeen) {
  const dynamo = memoryDynamo();
  dynamo.put("potholes", { id: 7 }, {
    first_seen_at: firstSeen, last_seen_at: lastSeen, observation_count: 1, complaint_count: 1,
  });
  const repository = createDynamoRepository({
    client: dynamo,
    tables: { potholes: "potholes", records: "records", control: "control" },
  });
  const observe = (installId, observationId, observedAt) => repository.attachObservation({
    potholeId: 7,
    observation: {
      install_id: installId,
      client_observation_id: observationId,
      observed_at: observedAt,
    },
  });
  return { dynamo, observe, pothole: () => dynamo.get("potholes", { id: 7 }) };
}

test("a late observation does not move last_seen_at backwards", async () => {
  const h = seeded(1_000, 5_000);
  await h.observe("second", "late", 3_000);
  assert.equal(h.pothole().last_seen_at, 5_000);
  assert.equal(h.pothole().first_seen_at, 1_000);
  assert.equal(h.pothole().observation_count, 2);
  assert.equal(h.pothole().complaint_count, 2);
});

test("an observation older than the first one moves first_seen_at back", async () => {
  const h = seeded(1_000, 5_000);
  await h.observe("second", "older", 500);
  assert.equal(h.pothole().first_seen_at, 500);
  assert.equal(h.pothole().last_seen_at, 5_000);
});

test("a newer observation still moves last_seen_at forward", async () => {
  const h = seeded(1_000, 5_000);
  await h.observe("second", "newer", 9_000);
  await h.observe("second", "newest", 12_000);
  assert.equal(h.pothole().last_seen_at, 12_000);
  assert.equal(h.pothole().observation_count, 3);
  assert.equal(h.pothole().complaint_count, 2, "one new observer, however many reports");
});

test("a re-sent observation is stored once", async () => {
  const h = seeded(1_000, 5_000);
  assert.deepEqual(await h.observe("second", "same", 6_000), { newObserver: true });
  const again = await h.observe("second", "same", 6_000);
  assert.equal(again.alreadyStored, true);
  assert.equal(h.pothole().observation_count, 2);
});

// The report route, end to end with the service's own validation.

function reportRepository({ alreadyStored = false } = {}) {
  const repository = memoryRepository();
  const seen = { observations: [], reports: [] };
  const pothole = { id: 7, lat: 12.9716, lng: 77.5946, damage_type: "pothole_cavity",
    first_seen_at: 1, last_seen_at: 1, observation_count: 1, complaint_count: 1,
    server_verified_count: 0, map_shard: "07" };
  Object.assign(repository, {
    seen,
    async acquireLocationLocks() { return true; },
    async releaseLocationLocks() {},
    async findNearby() { return [pothole]; },
    async attachObservation({ observation }) {
      seen.observations.push(observation);
      return alreadyStored ? { alreadyStored: true, newObserver: false } : { newObserver: false };
    },
    async getPothole() { return pothole; },
    async recordReport(value) { seen.reports.push(value); },
  });
  return repository;
}

const geolocator = {
  async resolve({ lat, lng }) {
    return { lat, lng, road_ownership: "municipal", source: "kgis", lgd: "1", town: "T" };
  },
};

const reportBody = (observedAt) => ({
  client_observation_id: "obs-1",
  observed_at: observedAt,
  lat: 12.97161,
  lng: 77.59461,
  damage_type: "pothole_cavity",
  size: null,
  image_hash: "a".repeat(64),
  capture_source: "manual",
  location_source: "device_gps",
  detector: {
    provider: "own_key",
    prompt_version: DETECT_PROMPT_VERSION,
    schema_version: DETECT_SCHEMA_VERSION,
  },
});

test("an observation from a clock running ahead is stamped with the server's time", async () => {
  const repository = reportRepository();
  const h = await harness({ repository, geolocator });
  const before = Date.now();
  const result = await h.post("/v1/potholes/report",
    reportBody(before + 365 * 86_400_000));
  assert.equal(result.statusCode, 200, result.body);
  const stored = repository.seen.observations[0].observed_at;
  assert.ok(stored >= before && stored <= Date.now(), `stored ${stored}`);
});

test("an observation a few minutes ahead keeps its own time", async () => {
  const repository = reportRepository();
  const h = await harness({ repository, geolocator });
  const observedAt = Date.now() + 2 * 60_000;
  await h.post("/v1/potholes/report", reportBody(observedAt));
  assert.equal(repository.seen.observations[0].observed_at, observedAt);
});

test("a re-sent observation is not counted again in the impact totals", async () => {
  const repository = reportRepository({ alreadyStored: true });
  const h = await harness({ repository, geolocator });
  const result = await h.post("/v1/potholes/report", reportBody(Date.now() - 60_000));
  assert.equal(result.statusCode, 200, result.body);
  assert.equal(JSON.parse(result.body).duplicate, true);
  assert.equal(repository.seen.reports.length, 0);
});

test("a report past 75 degrees is refused before it takes a single lock", async () => {
  const repository = reportRepository();
  let locked = 0;
  repository.acquireLocationLocks = async () => { locked += 1; return true; };
  const h = await harness({ repository, geolocator });
  const result = await h.post("/v1/potholes/report",
    { ...reportBody(Date.now()), lat: 78.2232, lng: 15.6267 });
  assert.equal(result.statusCode, 400);
  assert.equal(JSON.parse(result.body).error, "bad_report");
  assert.equal(locked, 0);
});

// The app asked /v1/tenders/resolve and then /v1/potholes/report in series, and the
// server resolved KGIS for both. The report now carries the same routing answer.

test("a report carries the routing answer the resolve route would give", async () => {
  const repository = reportRepository();
  repository.queryTenders = async () => [];
  let resolved = 0;
  const counting = { resolve: (input) => { resolved += 1; return geolocator.resolve(input); } };
  const h = await harness({ repository, geolocator: counting });
  const result = await h.post("/v1/potholes/report", reportBody(Date.now()));
  assert.equal(result.statusCode, 200, result.body);
  assert.equal(resolved, 1);
  const routing = JSON.parse(result.body).routing;
  const resolve = await h.post("/v1/tenders/resolve", { lat: 12.97161, lng: 77.59461 });
  const { request_id: _requestId, ...expected } = JSON.parse(resolve.body);
  assert.deepEqual(routing, expected);
  assert.equal(routing.reason, "no_tenders_for_jurisdiction");
});

test("a report whose road ownership is unknown still lands, without routing", async () => {
  const repository = reportRepository();
  const unknown = { async resolve() { return { road_ownership: "unknown", source: "unresolved" }; } };
  const h = await harness({ repository, geolocator: unknown });
  const result = await h.post("/v1/potholes/report", reportBody(Date.now()));
  assert.equal(result.statusCode, 200, result.body);
  assert.equal(JSON.parse(result.body).routing, null);
});
