// Reads the State/UT road notice packs the app ships (static/road-notice-manifest-*.json)
// and says which urban body each notice belongs to. Used only by the offline ward tools
// (india-ward-inventory.mjs, india-ward-coverage.mjs); the service does not import it.
//
// Nothing here is guessed from a title's place names. A notice gets an urban body only
// where the portal's own organisation chain names one ("Directorate of Local Bodies UP ||
// Ghaziabad Municipal Corporation") or, on the three portals whose chain stops at the
// department (Bihar, Chhattisgarh, Jharkhand), where the title itself says "under Bettiah
// Municipal Corporation" or "Nagar Panchayat Ramgarh".
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");

export function newestManifest(prefix) {
  const dir = path.join(root, "static");
  const versioned = fs.readdirSync(dir)
    .map((name) => name.match(new RegExp(`^${prefix}-v(\\d+)\\.(\\d+)\\.json$`)))
    .filter(Boolean)
    .sort((left, right) => Number(left[1]) - Number(right[1]) || Number(left[2]) - Number(right[2]));
  if (!versioned.length) throw new Error(`No versioned ${prefix} in static/`);
  return path.join(dir, versioned[versioned.length - 1][0]);
}

// Every notice of every State/UT pack, hash-checked against the manifest.
export function loadRoadNotices() {
  const manifestPath = newestManifest("road-notice-manifest");
  const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
  const notices = [];
  const states = [];
  for (const resource of Object.values(manifest.resources)) {
    const bytes = fs.readFileSync(path.join(root, "docs", resource.path));
    if (crypto.createHash("sha256").update(bytes).digest("hex") !== resource.sha256) {
      throw new Error(`Road notice pack ${resource.pack_id} does not match its manifest hash`);
    }
    const pack = JSON.parse(bytes);
    states.push({ state: resource.state_code, notices: pack.notices.length, retrieved_at: resource.source_retrieved_at });
    for (const notice of pack.notices) {
      notices.push({
        state: resource.state_code,
        title: String(notice.title || ""),
        chain: String(notice.organisation_chain || "").split("||").map((part) => part.trim()).filter(Boolean),
        tender_id: notice.tender_id,
        closing_at: notice.closing_at || null,
      });
    }
  }
  return { manifest: path.basename(manifestPath), generated_at: manifest.generated_at, states, notices };
}

const KINDS = [
  ["Municipal Corporation", /\b(?:mun+i?cipal|minicipal)\s+corporation\b|\bnagar\s+nigam\b|\bmahanagar\s+(?:seva\s+sadan|palika)\b|\bcorporation\s+of\b|\b(?:city\s+)?corporation\b(?!\s+(?:limited|ltd))/i],
  ["Municipal Council", /\bnagar\s*pali(?:ka|ke)(?:\s+parishad)?\b|\bnagarpalika\b|\bmunicipal\s+council\b|\bmunicipality\b|\bnagar\s+seva\s+sadan\b|\bnagar\s+parishad\b/i],
  ["Town Panchayat", /\bnagar\s*a?\s*panchayath?\b|\bnagarapanchayath?\b|\btown\s+panchayath?\b|\bNAC\b|\bnotified\s+area\b/i],
  ["Development Authority", /\bdevelopment\s+authority\b|\bvikas\s+pradhikaran\b|\bUIT\b|\bGMDA\b|\bHMDA\b|\bHUDA\b|\bGMADA\b|\bPDA\b|\bimprovement\s+trust\b/i],
];
// Words in a chain segment that are the office, not the town.
const NOISE = new RegExp([
  "mun+i?cipal(?:ity|ities)?", "minicipal", "muncipal", "corporations?", "nagar\\s*nigam", "mahanagar", "nagar\\s+seva\\s+sadan", "seva\\s+sadan",
  "nagar\\s*pali(?:ka|ke)", "nagarpalika", "parishad", "council", "nagar\\s*a?\\s*panchayath?", "nagarapanchayath?",
  "town\\s+panchayat", "NAC", "notified\\s+area\\s+committe?e?", "development\\s+authority", "UAD", "UP", "city",
  "commissioner", "CEO", "EO", "mayor", "superintendent\\s+engineer", "office\\s+of(?:\\s+the)?", "engineers?",
  "telangana", "of", "the", "dist", "greater", "new", "secretary", "nagar(?=\\s*$)", "ADB\\s+Tender", "UDandUHD",
].map((word) => `\\b${word}\\b`).join("|"), "gi");

