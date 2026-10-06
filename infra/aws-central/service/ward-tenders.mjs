import { metresBetween } from "./spatial.mjs";
import { tenderCoversCarriageway } from "./tender-scope.mjs";
import { STOP } from "./tenders.mjs";

// Ward-level tenders: road-surface tenders whose title names the ward or the locality a
// municipal point is in. This is deliberately a weaker claim than `tender` (matchTender),
// which needs the street. Most Bengaluru tenders are area-wide ("Improvements to roads
// and drains in Munnekolala colony at Munnekolala ward no.105"), so a street is never in
// the title, and the answer a person can use is "these works were tendered for your
// ward", labelled as such.
//
// Names are matched, never ward numbers: KGIS carries the Greater Bengaluru numbering
// (Cox Town is ward 10) and the titles carry the old BBMP numbering (Cox Town is 108).
//
// Every rule below was read off the 795 Bengaluru titles and the 369 Bengaluru ward names
// with tools/ward-tender-vocabulary.mjs, which prints the pairs each rule is there for.

// Words that say what kind of place a name is, or that every title carries. A name made
// only of these names nothing ("Main Road", "2nd Stage", "Ward", "Layout").
const GENERIC = new Set([...STOP,
  "nagara", "layouts", "crosses", "streets", "colony", "extension", "extn", "badavane", "ward", "wards", "surrounding", "surroundings",
  "areas", "garden", "gardens", "park", "temple", "old", "new", "sub", "st", "nd", "rd", "th",
  "lane", "avenue", "bbmp", "gba", "bda", "improvement", "improvements", "drain", "drains",
  "development", "construction", "asphalting", "resurfacing", "works", "work", "other",
]);
// Words that only join names. No name runs across one: "in Bommanahalli" is Bommanahalli.
const JOINING = new Set(["the", "of", "at", "in", "from", "to", "and", "near", "no", "number",
  "for", "on", "by", "with", "under", "as", "per", "via", "upto", "up", "its", "all"]);
// A name followed by one of these is an office or an electorate, larger than a ward:
// "Ward No. 214-Puttenahalli ... in Bommanahalli Division" is Puttenahalli's tender, not
// Bommanahalli's, and roads "at Amruthanagar in Byatarayanapura Constituency" are
// Amruthanagar's. Only a pothole filling title that names no ward of its own is about the
// whole unit ("Filling of potholes in ward No. 173, 174 and 175 in Koramangala Sub
// Division"), and there the unit's name is offered. Titles are cut at 150 characters, so
// the last word may be the front of one of these.
const LARGER_UNITS = new Set(["division", "divisions", "subdivision", "sub", "zone", "constituency", "assembly"]);
const largerUnit = (word, last) => LARGER_UNITS.has(word)
  || (last && word.length >= 3 && [...LARGER_UNITS].some((unit) => unit.startsWith(word)));
const potholeWork = (title) => /\bpot\s*holes?\b/i.test(title);
const DIRECTIONS = new Set(["east", "west", "north", "south", "central"]);
// A word before a name that makes it another place: Old Guddadahalli is not Guddadahalli
// and New Thippasandra is not Thippasandra. Any other name word in front does the same
// (Horamavu Agara is not Agara, Kaval Byrasandra is not Byrasandra, Agrahara Dasarahalli
// is not Dasarahalli, "A Narayanapura" and "K Narayanapura" are two wards), which
// titleNameKeys checks for itself.
const QUALIFIERS = new Set(["old", "new", "upper", "lower"]);
// A word after a name that makes it another place: Chowdeshwari Nagar is not the
// Chowdeshwari ward, Vasanthapura is not Vasanth Nagar.
const NAME_FORMING = new Set(["nagar", "nagara", "pura", "puram", "palya", "halli", "sandra",
  "pet", "pete", "town", "kere", "layout", "block"]);
// A name followed by one of these is the name of a road, which can run through several
// wards: "Hennur Main Road" is not a tender for Hennur ward.
const ROAD_WORDS = new Set(["road", "rd", "street", "highway", "flyover", "underpass", "junction", "circle"]);
const ROAD_LEADERS = new Set(["main", "cross", "ring", "link", "service", "high", "double"]);

const VOWELS = new Set(["a", "e", "i", "o", "u"]);

