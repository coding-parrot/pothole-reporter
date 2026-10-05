#!/usr/bin/env node
// Read-only production inventory + explicitly bounded local dataset coverage.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {coverageFailures} from './tender_coverage_gate.mjs';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const argv = process.argv.slice(2);
const arg = (key, fallback) => argv.includes(key) ? argv[argv.indexOf(key) + 1] : fallback;
const out = path.resolve(arg('--out', path.join(root, 'eval/results/tender-coverage')));
const profile = arg('--profile', 'pothole'), region = arg('--region', 'ap-south-1');
fs.mkdirSync(out, {recursive: true});
const aws = (...args) => JSON.parse(execFileSync('aws', [...args, '--profile', profile, '--region', region, '--output', 'json'], {encoding: 'utf8', timeout: 60000}));
const read = name => JSON.parse(fs.readFileSync(path.join(root, name), 'utf8'));
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const save = (name, value) => fs.writeFileSync(path.join(out, name), JSON.stringify(value, null, 2) + '\n');
const time = new Date(), end = Math.floor(time.getTime()/1000), start = end - 7*86400;
const config = aws('lambda', 'get-function-configuration', '--function-name', 'pothole-reporter-central', '--query', '{LastModified:LastModified,TendersTable:Environment.Variables.TENDERS_TABLE}');
if (!config.TendersTable) throw Error('No configured production tender table');
let key, count = 0, scanned = 0, pages = 0;
do {
  const args = ['dynamodb', 'scan', '--table-name', config.TendersTable, '--select', 'COUNT', '--consistent-read', '--no-paginate'];
  if (key) args.push('--exclusive-start-key', JSON.stringify(key));
  const response = aws(...args);
  count += response.Count; scanned += response.ScannedCount; pages++;
  key = response.LastEvaluatedKey;
  if (pages > 100) throw Error('Audit page ceiling reached; refusing partial count');
} while (key);
const query = 'filter event = "http_request" and route = "/v1/tenders/resolve" and method = "POST" | stats count(*) as requests, count_distinct(request_id) as distinct_requests by outcome, status | sort requests desc';
const {queryId} = aws('logs', 'start-query', '--log-group-name', '/aws/lambda/pothole-reporter-central', '--start-time', String(start), '--end-time', String(end), '--query-string', query);
let logs;
for (let n = 0; n < 30; n++) {
  logs = aws('logs', 'get-query-results', '--query-id', queryId);
  if (!['Scheduled', 'Running'].includes(logs.status)) break;
  await new Promise(r => setTimeout(r, 1000));
}
if (logs.status !== 'Complete') throw Error(`Log audit incomplete: ${logs.status}`);
save('cloudwatch-results.json', logs);
const outcomes = logs.results.map(row => Object.fromEntries(row.map(x => [x.field, x.value])));
const requests = outcomes.reduce((n, x) => n + Number(x.requests), 0);
const matched = outcomes.filter(x => x.outcome === 'tender_matched').reduce((n, x) => n + Number(x.requests), 0);
const snapshot = read('data/tenders-karnataka.json');
const towns = read('data/karnataka-towns.json').towns;
const manifest = read('static/pack-manifest-v1.35.json');
const resource = manifest.resources['in-ka-tenders'];
const packBytes = fs.readFileSync(path.join(root, 'docs', resource.path));
if (sha(packBytes) !== resource.sha256) throw Error('Runtime tender pack hash mismatch');
const pack = JSON.parse(packBytes).tenders;
const bodyCounts = new Map();
for (const row of pack) bodyCounts.set(String(row.b), (bodyCounts.get(String(row.b)) || 0) + 1);
const townInventory = towns.map(t => ({name: t.name, body_lgd: String(t.lgd), local_pack_records: bodyCounts.get(String(t.lgd)) || 0, production_tender_records: count === 0 ? 0 : null}));
const coveredTowns = townInventory.filter(x => x.local_pack_records > 0).length;
const summary = {
  audited_at: time.toISOString(), production: {function: 'pothole-reporter-central', region, ...config,
    consistent_table_count: count, scanned, pages, server_tender_coverage_percent: count === 0 ? 0 : null,
    proof: count === 0 ? 'Configured table is empty: every municipal query returns no candidates; non-municipal routes return no tender by code.' : 'Non-empty inventory alone cannot establish road coverage.'},
  traffic: {start: new Date(start*1000).toISOString(), end: new Date(end*1000).toISOString(), queryId, query,
    resolve_post_requests: requests, tender_matched_requests: matched, match_rate_percent: requests ? 100*matched/requests : null, outcomes,
    limitations: ['Request counts, not distinct places or users.', 'May include automated tests.', 'Only explicit /v1/tenders/resolve; report route has a separate response path.']},
  local_snapshot: {records: snapshot.length, mapped_records: snapshot.filter(x => x.b).length,
    named_contractor_strings: snapshot.filter(x => x.c).length, warning: 'Mirror names are not verified award identities; not production records.'},
  client_pack: {resource, sha256_verified: true, records: pack.length,
    records_with_named_contractors: pack.filter(x => x.c).length,
    local_body_buckets: bodyCounts.size, towns_with_records: coveredTowns, town_roster_denominator: towns.length,
    town_inventory_percent: 100*coveredTowns/towns.length, legacy_bengaluru_pool_records: bodyCounts.get('BLR') || 0,
    warning: 'At-least-one-record per town is NOT percent of roads covered, successful GPS matching, current liability, or production AWS coverage.'},
};
summary.release_gate_failures = coverageFailures(summary);
save('summary.json', summary); save('town-inventory.json', townInventory);
console.log(JSON.stringify(summary, null, 2));
if (!argv.includes('--no-gate') && summary.release_gate_failures.length) process.exitCode = 1;