function tidy(name) {
  const words = String(name).replace(NOISE, " ").replace(/[^A-Za-z.\s-]/g, " ").replace(/[-.]/g, " ")
    .split(/\s+/).filter(Boolean)
    .map((word) => word[0].toUpperCase() + word.slice(1).toLowerCase());
  // "Shamli Shamli" (town, then district of the same name) is Shamli.
  return words.filter((word, index) => word !== words[index - 1]).join(" ");
}

// One spelling per town where the portals disagree with each other or with the town's
// usual name, and the bodies that are one city (Hyderabad has been three corporations
// since G.O.Ms.No.55 of 11 Feb 2026; Ahmedabad's AMC tenders are filed by zone).
const CITY = new Map(Object.entries({
  hissar: "Hisar", gurgaon: "Gurugram", chennai: "Chennai", "okla industrial": "Noida", pilibheet: "Pilibhit",
  cyberabad: "Hyderabad", malkajgiri: "Hyderabad", hyderabad: "Hyderabad", "kalyan dombivli": "Kalyan-Dombivli",
  "s a s nagar": "SAS Nagar (Mohali)", mumbai: "Mumbai", "coimbatore": "Coimbatore", "vasai virar": "Vasai-Virar",
  gmda: "Gurugram", huda: "Haryana (HSVP estates)", gmada: "SAS Nagar (Mohali)", pda: "Patiala", "uit udaipur": "Udaipur",
  "uit kota": "Kota", "uit bhilwara": "Bhilwara", "uit sikar": "Sikar", "uit alwar": "Alwar", "rgia hyd rgia hyderabad": "Hyderabad (RGIA)",
  "mumbai metropolitan region": "Mumbai", "tripura urban planning and": "Agartala", "yamuna expressway industrial": "Yamuna Expressway",
  "noida industrial": "Greater Noida", "bharuch ankleshwar urban": "Bharuch-Ankleshwar",
}));

function body(kind, raw, how) {
  const name = tidy(raw);
  if (!name) return null;
  const city = CITY.get(name.toLowerCase()) || name;
  return { kind, city, label: `${city} ${kind}`, how };
}

const kindOf = (text) => KINDS.find(([, pattern]) => pattern.test(text))?.[0] || null;

