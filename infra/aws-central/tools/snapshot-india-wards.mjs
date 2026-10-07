#!/usr/bin/env node
// Snapshots municipal ward polygons for cities outside Karnataka into
// data/wards/<STATE>/<city>.json, in the shape of data/karnataka-ward-polygons.json
// (integer WGS84 coordinates at coordinate_scale, each ring [x0, y0, dx1, dy1, ...]), so
// one loader reads both. Offline tool: the service does not import it.
//
//   node infra/aws-central/tools/snapshot-india-wards.mjs --list
//     Every source in the registry: licence, whether it may be committed, and why not.
//
//   node infra/aws-central/tools/snapshot-india-wards.mjs --city UP/kanpur
//     Downloads that source (one request at a time, real User-Agent), keeps the raw
//     download under data/wards/.work/raw/ (gitignored) and writes the snapshot.
//     --offline reads the raw file already in .work instead of the network.
//     --raw <file> reads a file a person downloaded (for hosts that refuse this tool).
//
//   node infra/aws-central/tools/snapshot-india-wards.mjs --all [--offline]
//     Every source whose licence allows redistribution, then the index.
//
//   node infra/aws-central/tools/snapshot-india-wards.mjs --verify
//     Opens every registry source and prints the HTTP status and the feature count it
//     holds today. Writes nothing.
//
//   node infra/aws-central/tools/snapshot-india-wards.mjs --index
//     Rebuilds data/wards/index.json (path, hash, bytes and count of every snapshot).
//
// A source whose licence is unclear, proprietary or non-commercial is never written under
// data/wards/<STATE>/: `--city` on one of those needs --allow-unlicensed and writes to
// data/wards/.work/unlicensed/, for measuring only.
//
// Nothing here draws geometry. Rings are copied from the source, rounded to
// coordinate_scale (about 1.1 m), closed where the source left a ring open, and dropped
// only when rounding leaves fewer than four points; every such change is counted in the
// snapshot's provenance block.
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { pointInRings } from "../service/spatial.mjs";

export const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
export const WARDS_DIR = path.join(root, "data/wards");
export const WORK_DIR = path.join(WARDS_DIR, ".work");
export const INDEX_PATH = path.join(WARDS_DIR, "index.json");
export const SCALE = 100_000;
export const SNAPSHOT_FORMAT = "pothole-india-ward-polygons";
export const INDEX_FORMAT = "pothole-india-ward-index";
export const USER_AGENT = "PotholeReporter-data/1 (+https://coding-parrot.github.io/pothole-reporter/; contact@aiengg.dev)";

const sha256 = (bytes) => crypto.createHash("sha256").update(bytes).digest("hex");
const today = () => new Date().toISOString().slice(0, 10);
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// ---------------------------------------------------------------------------------------
// Licences. "open" may be committed. Everything else is listed, measured and left out.
// ---------------------------------------------------------------------------------------
const ODBL = {
  licence: "Open Data Commons Open Database License (ODbL) 1.0",
  licence_url: "https://opendatacommons.org/licenses/odbl/1-0/",
  licence_status: "open",
};
const CC_BY_4 = {
  licence: "Creative Commons Attribution 4.0 International (CC BY 4.0)",
  licence_url: "https://creativecommons.org/licenses/by/4.0/",
  licence_status: "open",
};
const CC_BY_SA_25_IN = {
  licence: "Creative Commons Attribution-ShareAlike 2.5 India (CC BY-SA 2.5 IN)",
  licence_url: "https://creativecommons.org/licenses/by-sa/2.5/in/",
  licence_status: "open",
};
const OPENCITY_PD = {
  licence: "Other (Public Domain), as stated on the OpenCity dataset page",
  licence_url: "https://data.opencity.in/",
  licence_status: "open",
};
const GODL = {
  licence: "Government Open Data License - India (GODL-India)",
  licence_url: "https://www.data.gov.in/Godl",
  licence_status: "open",
};
const NO_LICENCE = {
  licence: "No reuse licence published with the service",
  licence_url: null,
  licence_status: "unclear",
};
const ESRI_INDIA = {
  licence: "Esri India Master Terms of Use (\"Users are not permitted to export data for offline use\")",
  licence_url: "https://www.arcgis.com/home/item.html?id=9ebf199936d24f4bb05ed913aa3bd19d",
  licence_status: "proprietary",
};

const datameet = (folder, file) => ({
  kind: "geojson",
  url: `https://raw.githubusercontent.com/datameet/Municipal_Spatial_Data/master/${folder}/${encodeURIComponent(file)}`,
  page: `https://github.com/datameet/Municipal_Spatial_Data/tree/master/${folder}`,
  publisher: "DataMeet India community (Municipal_Spatial_Data)",
  last_edited_api: `https://api.github.com/repos/datameet/Municipal_Spatial_Data/commits?path=${folder}/${encodeURIComponent(file)}&per_page=1`,
});
const datameetAttribution = (folder, file, licence) => `${folder} Municipal Spatial Data `
  + `(https://github.com/datameet/Municipal_Spatial_Data/blob/master/${folder}/${file}) by DataMeet India community `
  + `(http://datameet.org/), ${licence}`;
// OpenStreetMap, asked of Overpass for one bounding box and one name pattern.
const overpass = (level, namePattern, [south, west, north, east]) => ({
  kind: "overpass",
  url: "https://overpass-api.de/api/interpreter",
  query: `[out:json][timeout:120];relation["boundary"="administrative"]["admin_level"="${level}"]["name"~"${namePattern}"]`
    + `(${south},${west},${north},${east});out geom;`,
  page: "https://www.openstreetmap.org/",
  publisher: "OpenStreetMap contributors",
  attribution: "© OpenStreetMap contributors, ODbL 1.0 (https://www.openstreetmap.org/copyright)",
  ...ODBL,
});

// A name as the source writes it, with runs of white space made one space and a long dash
// (U+2013, U+2014) written as a hyphen: one Pune prabhag is "Mahatma Phule Smarak \u2013Bhavani Peth".
const text = (value) => {
  const cleaned = String(value ?? "").replace(/[\u2013\u2014]/g, "-").replace(/\s+/g, " ").trim();
  return cleaned || null;
};
const number = (value) => {
  const match = /\d+/.exec(String(value ?? ""));
  return match && Number(match[0]) > 0 ? String(Number(match[0])) : null;
};
// "Ward 3 Cherlapally", "48 RAMOL HATHIJAN", "Ward 116".
const numberThenName = (value) => {
  const match = /^\s*(?:ward\s*(?:no\.?)?\s*)?(\d+)\s*[-.:]?\s*(.*)$/i.exec(String(value ?? ""));
  return match ? { no: String(Number(match[1])), name: text(match[2]) } : { no: null, name: text(value) };
};

// ---------------------------------------------------------------------------------------
// The registry. One entry per source that was opened and read on 7 Oct 2026. `notices`
// says which road notices belong to the body (the coverage tool reads it).
// ---------------------------------------------------------------------------------------
const opencity = (dataset, resource, file) => ({
  kind: "kml",
  url: `https://data.opencity.in/dataset/${dataset}/resource/${resource}/download/${file}`,
  publisher: "OpenCity (Oorvani Foundation), data.opencity.in",
});

// "dm" builds a DataMeet entry: folder, file, licence, then what differs.
const dm = (id, body, folder, file, licence, rest) => {
  const [state, city] = id.split("/");
  return {
    id, state, city, body,
    ...datameet(folder, file), ...licence,
    attribution: datameetAttribution(folder, file, licence === CC_BY_4 ? "CC BY 4.0" : "CC BY-SA 2.5 IN"),
    caveats: [],
    ...rest,
  };
};
const osm = (id, body, level, pattern, box, rest) => {
  const [state, city] = id.split("/");
  return { id, state, city, body, ...overpass(level, pattern, box), source_fields: "name", caveats: [], ...rest };
};