// One spelling for the ways the same Kannada name is written in Latin letters.
//   oo -> u, ee -> i     Hoodi / Hudi, Neelasandra / Nilasandra
//   w -> v, y -> i       Nagawara / Nagavara, Hoody / Hoodi, Shettyhalli / Shettihalli
//                        (a y that opens a word is a consonant and stays)
//   h after t d b k g    Thubarahalli / Tubarahalli, Thippasandra / Tippasandra
//   doubled letters      Munnekolala / Munekolala, Kadugodi / Kaddugodi
//   final "puram"        Lingarajapuram / Lingarajpura, K R Puram / K.R Pura
//   trailing vowels      Bellanduru / Bellandur, Hebbala / Hebbal, Munnekolalu / Munnekolala
export const SPELLING_RULES = [
  ["oo_ee", (word) => word.replace(/oo/g, "u").replace(/ee/g, "i")],
  ["w_y", (word) => word.replace(/w/g, "v").replace(/(?!^)y/g, "i")],
  ["h_after_stop", (word) => word.replace(/([tdbkg])h/g, "$1")],
  ["doubled_letters", (word) => word.replace(/(.)\1+/g, "$1")],
];
export const ENDING_RULES = [
  ["final_puram", (key) => key.replace(/puram$/, "pur")],
  ["trailing_vowels", (key) => key.replace(/[aeiou]+$/, "")],
];
// Each word is respelled on its own and the words are then joined, so that "Vignan
// Nagara" keeps both its n's and stays one vowel from "Vignananagara". `without` names
// rules to leave out; only the vocabulary tool passes it, to show which real pairs each
// rule is there for.
export function localityKey(words, without = null) {
  let key = words.map((word) => {
    let spelled = String(word).toLowerCase().replace(/[^a-z]/g, "");
    for (const [name, apply] of SPELLING_RULES) if (!without?.has(name)) spelled = apply(spelled);
    return spelled;
  }).join("");
  for (const [name, apply] of ENDING_RULES) if (!without?.has(name)) key = apply(key);
  return key;
}

// What is left to differ after localityKey is counted here. A vowel written or dropped
// costs 1 (Banasawadi / Banaswadi, Lingarajapura / Lingarajpura, Koramangala /
// Kormangala), so does a vowel changed (Yelechenahalli / Yelachenahalli, Kadugudi /
// Kadugodi) and an n written or dropped (Munnenkolalu / Munnekolala). Two things are
// another name and cost more than any limit:
//   a change to the name's opening: its first two letters or its first vowel
//                                  Chokkasandra / Chikkasandra, Herohalli / Harohalli and
//                                  Mallasandra / Mylasandra are three pairs of places,
//                                  and "A Adugodi" is not Adugodi
//   any other consonant            Shanthinagar / Shakthinagar, Jakkasandra / Lakkasandra,
//                                  Agara / Agaram, Shivanagar / Shivajinagar
// Swapped neighbours are not forgiven either: that rule paired Amruthahalli with
// Marathahalli and no ward needed it.
const OTHER_NAME = 9;
export const SPELLING_EDITS = ["vowel", "n"];
const opening = (key) => Math.max(1, [...key].findIndex((letter) => VOWELS.has(letter)));
export function spellingDistance(left, right, without = null) {
  const leftOpening = opening(left);
  const rightOpening = opening(right);
  const vowel = (letter) => !without?.has("vowel") && VOWELS.has(letter);
  const free = (letter) => vowel(letter) || (!without?.has("n") && letter === "n");
  const rows = left.length + 1;
  const columns = right.length + 1;
  const drop = (key, index, open) => (index > open && free(key[index]) ? 1 : OTHER_NAME);
  const cost = Array.from({ length: rows }, () => new Array(columns).fill(0));
  for (let i = 1; i < rows; i += 1) cost[i][0] = cost[i - 1][0] + drop(left, i - 1, leftOpening);
  for (let j = 1; j < columns; j += 1) cost[0][j] = cost[0][j - 1] + drop(right, j - 1, rightOpening);
  for (let i = 1; i < rows; i += 1) {
    for (let j = 1; j < columns; j += 1) {
      const a = left[i - 1];
      const b = right[j - 1];
      const change = a === b ? 0
        : vowel(a) && vowel(b) && i - 1 > leftOpening && j - 1 > rightOpening ? 1 : OTHER_NAME;
      cost[i][j] = Math.min(cost[i - 1][j - 1] + change,
        cost[i - 1][j] + drop(left, i - 1, leftOpening),
        cost[i][j - 1] + drop(right, j - 1, rightOpening));
    }
  }
  return cost[rows - 1][columns - 1];
}

