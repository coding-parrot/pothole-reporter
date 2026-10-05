#!/usr/bin/env node
// Default is a local dry run. --apply only bootstraps the configured EMPTY table.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {hasRoadSurfaceScope} from '../service/tenders.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
export function prepareTenders(resource, bytes, now = new Date()) {
  const hash = crypto.createHash('sha256').update(bytes).digest('hex');
  if (hash !== resource.sha256) throw Error('Tender pack SHA-256 mismatch');
  const reviewDeadline = Date.parse(resource.review_after);
  if (!Number.isFinite(reviewDeadline) || reviewDeadline <= now.getTime()) throw Error('Tender pack needs source review');
  const pack = JSON.parse(bytes);
  if (pack.pack_id !== 'in-ka-tenders' || pack.state_code !== 'KA') throw Error('Wrong tender pack');
  const seen = new Set();
  return pack.tenders.filter(row => hasRoadSurfaceScope(row.t)).map(row => {
    if (!row.tn || !row.b || !row.t || !row.loc) throw Error('Incomplete indexed tender row');
    const key = `${row.b}\0${row.tn}`;
    if (seen.has(key)) throw Error(`Duplicate tender key: ${row.tn}`);
    seen.add(key);
    return {body_lgd: String(row.b), tender_number: row.tn, title: row.t, location: row.loc,
      contractor: null, published: row.d || null, source_name: 'Karnataka published procurement index (award and liability unverified)',
      source_url: resource.url, source_sha256: hash, source_retrieved_at: resource.source_retrieved_at,
      award_verified: false, liability_verified: false};
  });
}

export function toItem(row) {
  return Object.fromEntries(Object.entries(row).map(([key, value]) => [key,
    value === null ? {NULL: true} : typeof value === 'boolean' ? {BOOL: value} : {S: String(value)}]));
}

async function main() {
  const args = process.argv.slice(2);
  const arg = (key, fallback) => args.includes(key) ? args[args.indexOf(key)+1] : fallback;
  const profile = arg('--profile', 'pothole'), region = arg('--region', 'ap-south-1');
  const out = path.resolve(arg('--out', path.join(root, 'eval/results/tender-seed')));
  const manifest = JSON.parse(fs.readFileSync(path.join(root, 'static/pack-manifest-v1.35.json')));
  const resource = manifest.resources['in-ka-tenders'];
  const rows = prepareTenders(resource, fs.readFileSync(path.join(root, 'docs', resource.path)));
  if (!rows.length) throw Error('Refusing an empty seed');
  fs.mkdirSync(out, {recursive: true});
  const plan = {at: new Date().toISOString(), resource, count: rows.length,
    body_count: new Set(rows.map(row => row.body_lgd)).size, items: rows};
  fs.writeFileSync(path.join(out, 'plan.json'), JSON.stringify(plan, null, 2));
  console.log(JSON.stringify({count: rows.length, body_count: plan.body_count, apply: args.includes('--apply')}));
  if (!args.includes('--apply')) return;
  const aws = (...params) => JSON.parse(execFileSync('aws', [...params, '--profile', profile,
    '--region', region, '--output', 'json'], {encoding: 'utf8', timeout: 60000}));
  const table = aws('lambda', 'get-function-configuration', '--function-name', 'pothole-reporter-central',
    '--query', 'Environment.Variables.TENDERS_TABLE');
  if (table !== 'pothole-reporter-central-tenders') throw Error('Unexpected configured table; refusing writes');
  const before = aws('dynamodb', 'scan', '--table-name', table, '--consistent-read', '--select', 'COUNT', '--limit', '1');
  if (before.Count || before.LastEvaluatedKey) throw Error('Bootstrap requires an empty table; preserve existing rows');
  const receipt = {table, source_sha256: resource.sha256, started_at: new Date().toISOString(), written: 0, complete: false};
  const save = () => fs.writeFileSync(path.join(out, 'receipt.json'), JSON.stringify(receipt, null, 2));
  save();
  for (let index = 0; index < rows.length; index += 25) {
    let pending = rows.slice(index, index+25).map(row => ({PutRequest: {Item: toItem(row)}}));
    for (let attempt = 0; pending.length && attempt < 8; attempt++) {
      const result = aws('dynamodb', 'batch-write-item', '--request-items', JSON.stringify({[table]: pending}));
      const retry = result.UnprocessedItems?.[table] || [];
      receipt.written += pending.length - retry.length;
      pending = retry;
      save();
      if (pending.length) await new Promise(resolve => setTimeout(resolve, Math.min(5000, 100 * 2 ** attempt)));
    }
    if (pending.length) throw Error(`Unprocessed writes remain; inspect ${out}/receipt.json before resuming`);
    console.log(`Seeded ${receipt.written}/${rows.length}`);
  }
  let key, count = 0;
  do {
    const extra = key ? ['--exclusive-start-key', JSON.stringify(key)] : [];
    const page = aws('dynamodb', 'scan', '--table-name', table, '--select', 'COUNT', '--consistent-read', '--no-paginate', ...extra);
    count += page.Count; key = page.LastEvaluatedKey;
  } while (key);
  if (count !== rows.length) throw Error(`Read-back count ${count} differs from planned ${rows.length}`);
  receipt.complete = true; receipt.verified_count = count; receipt.completed_at = new Date().toISOString(); save();
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(error => {console.error(error.message); process.exitCode = 1;});
}