export const SOURCES = [
  // ------------------------------------------------------------------------------------
  // Committed: the file we download carries an open licence stated by its publisher.
  // ------------------------------------------------------------------------------------
  dm("UP/kanpur", "Kanpur Municipal Corporation", "Kanpur", "Kanpur_wards.geojson", CC_BY_4, {
    read: (p) => ({ no: number(p["Ward No"]), name: text(p["Ward Name"]), zone: text(p["Zone No"]) }),
    source_fields: "Ward No,Ward Name,Zone No",
    upstream: "No source note in the folder (the readme is a title only), so the repository's default licence applies; the folder also holds a scanned Kanpur Nagar Nigam map.",
    vintage: "Undated. The file draws 58 polygons; Kanpur Nagar Nigam has 110 wards.",
    caveats: [
      "Covers about half of the corporation: 58 polygons for 110 wards.",
      "Coordinates are Web Mercator with no CRS declared; converted to WGS84 by this tool.",
    ],
    notices: { state: "UP", city: "Kanpur", names: /\bkanpur\b(?!\s+dehat)/i },
  }),
  dm("UP/lucknow", "Lucknow Municipal Corporation", "Lucknow", "Lucknow_ward_boundary.geojson", CC_BY_4, {
    read: (p) => ({ no: number(p["Ward Num"]), name: text(p["Ward Name"]), zone: text(p.Zone) }),
    source_fields: "Ward Num,Ward Name,Zone",
    upstream: "No source note in the folder (the readme is a title only), so the repository's default licence applies; the folder also holds a scanned Lucknow map.",
    vintage: "Undated 110-ward map (plus Airport and Cantonment, which carry no number).",
    notices: { state: "UP", city: "Lucknow", names: /\blucknow\b/i },
  }),
  dm("MP/bhopal", "Bhopal Municipal Corporation", "Bhopal", "Bhopal_wards.geojson", CC_BY_SA_25_IN, {
    read: (p) => ({ no: number(p.Ward_Number), name: text(p.Name), zone: text(p.zone) }),
    source_fields: "Ward_Number,Name,zone",
    upstream: "DataMeet: \"liberated from a Google Map present on the Bhopal Municipal Corporation website\" (bhopalmunicipal.com/city-information/informative-map.html).",
    vintage: "85-ward map as the corporation published it; not checked against any later re-delimitation.",
    caveats: ["Ward names are in Devanagari; a Latin-letter tender title cannot be matched to them by name, only by ward number."],
    notices: { state: "MP", city: "Bhopal", names: /\bbhopal\b/i },
  }),
  dm("GJ/ahmedabad", "Ahmedabad Municipal Corporation", "Ahmedabad", "Wards.geojson", CC_BY_SA_25_IN, {
    read: (p) => numberThenName(p.Name),
    source_fields: "Name",
    upstream: "DataMeet: \"liberated from a Google Map present on the Ahmedabad Municipal Corporation website\" (google.com/maps/d/viewer?mid=1puWc7gN8WPNa2iYqboJypmfP3y8).",
    vintage: "The 48 wards in force since 2015.",
    notices: { state: "GJ", city: "Ahmedabad", names: /\bahmedabad\b|\bAMC\b/i },
  }),
  dm("GJ/vadodara", "Vadodara Municipal Corporation (administrative wards)", "Vadodara", "vardodara_wards.geojson", CC_BY_4, {
    read: (p) => ({ no: number(p.ward_no), name: text(p.ward_name) }),
    source_fields: "ward_no,ward_name",
    upstream: "DataMeet: \"sourced from VMC website\" (vmc.gov.in/AdministrativeWardwiseMap.aspx). No licence line in the folder, so the repository default applies.",
    vintage: "12 administrative wards as the corporation's site drew them when scraped; elections are held on 19 wards.",
    caveats: ["Administrative wards, not election wards."],
    notices: { state: "GJ", city: "Vadodara", names: /\bvadodara\b|\bVMC\b/i },
  }),
  dm("DL/delhi-2017", "Municipal Corporations of Delhi (2017 wards)", "Delhi", "Delhi_Wards.geojson", CC_BY_SA_25_IN, {
    read: (p) => ({ no: /^\d+$/.test(String(p.Ward_No)) ? number(p.Ward_No) : null, name: text(p.Ward_Name), zone: /^\d+$/.test(String(p.Ward_No)) ? null : text(String(p.Ward_No).replace(/_\d+$/, "")) }),
    source_fields: "Ward_No,Ward_Name",
    upstream: "DataMeet: \"scraped from an ArcGIS Online map\" (arcgis.com/home/item.html?id=7c4f1b9be6cc4cecbcd28ee5136898f7).",
    vintage: "The 272 wards of the three former corporations (2017 election), with 8 Delhi Cantonment and 9 NDMC charges. The unified MCD has had 250 wards since 2022.",
    caveats: ["Superseded numbering: match by name, never by number, for anything dated after 2022."],
    notices: { state: "DL", any: true },
  }),
  osm("TG/hyderabad", "Greater Hyderabad Municipal Corporation (150 election wards)", 10, "^Ward [0-9]", [17.2, 78.2, 17.62, 78.7], {
    read: (p) => numberThenName(p.name),
    upstream: "OpenStreetMap administrative relations, admin_level 10, named \"Ward <n> <name>\".",
    vintage: "The 150 GHMC election wards. G.O.Ms.No.292 (24 Dec 2025) reorganised the area into 12 zones and 60 circles and G.O.Ms.No.55 (11 Feb 2026) constituted three corporations; a ward's name survives that, its number may not.",
    caveats: ["Not every one of the 150 wards is mapped (see count)."],
    notices: { state: "TG", city: "Hyderabad", names: /\bhyderabad\b|\bGHMC\b|\bHYD\b/i },
  }),
  dm("TN/chennai", "Greater Chennai Corporation", "Chennai", "Wards.geojson", CC_BY_SA_25_IN, {
    read: (p) => ({ no: number(p.Ward_No), name: null, zone: text(p.Zone_Name) }),
    source_fields: "Ward_No,Zone_No,Zone_Name",
    upstream: "DataMeet: \"received from the Transparent Chennai team\".",
    vintage: "The 200 wards and 15 zones of the expanded corporation.",
    caveats: ["Numbers and zone names only: the file carries no ward names."],
    notices: { state: "TN", city: "Chennai", names: /\bchennai\b/i },
  }),
  dm("TN/coimbatore", "Coimbatore City Municipal Corporation", "Coimbatore", "Cbe2011Wards.geojson", CC_BY_SA_25_IN, {
    read: (p) => ({ no: number(p["2011WardNumbers"]), name: null, zone: text(p.Zone) }),
    source_fields: "2011WardNumbers,Zone",
    upstream: "DataMeet: \"the ward map of Coimbatore reorganized (100 wards)\".",
    vintage: "The 100 wards of the 2011 reorganisation.",
    caveats: ["Numbers only."],
    notices: { state: "TN", city: "Coimbatore", names: /\bcoimbatore\b/i },
  }),
  osm("TN/tiruchirappalli", "Tiruchirappalli City Municipal Corporation", 10, "^Ward [0-9]", [10.7, 78.6, 10.9, 78.8], {
    read: (p) => numberThenName(p.name),
    upstream: "OpenStreetMap administrative relations, admin_level 10, named \"Ward <n>\".",
    vintage: "65 wards.",
    caveats: ["Numbers only."],
    notices: { state: "TN", city: "Tiruchirappalli", names: /\btiruchirap+al+i\b|\btrichy\b/i },
  }),
  dm("OD/bhubaneswar", "Bhubaneswar Municipal Corporation", "Bhubaneswar", "Wards.GeoJSON", CC_BY_SA_25_IN, {
    read: (p) => ({ no: number(p.wardno), name: null, zone: text(p.municipalzone) }),
    source_fields: "wardno,municipalzone",
    upstream: "DataMeet: \"scraped from http://www.bhubaneswarone.in/home/index.html\" (the Bhubaneswar One portal of the development authority and the corporation). OpenCity republishes the same file as CC BY-SA.",
    vintage: "67 wards.",
    caveats: ["Numbers only."],
    notices: { state: "OD", city: "Bhubaneswar", names: /\bbhubaneswar\b|\bBMC\b/i },
  }),
  dm("CH/chandigarh-2016", "Municipal Corporation Chandigarh (26 wards)", "Chandigarh", "Chandigarh_Wards.geojson", CC_BY_SA_25_IN, {
    read: (p) => (/^\d+$/.test(String(p.Ward_name)) ? { no: number(p.Ward_name), name: null } : { no: null, name: text(p.Ward_name) }),
    source_fields: "Ward_name",
    upstream: "DataMeet; no source note in the folder.",
    vintage: "The 26 wards used until 2021. The corporation has had 35 wards since.",
    caveats: ["Superseded delimitation: these ward numbers are not today's."],
    notices: { state: "CH", city: "Chandigarh", any: true },
  }),
  dm("HR/faridabad", "Municipal Corporation Faridabad (40 wards)", "Faridabad", "Faridabad_Wards.geojson", CC_BY_SA_25_IN, {
    read: (p) => ({ no: number(p.Ward_No), name: null }),
    source_fields: "Ward_No",
    upstream: "DataMeet; no source note in the folder.",
    vintage: "40 wards as drawn; not checked against the later re-delimitation.",
    caveats: ["Numbers only."],
    notices: { state: "HR", city: "Faridabad", names: /\bfaridabad\b/i },
  }),
  dm("RJ/jaipur-2009", "Jaipur Municipal Corporation (77 wards)", "Jaipur", "Jaipur_Wards.geojson", CC_BY_SA_25_IN, {
    read: (p) => ({ no: number(p.WARD_NO), name: null, zone: text(p.ZONE_NAME) }),
    source_fields: "WARD_NO,ZONE_NAME,AC",
    upstream: "DataMeet; no source note in the folder.",
    vintage: "77 wards, the count Jaipur had before 2014. It had 91 wards from 2014 and 250 across two corporations from 2020.",
    caveats: ["Superseded delimitation: these ward numbers are not today's."],
    notices: { state: "RJ", city: "Jaipur", names: /\bjaipur\b/i },
  }),
  dm("RJ/kishangarh", "Kishangarh Municipal Council", "Kishangarh", "Kishangarh_Wards.geojson", CC_BY_SA_25_IN, {
    read: (p) => ({ no: number(p.Ward_No), name: null }),
    source_fields: "Ward_No",
    upstream: "DataMeet: \"digitised from a hardcopy of ward map and ward scripts taken from Kishangarh Municipal Council\".",
    vintage: "45 wards as drawn; undated.",
    caveats: ["Numbers only."],
    notices: { state: "RJ", city: "Kishangarh", names: /\bkishangarh\b/i },
  }),
  dm("WB/kolkata", "Kolkata Municipal Corporation", "Kolkata", "kolkata.geojson", CC_BY_SA_25_IN, {
    read: (p) => ({ no: number(p.WARD), name: null }),
    source_fields: "WARD",
    upstream: "DataMeet: \"shared by Justin (github.com/justinelliotmeyers) in a datameet thread\". OpenCity republishes the same 141 polygons as its 2022 map.",
    vintage: "141 wards. The corporation has 144; wards 142 to 144 (Joka) are not drawn.",
    caveats: ["Numbers only.", "Wards 142 to 144 are missing."],
    notices: { state: "WB", city: "Kolkata", names: /\bkolkata\b|\bKMC\b/i },
  }),
  dm("MH/mumbai", "Brihanmumbai Municipal Corporation (administrative wards)", "Mumbai", "BMC_Wards.geojson", CC_BY_SA_25_IN, {
    read: (p) => ({ no: null, name: text(p.name) }),
    source_fields: "name",
    upstream: "DataMeet (Pune chapter).",
    vintage: "The 24 lettered administrative wards (A to T); not checked against later splits.",
    caveats: ["Administrative wards are lettered. A title's \"Ward No.\" is an electoral ward: see MH/mumbai-electoral-2017."],
    notices: { state: "MH", city: "Mumbai", names: /\bmumbai\b|\bMCGM\b|\bBMC\b/i },
  }),
  dm("MH/mumbai-electoral-2017", "Brihanmumbai Municipal Corporation (227 electoral wards of 2017)", "Mumbai", "bmc_electoral_wards_2017", CC_BY_SA_25_IN, {
    read: (p) => ({ no: number(p.PRABHAG_NO), name: null, zone: text(p.WARD) }),
    source_fields: "PRABHAG_NO,WARD",
    upstream: "DataMeet (Pune chapter); electoral wards of the 2017 election, each with the administrative ward it falls in.",
    vintage: "The 227 electoral wards of the 2017 election.",
    caveats: ["Numbers only; `zone` is the lettered administrative ward."],
    notices: { state: "MH", city: "Mumbai", names: /\bmumbai\b|\bMCGM\b|\bBMC\b/i },
  }),
  {
    id: "MH/pune", state: "MH", city: "pune", body: "Pune Municipal Corporation (41 prabhags of 2025)",
    ...opencity("98f28dac-9158-46ee-a91e-a514d9af427c", "2badcc86-489c-4b7e-b7dd-a273ef01b798", "b7a3f392-238c-4c55-a3ed-0c13fd4aaa0a.kml"),
    page: "https://data.opencity.in/dataset/pune-wards-info", ...OPENCITY_PD,
    attribution: "PMC Wards Info (https://data.opencity.in/dataset/pune-wards-info), OpenCity; source named by OpenCity: Pune Municipal Corporation (pmc.gov.in)",
    read: (p) => ({ no: number(p.qwr), name: null }),
    source_fields: "qwr",
    upstream: "OpenCity resource \"PMC Electoral Wards 2025\": \"Electoral wards for 2025 with 41 wards\"; the dataset names pmc.gov.in as its source.",
    vintage: "The 41 prabhags of the 2025 delimitation.",
    caveats: [
      "Numbers only (field `qwr`).",
      "The licence is the one OpenCity states on the dataset page; Pune Municipal Corporation itself publishes no licence.",
    ],
    notices: { state: "MH", city: "Pune", names: /\bpune\b|\bPMC\b/i },
  },
  dm("MH/pune-2022", "Pune Municipal Corporation (58 prabhags notified in 2022)", "Pune", "pune-electoral-wards_2022.geojson", CC_BY_SA_25_IN, {
    read: (p) => ({ no: number(p.wardnum), name: text(p.Name2) }),
    source_fields: "wardnum,Name2,origin",
    upstream: "DataMeet: traced by SeerMaps Technologies from the PDF maps at pmc.gov.in/en/pmc-final-prabhag-rachna-2022, commissioned by Nikhil VJ; also MIT at github.com/answerquest/pune-2022-wards.",
    vintage: "The 58 prabhags notified in 2022, superseded by the 41 of 2025. Kept because it is the only Pune file with names.",
    caveats: ["Traced from PDF maps.", "Superseded numbering."],
    notices: { state: "MH", city: "Pune", names: /\bpune\b|\bPMC\b/i },
  }),
  dm("MH/navi-mumbai", "Navi Mumbai Municipal Corporation (111 electoral wards)", "NMMC", "NMC_ElectoralWards.geojson", CC_BY_SA_25_IN, {
    read: (p) => ({ no: number(p.Name), name: null }),
    source_fields: "Name",
    upstream: "DataMeet: \"Navi Mumbai's electoral wards. Source: http://www.nmmconline.com/ward-map\" (the corporation's site; file dated 18 Mar 2015).",
    vintage: "The 111 electoral wards of the 2015 election.",
    caveats: ["Numbers only."],
    notices: { state: "MH", city: "Navi Mumbai", names: /\bnavi mumbai\b|\bNMMC\b/i },
  }),
  dm("MH/pimpri-chinchwad", "Pimpri Chinchwad Municipal Corporation (electoral wards)", "PCMC", "pcmc-electoral-wards.geojson", CC_BY_SA_25_IN, {
    read: (p) => ({ no: number(p.wardnum), name: null, zone: text(p.zone) }),
    source_fields: "wardnum,zone,name",
    upstream: "DataMeet: \"Source: http://shelter-associates.org/spatial-slum-information (scraped)\".",
    vintage: "66 electoral wards as drawn; undated, and older than the present prabhag delimitation.",
    caveats: ["Numbers only.", "Likely superseded numbering."],
    notices: { state: "MH", city: "Pimpri Chinchwad", names: /\bpimpri\b|\bPCMC\b/i },
  }),
  dm("KL/kochi", "Kochi Municipal Corporation (74 divisions)", "Kochi", "KCH_wards.geojson", CC_BY_SA_25_IN, {
    read: (p) => ({ no: number(p.Ward_No), name: text(p.Ward_Name) }),
    source_fields: "Ward_No,Ward_Name",
    upstream: "DataMeet: \"the wards as of 2022, scraped from https://opensdi.kerala.gov.in/layers/opensdi_data:geonode:KSUDP_KCH_Ward_Boundary\" (Kerala State Spatial Data Infrastructure).",
    vintage: "The divisions as of 2022. Kerala re-delimited every local body in 2025.",
    caveats: ["Superseded by the 2025 delimitation."],
    notices: { state: "KL", city: "Kochi", names: /\bkochi\b|\bcochin\b/i },
  }),
  dm("AP/vijayawada", "Vijayawada Municipal Corporation", "Vijayawada", "Vijayawada_Wards.geojson", CC_BY_SA_25_IN, {
    read: (p) => ({ no: number(p.WARD_NO), name: null }),
    source_fields: "WARD_NO",
    upstream: "DataMeet; no source note in the folder.",
    vintage: "77 polygons as drawn; undated.",
    caveats: ["Numbers only.", "The app ships no Andhra Pradesh road notices, so nothing can be matched to these yet."],
    notices: { state: "AP", city: "Vijayawada", names: /\bvijayawada\b/i },
  }),
  dm("BR/katihar", "Katihar Municipal Corporation", "Katihar", "Katihar_Wards.geojson", CC_BY_SA_25_IN, {
    read: (p) => ({ no: number(p.Ward_No), name: null }),
    source_fields: "Ward_No",
    upstream: "DataMeet; no source note in the folder.",
    vintage: "45 wards as drawn; undated.",
    caveats: ["Numbers only."],
    notices: { state: "BR", city: "Katihar", names: /\bkatihar\b/i },
  }),
  dm("BR/purnia", "Purnia Municipal Corporation", "Purnia", "Purnia_Wards.geojson", CC_BY_SA_25_IN, {
    read: (p) => ({ no: number(p.Ward_No), name: null }),
    source_fields: "Ward_No",
    upstream: "DataMeet; no source note in the folder.",
    vintage: "43 wards as drawn; undated.",
    caveats: ["Numbers only."],
    notices: { state: "BR", city: "Purnia", names: /\bpurnia\b|\bpurnea\b/i },
  }),
  dm("BR/bodh-gaya", "Bodh Gaya Nagar Parishad", "Bodh_Gaya", "Bodh_Gaya_Wards.geojson", CC_BY_SA_25_IN, {
    read: (p) => ({ no: number(p.Ward_No), name: null }),
    source_fields: "Ward_No",
    upstream: "DataMeet; no source note in the folder.",
    vintage: "19 wards as drawn; undated.",
    caveats: ["Numbers only."],
    notices: { state: "BR", city: "Bodh Gaya", names: /\bbodh\s*gaya\b/i },
  }),
  osm("SK/gangtok", "Gangtok Municipal Corporation", 10, ".", [27.28, 88.57, 27.38, 88.65], {
    read: (p) => ({ no: number(p.ref), name: text(String(p.name || "").replace(/\s+ward$/i, "")) }),
    source_fields: "name,ref",
    upstream: "OpenStreetMap administrative relations, admin_level 10.",
    vintage: "15 wards as mapped.",
    notices: { state: "SK", city: "Gangtok", names: /\bgangtok\b/i },
  }),
  // ------------------------------------------------------------------------------------
  // Opened and counted, never committed: no reuse licence, or a proprietary one.
  // ------------------------------------------------------------------------------------
  {
    id: "DL/delhi-mcd-2022", state: "DL", city: "delhi-mcd-2022", body: "Municipal Corporation of Delhi (250 wards of 2022)",
    kind: "arcgis", url: "https://gsdl.org.in/arcgis/rest/services/MCD/MCD_Ward_Zone_2022/MapServer/1",
    page: "https://gsdl.org.in/arcgis/rest/services/MCD/MCD_Ward_Zone_2022/MapServer",
    publisher: "Geospatial Delhi Limited (Government of NCT of Delhi)", ...NO_LICENCE,
    attribution: "Geospatial Delhi Limited, Government of NCT of Delhi",
    arcgis_fields: "Ward_No,WardName,AC_No,AC_Name", insecure_tls: true,
    read: (p) => ({ no: number(p.Ward_No), name: text(p.WardName) }),
    source_fields: "Ward_No,WardName",
    upstream: "Official service of the Delhi government's GIS company.",
    vintage: "The 250 wards of the 2022 delimitation, in force.",
    caveats: ["The service's copyrightText is empty and no terms page names a reuse licence."],
    notices: { state: "DL", any: true },
  },
  {
    id: "TG/hyderabad-tgrac", state: "TG", city: "hyderabad-tgrac", body: "Greater Hyderabad Municipal Corporation (TGRAC ward layer)",
    kind: "arcgis", url: "https://tgrac.telangana.gov.in/arcgis/rest/services/TCUR_Folder/TCUR_Telangana_Core_Urban_Region_V2/MapServer/55",
    page: "https://tgrac.telangana.gov.in/arcgis/rest/services/TCUR_Folder/TCUR_Telangana_Core_Urban_Region_V2/MapServer",
    publisher: "Telangana Remote Sensing Applications Centre (TGRAC), Government of Telangana", ...NO_LICENCE,
    attribution: "Telangana Remote Sensing Applications Centre (TGRAC), Government of Telangana",
    arcgis_fields: "ward,CIRCLE,ZONE", insecure_tls: true,
    read: (p) => ({ ...numberThenName(p.ward), zone: text(p.CIRCLE) }),
    source_fields: "ward,CIRCLE,ZONE",
    upstream: "Official service; the app already asks it one point at a time (in-tg-routing) and redistributes nothing from it. OpenCity's \"Greater Hyderabad Wards Map 2022\" is a copy of this layer (155 features, the same fields).",
    vintage: "155 ward polygons with circle and zone.",
    caveats: ["No reuse licence; the repo's own Telangana pack records \"no boundary geometry is redistributed\"."],
    notices: { state: "TG", city: "Hyderabad", names: /\bhyderabad\b|\bGHMC\b|\bHYD\b/i },
  },
  {
    id: "TG/core-urban-ulb-wards-tgrac", state: "TG", city: "core-urban-ulb-wards-tgrac", body: "Municipalities around Hyderabad (TGRAC \"Wards Municipality\" layer)",
    kind: "arcgis", url: "https://tgrac.telangana.gov.in/arcgis/rest/services/TCUR_Folder/TCUR_Telangana_Core_Urban_Region_V2/MapServer/48",
    page: "https://tgrac.telangana.gov.in/arcgis/rest/services/TCUR_Folder/TCUR_Telangana_Core_Urban_Region_V2/MapServer",
    publisher: "Telangana Remote Sensing Applications Centre (TGRAC), Government of Telangana", ...NO_LICENCE,
    attribution: "Telangana Remote Sensing Applications Centre (TGRAC), Government of Telangana",
    arcgis_fields: "Name,Ward_No,ULB_Name,Category", insecure_tls: true,
    read: (p) => ({ no: number(p.Ward_No), name: null, zone: text(p.ULB_Name) }),
    source_fields: "Ward_No,ULB_Name,Category",
    upstream: "Official service.",
    vintage: "885 ward polygons of the municipalities in the Core Urban Region.",
    caveats: ["No reuse licence.", "Several bodies in one layer: a ward number repeats once per body (ULB_Name, kept as `zone`)."],
    notices: { state: "TG", city: "Hyderabad", names: /\bhyderabad\b|\bGHMC\b|\bHYD\b/i },
  },
  {
    id: "MH/pune-pmc-wfs", state: "MH", city: "pune-pmc-wfs", body: "Pune Municipal Corporation (official prabhag layer)",
    kind: "geojson",
    url: "https://iwmsgis.pmc.gov.in/geoserver/pmc/ows?service=WFS&version=1.0.0&request=GetFeature&typeName=pmc:Prabhag_Boundary&outputFormat=application%2Fjson&srsName=EPSG%3A4326",
    page: "https://iwmsgis.pmc.gov.in/geoserver/pmc/ows?service=WFS&version=1.0.0&request=GetCapabilities",
    publisher: "Pune Municipal Corporation (IWMS GIS)", ...NO_LICENCE,
    licence: "No reuse licence. The WFS capabilities read Fees NONE and AccessConstraints NONE, which are GeoServer's defaults, not a grant.",
    attribution: "Pune Municipal Corporation",
    read: (p) => ({ no: number(p.prabhag_no ?? p.Prabhag_No ?? p.PRABHAG_NO ?? p.ward_no ?? p.Name ?? p.name), name: text(p.prabhag_na ?? p.Prabhag_Na ?? p.prabhag_name ?? null) }),
    source_fields: "(see --verify)",
    upstream: "Official service; the app's Maharashtra pack already copies the city outline (pmc:PMC_Boundary) from it.",
    vintage: "The corporation's own prabhag layer.",
    caveats: ["No explicit reuse licence."],
    notices: { state: "MH", city: "Pune", names: /\bpune\b|\bPMC\b/i },
  },
  {
    id: "IN/esri-india-ward-boundaries", state: "IN", city: "esri-india-ward-boundaries", body: "Esri India Living Atlas: India Ward Boundaries (\"more than 700 cities\")",
    kind: "arcgis-item", url: "https://www.arcgis.com/sharing/rest/content/items/9ebf199936d24f4bb05ed913aa3bd19d?f=json",
    page: "https://www.arcgis.com/home/item.html?id=9ebf199936d24f4bb05ed913aa3bd19d",
    publisher: "Esri India (credits: Government of India, State Municipal Corporations, Esri India)", ...ESRI_INDIA,
    attribution: "Esri India",
    source_fields: "n/a", read: () => ({ no: null, name: null }),
    upstream: "Esri India's compilation of municipal ward maps; needs an ArcGIS account to query.",
    vintage: "Catalogue item last modified 16 Jun 2026.",
    caveats: [
      "Proprietary. The item text says the layer is for online visualisation and analysis and that export for offline use is not permitted.",
      "OpenCity's 2024 ward maps for Ahmedabad, Bhopal, Indore, Jaipur, Kochi, Coimbatore, Madurai, Visakhapatnam, Navi Mumbai, Thane, Kalyan-Dombivli, Faridabad, Gurugram and Guwahati name livingatlas.esri.in as their source while carrying a Public Domain label; they are skipped for that conflict.",
    ],
    notices: null,
  },
];