// How many such differences a name may carry, by the length of the shorter spelling: none
// under 6 letters (Agara, Hoodi, Kudlu and J P Nagar must be written as they are), 1 from
// 6 to 8, 2 from 9. The length counted is the name's own letters, without words like
// layout or nagar: "BEML Layout" has four, so "BM Layout" is not a spelling of it.
const allowance = (length) => (length < 6 ? 0 : length <= 8 ? 1 : 2);
export function sameLocality(left, right, without = null) {
  if (!left?.key || !right?.key) return null;
  if (left.key === right.key) return "equal";
  const limit = Math.min(allowance(Math.min(left.key.length, right.key.length)),
    allowance(Math.min(left.own, right.own)));
  if (!limit || Math.abs(left.key.length - right.key.length) > limit) return null;
  // The opening is never edited, so two names that open differently are not compared.
  if (left.key[0] !== right.key[0] || left.key[1] !== right.key[1]) return null;
  return spellingDistance(left.key, right.key, without) <= limit ? "variant" : null;
}

// A spelling key with the count of letters that are the name's own: the words that are
// not generic, less a generic ending written into the name ("Vignananagara").
function named(words, without = null) {
  const own = localityKey(words.filter((word) => !GENERIC.has(word)), without)
    .replace(/(?:nagar|laiout|layout|colon)$/, "");
  return { key: localityKey(words, without), own: own.length };
}

const wordsOf = (value) => String(value || "").toLowerCase().match(/[a-z]+|\d+[a-z]*/g) || [];
const numbered = (word) => /^\d/.test(word);

// The spellings a ward or locality name may be found under in a title, or none when the
// name is generic. "49 - Doddakannelli Ward" is Doddakannelli, "Jayanagar East" is
// Jayanagar, "BEML Layout 6th Stage" is BEML Layout, "Thubarahalli Palya" (the hamlet of
// Thubarahalli) is also Thubarahalli.
export function localityKeys(name, without = null) {
  let words = wordsOf(String(name || "").replace(/^\s*\d+\s*-\s*/, ""));
  const firstNumber = words.findIndex(numbered);
  if (firstNumber > 0) words = words.slice(0, firstNumber);
  words = words.filter((word) => !numbered(word));
  while (words.length > 1 && (words[words.length - 1] === "ward" || DIRECTIONS.has(words[words.length - 1]))) {
    words = words.slice(0, -1);
  }
  const distinctive = words.filter((word) => !GENERIC.has(word)).join("");
  if (distinctive.length < 2) return [];
  const keys = [named(words, without)];
  const last = words[words.length - 1];
  if (words.length > 1 && last === "palya" && /(?:halli|sandra|pura|kere)$/.test(words[words.length - 2])) {
    keys.push(named(words.slice(0, -1), without));
  }
  return keys.filter((entry, index, all) => entry.key.length >= 3
    && all.findIndex((other) => other.key === entry.key) === index);
}

