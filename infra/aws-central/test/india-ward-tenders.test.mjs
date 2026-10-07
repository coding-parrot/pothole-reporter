import assert from "node:assert/strict";
import test from "node:test";

import { matchIndiaWardTenders, urbanBodyAt, wardMarkersIn } from "../service/india-ward-tenders.mjs";
import { matchWardTenders, wardNumbers } from "../service/ward-tenders.mjs";

// Ward tenders outside Karnataka. Every title below is one a State portal published in the
// road notice packs of 5 Oct 2026; none of the tests reads the packs, which change every
// week. The wards are the real names and numbers of the committed snapshots, drawn here
// as squares.

const BHOPAL = { letters: ["w"], words: [], zone_letter: "z" };
const CHENNAI = { letters: ["d"], words: ["div", "divn", "dn", "division"], zone_letter: "z" };
const numbers = (title, markers) => wardMarkersIn(title, markers).numbers;
const names = (title) => wardMarkersIn(title).names.map((runs) => runs[0]);

test("a zone number is never read as a ward: WARD 47 ZONE 06 is ward 47", () => {
  assert.deepEqual(numbers("CONSTRUCTION OF CC ROAD FROM ROHIT KIRANA TO SHOP OF GANGARAM KUSHWAHA AT PANCHSHEEL NAGAR WARD 47 ZONE 06", BHOPAL), [47]);
  assert.deepEqual(numbers("CONSTRUCTION OF C.C. ROAD AT GHASIYARAN KI GALI AND ROAD CROSS AT CHHAVNI WARD 19 ZONE 05", BHOPAL), [19]);
  assert.deepEqual(numbers("Construction of C.C. Road Near Ginnori Unani Shafakhana Road Site Ginnori Road in Ward No.22, Zone No.05", BHOPAL), [22]);
  assert.deepEqual(numbers("Construction of Drain and road cross at CRP dayal house to fatak road ward 04, zone 01", BHOPAL), [4]);
  assert.deepEqual(numbers("CONSTRUCTION OF CC ROAD AT E7-71 TO HANUMAN GADHI MANDIR WARD -49 ZONE 10", BHOPAL), [49]);
  assert.deepEqual(numbers("Road improvement work by HMP from Om Shakti Hospital to Hari Om Stationery in Mohalla Gautam Vihar under Zone-6 Ward-19."), [19]);
  // The Karnataka reader, which this one stands beside and does not replace, reads both.
  assert.deepEqual([...wardNumbers("CONSTRUCTION OF CC ROAD AT PANCHSHEEL NAGAR WARD 47 ZONE 06")], [47, 6]);
});

test("a circle number is never read as a ward", () => {
  assert.deepEqual(numbers("Recarpeting of BT Road from Babbuguda Culvert to Neelima Hospital in Mothi Nagar, Ward No.256, Moosapet Circle-53, CMC(2nd Call)."), [256]);
  assert.deepEqual(numbers("Recarpeting of BT Road from Rolling Hills to Smondo Apartment in Ramky CEO Lane in Kondapur Ward No.104, SLP Cir-20, SLPZ,GHMC (2nd Call)."), [104]);
  assert.deepEqual(numbers("Recarpeting of BT road at Osman nagar road in Tellapur ward no 263 in Patancheru Circle 46, Slp Zone, CMC (Item No.1) (7th call)"), [263]);
  assert.deepEqual(numbers("Repairs of potholes using Pot-Hole Repairs Machine Jet patcher in Charminar Zone of Greater Hyderabad Municipal Corporation.(Recall) Rs.50.00 Lakhs"), []);
});

