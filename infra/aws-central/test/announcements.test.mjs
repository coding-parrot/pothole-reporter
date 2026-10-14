import assert from "node:assert/strict";
import test from "node:test";

import { createService } from "../service/core.mjs";
import { createDynamoRepository } from "../service/dynamo-repository.mjs";
import { addAnnouncement } from "../tools/send-announcement.mjs";

// The owner (14 Oct 2026): "Enable notification sending abilities at will." The phone
// asks GET /v1/messages about once an hour and shows what is new as a notification. A
// message is written by tools/send-announcement.mjs into one control row. The read is
// public and carries nothing about the phone: everyone gets the same answer.

function service(messages, now = 1_800_000_000_000) {
  const repository = {
    dedupeRadiusMetres: 30,
    async listAnnouncements() { return messages; },
    async recordRequest() {},
  };
  const unused = { async detect() { throw new Error("not used"); }, status() { return {}; },
    async lookup() { throw new Error("not used"); } };
  return createService({ repository, detector: unused, geolocator: unused, now: () => now,
    logger: { log() {}, error() {} } });
}

const get = (app, query = "") => app({
  rawPath: "/v1/messages",
  requestContext: { http: { method: "GET" } },
  queryStringParameters: query ? Object.fromEntries(new URLSearchParams(query)) : undefined,
  headers: {},
});

const MESSAGES = [
  { id: "a", title: "Old", body: "Sent long ago", created_at: 1_000, expires_at: 9_999_999_999 },
  { id: "b", title: "New", body: "Fresh", created_at: 2_000, expires_at: 9_999_999_999 },
  { id: "c", title: "Gone", body: "Expired", created_at: 3_000, expires_at: 1 },
];

test("a phone gets the messages written after the one it last saw, oldest first", async () => {
  const answer = await get(service(MESSAGES), "since=1500");
  assert.equal(answer.statusCode, 200);
  const body = JSON.parse(answer.body);
  assert.deepEqual(body.messages.map((message) => message.id), ["b"]);
  assert.deepEqual(Object.keys(body.messages[0]).sort(), ["body", "created_at", "id", "title"]);
  assert.equal(typeof body.now, "number");
});

test("an expired message is never sent", async () => {
  const body = JSON.parse((await get(service(MESSAGES), "since=0")).body);
  assert.deepEqual(body.messages.map((message) => message.id), ["a", "b"]);
});

test("a phone asking for the first time gets nothing old, only the server's clock", async () => {
  const body = JSON.parse((await get(service(MESSAGES))).body);
  assert.deepEqual(body.messages, []);
  assert.equal(body.now, 1_800_000_000_000);
});

test("a malformed cursor is refused", async () => {
  const answer = await get(service(MESSAGES), "since=yesterday");
  assert.equal(answer.statusCode, 400);
});

test("the answer may be cached for a minute, so a burst of phones costs one read", async () => {
  const answer = await get(service(MESSAGES), "since=0");
  const cache = Object.entries(answer.headers).find(([key]) => key.toLowerCase() === "cache-control");
  assert.match(cache[1], /max-age=60/);
});

test("the repository reads the one announcements row", async () => {
  const reads = [];
  const client = { async send(command) { reads.push(command.input); return { Item: { id: "ANNOUNCEMENTS", messages: MESSAGES } }; } };
  const repository = createDynamoRepository({ client, tables: { control: "control" } });
  assert.deepEqual(await repository.listAnnouncements(), MESSAGES);
  assert.deepEqual(reads[0].Key, { id: "ANNOUNCEMENTS" });
});

test("sending keeps the newest twenty, each with its own expiry", () => {
  let row = { id: "ANNOUNCEMENTS", messages: [] };
  for (let index = 0; index < 25; index += 1) {
    row = addAnnouncement(row, { title: `T${index}`, body: "B", now: 10_000 + index, days: 7 });
  }
  assert.equal(row.messages.length, 20);
  assert.equal(row.messages.at(-1).title, "T24");
  assert.equal(row.messages.at(-1).expires_at, Math.floor((10_024 + 7 * 86_400_000) / 1000));
  assert.throws(() => addAnnouncement(row, { title: "", body: "B", now: 1, days: 7 }), /title/);
  assert.throws(() => addAnnouncement(row, { title: "x".repeat(81), body: "B", now: 1, days: 7 }), /title/);
  assert.throws(() => addAnnouncement(row, { title: "T", body: "x".repeat(301), now: 1, days: 7 }), /body/);
});