export const sourceById = (id) => SOURCES.find((source) => source.id.toLowerCase() === String(id).toLowerCase());
export const committable = (source) => source.licence_status === "open";

// ---------------------------------------------------------------------------------------
// Network: one request at a time, at most one a second to a host, three tries with a
// growing wait.
// ---------------------------------------------------------------------------------------
const lastAsked = new Map();
async function politeFetch(url, options = {}, { insecure = false } = {}) {
  const host = new URL(url).host;
  let failure = null;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const wait = (lastAsked.get(host) || 0) + 1_100 - Date.now() + attempt * 20_000;
    if (wait > 0) await sleep(wait);
    lastAsked.set(host, Date.now());
    try {
      if (insecure) process.env.NODE_TLS_REJECT_UNAUTHORIZED = "0";
      const response = await fetch(url, {
        ...options,
        headers: { "User-Agent": USER_AGENT, ...(options.headers || {}) },
        signal: AbortSignal.timeout(300_000),
      });
      if (response.status === 429 || response.status >= 500) throw new Error(`${host} answered ${response.status}`);
      return response;
    } catch (error) {
      failure = error;
    } finally {
      if (insecure) delete process.env.NODE_TLS_REJECT_UNAUTHORIZED;
    }
  }
  throw failure;
}

const rawPath = (source) => path.join(WORK_DIR, "raw", `${source.state}-${source.city}.${source.kind === "kml" ? "kml" : "json"}`);

