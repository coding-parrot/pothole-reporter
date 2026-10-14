#!/usr/bin/env node
// Send a notification to every phone running 1.43.0 or later.
//
//   AWS_PROFILE=pothole node tools/send-announcement.mjs --title "..." --body "..." [--days 7] [--dry-run]
//
// The phones ask GET /v1/messages about once an hour (MessageWorker in the app) and show
// each new message as a notification, so it arrives within about an hour, sooner on a
// phone that opens the app. A message stops being sent after --days (default 7); a phone
// installed after it was written never sees it. The newest twenty are kept in one row of
// the control table, which has no expiry of its own.

import { pathToFileURL } from "node:url";

export const ANNOUNCEMENTS_ID = "ANNOUNCEMENTS";
const KEEP = 20;
const TITLE_MAX = 80;
const BODY_MAX = 300;

export function addAnnouncement(row, { title, body, now, days }) {
  title = String(title || "").trim();
  body = String(body || "").trim();
  if (!title || title.length > TITLE_MAX) throw new Error(`title must be 1 to ${TITLE_MAX} characters`);
  if (!body || body.length > BODY_MAX) throw new Error(`body must be 1 to ${BODY_MAX} characters`);
  if (!(days > 0 && days <= 30)) throw new Error("days must be between 1 and 30");
  const message = {
    id: String(now), title, body, created_at: now,
    expires_at: Math.floor((now + days * 86_400_000) / 1000),
  };
  const messages = [...((row && row.messages) || []), message].slice(-KEEP);
  return { id: ANNOUNCEMENTS_ID, messages };
}

function argument(name, fallback) {
  const at = process.argv.indexOf(`--${name}`);
  return at > 0 ? process.argv[at + 1] : fallback;
}

async function main() {
  const { DynamoDBClient } = await import("@aws-sdk/client-dynamodb");
  const { DynamoDBDocumentClient, GetCommand, PutCommand } = await import("@aws-sdk/lib-dynamodb");
  const table = argument("table", "pothole-reporter-central-control");
  const client = DynamoDBDocumentClient.from(new DynamoDBClient({ region: process.env.AWS_REGION || "ap-south-1" }));
  const current = (await client.send(new GetCommand({ TableName: table, Key: { id: ANNOUNCEMENTS_ID } }))).Item;
  const next = addAnnouncement(current, {
    title: argument("title"), body: argument("body"), now: Date.now(), days: Number(argument("days", "7")),
  });
  const message = next.messages.at(-1);
  console.log(`${process.argv.includes("--dry-run") ? "would send" : "sending"}: "${message.title}" / "${message.body}"`
    + ` until ${new Date(message.expires_at * 1000).toISOString()}`);
  if (process.argv.includes("--dry-run")) return;
  await client.send(new PutCommand({ TableName: table, Item: next }));
  console.log(`stored; ${next.messages.length} message(s) kept. Phones show it within about an hour.`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error) => { console.error(error.message); process.exit(1); });
}
