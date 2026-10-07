import { noticeForBody, urbanBodyOf } from "./notice-bodies.mjs";
import { tenderCoversCarriageway } from "./tender-scope.mjs";
import { localityKeys, sameLocality } from "./ward-tenders.mjs";

// Ward tenders outside Karnataka: the open road notices of the urban body a point's ward
// belongs to, whose title says that ward. The Karnataka matcher (ward-tenders.mjs) is not
// used and not changed: it reads 795 Bengaluru titles against a register of 369 names, and
// its number reader was written for those titles. On the State portals' titles it returned
// a zone or circle number as a ward on 61 titles ("WARD 47 ZONE 06" gave 47 and 6) and did
// not read "Div-128" or "W06" at all (data/wards/COVERAGE.md, section 7).
//
// What a title has to say, measured on the pairs a person read (COVERAGE.md sections 7
// and 11, data/wards/handread.json, data/wards/runtime-handread.json):
//
//   by name    the ward's name on the title's own ward marker: "Vatva Ward of the South
//              Zone", "in South Zone Lambha Ward", "Ward-17 Cherlapally". A ward's name
//              anywhere else in a title was a road ("Bawana-Auchandi Road"), a circle
//              ("Kapra-Circle") or a sub-city ("Rohini") and is never matched here.
//   by number  the ward's number on the title's ward marker, and only for a snapshot whose
//              numbering is the tenders' (index `use.numbers` "current"). A number a title
//              gives to a zone, a circle, a unit or an office is not a ward's.
//
// The body comes first in both: only notices that notice-bodies.mjs files under the
// snapshot's own city are read, so Shahpur ward of Ahmedabad never meets a Shahpur in
// another town's title.

const NUMBER = /^\d{1,3}$/;
// Words that mark a ward. "wards" opens a list of numbers ("wards 41 and 42") and takes no
// name: "Khadia and other wards" names nothing.
const WARD_WORDS = new Set(["ward", "wad", "vard"]);
const WARD_PLURALS = new Set(["wards"]);
// Between a marker and its number: "Ward No. 27", "ward number 232", "W.No. 17".
const FILLERS = new Set(["no", "nos", "number", "numbers", "num"]);
// A marker's number that belongs to an earlier delimitation: "(Old Ward No-167)".
const SUPERSEDED = new Set(["old", "former", "erstwhile", "previous", "earlier"]);
// A number followed by one of these is a landmark or a measure, not the ward of the work:
// "BACKSIDE OF WARD 50 OFFICE", "ward 4 km 2".
const NOT_A_WARD = new Set(["office", "karyalay", "karyalaya", "km", "kms", "m", "mtr", "mtrs",
  "meter", "meters", "metre", "metres", "mm", "cm", "ft", "feet", "lakh", "lakhs", "crore"]);
// Units larger than a ward. A name that runs into one is the unit's ("Moosapet Circle-53",
// "Kapra-Circle 07", "Central Zone"), and a ward's name never runs across one ("South Zone
// Vatwa Ward" is Vatwa).
const UNITS = new Set(["zone", "zon", "zonal", "circle", "cir", "division", "divn", "div",
  "subdivision", "constituency", "assembly", "unit", "corporation", "municipal", "nigam"]);
// Words no name runs across. The second line is words these titles put in front of
// "ward" that are not its name: "different wards", "Different Ward and Different roads".
const JOINING = new Set(["the", "of", "at", "in", "from", "to", "and", "near", "for", "on", "by",
  "with", "under", "as", "per", "via", "upto", "up", "its", "all", "within", "including",
  "other", "others", "different", "various", "diff", "each", "every", "any", "said", "same", "this"]);
// A word after a name that makes it another place (Shastri Nagar is not Shastri) or a
// road ("Ward 5 Station Road").
const NAME_FORMING = new Set(["nagar", "nagara", "pura", "puram", "pur", "palya", "colony", "town",
  "layout", "block", "vihar", "enclave", "garden", "gam", "gaon", "village", "area"]);
const ROAD_WORDS = new Set(["road", "rd", "marg", "street", "st", "lane", "gali", "salai", "path",
  "highway", "bypass", "chowk", "chauraha", "junction", "flyover", "bridge", "main", "cross"]);

// A title as words and numbers, each with the text that stood before it: "w06,z20" is
// w, 06, z, 20 with gaps "", "", ",", "". The gaps say what a regular expression over
// words cannot: that 06 is glued to its w, and that a comma parts two names.
function tokensOf(title) {
  const text = String(title || "").toLowerCase();
  const tokens = [];
  let end = 0;
  for (const match of text.matchAll(/[a-z]+|\d+/g)) {
    tokens.push({ text: match[0], gap: text.slice(end, match.index), number: /^\d/.test(match[0]) });
    end = match.index + match[0].length;
  }
  return tokens;
}