async function download(source) {
  if (source.kind === "overpass") {
    const response = await politeFetch(source.url, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: `data=${encodeURIComponent(source.query)}`,
    });
    if (!response.ok) throw new Error(`Overpass answered ${response.status} for ${source.id}`);
    return Buffer.from(await response.arrayBuffer());
  }
  if (source.kind === "arcgis") {
    const count = await (await politeFetch(`${source.url}/query?where=1%3D1&returnCountOnly=true&f=json`, {}, { insecure: source.insecure_tls })).json();
    if (!Number.isInteger(count.count)) throw new Error(`${source.id}: no count: ${JSON.stringify(count).slice(0, 200)}`);
    const features = [];
    // Old ArcGIS servers have no resultOffset, so pages are cut by object id.
    const ids = (await (await politeFetch(`${source.url}/query?where=1%3D1&returnIdsOnly=true&f=json`, {}, { insecure: source.insecure_tls })).json());
    const objectIds = (ids.objectIds || []).slice().sort((left, right) => left - right);
    if (objectIds.length !== count.count) throw new Error(`${source.id}: ${objectIds.length} ids for ${count.count} features`);
    for (let start = 0; start < objectIds.length; start += 100) {
      const page = objectIds.slice(start, start + 100);
      const response = await politeFetch(`${source.url}/query`, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: `objectIds=${page.join(",")}&outFields=${encodeURIComponent(source.arcgis_fields || "*")}&returnGeometry=true&outSR=4326&f=json`,
      }, { insecure: source.insecure_tls });
      const body = await response.json();
      if (!Array.isArray(body.features)) throw new Error(`${source.id}: page without features: ${JSON.stringify(body).slice(0, 200)}`);
      features.push(...body.features);
      console.log(`  fetched ${features.length} of ${objectIds.length}`);
    }
    if (features.length !== count.count) throw new Error(`${source.id}: fetched ${features.length} of ${count.count}`);
    return Buffer.from(JSON.stringify({ arcgis_layer: source.url, features }));
  }
  const response = await politeFetch(source.url, {}, { insecure: source.insecure_tls });
  if (!response.ok) throw new Error(`${new URL(source.url).host} answered ${response.status} for ${source.id}`);
  return Buffer.from(await response.arrayBuffer());
}