// Every run of one to three words in a title that could be a place name there, as a
// spelling key. Runs stop at punctuation, at joining words and at numbers. A run is not
// offered when the word after it makes it another name, a road's name or (but for area-wide
// pothole filling) a larger unit's.
// A name may be followed by the part of it the work is in ("Bhattarahalli Janatha
// Colony"), but a name in front makes a compound that may be another place (Horamavu
// Agara is not Agara). Such a run is offered with `lead`, the spellings of the name in
// front, and counts only for a point that is in that place too ("Doddigunta Coxtown" for
// a point in Doddigunta, Cox Town).
export function titleNameKeys(title, without = null) {
  const keys = new Map();
  const titled = new Set(titledWardKeys(title).map((entry) => entry.key));
  const pothole = potholeWork(title);
  const segments = String(title || "").toLowerCase().split(/[^a-z0-9.\s]+|\s-\s/)
    .filter((segment) => /[a-z0-9]/.test(segment));
  for (const [segmentIndex, segment] of segments.entries()) {
    const lastSegment = segmentIndex === segments.length - 1;
    const words = wordsOf(segment.replace(/\./g, " "));
    const boundary = (word) => word === undefined || numbered(word) || JOINING.has(word);
    const plain = (word) => boundary(word) || (GENERIC.has(word) && !QUALIFIERS.has(word));
    for (let start = 0; start < words.length; start += 1) {
      if (boundary(words[start]) || (GENERIC.has(words[start]) && !QUALIFIERS.has(words[start]))) continue;
      let lead = null;
      if (!plain(words[start - 1])) {
        if (QUALIFIERS.has(words[start - 1])) continue;
        lead = [];
        for (let length = 1; length <= 2 && !boundary(words[start - length]); length += 1) {
          if (!plain(words[start - length - 1])) continue;
          const front = words.slice(start - length, start);
          if (!GENERIC.has(front[0])) lead.push(named(front, without));
        }
        if (!lead.length) continue;
      }
      for (let length = 1; length <= 3 && start + length <= words.length; length += 1) {
        const run = words.slice(start, start + length);
        if (boundary(run[run.length - 1])) break;
        const after = words[start + length];
        const afterNext = words[start + length + 1];
        if (after && NAME_FORMING.has(after)) continue;
        if (after && (ROAD_WORDS.has(after) || (ROAD_LEADERS.has(after) && ROAD_WORDS.has(afterNext)))) continue;
        const entry = { ...named(run, without), lead };
        if (entry.key.length < 3) continue;
        if (after && largerUnit(after, start + length === words.length - 1 && lastSegment)
            && !(pothole && (!titled.size || titled.has(localityKey(run))))) continue;
        const held = keys.get(entry.key);
        // A plainly bounded mention beats a compound one, then the shorter own part.
        if (held && (!held.lead || lead) && held.own <= entry.own) continue;
        if (held && !held.lead && lead) continue;
        keys.set(entry.key, entry);
      }
    }
  }
  return [...keys.values()];
}

// The names a title attaches to its own ward marker: "in Ward No.101 Doddanekundi",
// "at Munnekolala ward no.105", "Ward No 57 Rajmahal Gutthalli, 58- Kadumalleshwara".
// The number is read past, never kept: it only says where the name is.
export function titledWardKeys(title) {
  const words = wordsOf(String(title || "").toLowerCase().replace(/\./g, " "));
  const plain = (word) => word !== undefined && !numbered(word) && !JOINING.has(word) && word !== "ward";
  const keys = new Map();
  const offer = (run) => {
    if (!run.length || (GENERIC.has(run[0]) && !QUALIFIERS.has(run[0]))) return;
    const entry = named(run);
    if (entry.key.length >= 3) keys.set(entry.key, entry);
  };
  for (let index = 0; index < words.length; index += 1) {
    if (words[index] !== "ward") continue;
    for (let length = 1; length <= 3 && plain(words[index - length]); length += 1) {
      offer(words.slice(index - length, index));
    }
    // "ward No 165 B Venkatareddy Nagara": the register's ward is Venkat Reddy Nagara, so
    // the name is also read from past any initials.
    const afterInitials = (from) => {
      let start = from;
      while (plain(words[start]) && words[start].length === 1) start += 1;
      return start;
    };
    let next = index + 1;
    while (next < words.length && (numbered(words[next]) || ["no", "number", "nos", "old", "new"].includes(words[next]))) {
      const afterNumber = numbered(words[next]) ? next + 1 : null;
      next += 1;
      if (afterNumber === null) continue;
      for (const from of new Set([afterNumber, afterInitials(afterNumber)])) {
        for (let length = 1; length <= 3 && plain(words[from + length - 1]); length += 1) {
          offer(words.slice(from, from + length));
        }
      }
    }
  }
  return [...keys.values()];
}

// Bengaluru has two Byatarayanapuras, two Manjunatha Nagars and a Vinayaka Layout in most
// corners, and its five corporations share one tender index. Where a title says which
// ward its work is in, and that ward is one the register draws, the work is there: a
// point more than this far from every such ward is in a namesake. The nearest wrong
// namesake in the 795 titles was 7.4 km off and the farthest right neighbour 3.4 km
// (ward centre to ward centre), so 3 km from the point to the ward's box separates them.
export const NAMESAKE_METRES = 3_000;
function metresToBox(lat, lng, [west, south, east, north]) {
  const nearLat = Math.min(Math.max(lat, south), north);
  const nearLng = Math.min(Math.max(lng, west), east);
  return metresBetween(lat, lng, nearLat, nearLng);
}
const rosterKeys = new WeakMap();
function keysOfRoster(roster) {
  if (!rosterKeys.has(roster)) {
    rosterKeys.set(roster, roster.flatMap((ward) => localityKeys(ward.name).map((entry) => ({ entry, bbox: ward.bbox }))));
  }
  return rosterKeys.get(roster);
}
export function elsewhere(title, point, roster) {
  if (!point || !Array.isArray(roster) || !roster.length) return false;
  const titled = titledWardKeys(title);
  if (!titled.length) return false;
  let nearest = Infinity;
  for (const { entry, bbox } of keysOfRoster(roster)) {
    if (!titled.some((name) => sameLocality(entry, name))) continue;
    nearest = Math.min(nearest, metresToBox(point.lat, point.lng, bbox));
  }
  return Number.isFinite(nearest) && nearest > NAMESAKE_METRES;
}

