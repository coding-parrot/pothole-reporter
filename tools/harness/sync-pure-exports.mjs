#!/usr/bin/env node
// Keep __pure exporting every pure top-level helper the bundle defines.
//
//   node tools/harness/sync-pure-exports.mjs [--check]
//
// __pure is the test surface. When the merge truncated it, suites failed with
// "P.x is not a function" and looked like app bugs. Deriving the list from the file
// means a helper can never be defined but unreachable from a test again.
//
// Functions and const bindings only: a `let` would be exported by value and would lie
// about state that changes later.

import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

import * as espree from "espree";

const repoRoot = resolve(import.meta.dirname, "../..");
const target = `${repoRoot}/static/standalone.js`;
const check = process.argv.includes("--check");
const source = readFileSync(target, "utf8");

const ast = espree.parse(source, { ecmaVersion: 2024, sourceType: "script", range: true });
const names = new Set();
for (const node of ast.body) {
  if (node.type !== "ExpressionStatement" || node.expression.type !== "CallExpression") continue;
  const callee = node.expression.callee;
  if (!["FunctionExpression", "ArrowFunctionExpression"].includes(callee.type)) continue;
  for (const statement of callee.body.body || []) {
    if (statement.type === "FunctionDeclaration" && statement.id) {
      names.add(statement.id.name);
    } else if (statement.type === "VariableDeclaration" && statement.kind === "const") {
      for (const declarator of statement.declarations) {
        if (declarator.id.type === "Identifier") names.add(declarator.id.name);
      }
    }
  }
}

const block = /const __pure = \{([\s\S]*?)\};/.exec(source);
if (!block) {
  console.error("__pure export block not found");
  process.exit(2);
}
const entries = block[1].split(/,(?![^{]*\})/).map((part) => part.trim())
  .filter(Boolean).filter((part) => !part.startsWith("//"));
// Aliases such as `matchTenderFor: matchTender` are real exports; keep them verbatim.
const aliases = entries.filter((part) => part.includes(":"));
// An alias is written out verbatim at the end. Its key must never also be emitted as a
// shorthand, which would export an identifier the bundle never declares and kill the
// whole script at load with "matchTenderFor is not defined".
const aliasKeys = new Set(aliases.map((part) => part.split(":")[0].trim()));
const exported = new Set(entries.map((part) => part.split(":")[0].trim())
  .filter((part) => /^[A-Za-z_$][\w$]*$/.test(part)));
const shorthand = new Set([...exported].filter((name) => !aliasKeys.has(name)));
// __pure cannot list itself. The repair updater is deliberately not a pure API: repair
// verification belongs to the native service, and unit_test asserts these stay private.
const SKIP = new Set(["__pure", "findRepairCandidateFromReports", "repairTargetMatch",
  "clearAbsenceForRepair", "repairConditionFor", "repairEvidenceFromReport",
  "findRepairCandidate", "applyRepairObservation"]);
const missing = [...names].filter((name) => !exported.has(name) && !SKIP.has(name)).sort();

if (!missing.length) {
  console.log(`__pure exports all ${exported.size} helpers.`);
  process.exit(0);
}
if (check) {
  console.log(`__pure is missing ${missing.length} helper(s): ${missing.slice(0, 12).join(", ")}`
    + (missing.length > 12 ? ", ..." : ""));
  process.exit(1);
}

const lines = [];
let line = "                  ";
for (const name of [...new Set([...shorthand, ...missing])].sort().concat(aliases)) {
  if (line.length + name.length + 2 > 96) { lines.push(line); line = "                  "; }
  line += ` ${name},`;
}
lines.push(line);
const rebuilt = `const __pure = {\n${lines.join("\n")}\n                 };`;
writeFileSync(target, source.slice(0, block.index) + rebuilt + source.slice(block.index + block[0].length));
console.log(`__pure now exports ${exported.size + missing.length} helpers (added ${missing.length}).`);