// The date the publisher last changed the file, where the publisher says.
async function lastEdited(source) {
  if (!source.last_edited_api) return null;
  try {
    const commits = await (await politeFetch(source.last_edited_api)).json();
    return String(commits?.[0]?.commit?.committer?.date || "").slice(0, 10) || null;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------------------
// Parsers: each returns [{ properties, polygons: [[ring, ...], ...] }] in the source's
// own coordinates, a ring being [[x, y], ...].
// ---------------------------------------------------------------------------------------
// A feature collection, or one feature per line (DataMeet's Kochi file).
function parseGeojson(bytes) {
  let data;
  try {
    data = JSON.parse(bytes);
  } catch {
    data = { features: bytes.toString("utf8").split("\n").filter((line) => line.trim()).map((line) => JSON.parse(line)) };
  }
  if (!Array.isArray(data.features)) throw new Error("Not a GeoJSON feature collection");
  return data.features.flatMap((feature) => {
    const geometry = feature.geometry;
    if (!geometry) return [];
    const polygons = geometry.type === "Polygon" ? [geometry.coordinates]
      : geometry.type === "MultiPolygon" ? geometry.coordinates : null;
    if (!polygons) return [];
    return [{ properties: feature.properties || {}, polygons }];
  });
}

function parseArcgis(bytes) {
  return JSON.parse(bytes).features.flatMap(({ attributes, geometry }) => (
    Array.isArray(geometry?.rings) ? [{ properties: attributes || {}, polygons: [geometry.rings] }] : []
  ));
}

const unescapeXml = (value) => String(value).replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, "$1")
  .replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, "\"").replace(/&apos;/g, "'").replace(/&amp;/g, "&");