test("Bhopal's W06 and Z20 are a ward and a zone", () => {
  assert.deepEqual(numbers("Construction of cc road from canara bank to mahindra showroom and vallabh nagar sanjeevani to vijay nagar main road w06,z20", BHOPAL), [6]);
  assert.deepEqual(numbers("CONSTRUCTION OF CC ROAD AT JAIN NAGAR VALLABH NAGAR VIJAY NAGAR VITTHAL NAGAR W-06 Z-20", BHOPAL), [6]);
  assert.deepEqual(numbers("CONSTRUCTION OF CC ROAD NEAR BHUMIKA PARISAR W30 Z08", BHOPAL), [30]);
  // A body whose titles do not write wards that way gets nothing from the same letters.
  assert.deepEqual(numbers("CONSTRUCTION OF CC ROAD NEAR BHUMIKA PARISAR W30 Z08"), []);
  assert.deepEqual(numbers("Road from D block to W block, 12 m wide", { letters: ["w", "d"], words: [] }), [], "a lone letter is not a marker");
  // The shorthand is a pair, ward then zone. A W with a number and no Z behind it is a
  // house or a plot: "HN B-164" is in these titles, and a W-12 would read the same.
  assert.deepEqual(numbers("CONSTRUCTION OF CC ROAD FROM HOUSE W-12 TO PARK AT ARERA COLONY WARD 49 ZONE 10", BHOPAL), [49]);
  assert.deepEqual(numbers("CONSTRUCTION OF CC ROAD FROM HOUSE W-12 TO PARK AT ARERA COLONY", BHOPAL), []);
  assert.deepEqual(numbers("ASPHALTING OF ROAD NEAR STERLING GREENVIEW PHASE 2 AND AFTER 70A JANKI NAGAR TO COMFORT GARDEN W30 Z08", BHOPAL), [30]);
  assert.deepEqual(numbers("ASPHALTING OF ROAD WORK FROM H.NO.165-171 DURGESH VIHAR AND FROM H.NO.146-131 NEERJA NAGAR W-66 Z-15.", BHOPAL), [66]);
  assert.deepEqual(numbers("CONSTRUCTION OF CC ROAD G-3 GULMOHAR BUNDELA JI HOUSE TO GULDASTA APARTMENT WARD 50 ZONE 10", BHOPAL), [50]);
});

test("Chennai's divisions are its wards, and elsewhere a division is not", () => {
  assert.deepEqual(numbers("Improvements to BT Road in Arcot Road North Side from Thiruvalluvar Salai to KalliammanKoil Street in Div-128, Zone-10.", CHENNAI), [128]);
  assert.deepEqual(numbers("Restoration of OFC Road cut at Chithatheripet Kovam bridge road and Egmore commissioner office and Restoration of OFC Road cut at Ganeshpuram main road in Dn 62 and 45 Zone 5 and 4", CHENNAI), [62, 45]);
  assert.deepEqual(numbers("Restoration of OFC road cuts by Bharti Airtel Ltd. at Ramasamy Salai, Arcot Road, P.V. Rajamannar Salai, Ashok Pillar Main Road D128 129 136 137 138 in Z10 and Jio Digital Fibre Pvt. Ltd. at Chettiyar Agaram Main Road (D150, Z11).", CHENNAI), [128, 129, 136, 137, 138, 150]);
  assert.deepEqual(numbers("RESTORATION OF ROAD CUT CC LAYING WORK MADE BY CMWSSB AT KUMARAN MAIN STREET IN DN-27, UNIT-7, ZONE-3", CHENNAI), [27]);
  assert.deepEqual(numbers("Repairing of Road Potholes Patches using Cold mix Injection Pothole Patching Repairing Machine in Bus Route Roads of Zone 11, 12, 13, 14 15.", CHENNAI), []);
  assert.deepEqual(numbers("Construction of road gully grating of 350mm dia RCC NP3 pipe in front of H. No. 161 gate 164-172, sports complex to Proston Mall 402, 185, 374, 340, 330, 568 in sector - 31 of ward no. 27 div-5 Faridabad. Recall"), [27]);
});

