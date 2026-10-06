import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { wardRosterOf } from "../service/geolocation.mjs";
import { matchTender } from "../service/tenders.mjs";
import {
  WARD_TENDER_LIMIT, elsewhere, localityKeys, matchWardTenders, offeredFor, sameLocality, titleNameKeys,
  titledWardKeys,
} from "../service/ward-tenders.mjs";
import { loadBodyTenders, loadWardNames } from "../tools/ward-tender-vocabulary.mjs";
import { CASES } from "./ward-tender-cases.mjs";

// On 6 Oct 2026, 40 real Bengaluru pothole locations went through the street matcher
// with the 795 Bengaluru road tenders: 1 matched. Tenders for those exact localities
// exist, but they name a ward or a locality, never the street. These are the cases from
// that run, against the same tender rows and the packaged ward polygons.

const BENGALURU = loadBodyTenders("BLR").rows;
const WARDS = JSON.parse(readFileSync(new URL("../../../data/karnataka-ward-geometry.json", import.meta.url), "utf8"));
const ROSTER = wardRosterOf(WARDS, "20G3041");
const titles = (found) => found.map((tender) => tender.title);
const same = (left, right) => localityKeys(left).some((a) => localityKeys(right).some((b) => sameLocality(a, b)));
const tender = (title, extra = {}) => ({
  tender_number: `T/${title.length}/${title.slice(-12)}`, title, location: "BBMP Test Division",
  published: "01-01-2025", source_name: "test index", source_url: "https://example.test/pack.json", ...extra,
});

const localitiesOf = (address) => [address.neighbourhood, address.hamlet, address.quarter, address.suburb, address.village]
  .filter((value, index, all) => value && all.indexOf(value) === index);

test("the pack still holds the Bengaluru rows the run used", () => {
  assert.equal(loadBodyTenders("BLR").raw, 797);
  assert.equal(BENGALURU.length, 795);
  assert.equal(ROSTER.length, 369);
});

