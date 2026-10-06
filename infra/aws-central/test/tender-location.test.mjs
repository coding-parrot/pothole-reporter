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

// Seen live on 6 Oct 2026: a pothole on "2nd Cross Road, Gandhi Nagar, Munnenkolalu" was
// given the tender for "Surabhi layout 2nd cross roads", across the city. Every layout
// has a 2nd Cross and an 8th Main, so a numbered or generic road name is not evidence on
// its own: the title must also name the place the road is in.
const blr = (title) => ({tender_number:`T/${title.length}`,title,location:'BBMP Kengeri Rajarajeshwarinagar'});
test('a numbered cross road does not match the same number in another layout', () => {
  const result = matchTender('2nd Cross Road, Gandhi Nagar, Munnenkolalu, Bengaluru, 560037',
    [blr('Resurfacing of roads at Surabhi layout 2nd cross roads in Kengeri')]);
  assert.equal(result.tender, null);
  assert.equal(result.reason, 'no_location_match');
});
test('a numbered main road does not match on its number alone', () => {
  assert.equal(matchTender('8th Main Road, Thubarahalli Palya, Kundalahalli, Bengaluru, 560066',
    [blr('Asphalting of 8th main road in Vijayanagar')]).tender, null);
});
test('a generic service road does not match any service road', () => {
  assert.equal(matchTender('Service Road, Ferns Habitat, Mahadevapura, Bengaluru, 560048',
    [blr('Improvements to service road near Hebbal flyover')]).tender, null);
});
test('a numbered road still matches when the title names its locality', () => {
  const result = matchTender('2nd Cross Road, Gandhi Nagar, Munnenkolalu, Bengaluru, 560037',
    [blr('Resurfacing of 2nd cross road in Munnenkolalu')]);
  assert.ok(result.tender, result.reason);
});
test('a named road still matches on its own name', () => {
  assert.ok(matchTender('Halasuru Road, Hanumanthappa Layout, Bengaluru, 560042',
    [blr('Improvements of Roads and Drains at Halasuru Road Crossess in Ward No121 Halasuru')]).tender);
});