test("a list of wards is read whole, and a ward office, an old number and a measure are not wards", () => {
  assert.deepEqual(numbers("Regular repair and maintenance of road and pothole in ward no. 34, 35 and 44"), [34, 35, 44]);
  assert.deepEqual(numbers("Regular repairs and maintenance of roads in ward no. 41 and 42."), [41, 42]);
  assert.deepEqual(numbers("Damar road construction work in Ward No. 40, 41, 42, 46 and 48 of Mansarovar Zone."), [40, 41, 42, 46, 48]);
  assert.deepEqual(numbers("CONSTRUCTION OF CC ROAD AT BACKSIDE OF WARD 50 OFFICE AND SAJID ALI JI HOUSE WARD 50 ZONE 10", BHOPAL), [50]);
  assert.deepEqual(numbers("CONSTRUCTION OF CC ROAD AT BACKSIDE OF WARD 12 OFFICE AND SAJID ALI JI HOUSE WARD 50 ZONE 10", BHOPAL), [50]);
  assert.deepEqual(wardMarkersIn("CONSTRUCTION OF C.C. ROAD AND ROAD CROSS AISHABAG WARD OFFICE 39 TO SUVIDHA ELECTRANICS AISHABAG HOUSING BOARD COLONY AT WARD NO.39 ZONE NO.11", BHOPAL).numbers, [39]);
  assert.deepEqual(names("ROAD CROSS AISHABAG WARD OFFICE 12 TO SUVIDHA ELECTRANICS"), [], "the name in front of a ward office is not the ward of the work");
  assert.deepEqual(numbers("Improvements in Ward No-188 Yediyur (Old Ward No-167)"), [188]);
  assert.deepEqual(numbers("Construction of CC road in ward 5, 6 m wide from the temple"), [5]);
  assert.deepEqual(numbers("Strengthening of SH 14 from km 108 to km 149"), []);
  assert.deepEqual(numbers("Restoration of damanged cc road at RR nagar road to HAL Park in ward no old Bowenpally, Kukatpally circle , GHMC(Reserved for SC Communities only) (Recall)"), []);
});

test("a ward's name is read off the title's own ward marker, the way Ahmedabad writes it", () => {
  assert.deepEqual(names("In the area of Shahibaug ward of the Central zone, Bhogilal Chali, Vasudev Pura Bhilwas, Gowaji Chhapra, Kuberpura and other areas"), ["shahibaug"]);
  assert.deepEqual(names("To construct RCC roads and pave block footpaths in various party plots/urban health centers/municipal stores and other municipal buildings in South Zone Vatwa Ward.(ARC)"), ["vatwa"]);
  assert.deepEqual(names("Hot mix patch work using milling method to repair pot holes on TP roads of Khokhara and Indrapuri ward & other wards of South Zone(ARC)"), ["indrapuri"]);
  assert.deepEqual(names("making the unpaved (kacha) parts of the road motorable in different areas of the ward and behind Gebanshah in the South Zone, Lambha Ward."), ["lambha"]);
  assert.deepEqual(names("Construction of New Road, Regrade and Resurface work at Different roads of Ranip Ward of West Zone of AMC (ARC)."), ["ranip"]);
  // Not a ward's name: "different wards", "other wards", "Different Ward".
  assert.deepEqual(names("Construction of New Road, Regrade and Resurface work at different wards of North Zone of AMC (ARC)."), []);
  assert.deepEqual(names("In Central Zone D.C. City Engineer-2, including Khadia and other wards, carrying out RCC road construction in various streets, lanes and areas. (AMC)"), []);
  assert.deepEqual(names("Regrade and Resurface of Different Ward and Different roads of Dy.C.E-2 of West Zone of AMC (ARC)."), []);
});

test("a name behind the ward's number is read, and a circle's or a road's name is not", () => {
  assert.deepEqual(names("Road improvement work near the Kali Mata Temple in Ghaukheda, under Zone-02, Ward-11, Safipur"), ["safipur"]);
  assert.deepEqual(names("Improvement work of road and drain of Mohalla Peeli Building under Zone 06 Ward 91 Shastri Nagar."), ["shastri nagar"]);
  assert.deepEqual(names("Strengthening and Recarpeting of damaged existing BT Road from Ganesh Kaman to Union Bank and internal lanes at EC Nagar in Ward-17 Cherlapally of Kapra-Circle 07, Uppal Zone, GHMC. (3RD RECALL)"), ["cherlapally"]);
  // "Ward No.256, Moosapet Circle-53": Moosapet is the circle, the ward is Mothi Nagar.
  assert.deepEqual(names("Recarpeting of BT Road from Babbuguda Culvert to Neelima Hospital in Mothi Nagar, Ward No.256, Moosapet Circle-53, CMC(2nd Call)."), []);
  assert.deepEqual(names("Restoration of badly damaged internal lanes of jalavayu vihar colony road no. 3,4, 5 bhagya nagar ward no. 244, Allwyn Colony circle-51, Kukatpally Zone, CMC").filter((name) => /allwyn/.test(name)), []);
  assert.deepEqual(names("Construction of CC road in Ward No 12 Station Road and drain"), []);
  assert.deepEqual(names("Construction of CC road in ward no. 34, 35 and 44 Ram Nagar"), [], "a list of wards is followed by a place in them");
});

