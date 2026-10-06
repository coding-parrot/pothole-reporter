import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { wardRosterOf } from "../service/geolocation.mjs";
import { matchTender } from "../service/tenders.mjs";
import {
  WARD_TENDER_LIMIT, elsewhere, localityKeys, matchWardTenders, offeredFor, sameLocality, titleNameKeys,
  titledWardKeys, wardNumbers,
} from "../service/ward-tenders.mjs";
import { loadBodyTenders, loadWardNames } from "../tools/ward-tender-vocabulary.mjs";
import { CASES, GANDHI_NAGAR } from "./ward-tender-cases.mjs";

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

// ------------------------------------------------- a locality's name is not a place
//
// A ward's name is the register's and a tender that attaches it to its own ward marker
// is about that ward. A locality's name (Gandhi Nagar, Ambedkar Colony, Vinayaka Layout)
// is one of many across the city, so a tender matched on it has to be anchored.

test("Gandhi Nagar, Munnekolala: the Gandhinagaras of Yelahanka and Kengeri are not answered", () => {
  const point = { lat: GANDHI_NAGAR.lat, lng: GANDHI_NAGAR.lng };
  const real = (title) => BENGALURU.find((row) => row.title.startsWith(title));
  assert.equal(real(GANDHI_NAGAR.yelahanka).location, "BBMP Yelahanka Division");
  assert.equal(real(GANDHI_NAGAR.kengeri).location, "BBMP Kengeri Rajarajeshwarinagar");
  const found = matchWardTenders({
    wardName: GANDHI_NAGAR.ward, localities: localitiesOf(GANDHI_NAGAR.address), tenders: BENGALURU,
    point, roster: ROSTER, limit: Infinity,
  });
  assert.deepEqual(titles(found).sort(), [...GANDHI_NAGAR.expected].sort());
  assert.ok(found.every((entry) => entry.match_basis === "ward name Munnenkolalu"));
  // Each rule on its own. With no roster there is no geography, and the ward's own
  // tenders (Mahadevapura Division) are what say the two are elsewhere.
  const byDivision = matchWardTenders({
    wardName: GANDHI_NAGAR.ward, localities: ["Gandhi Nagar"], limit: Infinity,
    tenders: [real(GANDHI_NAGAR.expected[0]), real(GANDHI_NAGAR.yelahanka), real(GANDHI_NAGAR.kengeri)],
  });
  assert.deepEqual(titles(byDivision), [GANDHI_NAGAR.expected[0]]);
  // With no ward tender at all, the name is in two divisions: it says nothing.
  const ambiguous = matchWardTenders({
    wardName: GANDHI_NAGAR.ward, localities: ["Gandhi Nagar"], limit: Infinity,
    tenders: [real(GANDHI_NAGAR.yelahanka), real(GANDHI_NAGAR.kengeri)],
  });
  assert.deepEqual(ambiguous, []);
  // Alone in the index each would be unambiguous, and each still names somewhere the
  // register can place far from the point: "kempegowda ward ... of yelahanka Sub
  // division", "Kengeri Kote".
  for (const title of [GANDHI_NAGAR.yelahanka, GANDHI_NAGAR.kengeri]) {
    assert.equal(elsewhere(real(title).title, point, ROSTER, { wide: true }), true, title);
    assert.deepEqual(matchWardTenders({
      wardName: GANDHI_NAGAR.ward, localities: ["Gandhi Nagar"], tenders: [real(title)], point, roster: ROSTER,
    }), [], title);
  }
});