export function parseKml(bytes) {
  const xml = bytes.toString("utf8");
  const features = [];
  for (const [, placemark] of xml.matchAll(/<Placemark\b[^>]*>([\s\S]*?)<\/Placemark>/g)) {
    const properties = {};
    const name = /<name>([\s\S]*?)<\/name>/.exec(placemark);
    if (name) properties.name = unescapeXml(name[1]).trim();
    for (const [, key, value] of placemark.matchAll(/<SimpleData name="([^"]+)">([\s\S]*?)<\/SimpleData>/g)) properties[key] = unescapeXml(value).trim();
    for (const [, key, value] of placemark.matchAll(/<Data name="([^"]+)">\s*(?:<displayName>[\s\S]*?<\/displayName>\s*)?<value>([\s\S]*?)<\/value>/g)) properties[key] = unescapeXml(value).trim();
    const polygons = [];
    for (const [, polygon] of placemark.matchAll(/<Polygon\b[^>]*>([\s\S]*?)<\/Polygon>/g)) {
      const rings = [];
      for (const [, coordinates] of polygon.matchAll(/<coordinates>([\s\S]*?)<\/coordinates>/g)) {
        rings.push(coordinates.trim().split(/\s+/).map((tuple) => tuple.split(",").slice(0, 2).map(Number)));
      }
      if (rings.length) polygons.push(rings);
    }
    if (polygons.length) features.push({ properties, polygons });
  }
  return features;
}

// Overpass `out geom`: a relation's member ways arrive as open lines. Ways that share an
// end point are joined until the line closes. Outer and inner ways are joined apart; the
// service tests a point by the even-odd rule over every ring, so no orientation is kept.
export function assembleRings(ways) {
  const lines = ways.map((way) => way.map(({ lon, lat }) => [lon, lat])).filter((line) => line.length >= 2);
  const key = ([x, y]) => `${x},${y}`;
  const rings = [];
  let open = 0;
  while (lines.length) {
    let ring = lines.pop();
    let grew = true;
    while (key(ring[0]) !== key(ring[ring.length - 1]) && grew) {
      grew = false;
      for (let index = 0; index < lines.length; index += 1) {
        const line = lines[index];
        const tail = key(ring[ring.length - 1]);
        const head = key(ring[0]);
        if (key(line[0]) === tail) ring = ring.concat(line.slice(1));
        else if (key(line[line.length - 1]) === tail) ring = ring.concat(line.slice(0, -1).reverse());
        else if (key(line[line.length - 1]) === head) ring = line.slice(0, -1).concat(ring);
        else if (key(line[0]) === head) ring = line.slice(1).reverse().concat(ring);
        else continue;
        lines.splice(index, 1);
        grew = true;
        break;
      }
    }
    if (key(ring[0]) === key(ring[ring.length - 1]) && ring.length >= 4) rings.push(ring);
    else open += 1;
  }
  return { rings, open };
}

function parseOverpass(bytes) {
  const data = JSON.parse(bytes);
  const features = [];
  // A relation whose boundary does not close is left out whole and named: half a ward
  // would answer for the wrong streets.
  const broken = [];
  for (const element of data.elements || []) {
    if (element.type !== "relation") continue;
    const members = (element.members || []).filter((member) => member.type === "way" && Array.isArray(member.geometry));
    const outer = assembleRings(members.filter((member) => member.role !== "inner").map((member) => member.geometry));
    const inner = assembleRings(members.filter((member) => member.role === "inner").map((member) => member.geometry));
    if (outer.open || inner.open || !outer.rings.length) {
      broken.push(`${element.tags?.name || "unnamed"} (relation ${element.id})`);
      continue;
    }
    features.push({ properties: { ...element.tags, osm_relation_id: element.id }, polygons: [[...outer.rings, ...inner.rings]] });
  }
  return Object.assign(features, { broken, relations: features.length + broken.length, osm_base: data.osm3s?.timestamp_osm_base || null });
}

// ---------------------------------------------------------------------------------------
// Normalising
// ---------------------------------------------------------------------------------------
const EARTH = 6_378_137;
const fromWebMercator = ([x, y]) => [
  (x / EARTH) * (180 / Math.PI),
  (2 * Math.atan(Math.exp(y / EARTH)) - Math.PI / 2) * (180 / Math.PI),
];

// A ring smaller than this is not a place: DataMeet's Kochi file draws ward 7 (Cheralai)
// as four points one centimetre apart. 100 squared units is about 120 square metres.
export const MIN_RING_AREA = 100;

// [x0, y0, dx1, dy1, ...] at SCALE, consecutive duplicates (after rounding) dropped, the
// ring closed. Null when fewer than four points are left or the ring encloses nothing.
export function encodeRing(positions, counters = {}) {
  const points = [];
  for (const [lng, lat] of positions) {
    const x = Math.round(lng * SCALE);
    const y = Math.round(lat * SCALE);
    const last = points[points.length - 1];
    if (last && last[0] === x && last[1] === y) continue;
    points.push([x, y]);
  }
  if (points.length && (points[0][0] !== points[points.length - 1][0] || points[0][1] !== points[points.length - 1][1])) {
    points.push([points[0][0], points[0][1]]);
    counters.closed = (counters.closed || 0) + 1;
  }
  if (points.length < 4) {
    counters.dropped = (counters.dropped || 0) + 1;
    return null;
  }
  // Twice the ring's area, in squared units of 1/SCALE degrees (a unit is about 1.1 m).
  let doubled = 0;
  for (let index = 1; index < points.length; index += 1) {
    doubled += points[index - 1][0] * points[index][1] - points[index][0] * points[index - 1][1];
  }
  if (Math.abs(doubled) / 2 < MIN_RING_AREA) {
    counters.degenerate = (counters.degenerate || 0) + 1;
    return null;
  }
  const out = [points[0][0], points[0][1]];
  for (let index = 1; index < points.length; index += 1) {
    out.push(points[index][0] - points[index - 1][0], points[index][1] - points[index - 1][1]);
  }
  return out;
}

export function boxOfRings(rings) {
  const box = [Infinity, Infinity, -Infinity, -Infinity];
  for (const run of rings) {
    let x = run[0];
    let y = run[1];
    for (let index = 0; index < run.length; index += 2) {
      if (index) {
        x += run[index];
        y += run[index + 1];
      }
      if (x < box[0]) box[0] = x;
      if (y < box[1]) box[1] = y;
      if (x > box[2]) box[2] = x;
      if (y > box[3]) box[3] = y;
    }
  }
  return box;
}

// Each State's bounding box, from the State outline the app already ships (OpenStreetMap,
// in the routing packs), in degrees: [west, south, east, north].
let stateBoxes = null;
export function stateBox(stateCode) {
  if (!stateBoxes) {
    stateBoxes = new Map();
    const dir = path.join(root, "static");
    const newest = fs.readdirSync(dir).map((name) => name.match(/^pack-manifest-v(\d+)\.(\d+)\.json$/)).filter(Boolean)
      .sort((left, right) => Number(left[1]) - Number(right[1]) || Number(left[2]) - Number(right[2])).pop()[0];
    const manifest = JSON.parse(fs.readFileSync(path.join(dir, newest), "utf8"));
    for (const resource of Object.values(manifest.resources)) {
      if (resource.kind !== "routing") continue;
      const payload = JSON.parse(fs.readFileSync(path.join(root, "docs", resource.path), "utf8")).payload;
      const regions = [payload?.region, ...Object.values(payload?.regions || {})].filter(Boolean);
      for (const region of regions) {
        const statewide = /^Full (?:State|Union Territory)|^Delhi NCT/.test(String(region.scope || ""));
        if (!statewide || !region.bbox) continue;
        stateBoxes.set(resource.state_code, [region.bbox.min_lng, region.bbox.min_lat, region.bbox.max_lng, region.bbox.max_lat]);
      }
    }
  }
  const box = stateBoxes.get(stateCode);
  if (!box) throw new Error(`No State outline in the routing packs for ${stateCode}`);
  return box;
}

