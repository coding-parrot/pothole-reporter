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
// with tools/ward-tender-vocabulary.mjs, which prints the pairs each rule is there for,
// and three (sides of a ward, two-place ward names, two-word road names) off Mangaluru
// and Mysuru.
//
// A name is not a place. Bengaluru has a Gandhi Nagar in Munnekolala, in Yelahanka and in
// Kengeri, and on 6 Oct 2026 a street in the first was answered with the tenders of the
// other two. So a match has to be anchored, and how depends on what it is (see
// matchWardTenders): a title that attaches the ward's name to its own ward marker is
// about that ward; any other mention of a name is kept only where the tender's division
// (the `location` column) and the places its title names agree with the point.

// Words that say what kind of place a name is, or that every title carries. A name made
// only of these names nothing ("Main Road", "2nd Stage", "Ward", "Layout").
const GENERIC = new Set([...STOP,
  "nagara", "layouts", "crosses", "streets", "colony", "extension", "extn", "badavane",
  "ward", "wards", "surrounding", "surroundings", "areas", "garden", "gardens", "park",
  "temple", "old", "new", "sub", "st", "nd", "rd", "th", "lane", "avenue", "bbmp", "gba",
  "bda", "improvement", "improvements", "drain", "drains", "development", "construction",
  "asphalting", "resurfacing", "works", "work", "other",
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
const namesRoad = (words, index) => ROAD_WORDS.has(words[index])
  || (ROAD_LEADERS.has(words[index]) && ROAD_WORDS.has(words[index + 1]));

// Big layouts are numbered in parts and the geocoder says which one a point is in ("JP
// Nagar 2nd Phase", "HBR Layout 5th Block"). A title that names another part ("JP Nagar
// 6th Phase", "5th Block HBR Layout") is kilometres away under the same name.
const PART_KINDS = new Set(["phase", "stage", "block", "sector"]);
// The parts named right after a run of words ("1st and 2nd Block") or right before it.
function partsBeside(words, start, end) {
  const parts = [];
  let at = end;
  const listed = [];
  while (/^\d+(?:st|nd|rd|th)?$/.test(words[at] || "") || (listed.length && words[at] === "and")) {
    if (words[at] !== "and") listed.push(parseInt(words[at], 10));
    at += 1;
  }
  if (listed.length && PART_KINDS.has(words[at])) for (const number of listed) parts.push(`${words[at]} ${number}`);
  if (PART_KINDS.has(words[start - 1]) && /^\d+(?:st|nd|rd|th)?$/.test(words[start - 2] || "")) {
    parts.push(`${words[start - 1]} ${parseInt(words[start - 2], 10)}`);
  }
  return parts;
}

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
    .replace(/(?:nagar|laiout|colon)$/, "");
  return { key: localityKey(words, without), own: own.length };
}

const wordsOf = (value) => String(value || "").toLowerCase().match(/[a-z]+|\d+[a-z]*/g) || [];
const numbered = (word) => /^\d/.test(word);

// The spellings a ward or locality name may be found under in a title, or none when the
// name is generic. "49 - Doddakannelli Ward" is Doddakannelli, "BEML Layout 6th Stage" is
// BEML Layout, "Thubarahalli Palya" (the hamlet of Thubarahalli) is also Thubarahalli, and
// "Gokulam,Brudhavana" (a Mysuru ward) is two names. "Jayanagar East" is Jayanagar with
// `toward` east: a title's plain Jayanagar is taken, its Jayanagar West is not (Mangaluru
// has Kunjathbail North and Kunjathbail South). "JP Nagar 2nd Phase" is JP Nagar with
// `part` "phase 2", held to a title's parts in the same way.
export function localityKeys(name, without = null) {
  const keys = [];
  for (const part of String(name || "").replace(/^\s*\d+\s*-\s*/, "").split(/[,;/&]+/)) {
    let words = wordsOf(part);
    const numberedPart = /(\d+)(?:st|nd|rd|th)?\s+(phase|stage|block|sector)\b/.exec(part.toLowerCase());
    const layoutPart = numberedPart ? `${numberedPart[2]} ${Number(numberedPart[1])}` : null;
    const firstNumber = words.findIndex(numbered);
    if (firstNumber > 0) words = words.slice(0, firstNumber);
    words = words.filter((word) => !numbered(word));
    let toward = null;
    while (words.length > 1 && (words[words.length - 1] === "ward" || DIRECTIONS.has(words[words.length - 1]))) {
      if (DIRECTIONS.has(words[words.length - 1])) toward = toward || words[words.length - 1];
      words = words.slice(0, -1);
    }
    const distinctive = words.filter((word) => !GENERIC.has(word)).join("");
    if (distinctive.length < 2) continue;
    keys.push({ ...named(words, without), toward, part: layoutPart });
    const last = words[words.length - 1];
    if (words.length > 1 && last === "palya" && /(?:halli|sandra|pura|kere)$/.test(words[words.length - 2])) {
      keys.push({ ...named(words.slice(0, -1), without), toward, part: layoutPart });
    }
  }
  return keys.filter((entry, index, all) => entry.key.length >= 3
    && all.findIndex((other) => other.key === entry.key) === index);
}

// A wanted name against a name a title offers: the same place, and not the title's other
// half of it.
export const offeredFor = (want, name, without = null) => sameLocality(want, name, without)
  && (!want.toward || !name.toward || name.toward.has(null) || name.toward.has(want.toward))
  && (!want.part || !name.parts?.size || name.parts.has(null) || name.parts.has(want.part));

// Every run of one to three words in a title that could be a place name there, as a
// spelling key, with the direction words that follow it (`toward`; null for none) and
// the numbered parts named beside it (`parts`; null for a mention with none). Runs
// stop at punctuation, at joining words and at numbers. A run is not offered when the
// word after it makes it another name, a road's name or (but for area-wide pothole
// filling) a larger unit's. A name may be followed by the part of it the work is in
// ("Bhattarahalli Janatha Colony"), but a name in front makes a compound that may be
// another place (Horamavu Agara is not Agara). Such a run is offered with `lead`, the
// spellings of the name in front, and counts only for a point that is in that place too
// ("Doddigunta Coxtown" for a point in Doddigunta, Cox Town).
export function titleNameKeys(title, without = null) {
  const keys = new Map();
  const ways = new Map();
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
        if (after && NAME_FORMING.has(after)) continue;
        if (namesRoad(words, start + length)) continue;
        // "Kadri Kambla road" is a road too: Kadri is not where the work is.
        if (after && !boundary(after) && !GENERIC.has(after) && namesRoad(words, start + length + 1)) continue;
        const entry = { ...named(run, without), lead };
        if (entry.key.length < 3) continue;
        if (after && largerUnit(after, start + length === words.length - 1 && lastSegment)
            && !(pothole && (!titled.size || titled.has(localityKey(run))))) continue;
        // The direction words that follow the name, from every mention of its kind.
        const slot = `${lead ? "behind" : "plain"} ${entry.key}`;
        if (!ways.has(slot)) ways.set(slot, { toward: new Set(), parts: new Set() });
        entry.toward = ways.get(slot).toward.add(DIRECTIONS.has(after) ? after : null);
        // A mention on the ward marker ("in Ward No 185 J P Nagara") says which ward the
        // work is filed under, not which part it is in, and adds nothing here.
        const beside = partsBeside(words, start, start + length);
        const filedUnder = titled.has(entry.key)
          && (after === "ward" || start === 0 || numbered(words[start - 1]));
        entry.parts = ways.get(slot).parts;
        for (const part of beside.length ? beside : filedUnder ? [] : [null]) entry.parts.add(part);
        const held = keys.get(entry.key);
        // A plainly bounded mention beats a compound one, then the shorter own part.
        if (held && ((!held.lead && lead) || (Boolean(held.lead) === Boolean(lead) && held.own <= entry.own))) continue;
        keys.set(entry.key, entry);
      }
    }
  }
  return [...keys.values()];
}

