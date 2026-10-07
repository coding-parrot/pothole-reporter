import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import test from "node:test";

import { BROKEN_RULES_METRIC, CANARY_FAILED_METRIC, createHealthFunction } from "../service/health/function.mjs";
import { HEALTHY_WINDOW, fakeApi, fetchFrom } from "./health-support.mjs";

// The scheduled health function as the stack declares it. Every unit test passed while
// GET /v1/map returned 500 for ten days on a grant only the template decides, so what
// the template gives this function, and withholds from it, is read here: the role's
// scope, the schedule and what each rule sends, and the three alarms that carry its news.

const template = readFileSync(new URL("../template.yaml", import.meta.url), "utf8");

// One resource's text: from its name at two spaces to the next line at two spaces or less.
function resource(name) {
  const lines = template.split("\n");
  const start = lines.indexOf(`  ${name}:`);
  assert.notEqual(start, -1, `template.yaml declares no ${name}`);
  const length = lines.slice(start + 1).findIndex((line) => /^ {0,2}[A-Za-z#]/.test(line));
  return lines.slice(start, length === -1 ? undefined : start + 1 + length).join("\n");
}
const value = (text, key) => {
  const match = text.match(new RegExp(`^\\s*${key}: (.+)$`, "m"));
  assert.ok(match, `${key} not found`);
  return match[1].trim();
};
const statement = (role, sid) => {
  const start = role.indexOf(`- Sid: ${sid}`);
  assert.notEqual(start, -1, `no statement ${sid}`);
  const next = role.indexOf("- Sid:", start + 1);
  return role.slice(start, next === -1 ? undefined : next);
};
const actions = (text) => [...text.matchAll(/\b(logs|ssm|dynamodb|secretsmanager|lambda|kms|s3|iam|sns|cloudwatch|events):[A-Za-z*]+/g)].map((match) => match[0]);

const fn = resource("HealthFunction");
const role = resource("HealthRole");

test("the health function is the same package under another handler, small and one at a time", () => {
  const central = resource("CentralFunction");
  assert.equal(value(fn, "Runtime"), "nodejs22.x");
  assert.equal(value(fn, "Handler"), "infra/aws-central/service/health/handler.handler");
  assert.ok(existsSync(new URL("../service/health/handler.mjs", import.meta.url)));
  assert.match(readFileSync(new URL("../service/health/handler.mjs", import.meta.url), "utf8"), /^export const handler = /m);
  for (const key of ["S3Bucket", "S3Key"]) assert.equal(value(fn, key), value(central, key), "one build, one zip");
  const memory = Number(value(fn, "MemorySize"));
  assert.ok(memory >= 256 && memory <= 512, `${memory} MB`);
  assert.equal(value(fn, "ReservedConcurrentExecutions"), "1");
  assert.equal(value(fn, "Role"), "!GetAtt HealthRole.Arn");
  assert.notEqual(value(central, "Role"), value(fn, "Role"));
  assert.match(fn, /DependsOn: HealthLogs/);
  assert.equal(value(resource("HealthLogs"), "LogGroupName"), "!Sub '/aws/lambda/${ProjectPrefix}-health'");
  assert.equal(value(fn, "FunctionName"), "!Sub '${ProjectPrefix}-health'", "Lambda logs to /aws/lambda/<function name>");
});

test("the timeout outlasts the slowest run that still ends in a report", () => {
  // window: the first query, then the other five together, 45 s of patience each.
  // reads: four requests of 20 s. full: those, a registration of 20 s, four signed of 30 s.
  const insights = readFileSync(new URL("../service/health/function.mjs", import.meta.url), "utf8");
  const patience = Number(insights.match(/patienceMs: ([\d_]+)/)[1].replaceAll("_", "")) / 1000;
  const windowAndReads = 2 * patience + 4 * 20;
  const full = 4 * 20 + 20 + 4 * 30;
  const timeout = Number(value(fn, "Timeout"));
  assert.ok(timeout > Math.max(windowAndReads, full), `${timeout} s against ${windowAndReads} s and ${full} s`);
  assert.ok(timeout <= 900);
});

test("the function is given exactly the settings its entry point reads", () => {
  const entry = readFileSync(new URL("../service/health/handler.mjs", import.meta.url), "utf8");
  const read = [...new Set([...entry.matchAll(/process\.env\.([A-Z_]+)/g)].map((match) => match[1]))].sort();
  const variables = fn.slice(fn.indexOf("Variables:"));
  const given = [...variables.matchAll(/^ {10}([A-Z_]+): /gm)].map((match) => match[1]).sort();
  assert.deepEqual(given, read);
  assert.equal(value(variables, "API_URL"), "!GetAtt Api.ApiEndpoint", "the public API, as a phone reaches it");
  assert.equal(value(variables, "CENTRAL_LOG_GROUP"), "!Ref LambdaLogs");
  assert.equal(value(variables, "METRIC_NAMESPACE"), "!Ref ProjectPrefix");
  assert.equal(value(variables, "CANARY_KEY_PARAMETER"), "!Sub '/${ProjectPrefix}/health/canary-key'");
});

test("its role may query one log group, write its own log and keep one parameter, and nothing else", () => {
  assert.deepEqual(actions(role.slice(role.indexOf("Policies:"))).sort(), [
    "logs:CreateLogStream", "logs:GetQueryResults", "logs:PutLogEvents", "logs:StartQuery", "logs:StopQuery",
    "ssm:GetParameter", "ssm:PutParameter",
  ]);
  assert.equal(role.match(/- Sid:/g).length, 4);
  assert.ok(!/Action: '?\*'?\s*$/m.test(role) && !/NotAction|NotResource|ManagedPolicyArns/.test(role));
  // No table, no secret, no function: the canary goes through the public API like a phone.
  assert.ok(!/Table|DetectorSecret|CentralFunction/.test(role));

  assert.equal(value(statement(role, "WriteOwnLogs"), "Resource"), "!Sub '${HealthLogs.Arn}:*'");
  const query = statement(role, "QueryCentralRequestLog");
  assert.deepEqual(actions(query), ["logs:StartQuery"]);
  assert.equal(value(query, "Resource"), "!GetAtt LambdaLogs.Arn", "the central function's log group and no other");
  // The only wildcard: the two calls that name a query by id, which IAM cannot scope.
  const results = statement(role, "ReadAndStopItsQueries");
  assert.deepEqual(actions(results).sort(), ["logs:GetQueryResults", "logs:StopQuery"]);
  assert.equal(role.match(/Resource: '\*'/g).length, 1);

  const key = statement(role, "KeepCanaryKey");
  assert.deepEqual(actions(key).sort(), ["ssm:GetParameter", "ssm:PutParameter"]);
  assert.equal(value(key, "Resource"),
    "!Sub 'arn:${AWS::Partition}:ssm:${AWS::Region}:${AWS::AccountId}:parameter/${ProjectPrefix}/health/canary-key'");
  // The parameter the role may touch is the one the function is told to use.
  const name = value(fn, "CANARY_KEY_PARAMETER").match(/'(.+)'/)[1];
  assert.ok(value(key, "Resource").endsWith(`:parameter${name}'`));
});

test("the central function's role gains nothing from this", () => {
  const central = resource("LambdaRole");
  assert.ok(!/ssm:|logs:StartQuery|logs:GetQueryResults/.test(central));
});

const RULES = { HealthWindowSchedule: "HealthWindowInvokePermission", HealthReadsSchedule: "HealthReadsInvokePermission",
  HealthCanarySchedule: "HealthCanaryInvokePermission" };
const schedule = (name) => {
  const rule = resource(name);
  const cron = value(rule, "ScheduleExpression").match(/^cron\((\S+) (\S+) \* \* \? \*\)$/);
  assert.ok(cron, `${name} is not a daily cron expression`);
  return { rule, minute: cron[1], hour: cron[2], input: JSON.parse(value(rule, "Input").slice(1, -1)) };
};
// Runs in six hours of a cron hour field: every hour, or every Nth.
const runsInSixHours = (hour) => (hour === "*" ? 6 : 6 / Number(hour.match(/^0\/(\d+)$/)[1]));

test("three rules invoke it, each allowed to, each sending an event the function accepts", async () => {
  for (const [name, permission] of Object.entries(RULES)) {
    const { rule, input } = schedule(name);
    assert.equal(value(rule, "State"), "ENABLED");
    assert.equal(value(rule, "Arn"), "!GetAtt HealthFunction.Arn");
    const grant = resource(permission);
    assert.equal(value(grant, "FunctionName"), "!Ref HealthFunction");
    assert.equal(value(grant, "Principal"), "events.amazonaws.com");
    assert.equal(value(grant, "SourceArn"), `!GetAtt ${name}.Arn`);
    // The event as written in the template, given to the function itself.
    const written = [];
    const health = createHealthFunction({
      apiUrl: "https://api.test", logGroup: "g", namespace: "n", keyParameter: "/k", fetch: fetchFrom(fakeApi()),
      readImage: () => Buffer.from("jpeg"), log: () => {}, emit: (line) => written.push(JSON.parse(line)), sleep: async () => {},
      logs: { startQuery: async ({ queryString }) => ({ queryId: String(HEALTHY_WINDOW.findIndex((entry) => queryString.includes(entry.match))) }),
        getQueryResults: async ({ queryId }) => ({ status: "Complete", results: HEALTHY_WINDOW[Number(queryId)].rows.map((row) => Object.entries(row).map(([field, v]) => ({ field, value: v }))) }),
        stopQuery: async () => {} },
      parameters: { getParameter: async () => { throw Object.assign(new Error("none"), { name: "ParameterNotFound" }); }, putParameter: async () => ({}) },
    });
    assert.equal((await health(input)).healthy, true, `${name} sends ${JSON.stringify(input)}`);
    assert.deepEqual([written[0].window, written[0].canary], [input.window ?? null, input.canary ?? null]);
  }
  assert.deepEqual(schedule("HealthWindowSchedule").input, { window: "6h", canary: "reads" });
  assert.deepEqual(schedule("HealthReadsSchedule").input, { canary: "reads" });
  assert.deepEqual(schedule("HealthCanarySchedule").input, { canary: "full" });
});

test("no two rules fire in the same minute, because the function runs one at a time", () => {
  const minutes = Object.keys(RULES).map((name) => schedule(name).minute);
  assert.equal(new Set(minutes).size, minutes.length, `minutes ${minutes}`);
  for (const minute of minutes) assert.match(minute, /^\d+$/);
  // Far enough apart that one run has ended before the next: the timeout is 4 minutes.
  const sorted = minutes.map(Number).sort((a, b) => a - b);
  const gaps = sorted.map((minute, index) => (sorted[(index + 1) % sorted.length] - minute + 60) % 60);
  assert.ok(Math.min(...gaps) * 60 > Number(value(fn, "Timeout")), `gaps ${gaps} minutes`);
});

test("the cadence: a canary every half hour, the window every hour, a detection every three", () => {
  assert.equal(schedule("HealthWindowSchedule").hour, "*");
  assert.equal(schedule("HealthReadsSchedule").hour, "*");
  assert.equal(Math.abs(Number(schedule("HealthWindowSchedule").minute) - Number(schedule("HealthReadsSchedule").minute)), 30);
  assert.equal(runsInSixHours(schedule("HealthCanarySchedule").hour), 2);
});

// The request log does not say which install made a request, so the full canary's three
// tender lookups are judged by the window rules beside people's, and none of them ever
// matches a tender (its Bengaluru point answers no_location_match). "tenders match"
// fails at 20 lookups with none matched (test/health-window.test.mjs). The canaries of
// one window must stay under half of that, or a quiet night alarms on the canary alone.
test("the full canaries of one window stay under half the lookups that would break a rule on their own", () => {
  const LOOKUPS_PER_FULL_CANARY = 3;
  const window = Number(schedule("HealthWindowSchedule").input.window.match(/^(\d+)h$/)[1]);
  const full = Object.keys(RULES).map(schedule).filter(({ input }) => input.canary === "full");
  const canaries = full.reduce((sum, { hour }) => sum + runsInSixHours(hour) * (window / 6), 0);
  assert.equal(canaries * LOOKUPS_PER_FULL_CANARY, 6);
  assert.ok(canaries * LOOKUPS_PER_FULL_CANARY <= 10, `${canaries} full canaries in ${window} h`);
});

test("a failed or throttled run is never retried: each full canary is a paid detection", () => {
  const config = resource("HealthInvokeConfig");
  assert.equal(value(config, "FunctionName"), "!Ref HealthFunction");
  assert.equal(value(config, "MaximumRetryAttempts"), "0");
  assert.ok(Number(value(config, "MaximumEventAgeInSeconds")) <= 300);
});

test("broken rules alarm on the second evaluation, a failed canary on the first, and silence is healthy for both", () => {
  const rules = resource("HealthRulesAlarm");
  assert.equal(value(rules, "MetricName"), BROKEN_RULES_METRIC);
  assert.equal(value(rules, "Period"), "3600", "the window is judged every hour");
  assert.deepEqual([value(rules, "EvaluationPeriods"), value(rules, "DatapointsToAlarm")], ["2", "2"]);
  const canary = resource("HealthCanaryAlarm");
  assert.equal(value(canary, "MetricName"), CANARY_FAILED_METRIC);
  assert.equal(value(canary, "EvaluationPeriods"), "1");
  // CloudWatch looks back two periods more than it evaluates. That range has to hold the
  // last full canary, or a fault only the full canary sees would clear and alarm again
  // on every run.
  const lookBack = (Number(value(canary, "EvaluationPeriods")) + 2) * Number(value(canary, "Period"));
  assert.ok(lookBack > 3 * 3600 && lookBack <= 6 * 3600, `${lookBack} s`);
  for (const alarm of [rules, canary]) {
    // The namespace the function writes to (METRIC_NAMESPACE), with no dimension.
    assert.equal(value(alarm, "Namespace"), value(fn, "METRIC_NAMESPACE"));
    assert.ok(!/Dimensions:/.test(alarm));
    assert.equal(value(alarm, "Statistic"), "Maximum");
    assert.equal(value(alarm, "Threshold"), "1");
    assert.equal(value(alarm, "ComparisonOperator"), "GreaterThanOrEqualToThreshold");
    // The metric is written only when something is broken, so no data is the healthy state.
    assert.equal(value(alarm, "TreatMissingData"), "notBreaching");
  }
});

test("a health function that does not finish its runs alarms, and no data counts as not finishing", () => {
  const silent = resource("HealthSilentAlarm");
  assert.equal(value(silent, "TreatMissingData"), "breaching", "a dead checker must not look healthy");
  assert.equal(value(silent, "Expression"), "invocations - errors");
  assert.deepEqual([value(silent, "ComparisonOperator"), value(silent, "Threshold")], ["LessThanThreshold", "1"]);
  assert.deepEqual([value(silent, "EvaluationPeriods"), value(silent, "DatapointsToAlarm")], ["2", "2"]);
  assert.deepEqual([...silent.matchAll(/MetricName: (\w+)/g)].map((match) => match[1]), ["Invocations", "Errors"]);
  assert.equal(silent.match(/Namespace: AWS\/Lambda/g).length, 2, "Lambda's own metrics, which cost nothing");
  assert.equal(silent.match(/Value: !Ref HealthFunction/g).length, 2);
  // Every period must hold a run when all is well: the function runs every 30 minutes.
  assert.deepEqual([...silent.matchAll(/Period: (\d+)/g)].map((match) => match[1]), ["1800", "1800"]);
  assert.equal(silent.match(/ReturnData: true/g).length, 1);
});

test("all three alarms tell the stack's alert topic, on the way in and on the way out", () => {
  for (const name of ["HealthRulesAlarm", "HealthCanaryAlarm", "HealthSilentAlarm"]) {
    const alarm = resource(name);
    assert.equal(value(alarm, "AlarmActions"), "[!Ref AlertTopic]");
    assert.equal(value(alarm, "OKActions"), "[!Ref AlertTopic]");
  }
});

// Alarms are USD 0.10 a month each in Mumbai (the account's ten free ones were already
// taken by 16 others on 7 Oct 2026), and the alarm on two Lambda metrics counts twice.
test("the stack has nine alarms", () => {
  assert.equal(template.match(/Type: AWS::CloudWatch::Alarm/g).length, 9);
});

test("deploy.sh can find the health function: the stack names it in its outputs", () => {
  const outputs = template.slice(template.indexOf("\nOutputs:"));
  assert.match(outputs, /HealthFunctionName:\n(?: {4}.*\n)* {4}Value: !Ref HealthFunction/);
});