// The State outlines are OpenStreetMap's and the wards are someone else's drawing of the
// same line. Five Delhi wards of the 2017 file overshoot the outline's box, the farthest
// (Madanpur Khadar, on the Yamuna) by 237 m; Chandigarh's westernmost by under 1 m. A
// ward may cross its State's box by this much (0.003 degrees is about 330 m) and no more.
export const STATE_BOX_PAD = 0.003;

// How much of each ward also lies in another ward of the same body: every ward is
// sampled on a 24 by 24 grid over its box, and the share of its inside points that a
// second ward also holds is that pair's overlap. Returns [{ a, b, share }], largest
// first, `a` being the ward measured. Deterministic: the grid is fixed.
export const OVERLAP_GRID = 24;
export function overlapPairs(wards) {
  const pairs = [];
  for (const ward of wards) {
    const inside = [];
    for (let i = 0; i < OVERLAP_GRID; i += 1) {
      for (let j = 0; j < OVERLAP_GRID; j += 1) {
        const x = ward.bbox[0] + ((i + 0.5) / OVERLAP_GRID) * (ward.bbox[2] - ward.bbox[0]);
        const y = ward.bbox[1] + ((j + 0.5) / OVERLAP_GRID) * (ward.bbox[3] - ward.bbox[1]);
        if (pointInRings(x, y, ward.rings)) inside.push([x, y]);
      }
    }
    if (!inside.length) continue;
    for (const other of wards) {
      if (other === ward || other.bbox[0] > ward.bbox[2] || other.bbox[2] < ward.bbox[0]
          || other.bbox[1] > ward.bbox[3] || other.bbox[3] < ward.bbox[1]) continue;
      const shared = inside.filter(([x, y]) => pointInRings(x, y, other.rings)).length;
      if (shared) pairs.push({ a: ward.code, b: other.code, share: shared / inside.length });
    }
  }
  return pairs.sort((left, right) => right.share - left.share || (left.a < right.a ? -1 : 1));
}
// A ward that lies mostly inside another ward of its body is a drawing error in the
// source (DataMeet's Navi Mumbai file draws ward 39 wholly inside ward 41). Nothing here
// can say which of the two is wrong, so both are left out and named.
export const CONTAINED_SHARE = 0.5;
// The most two kept wards may share. The worst pair in any snapshot on 7 Oct 2026 was
// 7.4% (two hand-digitised Kanpur wards); a boundary strip, not a second ward.
export const SLIVER_SHARE = 0.1;

const byNumberThenName = (left, right) => {
  const a = left.no === null ? Infinity : Number(left.no);
  const b = right.no === null ? Infinity : Number(right.no);
  return a - b || String(left.name || "").localeCompare(String(right.name || "")) || String(left.zone || "").localeCompare(String(right.zone || ""));
};

export function buildSnapshot(source, rawBytes, { retrievedAt = today(), sourceLastEdited = null, rawFrom = source.url } = {}) {
  const parsed = source.kind === "kml" ? parseKml(rawBytes)
    : source.kind === "overpass" ? parseOverpass(rawBytes)
      : source.kind === "arcgis" ? parseArcgis(rawBytes) : parseGeojson(rawBytes);
  if (!parsed.length) throw new Error(`${source.id}: the source holds no polygons`);
  const counters = { closed: 0, dropped: 0, degenerate: 0, merged: 0, anonymous: 0, emptied: [] };
  const firstPoint = parsed[0].polygons[0][0][0];
  const mercator = Math.abs(firstPoint[0]) > 180 || Math.abs(firstPoint[1]) > 90;
  const merged = new Map();
  for (const feature of parsed) {
    const fields = source.read(feature.properties);
    const rings = feature.polygons.flat()
      .map((ring) => encodeRing(mercator ? ring.map(fromWebMercator) : ring, counters))
      .filter(Boolean);
    if (!rings.length) {
      counters.emptied.push([fields.no, fields.name].filter(Boolean).join(" ") || "unnamed");
      continue;
    }
    // A polygon with neither a number nor a name cannot be matched or shown.
    if (fields.no === null && fields.name === null) {
      counters.anonymous += 1;
      continue;
    }
    // A ward drawn as several features with one number and one name is one ward.
    const slot = `${fields.zone ?? ""}|${fields.no ?? ""}|${fields.name ?? ""}`;
    const held = merged.get(slot);
    if (held) {
      held.rings.push(...rings);
      counters.merged += 1;
    } else merged.set(slot, { ...fields, rings });
  }
  const townCode = `${source.state}-${source.city}`;
  const [west, south, east, north] = stateBox(source.state).map((value) => value * SCALE);
  const pad = STATE_BOX_PAD * SCALE;
  const seen = new Map();
  const drawn = [...merged.values()].sort(byNumberThenName).map((ward) => {
    const bbox = boxOfRings(ward.rings);
    if (bbox[0] < west - pad || bbox[1] < south - pad || bbox[2] > east + pad || bbox[3] > north + pad) {
      throw new Error(`${source.id}: ward ${ward.no} ${ward.name} lies outside ${source.state}: ${bbox.map((value) => value / SCALE)}`);
    }
    const stem = `${townCode}-${ward.no ?? "x"}`;
    const repeat = (seen.get(stem) || 0) + 1;
    seen.set(stem, repeat);
    const entry = {
      code: repeat === 1 ? stem : `${stem}.${repeat}`,
      no: ward.no,
      name: ward.name,
      town_code: townCode,
      body: source.body,
      state: source.state,
    };
    if (ward.zone) entry.zone = ward.zone;
    entry.bbox = bbox;
    entry.rings = ward.rings;
    return entry;
  });
  const contained = new Set(overlapPairs(drawn).filter((pair) => pair.share > CONTAINED_SHARE).flatMap((pair) => [pair.a, pair.b]));
  const wards = drawn.filter((ward) => !contained.has(ward.code));
  const overlaps = overlapPairs(wards);
  return {
    _comment: `Ward polygons of ${source.body}, copied from the source named in provenance. Same shape as `
      + "data/karnataka-ward-polygons.json: WGS84 integers at coordinate_scale, each ring encoded as "
      + "[x0, y0, dx1, dy1, ...] and closed; a point is in a ward by the even-odd rule over all of its rings. "
      + `Rebuild with: node infra/aws-central/tools/snapshot-india-wards.mjs --city ${source.id}`,
    format: SNAPSHOT_FORMAT,
    schema_version: 1,
    state_code: source.state,
    city: source.city,
    body: source.body,
    town_code: townCode,
    source: source.url,
    source_fields: source.source_fields,
    source_last_edited: sourceLastEdited,
    retrieved_at: retrievedAt,
    spatial_reference: 4326,
    coordinate_scale: SCALE,
    provenance: {
      source_url: source.url,
      ...(source.query ? { source_query: source.query } : {}),
      source_page: source.page,
      publisher: source.publisher,
      upstream: source.upstream,
      licence: source.licence,
      licence_url: source.licence_url,
      licence_status: source.licence_status,
      attribution: source.attribution,
      retrieved_at: retrievedAt,
      retrieved_from: rawFrom,
      source_last_edited: sourceLastEdited,
      ...(parsed.osm_base ? { osm_data_as_of: parsed.osm_base } : {}),
      raw_sha256: sha256(rawBytes),
      raw_bytes: rawBytes.length,
      source_crs: mercator ? "EPSG:3857 (undeclared in the file; detected from the coordinate range and converted)" : "EPSG:4326",
      vintage: source.vintage,
      caveats: source.caveats,
      features_in_source: parsed.relations ?? parsed.length,
      features_merged_same_number_and_name: counters.merged,
      features_left_out_no_number_or_name: counters.anonymous,
      rings_closed_by_tool: counters.closed,
      rings_dropped_under_four_points: counters.dropped,
      rings_dropped_enclosing_nothing: counters.degenerate,
      wards_left_out_no_usable_ring: counters.emptied,
      wards_left_out_inside_another_ward: drawn.filter((ward) => contained.has(ward.code)).map((ward) => [ward.no, ward.name].filter(Boolean).join(" ")),
      overlap: {
        method: `share of a ward's ${OVERLAP_GRID}x${OVERLAP_GRID} grid samples that a second ward of the body also holds`,
        worst_pair_share: overlaps.length ? Number(overlaps[0].share.toFixed(4)) : 0,
        worst_pair: overlaps.length ? [overlaps[0].a, overlaps[0].b] : null,
        pairs_over_1_percent: overlaps.filter((pair) => pair.share > 0.01).length,
      },
      ...(parsed.broken?.length ? { relations_left_out_boundary_not_closed: parsed.broken } : {}),
    },
    count: wards.length,
    named: wards.filter((ward) => ward.name).length,
    numbered: wards.filter((ward) => ward.no).length,
    wards,
  };
}