// What a title says beside its ward markers: the names ("in Ward No.101 Doddanekundi",
// "at Munnekolala ward no.105") and the numbers. A marker can open a list: "Ward No 57
// Rajmahal Gutthalli, 58- Kadumalleshwara, 59- Subramanyanagar & 60- Gayathrinagar" names
// four wards, "Ward No-188 Yediyur (Old Ward No-167)" gives one ward two numbers, "Ward
// No-76/24 Hennur" likewise, "ward No. 173, 174 and 175" gives three and no name.
//
// The numbers are the index's own (two BBMP numberings, both older than the register's)
// and are only ever compared with each other, title against title: the register numbers
// Cox Town 10 and nothing here reads that. 690 of the 702 Bengaluru titles with a ward
// marker carry a number.
const markerCache = new Map();
function wardMarkers(title) {
  const text = String(title || "");
  if (markerCache.has(text)) return markerCache.get(text);
  if (markerCache.size >= 6_000) markerCache.clear();
  const words = wordsOf(text.toLowerCase().replace(/\./g, " "));
  const marker = (word) => word === "ward" || word === "wad";
  const plain = (word) => word !== undefined && !numbered(word) && !JOINING.has(word) && !marker(word);
  const whole = (word) => /^\d{1,3}$/.test(word || "");
  const filler = (word) => ["no", "number", "nos", "numbers"].includes(word);
  // "old" or "new" in front of a number ("Old No.147", "New Ward no 14") is about the
  // number; in front of a name ("New Thippasandra") it is the name.
  const pastAge = (at) => {
    if (words[at] !== "old" && words[at] !== "new") return at;
    let peek = at + 1;
    while (marker(words[peek]) || filler(words[peek])) peek += 1;
    return whole(words[peek]) ? peek : at;
  };
  const names = new Map();
  const numbers = new Set();
  const offer = (run) => {
    if (!run.length || (GENERIC.has(run[0]) && !QUALIFIERS.has(run[0]))) return;
    const entry = { ...named(run), words: run.map((word) => localityKey([word])) };
    if (entry.key.length >= 3) names.set(entry.key, entry);
  };
  for (let index = 0; index < words.length; index += 1) {
    if (!marker(words[index])) continue;
    for (let length = 1; length <= 3 && plain(words[index - length]); length += 1) {
      offer(words.slice(index - length, index));
    }
    let at = index + 1;
    for (let item = 0; item < 12; item += 1) {
      // "no" belongs to the marker. Further down the list a number follows its name
      // directly; "Coxtown (Block no 22, 23, 25)" is not three more wards.
      while (item === 0 && filler(words[at])) at += 1;
      at = pastAge(at);
      if (!whole(words[at])) break;
      while (whole(words[at]) || (words[at] === "and" && whole(words[at + 1]))) {
        if (whole(words[at])) numbers.add(Number(words[at]));
        at += 1;
      }
      if (pastAge(at) !== at) continue;
      let end = at;
      while (plain(words[end])) end += 1;
      // "ward No 165 B Venkatareddy Nagara": the register's ward is Venkat Reddy Nagara,
      // so the name is also read from past any initials.
      let pastInitials = at;
      while (pastInitials < end && words[pastInitials].length === 1) pastInitials += 1;
      for (const from of new Set([at, pastInitials])) {
        for (let length = 1; length <= 3 && from + length <= end; length += 1) offer(words.slice(from, from + length));
      }
      at = words[end] === "and" && whole(words[end + 1]) ? end + 1 : end;
    }
  }
  const markers = { names: [...names.values()], numbers };
  markerCache.set(text, markers);
  return markers;
}
export const titledWardKeys = (title) => wardMarkers(title).names;
export const wardNumbers = (title) => wardMarkers(title).numbers;