test("a locality tender is kept only in a division that serves the point's ward", () => {
  const ward = tender("Asphalting of roads in Ward No.105 Munnekolala", { tender_number: "W", location: "BBMP Mahadevapura Division" });
  const here = tender("Asphalting of roads in Gandhi Nagar and surrounding area", { tender_number: "HERE", location: "BBMP Mahadevapura Division" });
  const there = tender("Resurfacing of roads in Gandhinagar 2nd stage", { tender_number: "THERE", location: "BBMP Yelahanka Division" });
  const found = matchWardTenders({ wardName: "Munnenkolalu", localities: ["Gandhi Nagar"], tenders: [there, here, ward] });
  assert.deepEqual(found.map((entry) => entry.tender_number), ["W", "HERE"]);
  assert.equal(found[1].match_basis, "locality Gandhi Nagar");
});

test("with no tender for the ward, a locality tender is kept only if its name is in one division", () => {
  const one = tender("Asphalting of roads in Thubarahalli", { tender_number: "ONE", location: "BBMP Mahadevapura Division" });
  const again = tender("Improvements to drains and roads in Tubarahalli extension", { tender_number: "AGAIN", location: "BBMP Mahadevapura Division" });
  const other = tender("Asphalting of roads in Thubarahalli and surrounding area", { tender_number: "OTHER", location: "BBMP Dasarahalli Division" });
  const unambiguous = matchWardTenders({ wardName: "Kundalahalli", localities: ["Thubarahalli Palya"], tenders: [one, again] });
  assert.deepEqual(unambiguous.map((entry) => entry.tender_number).sort(), ["AGAIN", "ONE"]);
  assert.deepEqual(matchWardTenders({ wardName: "Kundalahalli", localities: ["Thubarahalli Palya"], tenders: [one, again, other] }), []);
});

test("a locality tender whose title names a ward the register places far away is left out", () => {
  const point = { lat: GANDHI_NAGAR.lat, lng: GANDHI_NAGAR.lng };
  const kept = (title) => matchWardTenders({ wardName: "Munnenkolalu", localities: ["Gandhi Nagar"], tenders: [tender(title)], point, roster: ROSTER }).length;
  assert.equal(kept("Asphalting of roads in Gandhi Nagar and surrounding area"), 1, "nothing in the title is placed");
  assert.equal(kept("Asphalting of roads in Gandhi Nagar near Kengeri"), 0, "a plain mention of a register ward");
  assert.equal(kept("Asphalting of roads in Gandhi Nagar in kempegowda ward no 01"), 0, "an old ward found inside register names");
  assert.equal(kept("Asphalting of roads in Gandhi Nagar of yelahanka Sub division"), 0, "a sub division");
  assert.equal(kept("Asphalting of roads in Gandhi Nagar in Mahadevapura Division"), 1, "a unit that is here");
  assert.equal(kept("Asphalting of roads in Gandhi Nagar in Rajarajeshwarinagara Division"), 0, "a unit across the city");
  assert.equal(kept("Asphalting of roads in Gandhi Nagar and Marathahalli"), 1, "a neighbouring ward");
  // A common word inside a register name is not a place: Lakshmi Devi Nagar is a ward.
  assert.equal(kept("Asphalting of roads in Gandhi Nagar and Lakshmi layout"), 1);
});

