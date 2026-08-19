// A D1-shaped wrapper over node:sqlite, so the tests run the worker's real SQL against
// the real schema.sql rather than a mock that agrees with whatever the code does.
import { DatabaseSync } from "node:sqlite";
import { readFileSync } from "node:fs";

export function makeD1(schemaPath = new URL("../schema.sql", import.meta.url)) {
  const db = new DatabaseSync(":memory:");
  db.exec(readFileSync(schemaPath, "utf8"));
  const conv = (sql) => sql.replace(/\?(\d+)/g, "?");   // D1 allows ?1, node:sqlite wants ?
  return {
    db,
    prepare(sql) {
      const text = conv(sql);
      let args = [];
      const api = {
        bind(...a) { args = a.map((v) => (v === undefined ? null : v)); return api; },
        async all() { return { results: db.prepare(text).all(...args) }; },
        async first() { return db.prepare(text).get(...args) ?? null; },
        async run() {
          const r = db.prepare(text).run(...args);
          return { meta: { changes: Number(r.changes), last_row_id: Number(r.lastInsertRowid) } };
        },
      };
      return api;
    },
  };
}