// Bengaluru has two Byatarayanapuras, two Manjunatha Nagars and a Vinayaka Layout in most
// corners, and its five corporations share one tender index. Where a title says which
// ward its work is in, and that ward is one the register draws, the work is there: a
// point more than this far from every such ward is in a namesake. The nearest wrong
// namesake in the 795 titles was 7.4 km off and the farthest right neighbour 3.4 km
// (ward centre to ward centre), so 3 km from the point to the ward's box separates them.
export const NAMESAKE_METRES = 3_000;
// A division or a constituency is larger than a ward. Where a title gives both, the ward
// lay up to 6.0 km from the register ward the unit is named for (Kogilu in Byatarayanapura
// Sub Division), with one at 12.4 km (Yeshwanthpura in Rajarajeshwarinagara Division).
// Yelahanka is 25 km from Munnekolala.
export const UNIT_METRES = 8_000;
function metresToBox(lat, lng, [west, south, east, north]) {
  const nearLat = Math.min(Math.max(lat, south), north);
  const nearLng = Math.min(Math.max(lng, west), east);
  return metresBetween(lat, lng, nearLat, nearLng);
}

// The register's wards as the titles can name them: by spelling key, and by their words,
// so that an old ward or a sub division ("kempegowda ward", "yelahanka Sub division") is
// placed by the register wards that carry its name (Raja Kempegowda ward, Yelahanka
// Satellite Town).
const rosterIndex = new WeakMap();
function indexOfRoster(roster) {
  if (!rosterIndex.has(roster)) {
    rosterIndex.set(roster, {
      wards: roster.map((ward) => {
        let words = wordsOf(ward.name).filter((word) => !numbered(word));
        while (words.length > 1 && (words[words.length - 1] === "ward" || DIRECTIONS.has(words[words.length - 1]))) {
          words = words.slice(0, -1);
        }
        return { keys: localityKeys(ward.name), words: words.map((word) => localityKey([word])), bbox: ward.bbox };
      }),
      places: new Map(),
    });
  }
  return rosterIndex.get(roster);
}