export const snapshotPath = (source) => (committable(source)
  ? path.join(WARDS_DIR, source.state, `${source.city}.json`)
  : path.join(WORK_DIR, "unlicensed", source.state, `${source.city}.json`));

function writeJsonIfChanged(file, value) {
  const bytes = Buffer.from(`${JSON.stringify(value)}\n`);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  if (fs.existsSync(file) && fs.readFileSync(file).equals(bytes)) {
    console.log(`unchanged ${path.relative(root, file)} (${bytes.length} bytes)`);
    return;
  }
  fs.writeFileSync(file, bytes);
  console.log(`wrote ${path.relative(root, file)} (${bytes.length} bytes)`);
}

async function snapshotOne(source, { offline, rawFile, allowUnlicensed }) {
  if (source.kind === "arcgis-item") throw new Error(`${source.id} is a catalogue record, not a download`);
  if (!committable(source) && !allowUnlicensed) {
    throw new Error(`${source.id} is not committable (${source.licence}). `
      + "Pass --allow-unlicensed to write it under data/wards/.work/unlicensed/ for measuring only.");
  }
  const held = rawFile || rawPath(source);
  let bytes;
  let rawFrom = source.url;
  // A snapshot rebuilt from the raw download keeps the dates of that download.
  const previous = fs.existsSync(snapshotPath(source)) ? JSON.parse(fs.readFileSync(snapshotPath(source), "utf8")) : null;
  let retrievedAt = today();
  let edited = null;
  if (rawFile || offline) {
    if (!fs.existsSync(held)) throw new Error(`${source.id}: no raw download at ${held}`);
    bytes = fs.readFileSync(held);
    if (rawFile) rawFrom = `${source.url} (downloaded by hand)`;
    if (previous?.provenance?.raw_sha256 === sha256(bytes)) {
      retrievedAt = previous.retrieved_at;
      edited = previous.source_last_edited;
    } else {
      retrievedAt = fs.statSync(held).mtime.toISOString().slice(0, 10);
      edited = offline ? await lastEdited(source) : null;
    }
  } else {
    console.log(`fetching ${source.id} from ${new URL(source.url).host}`);
    bytes = await download(source);
    fs.mkdirSync(path.dirname(rawPath(source)), { recursive: true });
    fs.writeFileSync(rawPath(source), bytes);
    edited = await lastEdited(source);
  }
  const snapshot = buildSnapshot(source, bytes, { retrievedAt, sourceLastEdited: edited, rawFrom });
  writeJsonIfChanged(snapshotPath(source), snapshot);
  console.log(`  ${source.id}: ${snapshot.count} wards (${snapshot.named} named, ${snapshot.numbered} numbered) from ${snapshot.provenance.features_in_source} features`);
  return snapshot;
}

export function buildIndex() {
  const snapshots = [];
  for (const source of SOURCES.filter(committable)) {
    const file = snapshotPath(source);
    if (!fs.existsSync(file)) continue;
    const bytes = fs.readFileSync(file);
    const snapshot = JSON.parse(bytes);
    snapshots.push({
      id: source.id,
      path: path.relative(root, file),
      state_code: snapshot.state_code,
      city: snapshot.city,
      body: snapshot.body,
      town_code: snapshot.town_code,
      count: snapshot.count,
      named: snapshot.named,
      numbered: snapshot.numbered,
      bytes: bytes.length,
      sha256: sha256(bytes),
      raw_sha256: snapshot.provenance.raw_sha256,
      source: snapshot.source,
      publisher: snapshot.provenance.publisher,
      licence: snapshot.provenance.licence,
      attribution: snapshot.provenance.attribution,
      retrieved_at: snapshot.retrieved_at,
      source_last_edited: snapshot.source_last_edited,
      vintage: snapshot.provenance.vintage,
    });
  }
  return {
    _comment: "Generated by infra/aws-central/tools/snapshot-india-wards.mjs --index. One row per ward snapshot under "
      + "data/wards/<STATE>/, with the hash of the file as committed. Do not edit by hand.",
    format: INDEX_FORMAT,
    schema_version: 1,
    snapshot_format: SNAPSHOT_FORMAT,
    coordinate_scale: SCALE,
    count: snapshots.length,
    wards: snapshots.reduce((sum, entry) => sum + entry.count, 0),
    bytes: snapshots.reduce((sum, entry) => sum + entry.bytes, 0),
    snapshots,
  };
}

async function verify() {
  for (const source of SOURCES) {
    try {
      if (source.kind === "arcgis") {
        const response = await politeFetch(`${source.url}/query?where=1%3D1&returnCountOnly=true&f=json`, {}, { insecure: source.insecure_tls });
        console.log(`${source.id}: HTTP ${response.status}, ${JSON.stringify(await response.json())} features, ${source.licence_status}`);
      } else if (source.kind === "arcgis-item") {
        const response = await politeFetch(source.url);
        const item = await response.json();
        console.log(`${source.id}: HTTP ${response.status}, item "${item.title}" modified ${new Date(item.modified).toISOString().slice(0, 10)}, ${source.licence_status}`);
      } else {
        const bytes = await download(source);
        const parsed = source.kind === "kml" ? parseKml(bytes) : source.kind === "overpass" ? parseOverpass(bytes) : parseGeojson(bytes);
        console.log(`${source.id}: HTTP 200, ${bytes.length} bytes, ${parsed.length} polygon features, sha256 ${sha256(bytes)}, ${source.licence_status}`);
      }
    } catch (error) {
      console.log(`${source.id}: FAILED ${error.message}`);
    }
  }
}

async function main() {
  const args = process.argv.slice(2);
  const flag = (name) => args.includes(name);
  const value = (name) => (args.includes(name) ? args[args.indexOf(name) + 1] : null);
  const options = { offline: flag("--offline"), rawFile: value("--raw"), allowUnlicensed: flag("--allow-unlicensed") };
  if (flag("--list")) {
    for (const source of SOURCES) {
      console.log(`${source.id.padEnd(30)} ${committable(source) ? "commit " : "skip   "} ${source.licence_status.padEnd(11)} ${source.kind.padEnd(11)} ${source.publisher}`);
    }
    return;
  }
  if (flag("--verify")) return verify();
  if (value("--city")) {
    const source = sourceById(value("--city"));
    if (!source) throw new Error(`No source ${value("--city")}; see --list`);
    await snapshotOne(source, options);
  } else if (flag("--all")) {
    for (const source of SOURCES.filter(committable)) await snapshotOne(source, options);
  } else if (!flag("--index")) {
    throw new Error("Nothing to do: pass --list, --verify, --city <STATE/city>, --all or --index");
  }
  writeJsonIfChanged(INDEX_PATH, buildIndex());
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) await main();
