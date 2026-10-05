import test from 'node:test';
import assert from 'node:assert/strict';
import {matchTender} from '../service/tenders.mjs';
const row = (title) => ({tender_number:'TEST/ROAD',title,location:'DMA City Corporation Mysuru'});
test('city metadata does not match an unrelated road', () => {
  assert.equal(matchTender('Unknown locality, Mysuru', [row('Resurfacing of Rose Road in Mysuru')]).tender,null);
});
for (let ward = 1; ward <= 30; ward++) test(`same road name in different ward ${ward} is rejected`, () => {
  assert.equal(matchTender(`Rose Road, Ward ${ward}, Mysuru`,
    [row(`Resurfacing of Rose Road in Ward ${ward+1}, Mysuru`)]).tender,null);
});
test('same ward cannot substitute another named road', () => {
  assert.equal(matchTender('Lily Road, Ward 5, Mysuru',
    [row('Resurfacing of Rose Road in Ward 5, Mysuru')]).tender,null);
});
test('explicit ward-wide pothole work remains eligible', () => {
  assert.ok(matchTender('Lily Road, Ward 5, Mysuru',
    [row('Pothole filling throughout Ward 5 in Mysuru')]).tender);
});
