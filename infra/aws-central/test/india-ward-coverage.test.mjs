import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { urbanBodyOf } from "../tools/india-notice-bodies.mjs";
import { broadWardNumbers, unitNumbers } from "../tools/india-ward-coverage.mjs";
import { INDEX_PATH, root } from "../tools/snapshot-india-wards.mjs";

// The offline tools that say which urban body a road notice belongs to and how its title
// marks a ward. Every title and organisation chain below is one a State portal published
// in the packs of 5 Oct 2026; none of the tests reads the packs, which change every week.

const notice = (state, chain, title = "") => ({ state, chain: chain.split("||"), title });

test("a notice's urban body is the one its portal names", () => {
  assert.deepEqual(urbanBodyOf(notice("UP", "Directorate of Local Bodies UP||Ghaziabad Municipal Corporation")),
    { kind: "Municipal Corporation", city: "Ghaziabad", label: "Ghaziabad Municipal Corporation", how: "chain" });
  assert.equal(urbanBodyOf(notice("UP", "Directorate of Local Bodies UP||Nagar Palika Parishad Shamli Shamli")).label, "Shamli Municipal Council");
  assert.equal(urbanBodyOf(notice("MP", "Directorate Urban Administration and Development||Muncipal Corporations - UAD||Muncipal Corporation-Bhopal - UAD||Civil - MC Bhopal- UAD")).city, "Bhopal");
  assert.equal(urbanBodyOf(notice("MP", "Directorate Urban Administration and Development||Join Director-Indore Division - UAD||Khargone-Nagar Palika Khargone - UAD")).city, "Khargone");
  assert.equal(urbanBodyOf(notice("GJ", "AMC-Engineering Department - SZ - Danilimda Ward")).city, "Ahmedabad");
  assert.equal(urbanBodyOf(notice("WB", "MUNICIPAL AFFAIRS DEPARTMENT||URBAN LOCAL BODIES||ASANSOL MC")).label, "Asansol Municipal Corporation");
  assert.equal(urbanBodyOf(notice("HR", "Haryana Government||Urban Local Bodies||MC Hissar")).city, "Hisar");
  assert.equal(urbanBodyOf(notice("RJ", "DLB||Dy.DR-Jaipur||Commissioner-Alwar")).city, "Alwar");
  assert.equal(urbanBodyOf(notice("TN", "Corporation of Chennai||Head Quarters,CoC")).city, "Chennai");
  assert.equal(urbanBodyOf(notice("JK", "HAUDD||Srinagar Municipal Corporation||City Roads Division Srinagar")).city, "Srinagar");
  // Hyderabad has been three corporations since February 2026; they are one city here.
  for (const chain of ["cyberabad municipal corporation-Kukatpally Zone (Zone 6) GHMC HYD", "Malkajgiri Municipal Corporation-EE-DIV-11", "GREATER HYDERABAD MUNICIPAL CORPORATION-SUPERITENDING ENGINEER-III"]) {
    assert.equal(urbanBodyOf(notice("TG", chain)).city, "Hyderabad", chain);
  }
});

test("a works division, a panchayat and a company are not urban bodies", () => {
  for (const [state, chain] of [
    ["UP", "Chief Engineer Agra Zone PWD Agra UP||AGRA CIRCLE PWD AGRA"],
    ["KL", "Local Self Government Department||Kottayam||Grama Panchayath Section Office KTYM||Office of the AE Athirampuzha Grama Panchayath"],
    ["WB", "Zilla Parishad||Zilla Parishad||PURBA BARDHAMAN"],
    ["HR", "Haryana Board Corporation||HPGCL||PTPS Panipat"],
    ["MP", "Madhya Pradesh Road Development Corporation Limited"],
    ["KL", "Roads and Bridges Development Corporation Kerala"],
    ["UP", "Department of MSME And Export Promotion||UP Small Industries Corporation Ltd"],
  ]) assert.equal(urbanBodyOf(notice(state, chain)), null, chain);
});