test("a title's ward markers are read as a list, names and old numbers both", () => {
  const names = (title) => titledWardKeys(title).map((entry) => entry.key);
  const numbers = (title) => [...wardNumbers(title)].sort((a, b) => a - b);
  const list = "Pothole filling IN Ward No 57 Rajmahal Gutthalli, 58- Kadumalleshwara, 59- Subramanyanagar & 60- Gayathrinagar in Malleshwaram Sub Division";
  assert.deepEqual(numbers(list), [57, 58, 59, 60]);
  for (const ward of ["Rajamahal", "Subramanyanagara", "Gayathri Nagara"]) {
    assert.ok(localityKeys(ward).some((key) => titledWardKeys(list).some((name) => sameLocality(key, name))), ward);
  }
  assert.ok(!names(list).includes("maleshvaram"), "the sub division is not one of the wards");
  const mixed = "Comprehensive Development of roads in ward No 147 Adugodi, 148 Ejipura, 151 Koramangala and 173 Jakkasandra in BTM Layout";
  assert.deepEqual(numbers(mixed), [147, 148, 151, 173]);
  assert.ok(names(mixed).includes(localityKeys("Jakkasandra")[0].key));
  assert.deepEqual(numbers("Improvements at Tata Silk Farm in Ward No-188 Yediyur (Old Ward No-167)."), [167, 188]);
  assert.deepEqual(numbers("Roads in 7th Block Koramangala in ward No. 176 (Old No.147) Adugodi"), [147, 176]);
  assert.deepEqual(numbers("5th Block HBR Layout Ward No-76/24 Hennur"), [24, 76]);
  assert.deepEqual(numbers("Asphalting to Bad Reaches in Ward 182, 183 & 186 in Shakambari Nagar Sub Division"), [182, 183, 186]);
  assert.deepEqual(numbers("Filling of potholes in ward No. 173, 174 and 175 in Koramangala Sub Division"), [173, 174, 175]);
  assert.deepEqual(numbers("Roads in Coxtown in ward no -108, Coxtown (Block no 22, 23, 25)"), [108]);
  assert.deepEqual(numbers("Pothole Filling Works in Ward No. 213-Jaraganahalli for the year 2024-25 in Bommanahalli Division."), [213]);
  assert.deepEqual(numbers("Resurfacing of 1st main 24th cross in kempegowda ward no 01 of yelahanka Sub division"), [1]);
  assert.deepEqual(numbers("Improvements to roads at 5th cross in ward 5th block from 27th Cross to 7th Cross"), []);
  assert.deepEqual(numbers("Census Block Number 120 in Vijinapura Ward No.89"), [89]);
  // The register's own number is never an input: only titles are read.
  assert.deepEqual(numbers("Improvements to roads in Wad no 113 Hoysalanagara"), [113]);
});

test("a ward with no tender of its own takes a mention only if its name is in one division", () => {
  // Ambedkarnagar is a register ward of the Central corporation. The index has an
  // Ambedkar Nagar in four divisions, none of them filed under a ward of that name.
  const tenders = BENGALURU.filter((row) => /ambedkar\s*nagar/i.test(row.title));
  assert.equal(new Set(tenders.map((row) => row.location)).size, 4);
  const ward = ROSTER.find((entry) => entry.name === "Ambedkarnagar");
  const point = { lat: (ward.bbox[1] + ward.bbox[3]) / 2, lng: (ward.bbox[0] + ward.bbox[2]) / 2 };
  assert.deepEqual(matchWardTenders({ wardName: "Ambedkarnagar", tenders: BENGALURU, point, roster: ROSTER, limit: Infinity }), []);
  // Kundalahalli has no tender of its own either, and is in one division.
  const kundalahalli = matchWardTenders({
    wardName: "Kundalahalli", tenders: BENGALURU, point: { lat: 12.95906, lng: 77.7207 }, roster: ROSTER, limit: Infinity,
  });
  assert.equal(kundalahalli.length, 2);
});