// ----------------------------------------------------------------------------------------
// Matching, against wards drawn as squares.

const square = (code, no, name, x) => ({ code, no, name, bbox: [x, 0, x + 10, 10], rings: [[x, 0, 10, 0, 0, 10, -10, 0, 0, -10]] });
const AHMEDABAD = {
  id: "GJ/ahmedabad", state_code: "GJ", notices_city: "Ahmedabad", by: "name", numbers: "untested",
  markers: { letters: [], words: [] },
  wards: [square("GJ-ahmedabad-46", "46", "LAMBHA", 0), square("GJ-ahmedabad-47", "47", "VATVA", 20),
    square("GJ-ahmedabad-16", "16", "SHAHIBAG", 40), square("GJ-ahmedabad-13", "13", "SAIJPUR BOGHA", 60),
    square("GJ-ahmedabad-6", "6", "NEW WADAJ", 80), square("GJ-ahmedabad-28", "28", "KHADIA", 100)],
};
const BHOPAL_WARDS = {
  id: "MP/bhopal", state_code: "MP", notices_city: "Bhopal", by: "number", numbers: "current", markers: BHOPAL,
  wards: [square("MP-bhopal-47", "47", "डाॅ. राजेन्द्र प्रसाद", 0), square("MP-bhopal-6", "6", "महावीरगिरी", 20),
    square("MP-bhopal-20", "20", "x", 40), square("MP-bhopal-50", "50", "गुलमोहर", 60)],
};
const ward = (snapshot, no) => snapshot.wards.find((entry) => entry.no === String(no));
const CLOCK = Date.parse("2026-10-07T06:00:00Z");
let serial = 0;
const notice = (chain, title, patch = {}) => {
  serial += 1;
  return {
    award_verified: false, closing_at: "2026-10-16T18:00:00+05:30", dlp_verified: false, lifecycle: "procurement_notice",
    opening_at: null, organisation_chain: chain, published_at: null, record_id: `r:${serial}`, scope: "road_surface",
    segment_verified: false, source_id: "portal", source_url: "https://portal.test/view", tender_id: String(1000 + serial),
    tender_reference: `ref ${serial}`, title, ...patch,
  };
};
const pack = (notices) => ({ notices, sources: [{ source_id: "portal", source_name: "State portal", source_url: "https://portal.test/" }] });
const AMC = "AMC-Engineering Department - SZ - Danilimda Ward";
const BMC = "Directorate Urban Administration and Development||Muncipal Corporations - UAD||Muncipal Corporation-Bhopal - UAD||Civil - MC Bhopal- UAD";
const titles = (found) => found.map((tender) => tender.title);
const LAMBHA = "Road resurfacing and milling work at various locations in the South Zone Lambha Ward.";
const VATVA = "Work of supplying supervisors, laborers, and tractor-trolleys for pothole filling, protection, and maintenance activities under monsoon preparation during the rainy season in Gamtal as well as different areas as required and on roads in Vatva Ward of the South Zone.";