const spacesOnly = (gap) => /^\s*$/.test(gap);
// Does `next` carry on the name `previous` began? "Saraspur-Rakhiyal", "New Wadaj", and
// "S.P.Stadium" or "B.N Reddy" where the dot follows an initial. "Uppal. Uppal Circle" is
// two sentences.
const joined = (previous, next) => /^\s+$/.test(next.gap) || next.gap === "-"
  || (/^\.\s?$/.test(next.gap) && previous.text.length <= 2);
// Between a marker and its number: "Ward No.39", "WARD -49", "ward no-263", "Ward No,10".
const markerToNumber = (gap) => /^[\s.:,\-]*$/.test(gap) && gap.length <= 4;
// Between the numbers of one list: "34, 35 and 44", "128 129 136".
const insideList = (gap) => /^[\s,&]*$/.test(gap) && gap.length <= 3;

const lettersIn = (text) => text.replace(/[^a-z]/g, "").length;

const markerCache = new Map();
// What a title says on its ward markers.
//   numbers  every ward number, in the order written
//   names    for each marker that carries a name, the runs of words that may be it, the
//            longest first: the one to three words right before "ward" ("Shahibaug ward";
//            "South Zone Vatwa Ward" is Vatwa, a name does not run across a zone), or the
//            words right after its one number up to the next break ("Ward-11, Safipur",
//            "Ward 91 Shastri Nagar."; "Ward 5 Station Road" names a road and is left out)
// `letters` are the single letters this body's titles use as a ward marker, glued to the
// number or to a separator ("W06", "W-64", "D150"), and `words` its other markers ("div",
// "dn" in Chennai, where a division is a ward). Elsewhere "div-5" is a works division.
export function wardMarkersIn(title, { letters = [], words = [] } = {}) {
  const cacheKey = `${letters.join("")}|${words.join(",")}|${title}`;
  const held = markerCache.get(cacheKey);
  if (held) return held;
  if (markerCache.size >= 8_000) markerCache.clear();
  const tokens = tokensOf(title);
  const numbers = [];
  const names = [];
  const label = (run) => run.map((token) => token.text).join(" ");
  const word = (token) => Boolean(token) && !token.number && !JOINING.has(token.text) && !UNITS.has(token.text)
    && !WARD_WORDS.has(token.text) && !WARD_PLURALS.has(token.text) && !FILLERS.has(token.text);
  for (let index = 0; index < tokens.length; index += 1) {
    const token = tokens[index];
    if (token.number) continue;
    const named = WARD_WORDS.has(token.text);
    const lettered = letters.includes(token.text);
    if (!named && !lettered && !WARD_PLURALS.has(token.text) && !words.includes(token.text)) continue;
    let at = index + 1;
    // "AISHABAG WARD OFFICE 39 TO SUVIDHA ELECTRONICS": the ward's office is a landmark on
    // the way, and neither its number nor the name in front of it is the ward of the work.
    if (NOT_A_WARD.has(tokens[at]?.text)) continue;
    const superseded = SUPERSEDED.has(tokens[index - 1]?.text);
    if (named && spacesOnly(token.gap) && !superseded) {
      const runs = [];
      for (let length = 1; length <= 3 && word(tokens[index - length]); length += 1) {
        if (length > 1 && !joined(tokens[index - length], tokens[index - length + 1])) break;
        const run = label(tokens.slice(index - length, index));
        if (lettersIn(run) >= 3) runs.unshift(run);
      }
      if (runs.length) names.push(runs);
    }
    // A lone letter marks a ward only where the number is glued to it: "W06", "W-64", "W.No.6".
    if (lettered && !/^[.\-:]?$/.test(tokens[at]?.gap ?? " ")) continue;
    while (FILLERS.has(tokens[at]?.text) && markerToNumber(tokens[at].gap)) at += 1;
    if (!tokens[at]?.number || !NUMBER.test(tokens[at].text) || !markerToNumber(tokens[at].gap)) continue;
    if (superseded) continue;
    let read = 0;
    while (tokens[at]?.number && NUMBER.test(tokens[at].text)) {
      const after = tokens[at + 1];
      // "2nd", "12m", "5A": letters glued to the digits make it something else.
      const glued = after && !after.number && after.gap === "";
      if (glued || (after && NOT_A_WARD.has(after.text))) break;
      if (Number(tokens[at].text) > 0) numbers.push(Number(tokens[at].text));
      read += 1;
      at += 1;
      if (tokens[at]?.text === "and" && tokens[at + 1]?.number && spacesOnly(tokens[at].gap)
          && spacesOnly(tokens[at + 1].gap)) at += 1;
      else if (!tokens[at]?.number || !insideList(tokens[at].gap)) break;
    }
    // The name behind one number. A list of numbers is followed by a place in them, not
    // by their name.
    if (read !== 1 || !tokens[at] || !/^\s*[,\-:]?\s*$/.test(tokens[at].gap)) continue;
    let end = at;
    while (word(tokens[end]) && (end === at || joined(tokens[end - 1], tokens[end]))) end += 1;
    const next = tokens[end];
    const runsOn = next && joined(tokens[end - 1] || next, next)
      && (ROAD_WORDS.has(next.text) || UNITS.has(next.text));
    const last = tokens[end - 1];
    const run = label(tokens.slice(at, end));
    if (end > at && end - at <= 3 && !runsOn && !ROAD_WORDS.has(last.text) && lettersIn(run) >= 3
        && !(next?.number && next.gap === "")) names.push([run]);
  }
  const markers = { numbers: [...new Set(numbers)], names };
  markerCache.set(cacheKey, markers);
  return markers;
}

