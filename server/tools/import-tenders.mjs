// Load the contract table into D1 from the bundled JSON, then the 9.5 MB file can leave
// the APK. Run once, and again whenever tools/pull-kppp.py refreshes the data:
//   node tools/import-tenders.mjs ../data/tenders-karnataka.json > /tmp/tenders.sql
//   wrangler d1 execute pothole --file=/tmp/tenders.sql --remote
import { readFileSync } from "node:fs";

const src = process.argv[2] || "../data/tenders-karnataka.json";
const rows = JSON.parse(readFileSync(src, "utf8"));
const q = (v) => (v === null || v === undefined ? "NULL" : `'${String(v).replace(/'/g, "''")}'`);

// Only rows a municipal officer could be asked to enforce are worth shipping: the rest
// are never citable, and they are two thirds of the file.
const keep = rows.filter((r) => r.b);
console.log("BEGIN TRANSACTION;");
console.log("DELETE FROM tenders;");
for (let i = 0; i < keep.length; i += 500) {
  const chunk = keep.slice(i, i + 500).map((r) =>
    `(${q(r.tn)},${q(r.t)},${q(r.loc)},${q(r.d)},${q(r.c)},${q(r.b)})`).join(",\n");
  console.log(`INSERT OR REPLACE INTO tenders (tn,title,loc,published,contractor,body_lgd) VALUES\n${chunk};`);
}
console.log("COMMIT;");
console.error(`${keep.length} citable contracts of ${rows.length} total`);
