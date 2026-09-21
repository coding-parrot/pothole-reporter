import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import test from "node:test";

// A Query on a global secondary index is authorised against <table-arn>/index/<name>,
// not the table ARN. The role once listed only table ARNs, and GET /v1/map returned 500
// on every call for ten days while every unit test passed. This reads the template the
// way IAM does, so a new index without a grant fails here instead of in production.

const template = readFileSync(new URL("../template.yaml", import.meta.url), "utf8");
const serviceDir = new URL("../service/", import.meta.url);

function indexesByTable() {
  const tables = new Map();
  let current = null;
  for (const line of template.split("\n")) {
    const resource = line.match(/^ {2}([A-Za-z0-9]+):\s*$/);
    if (resource) current = resource[1];
    const index = line.match(/IndexName:\s*([A-Za-z0-9_-]+)/);
    if (index && current) {
      if (!tables.has(current)) tables.set(current, []);
      tables.get(current).push(index[1]);
    }
  }
  return tables;
}

function runtimePolicyResources() {
  const start = template.indexOf("Sid: ReadWriteCentralTables");
  assert.notEqual(start, -1, "the runtime policy statement ReadWriteCentralTables is missing");
  const end = template.indexOf("- Sid:", start + 1);
  return template.slice(start, end === -1 ? undefined : end);
}

test("every index the service queries is declared in the template", () => {
  const declared = new Set([...indexesByTable().values()].flat());
  const used = new Set();
  for (const file of readdirSync(serviceDir).filter((name) => name.endsWith(".mjs"))) {
    const source = readFileSync(new URL(file, serviceDir), "utf8");
    for (const match of source.matchAll(/IndexName:\s*["']([^"']+)["']/g)) used.add(match[1]);
  }
  assert.ok(used.size > 0, "expected the service to query at least one index");
  for (const name of used) assert.ok(declared.has(name), `service queries ${name}, which template.yaml does not declare`);
});

test("the runtime role may query every declared index", () => {
  const statement = runtimePolicyResources();
  assert.match(statement, /dynamodb:Query/);
  for (const [table, names] of indexesByTable()) {
    const all = statement.includes(`\${${table}.Arn}/index/*`);
    for (const name of names) {
      assert.ok(
        all || statement.includes(`\${${table}.Arn}/index/${name}`),
        `ReadWriteCentralTables grants ${table} but not its index ${name}; a Query on it is denied`,
      );
    }
  }
});