// The wards of a roster a title's markers name. For each marker the longest run that is
// a ward's name decides ("New Wadaj ward" is New Wadaj, never a Wadaj). A run that fits
// more than one ward names none: "Yashoda Nagar" fits Yashoda Nagar East and Yashoda Nagar
// West, and a title that does not say which is answered for neither.
function namedWards(names, roster) {
  const found = new Set();
  for (const runs of names) {
    for (const run of runs) {
      const fits = new Set();
      for (const offered of localityKeys(run)) {
        for (const ward of roster) {
          if (ward.keys.some((key) => sameLocality(key, offered)
              && (key.toward || null) === (offered.toward || null))) fits.add(ward.code);
        }
      }
      if (fits.size === 1) found.add([...fits][0]);
      if (fits.size) break;
    }
  }
  return found;
}

// A snapshot's wards as a title can name them. Bhopal's names are in Devanagari and have
// no spelling key, which is one reason it is matched by number.
const rosters = new WeakMap();
function rosterOf(snapshot) {
  if (!rosters.has(snapshot)) {
    rosters.set(snapshot, snapshot.wards.map((ward) => ({ code: ward.code, keys: ward.name ? localityKeys(ward.name) : [] })));
  }
  return rosters.get(snapshot);
}

// The notices of one body in a State's pack that could be shown at all, each with what
// its ward markers say. Worked out once per pack per body: a pack is read once per
// process and Uttar Pradesh's holds 1,664 notices. The admission rules are those of
// roadNoticeCandidates in national-tenders.mjs, less the closing date, which moves.
const bodyNotices = new WeakMap();
export function noticesOfBody(pack, snapshot) {
  if (!bodyNotices.has(pack)) bodyNotices.set(pack, new Map());
  const held = bodyNotices.get(pack);
  if (!held.has(snapshot.id)) {
    const roster = rosterOf(snapshot);
    const rows = [];
    for (const record of Array.isArray(pack?.notices) ? pack.notices : []) {
      if (!record || typeof record.title !== "string" || !record.title) continue;
      const body = urbanBodyOf(noticeForBody(snapshot.state_code, record));
      // A development authority shares the city's name and has no wards.
      if (!body || body.city !== snapshot.notices_city || body.kind === "Development Authority") continue;
      const closes = Date.parse(String(record.closing_at || ""));
      const shown = record.lifecycle === "procurement_notice" && record.scope === "road_surface"
        && record.segment_verified === false && record.award_verified === false
        && record.dlp_verified === false && Number.isFinite(closes)
        && tenderCoversCarriageway(record.title, record.tender_reference);
      const markers = wardMarkersIn(record.title, snapshot.markers);
      rows.push({
        record, closes, shown, body: body.label,
        numbers: markers.numbers,
        named: snapshot.by === "name" ? namedWards(markers.names, roster) : null,
      });
    }
    held.set(snapshot.id, rows);
  }
  return held.get(snapshot.id);
}

const dayOrder = (value) => {
  const iso = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value || ""));
  return iso ? `${iso[1]}${iso[2]}${iso[3]}` : "";
};

// The same eight fields as a Karnataka ward tender (publicWardTender in ward-tenders.mjs):
// the app reads tender_number, title and published, and the shape is a contract.
// tender_number is written as the street-level notice match writes it.
function publicWardNotice(record, pack, basis) {
  const source = (pack.sources || []).find((item) => item.source_id === record.source_id);
  return {
    tender_number: record.tender_reference === record.tender_id || !record.tender_reference
      ? String(record.tender_id) : `${record.tender_reference} [${record.tender_id}]`,
    title: record.title,
    location: record.organisation_chain || null,
    published: record.published_at || null,
    source_name: source ? source.source_name : "Official State/UT e-Procurement portal",
    source_url: source ? source.source_url : record.source_url || null,
    match_basis: basis,
    scope: "ward",
  };
}

