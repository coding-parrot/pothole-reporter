// Renders data/wards/COVERAGE.md from the measurements of india-ward-coverage.mjs. The
// numbers are computed; what was opened on the network on 7 Oct 2026 (a source's HTTP
// status, its feature count, its licence text) cannot be recomputed from the repo and is
// recorded here as it was read that day.
import fs from "node:fs";
import path from "node:path";
import zlib from "node:zlib";

import { urbanBodyOf, root } from "./india-notice-bodies.mjs";
import { SOURCES, committable } from "./snapshot-india-wards.mjs";

const OPENED_ON = "7 Oct 2026";
const STATE_NAMES = {
  AN: "Andaman and Nicobar", AP: "Andhra Pradesh", AR: "Arunachal Pradesh", AS: "Assam", BR: "Bihar", CG: "Chhattisgarh",
  CH: "Chandigarh", DH: "Dadra, Nagar Haveli, Daman, Diu", DL: "Delhi", GA: "Goa", GJ: "Gujarat", HP: "Himachal Pradesh",
  HR: "Haryana", JH: "Jharkhand", JK: "Jammu and Kashmir", KA: "Karnataka", KL: "Kerala", LA: "Ladakh", LD: "Lakshadweep",
  MH: "Maharashtra", ML: "Meghalaya", MN: "Manipur", MP: "Madhya Pradesh", MZ: "Mizoram", NL: "Nagaland", OD: "Odisha",
  PB: "Punjab", PY: "Puducherry", RJ: "Rajasthan", SK: "Sikkim", TG: "Telangana", TN: "Tamil Nadu", TR: "Tripura",
  UK: "Uttarakhand", UP: "Uttar Pradesh", WB: "West Bengal",
};

// Sources opened on 7 Oct 2026 that are not in the snapshot registry, by city. `count` is
// the number of ward polygons counted in the file or returned by the service that day.
const ESRI = "labelled Public Domain by OpenCity, which names livingatlas.esri.in as the source; the Esri India layer forbids export for offline use. Refused";
const opencity = (slug, resource, count, field, decision, date = "2024") => ({
  name: `OpenCity: ${resource}`, url: `https://data.opencity.in/dataset/${slug}`, publisher: "OpenCity (Oorvani Foundation)",
  licence: "Other (Public Domain), as labelled", date, count, field, decision,
});
const EXTRA = {
  "GJ/Ahmedabad": [opencity("amdavad-municipal-corporation-wards-map-2024", "Amdavad Wards Map 2024 (KML)", 48, "ward_lgd_name, sourcewardname", ESRI)],
  "MP/Bhopal": [opencity("bhopal-wards-map", "Bhopal Wards Map 2024 (KML)", 85, "ward_lgd_name, sourcewardcode", ESRI)],
  "MP/Indore": [opencity("indore-wards-map", "Indore Wards Map 2024 (KML)", 85, "ward_lgd_name, sourcewardcode", ESRI)],
  "RJ/Jaipur": [opencity("jaipur-municipal-corporation-wards-map", "Greater Jaipur Nagar Nigam Wards 2024 (KML)", 150, "ward_lgd_name, wardcode", ESRI)],
  "KL/Kochi": [opencity("kochi-wards-map", "Kochi Wards Map 2024 (KML)", 74, "ward_lgd_name, sourcewardcode", ESRI)],
  "AS/Guwahati": [
    opencity("guwahati-wards-information", "Guwahati Ward Boundaries 2022 (KML)", 60, "ward_lgd_name, sourcewardname", ESRI, "2022"),
    opencity("guwahati-wards-information", "Guwahati Proposed Wards Map by GMDA (KML)", 74, "ward no", "a master plan proposal, not the wards in force; same dataset, same label. Refused", "undated"),
  ],
  "TG/Hyderabad": [opencity("hyderabad-wards-info", "Greater Hyderabad Wards Map 2022 (KML)", 155, "ward, CIRCLE, ZONE", "a copy of the TGRAC ward layer (same 155 polygons, same fields), which carries no licence. Refused", "2022")],
  "TN/Chennai": [
    { ...opencity("gcc-ward-information", "Chennai GCC Ward Map 2022 (KML)", 200, "name", "no licence on the dataset. Refused", "2022"), licence: "License not specified, as labelled" },
    { name: "Greater Chennai Corporation GIS, EDP_wardBoundary_2025", url: "https://gisgcc.chennaicorporation.gov.in/server/rest/services/GCCDepts/EDPMobile2025/FeatureServer/2", publisher: "Greater Chennai Corporation", licence: "none stated (copyrightText empty)", date: "2025", count: 200, field: "ward, zone", decision: "official and current, no reuse licence; the app's Tamil Nadu pack already declines to copy this service. Refused" },
  ],
  "WB/Kolkata": [opencity("kolkata-wards-information", "Kolkata Wards Map 2022 (KML)", 141, "WARD", "the same 141 polygons as DataMeet's file, which is the one committed", "2022")],
  "MH/Mumbai": [
    opencity("mumbai-wards-map", "Mumbai Wards Map (KML)", 24, "NAME", "the 24 lettered wards; DataMeet's file of the same wards is the one committed", "undated"),
    opencity("mumbai-wards-map", "Mumbai Prabhag Boundaries Map (KML)", 56, "PRABHAG_NO, WARD", "56 of the 227 electoral wards only. Not used", "2016"),
    { name: "BMC ArcGIS Online, Prabhaag_Boundary", url: "https://services8.arcgis.com/r6MmJtuWAzMawmJ8/ArcGIS/rest/services/Prabhaag_Boundary/FeatureServer/0", publisher: "Brihanmumbai Municipal Corporation", licence: "none stated (copyrightText empty)", date: "undated", count: 227, field: "PRABHAG_NO, WARD", decision: "official, no reuse licence. Refused; DataMeet's 227 electoral wards of 2017 are committed" },
    { name: "BMC ArcGIS Online, BMC_Ward", url: "https://services8.arcgis.com/r6MmJtuWAzMawmJ8/ArcGIS/rest/services/BMC_Ward/FeatureServer/0", publisher: "Brihanmumbai Municipal Corporation", licence: "none stated (copyrightText empty)", date: "undated", count: 24, field: "NAME", decision: "official, no reuse licence. Refused" },
  ],
  "MH/Pune": [
    opencity("pune-wards-info", "PMC Prabhag Boundary (KML)", 72, "prabhag_id, prabhag, ward", "the same 72 polygons as the corporation's own WFS layer; OpenCity says \"the year of these boundaries is not known\". Not used", "unknown"),
  ],
  "OD/Bhubaneswar": [{ name: "OpenCity: Bhubaneswar Administrative Boundaries, Wards (GeoJSON)", url: "https://data.opencity.in/dataset/bhubaneswar-administrative-boundaries", publisher: "OpenCity (Oorvani Foundation)", licence: "Creative Commons Attribution Share-Alike", date: "2026-04-17", count: 67, field: "wardno", decision: "byte for byte DataMeet's file (723,961 bytes), which is the one committed" }],
  "MP/Jabalpur": [{ name: "Open Government Data Platform: Covid_19 : Jabalpur Ward boundary (KML, 335,430 bytes)", url: "https://smartcities.data.gov.in/resources/covid19-jabalpur-ward-boundary", publisher: "Jabalpur Smart City", licence: "Government Open Data License - India", date: "2020-07-08", count: "not opened", field: "not read", decision: "catalogue record read (HTTP 200); the file host www.data.gov.in answered HTTP 403 to this tool. In the registry as MP/jabalpur for a person to fetch" }],
  "KL/Thiruvananthapuram": [],
};
const KERALA = { name: "Kerala Delimitation Commission ward map (K-SMART)", url: "https://wardmap.ksmart.live/", publisher: "Delimitation Commission, Kerala (Information Kerala Mission)", licence: "none stated", date: "2025 delimitation", count: "not downloadable", field: "vector tiles", decision: "the page opens (HTTP 200); its tile service (kmapdev.ksmart.live/tiles/wb_kerala) answered HTTP 403 \"Access denied\" to this tool. A map to look at, not a dataset" };
EXTRA["KL/Kochi"].push(KERALA);
EXTRA["KL/Thiruvananthapuram"].push(KERALA);