test("where the portal's chain stops at the department, only a title that names the body gives one", () => {
  const bihar = "Organisation ID 538||Department ID 800";
  assert.equal(urbanBodyOf(notice("BR", bihar, "Construction of a PCC road and drain from Isha Master's house to the main road in Ward No. 05 under Bettiah Municipal Corporation.")).label, "Bettiah Municipal Corporation");
  assert.equal(urbanBodyOf(notice("BR", bihar, "Construction Of Pcc Road and Rcc Drain at Sunder lal lane Main Road Piyau tak in Ward No-37")), null);
  // The same words in a State whose chain does name bodies are not read from the title.
  assert.equal(urbanBodyOf(notice("UP", "Chief Engineer PWD Varanasi Zone", "Road in Ward No. 05 under Bettiah Municipal Corporation")), null);
});

test("ward markers the service's parser does not read are read here, and zone numbers are told apart", () => {
  assert.deepEqual([...broadWardNumbers("Improvements to BT Road in Arcot Road North Side from Thiruvalluvar Salai to KalliammanKoil Street in Div-128, Zone-10.")], [128]);
  assert.deepEqual([...broadWardNumbers("Restoration of OFC Road cut at Ganeshpuram main road in Dn 62 and 45 Zone 5 and 4")], [62]);
  assert.deepEqual([...broadWardNumbers("Construction of cc road from canara bank to mahindra showroom w06,z20")], [6]);
  assert.deepEqual([...broadWardNumbers("CONSTRUCTION OF CC ROAD AT PANCHSHEEL NAGAR WARD 47 ZONE 06")], [47]);
  assert.deepEqual([...broadWardNumbers("Strengthening of SH 14 from km 108 to km 149")], []);
  assert.deepEqual([...broadWardNumbers("Road from D block to W block, 12 m wide")], [], "a lone letter is not a marker");
  assert.deepEqual([...unitNumbers("CONSTRUCTION OF CC ROAD AT PANCHSHEEL NAGAR WARD 47 ZONE 06")], [6]);
  assert.deepEqual([...unitNumbers("in Tellapur ward no-263, Patancheru Circle-46, SLP Zone, CMC")], [46]);
  assert.deepEqual([...unitNumbers("Kondapur Ward No.104, SLP Cir-20, SLPZ,GHMC")], [20]);
  assert.deepEqual([...unitNumbers("w06,z20")], [20]);
});

test("the hand-read record is whole: every pair has a verdict the file defines, a reason and a ward in a snapshot", () => {
  const handread = JSON.parse(readFileSync(new URL("../../../data/wards/handread.json", import.meta.url)));
  const index = JSON.parse(readFileSync(INDEX_PATH));
  assert.equal(handread.format, "pothole-india-ward-handread");
  let read = 0;
  for (const [id, city] of Object.entries(handread.cities)) {
    const entry = index.snapshots.find((item) => item.id === id);
    assert.ok(entry, `${id} is not a committed snapshot`);
    const codes = new Set(JSON.parse(readFileSync(new URL(entry.path, `file://${root}/`))).wards.map((ward) => ward.code));
    assert.ok(city.pairs.length >= 15 && city.pairs.length <= 30, `${id}: ${city.pairs.length} pairs`);
    assert.ok(city.pairs_in_all >= city.pairs.length, id);
    for (const pair of city.pairs) {
      assert.ok(handread.verdicts[pair.verdict], `${id} pair ${pair.n}: verdict ${pair.verdict}`);
      assert.ok(pair.reason.length > 10 && pair.title.length > 10 && pair.tender_id, `${id} pair ${pair.n}`);
      assert.ok(["name", "number"].includes(pair.basis));
      assert.ok(codes.has(pair.ward_code), `${id} pair ${pair.n}: ${pair.ward_code} is not in the snapshot`);
      read += 1;
    }
  }
  assert.equal(read, 142);
  assert.equal(handread.gazetteer_hits.pairs.length, 30);
  for (const pair of handread.gazetteer_hits.pairs) assert.ok(handread.gazetteer_hits.verdicts[pair.verdict] && pair.reason.length > 10, `gazetteer pair ${pair.n}`);
});
