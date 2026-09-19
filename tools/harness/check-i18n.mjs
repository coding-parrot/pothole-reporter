#!/usr/bin/env node
// Every language the app offers must say the same things.
//
//   node tools/harness/check-i18n.mjs [--json]
//
// The Settings screen lists four languages. A merge once left two of those dictionaries
// on an older build: keys the code still asks for were missing, and t() answered with
// the raw key, so a Marathi tester saw "chip_fixed" printed on screen. This fails when
// a key the code uses is missing from any language, when a dictionary carries a key
// nothing uses, or when a translation drops a {placeholder} the English string has.

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const repoRoot = resolve(import.meta.dirname, "../..");
const html = readFileSync(`${repoRoot}/static/index.html`, "utf8");

function sliceObject(source, marker) {
  const start = source.indexOf(marker);
  if (start < 0) throw new Error(`${marker} not found`);
  const open = source.indexOf("{", start);
  let depth = 0, index = open, quote = null, escaped = false;
  for (; index < source.length; index++) {
    const ch = source[index];
    if (quote) {
      if (escaped) escaped = false;
      else if (ch === "\\") escaped = true;
      else if (ch === quote) quote = null;
      continue;
    }
    if (ch === '"' || ch === "'" || ch === "`") { quote = ch; continue; }
    if (ch === "{") depth++;
    else if (ch === "}" && !--depth) break;
  }
  return { source: source.slice(open, index + 1), start: open, end: index + 1 };
}

const dict = sliceObject(html, "const I18N = {");
const I18N = (0, eval)(`(${dict.source})`);
const rest = html.slice(0, dict.start) + html.slice(dict.end);

const used = new Set();
for (const match of rest.matchAll(/\bt\(\s*["'`]([a-z0-9_]+)["'`]/g)) used.add(match[1]);
// Keys built by concatenation: t("size_" + value), t(`assessment_${value}`).
const prefixes = new Set([
  ...[...rest.matchAll(/\bt\(\s*["'`]([a-z0-9_]+_)["'`]\s*\+/g)].map((m) => m[1]),
  ...[...rest.matchAll(/\bt\(\s*`([a-z0-9_]+_)\$\{/g)].map((m) => m[1]),
]);

const languages = Object.keys(I18N);
const [base] = languages;
const every = [...new Set(languages.flatMap((language) => Object.keys(I18N[language])))].sort();
const isUsed = (key) => used.has(key) || [...prefixes].some((prefix) => key.startsWith(prefix));
const placeholders = (value) =>
  [...String(value).matchAll(/\{([a-z0-9_]+)\}/g)].map((match) => match[1]).sort().join(",");

// A key can also be read indirectly, as optionalPrivacyText("privacy_source") does.
// Deleting one of those once emptied the first line of the consent notice, so a key
// counts as used whenever its exact name appears as a quoted string in the code.
const quoted = new Set([...rest.matchAll(/["'`]([a-z0-9_]+)["'`]/g)].map((m) => m[1]));
const failures = [];
const unused = every.filter((key) => !isUsed(key) && !quoted.has(key));
for (const key of unused) {
  failures.push(`unused: ${key} is defined in ${languages.filter((l) => key in I18N[l]).join("/")}`
    + " but nothing reads it");
}
for (const key of every.filter(isUsed)) {
  for (const language of languages) {
    if (!(key in I18N[language])) failures.push(`missing: ${language}.${key}`);
  }
  const want = placeholders(I18N[base][key]);
  for (const language of languages) {
    if (!(key in I18N[language]) || language === base) continue;
    const got = placeholders(I18N[language][key]);
    if (got !== want) failures.push(`placeholder: ${language}.${key} has {${got}}, ${base} has {${want}}`);
  }
}
for (const key of [...used].filter((key) => !every.includes(key) && !prefixes.has(key))) {
  failures.push(`undefined: t("${key}") has no entry in any language`);
}

if (process.argv.includes("--json")) {
  console.log(JSON.stringify({ languages, unused, failures }, null, 1));
  process.exit(failures.length ? 1 : 0);
}
if (!failures.length) {
  console.log(`i18n: ${languages.join("/")} each define the same ${every.length} keys.`);
  process.exit(0);
}
console.log(`FAIL ${failures.length} i18n problem(s):`);
for (const failure of failures.slice(0, 40)) console.log("  - " + failure);
if (failures.length > 40) console.log(`  ... and ${failures.length - 40} more`);
process.exit(1);