// The cities the inventory was asked for: the 25 with the most notices from an urban
// body, then the named metros. [State, city as the notice classifier names it, what
// OpenStreetMap holds (Geofabrik extract, data of 6 Oct 2026), note].
const NO_OSM = "none";
const CITIES = [
  ["UP", "Ghaziabad", NO_OSM, "Named in the Esri India layer's description; no other source found."],
  ["UP", "Kanpur", NO_OSM, ""],
  ["UP", "Shamli", NO_OSM, "Nothing found."],
  ["UP", "Ramkola Kushinagar", NO_OSM, "Nothing found."],
  ["BR", "Bettiah", NO_OSM, "Nothing found."],
  ["MP", "Gwalior", NO_OSM, "Nothing found (the 59 admin_level 9 relations nearby are villages)."],
  ["MP", "Bhopal", NO_OSM, ""],
  ["GJ", "Ahmedabad", NO_OSM, ""],
  ["OD", "Soro", NO_OSM, "Nothing found."],
  ["TG", "Karimnagar", NO_OSM, "Nothing found; the TGRAC ward layers cover the Hyderabad region only."],
  ["JK", "Srinagar", NO_OSM, "Nothing found."],
  ["RJ", "Alwar", NO_OSM, "Nothing found."],
  ["UP", "Aliganj Etah", NO_OSM, "Nothing found."],
  ["OD", "Rajgangpur", NO_OSM, "Nothing found."],
  ["UP", "Noida", NO_OSM, "Notices name sectors, not wards; OpenStreetMap does not map Noida's sectors as boundaries. Named in the Esri India layer's description."],
  ["WB", "Jalpaiguri", NO_OSM, "The State's urban GIS (nagargispariseva.wb.gov.in) did not answer on 7 Oct 2026 (connection timed out twice)."],
  ["UP", "Saharanpur", NO_OSM, "Nothing found."],
  ["MP", "Sagar", NO_OSM, "Nothing found."],
  ["RJ", "Jodhpur", NO_OSM, "Nothing found."],
  ["MP", "Jabalpur", NO_OSM, ""],
  ["UP", "Pilibhit", NO_OSM, "Nothing found."],
  ["WB", "Baidyabati", NO_OSM, "As Jalpaiguri: the State's urban GIS did not answer."],
  ["MP", "Khargone", NO_OSM, "Nothing found."],
  ["MP", "Ujjain", NO_OSM, "Nothing found."],
  ["MP", "Katni", NO_OSM, "Nothing found."],
  ["MH", "Mumbai", "23 lettered administrative wards (admin_level 10)", ""],
  ["DL", null, "no wards: the 1,045 admin_level 10 relations are colonies and villages", ""],
  ["TG", "Hyderabad", "149 ward relations, 147 with a closed boundary", ""],
  ["TN", "Chennai", "196 of 200 wards, numbers only", ""],
  ["MH", "Pune", NO_OSM, ""],
  ["WB", "Kolkata", NO_OSM, "The State's urban GIS, from which the app's pack took the Kolkata outline on 21 Aug 2026, did not answer on 7 Oct 2026."],
  ["UP", "Lucknow", NO_OSM, ""],
  ["RJ", "Jaipur", NO_OSM, ""],
  ["BR", "Patna", NO_OSM, "Nothing found."],
  ["MP", "Indore", NO_OSM, ""],
  ["MH", "Nagpur", NO_OSM, "Esri India publishes a Nagpur ward layer under the same terms as its national one (item 08ce47bd569d46efa470c8a70fd39bd3). Refused."],
  ["GJ", "Surat", NO_OSM, "Nothing found."],
  ["KL", "Kochi", NO_OSM, "The Kerala SDI (opensdi.kerala.gov.in), DataMeet's upstream, did not answer on 7 Oct 2026."],
  ["KL", "Thiruvananthapuram", NO_OSM, "The Kerala SDI did not answer on 7 Oct 2026."],
  ["OD", "Bhubaneswar", "64 of 67 wards, numbers only", ""],
  ["AS", "Guwahati", NO_OSM, ""],
  ["CH", "Chandigarh", "26 wards of the old delimitation and 60 sectors", ""],
];
const CITY_LABEL = { "Ramkola Kushinagar": "Ramkola (Kushinagar)", "Aliganj Etah": "Aliganj (Etah)" };

const licenceShort = (licence) => (/ODbL/.test(licence) ? "ODbL 1.0" : /ShareAlike/.test(licence) ? "CC BY-SA 2.5 IN" : /Attribution 4\.0/.test(licence) ? "CC BY 4.0"
  : /Public Domain/.test(licence) ? "Public Domain, as OpenCity states" : licence);
const table = (headers, rows) => [
  `| ${headers.join(" | ")} |`,
  `| ${headers.map(() => "---").join(" | ")} |`,
  ...rows.map((row) => `| ${row.map((cell) => String(cell ?? "").replace(/\|/g, "/").replace(/\n/g, " ")).join(" | ")} |`),
].join("\n");
const pct = (part, whole) => (whole ? `${((100 * part) / whole).toFixed(part / whole < 0.1 ? 1 : 0)}%` : "0%");
const n = (value) => Number(value).toLocaleString("en-US");
const short = (text, length = 110) => (String(text).length > length ? `${String(text).slice(0, length - 1).trimEnd()}...` : String(text));

