// What one health run found: every rule with its verdict, in the order judged, and the
// lines a person reads. The command-line script prints each line as it is made; the
// scheduled function keeps them and logs the whole report once.
//
// A rule is ok, fail or skip (looked at, and deliberately not judged). `part` says which
// half of the run a rule belongs to, "window" or "canary", so the scheduled function can
// count broken log rules and a failed canary apart.
export function createReport({ print = () => {} } = {}) {
  const rules = [];
  const notes = [];
  const lines = [];
  let part = null;
  const say = (line) => {
    lines.push(line);
    print(line);
  };
  const verdict = (state, label) => (name, detail) => {
    rules.push({ part, name, state, detail });
    say(`  ${label} ${name}: ${detail}`);
  };
  return {
    begin(name, title) {
      part = name;
      say(`\n${title}`);
    },
    ok: verdict("ok", "ok  "),
    fail: verdict("fail", "FAIL"),
    skip: verdict("skip", "skip"),
    // Said once, in the closing line: how much the window held.
    note(text) { notes.push(text); },
    // A half that could not be judged to its end: a query failed, the API did not answer.
    // It is a broken rule like any other, and the only one that says nothing was learned.
    crashed(error, where = part) {
      rules.push({ part: where, name: "health check ran", state: "fail", detail: error.message, crashed: true });
      say(`  FAIL health check ran: ${error.message}`);
    },
    conclude() {
      const failures = rules.filter((rule) => rule.state === "fail");
      say(`\n${failures.length ? `UNHEALTHY: ${failures.length} rule(s) broken` : "HEALTHY"}${notes.length ? ` (${notes.join("; ")})` : ""}`);
      return { healthy: failures.length === 0, rules, failures, notes, lines };
    },
  };
}