test("by name: a notice of the body whose ward marker carries the ward's name, and no other", () => {
  const gujarat = pack([
    notice(AMC, LAMBHA),
    notice(AMC, VATVA),
    // The filing office is not the place: this chain says Danilimda Ward for every title.
    notice(AMC, "Road resurfacing and milling work at various locations in the South Zone."),
    // A name off the ward marker is a village or a road: Saijpur gam is in Lambha, and
    // Saijpur Bogha is a ward on the other side of the city.
    notice(AMC, "Construction of RCC roads and laying of paver blocks in Bharvad Vas of Saijpur-gam and Vatva Road in the South Zone Lambha Ward"),
    // Another body's Lambha.
    notice("NAGARPALIKA-DHARAMPUR", "Resurfacing of road in Lambha Ward"),
    notice("R&B-Division Office - Ahmedabad", "Resurfacing of Lambha Ward approach road in Lambha Ward"),
    // Closed, and not the carriageway.
    notice(AMC, "Resurfacing of internal roads in Lambha Ward", { closing_at: "2026-10-06T18:00:00+05:30" }),
    notice(AMC, "Construction of compound wall of the health centre in Lambha Ward"),
  ]);
  const lambha = matchIndiaWardTenders({ ward: ward(AHMEDABAD, 46), snapshot: AHMEDABAD, pack: gujarat, now: CLOCK });
  assert.deepEqual(titles(lambha).sort(), [gujarat.notices[3].title, LAMBHA].sort());
  assert.ok(lambha.every((entry) => entry.match_basis === "ward name LAMBHA" && entry.scope === "ward"));
  assert.deepEqual(titles(matchIndiaWardTenders({ ward: ward(AHMEDABAD, 47), snapshot: AHMEDABAD, pack: gujarat, now: CLOCK })), [VATVA]);
  assert.deepEqual(matchIndiaWardTenders({ ward: ward(AHMEDABAD, 13), snapshot: AHMEDABAD, pack: gujarat, now: CLOCK }), []);
  assert.deepEqual(matchIndiaWardTenders({ ward: ward(AHMEDABAD, 16), snapshot: AHMEDABAD, pack: gujarat, now: CLOCK }), []);
});

test("by name: the portals' spellings of a ward are the ward, and a longer name is not a shorter one", () => {
  const gujarat = pack([
    notice("AMC-Central Zone", "In Shahibaug Ward of Central Zone, Regarding the Road Departments work arrangements are being made for the supply of Wetmix to fill potholes and carry out patchwork repairs.(ARC Tender)"),
    notice(AMC, "To construct RCC roads and repair them as per the requirement in South Zone Vatwa Ward.(ARC)"),
    notice("AMC-West Zone", "Resurfacing of roads in New Wadaj Ward of West Zone"),
  ]);
  const of = (no) => titles(matchIndiaWardTenders({ ward: ward(AHMEDABAD, no), snapshot: AHMEDABAD, pack: gujarat, now: CLOCK }));
  assert.deepEqual(of(16), [gujarat.notices[0].title], "Shahibaug is SHAHIBAG");
  assert.deepEqual(of(47), [gujarat.notices[1].title], "Vatwa is VATVA");
  assert.deepEqual(of(6), [gujarat.notices[2].title]);
  const wadaj = { ...AHMEDABAD, wards: [...AHMEDABAD.wards, square("GJ-ahmedabad-99", "99", "WADAJ", 120)] };
  assert.deepEqual(matchIndiaWardTenders({ ward: ward(wadaj, 99), snapshot: wadaj, pack: gujarat, now: CLOCK }), [], "New Wadaj ward is not Wadaj's");
});

test("by name: a name that fits two wards of the body is answered for neither", () => {
  const kanpur = {
    id: "UP/kanpur", state_code: "UP", notices_city: "Kanpur", by: "name", numbers: "wrong", markers: { letters: [], words: [] },
    wards: [square("UP-kanpur-66", "66", "Yashoda Nagar East", 0), square("UP-kanpur-67", "67", "Yashoda Nagar West", 20),
      square("UP-kanpur-29", "29", "Safipur", 40)],
  };
  const chain = "Directorate of Local Bodies UP||Kanpur Municipal Corporation";
  const up = pack([
    notice(chain, "Improvement work of road and drain in Mohalla Gopal Nagar under Zone 03 Ward 66 Yashoda Nagar."),
    notice(chain, "Road improvement work near the Kali Mata Temple in Ghaukheda, under Zone-02, Ward-11, Safipur"),
    notice(chain, "Road improvement work in Yashoda Nagar West ward under Zone-03"),
  ]);
  const of = (no) => titles(matchIndiaWardTenders({ ward: ward(kanpur, no), snapshot: kanpur, pack: up, now: CLOCK }));
  assert.deepEqual(of(66), []);
  assert.deepEqual(of(67), [up.notices[2].title]);
  // The title's 11 is another delimitation's number; the name is what places it.
  assert.deepEqual(of(29), [up.notices[1].title]);
  // Lucknow's one notice says "Janakipuram Ward II". The file draws Jankipuram 1st and
  // Jankipuram 2nd, the reader does not take "II" for "2nd", and so neither is answered.
  const lucknow = {
    id: "UP/lucknow", state_code: "UP", notices_city: "Lucknow", by: "name", numbers: "untested", markers: { letters: [], words: [] },
    wards: [square("UP-lucknow-1", "1", "Jankipuram 1st", 0), square("UP-lucknow-2", "2", "Jankipuram 2nd", 20)],
  };
  const lko = pack([notice("Directorate of Local Bodies UP||Lucknow Municipal Corporation",
    "Construction of paver-block roads and interlocking side strips, etc., in Sector-H (under Janakipuram Ward II) extending from S.P. Maurya house to Bright Way School and including various connecting lanes.")]);
  for (const entry of lucknow.wards) assert.deepEqual(matchIndiaWardTenders({ ward: entry, snapshot: lucknow, pack: lko, now: CLOCK }), [], entry.name);
});

