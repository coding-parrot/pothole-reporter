// Scope parity with the shipped browser classifier is enforced by tender-scope.test.mjs.
// Keep procurement scope separate from pothole image classification.
const ROAD_WORK_ACTIONS = new Set(["asphalt", "asphalting", "construct", "construction",
  "develop", "development", "improve", "improvement", "improvements", "maintain",
  "maintenance", "patching", "recarpet", "recarpeting", "reconstruct", "reconstruction",
  "rehabilitate", "rehabilitation", "renew", "renewal", "repair", "repairs", "resurface",
  "resurfacing", "restore", "restoration", "strengthen", "strengthening", "tarring",
  "upgrade", "upgradation", "widen", "widening", "formation"]);
const ROAD_NOUNS = new Set(["road", "roads", "carriageway", "carriageways"]);
const NON_CARRIAGEWAY_ASSETS = new Set(["arch", "arches", "barricade", "barricades",
  "bhavan", "bhavana", "borewell", "bridge", "bridges", "building", "buildings", "burial",
  "bus", "cable", "cables", "cattle", "cd", "camera", "cameras", "cctv", "center",
  "centre", "chamber", "chambers", "cistern", "college", "collage", "complex", "compound",
  "court", "courts", "culvert", "culverts", "deck", "dog", "dogsheltar", "drain", "drainage", "drains",
  "electrical", "fence", "fencing", "footpath", "footpaths", "garden", "facility",
  "facilities", "floor", "floors", "gantry", "gateway", "gateways", "graveyard", "hall",
  "helipad", "helipads", "hospital", "house", "houses",
  "kerb", "kerbs", "curb", "curbs", "lake", "lawn", "lawns", "light", "lighting", "lights", "machinehole",
  "machineholes", "manhole", "manholes", "mast", "masts", "median", "mh", "mhc",
  "network", "nursery", "park", "parking", "path", "paths", "pedestrian", "pipeline", "pipelines", "pipe", "pipes",
  "playground", "plaza", "pole", "poles", "pound", "pumphouse", "pump", "quarters", "roof", "roofs",
  "room", "rooms", "runway", "runways", "school", "sewer", "sewerage", "shed", "shelter", "shishuvihara",
  "sidewalk", "sidewalks", "sign", "signage", "signboard", "signboards", "slab", "sorting",
  "stand", "temple", "toilet", "toilets", "track", "tracks", "transformer", "transformers", "tree", "trees",
  "ugd", "unit", "urinal", "urinals", "utility", "utilities", "valve", "valves",
  "vending", "walkway", "walkways", "wall", "walls", "water"]);
const NON_SURFACE_ROAD_MODIFIERS = new Set(["divider", "dividers", "furniture", "light",
  "lighting", "lights", "marking", "markings", "median", "medians", "shoulder", "shoulders",
  "sign", "signage", "signboard", "signboards"]);
const ROAD_PREFIX_MODIFIERS = new Set(["asphalt", "asphalted", "asphaltic", "bituminous",
  "bt", "cc", "cement", "concrete", "flexible", "internal", "link", "main", "metalled",
  "paver", "rigid"]);
const LOCATION_PREPOSITIONS = new Set(["across", "along", "at", "behind", "beside", "in",
  "inside", "near", "on", "opposite", "within"]);
