import test from 'node:test';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import {prepareTenders, toItem} from '../tools/seed-tenders.mjs';
const fixture = (rows) => {
  const bytes = Buffer.from(JSON.stringify({pack_id:'in-ka-tenders',state_code:'KA',tenders:rows}));
  return [{sha256:crypto.createHash('sha256').update(bytes).digest('hex'),review_after:'2099-01-01'}, bytes];
};
const row = {b:'251893',tn:'TEST/1',t:'Resurfacing of Rose Road',loc:'Town',c:'UNVERIFIED PERSON'};
test('seed strips unverified contractor identity and marks liability unverified', () => {
  const [item] = prepareTenders(...fixture([row]));
  assert.equal(item.contractor, null); assert.equal(item.award_verified, false);
  assert.deepEqual(toItem(item).contractor, {NULL:true});
});
test('seed rejects wrong hash, expiry, malformed rows and duplicate keys', () => {
  const [resource, bytes] = fixture([row]);
  assert.throws(()=>prepareTenders({...resource,sha256:'bad'},bytes));
  assert.throws(()=>prepareTenders({...resource,review_after:'2000-01-01'},bytes));
  assert.throws(()=>prepareTenders({...resource,review_after:'not-a-date'},bytes));
  assert.throws(()=>prepareTenders(...fixture([{...row,b:''}])));
  assert.throws(()=>prepareTenders(...fixture([row,row])));
});
test('seed excludes unrelated footpath work', () => {
  assert.deepEqual(prepareTenders(...fixture([{...row,t:'Construction of footpath at Rose Road'}])), []);
});