for (const [name, point] of Object.entries(CASES)) {
  test(`${name}: the real tenders are ward tenders while the street matcher finds none`, () => {
    assert.equal(matchTender(point.street, BENGALURU).tender, null, "no street-level tender, as on 6 Oct 2026");
    const found = matchWardTenders({
      wardName: point.ward, localities: localitiesOf(point.address), tenders: BENGALURU,
      point: { lat: point.lat, lng: point.lng }, roster: ROSTER,
    });
    for (const title of point.expected) assert.ok(titles(found).includes(title), `${title}\nfound:\n${titles(found).join("\n")}`);
    assert.ok(found.length <= WARD_TENDER_LIMIT);
    for (const entry of found) {
      assert.deepEqual(Object.keys(entry), ["tender_number", "title", "location", "published", "source_name",
        "source_url", "match_basis", "scope"]);
      assert.equal(entry.scope, "ward");
      assert.match(entry.match_basis, /^(ward name|locality) \S/);
      assert.match(entry.source_url, /^https:\/\//);
    }
  });
}

test("a ward with no tender returns an empty list", () => {
  // Agaram (KGIS ward 24, Central) and its neighbourhood Gowthamapura: nothing in the index.
  assert.deepEqual(matchWardTenders({
    wardName: "Agaram", localities: ["Gowthamapura", "Agaram"], tenders: BENGALURU,
    point: { lat: 12.97298, lng: 77.62247 }, roster: ROSTER,
  }), []);
  assert.deepEqual(matchWardTenders({ wardName: "Munnenkolalu", tenders: [] }), []);
  assert.deepEqual(matchWardTenders({ wardName: null, localities: [], tenders: BENGALURU }), []);
  assert.deepEqual(matchWardTenders(), []);
});

test("ward numbers never cause a match", () => {
  // KGIS numbers Cox Town 10. The old BBMP ward 10 was somewhere else entirely.
  const elsewhereTen = [
    tender("Improvements to roads and drains in ward no 10 Somewhere Else"),
    tender("Asphalting of roads in Ward No.10"),
    tender("Pothole filling works in ward 10 and ward 41"),
    tender("Resurfacing of 10th cross road in ward number 10, Dasappa Layout"),
  ];
  for (const wardName of ["Cox Town", "10 - Cox Town", "10", "Ward 10", "Ward No 10"]) {
    assert.deepEqual(matchWardTenders({ wardName, localities: ["10", "Ward 10"], tenders: elsewhereTen }), [], wardName);
  }
  // The same ward under its old number is found by its name alone.
  const byName = matchWardTenders({ wardName: "10 - Cox Town", tenders: [tender("Asphalting of roads in Ward No -108, Coxtown")] });
  assert.equal(byName.length, 1);
  assert.equal(byName[0].match_basis, "ward name Cox Town");
  assert.deepEqual(titledWardKeys("Asphalting of roads in Ward No.10").map((entry) => entry.key), [], "a number is not a name");
});

test("generic words never match", () => {
  const generic = [
    tender("Improvements to roads and drains in ward no 5 and surrounding area"),
    tender("Asphalting of main road and cross roads in layout, nagar and colony limits of Bengaluru"),
    tender("Resurfacing of 2nd main road, 3rd cross, 1st stage, 2nd block, east zone"),
    tender("Development of roads in BDA Layout, AECS layout and R T Nagar"),
  ];
  const names = ["Ward", "Road", "Main Road", "Layout", "Nagar", "Colony", "Cross", "2nd Stage", "1st Block",
    "Bengaluru", "East Zone", "Central", "Area", "Old Town", "New Extension", "Temple Ward"];
  for (const name of names) {
    assert.deepEqual(localityKeys(name), [], `${name} is not a name`);
    assert.deepEqual(matchWardTenders({ wardName: name, localities: names, tenders: generic }), [], name);
  }
  // A name that has a generic word in it needs its own part too.
  assert.deepEqual(matchWardTenders({ wardName: "HSR Layout", localities: ["J.P Nagar"], tenders: generic }), []);
  assert.equal(matchWardTenders({ wardName: "HSR Layout", tenders: [tender("Pothole filling in Ward No.221 - HSR Layout")] }).length, 1);
});

test("spellings of one name are one name", () => {
  for (const [left, right] of [
    ["Munnenkolalu", "Munnekolala"], ["Thubarahalli", "Tubarahalli"], ["Cox Town", "Coxtown"],
    ["Bellanduru", "Bellandur"], ["Banaswadi", "Banasawadi"], ["Nagavara", "Nagawara"], ["Hoodi", "Hoody"],
    ["Lingarajpura", "Lingarajapuram"], ["K.R Pura", "K R Puram"], ["Dodda Nekkundi", "Doddanekundi"],
    ["Kormangala East", "Koramangala"], ["Yelachenahalli", "Yelechenahalli"], ["Kadugodi", "Kadugudi"],
    ["Hebbal", "Hebbala"], ["Jeevan Bhimanagar", "Jeevanbhimangara"], ["Doddakannelli Ward", "Doddakannalli"],
    ["Vignananagara", "Vignan Nagara"], ["Shettihalli", "Shettyhalli"], ["49 - Doddakannelli Ward", "Doddakannelli"],
    ["Chikka Adugodi", "Chikkadugodi"], ["J.P Nagar", "JP Nagara"], ["A Narayanapura", "A.Narayanapaura"],
  ]) assert.ok(same(left, right), `${left} / ${right}`);
});

test("another place with a near name is another place", () => {
  for (const [left, right] of [
    ["Agara", "Agaram"], ["Jakkasandra", "Lakkasandra"], ["Shanthinagar", "Shakthinagar"],
    ["Chokkasandra", "Chikkasandra"], ["Herohalli", "Harohalli"], ["Mallasandra", "Mylasandra"],
    ["Amruthahalli", "Marathahalli"], ["Shivanagara", "Shivajinagar"], ["Kodihalli", "Kodigehalli"],
    ["BEML Layout", "BM Layout"], ["Vidyaranyapura", "Vidyaranyanagara"], ["Hosahalli", "Hosakerehalli"],
    ["Nagapura", "Nagavara"], ["Channasandra", "Chennasandra"], ["Kammanahalli", "Bommanahalli"],
    ["Subramanyapura", "Subramanyanagara"], ["J.P Nagar", "R.T Nagar"], ["Hongasandra", "Singasandra"],
    ["A Adugodi", "Adugodi"], ["A Narayanapura", "Narayanapura"], ["K Narayanapura", "A Narayanapura"],
    ["Yamalur", "Yemalur"], ["Anjanapura", "Ajanapura"],
  ]) assert.ok(!same(left, right), `${left} / ${right}`);
});

test("a name inside another name, a road's name or a larger unit's is not offered", () => {
  const offered = (title, name) => localityKeys(name)
    .some((key) => titleNameKeys(title).some((entry) => !entry.lead && offeredFor(key, entry)));
  assert.ok(offered("Improvements to roads at Horamavu agara Jyothi Nagara Colony in ward No.87 Horamavu", "Horamavu"));
  assert.ok(!offered("Improvements to roads at Horamavu agara Jyothi Nagara Colony in ward No.87 Horamavu", "Agara"));
  assert.ok(!offered("Asphalting of roads in Old Guddadahalli", "Guddadahalli"));
  assert.ok(offered("Asphalting of roads in Old Guddadahalli", "Old Guddadahalli"));
  assert.ok(!offered("Improvements to roads in Agrahara Dasarahalli", "Dasarahalli"));
  assert.ok(!offered("Resurfacing of roads in A. Narayanapura", "Narayanapura"));
  assert.ok(!offered("Resurfacing of roads in Chowdeshwari Nagar", "Chowdeshwari ward"));
  assert.ok(offered("Resurfacing of roads in Chowdeshwari ward no-02", "Chowdeshwari ward"));
  assert.ok(!offered("Asphalting of Hennur Main Road from the junction", "Hennur"));
  assert.ok(!offered("Asphalting of Anandapura road", "Anandapura"));
  assert.ok(!offered("Widening of Kadri Kambla road of 30th Kodialbail ward in MCC limits", "KADRI NORTH"));
  assert.ok(offered("Widening of Kadri Kambla road of 30th Kodialbail ward in MCC limits", "KODIYALBAIL"));
  assert.ok(!offered("Pothole filling in Ward No. 214-Puttenahalli in Bommanahalli Division", "Bommanahalli"));
  assert.ok(offered("Pothole filling in Ward No. 214-Puttenahalli in Bommanahalli Division", "Puttenahalli"));
  assert.ok(offered("Improvements to roads at Bhattarahalli Janatha Colony in ward No-91 K R Pura", "Bhattarahalli"));
  assert.ok(!offered("IMPROVEMENTS OF ROADS AND DRAINS AT AMRUTHANAGAR AND SURROUNDING AREAS IN BYATARAYANAPURA CONSTITUENCY.", "Byatarayanapura"));
  assert.ok(!offered("Improvements of roads at Cauvery Layout and surrounding areas in Byatarayanapura const", "Byatarayanapura"),
    "a title cut at 150 characters");
  // In a pothole filling title a unit is offered where it is all the title says about the place.
  assert.ok(offered("Filling of potholes in ward No. 173, 174 and 175 in Koramangala Sub Division", "Kormangala East"));
  assert.ok(offered("Pothole filling works in ward limits of Mahadevapura Assembly Constituency for the year 2024-25", "Mahadevapura"));
  assert.ok(offered("Maintenance of roads and pot holes filling in ward No.46,47,48 & 49 Nagapura sub division", "Nagapura"));
  assert.ok(offered("Improvements to roads in Munnekolala colony at Munnekolala ward no.105", "Munnenkolalu"));
});

test("a ward that is one side of a place does not take the other side's tender", () => {
  // Mangaluru: Kunjathbail North and Kunjathbail South are two wards.
  const north = tender("Providing concrete pavement near KHB colony of 13 Kunjathbail north ward in MCC limits");
  const plain = tender("Asphalting of roads in Kunjathbail in MCC limits");
  assert.deepEqual(titles(matchWardTenders({ wardName: "KUNJATHBAIL SOUTH", tenders: [north, plain] })), [plain.title]);
  assert.deepEqual(titles(matchWardTenders({ wardName: "KUNJATHBAIL NORTH", tenders: [north, plain] })).sort(),
    [north.title, plain.title].sort());
  assert.deepEqual(titles(matchWardTenders({ wardName: "Kunjathbail", tenders: [north, plain] })).sort(),
    [north.title, plain.title].sort(), "a ward with no side takes either");
  // A title that says the name both ways is taken.
  const both = tender("Asphalting of roads in Jayanagar West and in Jayanagar 4th block");
  assert.equal(matchWardTenders({ wardName: "Jayanagar East", tenders: [both] }).length, 1);
  assert.equal(matchWardTenders({ wardName: "Jayanagar East", tenders: [tender("Asphalting of roads in Jayanagar West")] }).length, 0);
});

test("a ward named for two places is found under either", () => {
  // Mysuru's "Gokulam,Brudhavana".
  assert.deepEqual(localityKeys("Gokulam,Brudhavana").map((entry) => entry.key), ["gokulam", "brudavan"]);
  assert.equal(matchWardTenders({ wardName: "Gokulam,Brudhavana", tenders: [tender("Asphalting of roads in Gokulam 3rd stage")] }).length, 1);
});

test("a name behind another name counts only for a point in both places", () => {
  const title = "Improvements to Roads and Drain at Doddigunta Coxtown and Surrounding area in Ward no.108";
  const both = matchWardTenders({ wardName: "Cox Town", localities: ["Doddigunta"], tenders: [tender(title)] });
  assert.deepEqual(both.map((entry) => entry.match_basis), ["ward name Cox Town"]);
  const coxOnly = matchWardTenders({ wardName: "Cox Town", localities: ["Sindhi Colony"], tenders: [tender(title)] });
  assert.deepEqual(coxOnly, [], "Doddigunta Coxtown could be a place of its own");
  const agara = tender("Improvements to roads at Horamavu agara Jyothi Nagara Colony in ward No.87 Horamavu");
  assert.deepEqual(matchWardTenders({ wardName: "Agara", localities: ["HSR Layout"], tenders: [agara] }), []);
  assert.equal(matchWardTenders({ wardName: "Horamavu", tenders: [agara] }).length, 1);
});

test("only road-surface tenders are offered", () => {
  const found = matchWardTenders({ wardName: "Munnenkolalu", tenders: [
    tender("Construction of footpath at Munnekolala"),
    tender("Providing street lights in Munnekolala ward"),
    tender("Asphalting of roads in Munnekolala"),
  ] });
  assert.deepEqual(titles(found), ["Asphalting of roads in Munnekolala"]);
});

test("a title naming the ward comes first, then one naming a geocoder locality, then the newest", () => {
  const tenders = [
    tender("Asphalting of roads in Doddigunta", { tender_number: "L-OLD", published: "01-02-2024" }),
    tender("Asphalting of roads in Doddigunta and surrounding area", { tender_number: "L-NEW", published: "01-03-2025" }),
    tender("Asphalting of roads in Ward No -108, Coxtown", { tender_number: "W-OLD", published: "05-01-2024" }),
    tender("Resurfacing of roads in Cox Town", { tender_number: "W-NEW", published: "31-12-2024" }),
    tender("Asphalting of roads at Doddigunta in Ward No -108, Coxtown", { tender_number: "BOTH", published: "01-01-2023" }),
    tender("Asphalting of roads in Jeevanahalli", { tender_number: "NEITHER", published: "01-09-2025" }),
    tender("Asphalting of roads in Cox Town and Frazer Town", { tender_number: "W-UNDATED", published: null }),
  ];
  const found = matchWardTenders({ wardName: "Cox Town", localities: ["Doddigunta", "Cox Town"], tenders });
  assert.deepEqual(found.map((entry) => entry.tender_number), ["BOTH", "W-NEW", "W-OLD", "W-UNDATED", "L-NEW"]);
  assert.deepEqual(found.map((entry) => entry.match_basis), ["ward name Cox Town", "ward name Cox Town",
    "ward name Cox Town", "ward name Cox Town", "locality Doddigunta"]);
  assert.equal(matchWardTenders({ wardName: "Cox Town", localities: ["Doddigunta"], tenders, limit: 10 }).length, 6);
});

test("a namesake across town is left out when the title says which ward its work is in", () => {
  const title = "Improvements to Roads and Drains at Vinayaka layout Doddanekundi ward no.101";
  // Vinayaka Layout is a ward of the West corporation; Doddanekundi is 20 km east of it.
  const west = { lat: 12.9716, lng: 77.5113 };
  const east = { lat: 12.9698, lng: 77.6945 };
  assert.deepEqual(titledWardKeys(title).map((entry) => entry.key).sort(), ["dodanekund", "vinaiakalaioutdodanekund"]);
  assert.equal(elsewhere(title, west, ROSTER), true);
  assert.equal(elsewhere(title, east, ROSTER), false);
  assert.deepEqual(matchWardTenders({ wardName: "Vinayaka Layout", tenders: [tender(title)], point: west, roster: ROSTER }), []);
  assert.equal(matchWardTenders({ wardName: "Dodda Nekkundi", tenders: [tender(title)], point: east, roster: ROSTER }).length, 1);
  // With no roster, or a title that names no ward of its own, nothing is left out.
  assert.equal(matchWardTenders({ wardName: "Vinayaka Layout", tenders: [tender(title)] }).length, 1);
  assert.equal(elsewhere("Asphalting of roads in Vinayaka layout", west, ROSTER), false);
  assert.equal(elsewhere("Asphalting of roads in ward no 12 Nowhere Known", west, ROSTER), false);
});

test("the rules still find what they found on the day they were written", () => {
  // tools/ward-tender-vocabulary.mjs prints the detail. 183 of 369 ward names found a
  // tender on 6 Oct 2026; a rule change that loses a tenth of them needs a second look.
  const wards = loadWardNames("20G");
  assert.equal(wards.length, 369);
  assert.equal(wards.filter((ward) => !localityKeys(ward.name).length).length, 0);
  const covered = wards.filter((ward) => matchWardTenders({ wardName: ward.name, tenders: BENGALURU, limit: 1 }).length);
  assert.ok(covered.length >= 165 && covered.length <= 200, `${covered.length} ward names find a tender`);
});