test("by number: the title's ward number, never its zone, and only where the numbering is the tenders'", () => {
  const mp = pack([
    notice(BMC, "CONSTRUCTION OF CC ROAD FROM ROHIT KIRANA TO SHOP OF GANGARAM KUSHWAHA AT PANCHSHEEL NAGAR WARD 47 ZONE 06"),
    notice(BMC, "CONSTRUCTION OF CC ROAD AT JAIN NAGAR VALLABH NAGAR VIJAY NAGAR VITTHAL NAGAR W-06 Z-20"),
    notice(BMC, "CONSTRUCTION OF CC ROAD AT BACKSIDE OF WARD 50 OFFICE AND SAJID ALI JI HOUSE WARD 50 ZONE 10"),
    // Another body of the State with the same ward number.
    notice("Directorate Urban Administration and Development||Muncipal Corporations - UAD||Muncipal Corporation-Gwalior - UAD", "CONSTRUCTION OF CC ROAD IN WARD 47"),
    notice("Bhopal Development Authority", "CONSTRUCTION OF CC ROAD IN WARD 47"),
  ]);
  const of = (no, snapshot = BHOPAL_WARDS) => titles(matchIndiaWardTenders({ ward: ward(snapshot, no), snapshot, pack: mp, now: CLOCK }));
  assert.deepEqual(of(47), [mp.notices[0].title]);
  assert.deepEqual(of(6), [mp.notices[1].title], "ward 6 gets W-06, and not WARD 47 ZONE 06");
  assert.deepEqual(of(20), [], "Z-20 is a zone");
  assert.deepEqual(of(50), [mp.notices[2].title]);
  assert.equal(matchIndiaWardTenders({ ward: ward(BHOPAL_WARDS, 47), snapshot: BHOPAL_WARDS, pack: mp, now: CLOCK })[0].match_basis, "ward number 47");
  for (const untested of ["untested", "wrong", "none"]) {
    assert.deepEqual(of(47, { ...BHOPAL_WARDS, numbers: untested }), [], `numbers ${untested}`);
  }
  assert.deepEqual(of(47, { ...BHOPAL_WARDS, by: "nothing" }), []);
});