const explicitRoadDamageRe = /\b(?:repair(?:ing|s)?|fill(?:ing)?|patch(?:ing)?)\s+(?:of\s+)?(?:pot\s*holes?|potholes?)\b|\b(?:pot\s*holes?|potholes?)\s+(?:repair(?:s|ing)?|fill(?:ing)?|patch(?:ing)?|work|works)\b|\battend(?:ing)?\b.{0,48}\b(?:pot\s*holes?|potholes?)\b|\b(?:road|carriageway)\s+(?:patch(?:ing|work)?|surface\s+repair)\b|\b(?:patch(?:ing|work)?|surface\s+repair)\s+(?:of\s+)?(?:the\s+)?(?:road|carriageway)\b/;
const surfaceTreatmentRe = /\b(?:asphalting|re\s+asphalting|black\s*topping|tarring|resurfac(?:e|ing)|re\s+carpet(?:ing)?|recarpet(?:ing)?|recarpetting|dense\s+bituminous\s+macadam|bituminous\s+concrete|wet\s+mix\s+macadam|(?:premix|pre\s*mix)\s+carpet|seal\s+coat)\b/;
const nonCarriagewayTreatmentTargetRe = /\b(?:asphalting|re\s+asphalting|black\s*topping|tarring|resurfacing|re\s+carpeting|recarpeting|recarpetting|dense\s+bituminous\s+macadam|bituminous\s+concrete|wet\s+mix\s+macadam|(?:premix|pre\s*mix)\s+carpet|seal\s+coat|(?:pot\s*holes?|potholes?)\s+(?:repair(?:s|ing)?|filling|patching)?)\b(?:\s+work)?\s+(?:(?:of|to|on|at|in|for|with)\s+)?(?:the\s+)?(?:(?!roads?\b|carriageways?\b)[a-z0-9]+\s+){0,3}(?:bridge|court|culvert|drain|floor|footpath|garden|helipad|lawn|parking|path|playground|roof|runway|sidewalk|track|walkway|wall)s?\b/;
const materialPavementRe = /\b(?:asphalt(?:ic)?|bituminous|cement\s+concrete|concrete|flexible|rigid)\s+pavement\b/;
// Advisory/design/inspection assignments can repeat the full physical road scope
// without procuring the works. Keep this in lockstep with tools/tender_scope.py and
// apply it before any positive road phrase. EPC/design-and-build is intentionally not
// rejected unless the title explicitly describes one of these non-works services.
const nonWorksServiceRe = /\bconsult(?:ant|ancy|ants|ing)\b|\b(?:authority|independent)\s+engineer(?:ing)?\b|\bproject\s+management\s+(?:consult(?:ant|ancy|ing)|services?)\b|\b(?:preparation|prepare|preparing|revision|review)\s+of\s+(?:a\s+|the\s+)?(?:detailed\s+project\s+report|dpr)\b|\b(?:detailed\s+project\s+report|dpr)\s+(?:preparation|consultancy|services?)\b|\b(?:feasibility|traffic)\s+(?:study|studies|survey|surveys)\b|\bsurvey\s+(?:and|&)\s+investigation\b|\bthird\s+party\s+(?:inspection|quality\s+(?:audit|monitoring))\b|\b(?:quality\s+control|proof\s+checking)\s+(?:consultancy|services?)\b|\b(?:structural\s+)?design\s+(?:and|&)\s+drawing(?:s)?\b|\btotal\s+station\s+survey\b|\broad\s+inventory\b.*\b(?:survey|condition\s+assessment)\b|\bhiring\s+of\s+(?:labou?r|machinery|plant|equipment)\b|\bsupply(?:ing)?\s+of\b.*\b(?:aggregate|asphalt|bitumen|cold\s+mix|pothole(?:s)?\s+repair\s+material|ready\s+mix|stone\s+dust)\b/;
const roadsideVegetationRe = /\broad\s*side\s+(?:monsoon\s+)?plantations?\b|\broadside\s+(?:monsoon\s+)?plantations?\b|\bsocial\s+forestr(?:y|ies)\b|(?:\w*plantation\w*|\w*forestr\w*).*\broad\s+side\w*\b|\broad\s+side\w*\b.*(?:\w*plantation\w*|\w*forestr\w*)/;

const tenderTokens = (value) => (String(value || "").toLowerCase().match(/[a-z0-9]+/g) || []);
const hasAny = (tokens, values) => tokens.some((token) => values.has(token));
const mixedRoadScope = (tokens, roadIndex) => {
  let i = roadIndex - 1;
  while (i >= 0 && ROAD_PREFIX_MODIFIERS.has(tokens[i])) i--;
  return i >= 1 && tokens[i] === "and" && NON_CARRIAGEWAY_ASSETS.has(tokens[i - 1]);
};
const coordinatedRoadNoun = (tokens, roadIndex) => {
  let i = roadIndex - 1;
  while (i >= 0 && ROAD_PREFIX_MODIFIERS.has(tokens[i])) i--;
  return i >= 0 && ["and", "plus", "with"].includes(tokens[i]);
};
const roadIsNonSurfaceModifier = (tokens, roadIndex) => {
  const following = tokens.slice(roadIndex + 1, roadIndex + 4);
  if (!following.length) return false;
  if (NON_SURFACE_ROAD_MODIFIERS.has(following[0])) return true;
  for (const token of following) {
    if (["and", "at", "from", "in", "near", "of", "on", "to", "via"].includes(token)) break;
    if (NON_CARRIAGEWAY_ASSETS.has(token)) return true;
  }
  return following.length >= 2 && following[0] === "side"
    && ["drain", "drains", "light", "lights", "shoulder", "shoulders"].includes(following[1]);
};