export const INDIA_WARD_TENDER_LIMIT = 5;

// Up to five open road notices of the snapshot's body whose title says the ward. Order: a
// title about fewer wards first (one about this ward alone before one that lists five),
// then the most recently published, then the one that closes last.
//
// `ward` is the snapshot's own entry for the point; `snapshot` carries how it may be
// matched (`by`, and `numbers` for whether its numbering is the tenders').
export function matchIndiaWardTenders({
  ward = null, snapshot = null, pack = null, now = Date.now(), limit = INDIA_WARD_TENDER_LIMIT,
} = {}) {
  if (!ward || !snapshot || !pack) return [];
  const byNumber = snapshot.by === "number" && snapshot.numbers === "current" && ward.no;
  const byName = snapshot.by === "name" && ward.name;
  if (!byNumber && !byName) return [];
  const number = Number(ward.no);
  const found = [];
  for (const row of noticesOfBody(pack, snapshot)) {
    if (!row.shown || row.closes < now) continue;
    if (byNumber ? !row.numbers.includes(number) : !row.named.has(ward.code)) continue;
    found.push(row);
  }
  found.sort((left, right) => (byNumber ? left.numbers.length - right.numbers.length : 0)
    || (dayOrder(left.record.published_at) < dayOrder(right.record.published_at) ? 1
      : dayOrder(left.record.published_at) > dayOrder(right.record.published_at) ? -1 : 0)
    || right.closes - left.closes
    || String(left.record.tender_id).localeCompare(String(right.record.tender_id)));
  const basis = byNumber ? `ward number ${ward.no}` : `ward name ${ward.name}`;
  return found.slice(0, limit).map(({ record }) => publicWardNotice(record, pack, basis));
}

// The urban body a point outside Karnataka is in, with how many road notices of that body
// the State's pack holds (`road_notices`) and how many of those could be listed today
// (`road_notices_open`: bids still open, and the carriageway). It is a fact about the
// place for the log and for a later decision; nothing here goes into ward_tenders, which
// stays the notices whose title says the ward.
//
// `basis` says how the body is known. "ward_snapshot": one of the snapshot's wards holds
// the point. "geocoder_city": the geocoder's city is the city exactly one municipal body
// of the State's notices is filed under; that is the geocoder's word for the place, and a
// village beside Ghaziabad may carry it. A development authority shares its city's name,
// covers more than the city and is never answered.
const bodiesOfPack = new WeakMap();
function bodiesOf(pack, stateCode) {
  if (!bodiesOfPack.has(pack)) {
    const bodies = new Map();
    for (const record of Array.isArray(pack?.notices) ? pack.notices : []) {
      if (!record || typeof record.title !== "string") continue;
      const body = urbanBodyOf(noticeForBody(stateCode, record));
      if (!body || body.kind === "Development Authority") continue;
      if (!bodies.has(body.label)) bodies.set(body.label, { body, rows: [] });
      const closes = Date.parse(String(record.closing_at || ""));
      bodies.get(body.label).rows.push({
        closes,
        listable: Number.isFinite(closes) && record.lifecycle === "procurement_notice"
          && record.scope === "road_surface" && tenderCoversCarriageway(record.title, record.tender_reference),
      });
    }
    bodiesOfPack.set(pack, [...bodies.values()]);
  }
  return bodiesOfPack.get(pack);
}

export function urbanBodyAt({ snapshot = null, city = null, stateCode = null, pack = null, now = Date.now() } = {}) {
  const wanted = String((snapshot ? snapshot.notices_city : city) || "").trim().toLowerCase();
  if (!wanted || !/^[A-Z]{2}$/.test(String(stateCode || ""))) return null;
  const fits = pack ? bodiesOf(pack, stateCode).filter(({ body }) => body.city.toLowerCase() === wanted) : [];
  if (!snapshot && fits.length !== 1) return null;
  const rows = fits.flatMap((entry) => entry.rows);
  const one = fits.length === 1 ? fits[0].body : null;
  return {
    name: one ? one.label : snapshot.body,
    kind: one ? one.kind : null,
    city: one ? one.city : snapshot.notices_city,
    state_code: stateCode,
    basis: snapshot ? "ward_snapshot" : "geocoder_city",
    road_notices: rows.length,
    road_notices_open: rows.filter((row) => row.listable && row.closes >= now).length,
  };
}
