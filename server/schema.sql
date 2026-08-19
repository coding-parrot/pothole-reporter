-- Cloudflare D1. Applied with:
--   wrangler d1 execute pothole --file=schema.sql --remote
--
-- What is deliberately NOT here: no name, no phone, no email, no account. A row is a
-- road defect and the pseudonymous install that saw it. device_id is never returned by
-- any read endpoint, because a device's reports over time are a movement trace even
-- though a single one is not.

CREATE TABLE IF NOT EXISTS reports (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  lat          REAL    NOT NULL,
  lng          REAL    NOT NULL,
  size         TEXT,                      -- small | medium | large
  confidence   REAL,
  image_hash   TEXT    NOT NULL,          -- SHA-256 of the frame that produced it
  device_id    TEXT    NOT NULL,          -- pseudonymous, never exposed by a read
  lgd          TEXT,                      -- LGD code of the owning body, from the app
  town         TEXT,
  created_at   INTEGER NOT NULL,          -- epoch ms
  seen_count   INTEGER NOT NULL DEFAULT 1 -- how many separate installs reported it
);

-- Dedup and the city dashboard are both bounding-box reads, so the index is on the box.
CREATE INDEX IF NOT EXISTS reports_box  ON reports (lat, lng);
CREATE INDEX IF NOT EXISTS reports_lgd  ON reports (lgd, created_at);
-- One install reporting the same frame twice is a client retry, not a second pothole.
CREATE UNIQUE INDEX IF NOT EXISTS reports_once ON reports (device_id, image_hash);

-- Which installs have confirmed a given report. Kept separate so seen_count cannot be
-- inflated by one device, and so a device's history is never a single scannable column.
CREATE TABLE IF NOT EXISTS confirmations (
  report_id  INTEGER NOT NULL,
  device_id  TEXT    NOT NULL,
  created_at INTEGER NOT NULL,
  PRIMARY KEY (report_id, device_id)
);

-- Road-work contracts, moved off the phone. This is what let the APK drop 9.5 MB, and
-- it means refreshing the contract data no longer needs an app release.
CREATE TABLE IF NOT EXISTS tenders (
  tn         TEXT PRIMARY KEY,
  title      TEXT,
  loc        TEXT,
  published  TEXT,
  contractor TEXT,
  body_lgd   TEXT              -- NULL when the awarding body has no published address
);
CREATE INDEX IF NOT EXISTS tenders_body ON tenders (body_lgd);
