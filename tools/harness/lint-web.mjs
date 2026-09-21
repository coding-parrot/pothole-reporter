#!/usr/bin/env node
// Static gate: every identifier the shipped web app uses must exist.
//
// v1.38.1 shipped an index.html that read `trimmedKey` in the Settings save handler
// while nothing declared it, so the first screen of a fresh install threw
// "trimmedKey is not defined" and no tester could get past Settings. No test caught it
// because no test opened that build. This gate reads the file, not the running app.
//
//   node tools/harness/lint-web.mjs [paths...]
//
// Default targets are the canonical sources. Pass a path to check any copy, including
// one extracted from an APK.

import { readFileSync } from "node:fs";
import { basename, resolve } from "node:path";

import { Linter } from "eslint";
import globals from "globals";

const linter = new Linter();
const repoRoot = resolve(import.meta.dirname, "../..");
const targets = process.argv.slice(2).length
  ? process.argv.slice(2)
  : [`${repoRoot}/static/index.html`, `${repoRoot}/static/standalone.js`];

// Names the page legitimately gets from the other bundled files and from Capacitor.
const sharedGlobals = {
  ...globals.browser,
  ...globals.es2024,
  Capacitor: "readonly",
  L: "readonly",
  StandaloneAPI: "readonly",
  LLM_UI_CONFIG: "readonly",
  LLM_CONTRACT: "readonly",
  cordova: "readonly",
};

function inlineScript(source) {
  // One inline <script> holds the UI. Keep the leading newlines so reported line
  // numbers match the html file.
  const match = /<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/.exec(source);
  if (!match) return null;
  const before = source.slice(0, match.index + match[0].indexOf(match[1]));
  return "\n".repeat(before.split("\n").length - 1) + match[1];
}

let failures = 0;
for (const target of targets) {
  const source = readFileSync(target, "utf8");
  const code = target.endsWith(".html") ? inlineScript(source) : source;
  if (code === null) {
    console.log(`FAIL ${basename(target)}: no inline script found`);
    failures += 1;
    continue;
  }
  const messages = linter.verify(code, {
    languageOptions: {
      ecmaVersion: 2024,
      sourceType: "script",
      globals: {
        ...sharedGlobals,
        ...collectGlobals(targets, target),
        // A name the file itself installs on window is reachable bare at runtime.
        ...windowAssignments(code),
      },
    },
    rules: {
      "no-undef": "error",
      "no-dupe-keys": "error",
      "no-dupe-args": "error",
      "no-func-assign": "error",
      "no-unsafe-negation": "error",
      "no-unreachable": "error",
      "no-obj-calls": "error",
      "use-isnan": "error",
      "valid-typeof": "error",
      "no-const-assign": "error",
      "no-class-assign": "error",
      "no-dupe-class-members": "error",
      "no-self-assign": "error",
      "no-setter-return": "error",
      "no-sparse-arrays": "error",
    },
  });
  const errors = messages.filter((message) => message.severity === 2);
  // House rule: no em or en dashes anywhere the app ships, from UI strings and
  // aria-labels to complaint emails sent under the tester's name and code comments.
  // HTML entities render the same dash, so they count too.
  source.split("\n").forEach((line, index) => {
    const column = line.search(/[\u2013\u2014]|&[mn]dash;|&#821[12];|&#x201[34];/iu);
    if (column >= 0) {
      errors.push({ line: index + 1, column: column + 1, ruleId: "no-dash-characters",
                    message: "Em or en dash; use a comma, period, colon or parentheses" });
    }
  });
  // Settings labels once pointed at nothing, so TalkBack read every control as an
  // unnamed combo box and tapping a label did nothing. A named label must name its
  // control with for=.
  const settingsStart = source.indexOf('<div id="settings"');
  if (target.endsWith(".html") && settingsStart >= 0) {
    const settingsEnd = source.indexOf('\n  <div id="', settingsStart + 1);
    const block = source.slice(settingsStart, settingsEnd < 0 ? undefined : settingsEnd);
    const offset = source.slice(0, settingsStart).split("\n").length - 1;
    block.split("\n").forEach((line, index) => {
      for (const match of line.matchAll(/<label\b[^>]*\bid="([^"]+)"[^>]*>/g)) {
        if (!/\bfor="[^"]+"/.test(match[0])) {
          errors.push({ line: offset + index + 1, column: match.index + 1, ruleId: "settings-label-for",
                        message: `Settings label #${match[1]} has no for= naming its control` });
        }
      }
    });
  }
  if (errors.length) {
    failures += errors.length;
    console.log(`FAIL ${basename(target)}: ${errors.length} error(s)`);
    for (const error of errors) {
      console.log(`  ${target}:${error.line}:${error.column}  ${error.message} (${error.ruleId})`);
    }
  } else {
    console.log(`ok   ${basename(target)}`);
  }
}

function windowAssignments(code) {
  const result = {};
  for (const [, name] of code.matchAll(/window\.([A-Za-z_$][\w$]*)\s*=/g)) {
    result[name] = "readonly";
  }
  return result;
}

// standalone.js defines what index.html calls, and vice versa for a few callbacks the
// page installs on window. Treat top-level declarations of the sibling file as globals
// so a real missing name still fails while a legitimate cross-file call does not.
function collectGlobals(allTargets, current) {
  const result = {};
  for (const other of allTargets) {
    if (other === current) continue;
    const source = readFileSync(other, "utf8");
    const code = other.endsWith(".html") ? inlineScript(source) || "" : source;
    for (const [, name] of code.matchAll(/^(?:\s{0,2})(?:const|let|var|function|class)\s+([A-Za-z_$][\w$]*)/gm)) {
      result[name] = "readonly";
    }
    for (const [, name] of code.matchAll(/window\.([A-Za-z_$][\w$]*)\s*=/g)) {
      result[name] = "readonly";
    }
  }
  return result;
}

if (failures) {
  console.log(`\n${failures} static error(s). The app would throw these at a user.`);
  process.exit(1);
}
console.log("Static web checks passed.");
