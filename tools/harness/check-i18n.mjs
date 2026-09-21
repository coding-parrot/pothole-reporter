#!/usr/bin/env node
// Every language the app offers must say the same things.
//
//   node tools/harness/check-i18n.mjs [--json] [--stamp [<lang>.]<key>[,...]]
//
// The Settings screen lists four languages. A merge once left two of those dictionaries
// on an older build: keys the code still asks for were missing, and t() answered with
// the raw key, so a Marathi tester saw "chip_fixed" printed on screen. This fails when
// a key the code uses is missing from any language, when a dictionary carries a key
// nothing uses, or when a translation drops a {placeholder} the English string has.

import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
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

// Translations that once meant something else. English banner_setup became a shared
// service outage message, but Marathi and Bengali kept the personal-key era's "add an
// OpenAI API key to use photo and drive", and a key count cannot see that.
const LEGACY_PHRASES = {
  banner_setup: { mr: ["फोटो आणि ड्राइव्ह वापरण्यासाठी"], bn: ["ছবি ও ড্রাইভ ব্যবহার করতে"] },
};
for (const [key, byLanguage] of Object.entries(LEGACY_PHRASES)) {
  for (const [language, phrases] of Object.entries(byLanguage)) {
    const value = String(I18N[language] && I18N[language][key] || "");
    for (const phrase of phrases.filter((phrase) => value.includes(phrase))) {
      failures.push(`legacy: ${language}.${key} still says "${phrase}", which ${base} no longer means`);
    }
  }
}

// English left inside a translation. The Bengali coarse-GPS help once read "frame
// location ... calibration না-করা dashcam stream-এ ... authority ও tender routing":
// a tester who chose Bengali got the half they could not read. Names, file formats and
// the labels of buttons the tester presses (here or in another app) stay in Latin
// script; everything else is a word that still needs translating.
const LATIN_NAMES = new Set([
  "OpenAI", "API", "GPS", "GPX", "Android", "Google", "Maps", "GitHub", "Pages", "Meta",
  "glasses", "YOLO", "NHAI", "PWD", "IST", "UTC", "JPEG", "AVC", "HEVC", "AVI", "MKV", "MOV",
  "fps", "gpt", "mini", "High", "Pothole", "Reporter", "Photo", "Drive", "Send", "Share",
  "Import", "Documents", "pothole", "frames",
]);
// Technical terms the privacy disclosures name on purpose, as the English policy does.
const LATIN_TERMS = {
  settings_note: ["hash"], privacy_local: ["hash"], privacy_body: ["foreground", "service"],
  privacy_government: ["routing", "pack", "tile", "tiles"], provider_shared_note: ["gateway"],
};
// Bengali keeps "key" from the "OpenAI API key" field label: কী also means "what".
const LATIN_BY_LANGUAGE = { bn: ["key"] };
for (const language of languages.filter((l) => l !== base)) {
  for (const [key, value] of Object.entries(I18N[language])) {
    const text = String(value).replace(/\{[a-z0-9_]+\}|<[^>]+>|&[a-z]+;|https?:\/\/\S+/g, " ");
    const allowed = [...(LATIN_TERMS[key] || []), ...(LATIN_BY_LANGUAGE[language] || [])];
    for (const [word] of text.matchAll(/[A-Za-z]{3,}/g)) {
      if (LATIN_NAMES.has(word) || allowed.includes(word)) continue;
      failures.push(`english: ${language}.${key} leaves "${word}" untranslated`);
    }
  }
}

// Bengali counts carry their own classifier: eventCountText(2) is already "2টি ...".
// A template that adds another টি after a placeholder filled with such a phrase prints
// "2টি রাস্তার গর্তটি". Which placeholders hold counted phrases is read from the calls.
const countedVars = new Set([...rest.matchAll(
  /\b(?:const|let)\s+(\w+)\s*=\s*(?:[^;]*\?\s*)?(?:eventCountText|alreadyCountText)\(/g)]
  .map((m) => m[1]));
const countedValue = new RegExp(
  `(\\w+):\\s*(?:eventCountText\\(|alreadyCountText\\(|(?:${[...countedVars].join("|") || "$^"})\\b)`, "g");
for (const call of rest.matchAll(/\bt\(\s*"([a-z0-9_]+)",\s*\{([^}]*)\}/g)) {
  const [, key, args] = call;
  for (const [, name] of args.matchAll(countedValue)) {
    if (String(I18N.bn && I18N.bn[key] || "").includes(`{${name}}টি`)) {
      failures.push(`counted: bn.${key} adds টি after {${name}}, which already holds a counted phrase`);
    }
  }
}

// A translation is a copy of one English sentence. When the English changes, the
// translation describes an older build until someone rewrites it, and nothing above
// can tell: Marathi said "Pothole: no" long after English said "No road damage".
// i18n-sources.json keeps, for each translated string, a hash of the English it was
// written from. After updating a translation, record it with --stamp <lang>.<key>.
const SOURCES = `${repoRoot}/tools/harness/i18n-sources.json`;
const sourceHash = (value) => createHash("sha256").update(String(value)).digest("hex").slice(0, 12);
let sources = {};
try { sources = JSON.parse(readFileSync(SOURCES, "utf8")); } catch { sources = {}; }
// A bare key stamps it in every language, for a string added or rewritten in all four.
const stamps = process.argv.flatMap((arg, i) => process.argv[i - 1] === "--stamp" ? arg.split(",") : [])
  .flatMap((stamp) => stamp.includes(".") ? [stamp]
    : languages.filter((l) => l !== base).map((language) => `${language}.${stamp}`));
if (process.argv.includes("--stamp-all")) {
  for (const language of languages.filter((l) => l !== base)) {
    for (const key of Object.keys(I18N[base])) stamps.push(`${language}.${key}`);
  }
}
if (stamps.length) {
  for (const stamp of stamps) {
    const [language, key] = stamp.split(".");
    if (!I18N[language] || !(key in I18N[base])) throw new Error(`--stamp: no ${stamp}`);
    (sources[language] ||= {})[key] = sourceHash(I18N[base][key]);
  }
  for (const language of Object.keys(sources)) {
    sources[language] = Object.fromEntries(Object.entries(sources[language]).sort());
  }
  writeFileSync(SOURCES, JSON.stringify(sources, null, 1) + "\n");
}
for (const language of languages.filter((l) => l !== base)) {
  for (const key of Object.keys(I18N[base]).filter((key) => key in I18N[language])) {
    const recorded = sources[language] && sources[language][key];
    if (recorded !== sourceHash(I18N[base][key])) {
      failures.push(`stale: ${language}.${key} was ${recorded ? "translated from an older" : "never checked against the"}`
        + ` English; update it, then run check-i18n.mjs --stamp ${language}.${key}`);
    }
  }
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