// Names a title gives to something larger or older than a place in it: the runs before a
// division, sub division, zone or constituency.
function unitNames(title) {
  const words = wordsOf(String(title || "").toLowerCase().replace(/\./g, " "));
  const plain = (word) => word !== undefined && !numbered(word) && !JOINING.has(word) && !LARGER_UNITS.has(word) && word !== "ward";
  const runs = [];
  for (let index = 1; index < words.length; index += 1) {
    if (!LARGER_UNITS.has(words[index]) || LARGER_UNITS.has(words[index - 1])) continue;
    for (let length = 1; length <= 3 && plain(words[index - length]); length += 1) {
      const run = words.slice(index - length, index);
      if (GENERIC.has(run[0]) && !QUALIFIERS.has(run[0])) continue;
      runs.push({ ...named(run), words: run.map((word) => localityKey([word])) });
    }
  }
  return runs;
}

// Every run of one to three words between joining words and numbers, with none of the
// rules that say what a run is the name of. Used only to ask where a title is, never to
// match a point's name.
function everyName(title) {
  const names = [];
  for (const segment of String(title || "").toLowerCase().split(/[^a-z0-9.\s]+|\s-\s/)) {
    const words = wordsOf(segment.replace(/\./g, " "));
    const boundary = (word) => word === undefined || numbered(word) || JOINING.has(word);
    for (let start = 0; start < words.length; start += 1) {
      if (boundary(words[start]) || (GENERIC.has(words[start]) && !QUALIFIERS.has(words[start]))) continue;
      for (let length = 1; length <= 3 && !boundary(words[start + length - 1]); length += 1) {
        names.push(named(words.slice(start, start + length)));
      }
    }
  }
  return names;
}

const within = (words, run) => run.length <= words.length
  && words.some((_, at) => run.every((word, offset) => words[at + offset] === word));

// The register wards a title places itself in, each with how far from it the work may
// be. Narrowly: the wards whose name the title attaches to its own ward marker. Widely,
// for a match made on a locality's name: also any register ward the title mentions in
// full ("Kengeri Kote"), the wards that carry the name of an old ward it gives
// ("kempegowda ward"), and, at a unit's reach, the wards a unit is named for ("of
// yelahanka Sub division"). A common word inside a ward's name places nothing: "Lakshmi
// layout" is not Lakshmi Devi Nagar.
function placesOf(title, roster, wide) {
  const index = indexOfRoster(roster);
  const slot = `${wide ? "wide" : "titled"} ${title}`;
  if (!index.places.has(slot)) {
    if (index.places.size >= 12_000) index.places.clear();
    const titled = titledWardKeys(title);
    const every = wide ? everyName(title) : [];
    const units = wide ? unitNames(title) : [];
    const places = [];
    for (const ward of index.wards) {
      const named = (names) => names.some((name) => ward.keys.some((entry) => sameLocality(entry, name)));
      const carried = (names) => names.some((name) => name.own >= 5 && within(ward.words, name.words));
      if (named(units) || carried(units)) places.push({ bbox: ward.bbox, reach: UNIT_METRES });
      else if (named(titled) || named(every) || (wide && carried(titled))) places.push({ bbox: ward.bbox, reach: NAMESAKE_METRES });
    }
    index.places.set(slot, places);
  }
  return index.places.get(slot);
}

