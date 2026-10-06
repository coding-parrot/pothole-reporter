import { tenderCoversCarriageway } from './tender-scope.mjs';

export const STOP = new Set([
  "road", "roads", "street", "cross", "main", "layout", "bengaluru", "bangalore",
  "karnataka", "india", "ward", "city", "corporation", "south", "north", "east",
  "west", "central", "urban", "sector", "stage", "block", "phase", "nagar",
  "area", "locality", "village", "town", "zone", "division", "circle", "junction",
  "the", "of", "at", "in", "from", "to", "and", "near", "no", "number", "limits", "tmc", "cmc", "dma",
]);
function tokens(value) {
  const words = String(value || "").toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
  const result = new Set(words.filter((word) => word.length > 2 && !STOP.has(word)));
  for (let index = 0; index + 1 < words.length; index += 1) {
    const joined = words[index] + words[index + 1];
    if (joined.length >= 5 && !STOP.has(words[index]) && !STOP.has(words[index + 1])) {
      result.add(joined);
    }
  }
  return result;
}

export function hasRoadSurfaceScope(title) {
  return tenderCoversCarriageway(title);
}

function publicTender(tender, confidence, reason) {
  return {
    tender_number: tender.tender_number,
    title: tender.title,
    location: tender.location || null,
    contractor: tender.contractor || null,
    published: tender.published || null,
    confidence,
    reason,
    match_method: "deterministic_location_scope",
    warranty: "current liability not established by the publication record",
    warranty_code: "unverified",
    source_name: tender.source_name || null,
    source_url: tender.source_url || null,
  };
}

export function matchTender(address, tenders) {
  const wanted = tokens(String(address || "").split(",").slice(0, 4).join(","));
  const wardIds = value => new Set([...String(value || '').matchAll(/\bward\s*(?:(?:no|number)\.?\s*)?(\d+)\b/gi)].map(m => String(Number(m[1]))));
  const wantedWards = wardIds(address);
  if (!wanted.size && !wantedWards.size) return { tender: null, reason: "address_unresolved" };
  const road = String(address || '').split(',')[0].trim().toLowerCase();
  const namedRoad = /^[a-z0-9]+(?:\s+[a-z0-9]+){0,3}\s+(?:road|street|cross|lane)$/i.test(road);
  const eligible = [];
  for (const tender of tenders) {
    if (!hasRoadSurfaceScope(tender.title)) continue;
    const wards = wardIds(tender.title);
    if (wantedWards.size && wards.size && ![...wantedWards].some(w => wards.has(w))) continue;
    const title = String(tender.title || '').toLowerCase();
    const areaWide = /\b(?:all|various)\s+roads\b|\b(?:throughout|across)\b|\bpothole\s+(?:filling|repairs?|maintenance)\b(?:(?!\b(?:road|street|cross|lane)\b).){0,90}\bward\b/i.test(title);
    const numbered = /^(\d+(?:st|nd|rd|th)?)\s+(cross|main|road|street)$/.exec(road);
    const coordinatedNumber = numbered && new RegExp(`\\b${numbered[1]}\\s+(?:and\\s+\\d+(?:st|nd|rd|th)?\\s+){1,3}${numbered[2]}\\b`).test(title);
    if (namedRoad && !areaWide && !coordinatedNumber && !title.replace(/[^a-z0-9]+/g, ' ').includes(road)) continue;
    const candidate = tokens(title);
    // The civic-body/division column is routing metadata, not proof that a named
    // stretch covers the photo. Remove those words from both sides of matching.
    const administrative = tokens(tender.location);
    let overlap = 0;
    for (const token of wanted) if (candidate.has(token) && !administrative.has(token)) overlap += 1;
    if ([...wantedWards].some(w => wards.has(w))) overlap += 2;
    const citywideDescription = /\broads\s+in\s+(?:the\s+)?limits\s+of\b/.test(title);
    if (!namedRoad && citywideDescription && [...wanted].some(token => administrative.has(token) && candidate.has(token))) overlap += 1;
    if (overlap) eligible.push({ tender, overlap });
  }
  if (!eligible.length) return { tender: null, reason: "no_location_match" };
  eligible.sort((left, right) => right.overlap - left.overlap
    || String(left.tender.tender_number).localeCompare(String(right.tender.tender_number)));
  if (eligible[1] && eligible[1].overlap === eligible[0].overlap) {
    return { tender: null, reason: "no_confident_match" };
  }
  const confidence = Math.min(0.95, 0.62 + eligible[0].overlap * 0.08);
  return {
    tender: publicTender(
      eligible[0].tender,
      confidence,
      `${eligible[0].overlap} location token(s) matched an explicit road-surface scope.`,
    ),
    reason: null,
  };
}
