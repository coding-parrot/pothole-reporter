import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {hasRoadSurfaceScope, matchTender} from '../service/tenders.mjs';
import {coverageFailures} from '../../../eval/tender_coverage_gate.mjs';
import {releasePassed} from '../../../tools/harness/release-verdict.mjs';

const read = name => fs.readFileSync(new URL(`../../../${name}`, import.meta.url), 'utf8');
const fixtures = JSON.parse(read('tests/fixtures/tender-scope.json'));
for (const [verdict, rows] of Object.entries(fixtures)) {
  for (const row of rows) test(`scope ${verdict}: ${row.tn}`, () => {
    assert.equal(hasRoadSurfaceScope(row.title), verdict === 'accept', row.title);
  });
}

test('AWS scope implementation stays byte-identical to shipped browser logic', () => {
  const browser = read('static/standalone.js');
  const source = browser.slice(browser.indexOf('  const ROAD_WORK_ACTIONS ='),
    browser.indexOf('  // The optional pack contains only rows')).trimEnd()
    .split('\n').map(line => line.startsWith('  ') ? line.slice(2) : line).join('\n');
  const aws = fs.readFileSync(new URL('../service/tender-scope.mjs', import.meta.url), 'utf8');
  assert.equal(aws.slice(aws.indexOf('const ROAD_WORK_ACTIONS ='), aws.indexOf('\n\nexport {')), source);
});

const cases = JSON.parse(read('eval/tender_cases_v2.json')).cases;
test('evaluation has at least 100 distinct cases with both positive and negative labels', () => {
  assert.ok(cases.length >= 100);
  assert.equal(new Set(cases.map(c => c.id)).size, cases.length);
  assert.ok(cases.filter(c => c.expected_tender_number).length >= 50);
  assert.ok(cases.filter(c => !c.expected_tender_number).length >= 50);
});
for (const c of cases) test(`AWS regression: ${c.id}`, () => {
  for (const candidates of [c.candidates, [...c.candidates].reverse()]) {
    assert.equal(matchTender(c.address, candidates).tender?.tender_number ?? null,
      c.expected_tender_number);
  }
});
test('empty production cannot pass even with passing synthetic tests', () => {
  assert.equal(coverageFailures({production: {consistent_table_count: 0}}).length, 1);
  assert.equal(coverageFailures({}).length, 1);
});
test('nonempty production with zero observed matches still fails', () => {
  assert.equal(coverageFailures({production: {consistent_table_count: 100},
    traffic: {resolve_post_requests: 50, tender_matched_requests: 0}}).length, 1);
});

test('known failures and an empty harness selection cannot produce success', () => {
  assert.equal(releasePassed([]), false);
  assert.equal(releasePassed([{name: 'known broken', ok: false}]), false);
  assert.equal(releasePassed([{ok: true}, {ok: false}]), false);
  assert.equal(releasePassed([{ok: true}]), true);
});