// Every polygon the app's routing packs hold other than a State outline.
function packGeometry() {
  const dir = path.join(root, "static");
  const newest = fs.readdirSync(dir).map((name) => name.match(/^pack-manifest-v(\d+)\.(\d+)\.json$/)).filter(Boolean)
    .sort((left, right) => Number(left[1]) - Number(right[1]) || Number(left[2]) - Number(right[2])).pop()[0];
  const manifest = JSON.parse(fs.readFileSync(path.join(dir, newest), "utf8"));
  const rows = [];
  let outlines = 0;
  for (const [id, resource] of Object.entries(manifest.resources)) {
    if (resource.kind !== "routing") continue;
    const pack = JSON.parse(fs.readFileSync(path.join(root, "docs", resource.path), "utf8"));
    const found = new Map();
    const walk = (value, trail, parent) => {
      if (Array.isArray(value)) return value.forEach((item) => walk(item, `${trail}[]`, value));
      if (!value || typeof value !== "object") return undefined;
      if ((value.type === "Polygon" || value.type === "MultiPolygon") && Array.isArray(value.coordinates)) {
        const entry = found.get(trail) || { count: 0, labels: [], source: parent?.source_name || parent?.source || "" };
        entry.count += 1;
        entry.labels.push(String(parent?.name || parent?.source_name || parent?.code || parent?.authority_name || ""));
        found.set(trail, entry);
        return undefined;
      }
      return Object.entries(value).forEach(([key, item]) => walk(item, `${trail}.${key}`, value));
    };
    walk(pack.payload, "", null);
    for (const [trail, entry] of found) {
      const statewide = /\.region\.geometry$|regions\.(maharashtra|west_bengal)\.geometry$/.test(trail);
      if (statewide) {
        outlines += 1;
        continue;
      }
      rows.push([resource.state_code, id, trail.replace(/^\.|\.geometry$/g, ""), entry.count, short(entry.labels.filter(Boolean).join(", "), 90), short(entry.source, 80)]);
    }
  }
  return { manifest: newest, outlines, rows };
}