test("at most five are answered, a title about this ward alone first, in the Karnataka ward tender's shape", () => {
  const odisha = "Municipal Bodies||Bhubaneswar Municipal Corporation";
  const bhubaneswar = {
    id: "OD/bhubaneswar", state_code: "OD", notices_city: "Bhubaneswar", by: "number", numbers: "current", markers: { letters: [], words: [] },
    wards: [square("OD-bhubaneswar-35", "35", null, 0)],
  };
  const od = pack([
    notice(odisha, "Regular repair and maintenance of road and pothole in ward no. 34, 35 and 44", { published_at: "2026-10-01" }),
    notice(odisha, "Improvement of Roads in ward no. 35", { published_at: "2026-09-20" }),
    ...[1, 2, 3, 4, 5].map((n) => notice(odisha, `Improvement of road at Lane ${n} of Ananta Vihar in Ward No. 35`, { published_at: `2026-09-2${n}` })),
  ]);
  const found = matchIndiaWardTenders({ ward: bhubaneswar.wards[0], snapshot: bhubaneswar, pack: od, now: CLOCK });
  assert.equal(found.length, 5);
  assert.ok(!titles(found).includes(od.notices[0].title), "the title that lists three wards comes after six about this one");
  assert.equal(found[0].title, "Improvement of road at Lane 5 of Ananta Vihar in Ward No. 35", "the most recently published first");
  assert.equal(found[0].tender_number, `${od.notices[6].tender_reference} [${od.notices[6].tender_id}]`);
  assert.equal(found[0].published, "2026-09-25");
  assert.equal(found[0].location, odisha);
  assert.equal(found[0].source_name, "State portal");
  // The app reads ward_tenders of both kinds with one reader: the same keys, in the same order.
  const karnataka = matchWardTenders({ wardName: "Hoodi", tenders: [{ tender_number: "T/1", title: "Improvements to roads in Hoodi ward no.54", location: "Mahadevapura" }] });
  assert.deepEqual(Object.keys(found[0]), Object.keys(karnataka[0]));
  assert.equal(matchIndiaWardTenders({ ward: bhubaneswar.wards[0], snapshot: bhubaneswar, pack: od, now: CLOCK, limit: Infinity }).length, 7);
});

test("the body a point is in, and how many road notices it has, without making it a ward tender", () => {
  const up = pack([
    notice("Directorate of Local Bodies UP||Ghaziabad Municipal Corporation", "Road work in Ward 5"),
    notice("Directorate of Local Bodies UP||Ghaziabad Municipal Corporation", "Road work in Ward 9", { closing_at: "2026-10-06T10:00:00+05:30" }),
    notice("Directorate of Local Bodies UP||Ghaziabad Municipal Corporation", "Construction of compound wall in Ward 9"),
    notice("Ghaziabad Development Authority", "Road work in Madhuban Bapudham"),
    notice("Directorate of Local Bodies UP||Kanpur Municipal Corporation", "Road work in Ward 5"),
  ]);
  assert.deepEqual(urbanBodyAt({ city: "Ghaziabad", stateCode: "UP", pack: up, now: CLOCK }), {
    name: "Ghaziabad Municipal Corporation", kind: "Municipal Corporation", city: "Ghaziabad", state_code: "UP",
    basis: "geocoder_city", road_notices: 3, road_notices_open: 1,
  });
  assert.equal(urbanBodyAt({ city: "ghaziabad ", stateCode: "UP", pack: up, now: CLOCK }).name, "Ghaziabad Municipal Corporation");
  assert.equal(urbanBodyAt({ city: "Meerut", stateCode: "UP", pack: up, now: CLOCK }), null, "a city none of the State's notices is filed under");
  assert.equal(urbanBodyAt({ city: null, stateCode: "UP", pack: up, now: CLOCK }), null);
  assert.equal(urbanBodyAt({ city: "Ghaziabad", stateCode: "UP", pack: null, now: CLOCK }), null);
  const kanpur = { id: "UP/kanpur", state_code: "UP", notices_city: "Kanpur", body: "Kanpur Municipal Corporation" };
  // The snapshot's ward holds the point, so the body is its body whatever the geocoder called the city.
  assert.deepEqual(urbanBodyAt({ snapshot: kanpur, city: "Kanpur Nagar", stateCode: "UP", pack: up, now: CLOCK }), {
    name: "Kanpur Municipal Corporation", kind: "Municipal Corporation", city: "Kanpur", state_code: "UP",
    basis: "ward_snapshot", road_notices: 1, road_notices_open: 1,
  });
  const lucknow = { id: "UP/lucknow", state_code: "UP", notices_city: "Lucknow", body: "Lucknow Municipal Corporation" };
  assert.deepEqual(urbanBodyAt({ snapshot: lucknow, city: "Lucknow", stateCode: "UP", pack: up, now: CLOCK }), {
    name: "Lucknow Municipal Corporation", kind: null, city: "Lucknow", state_code: "UP",
    basis: "ward_snapshot", road_notices: 0, road_notices_open: 0,
  });
});