// The urban body a notice belongs to, or null (a PWD division, a zilla parishad, a rural
// works division, an irrigation circle).
export function urbanBodyOf(notice) {
  const { state, chain, title } = notice;
  const joined = chain.join(" || ");
  // Portals that file urban bodies under one department with the town in a later segment.
  if (state === "GJ") {
    const amc = /^(AMC|SMC|VMC|RMC|BMC|JMC|GMC)-(.*)$/.exec(chain[0] || "");
    if (amc) {
      const city = { AMC: "Ahmedabad", SMC: "Surat", VMC: "Vadodara", RMC: "Rajkot", BMC: "Bhavnagar", JMC: "Jamnagar", GMC: "Gandhinagar" }[amc[1]];
      return { kind: "Municipal Corporation", city, label: `${city} Municipal Corporation`, how: "chain" };
    }
    const palika = /^NAGARPALIKA-(.*)$/i.exec(chain[0] || "");
    if (palika) return body("Municipal Council", palika[1], "chain");
  }
  if (state === "WB" && /^MUNICIPAL AFFAIRS/i.test(chain[0] || "") && chain[2]) {
    const corporation = /\bMC$/i.test(chain[2]);
    return body(corporation ? "Municipal Corporation" : "Municipal Council", chain[2].replace(/\bMC$/i, ""), "chain");
  }
  if (state === "HR" && /^Urban Local Bodies$/i.test(chain[1] || "") && chain[2]) {
    // Haryana writes "MC" for corporation, council and committee alike.
    return body("Urban Local Body", chain[2].replace(/^MC\s+/i, ""), "chain");
  }
  if (state === "RJ" && chain[0] === "DLB" && chain[2]) {
    return body("Urban Local Body", chain[2].replace(/^(?:Commissioner|CEO|EO)\s*-\s*/i, ""), "chain");
  }
  if (state === "MP") {
    const town = /(?:Mun+i?cipal Corporation|Nagar Palika|Nagar Parishad)\s*-?\s*([A-Za-z ]+?)(?:\s*-\s*UAD)?$/i.exec(chain[2] || "");
    if (town && /Urban Administration/i.test(chain[0])) return body(kindOf(chain[2]), town[1], "chain");
  }
  if (state === "TG") {
    const town = /^(?:MUNICIPALITIES|MUNICIPAL ADMINISTRATION DEPARTMENT) - TELANGANA-(.*)$/i.exec(chain[0] || "");
    if (town) return body(kindOf(town[1]) || "Municipal Council", town[1].replace(/,.*$/, "").replace(/\bJammikunta\b.*/i, "Jammikunta"), "chain");
    const corporation = /^(.*?municipal corporation)-/i.exec(chain[0] || "");
    if (corporation) return body("Municipal Corporation", corporation[1], "chain");
    if (/^HMDA\b/.test(chain[0] || "")) return { kind: "Development Authority", city: "Hyderabad", label: "Hyderabad Metropolitan Development Authority", how: "chain" };
  }
  if (state === "UP" && /^New Okla/i.test(chain[0] || "")) {
    return { kind: "Development Authority", city: "Noida", label: "New Okhla Industrial Development Authority", how: "chain" };
  }
  // Everywhere else: the last chain segment that names an urban body.
  for (let index = chain.length - 1; index >= 0; index -= 1) {
    const segment = chain[index];
    if (/^Directorate|^Department of|Urban Administration and Development Department|Urban Local Bodies$|^Municipal Bodies$|^MAWS$/i.test(segment)) continue;
    const kind = kindOf(segment);
    if (!kind) continue;
    if (state === "CH" && /^Municipal Corporation$/i.test(segment)) {
      return { kind, city: "Chandigarh", label: "Chandigarh Municipal Corporation", how: "chain" };
    }
    if (/Industrial Development Corporation|Roads? (?:and Bridges )?Development Corporation|Warehousing|Transco|Generating|Marketing|Housing and Infrastructure|Small Industries|Board Corporation|Projects Corp|Industrial Infrastructure/i.test(segment)) continue;
    const named = body(kind, segment.replace(/^.*?-\s*(?=(?:Nagar|Mun))/i, ""), "chain");
    if (named) return named;
  }
  // Bihar, Chhattisgarh and Jharkhand stop at the department; the title carries the body.
  if (["BR", "CG", "JH"].includes(state) && !/PWD|Public Works|RWD|RCD|NREP|N\.R\.E\.P|Zila Parishad|PARD|Panchayat and Rural|WRD|Special Division/i.test(joined)) {
    const after = /\b(?:under|of|in|at)\s+((?:[A-Z][A-Za-z]+\s){1,2})(Municipal Corporation|Nagar Nigam|Nagar Parishad|Nagar Panchayat|Municipal Council|Municipality|Nagar Palika(?: Parishad)?)\b/.exec(title);
    if (after && !/^(?:Construction|The|Various|Different)\b/.test(after[1])) return body(kindOf(after[2]), after[1], "title");
    const before = /\b(Nagar Nigam|Nagar Parishad|Nagar Panchayat|Nagar Palika(?: Parishad)?|Municipal Corporation)\s*,?\s+((?:[A-Z][A-Za-z]+\s?){1,2})/.exec(title);
    if (before && !/^(?:Construction|The|Various|Different|Extending|Covering)\b/.test(before[2])) return body(kindOf(before[1]), before[2], "title");
  }
  return null;
}