// The register wards a title attaches to its own ward marker, as indexes into the roster.
function markedWards(title, roster) {
  const index = indexOfRoster(roster);
  const slot = `marked ${title}`;
  if (!index.places.has(slot)) {
    const titled = titledWardKeys(title);
    index.places.set(slot, index.wards.flatMap((ward, at) => (titled
      .some((name) => ward.keys.some((entry) => sameLocality(entry, name))) ? [at] : [])));
  }
  return index.places.get(slot);
}

export function elsewhere(title, point, roster, { wide = false } = {}) {
  if (!point || !Array.isArray(roster) || !roster.length) return false;
  const places = placesOf(title, roster, wide);
  if (!places.length) return false;
  return places.every(({ bbox, reach }) => metresToBox(point.lat, point.lng, bbox) > reach);
}

// The divisions that work near a point: the `location` of every tender that attaches a
// register ward within 3 km of the point to its own ward marker. Bengaluru's index names
// 35 divisions; a name mentioned in a tender of a division that works nowhere near the
// point is a namesake (Kothanur of the South corporation, Kothanur of K R Puram). `placed`
// is every division that says where any of its work is; three one-tender divisions
// ("BBMP West SWD", "BMBP Project Bommanahalli") never do.
function divisionsNear(point, roster, tenders) {
  const near = new Set();
  const placed = new Set();
  if (!point || !Array.isArray(roster) || !roster.length) return { near, placed };
  for (const tender of tenders) {
    if (typeof tender?.title !== "string") continue;
    const places = placesOf(tender.title, roster, false);
    if (places.length) placed.add(divisionOf(tender));
    if (places.some(({ bbox }) => metresToBox(point.lat, point.lng, bbox) <= NAMESAKE_METRES)) near.add(divisionOf(tender));
  }
  return { near, placed };
}
const divisionOf = (tender) => String(tender?.location || "").trim().toLowerCase();

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
    facts = {
      road,
      names: road ? titleNameKeys(title) : [],
      titled: road ? titledWardKeys(title) : [],
      numbers: road ? wardNumbers(title) : new Set(),
    };
    titleFacts.set(title, facts);
  }
  return facts;
}

export const WARD_TENDER_LIMIT = 5;