test("a numbered part of a layout does not take another part's tender", () => {
  const second = tender("Improvements to drains and Roads in 10th Cross and Surrounding area in J P Nagar 2nd Phase in Ward No-187 Sarakki", { tender_number: "P2" });
  const sixth = tender("Improvements to Roads and drains in 16th Cross in JP Nagar 6th Phase in ward No-187 Sarakki", { tender_number: "P6" });
  const both = tender("Improvements to Roads in 4th cross 7th Main in JP Nagar 3rd Phase and 5th Cross in JP Nagar 2nd Phase", { tender_number: "P3+2" });
  const before = tender("Improvements to drains and Roads in 16th Cross and Surrounding area in 4th Phase J P Nagara", { tender_number: "P4" });
  const list = tender("Improvements of drains and roads in Jayanagara 1st and 2nd Block in Ward No 163", { tender_number: "B1+2" });
  const whole = tender("Asphalting to bad reaches in Ward No.184, 185 & 187 J P Nagara", { tender_number: "ALL" });
  const tenders = [second, sixth, both, before, list, whole];
  const numbers = (locality) => matchWardTenders({ wardName: "Marenahalli South", localities: [locality], tenders, limit: 10 })
    .map((entry) => entry.tender_number).sort();
  assert.deepEqual(numbers("JP Nagar 2nd Phase"), ["ALL", "P2", "P3+2"]);
  assert.deepEqual(numbers("JP Nagar 4th Phase"), ["ALL", "P4"]);
  assert.deepEqual(numbers("J.P Nagar"), ["ALL", "P2", "P3+2", "P4", "P6"], "the whole layout takes every part");
  // The ward a title files the work under is not where the work is: "2nd Phase in Ward
  // No 185 J P Nagara" is 2nd Phase work.
  const filed = tender("Improvements to drains and Roads in 2nd cross in J P Nagar 2nd Phase in Ward No 185 J P Nagara", { tender_number: "P2/185" });
  const under = (locality) => matchWardTenders({ wardName: "Marenahalli South", localities: [locality], tenders: [filed, whole] })
    .map((entry) => entry.tender_number).sort();
  assert.deepEqual(under("JP Nagar 1st Phase"), ["ALL"]);
  // The same with the marker written "Ward No-185", which splits the title at the dash.
  const dashed = tender("Improvements to drains and Roads in 2nd main road and Surrounding area in 4th Phase J P Nagara in Ward No-185 J P Nagara", { tender_number: "P4/185" });
  assert.deepEqual(matchWardTenders({ wardName: "Shakambarinagara", localities: ["LIC Colony", "JP Nagar 1st Phase", "JP Nagar"], tenders: [dashed] }), []);
  assert.equal(matchWardTenders({ wardName: "Shakambarinagara", localities: ["JP Nagar 4th Phase"], tenders: [dashed] }).length, 1);
  assert.deepEqual(under("JP Nagar 2nd Phase"), ["ALL", "P2/185"]);
  assert.deepEqual(numbers("Jayanagar 2nd Block"), ["B1+2"]);
  assert.deepEqual(numbers("Jayanagar 5th Block"), []);
  // The real case of 6 Oct 2026 is untouched: no part is named beside BEML layout.
  assert.deepEqual(localityKeys("BEML Layout 6th Stage").map((entry) => entry.part), ["stage 6"]);
});

test("a ward's name used for a place in another division's ward is left out", () => {
  // Kothanur is a ward of the South corporation and a village in the north-east. The
  // tender is K R Puram's and names no ward of its own.
  const south = ROSTER.find((ward) => ward.name === "Kothanur");
  const point = { lat: (south.bbox[1] + south.bbox[3]) / 2, lng: (south.bbox[0] + south.bbox[2]) / 2 };
  const north = BENGALURU.filter((row) => /kothanur Balaji layout/i.test(row.title));
  assert.equal(north.length, 1);
  assert.equal(north[0].location, "BBMP K R Puram Mahadevapura");
  // It is the only Kothanur in the index, so the South ward has nothing.
  assert.deepEqual(matchWardTenders({ wardName: "Kothanur", tenders: BENGALURU, point, roster: ROSTER, limit: Infinity }), []);
  // The same mention is kept for a point K R Puram's division does work near: K
  // Narayanapura, the next ward to the northern Kothanur.
  const beside = ROSTER.find((ward) => ward.name === "K Narayanapura");
  const there = { lat: (beside.bbox[1] + beside.bbox[3]) / 2, lng: (beside.bbox[0] + beside.bbox[2]) / 2 };
  const kept = matchWardTenders({ wardName: "K Narayanapura", localities: ["Kothanur"], tenders: BENGALURU, point: there, roster: ROSTER, limit: Infinity });
  assert.ok(titles(kept).includes(north[0].title), titles(kept).join("\n"));
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