// "31-07-2025" in the Karnataka index, ISO dates elsewhere. Unreadable dates sort last.
function publishedOrder(value) {
  const text = String(value || "");
  const indian = /^(\d{2})-(\d{2})-(\d{4})$/.exec(text);
  if (indian) return `${indian[3]}${indian[2]}${indian[1]}`;
  const iso = /^(\d{4})-(\d{2})-(\d{2})/.exec(text);
  return iso ? `${iso[1]}${iso[2]}${iso[3]}` : "";
}

function publicWardTender(tender, basis) {
  return {
    tender_number: tender.tender_number,
    title: tender.title,
    location: tender.location || null,
    published: tender.published || null,
    source_name: tender.source_name || null,
    source_url: tender.source_url || null,
    match_basis: basis,
    scope: "ward",
  };
}

// A title's scope and names do not change between requests and a town's index is read
// whole every time, so both are worked out once per title per process.
const titleFacts = new Map();
function factsOf(title) {
  let facts = titleFacts.get(title);
  if (!facts) {
    if (titleFacts.size >= 6_000) titleFacts.clear();
    const road = tenderCoversCarriageway(title);
    facts = { road, names: road ? titleNameKeys(title) : [] };
    titleFacts.set(title, facts);
  }
  return facts;
}

export const WARD_TENDER_LIMIT = 5;

// Up to five road-surface tenders of the body whose title names the point's ward or one
// of the geocoder's localities for it. Order: a title that names the ward first, then one
// that names a geocoder locality, then the most recently published.
//
// `point` and `roster` (the named wards that share the body's tender index, each with its
// box in degrees) are optional. With them, a tender whose own ward is a namesake across
// town is left out.
export function matchWardTenders({
  wardName = null, localities = [], tenders = [], point = null, roster = null, limit = WARD_TENDER_LIMIT,
} = {}) {
  const wanted = [];
  const seenKeys = new Set();
  const add = (name, kind) => {
    const label = String(name || "").replace(/^\s*\d+\s*-\s*/, "").trim().slice(0, 160);
    for (const key of localityKeys(label)) {
      if (seenKeys.has(key.key)) continue;
      seenKeys.add(key.key);
      wanted.push({ key, ward: kind === "ward", basis: `${kind === "ward" ? "ward name" : "locality"} ${label}` });
    }
  };
  add(wardName, "ward");
  for (const locality of Array.isArray(localities) ? localities.slice(0, 8) : []) add(locality, "locality");
  if (!wanted.length) return [];
  const found = [];
  for (const tender of tenders) {
    if (typeof tender?.title !== "string" || !tender.title) continue;
    const { road, names } = factsOf(tender.title);
    if (!road) continue;
    // A name behind another name counts only when the one in front is the point's too.
    const ours = (name) => !name.lead
      || name.lead.some((front) => wanted.some((want) => sameLocality(want.key, front)));
    let ward = null;
    let locality = null;
    for (const want of wanted) {
      if ((want.ward ? ward : locality)
          || !names.some((name) => sameLocality(want.key, name) && ours(name))) continue;
      if (want.ward) ward = want.basis;
      else locality = want.basis;
    }
    if ((ward || locality) && !elsewhere(tender.title, point, roster)) {
      found.push({ tender, ward, locality, published: publishedOrder(tender.published) });
    }
  }
  found.sort((left, right) => Boolean(right.ward) - Boolean(left.ward)
    || Boolean(right.locality) - Boolean(left.locality)
    || (left.published < right.published ? 1 : left.published > right.published ? -1 : 0)
    || String(left.tender.tender_number).localeCompare(String(right.tender.tender_number)));
  return found.slice(0, limit).map(({ tender, ward, locality }) => publicWardTender(tender, ward || locality));
}