// Up to five road-surface tenders of the body whose title names the point's ward or one
// of the geocoder's localities for it. Order: a title that names the ward first, then one
// that names a geocoder locality, then the most recently published.
//
// What is kept:
//   the ward's name on the title's own ward marker ("in Ward No -108, Coxtown")
//       always, unless the register places that ward over 3 km away (a namesake ward).
//       These tenders say which divisions serve the ward and which old ward numbers it
//       had, and that is what every other match is held to.
//   the ward's name anywhere else in the title ("at Kothanur Balaji layout")
//       only in a division that works within 3 km of the point, and not under an old
//       ward number that is not the ward's; where the ward has no tender of its own,
//       only if every tender naming the ward is in one division
//   a locality's name ("Gandhinagara 1st main")
//       only in a division the ward's own tenders are in, and not under an old ward
//       number that is not the ward's (every village has an Ambedkar Colony: the one
//       "at Marathahalli ward no.106" is not Doddanekundi's, ward 101);
//       where the ward has no tender of its own: only if every tender naming the
//       locality is in one division, that division works within 3 km of the point, and
//       the titles that file the name under a register ward agree on one;
//       and never when the title names a ward, an old ward or a unit the register
//       places out of reach
//
// `point` and `roster` (the named wards that share the body's tender index, each with its
// box in degrees) are optional; without them only the rules that need no geography apply.
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
  const candidates = [];
  for (const tender of tenders) {
    if (typeof tender?.title !== "string" || !tender.title) continue;
    const { road, names, titled } = factsOf(tender.title);
    if (!road) continue;
    // A name behind another name counts only when the one in front is the point's too.
    const ours = (name) => !name.lead
      || name.lead.some((front) => wanted.some((want) => sameLocality(want.key, front)));
    let ward = null;
    let locality = null;
    for (const want of wanted) {
      if ((want.ward ? ward : locality)
          || !names.some((name) => offeredFor(want.key, name) && ours(name))) continue;
      if (want.ward) ward = want;
      else locality = want;
    }
    if (!ward && !locality) continue;
    candidates.push({
      tender, ward, locality,
      marked: Boolean(ward) && titled.some((name) => sameLocality(ward.key, name)),
      published: publishedOrder(tender.published),
    });
  }
  if (!candidates.length) return [];
  const { near, placed } = divisionsNear(point, roster, tenders);
  const worksNear = (tender) => !near.size || near.has(divisionOf(tender));
  // The ward's own tenders: its name on their ward marker, and not a namesake's.
  const marked = candidates.filter(({ tender, ward, marked: onMarker }) => ward && onMarker
    && !elsewhere(tender.title, point, roster));
  const oldNumbers = new Set(marked.flatMap(({ tender }) => [...factsOf(tender.title).numbers]));
  const sameOldWard = (tender) => {
    const numbers = factsOf(tender.title).numbers;
    return !oldNumbers.size || !numbers.size || [...numbers].some((number) => oldNumbers.has(number));
  };
  // Where a name is found over the whole index, by the name alone, whatever part or side
  // of it a title means ("JP Nagar 6th Phase" is still a JP Nagar when asking how many
  // places carry the name): in how many divisions, and whether the titles that file it
  // under a register ward of another name agree on one. A title that lists three or more
  // wards is work across them and says nothing of where the name is.
  const spread = new Map();
  const spreadOf = (want) => {
    if (!spread.has(want.key.key)) {
      const naming = tenders.filter((tender) => typeof tender?.title === "string"
        && factsOf(tender.title).names.some((name) => !name.lead && sameLocality(want.key, name)));
      const filedUnder = [];
      if (Array.isArray(roster) && roster.length) {
        const index = indexOfRoster(roster);
        for (const tender of naming) {
          const wards = markedWards(tender.title, roster)
            .filter((at) => !index.wards[at].keys.some((entry) => sameLocality(entry, want.key)));
          if (wards.length === 1 || wards.length === 2) filedUnder.push(wards);
        }
      }
      spread.set(want.key.key, {
        divisions: new Set(naming.map(divisionOf)),
        severalWards: filedUnder.length > 1
          && !filedUnder[0].some((ward) => filedUnder.every((wards) => wards.includes(ward))),
      });
    }
    return spread.get(want.key.key);
  };
  // A mention of the ward's name off the ward marker. A division that never says where
  // its work is cannot be judged and is let through. For a ward with no tender of its
  // own to say which divisions and old numbers are its, the name has to be in one
  // division over the whole index (Ambedkar Nagar is in four).
  const mentioned = candidates.filter(({ tender, ward, marked: onMarker }) => ward && !onMarker
    && !elsewhere(tender.title, point, roster)
    && (worksNear(tender) || !placed.has(divisionOf(tender)))
    && sameOldWard(tender)
    && (marked.length > 0 || spreadOf(ward).divisions.size <= 1));
  const byWard = [...marked, ...mentioned];
  const wardDivisions = new Set(byWard.map(({ tender }) => divisionOf(tender)));
  const byLocality = candidates.filter(({ tender, ward, locality }) => !ward && locality
    && !elsewhere(tender.title, point, roster, { wide: true })
    && sameOldWard(tender)
    && (wardDivisions.size ? wardDivisions.has(divisionOf(tender))
      : spreadOf(locality).divisions.size === 1 && worksNear(tender))
    && (oldNumbers.size > 0 || !spreadOf(locality).severalWards));
  const found = [...byWard, ...byLocality];
  found.sort((left, right) => Boolean(right.ward) - Boolean(left.ward)
    || Boolean(right.locality) - Boolean(left.locality)
    || (left.published < right.published ? 1 : left.published > right.published ? -1 : 0)
    || String(left.tender.tender_number).localeCompare(String(right.tender.tender_number)));
  return found.slice(0, limit)
    .map(({ tender, ward, locality }) => publicWardTender(tender, (ward || locality).basis));
}
