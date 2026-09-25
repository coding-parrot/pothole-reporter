import assert from 'node:assert/strict';
import test from 'node:test';
import { createDynamoRepository } from '../service/dynamo-repository.mjs';

function fixture() {
  const items = new Map();
  const client = { async send(command) {
    const input = command.input;
    if (command.constructor.name === 'PutCommand') {
      if (items.has(input.Item.id) && input.ConditionExpression) {
        const error = new Error('already exists');
        error.name = 'ConditionalCheckFailedException'; throw error;
      }
      items.set(input.Item.id, structuredClone(input.Item)); return {};
    }
    if (command.constructor.name === 'GetCommand') return {Item:items.get(input.Key.id)};
    throw new Error(`Unexpected ${command.constructor.name}`);
  }};
  return {items, repo:createDynamoRepository({client,tables:{control:'control'}})};
}

test('receipt written by detection can be read by reporting under the same key', async()=>{
  const {repo,items}=fixture(); const id='a'.repeat(64);
  await repo.putReceipt({id,install_id:'test',image_hash:'b'.repeat(64),expiresAt:Date.now()+60000});
  assert.ok(items.has(`RECEIPT#${id}`));
  assert.equal((await repo.getReceipt(id)).install_id,'test');
});

test('unexpired legacy receipt is recovered without deleting the original', async()=>{
  const {repo,items}=fixture(); const id='c'.repeat(64);
  items.set(id,{id,install_id:'test',client_observation_id:'obs',image_hash:'d'.repeat(64),
    expires_at:Math.ceil(Date.now()/1000)+60});
  assert.equal((await repo.getReceipt(id)).client_observation_id,'obs');
  assert.ok(items.has(`RECEIPT#${id}`)); assert.ok(items.has(id));
});

test('legacy lookup cannot expose unrelated control records or revive expired receipts', async()=>{
  const {repo,items}=fixture();
  items.set('CONFIG',{secret:'not a receipt'});
  assert.equal(await repo.getReceipt('CONFIG'),null);
  const id='e'.repeat(64); items.set(id,{id,expires_at:1});
  assert.equal(await repo.getReceipt(id),null);
  assert.ok(!items.has(`RECEIPT#${id}`));
});