// `samplePairs` and `sampleGazetteerHits` are handed in by india-ward-coverage.mjs, which
// imports this module only when it is asked to write the report.
export function render(all, { handread, samplePairs, sampleGazetteerHits }) {
  const { loaded, index, snapshots, refused, states, localities } = all;
  const total = loaded.notices.length;
  const out = [];
  const say = (...lines) => out.push(...lines, "");

  const bodies = new Map();
  for (const notice of loaded.notices) {
    const body = urbanBodyOf(notice);
    if (!body) continue;
    const key = `${notice.state}|${body.label}`;
    const entry = bodies.get(key) || { state: notice.state, label: body.label, city: body.city, kind: body.kind, count: 0 };
    entry.count += 1;
    bodies.set(key, entry);
  }
  const ranked = [...bodies.values()].sort((left, right) => right.count - left.count || (left.label < right.label ? -1 : 1));
  const urbanTotal = ranked.reduce((sum, entry) => sum + entry.count, 0);
  const cityCount = (state, city) => loaded.notices.filter((notice) => notice.state === state && (city === null ? true : urbanBodyOf(notice)?.city === city)).length;
  const snapshotsFor = (state, city) => snapshots.filter(({ source }) => source.notices?.state === state && (city === null ? source.notices.any : source.notices.city === city));
  const refusedFor = (state, city) => SOURCES.filter((source) => !committable(source) && source.notices?.state === state && (city === null ? source.notices.any : source.notices.city === city));

  // Bodies with a committed snapshot, each counted once.
  const covered = new Map();
  for (const { source, measure } of snapshots) covered.set(`${source.notices.state}|${source.notices.city || "*"}`, measure.notices_of_body);
  const coveredNotices = [...covered.values()].reduce((sum, value) => sum + value, 0);
  const withMarker = states.reduce((sum, row) => sum + row.ward_marker, 0);
  const withBroadOnly = states.reduce((sum, row) => sum + row.ward_marker_broader_only, 0);
  const noWard = states.reduce((sum, row) => sum + row.no_ward, 0);
  const namesPlace = states.reduce((sum, row) => sum + row.names_gazetteer_place, 0);
  const misread = states.reduce((sum, row) => sum + row.zone_or_circle_number_read_as_ward, 0);
  const measureOf = (id) => [...snapshots, ...refused].find(({ source }) => source.id === id)?.measure;
  // A body with two snapshots (Mumbai, Pune) is counted once.
  const namedOnce = new Map();
  for (const { source, measure } of snapshots) {
    const key = `${source.notices.state}|${source.notices.city || "*"}`;
    namedOnce.set(key, Math.max(namedOnce.get(key) || 0, measure.by_name.notices_naming_a_ward));
  }
  const noticesNamingAWard = [...namedOnce.values()].reduce((sum, value) => sum + value, 0);
  const read = (id) => {
    const pairs = handread?.cities?.[id]?.pairs || [];
    return { read: pairs.length, right: pairs.filter((pair) => pair.verdict === "right").length, wrong: pairs.filter((pair) => pair.verdict === "wrong").length };
  };
  const pairsRead = Object.values(handread?.cities || {}).reduce((sum, city) => sum + city.pairs.length, 0);
  const localitiesBytes = fs.readFileSync(path.join(root, "data/wards/india-localities.json"));
  const localitiesGzip = zlib.gzipSync(localitiesBytes).length;
  // Neighbourhood, quarter, suburb and locality places within 0.08 degrees (about 9 km)
  // of the place node that carries the town's name.
  const urbanPlacesNear = (stateCode, town) => {
    const state = localities.states[stateCode];
    let at = -1;
    for (let index = 0; index < state.count && at < 0; index += 1) if (state.n[index].toLowerCase() === town.toLowerCase() && "ct".includes(state.k[index])) at = index;
    for (let index = 0; index < state.count && at < 0; index += 1) if (state.n[index].toLowerCase() === town.toLowerCase()) at = index;
    if (at < 0) return null;
    let count = 0;
    for (let index = 0; index < state.count; index += 1) {
      if ("nqsl".includes(state.k[index]) && Math.abs(state.x[index] - state.x[at]) < 8_000 && Math.abs(state.y[index] - state.y[at]) < 8_000) count += 1;
    }
    return count;
  };

  say("# Ward polygons outside Karnataka: inventory and coverage",
    "",
    `Generated by \`node infra/aws-central/tools/india-ward-coverage.mjs\` from ${loaded.manifest} (notices retrieved ${loaded.generated_at}), `
    + `data/wards/index.json, data/wards/india-localities.json and data/wards/handread.json. Network facts (a source's status, count and licence text) are as read on ${OPENED_ON}. `
    + "Do not edit by hand: change the tool or the data and run it again.");

  say("## 1. The short version",
    "",
    `- The app holds ${n(total)} State road notices from ${loaded.states.filter((row) => row.notices).length} States and UTs. ${n(urbanTotal)} (${pct(urbanTotal, total)}) were tendered by a body the portal names as urban; the rest are public works divisions, zilla parishads, grama panchayats and rural works agencies.`,
    `- ${n(withMarker + withBroadOnly)} titles (${pct(withMarker + withBroadOnly, total)}) carry a ward marker, nearly always a number ("Ward No. 27", "Zone-6 Ward-19", "Div-128"). Outside Bengaluru the ward number, read together with the body, is what a title offers; the service's name rule finds a ward's name in ${n(noticesNamingAWard)} of the ${n(coveredNotices)} notices of the bodies snapshotted here.`,
    `- Ward polygons that anyone may copy exist for ${covered.size} bodies, in ${index.count} files (${n(index.wards)} wards, ${n(index.snapshots.reduce((sum, entry) => sum + entry.bytes, 0))} bytes). Those bodies, with every Delhi and Chandigarh notice counted as the city's, account for ${n(coveredNotices)} of the ${n(total)} notices (${pct(coveredNotices, total)}). Ghaziabad alone tendered ${n(cityCount("UP", "Ghaziabad"))} and has no polygons anyone may copy.`,
    `- Having polygons is not the same as being able to use them. A person read ${pairsRead} ward and notice pairs in the six bodies with the most notices: see section 7. In short: Ahmedabad works by name (${read("GJ/ahmedabad").right} of ${read("GJ/ahmedabad").read} right), Bhopal works by number once the zone number stops being read as a ward (${read("MP/bhopal").wrong} of ${read("MP/bhopal").read} wrong, all for that one reason), and the Kanpur, Jaipur and Hyderabad files are numbered for another delimitation than their 2026 tenders (${read("UP/kanpur").wrong} of ${read("UP/kanpur").read}, ${read("RJ/jaipur-2009").wrong} of ${read("RJ/jaipur-2009").read} and ${read("TG/hyderabad").wrong} of ${read("TG/hyderabad").read} pairs wrong; in Hyderabad ${(handread?.cities?.["TG/hyderabad"]?.pairs || []).filter((pair) => pair.verdict === "wrong" && /parser read/.test(pair.reason)).length} of those because a circle number was read as a ward).`,
    `- The service's ward number parser was written for Bengaluru titles. On ${n(misread)} titles elsewhere it returns a zone or circle number as a ward ("WARD 47 ZONE 06" gives 47 and 6), and it does not read "Div-128" or "W06" at all (${n(withBroadOnly)} titles).`,
    `- ${n(noWard)} titles (${pct(noWard, total)}) carry no ward marker. ${n(namesPlace)} of them contain a name the OpenStreetMap gazetteer holds for their State, but of 30 such titles read by a person only 4 named the locality of the work; 11 were another place of that name, a road's name, a person or a common word (section 8).`,
    `- The locality fallback is built: data/wards/india-localities.json, ${n(localities.count)} named places, ${n(localitiesBytes.length)} bytes, ODbL. It is thin exactly where the notices are: ${n(localities.states.UP.count)} places for Uttar Pradesh against ${n(localities.states.AP.count)} for Andhra Pradesh (section 9).`);

  // ------------------------------------------------------------------ 2. by State
  const stateRows = states.slice().sort((left, right) => right.notices - left.notices).map((row) => {
    const mine = snapshots.filter(({ source }) => source.state === row.state);
    const no = SOURCES.filter((source) => !committable(source) && source.state === row.state);
    const unread = SOURCES.filter((source) => source.unopened && source.state === row.state);
    return [
      `${row.state} ${STATE_NAMES[row.state] || ""}`.trim(), n(row.notices), n(row.urban),
      row.bodies.slice(0, 3).map(([label, count]) => `${label} ${count}`).join("; ") || "none named urban",
      mine.length ? mine.map(({ source, snapshot }) => `${source.id} (${snapshot.count})`).join(", ") : "none",
      [...mine.map(({ snapshot }) => licenceShort(snapshot.provenance.licence)),
        ...no.map((source) => `refused: ${source.id}`), ...unread.map((source) => `open licence, file not opened: ${source.id}`)].filter((value, at, list) => list.indexOf(value) === at).join("; ") || "",
    ];
  });
  say("## 2. Notices and boundary sources by State",
    "",
    `"From urban bodies" counts a notice only where the portal's organisation chain names the body (or, in Bihar, Chhattisgarh and Jharkhand, where the title does). Snapshot ids are followed by the number of wards.`,
    "",
    table(["State", "Notices", "From urban bodies", "Bodies with the most notices", "Ward polygons committed", "Licence, and what was refused"], stateRows),
    "",
    "States the app ships no road notices for: Andhra Pradesh, Karnataka (it has its own tender pack), Mizoram. Lakshadweep, Nagaland and Sikkim have packs with no notice in them.");

  // ------------------------------------------------------------------ 3. top 40
  say("## 3. The 40 urban bodies with the most notices",
    "",
    table(["#", "Body", "State", "Notices", "Ward polygons"], ranked.slice(0, 40).map((entry, at) => {
      const mine = snapshotsFor(entry.state, entry.city);
      const no = refusedFor(entry.state, entry.city);
      const unread = SOURCES.filter((source) => source.unopened && source.notices?.state === entry.state && source.notices.city === entry.city);
      const cell = mine.length ? mine.map(({ source, snapshot }) => `${source.id}: match by ${snapshot.use.by}`).join("; ")
        : unread.length ? `none; an open-licence file exists and could not be opened: ${unread.map((source) => source.id).join(", ")}`
          : no.length ? `refused: ${no.map((source) => source.id).join(", ")}` : entry.kind === "Development Authority" ? "none (a development authority has no wards)" : "none";
      return [at + 1, entry.label, entry.state, entry.count, cell];
    })),
    "",
    `These 40 tendered ${n(ranked.slice(0, 40).reduce((sum, entry) => sum + entry.count, 0))} notices. ${ranked.slice(0, 40).filter((entry) => snapshotsFor(entry.state, entry.city).length).length} of them have committed polygons.`);

  // ------------------------------------------------------------------ 4. packs
  const packs = packGeometry();
  say("## 4. Boundary geometry the repo already held",
    "",
    `In ${packs.manifest}: ${packs.outlines} State and UT outlines (OpenStreetMap), and these polygons below State level. No routing pack holds a ward of any city but Mumbai.`,
    "",
    table(["State", "Pack", "Where in the pack", "Polygons", "What", "Source recorded in the pack"], packs.rows),
    "",
    "Hyderabad (in-tg-routing) and Karnataka's urban bodies (in-ka-routing) are answered by asking an official service one point at a time; those packs hold no geometry. "
    + "The Ahmedabad outline is a union of OpenCity's 48 Ahmedabad wards, and OpenCity's Ahmedabad ward dataset names livingatlas.esri.in as its source (section 5): the pack records the outline as ODbL, while the Esri India layer forbids export. That is for the owner to look at; nothing here changes the pack.");

  // ------------------------------------------------------------------ 5. sources
  const sourceRows = [];
  for (const [state, city, osm, note] of CITIES) {
    const label = city === null ? "Delhi (all of the NCT)" : CITY_LABEL[city] || city;
    const count = cityCount(state, city);
    const lines = [];
    for (const { source, snapshot, entry } of snapshotsFor(state, city)) {
      lines.push([`${source.id} (committed)`, source.page, source.publisher, licenceShort(snapshot.provenance.licence), snapshot.source_last_edited || (snapshot.provenance.osm_data_as_of ? `OpenStreetMap as of ${snapshot.provenance.osm_data_as_of.slice(0, 10)}` : "undated"), `${snapshot.provenance.features_in_source} in the file, ${snapshot.count} kept`, snapshot.source_fields, `committed, ${n(entry.bytes)} bytes; match by ${snapshot.use.by}`]);
    }
    for (const source of refusedFor(state, city)) {
      const measured = refused.find((item) => item.source.id === source.id);
      lines.push([`${source.id} (refused)`, source.page, source.publisher, source.licence, source.vintage, source.counted ?? (measured ? measured.snapshot.provenance.features_in_source : "see registry"), source.source_fields, `refused: ${source.licence_status} licence`]);
    }
    for (const extra of EXTRA[`${state}/${city}`] || []) {
      lines.push([extra.name, extra.url, extra.publisher, extra.licence, extra.date, extra.count, extra.field, extra.decision]);
    }
    if (!lines.length) lines.push(["none", "", "", "", "", "", "", note || "Nothing found."]);
    lines.forEach((line, at) => sourceRows.push([at ? "" : `${label}, ${state}`, at ? "" : count, at ? "" : osm, ...line]));
    if (lines[0][0] !== "none" && note) sourceRows.push(["", "", "", "note", "", "", "", "", "", "", note]);
  }
  say("## 5. Sources opened for the cities asked about",
    "",
    `The 25 cities whose urban bodies tendered the most notices, then the metros named in the brief. Every source in the table was opened on ${OPENED_ON} unless its row says it could not be: the count is the number of ward polygons in the file or returned by the service that day. `
    + "\"OpenStreetMap\" is what the Geofabrik India extract (data of 6 Oct 2026) holds as administrative relations at level 9 or 10 around the city.",
    "",
    table(["City", "Notices of its bodies", "Wards in OpenStreetMap", "Source", "URL", "Publisher", "Licence", "Date", "Ward polygons", "Field with the ward number or name", "Decision"], sourceRows),
    "",
    "Where every city was looked for, and what each place holds:",
    "",
    "- **DataMeet Municipal_Spatial_Data** (github.com/datameet/Municipal_Spatial_Data, last pushed 28 Feb 2024): 27 folders. Licence: CC BY 4.0 unless a folder says otherwise, and most say CC BY-SA 2.5 India. 24 ward files from 23 folders are committed. Not used: Bangalore (Karnataka has its own register), Hyderabad (a 2018 copy of OpenStreetMap, fetched fresh instead), MMR and Mira-Bhayandar (outlines, no wards), and the superseded files beside the ones taken (Chennai 2008, Coimbatore's census wards, Pune 2012 and 2017).",
    "- **OpenStreetMap**: wards are mapped as boundaries for Chennai, Coimbatore, Tiruchirappalli, Hyderabad, Bhubaneswar, Chandigarh, Mumbai and Gangtok, and nowhere else outside Karnataka. Kerala's local body wards are not in it.",
    "- **OpenCity** (data.opencity.in): 20 ward datasets read. 14 name livingatlas.esri.in as their source and are refused; the rest repeat DataMeet's files or copy an unlicensed official layer. One is used: Pune's 41 prabhags of 2025.",
    `- **Esri India Living Atlas, \"India Ward Boundaries\"** (arcgis.com item 9ebf199936d24f4bb05ed913aa3bd19d, modified 16 Jun 2026): by its own description \"more than 700 cities\", Ghaziabad and Noida among them. Its licence text: \"Esri India Master Terms of Use ... Users are not permitted to export data for offline use.\" Refused. It is the only source found for most cities on the list.`,
    "- **Open Government Data Platform** (catalogue read through smartcities.data.gov.in, whose footer puts every dataset under the Government Open Data License - India): of its 27 KML and 50 ZIP resources, four are ward boundaries: Jabalpur, Agra, Dehradun and Thanjavur (all July 2020), plus a Pimpri Chinchwad record with no file. The file host www.data.gov.in answered HTTP 403 to this tool, so none was opened. They are in the registry (MP/jabalpur, UP/agra, UK/dehradun, TN/thanjavur): `node infra/aws-central/tools/snapshot-india-wards.mjs --city MP/jabalpur --raw <downloaded file>`.",
    "- **Official services with no licence**: Delhi (GSDL, the 250 wards of 2022), Hyderabad (TGRAC, 155 wards and 885 wards of surrounding municipalities), Pune (the corporation's WFS, 72 prabhags; its capabilities say Fees NONE and AccessConstraints NONE, which are GeoServer's defaults), Mumbai (BMC on ArcGIS Online, 227 electoral wards), Chennai (the corporation's GIS, 200 wards of 2025). All opened, counted, and refused. The first three can be snapshotted for measuring with `--allow-unlicensed`; nothing from them is committed.",
    "- **Kerala**: the Delimitation Commission's 2025 ward map (wardmap.ksmart.live) is a viewer; its tile service refused this tool (HTTP 403). The State SDI (opensdi.kerala.gov.in) did not answer. Kerala is 1,378 notices, 862 of them with a ward number, and has no ward polygons anyone may copy.",
    "- **West Bengal**: the State's urban GIS (nagargispariseva.wb.gov.in), which serves municipal boundaries, did not answer (two connection timeouts).");

  // ------------------------------------------------------------------ 6. snapshots
  say("## 6. What is committed",
    "",
    table(["Snapshot", "Body", "Wards", "Named", "Numbered", "Bytes", "Publisher's date", "Left out, and why", "Match by", "How that is known"], snapshots.map(({ entry, snapshot }) => {
      const p = snapshot.provenance;
      const left = [
        p.features_left_out_no_number_or_name ? `${p.features_left_out_no_number_or_name} polygon(s) with no number or name` : "",
        p.wards_left_out_no_usable_ring.length ? `wards ${p.wards_left_out_no_usable_ring.join(", ")}: the file draws them as a dot` : "",
        p.wards_left_out_inside_another_ward.length ? `wards ${p.wards_left_out_inside_another_ward.join(" and ")}: one is drawn inside the other` : "",
        ...(p.wards_left_out_not_a_ward || []).map((item) => `${item.ward}: a whole zone drawn as one ward`),
        ...(p.relations_left_out_boundary_not_closed || []).map((name) => `${name.replace(/ \(relation.*$/, "")}: boundary not closed in OpenStreetMap`),
      ].filter(Boolean).join("; ");
      return [entry.id, snapshot.body, snapshot.count, snapshot.named, snapshot.numbered, n(entry.bytes), snapshot.source_last_edited || `OpenStreetMap as of ${p.osm_data_as_of?.slice(0, 10) || snapshot.retrieved_at}`, left || "nothing", snapshot.use.by, snapshot.use.evidence];
    })),
    "",
    `Total: ${index.count} snapshots, ${n(index.wards)} wards, ${n(index.snapshots.reduce((sum, entry) => sum + entry.bytes, 0))} bytes, plus the locality gazetteer (${n(index.localities?.bytes || 0)} bytes): ${n(index.bytes)} bytes against a 60 MB budget. `
    + "Each file's provenance block gives the source URL, publisher, licence, required attribution, dates and the SHA-256 of the raw download; `vintage` and `caveats` say which delimitation it is.",
    "",
    "Checks every snapshot passes (infra/aws-central/test/india-wards.test.mjs): rings closed, at least four points, enclosing at least 100 square units, inside the State's box; no two wards of a body sharing more than 10% (the worst pair is 7.4%, in Kanpur); every snapshot identical to what the tool builds from its raw download.",
    "",
    "Is each file drawn where the city is? For every named ward, the gazetteer places of the same name inside the snapshot's box:",
    "",
    table(["Snapshot", "Same-name places", "In the ward of their name", "In another ward", "In no ward"], snapshots.filter(({ measure }) => measure.position.places > 0)
      .map(({ entry, measure }) => [entry.id, measure.position.places, measure.position.in_own_ward, measure.position.in_another_ward, measure.position.in_no_ward])),
    "",
    "A place node is a label, not an area, and \"Kaloor\" answers to both Kaloor North and Kaloor South, so \"in another ward\" is mostly the next ward along. The table says two things: the files are georeferenced where their cities are, and a place that carries a ward's name lies inside that ward only a little over half the time. That second point is why a file of locality points cannot stand in for ward polygons.");

  // ------------------------------------------------------------------ 7. matching
  const measured = [...snapshots.map((item) => ({ ...item, kind: "committed" })), ...refused.map((item) => ({ ...item, kind: "refused, measured only" }))]
    .filter(({ measure }) => measure.notices > 0).sort((left, right) => right.measure.notices_of_body - left.measure.notices_of_body);
  say("## 7. Matching, measured",
    "",
    "For each snapshot, the notices of its State that the body tendered, plus notices of no urban body that name the city in the title or as the tendering office. "
    + "\"By name\" is the service's own rule (service/ward-tenders.mjs, imported unchanged): a ward's name found in the title as a place name. "
    + "\"By number\" is the same module's `wardNumbers`. \"Broader markers\" adds the spellings that parser does not read (Div, Dn, D, W, Prabhag).",
    "",
    table(["Snapshot", "Notices (body + naming the city)", "Wards", "By name: wards with a notice", "By name: notices naming a ward", "Notices with a ward number", "... whose number the file draws", "By number: wards with a notice", "Ward number by broader markers", "Zone or circle number read as a ward"],
      measured.map(({ source, kind, measure }) => [
        `${source.id}${kind === "committed" ? "" : " (refused)"}`, `${measure.notices} (${measure.notices_of_body} + ${measure.notices_naming_city})`, measure.wards,
        measure.by_name.wards_with_a_notice, measure.by_name.notices_naming_a_ward, measure.by_number.notices_with_a_ward_number,
        measure.by_number.notices_whose_number_is_drawn, measure.by_number.wards_with_a_notice, measure.by_number.notices_with_a_number_by_broader_markers,
        measure.by_number.notices_where_a_zone_or_circle_number_is_read_as_a_ward,
      ])),
    "",
    `Snapshots of bodies with no notice in the packs are left out of the table: ${snapshots.filter(({ measure }) => measure.notices === 0).map(({ entry }) => entry.id).join(", ")}.`,
    "",
    "The three refused official layers were snapshotted outside the repo on 7 Oct 2026 and measured the same way against the same notices, to show what a licence would buy. Delhi's 250 wards of 2022: 5 wards named by 5 of 21 notices (the 2017 file: 10 wards, 6 notices, most of it \"Rohini\"). TGRAC's 155 Hyderabad wards: 8 wards named by 6 of 18 notices, and the same new-numbering mismatch as the OpenStreetMap wards. Pune's 72 prabhags: 1 named by 1 of 10 notices. "
    + "In the tenders the app holds today, current official polygons would change little: the limit is what the titles say, not the map. `--with-unlicensed` repeats the measurement on a machine that has fetched them.");

  const numbering = measured.filter(({ measure }) => measure.numbering.agree + measure.numbering.disagree > 0);
  say("### Is the file's numbering the tenders' numbering?",
    "",
    "Where a title gives a name beside its one ward number (\"Ward 30 Ambedkar Nagar\") and the file has a Latin-letter name for that number, the two can be compared:",
    "",
    table(["Snapshot", "Titles compared", "Same name", "Another name", "Example"], numbering.map(({ source, measure }) => [
      source.id, measure.numbering.agree + measure.numbering.disagree, measure.numbering.agree, measure.numbering.disagree, short(measure.numbering.examples[0] || "", 170),
    ])),
    "",
    "Bhopal's names are in Devanagari and cannot be compared this way; its numbering was checked by geography in the hand-read below (the places seven titles name lie in or within 100 m of the ward of the title's number). "
    + "For Chennai, Coimbatore, Bhubaneswar, Faridabad, Kolkata and the other number-only files nothing in the repo can test the numbering: the vintage in each provenance block is the guide.");

  // hand-read
  if (handread) {
    const rows = [];
    const detail = [];
    const stale = [];
    for (const [id, city] of Object.entries(handread.cities)) {
      const measure = measureOf(id);
      const sample = measure ? samplePairs(measure, city.seed) : [];
      const same = sample.length === city.pairs.length && sample.every((pair, at) => pair.notice.tender_id === city.pairs[at].tender_id && pair.ward.code === city.pairs[at].ward_code && pair.basis === city.pairs[at].basis);
      if (!same) stale.push(id);
      const count = (basis, verdict) => city.pairs.filter((pair) => (basis ? pair.basis === basis : true) && pair.verdict === verdict).length;
      const of = (basis) => city.pairs.filter((pair) => pair.basis === basis).length;
      rows.push([id, city.pairs_in_all, city.pairs.length, count(null, "right"), count(null, "wrong"), count(null, "one of several"), count(null, "cannot tell"),
        `${count("name", "wrong")} of ${of("name")}`, `${count("number", "wrong")} of ${of("number")}`]);
      const reasons = new Map();
      for (const pair of city.pairs.filter((item) => item.verdict === "wrong")) {
        const kind = /read (the zone number|Circle|circle|Cir)/.test(pair.reason) ? "the parser read a zone or circle number as a ward"
          : /road|corridor|far end/.test(pair.reason) ? "the name is a road's, or one end of one"
            : /circle;|is the circle/.test(pair.reason) ? "the name is the circle's, a larger unit"
              : pair.basis === "number" ? "the file's number is another delimitation's" : "another place of that name";
        reasons.set(kind, (reasons.get(kind) || 0) + 1);
      }
      detail.push(`- **${id}**: ${[...reasons].map(([kind, value]) => `${value} ${kind}`).join("; ") || "none wrong"}. ${city.pairs.find((pair) => pair.verdict === "wrong") ? `For example: "${short(city.pairs.find((pair) => pair.verdict === "wrong").title, 120)}" against ward ${city.pairs.find((pair) => pair.verdict === "wrong").ward}: ${city.pairs.find((pair) => pair.verdict === "wrong").reason}.` : ""}`);
    }
    say("### Pairs read by a person",
      "",
      `Read on ${handread.read_on}: the first 30 pairs of a seeded sample for each of the five bodies with the most notices and a committed snapshot (Kanpur 83, Bhopal 50, Ahmedabad 46, Delhi 21, then Hyderabad and Jaipur level at 18, so six). A body with fewer than 30 pairs had all of them read. `
      + `Evidence used: ${handread.evidence}. Every pair, verdict and reason is in data/wards/handread.json.`,
      ...(stale.length ? ["", `The reading is of the notices in ${handread.notices}. The packs or the snapshots have changed since for ${stale.join(", ")}: the pairs below are the record of that day, not today's sample (print today's with \`--pairs\`).`] : []),
      "",
      table(["Snapshot", "Pairs in all", "Read", "Right", "Wrong", "One name over several wards", "Cannot tell", "Wrong among name pairs", "Wrong among number pairs"], rows),
      "",
      "What was wrong:",
      "",
      ...detail,
      "",
      "Delhi's ten \"one name over several wards\" pairs are two notices about four Rohini sectors matched to all five wards whose name starts with Rohini: the work can lie in at most two wards per notice, so at least six of the ten are wrong.",
      "",
      "What this decides for the runtime:",
      "",
      "1. Outside Bengaluru a ward number is usable only with the body and only where the snapshot's delimitation is the tenders'. Each snapshot's `use` field says which it is: `by: number` for Bhopal, `by: name` for Kanpur, Hyderabad, Delhi and Ahmedabad, `by: nothing` for Jaipur and Chandigarh.",
      "2. A zone, circle or borough number must not be read as a ward. All 12 wrong Bhopal pairs and 10 of Hyderabad's 15 are this.",
      "3. A ward's name on the title's own ward marker (\"Ward-17 Cherlapally\", \"Vatva Ward of the South Zone\", \"Ward-11, Safipur\") was right every time it was read: Ahmedabad 30 of 30, Hyderabad 3 of 3, Kanpur 1 of 1. A ward's name anywhere else in a title was right once (Babupurwa, Kanpur) and otherwise a road (\"Bawana-Auchandi Road\"), a circle (\"Kapra-Circle\"), a sub-city (\"Rohini\") or a locality that two wards share (\"Yashoda Nagar\").",
      "4. A source can be wrong in ways no licence shows. Kanpur's file drew two whole zones as one ward each, Navi Mumbai's drew ward 39 inside ward 41, Kochi's drew three wards as dots. Those are left out and named in each provenance block; the same reading is owed to any source added later.");
  }

  // ------------------------------------------------------------------ 8. per State
  say("## 8. Ward marker or locality, by State",
    "",
    "For every title: does it carry a ward marker (the service's parser: a ward number, or a name on the word \"ward\"), a marker only the broader spellings catch, or none? "
    + "For the titles with none: does a place name in the title equal the spelling key of a place the gazetteer holds for that State (keys of five letters or more), or does the title name a road (\"... Road\", \"from ... to ...\")?",
    "",
    table(["State", "Notices", "Ward marker", "Broader marker only", "No ward", "No ward: share of all", "... names a gazetteer place", "... names a road", "... names either", "... names neither", "Gazetteer places in the State"],
      states.slice().sort((left, right) => right.notices - left.notices).filter((row) => row.notices).map((row) => [
        row.state, n(row.notices), n(row.ward_marker), row.ward_marker_broader_only, n(row.no_ward), pct(row.no_ward, row.notices), n(row.names_gazetteer_place), n(row.names_road), n(row.names_place_or_road), row.names_neither, n(row.gazetteer_places),
      ])),
    "",
    `All States: ${n(total)} notices; ${n(withMarker)} with a ward marker, ${n(withBroadOnly)} more by the broader spellings, ${n(noWard)} with none (${pct(noWard, total)}). Of those, ${n(namesPlace)} (${pct(namesPlace, noWard)}) contain a name the gazetteer holds and ${n(states.reduce((sum, row) => sum + row.names_place_or_road, 0))} (${pct(states.reduce((sum, row) => sum + row.names_place_or_road, 0), noWard)}) name a gazetteer place or a road.`,
    "",
    "\"Names a road\" is nearly every title: a road notice says from where to where. It is counted to show that these titles are about a stretch between two landmarks, usually houses (\"from Kanti Sharma to Panna Lal\"), which no gazetteer holds.");
  if (handread?.gazetteer_hits) {
    const hits = handread.gazetteer_hits;
    const sample = sampleGazetteerHits(states, hits.seed);
    const same = sample.length === hits.pairs.length && sample.every((hit, at) => hit.notice.tender_id === hits.pairs[at].tender_id && hit.place === hits.pairs[at].place);
    const count = (verdict) => hits.pairs.filter((pair) => pair.verdict === verdict).length;
    say("### Is a gazetteer hit the place of the work?",
      "",
      `Thirty of the titles that name a gazetteer place, seeded sample, read on ${handread.read_on}${same ? "" : ` against the notices in ${handread.notices} (the packs have changed since; this is the record of that day)`}:`,
      "",
      table(["Verdict", "Titles", "Examples"], [
        ["the locality of the work", count("locality"), hits.pairs.filter((pair) => pair.verdict === "locality").slice(0, 2).map((pair) => `${pair.place}: ${pair.reason}`).join("; ")],
        ["the district, taluka, block or tendering body", count("larger unit"), hits.pairs.filter((pair) => pair.verdict === "larger unit").slice(0, 2).map((pair) => `${pair.place}: ${pair.reason}`).join("; ")],
        ["wrong: another place of that name, a road's name, a person, a common word", count("wrong"), hits.pairs.filter((pair) => pair.verdict === "wrong").slice(0, 4).map((pair) => `${pair.place}: ${pair.reason}`).join("; ")],
        ["cannot tell", count("cannot tell"), hits.pairs.filter((pair) => pair.verdict === "cannot tell").slice(0, 2).map((pair) => `${pair.place}: ${pair.reason}`).join("; ")],
      ]),
      "",
      `So the ${pct(namesPlace, noWard)} above is a ceiling on what a name lookup against the whole State can do, and a loose one. The name rules were built for a ward roster of a few hundred names in one city; against tens of thousands of village names in a State they find \"water\" (Vatera) and a contractor's neighbour (Padham). `
      + "A gazetteer is safe only the other way round: start from the pothole's coordinates, take the handful of places within a kilometre or two, and look for those few names in the titles of that body.");
  }

  // ------------------------------------------------------------------ 9. gazetteer
  const kinds = localities.kind_counts;
  const top = Object.entries(localities.states).sort((left, right) => right[1].count - left[1].count);
  const noticeOf = Object.fromEntries(states.map((row) => [row.state, row.notices]));
  say("## 9. Locality fallback: what was assessed, what was built",
    "",
    table(["Candidate", "Opened?", "Licence", "Size", "Covers", "Verdict"], [
      ["OpenStreetMap place points (Geofabrik India extract)", `yes: ${localities.provenance.source_file}, ${n(localities.provenance.raw_bytes)} bytes, data of ${localities.provenance.osm_data_as_of?.slice(0, 10)}`, "ODbL 1.0, attribution required", `${n(localitiesBytes.length)} bytes as built (${n(localitiesGzip)} gzipped)`, `${n(localities.count)} named places in ${Object.keys(localities.states).length} States and UTs`, "Built and committed"],
      ["OpenStreetMap administrative boundaries below sub-district", "yes, same extract", "ODbL 1.0", "not built", "55,123 level 9 and 2,553 level 10 relations, but 45,746 of the level 9 are in Madhya Pradesh alone (village outlines) and 5,229 in Maharashtra; Uttar Pradesh has 153, West Bengal 40, Bihar 4, Kerala 4", "Not built: no coverage where the notices are"],
      ["Local Government Directory: \"Villages with PIN Codes\" and \"Local Bodies with PIN Codes\" (Ministry of Panchayati Raj, 22 Sep 2022)", "catalogue records read; the files (CSV, 16.5 MB and 0.1 MB) are on www.data.gov.in, which answered HTTP 403 to this tool", "Government Open Data License - India", "16.5 MB as published", "names of villages and local bodies with their pincode; a list, with no coordinates or boundaries by its description", "Not usable alone: a name list cannot place a point"],
      ["India Post: \"All India Pincode Directory\" (Department of Posts, 4 Dec 2020)", "catalogue record read; the file (CSV, 23.8 MB) could not be opened, same refusal", "Government Open Data License - India", "23.8 MB as published", "post offices by pincode, district and State; a pincode is coarser than a ward and carries a post office's name", "Not usable alone: no boundaries. No pincode boundary dataset was found by a title search of the catalogue"],
      ["Survey of India village and town boundaries", "not found: a title search of the Open Government Data catalogue for village and town boundaries returned no boundary dataset in its first 600 results", "not established", "unknown", "unknown", "Not assessed"],
    ]),
    "",
    `**What the built file holds.** ${n(localities.count)} places: ${n(kinds.v)} villages, ${n(kinds.h)} hamlets, ${n(kinds.n)} neighbourhoods, ${n(kinds.s)} suburbs, ${n(kinds.l)} localities, ${n(kinds.t)} towns, ${n(kinds.q)} quarters, ${n(kinds.c)} cities. `
    + `Left out and counted in its provenance: ${n(localities.provenance.counts.unnamed)} places with no name, ${n(localities.provenance.counts.no_latin_name)} with no Latin-letter name, ${n(localities.provenance.counts.outside_every_state)} outside every State outline, ${n(localities.provenance.counts.mapped_twice)} mapped twice. `
    + "Per State it is four parallel lists sorted south to north, so a lookup cuts by latitude and scans a few dozen entries; `placesNear` in tools/build-india-localities.mjs is a working reader (the service will want its own).",
    "",
    "**Coverage is uneven, and thin where the notices are.**",
    "",
    table(["State", "Places", "Road notices"], [...top.slice(0, 6), ...top.filter(([code]) => ["UP", "KL", "WB", "MP", "JK", "BR"].includes(code) && !top.slice(0, 6).some(([held]) => held === code))]
      .map(([code, state]) => [`${code} ${STATE_NAMES[code]}`, n(state.count), n(noticeOf[code] || 0)])),
    "",
    `Around the towns that tender most, the file holds this many neighbourhood, quarter, suburb and locality places within about 9 km of the town's own place node: ${[["UP", "Ghaziabad"], ["UP", "Kanpur"], ["UP", "Shamli"], ["UP", "Ramkola"], ["BR", "Bettiah"], ["MP", "Gwalior"], ["MP", "Bhopal"], ["GJ", "Ahmedabad"], ["OD", "Soro"], ["TG", "Karimnagar"], ["JK", "Srinagar"], ["RJ", "Alwar"], ["UP", "Saharanpur"], ["MP", "Sagar"], ["UP", "Pilibhit"], ["MP", "Khargone"]].map(([code, town]) => `${town} ${urbanPlacesNear(code, town) ?? "not in the file"}`).join(", ")}. `
    + "The test suite pins one case: a point in Indirapuram, Ghaziabad gets two housing societies and the suburb next door, because OpenStreetMap has no place called Indirapuram.",
    "",
    "**Recommendation.** Ship the file (it is small, clearly licensed, and needs no network), and use it in one direction only: from the pothole's coordinates to the two or three nearest place names, which are then looked for in the titles of the body that owns the point. "
    + "Do not use it to place a title on the map, and do not treat the nearest place as the ward. Expect it to add something in the big cities and almost nothing in the small towns of Uttar Pradesh, Bihar, Odisha and West Bengal, where the titles name houses and lanes that no public dataset holds.");

  // ------------------------------------------------------------------ 10. plainly
  const kl = states.find((row) => row.state === "KL");
  const up = states.find((row) => row.state === "UP");
  say("## 10. What \"all of India\" can and cannot mean",
    "",
    `**It can mean** ${covered.size} bodies in ${new Set(index.snapshots.map((entry) => entry.state_code)).size} States and UTs today: a pothole's coordinate becomes a ward, from files anyone may copy, in Ahmedabad, Bhopal, Chennai, Bhubaneswar, Coimbatore, Lucknow, Mumbai, Kolkata, Pune and the rest of section 6. For the tenders the app holds now, that reaches ${n(coveredNotices)} of ${n(total)} notices, and a tested, usable match in two bodies: Ahmedabad by ward name and Bhopal by ward number.`,
    "",
    "**It cannot mean, with data that exists and may be copied:**",
    "",
    `- The bodies that tender the most. Ghaziabad (${n(cityCount("UP", "Ghaziabad"))} notices), Shamli, Ramkola, Bettiah, Gwalior, Soro, Karimnagar, Srinagar and Alwar have no ward polygons under any licence that allows copying. For most of them the only map found is Esri India's, which forbids export.`,
    `- Kerala. ${n(kl.notices)} notices, ${n(kl.ward_marker)} with a ward number, almost all from grama panchayats. The 2025 ward map exists and is public to look at, not to download.`,
    `- Uttar Pradesh beyond Kanpur and Lucknow: ${n(up.notices)} notices, ${n(up.ward_marker)} with a ward number, from ${up.bodies.length} urban bodies, and ward files for two of them (one of which is half drawn and numbered for an older delimitation).`,
    "- Current wards in the cities that were re-delimited after their open file was drawn: Delhi (2022), Chandigarh (2021), Jaipur (2020), Hyderabad (2026), Kochi (2025), Pune's named file (2025). Their official current layers were opened and counted; none carries a reuse licence.",
    "",
    "**What would change that, in order of notices gained:**",
    "",
    "1. A licence, or the owner's decision to treat a public official service as usable, for the official layers that were opened and counted: Delhi GSDL (250 wards), TGRAC (155 Hyderabad wards, 885 municipal wards) and Pune's own prabhags, which are in the registry behind `--allow-unlicensed`, and the Mumbai and Chennai corporations' own ward layers, which are not. The Kolkata and Pune outlines in the routing packs already rest on that kind of source (the West Bengal pack says so itself: \"no explicit reuse licence was published\").",
    "2. Someone fetching the four Government Open Data License files in a browser (Jabalpur has 25 notices, Agra 5) and running the registry command.",
    "3. Asking Ghaziabad Nagar Nigam, Kerala's Delimitation Commission and the Uttar Pradesh Directorate of Local Bodies for their ward layers. No public download of any of them was found.",
    "",
    "Until then, the honest product outside these bodies is the one the titles support without any polygon: the body that owns the point (from the tendering office) and the ward number the citizen can read off a property tax bill, matched to \"Ward No. N\" in that body's titles.");

  return `${out.join("\n").replace(/[\u2013\u2014]/g, "-").replace(/\n{3,}/g, "\n\n").trimEnd()}\n`;
}