function tenderCoversCarriageway(title, tenderNumber) {
  void tenderNumber; // category fragments such as /RD/ are not scope evidence.
  let text = tenderTokens(title).join(" ");
  if (!text) return false;
  const subwork = text.split(/\bsw\b/);
  if (subwork.length > 1 && subwork[subwork.length - 1].trim()) {
    text = subwork[subwork.length - 1].trim();
  }
  if (nonWorksServiceRe.test(text)) return false;
  if (roadsideVegetationRe.test(text)) return false;
  const tokens = text.split(" ");
  const hasNonRoadAsset = hasAny(tokens, NON_CARRIAGEWAY_ASSETS);
  // Treatment words alone do not identify the asset. Public notices include resurfaced
  // tennis courts, asphalt garden paths and pothole repairs to footpaths. Do not let
  // those phrases bypass the object/coordination checks below.
  if (explicitRoadDamageRe.test(text)
      && !nonCarriagewayTreatmentTargetRe.test(text)) return true;
  if (surfaceTreatmentRe.test(text)
      && !nonCarriagewayTreatmentTargetRe.test(text)) return true;
  if (materialPavementRe.test(text)
      && !nonCarriagewayTreatmentTargetRe.test(text)) return true;
  for (let roadIndex = 0; roadIndex < tokens.length; roadIndex++) {
    if (!ROAD_NOUNS.has(tokens[roadIndex]) || roadIsNonSurfaceModifier(tokens, roadIndex)) continue;
    const after = tokens.slice(roadIndex + 1, roadIndex + 4);
    if (after.length && (ROAD_WORK_ACTIONS.has(after[0]) || ["work", "works"].includes(after[0]))) {
      const priorAssets = hasAny(tokens.slice(0, roadIndex), NON_CARRIAGEWAY_ASSETS);
      const governedTail = [];
      for (const token of tokens.slice(roadIndex + 1, roadIndex + 9)) {
        if (["at", "from", "near", "on", "to", "via"].includes(token)) break;
        governedTail.push(token);
      }
      const governedAssets = hasAny(governedTail, NON_CARRIAGEWAY_ASSETS);
      if ((!priorAssets || coordinatedRoadNoun(tokens, roadIndex)) && !governedAssets) return true;
    }
    const start = Math.max(0, roadIndex - 12);
    let actionIndex = null;
    for (let i = roadIndex - 1; i >= start; i--) {
      if (ROAD_WORK_ACTIONS.has(tokens[i])) { actionIndex = i; break; }
    }
    if (actionIndex !== null) {
      const gap = tokens.slice(actionIndex + 1, roadIndex);
      const competing = hasAny(gap, NON_CARRIAGEWAY_ASSETS);
      const directScope = gap.length <= 3;
      const actionObjectBefore = hasAny(tokens.slice(Math.max(0, actionIndex - 6), actionIndex),
        NON_CARRIAGEWAY_ASSETS);
      const coordinatedAction = actionIndex > 0
        && ["and", "plus", "with"].includes(tokens[actionIndex - 1]);
      const isLocation = hasAny(gap, LOCATION_PREPOSITIONS);
      if (mixedRoadScope(tokens, roadIndex) || (!competing && !isLocation
          && (!actionObjectBefore || coordinatedAction) && (directScope || !hasNonRoadAsset))) return true;
    }
  }
  const route = tokens.some((token, index) => /^(?:nh|sh|mdr|odr)\d+$/.test(token)
    || (["nh", "sh", "mdr", "odr"].includes(token) && /^\d+$/.test(tokens[index + 1] || "")));
  return route && hasAny(tokens, ROAD_WORK_ACTIONS) && !hasNonRoadAsset;
}

export { tenderCoversCarriageway };

