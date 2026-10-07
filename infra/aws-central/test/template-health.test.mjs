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
  const window = 2 * patience;
  const full = 4 * 20 + 20 + 4 * 30;
  const timeout = Number(value(fn, "Timeout"));
  // The hourly event runs both, one after the other.
  assert.ok(timeout > window + full, `${timeout} s against ${window} s and then ${full} s`);
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
  assert.equal(value(variables, "CANARY_INSTALL_PARAMETER"), "!Sub '/${ProjectPrefix}/health/canary-install-id'");
});

test("its role may query one log group, write its own log and keep one parameter, and nothing else", () => {
  assert.deepEqual(actions(role.slice(role.indexOf("Policies:"))).sort(), [
    "logs:CreateLogStream", "logs:GetQueryResults", "logs:PutLogEvents", "logs:StartQuery", "logs:StopQuery",
    "ssm:GetParameter", "ssm:GetParameter", "ssm:PutParameter", "ssm:PutParameter",
  ]);
  assert.equal(role.match(/- Sid:/g).length, 5);
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
  // And the install id it publishes for the central function, which is no secret.
  const publish = statement(role, "PublishCanaryInstallId");
  assert.deepEqual(actions(publish).sort(), ["ssm:GetParameter", "ssm:PutParameter"]);
  const published = value(fn, "CANARY_INSTALL_PARAMETER").match(/'(.+)'/)[1];
  assert.ok(value(publish, "Resource").endsWith(`:parameter${published}'`));
});

test("the central function's role gains one read, of the canary's install id, and nothing else", () => {
  const central = resource("LambdaRole");
  assert.deepEqual(actions(central).filter((action) => !action.startsWith("dynamodb:")).sort(),
    ["lambda:InvokeFunction", "logs:CreateLogStream", "logs:PutLogEvents", "secretsmanager:GetSecretValue", "ssm:GetParameter"]);
  assert.ok(!central.includes("canary-key"), "never the canary's private key");
});

const RULES = { HealthWindowSchedule: "HealthWindowInvokePermission", HealthCanarySchedule: "HealthCanaryInvokePermission" };
const schedule = (name) => {
  const rule = resource(name);
  const cron = value(rule, "ScheduleExpression").match(/^cron\((\S+) (\S+) \* \* \? \*\)$/);
  assert.ok(cron, `${name} is not a daily cron expression`);
  return { rule, minute: cron[1], hour: cron[2], input: JSON.parse(value(rule, "Input").slice(1, -1)) };
};

test("two rules invoke it, each allowed to, each sending an event the function accepts", async () => {
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
      apiUrl: "https://api.test", logGroup: "g", namespace: "n", keyParameter: "/k", installParameter: "/i", fetch: fetchFrom(fakeApi()),
      readImage: () => Buffer.from("jpeg"), log: () => {}, emit: (line) => written.push(JSON.parse(line)), sleep: async () => {},
      logs: { startQuery: async ({ queryString }) => ({ queryId: String(HEALTHY_WINDOW.findIndex((entry) => queryString.includes(entry.match))) }),
        getQueryResults: async ({ queryId }) => ({ status: "Complete", results: HEALTHY_WINDOW[Number(queryId)].rows.map((row) => Object.entries(row).map(([field, v]) => ({ field, value: v }))) }),
        stopQuery: async () => {} },
      parameters: { getParameter: async () => { throw Object.assign(new Error("none"), { name: "ParameterNotFound" }); }, putParameter: async () => ({}) },
    });
    assert.equal((await health(input)).healthy, true, `${name} sends ${JSON.stringify(input)}`);
    assert.deepEqual([written[0].window, written[0].canary], [input.window ?? null, input.canary ?? null]);
  }
  assert.deepEqual(schedule("HealthWindowSchedule").input, { window: "6h", canary: "full" });
  assert.deepEqual(schedule("HealthCanarySchedule").input, { canary: "full" });
  assert.ok(!template.includes("HealthReadsSchedule"), "the reads-only rule is gone: every scheduled canary is a full one");
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

// The full canary ran every three hours while its lookups were judged beside people's:
// none of them matches a tender, and "tenders match" fails at 20 unmatched in a window.
// Its lines are now marked by the service and left out by every query
// (test/health-window.test.mjs), so it runs every half hour: 12 canaries and 36 lookups
// in a window, none of them counted.
test("the cadence: the full canary every half hour, the window every hour", () => {
  const window = schedule("HealthWindowSchedule");
  const canary = schedule("HealthCanarySchedule");
  assert.deepEqual([window.hour, canary.hour], ["*", "*"]);
  assert.equal(Math.abs(Number(window.minute) - Number(canary.minute)), 30);
  assert.equal(template.match(/"canary": "full"/g).length, 2);
  assert.ok(!/"canary": "reads"/.test(template));
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
  // last canary (they are half an hour apart), or a standing fault would clear and alarm
  // again on every run.
  const lookBack = (Number(value(canary, "EvaluationPeriods")) + 2) * Number(value(canary, "Period"));
  assert.ok(lookBack > 2 * 1800 && lookBack <= 2 * 3600, `${lookBack} s`);
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

// GitHub ran the workflow's hourly schedule every 4 to 5 hours (16:48, 21:37 and 01:32
// UTC on 6 and 7 Oct 2026), which is why the schedule is the stack's now. Two schedules
// would also be two canaries, and twice the lookups in the log.
test("the GitHub workflow is for manual runs only and says where the scheduled check lives", () => {
  const workflow = readFileSync(new URL("../../../.github/workflows/production-health.yml", import.meta.url), "utf8");
  const triggers = workflow.slice(workflow.indexOf("\non:"), workflow.indexOf("\npermissions:"));
  assert.match(triggers, /^\s+workflow_dispatch:/m);
  assert.ok(!/schedule|cron/.test(triggers), "no schedule");
  const header = workflow.slice(0, workflow.indexOf("\non:"));
  assert.match(header, /HealthFunction in infra\/aws-central\/template\.yaml/);
  // A manual run still judges the window, runs the canary and keeps the issue honest.
  assert.match(workflow, /production-health\.mjs --window 6h --canary/);
  assert.match(workflow, /name: Open or update the issue\n\s+if: failure\(\)/);
  assert.match(workflow, /name: Close the issue when healthy\n\s+if: success\(\)/);
});
