// The app engine: the entire pipeline on-device, no server anywhere.
// The page's api() delegates every call here.
(() => {
  const NATIVE = !!(window.Capacitor && Capacitor.isNativePlatform && Capacitor.isNativePlatform());

  // Browser-only development shortcut. A URL fragment never reaches HTTP access logs;
  // remove it immediately after seeding the local key. This never runs in the APK.
  if (!NATIVE) {
    const k = new URLSearchParams(location.hash.replace(/^#/, "")).get("key");
    if (k) {
      localStorage.setItem("openai_key", k);
      history.replaceState(null, "", location.pathname + location.search);
    }
  }

  const S = {
    get key() { return (localStorage.getItem("openai_key") || "").trim(); },
    get name() { return (localStorage.getItem("sender_name") || "").trim() || "A concerned citizen"; },
    get debug() { return localStorage.getItem("debug_mode") === "1"; },
    get model() { return normaliseModel(localStorage.getItem("detection_model")); },
    get detail() { return normaliseDetail(localStorage.getItem("image_detail"), this.model); },
  };

  const LANG = () => {
    const value = localStorage.getItem("app_lang");
    return value === "kn" || value === "mr" || value === "bn" ? value : "en";
  };
  const PROGRESS = {
    en: { compress: "Preparing photo...", capture: "Preparing road views...",
          detect: "AI checking for reportable road damage...", finalize: "Checking location and complaint route...",
          write: "Writing the complaint...", email: "Opening your email app..." },
    kn: { compress: "ಫೋಟೋ ಸಂಕುಚಿಸಲಾಗುತ್ತಿದೆ...", capture: "ಫ್ರೇಮ್ ಸೆರೆಹಿಡಿಯಲಾಗುತ್ತಿದೆ...",
          detect: "AI ವರದಿ ಮಾಡಬಹುದಾದ ರಸ್ತೆ ಹಾನಿ ಪರಿಶೀಲಿಸುತ್ತಿದೆ...", finalize: "ಸ್ಥಳ ಮತ್ತು ದೂರು ಮಾರ್ಗ ಪರಿಶೀಲಿಸಲಾಗುತ್ತಿದೆ...",
          write: "ದೂರು ಬರೆಯಲಾಗುತ್ತಿದೆ...", email: "ನಿಮ್ಮ ಇಮೇಲ್ ಆ್ಯಪ್ ತೆರೆಯಲಾಗುತ್ತಿದೆ..." },
    mr: { compress: "फोटो तयार करत आहे...", capture: "रस्त्याची दृश्ये तयार करत आहे...",
          detect: "AI नोंदवण्यायोग्य रस्त्याचे नुकसान तपासत आहे...", finalize: "पत्ता आणि मार्ग निश्चित करत आहे...",
          write: "तक्रारीचा मसुदा तयार करत आहे...", email: "ईमेल अॅप उघडत आहे..." },
    bn: { compress: "ছবি প্রস্তুত করা হচ্ছে...", capture: "রাস্তার দৃশ্য প্রস্তুত করা হচ্ছে...",
          detect: "AI অভিযোগযোগ্য রাস্তার ক্ষতি খুঁজছে...", finalize: "ঠিকানা ও অভিযোগের পথ চূড়ান্ত করা হচ্ছে...",
          write: "অভিযোগের খসড়া তৈরি হচ্ছে...", email: "ইমেল অ্যাপ খোলা হচ্ছে..." },
  };
  const pmsg = (k) => (PROGRESS[LANG()] && PROGRESS[LANG()][k]) || PROGRESS.en[k];

  const DEFAULT_MODEL = "gpt-5-mini";
  const ALLOWED_MODELS = new Set([DEFAULT_MODEL, "gpt-5.6"]);
  const ALLOWED_DETAILS = new Set(["high", "original"]);
  const PROMPT_VERSION = "road-damage-v3";
  const SCHEMA_VERSION = 3;
  const MAX_DETECTION_IMAGES = 4;
  // Detection still examines every burst. Only after a burst is accepted do we group it
  // with a road-damage event already saved at the same place. This preserves capture
  // recall while stopping adjacent bursts and later drives from creating repeat drafts.
  const DEDUPE_ADJACENT_RADIUS_M = 12;
  const DEDUPE_HISTORY_RADIUS_M = 8;
  const DEDUPE_MISSING_HEADING_RADIUS_M = 5;
  const DEDUPE_SAME_DRIVE_S = 4;
  const DEDUPE_POOR_GPS_S = 2;
  const DEDUPE_HISTORY_S = 30 * 24 * 60 * 60;
  const ACCEPTED_REPORT_STATUSES = new Set(["draft", "queued", "sent", "unrouted"]);

  const normaliseManualCaptureSource = (value) => value === "manual_camera"
    ? "manual_camera" : value === "manual_import" ? "manual_import" : "manual";
  const isManualCaptureSource = (value) => /^manual(?:_|$)/.test(String(value || ""));

  function normaliseModel(value) {
    return ALLOWED_MODELS.has(value) ? value : DEFAULT_MODEL;
  }
  function normaliseDetail(value, model) {
    const picked = ALLOWED_DETAILS.has(value) ? value : "high";
    // `original` is intentionally an experiment arm for the newest model. Older
    // vision models do not support it, so fail safely to their highest valid setting.
    return picked === "original" && model !== "gpt-5.6" ? "high" : picked;
  }

  const OFFICERS = {
    "bengaluru central city corporation": ["Commissioner, Bengaluru Central City Corporation (BCCC)", "commissionerbccc@gmail.com"],
    "bengaluru east city corporation": ["Commissioner, Bengaluru East City Corporation (BECC)", "commissioner.becc@gmail.com"],
    "bengaluru north city corporation": ["Commissioner, Bengaluru North City Corporation (BNCC)", "bengalurunorthcitycorporation@gmail.com"],
    "bengaluru south city corporation": ["Commissioner, Bengaluru South City Corporation (BSCC)", "comm.south.gba@gmail.com"],
    "bengaluru west city corporation": ["Commissioner, Bengaluru West City Corporation (BWCC)", "commissioner.bwcc@gmail.com"],
  };

  // State-specific contacts and polygons live in immutable data packs. The APK keeps
  // only the parsers, strict validators and the package IDs Android permits it to query.
  // A downloaded pack can therefore add data, never executable behaviour.
  const AUTHORITY_REGISTRY_VERSION = 8;
  const LAUNCHABLE_PACKAGES = new Set([
    "com.bmc.potholequickfix", "com.sis.pwdsewaapp", "com.kmc.app",
    "com.newnmmc.app", "com.nyatitechnologies.pmcroadmitra",
    "com.ceedeev.grivenancev2", "cgg.gov.ghmc", "com.amplvb.ccrs",
    "com.nhai.rajmargyatra", "com.nammabengaluruNew.org",
    "com.esri.ugms_bmc", "in.gov.pmc.pmccare", "com.nic.dl.delhijanmitra",
  ]);
  const OFFICIAL_HANDOFF_CHANNELS = new Set(["official_handoff", "bmc_quickfix"]);
  const ISSUE_TYPES = Object.freeze(["road_damage", "garbage", "open_manhole"]);
  const ISSUE_TYPE_SET = new Set(ISSUE_TYPES);
  const normaliseIssueType = (value) => ISSUE_TYPE_SET.has(value) ? value : "road_damage";

  // Several cities publish a pothole-only app and a separate general civic system.
  // Keep the geographic authority binding unchanged, but choose the channel that can
  // actually accept the selected issue. These URLs are intentionally code-pinned: an
  // old saved report is refreshed through this table before any external page opens.
  const CIVIC_HANDOFF_OVERRIDES = Object.freeze({
    "mh-bmc": Object.freeze({
      handoff_name: "BMC MARG Complaint Portal",
      handoff_url: "https://marg.mcgm.gov.in/MARG/welcomePage.html",
      handoff_package: "com.esri.ugms_bmc",
      alternate_handoff_name: null,
      alternate_handoff_url: null,
      whatsapp_url: null,
      helpline: "1916",
    }),
    "mh-pmc": Object.freeze({
      handoff_name: "PMC CARE",
      handoff_url: "https://www.pmccare.in/",
      handoff_package: "in.gov.pmc.pmccare",
      alternate_handoff_name: null,
      alternate_handoff_url: null,
      whatsapp_url: "https://wa.me/919689900002",
      helpline: "18001030222",
    }),
    "mh-umc": Object.freeze({
      handoff_name: "Aaple Sarkar",
      handoff_url: "https://grievances.maharashtra.gov.in/en",
      handoff_package: null,
      alternate_handoff_name: null,
      alternate_handoff_url: null,
      whatsapp_url: null,
      helpline: null,
    }),
    "mh-mmr-unverified": Object.freeze({
      authority_name: "MMR civic authority (verify in Aaple Sarkar)",
      handoff_name: "Aaple Sarkar",
      handoff_url: "https://grievances.maharashtra.gov.in/en",
      handoff_package: null,
      alternate_handoff_name: null,
      alternate_handoff_url: null,
      whatsapp_url: null,
      helpline: null,
    }),
    "mh-statewide-unverified": Object.freeze({
      authority_name: "Maharashtra authority (select in Aaple Sarkar)",
      handoff_name: "Aaple Sarkar",
      handoff_url: "https://grievances.maharashtra.gov.in/en",
      handoff_package: null,
      alternate_handoff_name: "MahaULB (urban areas)",
      alternate_handoff_url: "https://mahaulb.in/MahaULB/index",
      whatsapp_url: null,
      helpline: null,
    }),
    "dl-pwd-sewa": Object.freeze({
      authority_name: "Delhi civic grievance coordination",
      handoff_name: "Delhi CM JanSunwai",
      handoff_url: "https://cmjansunwai.delhi.gov.in/",
      handoff_package: "com.nic.dl.delhijanmitra",
      alternate_handoff_name: "Delhi PGMS",
      alternate_handoff_url: "https://pgms.delhi.gov.in/",
      whatsapp_url: null,
      helpline: "1902",
    }),
  });
  const BENGALURU_AUTHORITY_NAMES = new Set([
    "bengaluru central city corporation", "bengaluru east city corporation",
    "bengaluru north city corporation", "bengaluru south city corporation",
    "bengaluru west city corporation",
  ]);
  const BENGALURU_HANDOFF = Object.freeze({
    handoff_name: "Namma Bengaluru (Sahaaya 2.0)",
    handoff_url: "https://nammabengaluru.org.in/login",
    handoff_package: "com.nammabengaluruNew.org",
    alternate_handoff_name: "BBMP official app directory",
    alternate_handoff_url: "https://site.bbmp.gov.in/departmentwebsites/BBMPIT/mobileapps.html",
    whatsapp_url: null,
    helpline: "1533",
  });
  // Only these reviewed general-grievance channels are allowed to inherit their base
  // route for garbage and manhole reports. Other municipal email/road routes fail
  // closed instead of assuming that a recipient accepts an unrelated category.
  const GENERAL_CIVIC_AUTHORITY_IDS = new Set([
    "wb-kmc", "wb-statewide-unverified", "tn-gcc", "tg-cure-shared", "gj-amc",
  ]);
  const MUMBAI_STATES = new Set(["maharashtra", "महाराष्ट्र"]);
  const WEST_BENGAL_STATES = new Set(["west bengal", "পশ্চিমবঙ্গ"]);
  const KARNATAKA_STATES = new Set(["karnataka", "ಕರ್ನಾಟಕ"]);
  const MUMBAI_DISTRICTS = new Set([
    "mumbai city district", "mumbai city", "mumbai suburban district", "mumbai suburban",
    "मुंबई शहर जिल्हा", "मुंबई शहर", "मुंबई उपनगर जिल्हा", "मुंबई उपनगर",
  ]);
  const MUMBAI_WARDS = new Set([
    "A", "B", "C", "D", "E", "F/N", "F/S", "G/N", "G/S", "H/E", "H/W",
    "K/E", "K/W", "L", "M/E", "M/W", "N", "P/N", "P/S", "R/C", "R/N",
    "R/S", "S", "T",
  ]);

  // These stable containers are populated only after a routing pack passes its complete
  // byte, envelope, contact and geometry validation. Keeping the references stable makes
  // the existing pure helpers and old saved reports upgrade without a second registry.
  const MMR_AUTHORITIES = [];
  const PMC_AUTHORITY = {};
  const MMR_FALLBACK_AUTHORITY = {};
  const MAHARASHTRA_STATE_AUTHORITY = {};
  const KMC_AUTHORITY = {};
  const WEST_BENGAL_STATE_AUTHORITY = {};
  const DELHI_PWD_AUTHORITY = {};
  const NATIONAL_HIGHWAY_AUTHORITY = {};
  const OFFICIAL_AUTHORITIES = [];
  const OFFICIAL_AUTHORITY_INDEX = new Map();
  const MMR_ALIAS_INDEX = new Map();
  const PACK_AUTHORITIES_BY_STATE = new Map();
  const PACK_ID_BY_AUTHORITY = new Map();
  const MMR_DIRECT_AUTHORITY_IDS = new Set([
    "mh-bmc", "mh-tmc", "mh-kdmc", "mh-nmmc", "mh-umc", "mh-bncmc",
    "mh-vvcmc", "mh-mbmc", "mh-panvel", "mh-ambarnath", "mh-badlapur",
  ]);
  const MMR_FALLBACK_AUTHORITY_IDS = new Set();

  // Karnataka jurisdiction lookup.
  //
  // Which body owns a road is a question the state already answers: KGIS holds the
  // boundary of every urban local body and returns the one containing a point, along
  // with its class and its national LGD code. Keying the officer directory on that code
  // rather than on a place name from a geocoder is what makes this work statewide: name
  // matching guessed, a point-in-polygon lookup does not.
  //
  // The rule that has not changed: a body we hold no verified address for is not routed.
  // Refusing is correct; addressing a citizen's complaint to a guess is not.
  const KGIS_TOWN_URL = "https://kgis.ksrsac.in/kgismaps/rest/services/Boundaries/Admin_Dynamic_New/MapServer/1/query";
  // State basemap layer 289, the national highway network, from the same KSRSAC service
  // the boundaries come from.
  const KGIS_NH_URL = "https://kgis.ksrsac.in/kgismaps/rest/services/State_Basemap/State_Basemap_Dynamic/MapServer/289/query";
  const KGIS_GP_URL = "https://kgis.ksrsac.in/kgismaps/rest/services/Boundaries/GP_Boundary/MapServer/0/query";
  const OFFICER_TITLES = { CC: "Commissioner", CMC: "Chief Officer", TMC: "Chief Officer",
                           TP: "Chief Officer", NAC: "Chief Officer" };

  let _bodies = null;
  // A routing-pack failure is never converted to an empty registry: empty would wrongly
  // claim the town has no published address. Null means the verified state data itself
  // was unavailable, and the caller reports that distinct fail-closed outcome.
  async function bodies() {
    if (_bodies) return _bodies;
    try {
      const pack = await loadStatePack("in-ka-routing");
      const loaded = pack && pack.payload && pack.payload.bodies;
      if (loaded && Object.keys(loaded).length) { _bodies = loaded; return _bodies; }
    } catch (e) { /* fall through and retry on the next call */ }
    return null;
  }

  // Bengaluru is still resolvable without the network: the five corporations are the
  // common case and a demo should not depend on a state GIS being reachable.
  const BLR = { minLat: 12.70, maxLat: 13.25, minLng: 77.25, maxLng: 77.90 };
  function inCoverage(lat, lng, address) {
    if (lat != null && lng != null && !Number.isNaN(lat) && !Number.isNaN(lng)) {
      return lat >= BLR.minLat && lat <= BLR.maxLat && lng >= BLR.minLng && lng <= BLR.maxLng;
    }
    if (address) {
      const low = address.toLowerCase();
      return low.includes("bengaluru") || low.includes("bangalore");
    }
    return false; // no location at all: we cannot claim to know who is responsible
  }

  const DETECT_PROMPT = `You are inspecting one or more chronologically ordered road views for a civic complaint app.

Decide whether they show reportable damage on the paved surface used by moving traffic. Classify the condition precisely:
- pothole_cavity: a localized open cavity with a broken rim, missing material, or visible depth.
- failed_patch: a previous repair that has broken, sunk, opened, or shed aggregate. A level intact patch is not damage.
- surface_breakup: asphalt/concrete has materially disintegrated or stripped across an area, even if there is no single cavity.
- rut_or_depression: a materially sunken wheel path or road depression with a genuine level change.
- other_road_damage: another serious defect in the travelled paved surface that needs repair.
- none: no reportable road damage is visible.

Choose one primary type consistently. Use failed_patch when the failed material or repair boundary is visibly a previous repair. Otherwise, a distinct localized open cavity takes precedence as pothole_cavity. Use surface_breakup only for broad disintegration without one dominant cavity or identifiable failed repair, and rut_or_depression for a smooth/continuous level change rather than missing broken material.

Evidence rules:
- A shadow, stain, water, glare, dust, loose roadside debris, lane marking, intact patch, manhole, drain, road edge, shoulder erosion, or speed breaker is not reportable damage by itself.
- The defect must be on the drivable paved surface, not merely beside it.
- Look for a defined broken edge/rim, missing material, displaced aggregate, or a depth/level-change cue. Use agreement and parallax across views when several are supplied.
- image_quality is unusable when blur, darkness, glare, obstruction, or distance prevents a defensible judgment.
- assessment is clear only when the defect and structural evidence are unambiguous; probable when strong evidence remains despite modest quality limits; uncertain when a confounder cannot be ruled out; absent when no reportable defect is visible.
- Set reportable true only when the most likely damage_type is not none. Do not convert uncertainty into confidence percentages.
- Classify size as small (below 30 cm wide), medium (30 to 60 cm), or large (above 60 cm or a damaged cluster). Use null when scale is not defensible.
- description: one or two factual sentences naming the condition, its position, the visible evidence, and the road-user hazard. Do not call failed surface or a failed repair a pothole.`;

  // Key order is the streaming order. The decision fields arrive before the factual
  // description, so the UI can update without using a made-up confidence percentage.
  const ASSESS_SCHEMA = {
    type: "object", additionalProperties: false,
    required: ["reportable", "assessment", "image_quality", "damage_type",
      "on_drivable_surface", "has_broken_edge_or_rim", "has_depth_or_surface_loss",
      "temporal_consistency", "size", "description"],
    properties: {
      reportable: { type: "boolean" },
      assessment: { type: "string", enum: ["clear", "probable", "uncertain", "absent"] },
      image_quality: { type: "string", enum: ["usable", "degraded", "unusable"] },
      damage_type: { type: "string", enum: ["pothole_cavity", "failed_patch", "surface_breakup",
        "rut_or_depression", "other_road_damage", "none"] },
      on_drivable_surface: { type: "boolean" },
      has_broken_edge_or_rim: { type: "boolean" },
      has_depth_or_surface_loss: { type: "boolean" },
      temporal_consistency: { type: "string", enum: ["consistent", "single_view", "inconsistent", "not_applicable"] },
      size: { type: ["string", "null"], enum: ["small", "medium", "large", null] },
      description: { type: "string" },
    },
  };
  const TENDER_SCHEMA = {
    type: "object", additionalProperties: false,
    required: ["match_index", "confidence", "reason"],
    properties: {
      match_index: { type: ["integer", "null"] },
      confidence: { type: "number" },
      reason: { type: "string" },
    },
  };

  // ---------- OpenAI ----------
  const OAI_URL = "https://api.openai.com/v1/responses";
  const authHeaders = () => ({ "Content-Type": "application/json", "Authorization": `Bearer ${S.key}` });

  // Detection is a classification job, not an essay: left at its default the model
  // spends 200+ hidden reasoning tokens per photo before answering, which measured
  // as roughly 3.5 of the 6.5 seconds a verdict used to take.
  const withSpeedDefaults = (body) => ({
    ...body,
    // Detection inputs can contain precise road imagery and addresses. Do not retain
    // response application state beyond the request; provider abuse-monitoring rules
    // remain governed by OpenAI's published policy and are disclosed in our policy.
    store: false,
    reasoning: (body && body.reasoning)
      || { effort: body && body.model === "gpt-5.6" ? "none" : "minimal" },
  });

  // Fatal means "fails the same way without streaming", so retrying plain is pointless.
  const fatal = (e) => { e.fatal = true; return e; };
  // A stalled request is worse than a failed one: without this a lost connection
  // leaves the UI on a spinner with no end, and a drive quietly stops forever.
  const REQUEST_TIMEOUT_MS = 30000;

  // The timeout used to be cleared the moment the headers arrived, so it only ever covered
  // the handshake. A response that sent headers and then stalled was never aborted, and a
  // drive wedged with every slot occupied while the HUD went on reporting it healthy.
  //
  // The timer now stays armed until the body is finished. A streaming caller re-arms it on
  // every chunk that carries data, so a slow but live response is fine and a silent one is
  // not, and disarms it when the body is done.
  async function fetchWithTimeout(url, init, ms = REQUEST_TIMEOUT_MS) {
    const ctl = typeof AbortController !== "undefined" ? new AbortController() : null;
    let timer = setTimeout(() => ctl && ctl.abort(), ms);
    const disarm = () => { clearTimeout(timer); timer = null; };
    const rearm = (delay) => {
      if (timer === null) return;             // already finished
      clearTimeout(timer);
      timer = setTimeout(() => ctl && ctl.abort(), delay === undefined ? ms : delay);
    };
    let res;
    try {
      res = await fetch(url, ctl ? { ...init, signal: ctl.signal } : init);
    } catch (e) {
      disarm();
      if (e && (e.name === "AbortError" || /abort/i.test(e.message || ""))) {
        const to = new Error("The network did not respond. Check the connection and try again.");
        to.timeout = true;
        throw to;
      }
      // Platform network errors read like `Unable to resolve host "api.openai.com"`.
      // Nobody watching a demo should be shown that.
      throw new Error("Could not reach OpenAI. Check the connection and try again.");
    }
    res.__disarm = disarm;
    res.__rearm = rearm;
    return res;
  }

  // Reading a body must always disarm the watchdog, including when it throws.
  async function readJson(res) {
    try { return await res.json(); }
    finally { if (res.__disarm) res.__disarm(); }
  }

  // Never surface a provider's response body: it is JSON, it is long, and on a
  // projected screen it reads as a crash.
  async function statusError(res) {
    const bad = mapStatus(res);
    if (bad) return bad;
    if (res.status === 400) return new Error("OpenAI rejected the request. If this persists, the app needs an update.");
    if (res.status === 408 || res.status === 504) return new Error("OpenAI timed out. Try again.");
    if (res.status >= 500) return new Error("OpenAI is having trouble right now. Try again in a moment.");
    return new Error("OpenAI could not process that image. Try again.");
  }

  function mapStatus(res) {
    if (res.status === 401) return fatal(new Error("OpenAI rejected the API key. Check it in settings."));
    if (res.status === 403) return fatal(new Error("This API key is not allowed to use the model. Check the key in settings."));
    // 429 covers both throttling and an exhausted balance, and telling someone to
    // wait a minute for a spent quota sends them in circles.
    if (res.status === 429) return fatal(new Error("OpenAI refused: rate limit or the key's credit is exhausted. Check the account."));
    return null;
  }

  async function oai(body) {
    if (!S.key) throw new Error("OpenAI API key missing. Tap the gear icon and paste it.");
    const res = await fetchWithTimeout(OAI_URL, {
      method: "POST", headers: authHeaders(), body: JSON.stringify(withSpeedDefaults(body)),
    });
    if (!res.ok) throw await statusError(res);
    const data = await readJson(res);
    const msg = (data.output || []).find((o) => o.type === "message");
    const text = msg && msg.content && msg.content.find((c) => c.type === "output_text");
    if (!text || !text.text) throw new Error("Empty model response.");
    return JSON.parse(text.text);
  }

  // Structured outputs stream in schema order. Only closed string values are read:
  // a partial `"pothole_cav` must never become a decision. The same semantic helper is
  // used for the streamed and final paths so the UI cannot announce a result that the
  // pipeline later reverses.
  const REPORTABLE_RE = /"reportable"\s*:\s*(true|false)/;
  const ASSESSMENT_RE = /"assessment"\s*:\s*"(clear|probable|uncertain|absent)"/;
  const QUALITY_RE = /"image_quality"\s*:\s*"(usable|degraded|unusable)"/;
  const DAMAGE_RE = /"damage_type"\s*:\s*"(pothole_cavity|failed_patch|surface_breakup|rut_or_depression|other_road_damage|none)"/;

  function decisionFor(a) {
    if (!a || a.reportable !== true || a.damage_type === "none" || !a.on_drivable_surface) return "reject";
    if (a.assessment === "absent") return "reject";
    if (a.image_quality === "unusable" || a.assessment === "uncertain" ||
        a.temporal_consistency === "inconsistent") return "review";
    if (a.assessment !== "clear" && a.assessment !== "probable") return "review";
    // Structural damage needs at least one visible physical cue. Failed patches and
    // broad surface breakup do not need a cavity-shaped rim, but they do need either a
    // broken edge or actual material/depth loss.
    if (!a.has_broken_edge_or_rim && !a.has_depth_or_surface_loss) return "review";
    return "accept";
  }

  function partialAssessment(text) {
    const r = REPORTABLE_RE.exec(text);
    if (!r) return null;
    if (r[1] === "false") return {
      reportable: false, assessment: "absent", image_quality: "usable", damage_type: "none",
      on_drivable_surface: false, has_broken_edge_or_rim: false,
      has_depth_or_surface_loss: false, temporal_consistency: "not_applicable",
    };
    const a = ASSESSMENT_RE.exec(text), q = QUALITY_RE.exec(text), d = DAMAGE_RE.exec(text);
    if (!a || !q || !d) return null;
    return {
      reportable: true, assessment: a[1], image_quality: q[1], damage_type: d[1],
      // The later evidence fields are deliberately left unknown. peekVerdict only
      // announces a positive once the complete semantic decision can be evaluated.
    };
  }

  const peekVerdict = (partial) => {
    const a = partialAssessment(partial);
    if (!a) return null;
    if (!a.reportable) return { accepted: false, review: false, damage_type: "none", assessment: "absent" };
    const road = /"on_drivable_surface"\s*:\s*(true|false)/.exec(partial);
    const edge = /"has_broken_edge_or_rim"\s*:\s*(true|false)/.exec(partial);
    const depth = /"has_depth_or_surface_loss"\s*:\s*(true|false)/.exec(partial);
    const temporal = /"temporal_consistency"\s*:\s*"(consistent|single_view|inconsistent|not_applicable)"/.exec(partial);
    if (!road || !edge || !depth || !temporal) return null;
    Object.assign(a, {
      on_drivable_surface: road[1] === "true",
      has_broken_edge_or_rim: edge[1] === "true",
      has_depth_or_surface_loss: depth[1] === "true",
      temporal_consistency: temporal[1],
    });
    const decision = decisionFor(a);
    return { accepted: decision === "accept", review: decision === "review",
             damage_type: a.damage_type, assessment: a.assessment };
  };

  // True once the response has proved that Drive Mode will not create a complaint.
  // Debug/evaluation calls do not enable cancellation because they need the exact full
  // verdict, including the reason for a miss.
  const peekReject = (partial) => {
    const r = REPORTABLE_RE.exec(partial);
    if (!r) return false;
    if (r[1] === "false") return true;
    const a = peekVerdict(partial);
    return !!a && !a.accepted;
  };

  function drainSSE(chunk, state, onEarly, stopWhenRejected) {
    state.buf += chunk;
    let i;
    while ((i = state.buf.indexOf("\n")) >= 0) {
      const line = state.buf.slice(0, i).trim();
      state.buf = state.buf.slice(i + 1);
      if (!line.startsWith("data:")) continue;
      const payload = line.slice(5).trim();
      if (!payload || payload === "[DONE]") continue;
      let ev;
      try { ev = JSON.parse(payload); } catch (e) { continue; }
      if (ev.type === "response.output_text.delta" && typeof ev.delta === "string") {
        state.text += ev.delta;
        if (!state.early && onEarly) {
          const v = peekVerdict(state.text);
          if (v) { state.early = true; try { onEarly(v); } catch (e) {} }
        }
        if (stopWhenRejected && !state.stop && peekReject(state.text)) state.stop = true;
      }
    }
  }

  async function oaiStream(body, onEarly, stopWhenRejected) {
    if (!S.key) throw new Error("OpenAI API key missing. Tap the gear icon and paste it.");
    const res = await fetchWithTimeout(OAI_URL, {
      method: "POST", headers: authHeaders(),
      body: JSON.stringify(withSpeedDefaults({ ...body, stream: true })),
    });
    if (!res.ok) throw await statusError(res);

    const state = { buf: "", text: "", early: false, stop: false };
    if (res.body && typeof res.body.getReader === "function") {
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        // A chunk that carries data proves the response is alive, so the watchdog resets.
        // A response that goes silent mid-body is aborted rather than hanging the drive.
        if (res.__rearm) res.__rearm();
        drainSSE(dec.decode(value, { stream: true }), state, onEarly, stopWhenRejected);
        if (state.stop) { try { await reader.cancel(); } catch (e) {} break; }
      }
    } else {
      // Buffered transports (the native HTTP bridge) hand back the whole SSE body at once,
      // so there is nothing left to stop early: the tokens were already generated.
      try { drainSSE(await res.text(), state, onEarly, stopWhenRejected); }
      finally { if (res.__disarm) res.__disarm(); }
    }
    if (res.__disarm) res.__disarm();
    if (state.stop) return rejectedVerdict(state.text);
    drainSSE("\n", state, onEarly, stopWhenRejected);
    if (state.stop) return rejectedVerdict(state.text);
    if (!state.text) throw new Error("Empty model response.");
    return JSON.parse(state.text);
  }

  // Reconstructed from the closed fields that arrived before Drive Mode cancelled the
  // remaining description. It deliberately has the complete new schema shape.
  function rejectedVerdict(text) {
    const r = REPORTABLE_RE.exec(text), a = ASSESSMENT_RE.exec(text), q = QUALITY_RE.exec(text), d = DAMAGE_RE.exec(text);
    const road = /"on_drivable_surface"\s*:\s*(true|false)/.exec(text);
    const edge = /"has_broken_edge_or_rim"\s*:\s*(true|false)/.exec(text);
    const depth = /"has_depth_or_surface_loss"\s*:\s*(true|false)/.exec(text);
    const temporal = /"temporal_consistency"\s*:\s*"(consistent|single_view|inconsistent|not_applicable)"/.exec(text);
    return {
      reportable: !!r && r[1] === "true",
      assessment: a ? a[1] : "absent",
      image_quality: q ? q[1] : "usable",
      damage_type: d ? d[1] : "none",
      on_drivable_surface: !!road && road[1] === "true",
      has_broken_edge_or_rim: !!edge && edge[1] === "true",
      has_depth_or_surface_loss: !!depth && depth[1] === "true",
      temporal_consistency: temporal ? temporal[1] : "not_applicable",
      size: null,
      description: "",
    };
  }

  const fmt = (name, schema) => ({
    format: { type: "json_schema", name, schema, strict: true },
    verbosity: "low",
  });
  const progress = (m) => { try { window.dispatchEvent(new CustomEvent("pipeline-progress", { detail: m })); } catch (e) {} };
  const emitVerdict = (v) => { try { window.dispatchEvent(new CustomEvent("pipeline-verdict", { detail: v })); } catch (e) {} };

  function buildDetectionRequest(imageInputs, prompt, model = S.model, detail = S.detail) {
    const selectedModel = normaliseModel(model);
    const selectedDetail = normaliseDetail(detail, selectedModel);
    const images = (Array.isArray(imageInputs) ? imageInputs : [imageInputs])
      .filter((x) => x && (typeof x === "string" ? x : x.url))
      .slice(0, MAX_DETECTION_IMAGES);
    if (!images.length) throw new Error("No usable image supplied for detection.");
    const content = [];
    for (let i = 0; i < images.length; i++) {
      const item = typeof images[i] === "string" ? { url: images[i] } : images[i];
      content.push({ type: "input_image", image_url: item.url,
                     detail: normaliseDetail(item.detail || selectedDetail, selectedModel) });
    }
    // The prompt appears exactly once and follows the ordered evidence views.
    content.push({ type: "input_text", text: `${prompt}\n\nThe ${images.length} supplied image(s) are ordered exactly as labelled by the capture pipeline.` });
    return {
      model: selectedModel,
      input: [{ role: "user", content }],
      text: fmt("road_damage_assessment", ASSESS_SCHEMA),
    };
  }

  let streamBroken = false;
  async function analyzeImage(imageInputs, prompt, name, schema, model, onEarly, stopWhenRejected, detail) {
    const body = (schema === ASSESS_SCHEMA)
      ? buildDetectionRequest(imageInputs, prompt, model, detail)
      : {
          model,
          input: [{ role: "user", content: [
            { type: "input_image", image_url: Array.isArray(imageInputs) ? imageInputs[0] : imageInputs },
            { type: "input_text", text: prompt },
          ] }],
          text: fmt(name, schema),
        };
    if ((!onEarly && !stopWhenRejected) || streamBroken) return oai(body);
    try {
      return await oaiStream(body, onEarly, stopWhenRejected);
    } catch (e) {
      // A bad key or a rate limit fails identically unstreamed, so surface those.
      if (e && e.fatal) throw e;
      // A timeout says nothing about whether the server can stream, it says the network
      // stalled. Retrying it unstreamed stalls again, so a single stalled frame cost two
      // full timeouts, and latching streamBroken made every later frame pay for streaming
      // it would no longer use. Surface it and leave streaming alone.
      if (e && (e.timeout || e.name === "AbortError")) {
        // The abort can surface from the body reader rather than from fetch, where it
        // arrives as a bare "Aborted". Nobody watching a demo should be shown that.
        if (e.timeout) throw e;
        const to = new Error("The network did not respond. Check the connection and try again.");
        to.timeout = true;
        throw to;
      }
      // Anything else (a server that refuses stream:true, a transport that cannot
      // stream, a parse failure) must not cost us the verdict. Remember it, so the
      // wasted round trip is paid once per launch and not on every photo.
      streamBroken = true;
      return oai(body);
    }
  }

  // One warm TLS connection ahead of the first real call. Costs no tokens.
  let warmedAt = 0;
  async function prewarm() {
    if (!S.key || Date.now() - warmedAt < 60000) return;
    warmedAt = Date.now();
    try {
      await fetch("https://api.openai.com/v1/models?limit=1", { headers: authHeaders() });
    } catch (e) {}
  }

  // ---------- location ----------
  // The officer knows which state and country they work in. A complaint that says
  // "..., Bengaluru Central City Corporation, Bengaluru, Bangalore North, Bengaluru
  // Urban, Karnataka, 560052, India" reads like machine output, so the address is built
  // from the parts that actually locate the pothole: street, locality, city, pincode.
  // display_name is kept only for the offline routing fallback, which needs the
  // corporation name that this trimming deliberately drops.
  const NOMINATIM_REVERSE_ENDPOINT = "https://nominatim.openstreetmap.org/reverse";
  const reverseGeocodeCache = new Map();
  let nominatimSlot = Promise.resolve(), lastNominatimRequestAt = 0;

  async function waitForNominatimSlot() {
    const turn = nominatimSlot.then(async () => {
      const remaining = 1100 - (Date.now() - lastNominatimRequestAt);
      if (remaining > 0) await new Promise((resolve) => setTimeout(resolve, remaining));
      lastNominatimRequestAt = Date.now();
    });
    nominatimSlot = turn.catch(() => {});
    await turn;
  }

  async function reverseGeocodeUncached(lat, lng) {
    try {
      // Public Nominatim is deliberately serialized to below one request per second.
      // Production-scale deployments should change the endpoint to a compliant managed
      // or self-hosted instance; polygon routing itself does not depend on this response.
      await waitForNominatimSlot();
      const res = await fetchWithTimeout(
        `${NOMINATIM_REVERSE_ENDPOINT}?lat=${lat}&lon=${lng}&format=jsonv2&zoom=17&addressdetails=1&accept-language=en`,
        {}, 12000);
      if (!res.ok) return null;
      const d = await readJson(res);
      const a = d.address || {};
      const parts = [
        a.road || a.pedestrian || a.residential || a.footway,
        a.neighbourhood || a.hamlet,
        a.suburb || a.village,
        a.city || a.town || a.municipality,
        a.postcode,
      ].filter((x, i, all) => x && all.indexOf(x) === i);
      return {
        short: parts.join(", ") || d.display_name || null,
        full: d.display_name || null,
        // Keep jurisdiction-bearing fields separate. Collapsing these used to make it
        // impossible to tell whether "Pune" came from a city, a municipality or merely
        // the nearest town, which is unsafe when adjacent civic bodies share a district.
        city: a.city || null,
        town: a.town || null,
        municipality: a.municipality || null,
        city_district: a.city_district || null,
        county: a.county || null,
        village: a.village || null,
        suburb: a.suburb || null,
        neighbourhood: a.neighbourhood || null,
        postcode: a.postcode || null,
        state_district: a.state_district || null,
        state: a.state || null,
        country_code: a.country_code || null,
      };
    } catch (e) { return null; }
  }

  function reverseGeocode(lat, lng) {
    // About an 11 m grid at this latitude: adjacent captures reuse the same address and
    // a burst of manual reports cannot hammer the shared public endpoint.
    const key = `${Number(lat).toFixed(4)},${Number(lng).toFixed(4)}`;
    if (reverseGeocodeCache.has(key)) return reverseGeocodeCache.get(key);
    const request = reverseGeocodeUncached(lat, lng);
    reverseGeocodeCache.set(key, request);
    request.then((result) => { if (!result) reverseGeocodeCache.delete(key); },
      () => reverseGeocodeCache.delete(key));
    if (reverseGeocodeCache.size > 250) {
      reverseGeocodeCache.delete(reverseGeocodeCache.keys().next().value);
    }
    return request;
  }

  function mumbaiWardFromName(value) {
    const text = String(value || "").toUpperCase().replace(/\s+/g, " ");
    const coded = text.match(/(?:^|,\s*)([A-Z](?:[\/-][A-Z])?)\s+WARD(?:,|$)/)
      || text.match(/(?:^|,\s*)WARD\s+([A-Z](?:[\/-][A-Z])?)(?:,|$)/);
    if (coded) {
      const code = coded[1].replace("-", "/");
      if (MUMBAI_WARDS.has(code)) return code;
    }
    const named = text.match(/(?:^|,\s*)([FGHKMPR])\s+(NORTH|SOUTH|EAST|WEST|CENTRAL)\s+WARD(?:,|$)/);
    if (!named) return null;
    const direction = { NORTH: "N", SOUTH: "S", EAST: "E", WEST: "W", CENTRAL: "C" }[named[2]];
    const code = `${named[1]}/${direction}`;
    return MUMBAI_WARDS.has(code) ? code : null;
  }

  // Mumbai City and Mumbai Suburban are the two districts that make up Greater Mumbai.
  // This deliberately uses the OpenStreetMap response already obtained for the street
  // address. It does not copy BMC ward polygons or call an undocumented complaint API.
  // The ward label is only a suggestion parsed from OSM and is explicitly shown as such.
  function mumbaiFromGeocode(geo) {
    if (!geo || String(geo.country_code || "").toLowerCase() !== "in") return null;
    if (!MUMBAI_STATES.has(String(geo.state || "").trim().toLowerCase())) return null;
    const district = String(geo.state_district || "").trim().toLowerCase();
    if (!MUMBAI_DISTRICTS.has(district)) return null;
    const ward = mumbaiWardFromName(geo.full);
    return { kind: "mumbai", ward, district: geo.state_district, source: "openstreetmap" };
  }

  const normaliseAuthorityValue = (value) => String(value || "")
    .normalize("NFKC").trim().toLowerCase().replace(/\s+/g, " ");

  function validateOfficialHandoffRegistry(authorities) {
    const ids = new Set();
    const isHttps = (value) => /^https:\/\/[^\s]+$/.test(String(value || ""));
    const packageName = /^[a-zA-Z][a-zA-Z0-9_]*(?:\.[a-zA-Z0-9_]+)+$/;
    const email = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    const allowedFields = new Set([
      "id", "name", "aliases", "officer_email", "handoff_name", "handoff_url",
      "handoff_package", "alternate_handoff_name", "alternate_handoff_url",
      "whatsapp_url", "helpline",
    ]);
    for (const authority of authorities || []) {
      if (!authority || !authority.id || ids.has(authority.id)) {
        throw new Error("Duplicate or missing official authority ID.");
      }
      if (Object.keys(authority).some((field) => !allowedFields.has(field))) {
        throw new Error(`Official authority ${authority.id} has an unsupported field.`);
      }
      ids.add(authority.id);
      if (!/^[a-z]{2}-[a-z0-9-]{2,80}$/.test(authority.id)
          || !authority.name || authority.name.length > 200) {
        throw new Error(`Official authority ${authority.id} has an invalid identity.`);
      }
      if (authority.officer_email && !email.test(authority.officer_email)) {
        throw new Error(`Official authority ${authority.id} has an invalid email.`);
      }
      if (!authority.officer_email && (!authority.handoff_name || !isHttps(authority.handoff_url))) {
        throw new Error(`Official authority ${authority.id} has no valid handoff.`);
      }
      if (authority.handoff_url && !isHttps(authority.handoff_url)) {
        throw new Error(`Official authority ${authority.id} has a non-HTTPS handoff.`);
      }
      if (!!authority.alternate_handoff_name !== !!authority.alternate_handoff_url
          || (authority.alternate_handoff_url && !isHttps(authority.alternate_handoff_url))) {
        throw new Error(`Official authority ${authority.id} has an invalid alternate handoff.`);
      }
      if (authority.handoff_package && (!packageName.test(authority.handoff_package)
          || !LAUNCHABLE_PACKAGES.has(authority.handoff_package))) {
        throw new Error(`Official authority ${authority.id} has an invalid Android package.`);
      }
      if (authority.whatsapp_url
          && !/^https:\/\/wa\.me\/[1-9][0-9]{7,14}$/.test(authority.whatsapp_url)) {
        throw new Error(`Official authority ${authority.id} has an invalid WhatsApp route.`);
      }
      if (authority.helpline && !/^[0-9]{3,15}$/.test(authority.helpline)) {
        throw new Error(`Official authority ${authority.id} has an invalid helpline.`);
      }
      if (authority.aliases && (!Array.isArray(authority.aliases)
          || authority.aliases.length > 30
          || authority.aliases.some((alias) => typeof alias !== "string" || !alias || alias.length > 100))) {
        throw new Error(`Official authority ${authority.id} has invalid aliases.`);
      }
    }
    return true;
  }

  function validateAuthorityRegistry(authorities = MMR_AUTHORITIES) {
    const ids = new Set(), aliases = new Map();
    for (const authority of authorities) {
      if (!authority.id || ids.has(authority.id)) throw new Error("Duplicate or missing authority ID.");
      ids.add(authority.id);
      if (!authority.name || !Array.isArray(authority.aliases) || !authority.aliases.length) {
        throw new Error(`Authority ${authority.id} is incomplete.`);
      }
      if (authority.handoff_url && !String(authority.handoff_url).startsWith("https://")) {
        throw new Error(`Authority ${authority.id} has a non-HTTPS handoff.`);
      }
      if (authority.handoff_package
          && !/^[a-zA-Z][a-zA-Z0-9_]*(?:\.[a-zA-Z0-9_]+)+$/.test(authority.handoff_package)) {
        throw new Error(`Authority ${authority.id} has an invalid Android package.`);
      }
      for (const raw of authority.aliases) {
        const alias = normaliseAuthorityValue(raw);
        const prior = aliases.get(alias);
        if (!alias || (prior && prior !== authority.id)) {
          throw new Error(`Authority alias collision: ${raw}`);
        }
        aliases.set(alias, authority.id);
      }
    }
    return true;
  }

  function replaceStableObject(target, source) {
    for (const key of Object.keys(target)) delete target[key];
    Object.assign(target, source);
  }

  function rebuildOfficialAuthorityIndex() {
    OFFICIAL_AUTHORITIES.splice(0);
    OFFICIAL_AUTHORITY_INDEX.clear();
    for (const authorities of PACK_AUTHORITIES_BY_STATE.values()) {
      for (const authority of authorities) {
        if (OFFICIAL_AUTHORITY_INDEX.has(authority.id)) {
          throw new Error(`Official authority ${authority.id} appears in two state packs.`);
        }
        OFFICIAL_AUTHORITIES.push(authority);
        OFFICIAL_AUTHORITY_INDEX.set(authority.id, authority);
      }
    }
  }

  function installRoutingAuthorities(pack) {
    const authorities = pack && pack.authorities;
    if (!Array.isArray(authorities) || authorities.length > 1000) {
      throw new Error("Routing pack has an invalid authority registry.");
    }
    validateOfficialHandoffRegistry(authorities);
    const byId = new Map(authorities.map((authority) => [authority.id, authority]));
    if (pack.state_code === "MH") {
      const mmr = authorities.filter((authority) => authority.id.startsWith("mh-")
        && authority.id !== "mh-pmc" && authority.id !== "mh-mmr-unverified"
        && authority.id !== "mh-statewide-unverified");
      if (authorities.length !== 22 || mmr.length !== 19
          || !byId.has("mh-pmc") || !byId.has("mh-mmr-unverified")
          || !byId.has("mh-statewide-unverified")) {
        throw new Error("Maharashtra routing pack has an incomplete authority registry.");
      }
      validateAuthorityRegistry(mmr);
      MMR_AUTHORITIES.splice(0, MMR_AUTHORITIES.length, ...mmr);
      replaceStableObject(PMC_AUTHORITY, byId.get("mh-pmc"));
      replaceStableObject(MMR_FALLBACK_AUTHORITY, byId.get("mh-mmr-unverified"));
      replaceStableObject(MAHARASHTRA_STATE_AUTHORITY,
        byId.get("mh-statewide-unverified"));
      MMR_ALIAS_INDEX.clear();
      for (const authority of MMR_AUTHORITIES) {
        for (const alias of authority.aliases) {
          MMR_ALIAS_INDEX.set(normaliseAuthorityValue(alias), authority);
        }
      }
      MMR_FALLBACK_AUTHORITY_IDS.clear();
      for (const authority of MMR_AUTHORITIES) {
        if (!MMR_DIRECT_AUTHORITY_IDS.has(authority.id)) {
          MMR_FALLBACK_AUTHORITY_IDS.add(authority.id);
        }
      }
      PACK_AUTHORITIES_BY_STATE.set("MH", [
        ...MMR_AUTHORITIES, PMC_AUTHORITY, MMR_FALLBACK_AUTHORITY,
        MAHARASHTRA_STATE_AUTHORITY,
      ]);
    } else if (pack.state_code === "WB") {
      if (authorities.length !== 2 || !byId.has("wb-kmc")
          || !byId.has("wb-statewide-unverified")) {
        throw new Error("West Bengal routing pack has an invalid authority registry.");
      }
      replaceStableObject(KMC_AUTHORITY, byId.get("wb-kmc"));
      replaceStableObject(WEST_BENGAL_STATE_AUTHORITY,
        byId.get("wb-statewide-unverified"));
      PACK_AUTHORITIES_BY_STATE.set("WB", [KMC_AUTHORITY, WEST_BENGAL_STATE_AUTHORITY]);
    } else if (pack.state_code === "DL") {
      if (authorities.length !== 1 || !byId.has("dl-pwd-sewa")) {
        throw new Error("Delhi routing pack has an invalid authority registry.");
      }
      replaceStableObject(DELHI_PWD_AUTHORITY, byId.get("dl-pwd-sewa"));
      PACK_AUTHORITIES_BY_STATE.set("DL", [DELHI_PWD_AUTHORITY]);
    } else if (pack.state_code === "KA") {
      if (authorities.length) throw new Error("Karnataka contacts must use the LGD registry.");
      PACK_AUTHORITIES_BY_STATE.set("KA", []);
    } else if (pack.adapter === "municipal-city-v1") {
      if (!authorities.length || authorities.length > 100) {
        throw new Error("Municipal-city routing pack has an invalid authority registry.");
      }
      PACK_AUTHORITIES_BY_STATE.set(pack.state_code, authorities);
    } else {
      throw new Error("Unsupported routing-pack state.");
    }
    for (const [authorityId, packId] of PACK_ID_BY_AUTHORITY) {
      if (packId === pack.pack_id) PACK_ID_BY_AUTHORITY.delete(authorityId);
    }
    for (const authority of authorities) PACK_ID_BY_AUTHORITY.set(authority.id, pack.pack_id);
    rebuildOfficialAuthorityIndex();
    return true;
  }

  function pointOnSegment(x, y, ax, ay, bx, by) {
    const cross = (x - ax) * (by - ay) - (y - ay) * (bx - ax);
    if (Math.abs(cross) > 1e-10) return false;
    return x >= Math.min(ax, bx) - 1e-10 && x <= Math.max(ax, bx) + 1e-10
      && y >= Math.min(ay, by) - 1e-10 && y <= Math.max(ay, by) + 1e-10;
  }

  function pointInRing(lng, lat, ring) {
    let inside = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const [xi, yi] = ring[i], [xj, yj] = ring[j];
      if (pointOnSegment(lng, lat, xi, yi, xj, yj)) return true;
      const crosses = ((yi > lat) !== (yj > lat))
        && (lng < (xj - xi) * (lat - yi) / (yj - yi) + xi);
      if (crosses) inside = !inside;
    }
    return inside;
  }

  function pointInPolygon(lng, lat, rings) {
    if (!Array.isArray(rings) || !rings.length || !pointInRing(lng, lat, rings[0])) return false;
    for (let i = 1; i < rings.length; i++) if (pointInRing(lng, lat, rings[i])) return false;
    return true;
  }

  function pointInGeometry(lng, lat, geometry) {
    if (!geometry || !Number.isFinite(lng) || !Number.isFinite(lat)) return false;
    if (geometry.type === "Polygon") return pointInPolygon(lng, lat, geometry.coordinates);
    if (geometry.type === "MultiPolygon") {
      return geometry.coordinates.some((polygon) => pointInPolygon(lng, lat, polygon));
    }
    if (geometry.type === "GeometryCollection") {
      return Array.isArray(geometry.geometries)
        && geometry.geometries.some((part) => pointInGeometry(lng, lat, part));
    }
    return false;
  }

  function hasCoverageGeometry(geometry) {
    if (!geometry || !geometry.type) return false;
    const validPosition = (position) => Array.isArray(position) && position.length >= 2
      && Number.isFinite(position[0]) && Number.isFinite(position[1])
      && position[0] >= -180 && position[0] <= 180
      && position[1] >= -90 && position[1] <= 90;
    const validRing = (ring) => Array.isArray(ring) && ring.length >= 4
      && ring.every(validPosition)
      && ring[0][0] === ring[ring.length - 1][0]
      && ring[0][1] === ring[ring.length - 1][1];
    const validPolygon = (coordinates) => Array.isArray(coordinates)
      && coordinates.length > 0 && coordinates.every(validRing);
    if (geometry.type === "Polygon") {
      return validPolygon(geometry.coordinates);
    }
    if (geometry.type === "MultiPolygon") {
      return Array.isArray(geometry.coordinates) && geometry.coordinates.length > 0
        && geometry.coordinates.every(validPolygon);
    }
    return geometry.type === "GeometryCollection"
      && Array.isArray(geometry.geometries) && geometry.geometries.length > 0
      && geometry.geometries.every(hasCoverageGeometry);
  }

  function validMmrAuthorityBoundaries(mmr, fallbackAuthorityIds = MMR_FALLBACK_AUTHORITY_IDS) {
    const boundaries = mmr && mmr.authority_boundaries;
    if (!boundaries || typeof boundaries !== "object") return false;
    const entries = Object.entries(boundaries);
    const sameIds = (values, expected) => Array.isArray(values)
      && values.length === expected.size
      && values.every((id) => expected.has(id));
    if (entries.length !== MMR_DIRECT_AUTHORITY_IDS.size
        || !entries.every(([id]) => MMR_DIRECT_AUTHORITY_IDS.has(id))
        || !sameIds(mmr.boundary_complete_authority_ids, MMR_DIRECT_AUTHORITY_IDS)
        || !sameIds(mmr.boundary_missing_authority_ids, fallbackAuthorityIds)) {
      return false;
    }
    return entries.every(([id, boundary]) => {
      if (!boundary || !hasCoverageGeometry(boundary.geometry)) return false;
      const wards = boundary.wards || [];
      if (id !== "mh-bmc") return wards.length === 0;
      return wards.length === MUMBAI_WARDS.size
        && new Set(wards.map((ward) => ward && ward.code)).size === MUMBAI_WARDS.size
        && wards.every((ward) => ward && MUMBAI_WARDS.has(ward.code)
          && hasCoverageGeometry(ward.geometry));
    });
  }

  // Approximate the shortest ground distance to a polygon edge. If the phone's GPS
  // accuracy circle crosses a coverage or civic boundary, choosing from its centre point
  // would be false precision.
  function pointToSegmentMeters(lng, lat, a, b) {
    const rad = Math.PI / 180;
    const metresPerLng = 111320 * Math.cos(lat * rad);
    const ax = (a[0] - lng) * metresPerLng, ay = (a[1] - lat) * 110540;
    const bx = (b[0] - lng) * metresPerLng, by = (b[1] - lat) * 110540;
    const dx = bx - ax, dy = by - ay;
    const denom = dx * dx + dy * dy;
    const t = denom ? Math.max(0, Math.min(1, -(ax * dx + ay * dy) / denom)) : 0;
    return Math.hypot(ax + t * dx, ay + t * dy);
  }

  function geometryBoundaryDistanceMeters(lng, lat, geometry) {
    let best = Infinity;
    const ringDistance = (ring) => {
      if (!Array.isArray(ring) || ring.length < 2) return;
      for (let i = 1; i < ring.length; i++) {
        best = Math.min(best, pointToSegmentMeters(lng, lat, ring[i - 1], ring[i]));
      }
      if (ring[0] !== ring[ring.length - 1]) {
        best = Math.min(best, pointToSegmentMeters(lng, lat, ring[ring.length - 1], ring[0]));
      }
    };
    const visit = (part) => {
      if (!part) return;
      if (part.type === "Polygon") part.coordinates.forEach(ringDistance);
      else if (part.type === "MultiPolygon") {
        part.coordinates.forEach((polygon) => polygon.forEach(ringDistance));
      } else if (part.type === "GeometryCollection") {
        (part.geometries || []).forEach(visit);
      }
    };
    visit(geometry);
    return best;
  }

  // ---------- immutable state data packs ----------
  // The signed APK carries this tiny manifest. It names one immutable URL and full
  // SHA-256 for every supported resource; GitHub Pages never gets coordinates, photos,
  // report IDs or the API key, only the state-specific path the phone requests.
  const PACK_SITE_ROOT = "https://coding-parrot.github.io/pothole-reporter/";
  const SUPPORTED_STATE_PACKS = Object.freeze({
    "in-dl-routing": { state_code: "DL", kind: "routing", adapter: "delhi-nct-v1" },
    "in-mh-routing": { state_code: "MH", kind: "routing", adapter: "maharashtra-statewide-v1" },
    "in-wb-routing": { state_code: "WB", kind: "routing", adapter: "west-bengal-statewide-v1" },
    "in-ka-routing": { state_code: "KA", kind: "routing", adapter: "karnataka-kgis-v1" },
    "in-ka-tenders": { state_code: "KA", kind: "tenders", adapter: "karnataka-locally-indexed-v1" },
    "in-tn-routing": { state_code: "TN", kind: "routing", adapter: "municipal-city-v1" },
    "in-tg-routing": { state_code: "TG", kind: "routing", adapter: "municipal-city-v1" },
    "in-gj-routing": { state_code: "GJ", kind: "routing", adapter: "municipal-city-v1" },
  });
  const STATE_PACK_MAX_BYTES = 16 * 1024 * 1024;
  const STATE_PACK_FETCH_TIMEOUT_MS = 30000;
  let _statePackManifest = null, _statePackManifestPromise = null;
  const _statePackMemory = new Map(), _statePackPromises = new Map();

  const sameSet = (values, expected) => values.length === expected.size
    && values.every((value) => expected.has(value));
  const exactObjectKeys = (value, keys) => !!value && typeof value === "object"
    && !Array.isArray(value) && sameSet(Object.keys(value), new Set(keys));

  function validateStatePackManifest(manifest) {
    if (!exactObjectKeys(manifest,
      ["format", "schema_version", "catalog_version", "cache", "resources"])
        || manifest.format !== "pothole-pack-manifest"
        || manifest.schema_version !== 1 || manifest.catalog_version !== 1) {
      throw new Error("Invalid state-pack manifest.");
    }
    const cache = manifest.cache;
    if (!exactObjectKeys(cache,
      ["max_bytes", "routing_max_unused_days", "tender_max_unused_days"])
        || !Number.isInteger(cache.max_bytes) || cache.max_bytes < 1024 * 1024
        || cache.max_bytes > 128 * 1024 * 1024
        || !Number.isInteger(cache.routing_max_unused_days)
        || cache.routing_max_unused_days < 7 || cache.routing_max_unused_days > 365
        || !Number.isInteger(cache.tender_max_unused_days)
        || cache.tender_max_unused_days < 1 || cache.tender_max_unused_days > 180) {
      throw new Error("Invalid state-pack cache policy.");
    }
    const resources = manifest.resources;
    const expectedIds = new Set(Object.keys(SUPPORTED_STATE_PACKS));
    if (!resources || typeof resources !== "object" || Array.isArray(resources)
        || !sameSet(Object.keys(resources), expectedIds)) {
      throw new Error("State-pack manifest resource set is incomplete.");
    }
    for (const [packId, resource] of Object.entries(resources)) {
      const spec = SUPPORTED_STATE_PACKS[packId];
      const fields = ["pack_id", "state_code", "kind", "pack_version", "schema_version",
        "adapter", "path", "url", "bytes", "sha256", "coverage_scope", "statewide",
        "source_retrieved_at", "review_after", "licenses"];
      if (spec.kind === "tenders") fields.push("records");
      const pathMatch = resource && typeof resource.path === "string"
        ? resource.path.match(/^packs\/v[0-9]+\/states\/([a-z]{2})\/(routing|tenders)-([0-9a-f]{64})\.json$/)
        : null;
      if (!exactObjectKeys(resource, fields)
          || resource.pack_id !== packId || resource.state_code !== spec.state_code
          || resource.kind !== spec.kind || resource.adapter !== spec.adapter
          || resource.pack_version !== 1 || resource.schema_version !== 1
          || !pathMatch || pathMatch[1] !== spec.state_code.toLowerCase()
          || pathMatch[2] !== spec.kind || pathMatch[3] !== resource.sha256
          || resource.url !== PACK_SITE_ROOT + resource.path
          || !Number.isInteger(resource.bytes) || resource.bytes <= 0
          || resource.bytes > STATE_PACK_MAX_BYTES
          || !/^[0-9a-f]{64}$/.test(resource.sha256)
          || typeof resource.coverage_scope !== "string" || !resource.coverage_scope
          || resource.coverage_scope.length > 200 || typeof resource.statewide !== "boolean"
          || !/^\d{4}-\d{2}-\d{2}$/.test(resource.source_retrieved_at)
          || !/^\d{4}-\d{2}-\d{2}$/.test(resource.review_after)
          || !Array.isArray(resource.licenses) || !resource.licenses.length
          || resource.licenses.length > 10
          || resource.licenses.some((item) => typeof item !== "string" || !item || item.length > 300)
          || (spec.kind === "tenders"
            && (!Number.isInteger(resource.records) || resource.records <= 0 || resource.records > 100000))) {
        throw new Error(`Invalid state-pack resource: ${packId}`);
      }
    }
    return manifest;
  }

  async function getStatePackManifest() {
    if (_statePackManifest) return _statePackManifest;
    if (_statePackManifestPromise) return _statePackManifestPromise;
    _statePackManifestPromise = (async () => {
      try {
        const response = await fetch("pack-manifest.json", { cache: "no-store" });
        if (!response.ok) return null;
        const text = await response.text();
        if (!text || text.length > 128 * 1024) return null;
        _statePackManifest = validateStatePackManifest(JSON.parse(text));
      } catch (e) { /* a malformed bundled manifest disables routing; it never guesses */ }
      return _statePackManifest;
    })();
    const result = await _statePackManifestPromise;
    _statePackManifestPromise = null;
    return result;
  }

  function resolvePackUrl(resource) {
    if (!resource || typeof resource.path !== "string") return null;
    const localHost = location.hostname === "localhost" || location.hostname === "127.0.0.1"
      || location.hostname === "::1";
    // Capacitor also uses https://localhost. Its files contain no packs, so the native
    // app must use the pinned production URL; only the browser test/development server
    // maps the same immutable path locally.
    return !NATIVE && localHost ? `/${resource.path}` : resource.url;
  }

  async function sha256Bytes(value) {
    if (!(window.crypto && window.crypto.subtle)) return null;
    const bytes = value instanceof ArrayBuffer
      ? value : value && value.buffer instanceof ArrayBuffer
        ? value.buffer.slice(value.byteOffset || 0, (value.byteOffset || 0) + value.byteLength)
        : null;
    if (!bytes) return null;
    const digest = await crypto.subtle.digest("SHA-256", bytes);
    return [...new Uint8Array(digest)]
      .map((byte) => byte.toString(16).padStart(2, "0")).join("");
  }

  function validatePackEnvelope(resource, pack, kind) {
    const fields = kind === "routing"
      ? ["format", "schema_version", "pack_id", "pack_version", "state_code",
          "adapter", "generated_at", "authorities", "payload"]
      : ["format", "schema_version", "pack_id", "pack_version", "state_code",
          "adapter", "generated_at", "tenders"];
    if (!exactObjectKeys(pack, fields)
        || pack.format !== (kind === "routing" ? "pothole-routing-pack" : "pothole-tender-pack")
        || pack.schema_version !== resource.schema_version
        || pack.pack_id !== resource.pack_id || pack.pack_version !== resource.pack_version
        || pack.state_code !== resource.state_code || pack.adapter !== resource.adapter
        || pack.generated_at !== resource.source_retrieved_at) {
      throw new Error("State-pack envelope does not match its signed manifest entry.");
    }
  }

  function validateKarnatakaBodies(payload) {
    const bodies = payload && payload.bodies;
    const entries = bodies && typeof bodies === "object" && !Array.isArray(bodies)
      ? Object.entries(bodies) : [];
    const allowed = new Set(["name", "type", "officer", "email", "source", "matched_from", "short"]);
    const types = new Set(["CC", "CMC", "TMC", "TP", "NAC"]);
    if (!entries.length || entries.length > 500) return false;
    return entries.every(([lgd, body]) => /^\d{3,12}$/.test(lgd)
      && body && typeof body === "object" && !Array.isArray(body)
      && Object.keys(body).every((field) => allowed.has(field))
      && typeof body.name === "string" && !!body.name && body.name.length <= 120
      && typeof body.type === "string" && types.has(body.type)
      && typeof body.officer === "string" && !!body.officer && body.officer.length <= 100
      && typeof body.email === "string" && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(body.email)
      && body.email.length <= 200
      && typeof body.source === "string" && !!body.source && body.source.length <= 500
      && ["matched_from", "short"].every((field) => body[field] === undefined
        || (typeof body[field] === "string" && body[field].length <= 200)));
  }

  function validMunicipalEnvelope(value) {
    return exactObjectKeys(value, ["min_lng", "min_lat", "max_lng", "max_lat"])
      && [value.min_lng, value.max_lng].every((item) => Number.isFinite(item) && item >= -180 && item <= 180)
      && [value.min_lat, value.max_lat].every((item) => Number.isFinite(item) && item >= -90 && item <= 90)
      && value.min_lng < value.max_lng && value.min_lat < value.max_lat;
  }

  function municipalGeometryBounds(geometry) {
    if (!hasCoverageGeometry(geometry)) return null;
    const bounds = { min_lng: Infinity, min_lat: Infinity, max_lng: -Infinity, max_lat: -Infinity };
    const visit = (value) => {
      if (Array.isArray(value) && value.length >= 2
          && Number.isFinite(value[0]) && Number.isFinite(value[1])) {
        bounds.min_lng = Math.min(bounds.min_lng, value[0]);
        bounds.min_lat = Math.min(bounds.min_lat, value[1]);
        bounds.max_lng = Math.max(bounds.max_lng, value[0]);
        bounds.max_lat = Math.max(bounds.max_lat, value[1]);
      } else if (Array.isArray(value)) value.forEach(visit);
    };
    visit(geometry.coordinates);
    return Object.values(bounds).every(Number.isFinite) ? bounds : null;
  }

  const validMunicipalAliasList = (value) => Array.isArray(value)
    && value.length > 0 && value.length <= 30
    && value.every((item) => typeof item === "string" && !!item && item.length <= 100);

  const sameMunicipalEnvelope = (value, expected) => validMunicipalEnvelope(value)
    && Object.keys(expected).every((field) => value[field] === expected[field]);

  const sameMunicipalAliases = (value, expected) => validMunicipalAliasList(value)
    && value.length === expected.length
    && value.every((item, index) => item === expected[index]);

  const validOfficialPointQuery = (value, spatialRel) => !!value
    && /^https:\/\/[^\s]+$/.test(String(value.query_url || ""))
    && value.query_where === "1=1"
    && value.query_geometry_type === "esriGeometryEnvelope"
    && Number.isInteger(value.query_in_sr) && value.query_in_sr === 4326
    && value.query_spatial_rel === spatialRel;

  function validMunicipalExclusion(exclusion, envelope) {
    if (!exclusion || !["bbox", "official_point_query"].includes(exclusion.mode)) return false;
    const fields = ["id", "name", "mode", "bbox", "source_name", "source_url", "routing_note"];
    if (exclusion.mode === "official_point_query") {
      fields.push("query_url", "query_where", "query_geometry_type", "query_in_sr",
        "query_spatial_rel", "source_object_id");
    }
    return exactObjectKeys(exclusion, fields)
      && /^[a-z0-9][a-z0-9-]{2,100}$/.test(exclusion.id)
      && validMunicipalEnvelope(exclusion.bbox)
      && /^https:\/\/[^\s]+$/.test(exclusion.source_url)
      && ["name", "source_name", "routing_note"].every((field) =>
        typeof exclusion[field] === "string" && !!exclusion[field]
        && exclusion[field].length <= 500)
      && exclusion.bbox.min_lng >= envelope.min_lng
      && exclusion.bbox.min_lat >= envelope.min_lat
      && exclusion.bbox.max_lng <= envelope.max_lng
      && exclusion.bbox.max_lat <= envelope.max_lat
      && (exclusion.mode !== "official_point_query"
        || (typeof exclusion.source_object_id === "string" && !!exclusion.source_object_id
          && exclusion.source_object_id.length <= 200
          && validOfficialPointQuery(exclusion, "esriSpatialRelIntersects")));
  }

  function canonicalJson(value) {
    if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
    if (value && typeof value === "object") {
      return `{${Object.keys(value).sort().map((key) =>
        `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`;
    }
    return JSON.stringify(value);
  }

  async function validateMunicipalCityPayload(resource, pack) {
    const payload = pack.payload;
    if (!exactObjectKeys(payload, ["version", "retrieved_at", "regions"])
        || payload.version !== 1 || payload.retrieved_at !== pack.generated_at
        || !Array.isArray(payload.regions) || !payload.regions.length
        || payload.regions.length > 100) {
      return false;
    }
    const expected = MUNICIPAL_CITY_CONFIGS[resource.pack_id];
    if (!expected || payload.regions.length !== 1) return false;
    const authorityDigest = await sha256Hex(canonicalJson(pack.authorities));
    const regionDigest = await sha256Hex(canonicalJson(payload.regions[0]));
    if (authorityDigest !== expected.authority_sha256
        || regionDigest !== expected.region_sha256) return false;

    const authorityIds = new Set(pack.authorities.map((authority) => authority.id));
    const usedAuthorityIds = new Set();
    const commonFields = [
      "id", "authority_id", "name", "scope", "routing_mode", "routing_source",
      "match_value", "state_aliases", "place_aliases", "envelope", "source_name",
      "source_home_url", "source_url", "source_license", "attribution",
      "official_scope_reference", "routing_note", "limitations", "exclusions", "source_object_id",
    ];
    for (const region of payload.regions) {
      if (!region || !["boundary", "structured_geocode", "official_point_query"]
        .includes(region.routing_mode)) return false;
      const fields = [...commonFields];
      if (region.routing_mode === "boundary") {
        fields.push("coordinate_precision", "area_km2", "bbox", "geometry_sha256", "geometry");
      } else if (region.routing_mode === "official_point_query") {
        fields.push("query_url", "query_where", "query_geometry_type", "query_in_sr",
          "query_spatial_rel", "official_area_km2", "legal_references");
      }
      const strings = ["id", "authority_id", "name", "scope", "routing_source", "match_value",
        "source_name", "source_home_url", "source_url", "source_license", "attribution",
        "official_scope_reference", "routing_note", "source_object_id"];
      if (!exactObjectKeys(region, fields)
          || region.id !== expected.region_id
          || region.authority_id !== expected.authority_id
          || region.routing_mode !== expected.routing_mode
          || region.routing_source !== expected.routing_source
          || !sameMunicipalEnvelope(region.envelope, expected.envelope)
          || !sameMunicipalAliases(region.state_aliases, expected.state_aliases)
          || !sameMunicipalAliases(region.place_aliases, expected.place_aliases)
          || !/^[a-z0-9][a-z0-9-]{2,100}$/.test(region.id)
          || !new RegExp(`^${resource.state_code.toLowerCase()}-[a-z0-9-]{2,80}$`).test(region.authority_id)
          || strings.some((field) => typeof region[field] !== "string"
            || !region[field] || region[field].length > 1000)
          || !/^https:\/\/[^\s]+$/.test(region.source_home_url)
          || !/^https:\/\/[^\s]+$/.test(region.source_url)
          || !/^https:\/\/[^\s]+$/.test(region.official_scope_reference)
          || !validMunicipalAliasList(region.state_aliases)
          || !validMunicipalAliasList(region.place_aliases)
          || !validMunicipalEnvelope(region.envelope)
          || !Array.isArray(region.limitations) || !region.limitations.length
          || region.limitations.length > 10
          || region.limitations.some((item) => typeof item !== "string" || !item || item.length > 500)
          || !Array.isArray(region.exclusions) || region.exclusions.length > 10
          || region.exclusions.some((exclusion) =>
            !validMunicipalExclusion(exclusion, region.envelope))
          || !authorityIds.has(region.authority_id) || usedAuthorityIds.has(region.authority_id)) {
        return false;
      }
      usedAuthorityIds.add(region.authority_id);
      if (region.routing_mode === "boundary") {
        const digest = hasCoverageGeometry(region.geometry)
          ? await sha256Hex(JSON.stringify(region.geometry)) : null;
        const calculated = municipalGeometryBounds(region.geometry);
        const near = (first, second) => Math.abs(first - second) <= 1e-7;
        if (!Number.isInteger(region.coordinate_precision) || region.coordinate_precision !== 7
            || !Number.isFinite(region.area_km2) || region.area_km2 <= 1 || region.area_km2 > 10000
            || !validMunicipalEnvelope(region.bbox) || !calculated
            || !Object.keys(calculated).every((key) => near(calculated[key], region.bbox[key]))
            || calculated.min_lng < region.envelope.min_lng
            || calculated.min_lat < region.envelope.min_lat
            || calculated.max_lng > region.envelope.max_lng
            || calculated.max_lat > region.envelope.max_lat
            || !/^[0-9a-f]{64}$/.test(region.geometry_sha256)
            || digest !== region.geometry_sha256) {
          return false;
        }
      } else if (region.routing_mode === "official_point_query") {
        if (!validOfficialPointQuery(region, "esriSpatialRelWithin")
            || !Number.isFinite(region.official_area_km2)
            || region.official_area_km2 <= 1 || region.official_area_km2 > 10000
            || !Array.isArray(region.legal_references)
            || !region.legal_references.length || region.legal_references.length > 10
            || region.legal_references.some((reference) =>
              !exactObjectKeys(reference, ["title", "date", "url"])
              || typeof reference.title !== "string" || !reference.title
              || reference.title.length > 500
              || !/^\d{4}-\d{2}-\d{2}$/.test(String(reference.date || ""))
              || !/^https:\/\/[^\s]+$/.test(String(reference.url || "")))) {
          return false;
        }
      }
    }
    return usedAuthorityIds.size === authorityIds.size;
  }

  async function validateRoutingPack(resource, pack) {
    validatePackEnvelope(resource, pack, "routing");
    if (!Array.isArray(pack.authorities) || pack.authorities.length > 1000) {
      throw new Error("Routing pack authority list is invalid.");
    }
    validateOfficialHandoffRegistry(pack.authorities);
    const payload = pack.payload;
    if (resource.pack_id === "in-dl-routing") {
      const region = payload && payload.region;
      const digest = region && hasCoverageGeometry(region.geometry)
        ? await sha256Hex(JSON.stringify(region.geometry)) : null;
      if (!payload || payload.version !== 1 || !region || pack.authorities.length !== 1
          || pack.authorities[0].id !== "dl-pwd-sewa" || region.id !== "delhi-nct"
          || region.authority_id !== "dl-pwd-sewa" || Number(region.osm_relation_id) !== 1942586
          || region.geometry_sha256 !== DELHI_GEOMETRY_SHA256
          || digest !== DELHI_GEOMETRY_SHA256) {
        throw new Error("Delhi routing pack failed its boundary or authority checks.");
      }
    } else if (resource.pack_id === "in-wb-routing") {
      const byId = new Map(pack.authorities.map((authority) => [authority.id, authority]));
      const regions = payload && payload.regions;
      const kmc = regions && regions.kmc;
      const state = regions && regions.west_bengal;
      const kmcDigest = kmc && hasCoverageGeometry(kmc.geometry)
        ? await sha256Hex(JSON.stringify(kmc.geometry)) : null;
      const stateDigest = state && hasCoverageGeometry(state.geometry)
        ? await sha256Hex(JSON.stringify(state.geometry)) : null;
      if (!payload || payload.version !== 2 || !regions || !kmc || !state
          || pack.authorities.length !== 2 || !byId.has("wb-kmc")
          || !byId.has("wb-statewide-unverified")
          || kmc.authority_id !== "wb-kmc"
          || String(kmc.ulb_code) !== "250299" || String(kmc.mun_id) !== "250299_0000001"
          || kmc.geometry_sha256 !== KMC_GEOMETRY_SHA256
          || kmcDigest !== KMC_GEOMETRY_SHA256
          || state.authority_id !== "wb-statewide-unverified"
          || Number(state.source_relation_id) !== 1960177
          || state.geometry_sha256 !== WEST_BENGAL_STATE_GEOMETRY_SHA256
          || stateDigest !== WEST_BENGAL_STATE_GEOMETRY_SHA256) {
        throw new Error("West Bengal routing pack failed its boundary or authority checks.");
      }
    } else if (resource.pack_id === "in-mh-routing") {
      const byId = new Map(pack.authorities.map((authority) => [authority.id, authority]));
      const mmrAuthorities = pack.authorities.filter((authority) => authority.id.startsWith("mh-")
        && authority.id !== "mh-pmc" && authority.id !== "mh-mmr-unverified"
        && authority.id !== "mh-statewide-unverified");
      const fallbackIds = new Set(mmrAuthorities.map((authority) => authority.id)
        .filter((id) => !MMR_DIRECT_AUTHORITY_IDS.has(id)));
      const state = payload && payload.regions && payload.regions.maharashtra;
      const stateDigest = state && hasCoverageGeometry(state.geometry)
        ? await sha256Hex(JSON.stringify(state.geometry)) : null;
      if (!payload || payload.version !== 2 || !payload.regions || !payload.regions.mmr
          || !payload.regions.pmc || !state
          || pack.authorities.length !== 22 || mmrAuthorities.length !== 19
          || !byId.has("mh-pmc") || !byId.has("mh-mmr-unverified")
          || !byId.has("mh-statewide-unverified")
          || !hasCoverageGeometry(payload.regions.mmr.geometry)
          || !hasCoverageGeometry(payload.regions.pmc.geometry)
          || state.authority_id !== "mh-statewide-unverified"
          || Number(state.source_relation_id) !== 1950884
          || state.geometry_sha256 !== MAHARASHTRA_STATE_GEOMETRY_SHA256
          || stateDigest !== MAHARASHTRA_STATE_GEOMETRY_SHA256
          || !validMmrAuthorityBoundaries(payload.regions.mmr, fallbackIds)) {
        throw new Error("Maharashtra routing pack failed its boundary or authority checks.");
      }
      validateAuthorityRegistry(mmrAuthorities);
    } else if (resource.pack_id === "in-ka-routing") {
      if (pack.authorities.length || !validateKarnatakaBodies(payload)) {
        throw new Error("Karnataka routing pack failed its LGD contact checks.");
      }
    } else if (resource.adapter === "municipal-city-v1") {
      if (!await validateMunicipalCityPayload(resource, pack)) {
        throw new Error("Municipal-city routing pack failed its boundary, source or authority checks.");
      }
    } else {
      throw new Error("Unsupported routing pack.");
    }
    // Install contacts only after every byte, identity and resource-specific check passed.
    installRoutingAuthorities(pack);
    return pack;
  }

  function validateTenderPack(resource, pack) {
    validatePackEnvelope(resource, pack, "tenders");
    const rows = pack.tenders;
    const fields = new Set(["tn", "t", "loc", "c", "d", "b"]), seen = new Set();
    if (!Array.isArray(rows) || rows.length !== resource.records || rows.length > 100000) {
      throw new Error("Tender-pack record count is invalid.");
    }
    for (const row of rows) {
      if (!exactObjectKeys(row, fields)
          || typeof row.tn !== "string" || !row.tn || row.tn.length > 100
          || typeof row.t !== "string" || !row.t || row.t.length > 500
          || typeof row.loc !== "string" || row.loc.length > 200
          || typeof row.c !== "string" || row.c.length > 200
          || typeof row.d !== "string" || !/^\d{2}-\d{2}-\d{4}$/.test(row.d)
          || typeof row.b !== "string" || !/^(?:BLR|\d{3,12})$/.test(row.b)) {
        throw new Error("Tender pack contains an invalid record.");
      }
      const identity = `${row.tn}\u0000${row.b}`;
      if (seen.has(identity)) throw new Error("Tender pack contains a duplicate record.");
      seen.add(identity);
    }
    return pack;
  }

  async function validateDecodedStatePack(resource, bytes) {
    if (!(bytes instanceof ArrayBuffer) || bytes.byteLength !== resource.bytes) {
      throw new Error("State-pack byte length does not match its signed manifest.");
    }
    const digest = await sha256Bytes(bytes);
    if (!digest || digest !== resource.sha256) {
      throw new Error("State-pack checksum does not match its signed manifest.");
    }
    if (!window.TextDecoder) throw new Error("This device cannot decode verified state data.");
    const text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    const pack = JSON.parse(text);
    return resource.kind === "routing"
      ? validateRoutingPack(resource, pack) : validateTenderPack(resource, pack);
  }

  const statePackCacheKey = (resource) =>
    `${resource.pack_id}@${resource.pack_version}:${resource.sha256}`;
  const allStatePacks = () => op("readonly", (store) => store.getAll(), "state_packs");
  const getCachedStatePack = (key) => op("readonly", (store) => store.get(key), "state_packs");
  const putCachedStatePack = (record) => op("readwrite", (store) => store.put(record), "state_packs");
  const deleteCachedStatePack = (key) => op("readwrite", (store) => store.delete(key), "state_packs");

  async function cachedPackBytes(record) {
    const value = record && (record.blob || record.payload);
    if (value instanceof Blob) return value.arrayBuffer();
    if (value instanceof ArrayBuffer) return value.slice(0);
    if (ArrayBuffer.isView(value)) {
      return value.buffer.slice(value.byteOffset, value.byteOffset + value.byteLength);
    }
    throw new Error("Cached state pack has no binary payload.");
  }

  async function touchStatePack(record) {
    if (!record || Date.now() - Number(record.last_used_at || 0) < 24 * 60 * 60 * 1000) return;
    record.last_used_at = Date.now();
    try { await putCachedStatePack(record); } catch (e) { /* usage metadata is optional */ }
  }

  async function pruneStatePacks(activePackId = null) {
    const [manifest, highwayManifest] = await Promise.all([
      getStatePackManifest(), getHighwayPackManifest(),
    ]);
    if (!manifest && !highwayManifest) return { removed: 0, bytes: 0 };
    let records;
    try { records = await allStatePacks(); } catch (e) { return { removed: 0, bytes: 0 }; }
    const resources = [
      ...Object.values((manifest && manifest.resources) || {}),
      ...Object.values((highwayManifest && highwayManifest.tiles) || {}),
    ];
    const currentByKey = new Map(resources
      .map((resource) => [statePackCacheKey(resource), resource]));
    const protectedIds = new Set([
      activePackId, ..._statePackPromises.keys(),
      ...[..._highwayTilePromises.keys()].map((id) => `in-nh-${id}`),
    ]
      .filter(Boolean));
    const now = Date.now(), toDelete = [], kept = [];
    for (const record of records || []) {
      const resource = record && currentByKey.get(record.cache_key);
      if (!resource || record.pack_id !== resource.pack_id || record.sha256 !== resource.sha256
          || record.bytes !== resource.bytes) {
        toDelete.push(record); continue;
      }
      const ageDays = Math.max(0, now - Number(record.last_used_at || record.installed_at || 0))
        / (24 * 60 * 60 * 1000);
      const unusedLimit = resource.kind === "highways"
        ? highwayManifest.cache.max_unused_days
        : resource.kind === "tenders"
          ? manifest.cache.tender_max_unused_days : manifest.cache.routing_max_unused_days;
      if (ageDays > unusedLimit && !protectedIds.has(resource.pack_id)) {
        toDelete.push(record); continue;
      }
      kept.push({ record, resource });
    }
    let total = kept.reduce((sum, item) => sum + Math.max(0, Number(item.record.bytes) || 0), 0);
    kept.sort((left, right) => {
      const tenderFirst = (left.resource.kind === "tenders" ? 0 : 1)
        - (right.resource.kind === "tenders" ? 0 : 1);
      return tenderFirst || Number(left.record.last_used_at || 0) - Number(right.record.last_used_at || 0);
    });
    const maxBytes = Math.min(
      manifest ? manifest.cache.max_bytes : Infinity,
      highwayManifest ? highwayManifest.cache.max_bytes : Infinity);
    for (const item of kept) {
      if (total <= maxBytes) break;
      if (protectedIds.has(item.resource.pack_id)) continue;
      toDelete.push(item.record);
      total -= Math.max(0, Number(item.record.bytes) || 0);
    }
    let removed = 0, removedBytes = 0;
    for (const record of toDelete) {
      try {
        await deleteCachedStatePack(record.cache_key);
        removed++;
        removedBytes += Math.max(0, Number(record.bytes) || 0);
      } catch (e) { /* cleanup must never break the active report */ }
    }
    return { removed, bytes: removedBytes };
  }

  async function fetchStatePack(resource) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), STATE_PACK_FETCH_TIMEOUT_MS);
    try {
      const response = await fetch(resolvePackUrl(resource), {
        cache: "no-store", credentials: "omit", referrerPolicy: "no-referrer",
        signal: controller.signal,
      });
      if (!response.ok) return null;
      const contentType = response.headers.get("content-type") || "";
      // Content-Length can describe gzip/br transfer bytes while arrayBuffer() contains
      // decoded bytes. The post-read exact length and SHA below are the authoritative
      // checks, so a CDN compression choice cannot disable every production pack.
      if (contentType && !/json/i.test(contentType)) return null;
      const bytes = await response.arrayBuffer();
      const pack = await validateDecodedStatePack(resource, bytes);
      return { pack, bytes };
    } catch (e) { return null; }
    finally { clearTimeout(timer); }
  }

  async function loadStatePack(packId) {
    if (_statePackPromises.has(packId)) return _statePackPromises.get(packId);
    const task = (async () => {
      const manifest = await getStatePackManifest();
      const resource = manifest && manifest.resources[packId];
      if (!resource) return null;
      const cacheKey = statePackCacheKey(resource);
      const memory = _statePackMemory.get(packId);
      if (memory && memory.cache_key === cacheKey) return memory.pack;

      let cached = null;
      try { cached = await getCachedStatePack(cacheKey); } catch (e) { /* download below */ }
      if (cached) {
        try {
          const pack = await validateDecodedStatePack(resource, await cachedPackBytes(cached));
          _statePackMemory.set(packId, { cache_key: cacheKey, pack, resource });
          touchStatePack(cached);
          pruneStatePacks(packId);
          return pack;
        } catch (e) {
          try { await deleteCachedStatePack(cacheKey); } catch (_) {}
        }
      }

      const downloaded = await fetchStatePack(resource);
      if (!downloaded) return null;
      const { pack, bytes } = downloaded;
      const now = Date.now();
      if (bytes instanceof ArrayBuffer) {
        const record = {
          cache_key: cacheKey, pack_id: resource.pack_id, pack_version: resource.pack_version,
          state_code: resource.state_code, kind: resource.kind, sha256: resource.sha256,
          bytes: resource.bytes, installed_at: now, last_used_at: now,
          blob: new Blob([bytes], { type: "application/json" }),
        };
        try { await putCachedStatePack(record); }
        catch (e) {
          await pruneStatePacks(packId);
          try { await putCachedStatePack(record); } catch (_) { /* valid for this session */ }
        }
      }
      _statePackMemory.set(packId, { cache_key: cacheKey, pack, resource });
      pruneStatePacks(packId);
      return pack;
    })();
    _statePackPromises.set(packId, task);
    try { return await task; }
    finally { _statePackPromises.delete(packId); }
  }

  function statePackProvenance(packId, prefix = "routing") {
    const item = _statePackMemory.get(packId);
    const resource = item && item.resource;
    if (!resource) return {};
    return {
      [`${prefix}_pack_id`]: resource.pack_id,
      [`${prefix}_pack_version`]: resource.pack_version,
      [`${prefix}_pack_sha256`]: resource.sha256,
      [`${prefix}_pack_state_code`]: resource.state_code,
    };
  }

  function resetStatePackMemory() {
    _statePackManifest = null;
    _statePackManifestPromise = null;
    _statePackMemory.clear();
    _statePackPromises.clear();
    municipalCityCoverageCache.clear();
    municipalCityCoveragePromises.clear();
    resetHighwayPackMemory();
  }

  // ---------- nationwide National Highway tiles ----------
  // The app ships only this small catalog. Each immutable 2-degree tile is downloaded
  // after a report has a location, checked against its pinned SHA-256, and cached in the
  // same bounded store as state packs. Geometry says that a point is on a mapped NH/NE
  // carriageway; it deliberately does not pretend to identify the maintaining agency.
  const HIGHWAY_MANIFEST_MAX_BYTES = 512 * 1024;
  const HIGHWAY_TILE_MAX_BYTES = 8 * 1024 * 1024;
  const HIGHWAY_FETCH_TIMEOUT_MS = 30000;
  const HIGHWAY_REF_RE = /^N[HE]-[0-9]{1,4}[A-Z]{0,3}(?: \/ N[HE]-[0-9]{1,4}[A-Z]{0,3})*$/;
  let _highwayManifest = null, _highwayManifestPromise = null;
  const _highwayTileMemory = new Map(), _highwayTilePromises = new Map();

  function validateHighwayManifest(manifest) {
    if (!exactObjectKeys(manifest,
      ["authority", "cache", "catalog_version", "format", "match", "schema_version",
        "source", "tiles"])
        || manifest.format !== "pothole-highway-pack-manifest"
        || manifest.schema_version !== 1 || manifest.catalog_version !== 1) {
      throw new Error("Invalid National Highway manifest.");
    }
    const cache = manifest.cache;
    if (!exactObjectKeys(cache, ["max_bytes", "max_unused_days"])
        || cache.max_bytes !== 67108864 || cache.max_unused_days !== 90) {
      throw new Error("Invalid National Highway cache policy.");
    }
    const match = manifest.match;
    if (!exactObjectKeys(match,
      ["max_gps_accuracy_m", "max_match_distance_m", "minimum_match_distance_m",
        "tile_buffer_m", "tile_size_degrees"])
        || match.max_gps_accuracy_m !== 30 || match.max_match_distance_m !== 45
        || match.minimum_match_distance_m !== 15 || match.tile_buffer_m !== 60
        || match.tile_size_degrees !== 2) {
      throw new Error("Invalid National Highway matching policy.");
    }
    const authority = manifest.authority;
    validateOfficialHandoffRegistry([authority]);
    if (!exactObjectKeys(authority,
      ["alternate_handoff_name", "alternate_handoff_url", "handoff_name",
        "handoff_package", "handoff_url", "helpline", "id", "name"])
        || authority.id !== "in-national-highway"
        || authority.handoff_name !== "Rajmargyatra"
        || authority.handoff_package !== "com.nhai.rajmargyatra"
        || authority.handoff_url !== "https://play.google.com/store/apps/details?id=com.nhai.rajmargyatra"
        || authority.alternate_handoff_name !== "CPGRAMS"
        || authority.alternate_handoff_url !== "https://pgportal.gov.in/"
        || authority.helpline !== "1033") {
      throw new Error("Invalid National Highway official handoff.");
    }
    const source = manifest.source;
    const sourceFields = [
      "accepted_features", "attribution", "distinct_refs", "limitations",
      "mapped_carriageway_km", "output_features", "output_points", "source_features",
      "source_filter", "source_home_url", "source_license", "source_md5", "source_name",
      "source_points", "source_retrieved_at", "source_url", "tile_count",
    ];
    if (!exactObjectKeys(source, sourceFields)
        || source.source_name !== "OpenStreetMap India extract by Geofabrik"
        || source.source_url !== "https://download.geofabrik.de/asia/india-260820.osm.pbf"
        || source.source_retrieved_at !== "2026-08-20"
        || source.source_md5 !== "c5e0a62a1cb00c80d8c5948bf18370d7"
        || source.source_license !== "Open Data Commons Open Database License (ODbL) 1.0"
        || !Array.isArray(source.limitations) || source.limitations.length !== 3
        || !Number.isInteger(source.accepted_features) || source.accepted_features < 100000
        || !Number.isInteger(source.distinct_refs) || source.distinct_refs < 500) {
      throw new Error("Invalid National Highway source receipt.");
    }
    const tiles = manifest.tiles;
    if (!tiles || typeof tiles !== "object" || Array.isArray(tiles)
        || Object.keys(tiles).length < 50 || Object.keys(tiles).length > 300
        || source.tile_count !== Object.keys(tiles).length) {
      throw new Error("Invalid National Highway tile catalog.");
    }
    const fields = ["bbox", "bytes", "feature_count", "kind", "pack_id", "pack_version",
      "path", "ref_count", "schema_version", "sha256", "state_code", "tile_id", "url"];
    for (const [identifier, resource] of Object.entries(tiles)) {
      const idMatch = identifier.match(/^e([0-9]{3})n([0-9]{2})$/);
      const pathMatch = resource && typeof resource.path === "string"
        ? resource.path.match(/^packs\/v1\/highways\/(e[0-9]{3}n[0-9]{2})-([0-9a-f]{64})\.json$/)
        : null;
      const lng = idMatch ? Number(idMatch[1]) : NaN;
      const lat = idMatch ? Number(idMatch[2]) : NaN;
      if (!exactObjectKeys(resource, fields) || !idMatch
          || resource.tile_id !== identifier || resource.pack_id !== `in-nh-${identifier}`
          || resource.kind !== "highways" || resource.state_code !== "IN"
          || resource.pack_version !== 1 || resource.schema_version !== 1
          || !pathMatch || pathMatch[1] !== identifier || pathMatch[2] !== resource.sha256
          || resource.url !== PACK_SITE_ROOT + resource.path
          || !Number.isInteger(resource.bytes) || resource.bytes <= 0
          || resource.bytes > HIGHWAY_TILE_MAX_BYTES
          || !Number.isInteger(resource.feature_count) || resource.feature_count <= 0
          || !Number.isInteger(resource.ref_count) || resource.ref_count <= 0
          || !Array.isArray(resource.bbox) || resource.bbox.length !== 4
          || resource.bbox[0] !== lng || resource.bbox[1] !== lat
          || resource.bbox[2] !== lng + 2 || resource.bbox[3] !== lat + 2) {
        throw new Error(`Invalid National Highway tile resource: ${identifier}`);
      }
    }
    replaceStableObject(NATIONAL_HIGHWAY_AUTHORITY, authority);
    return manifest;
  }

  async function getHighwayPackManifest() {
    if (_highwayManifest) return _highwayManifest;
    if (_highwayManifestPromise) return _highwayManifestPromise;
    _highwayManifestPromise = (async () => {
      try {
        const response = await fetch("highway-manifest.json", { cache: "no-store" });
        if (!response.ok) return null;
        const text = await response.text();
        if (!text || text.length > HIGHWAY_MANIFEST_MAX_BYTES) return null;
        _highwayManifest = validateHighwayManifest(JSON.parse(text));
      } catch (e) { /* missing or malformed highway data fails closed at routing time */ }
      return _highwayManifest;
    })();
    const result = await _highwayManifestPromise;
    _highwayManifestPromise = null;
    return result;
  }

  function validateHighwayTile(resource, bytes) {
    if (!(bytes instanceof ArrayBuffer) || bytes.byteLength !== resource.bytes) {
      throw new Error("National Highway tile byte length does not match its manifest.");
    }
    return sha256Bytes(bytes).then((digest) => {
      if (!digest || digest !== resource.sha256 || !window.TextDecoder) {
        throw new Error("National Highway tile checksum does not match its manifest.");
      }
      const pack = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
      if (!exactObjectKeys(pack,
        ["coordinate_scale", "features", "format", "generated_at", "pack_id", "pack_version",
          "schema_version", "tile_id"])
          || pack.format !== "pothole-national-highway-tile"
          || pack.schema_version !== 1 || pack.pack_version !== 1
          || pack.pack_id !== resource.pack_id || pack.tile_id !== resource.tile_id
          || pack.generated_at !== "2026-08-20" || pack.coordinate_scale !== 100000
          || !Array.isArray(pack.features) || pack.features.length !== resource.feature_count) {
        throw new Error("National Highway tile envelope is invalid.");
      }
      const refs = new Set();
      for (const feature of pack.features) {
        if (!Array.isArray(feature) || feature.length !== 3
            || typeof feature[0] !== "string" || !HIGHWAY_REF_RE.test(feature[0])
            || !Array.isArray(feature[1]) || feature[1].length !== 4
            || feature[1].some((value) => !Number.isInteger(value))
            || feature[1][0] > feature[1][2] || feature[1][1] > feature[1][3]
            || !Array.isArray(feature[2]) || feature[2].length < 4
            || feature[2].length % 2 || feature[2].some((value) =>
              !Number.isInteger(value) || Math.abs(value) > 10000000)) {
          throw new Error("National Highway tile contains an invalid feature.");
        }
        refs.add(feature[0]);
      }
      if (refs.size !== resource.ref_count) {
        throw new Error("National Highway tile reference count is invalid.");
      }
      return pack;
    });
  }

  async function fetchHighwayTile(resource) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), HIGHWAY_FETCH_TIMEOUT_MS);
    try {
      const response = await fetch(resolvePackUrl(resource), {
        cache: "no-store", credentials: "omit", referrerPolicy: "no-referrer",
        signal: controller.signal,
      });
      if (!response.ok) return null;
      const contentType = response.headers.get("content-type") || "";
      if (contentType && !/json/i.test(contentType)) return null;
      const bytes = await response.arrayBuffer();
      return { pack: await validateHighwayTile(resource, bytes), bytes };
    } catch (e) { return null; }
    finally { clearTimeout(timer); }
  }

  async function loadHighwayTile(identifier) {
    if (_highwayTilePromises.has(identifier)) return _highwayTilePromises.get(identifier);
    const task = (async () => {
      const manifest = await getHighwayPackManifest();
      const resource = manifest && manifest.tiles[identifier];
      if (!resource) return null;
      const cacheKey = statePackCacheKey(resource);
      const memory = _highwayTileMemory.get(identifier);
      if (memory && memory.cache_key === cacheKey) return memory.pack;
      let cached = null;
      try { cached = await getCachedStatePack(cacheKey); } catch (e) { /* download below */ }
      if (cached) {
        try {
          const pack = await validateHighwayTile(resource, await cachedPackBytes(cached));
          _highwayTileMemory.set(identifier, { cache_key: cacheKey, pack, resource });
          touchStatePack(cached);
          pruneStatePacks(resource.pack_id);
          return pack;
        } catch (e) {
          try { await deleteCachedStatePack(cacheKey); } catch (_) {}
        }
      }
      const downloaded = await fetchHighwayTile(resource);
      if (!downloaded) return null;
      const now = Date.now();
      const record = {
        cache_key: cacheKey, pack_id: resource.pack_id, pack_version: resource.pack_version,
        state_code: "IN", kind: "highways", sha256: resource.sha256,
        bytes: resource.bytes, installed_at: now, last_used_at: now,
        blob: new Blob([downloaded.bytes], { type: "application/json" }),
      };
      try { await putCachedStatePack(record); }
      catch (e) {
        await pruneStatePacks(resource.pack_id);
        try { await putCachedStatePack(record); } catch (_) { /* valid for this session */ }
      }
      _highwayTileMemory.set(identifier,
        { cache_key: cacheKey, pack: downloaded.pack, resource });
      pruneStatePacks(resource.pack_id);
      return downloaded.pack;
    })();
    _highwayTilePromises.set(identifier, task);
    try { return await task; }
    finally { _highwayTilePromises.delete(identifier); }
  }

  function highwayPackProvenance(identifier) {
    const item = _highwayTileMemory.get(identifier);
    const resource = item && item.resource;
    if (!resource) return {};
    return {
      routing_pack_id: resource.pack_id,
      routing_pack_version: resource.pack_version,
      routing_pack_sha256: resource.sha256,
      routing_pack_state_code: "IN",
    };
  }

  function resetHighwayPackMemory() {
    _highwayManifest = null;
    _highwayManifestPromise = null;
    _highwayTileMemory.clear();
    _highwayTilePromises.clear();
    replaceStableObject(NATIONAL_HIGHWAY_AUTHORITY, {});
  }

  function highwayTileIdFor(lat, lng, tileSize = 2) {
    if (!Number.isFinite(lat) || !Number.isFinite(lng)
        || lat < 0 || lng < 0 || lat >= 100 || lng >= 100) return null;
    const west = Math.floor(lng / tileSize) * tileSize;
    const south = Math.floor(lat / tileSize) * tileSize;
    return `e${String(west).padStart(3, "0")}n${String(south).padStart(2, "0")}`;
  }

  function pointToHighwaySegment(lng, lat, aLng, aLat, bLng, bLat) {
    const rad = Math.PI / 180;
    const metresPerLng = 111320 * Math.cos(lat * rad);
    const ax = (aLng - lng) * metresPerLng, ay = (aLat - lat) * 110540;
    const bx = (bLng - lng) * metresPerLng, by = (bLat - lat) * 110540;
    const dx = bx - ax, dy = by - ay, denom = dx * dx + dy * dy;
    const turn = denom ? Math.max(0, Math.min(1, -(ax * dx + ay * dy) / denom)) : 0;
    const distance = Math.hypot(ax + turn * dx, ay + turn * dy);
    const bearing = ((Math.atan2(dx, dy) * 180 / Math.PI) + 360) % 360;
    return { distance, bearing };
  }

  const undirectedHeadingDifference = (left, right) => {
    const difference = Math.abs(left - right) % 180;
    return Math.min(difference, 180 - difference);
  };

  function matchHighwayTile(pack, manifest, lat, lng, gpsAccuracy, heading, speed) {
    if (!pack || !manifest) return null;
    const policy = manifest.match;
    const accurate = Number.isFinite(gpsAccuracy) && gpsAccuracy >= 0
      && gpsAccuracy <= policy.max_gps_accuracy_m;
    const threshold = accurate
      ? Math.min(policy.max_match_distance_m,
        Math.max(policy.minimum_match_distance_m, gpsAccuracy + 8))
      : policy.minimum_match_distance_m;
    const scale = pack.coordinate_scale;
    const latPad = Math.ceil(policy.max_match_distance_m / 110540 * scale);
    const lngPad = Math.ceil(policy.max_match_distance_m
      / Math.max(20000, 111320 * Math.cos(lat * Math.PI / 180)) * scale);
    const targetLng = Math.round(lng * scale), targetLat = Math.round(lat * scale);
    let best = null, secondDifferent = null;
    for (const feature of pack.features) {
      const ref = feature[0], bbox = feature[1], encoded = feature[2];
      if (targetLng < bbox[0] - lngPad || targetLng > bbox[2] + lngPad
          || targetLat < bbox[1] - latPad || targetLat > bbox[3] + latPad) continue;
      let x = encoded[0], y = encoded[1];
      for (let index = 2; index < encoded.length; index += 2) {
        const nextX = x + encoded[index], nextY = y + encoded[index + 1];
        const segment = pointToHighwaySegment(
          lng, lat, x / scale, y / scale, nextX / scale, nextY / scale);
        const candidate = { ref, distance: segment.distance, bearing: segment.bearing };
        if (!best || candidate.distance < best.distance) {
          if (best && best.ref !== candidate.ref
              && (!secondDifferent || best.distance < secondDifferent.distance)) {
            secondDifferent = best;
          }
          best = candidate;
        } else if (candidate.ref !== best.ref
            && (!secondDifferent || candidate.distance < secondDifferent.distance)) {
          secondDifferent = candidate;
        }
        x = nextX; y = nextY;
      }
    }
    if (!best || best.distance > threshold) return null;
    if (!accurate) return { uncertain: true, match: best };
    if (secondDifferent
        && secondDifferent.distance <= best.distance + Math.max(6, gpsAccuracy)) {
      return { uncertain: true, match: best, alternate: secondDifferent };
    }
    if (Number.isFinite(heading) && Number.isFinite(speed) && speed >= 2
        && undirectedHeadingDifference(heading, best.bearing) > 55) return null;
    return { uncertain: false, match: best };
  }

  async function nationalHighwayRoute(lat, lng, gpsAccuracy, heading, speed) {
    if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null;
    const inIndiaEnvelope = lat >= 5 && lat <= 36 && lng >= 67 && lng <= 98;
    if (!inIndiaEnvelope) return null;
    const manifest = await getHighwayPackManifest();
    if (!manifest) return unroutedRoute("road_class_unknown");
    const identifier = highwayTileIdFor(lat, lng, manifest.match.tile_size_degrees);
    const resource = identifier && manifest.tiles[identifier];
    if (!resource) return null;
    const pack = await loadHighwayTile(identifier);
    if (!pack) return unroutedRoute("road_class_unknown");
    const result = matchHighwayTile(pack, manifest, lat, lng, gpsAccuracy, heading, speed);
    if (!result) return null;
    if (result.uncertain) return unroutedRoute("location_uncertain", result.match.ref);
    const route = authorityRoute(NATIONAL_HIGHWAY_AUTHORITY, {
      routing_source: "osm_national_highway_geometry",
      match_field: "mapped_carriageway",
      match_value: `${result.match.ref} · ${Math.round(result.match.distance)} m`,
      region: "national-highway",
    });
    return {
      ...route,
      highway_ref: result.match.ref,
      ownership_unverified: true,
      tender_eligible: false,
      ...highwayPackProvenance(identifier),
    };
  }

  async function clearPackCache() {
    try { await op("readwrite", (store) => store.clear(), "state_packs"); } catch (e) {}
    resetStatePackMemory();
  }

  let _maharashtraCoverage = null, _maharashtraCoveragePromise = null;
  async function maharashtraCoverage() {
    if (_maharashtraCoverage) return _maharashtraCoverage;
    if (_maharashtraCoveragePromise) return _maharashtraCoveragePromise;
    _maharashtraCoveragePromise = (async () => {
      try {
        const pack = await loadStatePack("in-mh-routing");
        const data = pack && pack.payload;
        if (data && data.version === 2 && data.regions
            && data.regions.mmr && data.regions.pmc && data.regions.maharashtra
            && hasCoverageGeometry(data.regions.mmr.geometry)
            && hasCoverageGeometry(data.regions.pmc.geometry)
            && hasCoverageGeometry(data.regions.maharashtra.geometry)
            && validMmrAuthorityBoundaries(data.regions.mmr)) {
          _maharashtraCoverage = data;
        }
      } catch (e) { /* fail closed; a retry is allowed on the next report */ }
      return _maharashtraCoverage;
    })();
    const result = await _maharashtraCoveragePromise;
    _maharashtraCoveragePromise = null;
    return result;
  }

  const isMaharashtraGeocode = (geo) => !!geo
    && String(geo.country_code || "").toLowerCase() === "in"
    && MUMBAI_STATES.has(normaliseAuthorityValue(geo.state));

  // Wide enough to include the complete state plus a small rejection margin, but narrow
  // enough that unrelated reports do not download Maharashtra's routing pack. The pinned
  // state polygon—not this rectangle or a geocoder label—decides statewide containment.
  const MAHARASHTRA_ROUTING_ENVELOPE = {
    minLat: 15.40, maxLat: 22.20, minLng: 72.40, maxLng: 81.10,
  };
  const MAHARASHTRA_STATE_GEOMETRY_SHA256 =
    "1f5555fede30d19d58ffafabb7d38c8cba0af7b27f7c7129d10480351a0304ce";
  const inMaharashtraRoutingEnvelope = (lat, lng) => Number.isFinite(lat) && Number.isFinite(lng)
    && lat >= MAHARASHTRA_ROUTING_ENVELOPE.minLat
    && lat <= MAHARASHTRA_ROUTING_ENVELOPE.maxLat
    && lng >= MAHARASHTRA_ROUTING_ENVELOPE.minLng
    && lng <= MAHARASHTRA_ROUTING_ENVELOPE.maxLng;
  const isKarnatakaGeocode = (geo) => !!geo
    && String(geo.country_code || "").toLowerCase() === "in"
    && KARNATAKA_STATES.has(normaliseAuthorityValue(geo.state));
  const isKnownNonKarnatakaGeocode = (geo) => !!geo
    && !!String(geo.state || "").trim()
    && !!String(geo.country_code || "").trim()
    && (String(geo.country_code).toLowerCase() !== "in" || !isKarnatakaGeocode(geo));

  // The relevance envelope is intentionally wider than Delhi NCT. A point in nearby
  // Noida, Gurugram, Ghaziabad or Faridabad must get an explicit outside-area result,
  // not fall through to an unrelated state's GIS. Only the pinned polygon can accept.
  const DELHI_ENVELOPE = { minLat: 28.10, maxLat: 29.10, minLng: 76.65, maxLng: 77.65 };
  const DELHI_GEOMETRY_SHA256 = "3462ba68bdbbc1fdebc99403aa9e1f9db5e0b78e30ca138b2d25df7463506ab3";
  const inDelhiEnvelope = (lat, lng) => Number.isFinite(lat) && Number.isFinite(lng)
    && lat >= DELHI_ENVELOPE.minLat && lat <= DELHI_ENVELOPE.maxLat
    && lng >= DELHI_ENVELOPE.minLng && lng <= DELHI_ENVELOPE.maxLng;

  // This coarse rectangle only decides whether to download the West Bengal pack. The
  // checksum-pinned state polygon—not the rectangle or a geocoder label—accepts a point.
  const WEST_BENGAL_ROUTING_ENVELOPE = {
    minLat: 21.40, maxLat: 27.40, minLng: 85.60, maxLng: 90.10,
  };
  // This digest is over JSON.stringify(region.geometry). IDs and a closed ring are not
  // enough: a valid-shaped but wrong polygon could otherwise send Howrah to KMC. Updating
  // the boundary is therefore an explicit code-and-data release, never a silent asset swap.
  const KMC_GEOMETRY_SHA256 = "fa9e157d8cdc8d918dd934a77a5dcde375d3108598412cb8ca3e19ca2d916bf5";
  const WEST_BENGAL_STATE_GEOMETRY_SHA256 =
    "aa4ab13c3064be2e168889f6eb02e87c59e01bc709d36b66bece534dfea23015";
  const isWestBengalGeocode = (geo) => !!geo
    && String(geo.country_code || "").toLowerCase() === "in"
    && WEST_BENGAL_STATES.has(normaliseAuthorityValue(geo.state));
  const inWestBengalRoutingEnvelope = (lat, lng) => Number.isFinite(lat) && Number.isFinite(lng)
    && lat >= WEST_BENGAL_ROUTING_ENVELOPE.minLat
    && lat <= WEST_BENGAL_ROUTING_ENVELOPE.maxLat
    && lng >= WEST_BENGAL_ROUTING_ENVELOPE.minLng
    && lng <= WEST_BENGAL_ROUTING_ENVELOPE.maxLng;

  async function sha256Hex(value) {
    if (!window.TextEncoder) return null;
    const bytes = new TextEncoder().encode(value);
    return sha256Bytes(bytes);
  }

  let _delhiCoverage = null, _delhiCoveragePromise = null;
  async function delhiCoverage() {
    if (_delhiCoverage) return _delhiCoverage;
    if (_delhiCoveragePromise) return _delhiCoveragePromise;
    _delhiCoveragePromise = (async () => {
      try {
        const pack = await loadStatePack("in-dl-routing");
        const data = pack && pack.payload;
        const region = data && data.region;
        const geometryDigest = region && hasCoverageGeometry(region.geometry)
          ? await sha256Hex(JSON.stringify(region.geometry)) : null;
        if (data && data.version === 1 && region
            && region.id === "delhi-nct"
            && region.authority_id === DELHI_PWD_AUTHORITY.id
            && Number(region.osm_relation_id) === 1942586
            && region.geometry_sha256 === DELHI_GEOMETRY_SHA256
            && geometryDigest === DELHI_GEOMETRY_SHA256) {
          _delhiCoverage = data;
        }
      } catch (e) { /* fail closed; a retry is allowed on the next report */ }
      return _delhiCoverage;
    })();
    const result = await _delhiCoveragePromise;
    _delhiCoveragePromise = null;
    return result;
  }

  async function delhiRouteFromGeocode(_geo, lat, lng, gpsAccuracy) {
    if (!Number.isFinite(lat) || !Number.isFinite(lng) || !inDelhiEnvelope(lat, lng)) return null;
    const coverage = await delhiCoverage();
    if (!coverage) return unroutedRoute("jurisdiction_unavailable");

    const geometry = coverage.region.geometry;
    const enforceGpsAccuracy = gpsAccuracy !== undefined;
    if (enforceGpsAccuracy
        && (!Number.isFinite(gpsAccuracy) || gpsAccuracy < 0 || gpsAccuracy > 30)) {
      return unroutedRoute("location_uncertain");
    }
    if (Number.isFinite(gpsAccuracy)
        && geometryBoundaryDistanceMeters(lng, lat, geometry) <= gpsAccuracy) {
      return unroutedRoute("location_uncertain");
    }
    if (!pointInGeometry(lng, lat, geometry)) return unroutedRoute("outside_area");

    return authorityRoute(DELHI_PWD_AUTHORITY, {
      routing_source: "osm_delhi_nct_boundary",
      match_field: "boundary",
      match_value: "OpenStreetMap relation 1942586",
      region: "delhi",
    });
  }

  let _westBengalCoverage = null, _westBengalCoveragePromise = null;
  async function westBengalCoverage() {
    if (_westBengalCoverage) return _westBengalCoverage;
    if (_westBengalCoveragePromise) return _westBengalCoveragePromise;
    _westBengalCoveragePromise = (async () => {
      try {
        const pack = await loadStatePack("in-wb-routing");
        const data = pack && pack.payload;
        const regions = data && data.regions;
        const kmc = regions && regions.kmc;
        const state = regions && regions.west_bengal;
        const kmcDigest = kmc && hasCoverageGeometry(kmc.geometry)
          ? await sha256Hex(JSON.stringify(kmc.geometry)) : null;
        const stateDigest = state && hasCoverageGeometry(state.geometry)
          ? await sha256Hex(JSON.stringify(state.geometry)) : null;
        if (data && data.version === 2 && kmc && state
            && kmc.authority_id === KMC_AUTHORITY.id
            && String(kmc.ulb_code) === "250299"
            && String(kmc.mun_id) === "250299_0000001"
            && kmc.geometry_sha256 === KMC_GEOMETRY_SHA256
            && kmcDigest === KMC_GEOMETRY_SHA256
            && state.authority_id === WEST_BENGAL_STATE_AUTHORITY.id
            && Number(state.source_relation_id) === 1960177
            && state.geometry_sha256 === WEST_BENGAL_STATE_GEOMETRY_SHA256
            && stateDigest === WEST_BENGAL_STATE_GEOMETRY_SHA256) {
          _westBengalCoverage = data;
        }
      } catch (e) { /* fail closed; a retry is allowed on the next report */ }
      return _westBengalCoverage;
    })();
    const result = await _westBengalCoveragePromise;
    _westBengalCoveragePromise = null;
    return result;
  }

  // Kept as a compatibility alias for existing KMC tests and saved-report code.
  const kolkataCoverage = westBengalCoverage;

  async function kolkataRouteFromGeocode(geo, lat, lng, gpsAccuracy) {
    if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null;
    // Coordinates decide which state pack gets a vote. A stale reverse-geocoder label
    // must not let West Bengal block a coordinate-verified Chennai or Maharashtra route.
    if (!inWestBengalRoutingEnvelope(lat, lng)) return null;
    const coverage = await westBengalCoverage();
    if (!coverage) return unroutedRoute("jurisdiction_unavailable");

    const stateGeometry = coverage.regions.west_bengal.geometry;
    const kmcGeometry = coverage.regions.kmc.geometry;
    const inState = pointInGeometry(lng, lat, stateGeometry);
    const inKmc = pointInGeometry(lng, lat, kmcGeometry);
    const enforceGpsAccuracy = gpsAccuracy !== undefined;
    if (enforceGpsAccuracy
        && (!Number.isFinite(gpsAccuracy) || gpsAccuracy < 0 || gpsAccuracy > 30)) {
      return (inState || inKmc) ? unroutedRoute("location_uncertain") : null;
    }
    if (Number.isFinite(gpsAccuracy)) {
      const stateEdgeDistance = geometryBoundaryDistanceMeters(lng, lat, stateGeometry);
      const kmcEdgeDistance = geometryBoundaryDistanceMeters(lng, lat, kmcGeometry);
      if (stateEdgeDistance <= gpsAccuracy || kmcEdgeDistance <= gpsAccuracy) {
        return unroutedRoute("location_uncertain");
      }
    }
    if (!inState) return unroutedRoute("outside_area");

    if (inKmc) {
      return authorityRoute(KMC_AUTHORITY, {
        routing_source: "wb_udma_official_gis",
        match_field: "boundary",
        match_value: "wb_municipal_boundary:250299_0000001",
        region: "kolkata",
      });
    }
    return authorityRoute(WEST_BENGAL_STATE_AUTHORITY, {
      routing_source: "osm_west_bengal_state_boundary",
      match_field: "boundary",
      match_value: "West Bengal (OpenStreetMap relation 1960177)",
      region: "west-bengal",
    });
  }

  // Small relevance envelopes stay in the APK so a Chennai report never downloads the
  // Gujarat pack (and vice versa). The detailed coordinates and contacts remain in the
  // checksum-pinned state pack and are installed only after full validation.
  const MUNICIPAL_CITY_CONFIGS = Object.freeze({
    "in-tn-routing": {
      region_id: "chennai-gcc",
      authority_id: "tn-gcc",
      routing_mode: "boundary",
      routing_source: "osm_gcc_boundary",
      authority_sha256: "07dfd3529ac5b999ed0a93e65f9de6726fc75892ed04a2ea26a11978dab5fd73",
      region_sha256: "9a898af4d0f107587b3948ca9917c27acae471519b9157f79e6e661fe45eef3a",
      state_aliases: ["tamil nadu", "tamilnadu", "தமிழ்நாடு"],
      place_aliases: ["chennai", "madras", "சென்னை"],
      envelope: { min_lng: 80.05, min_lat: 12.75, max_lng: 80.40, max_lat: 13.30 },
    },
    "in-tg-routing": {
      region_id: "hyderabad-cure-2053",
      authority_id: "tg-cure-shared",
      routing_mode: "official_point_query",
      routing_source: "tgrac_cure_2053_point_query",
      authority_sha256: "f532d0edf9021be2de8ec52fdd45ef15ac497ed5f77b3ed479cda4c2574f7109",
      region_sha256: "0ee992fa5fc50c3aeb35900514ddde1e235bf3fa169a0cfaff1ace63580fb4ec",
      state_aliases: ["telangana", "తెలంగాణ"],
      place_aliases: ["hyderabad", "secunderabad", "హైదరాబాద్", "హైదరాబాదు"],
      envelope: { min_lng: 78.15, min_lat: 17.10, max_lng: 78.82, max_lat: 17.72 },
    },
    "in-gj-routing": {
      region_id: "ahmedabad-amc",
      authority_id: "gj-amc",
      routing_mode: "boundary",
      routing_source: "opencity_amc_wards_union",
      authority_sha256: "3367eca455389437650195de677f3e9f7031f75e4894a0cad2ad3ed96de80720",
      region_sha256: "86b08ba11e6ad0fe53a599356d9229fbc5302983dcf8c80e435250f281625b59",
      state_aliases: ["gujarat", "ગુજરાત"],
      place_aliases: ["ahmedabad", "amdavad", "અમદાવાદ", "अहमदाबाद"],
      envelope: { min_lng: 72.40, min_lat: 22.85, max_lng: 72.75, max_lat: 23.20 },
    },
  });
  const municipalCityCoverageCache = new Map();
  const municipalCityCoveragePromises = new Map();

  const pointInEnvelope = (lat, lng, envelope) => Number.isFinite(lat) && Number.isFinite(lng)
    && !!envelope && lng >= envelope.min_lng && lng <= envelope.max_lng
    && lat >= envelope.min_lat && lat <= envelope.max_lat;

  const envelopeGeometry = (envelope) => ({
    type: "Polygon",
    coordinates: [[
      [envelope.min_lng, envelope.min_lat], [envelope.max_lng, envelope.min_lat],
      [envelope.max_lng, envelope.max_lat], [envelope.min_lng, envelope.max_lat],
      [envelope.min_lng, envelope.min_lat],
    ]],
  });

  function indianStateMatches(geo, aliases) {
    if (!geo || normaliseAuthorityValue(geo.country_code) !== "in") return false;
    const state = normaliseAuthorityValue(geo.state);
    return aliases.some((alias) => normaliseAuthorityValue(alias) === state);
  }

  async function municipalCityCoverage(packId) {
    if (!MUNICIPAL_CITY_CONFIGS[packId]) return null;
    if (municipalCityCoverageCache.has(packId)) return municipalCityCoverageCache.get(packId);
    if (municipalCityCoveragePromises.has(packId)) return municipalCityCoveragePromises.get(packId);
    const request = (async () => {
      try {
        const pack = await loadStatePack(packId);
        const payload = pack && pack.payload;
        if (payload && payload.version === 1 && Array.isArray(payload.regions)
            && payload.regions.length) {
          municipalCityCoverageCache.set(packId, payload);
          return payload;
        }
      } catch (e) { /* fail closed and allow a later retry */ }
      return null;
    })();
    municipalCityCoveragePromises.set(packId, request);
    try { return await request; }
    finally { municipalCityCoveragePromises.delete(packId); }
  }

  function structuredPlaceMatch(geo, region) {
    if (!indianStateMatches(geo, region.state_aliases)) return null;
    const aliases = new Set(region.place_aliases.map(normaliseAuthorityValue));
    const supplied = ["city", "municipality"].map((field) => ({
      field, raw: geo[field], value: normaliseAuthorityValue(geo[field]),
    })).filter((item) => !!item.value);
    // Nominatim can return both fields. One exact match must not override another
    // non-empty civic field naming a different place.
    if (!supplied.length || supplied.some((item) => !aliases.has(item.value))) return null;
    return { field: supplied[0].field, value: supplied[0].raw };
  }

  function gpsAccuracyEnvelope(lat, lng, accuracy) {
    // ArcGIS treats a zero-area envelope as empty. One metre is a conservative numeric
    // floor when Android reports zero, while real fixes keep their full stated accuracy.
    const radius = Math.max(1, Number(accuracy));
    const latitudeRadius = radius / 111320;
    const longitudeRadius = radius
      / (111320 * Math.max(0.1, Math.cos(lat * Math.PI / 180)));
    return {
      xmin: lng - longitudeRadius,
      ymin: lat - latitudeRadius,
      xmax: lng + longitudeRadius,
      ymax: lat + latitudeRadius,
      spatialReference: { wkid: 4326 },
    };
  }

  async function officialArcGisCount(query, geometry) {
    // The TGRAC service does not advertise browser CORS. CapacitorHttp patches fetch in
    // the Android shell, so native requests work without weakening the public web app.
    if (!NATIVE) return null;
    const controller = typeof AbortController !== "undefined" ? new AbortController() : null;
    const timer = setTimeout(() => controller && controller.abort(), 12000);
    try {
      const url = new URL(query.query_url);
      url.searchParams.set("where", query.query_where);
      url.searchParams.set("geometry", JSON.stringify(geometry));
      url.searchParams.set("geometryType", query.query_geometry_type);
      url.searchParams.set("inSR", String(query.query_in_sr));
      url.searchParams.set("spatialRel", query.query_spatial_rel);
      url.searchParams.set("returnCountOnly", "true");
      url.searchParams.set("f", "json");
      const response = await fetch(url.toString(), {
        cache: "no-store",
        headers: { Accept: "application/json" },
        ...(controller ? { signal: controller.signal } : {}),
      });
      if (!response.ok) return null;
      const body = await response.json();
      return exactObjectKeys(body, ["count"])
        && Number.isInteger(body.count) && body.count >= 0 && body.count <= 1000
        ? body.count : null;
    } catch (e) {
      return null;
    } finally {
      clearTimeout(timer);
    }
  }

  async function officialPointRegionMatch(region, lat, lng, accuracy) {
    if (!pointInEnvelope(lat, lng, region.envelope)) return { kind: "outside" };
    if (!Number.isFinite(accuracy) || accuracy < 0 || accuracy > 30) {
      return { kind: "uncertain" };
    }
    const geometry = gpsAccuracyEnvelope(lat, lng, accuracy);
    const queries = [
      officialArcGisCount(region, geometry),
      ...region.exclusions.map((exclusion) => officialArcGisCount(exclusion, geometry)),
    ];
    const [coverageCount, ...exclusionCounts] = await Promise.all(queries);
    if (coverageCount === null || exclusionCounts.some((count) => count === null)) {
      return { kind: "unavailable" };
    }
    if (coverageCount === 0) return { kind: "outside" };
    if (coverageCount !== 1) return { kind: "unavailable" };
    if (exclusionCounts.some((count) => count > 0)) return { kind: "excluded" };
    return { kind: "match" };
  }

  async function municipalCityRouteFromGeocode(packId, geo, lat, lng, gpsAccuracy) {
    const config = MUNICIPAL_CITY_CONFIGS[packId];
    if (!config || !Number.isFinite(lat) || !Number.isFinite(lng)) return null;
    const relevant = pointInEnvelope(lat, lng, config.envelope)
      || indianStateMatches(geo, config.state_aliases);
    if (!relevant) return null;
    const coverage = await municipalCityCoverage(packId);
    if (!coverage) return unroutedRoute("jurisdiction_unavailable");

    const enforceGpsAccuracy = gpsAccuracy !== undefined;
    if (enforceGpsAccuracy
        && (!Number.isFinite(gpsAccuracy) || gpsAccuracy < 0 || gpsAccuracy > 30)) {
      return unroutedRoute("location_uncertain");
    }

    const matches = [];
    let touchesBoundary = false;
    for (const region of coverage.regions) {
      if (region.routing_mode === "official_point_query") {
        const result = await officialPointRegionMatch(region, lat, lng, gpsAccuracy);
        if (result.kind === "uncertain") return unroutedRoute("location_uncertain");
        if (result.kind === "unavailable") return unroutedRoute("jurisdiction_unavailable");
        if (result.kind === "excluded") return unroutedRoute("outside_area");
        if (result.kind === "match") {
          matches.push({
            region,
            match_field: "official_accuracy_envelope",
            match_value: region.match_value,
          });
        }
        continue;
      }
      let excluded = false;
      for (const exclusion of region.exclusions) {
        if (Number.isFinite(gpsAccuracy)
            && geometryBoundaryDistanceMeters(lng, lat, envelopeGeometry(exclusion.bbox)) <= gpsAccuracy) {
          touchesBoundary = true;
        }
        if (pointInEnvelope(lat, lng, exclusion.bbox)) excluded = true;
      }
      if (excluded) continue;
      if (region.routing_mode === "boundary") {
        if (Number.isFinite(gpsAccuracy)
            && geometryBoundaryDistanceMeters(lng, lat, region.geometry) <= gpsAccuracy) {
          touchesBoundary = true;
        }
        if (pointInGeometry(lng, lat, region.geometry)) {
          matches.push({ region, match_field: "boundary", match_value: region.match_value });
        }
      } else if (region.routing_mode === "structured_geocode"
          && pointInEnvelope(lat, lng, region.envelope)) {
        const structured = structuredPlaceMatch(geo, region);
        if (structured) {
          matches.push({
            region,
            match_field: "structured_place",
            match_value: `${structured.field}: ${structured.value}`,
          });
        }
      }
    }
    if (touchesBoundary || matches.length > 1) return unroutedRoute("location_uncertain");
    if (!matches.length) return unroutedRoute("outside_area");
    const match = matches[0];
    const authority = OFFICIAL_AUTHORITY_INDEX.get(match.region.authority_id);
    if (!authority) return unroutedRoute("jurisdiction_unavailable");
    return authorityRoute(authority, {
      routing_source: match.region.routing_source,
      match_field: match.match_field,
      match_value: match.match_value,
      region: match.region.id,
      pack_id: packId,
    });
  }

  function matchedMmrAuthorities(geo) {
    if (!isMaharashtraGeocode(geo)) return [];
    const fields = ["city", "town", "municipality", "city_district"];
    const found = new Map();
    for (const field of fields) {
      const value = normaliseAuthorityValue(geo[field]);
      const authority = MMR_ALIAS_INDEX.get(value);
      if (authority && !found.has(authority.id)) found.set(authority.id, { authority, field, value: geo[field] });
    }
    return [...found.values()];
  }

  function containingMmrAuthorities(coverage, lng, lat) {
    const boundaries = coverage && coverage.regions && coverage.regions.mmr
      && coverage.regions.mmr.authority_boundaries;
    if (!boundaries) return [];
    const matches = [];
    for (const [id, boundary] of Object.entries(boundaries)) {
      if (!pointInGeometry(lng, lat, boundary.geometry)) continue;
      const authority = OFFICIAL_AUTHORITY_INDEX.get(id);
      if (authority) matches.push({ authority, boundary });
    }
    return matches;
  }

  function bmcWardFromBoundary(boundary, lng, lat, gpsAccuracy) {
    if (!boundary || !Array.isArray(boundary.wards)) return null;
    const matches = boundary.wards.filter((ward) => pointInGeometry(lng, lat, ward.geometry));
    if (matches.length !== 1) return null;
    if (Number.isFinite(gpsAccuracy)
        && geometryBoundaryDistanceMeters(lng, lat, matches[0].geometry) <= gpsAccuracy) {
      return null;
    }
    return matches[0].code;
  }

  function unroutedRoute(reason, bodyName = null) {
    return { routed: false, unrouted_reason: reason, authority_name: bodyName };
  }

  function authorityRoute(authority, options = {}) {
    const email = authority.officer_email || null;
    const handoff = !email;
    const inferredPackId = authority.id && authority.id.startsWith("mh-") ? "in-mh-routing"
      : authority.id && authority.id.startsWith("wb-") ? "in-wb-routing"
        : authority.id === "dl-pwd-sewa" ? "in-dl-routing" : null;
    return {
      routed: true,
      officer_name: email ? `Civic complaint desk, ${authority.name}`
        : `${authority.handoff_name}, ${authority.name}`,
      officer_email: email,
      authority_id: authority.id,
      authority_name: authority.name,
      authority_registry_version: AUTHORITY_REGISTRY_VERSION,
      delivery_channel: handoff ? "official_handoff" : "email",
      ward_code: options.ward_code || null,
      routing_source: options.routing_source || "openstreetmap_structured",
      routing_match_field: options.match_field || null,
      routing_match_value: options.match_value || null,
      region: options.region || "mmr",
      ownership_unverified: true,
      handoff_name: authority.handoff_name || null,
      handoff_url: authority.handoff_url || null,
      handoff_package: authority.handoff_package || null,
      alternate_handoff_name: authority.alternate_handoff_name || null,
      alternate_handoff_url: authority.alternate_handoff_url || null,
      whatsapp_url: authority.whatsapp_url || null,
      helpline: authority.helpline || null,
      requires_official_reference: handoff,
      tender_eligible: false,
      ...statePackProvenance(options.pack_id || inferredPackId),
    };
  }

  function routeForIssue(route, issueType) {
    const issue = normaliseIssueType(issueType);
    if (!route || !route.routed) return route ? { ...route, issue_type: issue } : route;
    // Contract matching is meaningful only for detected road damage. A civic report must
    // never inherit a road tender merely because it shares the same municipal route.
    const base = {
      ...route,
      issue_type: issue,
      tender_eligible: issue === "road_damage" ? !!route.tender_eligible : false,
    };
    let override = null;
    if (issue !== "road_damage") override = CIVIC_HANDOFF_OVERRIDES[route.authority_id] || null;
    if (issue !== "road_damage"
        && BENGALURU_AUTHORITY_NAMES.has(normaliseAuthorityValue(route.authority_name))) {
      override = BENGALURU_HANDOFF;
    }
    // Every Maharashtra civic issue without a reviewed category-specific route has a
    // neutral statewide grievance handoff. This avoids pretending that a pothole-only
    // or bare municipal URL accepts garbage and manhole complaints while retaining any
    // polygon-selected local body only as context.
    if (issue !== "road_damage" && !override
        && /^mh-/.test(String(route.authority_id || ""))) {
      const stateRoute = CIVIC_HANDOFF_OVERRIDES["mh-statewide-unverified"];
      override = route.authority_id === "mh-mmr-unverified"
        ? CIVIC_HANDOFF_OVERRIDES["mh-mmr-unverified"]
        : { ...stateRoute, authority_name: base.authority_name };
    }
    if (!override) {
      if (issue === "road_damage" || GENERAL_CIVIC_AUTHORITY_IDS.has(route.authority_id)) {
        return base;
      }
      return {
        ...unroutedRoute("unsupported_issue_type", base.authority_name),
        issue_type: issue,
      };
    }
    const authorityName = override.authority_name || base.authority_name;
    return {
      ...base,
      ...override,
      issue_type: issue,
      authority_name: authorityName,
      officer_name: `${override.handoff_name}, ${authorityName}`,
      officer_email: null,
      delivery_channel: "official_handoff",
      requires_official_reference: true,
      tender_eligible: false,
    };
  }

  async function maharashtraRouteFromGeocode(geo, lat, lng, gpsAccuracy) {
    if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null;
    const relevant = inMaharashtraRoutingEnvelope(lat, lng) || isMaharashtraGeocode(geo);
    if (!relevant) return null;
    const coverage = await maharashtraCoverage();
    if (!coverage) {
      return unroutedRoute("jurisdiction_unavailable");
    }
    const inState = pointInGeometry(lng, lat, coverage.regions.maharashtra.geometry);
    const inPmc = pointInGeometry(lng, lat, coverage.regions.pmc.geometry);
    const inMmr = pointInGeometry(lng, lat, coverage.regions.mmr.geometry);
    const enforceGpsAccuracy = gpsAccuracy !== undefined;
    if (enforceGpsAccuracy
        && (!Number.isFinite(gpsAccuracy) || gpsAccuracy < 0 || gpsAccuracy > 30)) {
      return (inState || inPmc || inMmr
        || isMaharashtraGeocode(geo)) ? unroutedRoute("location_uncertain") : null;
    }
    if (Number.isFinite(gpsAccuracy)) {
      const stateEdgeDistance = geometryBoundaryDistanceMeters(
        lng, lat, coverage.regions.maharashtra.geometry);
      const pmcEdgeDistance = geometryBoundaryDistanceMeters(
        lng, lat, coverage.regions.pmc.geometry);
      const mmrEdgeDistance = geometryBoundaryDistanceMeters(
        lng, lat, coverage.regions.mmr.geometry);
      if (stateEdgeDistance <= gpsAccuracy
          || pmcEdgeDistance <= gpsAccuracy || mmrEdgeDistance <= gpsAccuracy) {
        return unroutedRoute("location_uncertain");
      }
    }
    if (!inState) return null;
    if (inPmc) {
      return authorityRoute(PMC_AUTHORITY, {
        routing_source: "pmc_official_gis", match_field: "boundary",
        match_value: "PMC_Boundary", region: "pune",
      });
    }
    if (inMmr) {
      if (Number.isFinite(gpsAccuracy)) {
        const boundaries = Object.values(coverage.regions.mmr.authority_boundaries || {});
        if (boundaries.some((boundary) =>
          geometryBoundaryDistanceMeters(lng, lat, boundary.geometry) <= gpsAccuracy)) {
          return unroutedRoute("location_uncertain");
        }
      }
      const contained = containingMmrAuthorities(coverage, lng, lat);
      if (contained.length === 1) {
        const match = contained[0];
        const ward = match.authority.id === "mh-bmc"
          ? bmcWardFromBoundary(match.boundary, lng, lat, gpsAccuracy) : null;
        const relationIds = Array.isArray(match.boundary.source_relation_ids)
          ? match.boundary.source_relation_ids.join(", ") : "";
        return authorityRoute(match.authority, {
          ward_code: ward, routing_source: "osm_ulb_boundary",
          match_field: "boundary",
          match_value: `${match.boundary.source_name || match.authority.name}${relationIds ? ` (OSM ${relationIds})` : ""}`,
          region: "mmr",
        });
      }
      // Rural MMR, an overlap, and the eight councils without a mapped administrative
      // polygon all go to a neutral state portal. A nearby/postal town name is only a
      // clue; it never selects a recipient by itself.
      const clues = matchedMmrAuthorities(geo);
      return authorityRoute(MMR_FALLBACK_AUTHORITY, {
        routing_source: "mmr_boundary_fallback",
        match_field: contained.length > 1 ? "overlapping_boundaries"
          : clues.length ? "unverified_place_clue" : "boundary",
        match_value: contained.length > 1
          ? contained.map((x) => x.authority.name).join(" / ")
          : clues.length ? clues.map((x) => x.authority.name).join(" / ") : "MMR",
        region: "mmr",
      });
    }
    return authorityRoute(MAHARASHTRA_STATE_AUTHORITY, {
      routing_source: "osm_maharashtra_state_boundary",
      match_field: "boundary",
      match_value: "Maharashtra (OpenStreetMap relation 1950884)",
      region: "maharashtra",
    });
  }

  // Asks the state which body contains this point. Returns null when the point is
  // outside every urban local body, which is the rural case: those roads belong to PWD
  // or the panchayat engineering department, not to a municipality.
  const kgisPoint = (base, lat, lng, fields) => {
    const geometry = encodeURIComponent(JSON.stringify(
      { x: lng, y: lat, spatialReference: { wkid: 4326 } }));
    return `${base}?geometry=${geometry}&geometryType=esriGeometryPoint`
      + `&spatialRel=esriSpatialRelIntersects&outFields=${fields}&returnGeometry=false&f=json`;
  };

  // Three outcomes, and telling them apart is the whole point. A town means a municipal
  // body owns the road. No town but a gram panchayat means rural Karnataka, which belongs
  // to PWD or the panchayat engineering department. Neither means the point is outside
  // Karnataka altogether. An empty features array is a normal 200, not an error.
  // One shared retry for the state GIS. Both callers fail closed on null, so a blip
  // costs a refusal the user can retry, never a wrong answer stated confidently.
  async function retryQuery(url, lat, lng, fields) {
    // Measured against the live service: it answers in 383 to 692 ms most of the time and
    // occasionally takes over seven seconds, and one request in ten timed out at eight.
    // Since this check fails closed, a slow government server was turning into a refusal
    // for roughly one report in ten. Three attempts at twelve seconds costs nothing on the
    // common path and makes that rare.
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        const r = await fetchWithTimeout(kgisPoint(url, lat, lng, fields), {}, 12000);
        if (r.ok) return r;
      } catch (e) { /* fall through to the retry */ }
      if (attempt < 2) await new Promise((res) => setTimeout(res, 300 * (attempt + 1)));
    }
    return null;
  }

  // ArcGIS reports failures as HTTP 200 with an error body and no features array, so a
  // bad query, an unavailable layer or a service error all look identical to "nothing
  // here" if the array is defaulted to empty. On the highway layer that meant the gate
  // failed OPEN and a national highway was addressed to a municipal officer. Returns null
  // when the service did not actually answer.
  const featuresOf = (body) =>
    (body && Array.isArray(body.features) && !body.error) ? body.features : null;

  async function kgisJurisdiction(lat, lng) {
    // Which polygon contains this point answers WHERE the pothole is, not WHO owns the
    // road. A national highway is a line that crosses town boundaries, so containment
    // alone addressed a Commissioner for NHAI's carriageway: measured, 5 of 12 verified
    // NH points in Karnataka routed to a municipal officer. The state's own basemap has
    // the highway network, on the host this already calls, so the two questions are asked
    // together and the answer costs no extra wait.
    const [town, nh] = await Promise.all([
      retryQuery(KGIS_TOWN_URL, lat, lng, "KGISTownName,Town_Type,KGISTownCode,LGD_TownCode"),
      // Exact containment only. A buffer picks up OBJECTID 3059, Bengaluru's MG Road,
      // which this land-cover layer misclassifies as National Highway, and that would
      // start refusing genuine city reports in the densest coverage area.
      //
      // Retried once, because this check fails closed: a momentary blip on the state's
      // server refuses a report the app could have routed, and there are now two calls
      // per report where there used to be one.
      retryQuery(KGIS_NH_URL, lat, lng, "Name"),
    ]);
    if (!town) return { kind: "road_class_unknown" };
    // Fail closed, but not into offline(): that fallback only knows Bengaluru, so a
    // failed highway check there refused every report in the rest of the state and
    // called it "outside Karnataka". An unanswered road-class check is its own outcome.
    if (!nh || !nh.ok) return { kind: "road_class_unknown" };
    const h = featuresOf(await readJson(nh));
    // A missing features array means the service did not answer the question. Reading it
    // as "no highway here" is the same failure as not asking at all.
    if (h === null) return { kind: "road_class_unknown" };
    if (h.length) {
      const road = ((h[0].attributes || {}).Name || "").trim();
      return { kind: "national_highway", name: road || null };
    }
    const t = featuresOf(await readJson(town));
    if (t === null) return { kind: "road_class_unknown" };
    if (t.length) {
      const a = t[0].attributes || {};
      return { kind: "town", name: a.KGISTownName || null,
               type: (a.Town_Type || "").trim().toUpperCase(),
               lgd: a.LGD_TownCode ? String(a.LGD_TownCode) : "" };
    }
    // Electronics City is the one town row with a blank type and no codes, so it lands
    // here as a named body with no LGD: still refused, but by name rather than silently.
    // "Outside Karnataka" is only true when the state actually answered and placed the
    // point in no town and no panchayat. If this query fails, we know nothing, and
    // saying "outside Karnataka" to someone standing on a village road in Magadi is
    // simply a lie. Retried for the same reason the highway check is.
    const gp = await retryQuery(KGIS_GP_URL, lat, lng, "KGISGPName");
    if (!gp) return { kind: "road_class_unknown" };
    const g = featuresOf(await readJson(gp));
    if (g === null) return { kind: "road_class_unknown" };
    const name = g.length && (g[0].attributes || {}).KGISGPName;
    if (name && String(name).trim()) return { kind: "rural", name: String(name).trim() };
    return { kind: "outside_state" };
  }

  // Garbage and manhole complaints belong to the containing civic body regardless of
  // the road class beside them. Querying the highway layer for those categories would
  // wrongly send a waste pile on an NH service road to Rajmargyatra—or refuse it.
  async function kgisCivicJurisdiction(lat, lng) {
    const town = await retryQuery(
      KGIS_TOWN_URL, lat, lng, "KGISTownName,Town_Type,KGISTownCode,LGD_TownCode");
    if (!town) return { kind: "jurisdiction_unavailable" };
    const t = featuresOf(await readJson(town));
    if (t === null) return { kind: "jurisdiction_unavailable" };
    if (t.length) {
      const a = t[0].attributes || {};
      return { kind: "town", name: a.KGISTownName || null,
               type: (a.Town_Type || "").trim().toUpperCase(),
               lgd: a.LGD_TownCode ? String(a.LGD_TownCode) : "" };
    }
    const gp = await retryQuery(KGIS_GP_URL, lat, lng, "KGISGPName");
    if (!gp) return { kind: "jurisdiction_unavailable" };
    const g = featuresOf(await readJson(gp));
    if (g === null) return { kind: "jurisdiction_unavailable" };
    const name = g.length && (g[0].attributes || {}).KGISGPName;
    if (name && String(name).trim()) return { kind: "rural", name: String(name).trim() };
    return { kind: "outside_state" };
  }

  // Resolves one explicit route object. Every path that cannot name a real body returns
  // an unrouted object rather than a plausible-looking guess or a fragile tuple.
  // One GIS answer per location, shared by contract lookup and officer routing. Both
  // need it, and asking twice would double the latency of the one network call on the
  // critical path.
  let _jurKey = null, _jurP = null;
  function jurisdictionOf(lat, lng) {
    const key = `${lat},${lng}`;
    if (_jurKey !== key) { _jurKey = key; _jurP = kgisJurisdiction(lat, lng); }
    return _jurP;
  }

  async function routeOfficer(geoOrAddress, lat, lng, gpsAccuracy, heading, speed, requestedIssueType) {
    const issueType = normaliseIssueType(requestedIssueType);
    if (lat == null || lng == null || Number.isNaN(lat) || Number.isNaN(lng)) {
      return routeForIssue(unroutedRoute("no_location"), issueType);
    }

    const geo = geoOrAddress && typeof geoOrAddress === "object" ? geoOrAddress : null;
    // Road class outranks the containing city. Without this first check, a pothole on an
    // NH passing through Delhi, Kolkata, Chennai, Hyderabad, Ahmedabad, MMR or Pune can
    // be addressed to the municipal body even though the highway has another maintainer.
    const highway = issueType === "road_damage"
      ? await nationalHighwayRoute(lat, lng, gpsAccuracy, heading, speed) : null;
    if (highway) return routeForIssue(highway, issueType);
    const delhi = await delhiRouteFromGeocode(geo, lat, lng, gpsAccuracy);
    if (delhi) return routeForIssue(delhi, issueType);

    const kolkata = await kolkataRouteFromGeocode(geo, lat, lng, gpsAccuracy);
    if (kolkata) return routeForIssue(kolkata, issueType);

    for (const packId of ["in-tn-routing", "in-tg-routing", "in-gj-routing"]) {
      const municipal = await municipalCityRouteFromGeocode(packId, geo, lat, lng, gpsAccuracy);
      if (municipal) return routeForIssue(municipal, issueType);
    }

    const maharashtra = await maharashtraRouteFromGeocode(geo, lat, lng, gpsAccuracy);
    if (maharashtra) return routeForIssue(maharashtra, issueType);
    // A Maharashtra geocode outside the pinned state polygon must not fall through to
    // Karnataka GIS and come back with a misleading state-service failure.
    if (isMaharashtraGeocode(geo)) return routeForIssue(unroutedRoute("outside_area"), issueType);
    // A geocoder-confirmed non-Karnataka state must never be sent to Karnataka GIS.
    // When geocoding failed entirely we still ask KGIS, because its polygon is the
    // authoritative way to distinguish Karnataka from an unsupported location.
    if (isKnownNonKarnatakaGeocode(geo)) return routeForIssue(unroutedRoute("outside_area"), issueType);

    // If the state cannot tell us what this road is, we do not name anyone. The highway
    // check lives inside kgisJurisdiction, so any path that routes without it can address
    // a Commissioner for a carriageway NHAI owns: with the GIS blocked, NH48 at
    // Nelamangala produced a send-ready draft to comm@bbmp.gov.in, because it sits inside
    // the old Bengaluru bounding box that the fallback trusted.
    //
    // Nothing is lost by refusing. Detection itself needs the network, so there is no
    // offline report to route; this path only fires when OpenAI is reachable and the
    // state GIS is not, and in that case the road's owner is genuinely unknown.
    let where;
    try {
      where = issueType === "road_damage"
        ? await jurisdictionOf(lat, lng) : await kgisCivicJurisdiction(lat, lng);
    } catch (e) {
      return routeForIssue(unroutedRoute(
        issueType === "road_damage" ? "road_class_unknown" : "jurisdiction_unavailable"), issueType);
    }

    if (where.kind === "outside_state") return routeForIssue(unroutedRoute("outside_area"), issueType);
    if (where.kind === "jurisdiction_unavailable") {
      return routeForIssue(unroutedRoute("jurisdiction_unavailable"), issueType);
    }
    if (where.kind === "national_highway") {
      return routeForIssue(issueType === "road_damage"
        ? unroutedRoute("national_highway", where.name)
        : unroutedRoute("outside_area", where.name), issueType);
    }
    if (where.kind === "road_class_unknown") return routeForIssue(unroutedRoute("road_class_unknown"), issueType);
    if (where.kind === "rural") return routeForIssue(unroutedRoute("rural_road", where.name), issueType);

    const registry = await bodies();
    if (!registry) return routeForIssue(unroutedRoute("jurisdiction_unavailable", where.name), issueType);
    const entry = where.lgd && registry[where.lgd];
    if (!entry || !entry.email) return routeForIssue(unroutedRoute("no_address_for_body", where.name), issueType);
    const title = entry.officer || OFFICER_TITLES[entry.type || where.type] || "Chief Officer";
    return routeForIssue({
      routed: true,
      officer_name: `${title}, ${entry.name}${entry.short ? ` (${entry.short})` : ""}`,
      officer_email: entry.email,
      authority_id: `ka-lgd-${where.lgd}`,
      authority_name: entry.name,
      authority_registry_version: AUTHORITY_REGISTRY_VERSION,
      delivery_channel: "email",
      ward_code: null,
      routing_source: "kgis",
      routing_match_field: "lgd",
      routing_match_value: where.lgd,
      region: "karnataka",
      ownership_unverified: false,
      requires_official_reference: false,
      tender_eligible: true,
      ...statePackProvenance("in-ka-routing"),
    }, issueType);
  }

  function distMeters(lat1, lng1, lat2, lng2) {
    const rad = Math.PI / 180;
    const dLat = (lat2 - lat1) * rad, dLng = (lng2 - lng1) * rad;
    const a = Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(dLng / 2) ** 2;
    return 2 * 6371000 * Math.asin(Math.sqrt(a));
  }

  const finiteCoord = (v) => typeof v === "number" && Number.isFinite(v);
  const acceptedReport = (r) => !!r && (r.decision === "accept" || ACCEPTED_REPORT_STATUSES.has(r.status));
  const storedDamageType = (r) => r && (r.damage_type || (r.is_pothole ? "pothole_cavity" : null));
  const localDamageFamily = new Set(["pothole_cavity", "failed_patch"]);
  const compatibleDamage = (a, b) => {
    const left = storedDamageType(a), right = storedDamageType(b);
    return !!left && (left === right || (localDamageFamily.has(left) && localDamageFamily.has(right)));
  };
  const sizeConflict = (a, b) => !!a.size && !!b.size
    && ((a.size === "small" && b.size === "large") || (a.size === "large" && b.size === "small"));
  const eventTime = (r) => Number.isFinite(r.last_seen_at) ? r.last_seen_at
    : Number.isFinite(r.captured_at) ? r.captured_at : r.created_at;
  const headingDifference = (a, b) => {
    const d = Math.abs(a - b) % 360;
    return Math.min(d, 360 - d);
  };
  const eventSighting = (r) => ({
    drive_id: r.drive_id == null ? null : String(r.drive_id),
    lat: finiteCoord(r.lat) ? r.lat : null,
    lng: finiteCoord(r.lng) ? r.lng : null,
    source_offset_s: Number.isFinite(r.source_offset_s) ? r.source_offset_s : null,
    captured_at: Number.isFinite(r.captured_at) ? r.captured_at : null,
    gps_accuracy: Number.isFinite(r.gps_accuracy) ? r.gps_accuracy : null,
    speed_mps: Number.isFinite(r.speed_mps) ? r.speed_mps : null,
    heading: Number.isFinite(r.heading) ? r.heading : null,
    source_event_key: r.source_event_key || null,
  });
  const storedSightings = (r) => Array.isArray(r.event_sightings) && r.event_sightings.length
    ? r.event_sightings.map((seen) => ({ ...seen,
        drive_id: seen.drive_id == null && r.drive_id != null ? String(r.drive_id) : seen.drive_id }))
    : [eventSighting(r)];

  function matchesEverySameDriveSighting(candidate, sightings) {
    // A normal 4-second cluster contains only a handful of samples. If corrupted or
    // imported data exceeds this bound, save a separate event instead of dropping one.
    if (sightings.length > 64) return false;
    return sightings.every((seen) => {
      const delta = (a, b) => Number.isFinite(a) && Number.isFinite(b) ? Math.abs(a - b) : Infinity;
      const seconds = Math.min(delta(candidate.source_offset_s, seen.source_offset_s),
                               delta(candidate.captured_at, seen.captured_at));
      if (!Number.isFinite(seconds)) return false;
      const positioned = finiteCoord(candidate.lat) && finiteCoord(candidate.lng)
        && finiteCoord(seen.lat) && finiteCoord(seen.lng);
      const accuracyPoor = !Number.isFinite(candidate.gps_accuracy) || !Number.isFinite(seen.gps_accuracy)
        || candidate.gps_accuracy > 30 || seen.gps_accuracy > 30;
      if (!positioned || accuracyPoor) return seconds <= DEDUPE_POOR_GPS_S;
      const distance = distMeters(candidate.lat, candidate.lng, seen.lat, seen.lng);
      const stationary = Number.isFinite(candidate.speed_mps) && Number.isFinite(seen.speed_mps)
        && candidate.speed_mps <= 1 && seen.speed_mps <= 1;
      return stationary
        ? seconds <= 30 && distance <= 5
        : seconds <= DEDUPE_SAME_DRIVE_S && distance <= DEDUPE_ADJACENT_RADIUS_M;
    });
  }

  // A GPS match works across drives and app restarts. The time match is deliberately
  // limited to one drive: it recovers recorded footage with no GPS, but cannot merge two
  // unrelated manual reports merely because they were processed at the same time.
  function roadEventMatch(candidate, prior) {
    if (!candidate.dedupe_eligible || !acceptedReport(prior)
        || prior.debug_capture || prior.dedupe_eligible === false) return null;
    // A fresh routable complaint is more useful than an old accepted observation that
    // could not name an authority. Never hide the sendable one behind the unrouted one.
    if (candidate.status === "draft" && prior.status === "unrouted") return null;
    const keys = Array.isArray(prior.source_event_keys) ? prior.source_event_keys : [];
    if (candidate.source_event_key
        && (prior.source_event_key === candidate.source_event_key || keys.includes(candidate.source_event_key))) {
      return { kind: "same_source" };
    }
    // A manual photo is an explicit user action. Do not silently swallow it based on
    // approximate phone GPS; automatic Drive/VOD observations are the duplicate source.
    if (isManualCaptureSource(candidate.capture_source)) return null;
    if (!compatibleDamage(candidate, prior) || sizeConflict(candidate, prior)) return null;
    const positioned = finiteCoord(candidate.lat) && finiteCoord(candidate.lng)
      && finiteCoord(prior.lat) && finiteCoord(prior.lng);
    const distance = positioned
      ? distMeters(candidate.lat, candidate.lng, prior.lat, prior.lng) : Infinity;
    const candidateDrive = candidate.drive_id == null ? null : String(candidate.drive_id);
    const sameDriveSightings = candidateDrive == null ? []
      : storedSightings(prior).filter((seen) => seen.drive_id != null
          && String(seen.drive_id) === candidateDrive);
    if (sameDriveSightings.length) return matchesEverySameDriveSighting(candidate, sameDriveSightings)
      ? { kind: "same_drive" } : null;

    // A repeat on another drive is less certain: require recent, precise GPS, compatible
    // scale and subtype, and travel direction when the phone supplied it. Missing heading
    // is allowed only at a tighter radius so existing v1.12 reports still protect users.
    if (!positioned || !Number.isFinite(candidate.gps_accuracy) || !Number.isFinite(prior.gps_accuracy)
        || candidate.gps_accuracy > 15 || prior.gps_accuracy > 15) return null;
    const age = Math.abs((eventTime(candidate) || 0) - (eventTime(prior) || 0));
    if (!Number.isFinite(age) || age > DEDUPE_HISTORY_S) return null;
    const left = storedDamageType(candidate), right = storedDamageType(prior);
    if (left === "other_road_damage" || right === "other_road_damage") return null;
    let radius = left === right ? DEDUPE_HISTORY_RADIUS_M : 5;
    const moving = Number.isFinite(candidate.speed_mps) && Number.isFinite(prior.speed_mps)
      && candidate.speed_mps >= 2 && prior.speed_mps >= 2;
    const headingsKnown = Number.isFinite(candidate.heading) && Number.isFinite(prior.heading);
    if (moving && headingsKnown) {
      if (headingDifference(candidate.heading, prior.heading) > 45) return null;
    } else {
      radius = Math.min(radius, DEDUPE_MISSING_HEADING_RADIUS_M);
    }
    return distance <= radius ? { kind: "prior_drive" } : null;
  }

  const sameRoadEvent = (candidate, prior) => !!roadEventMatch(candidate, prior);

  function findDuplicateReport(candidate, reports) {
    for (let i = reports.length - 1; i >= 0; i--) {
      if (sameRoadEvent(candidate, reports[i])) return reports[i];
    }
    return null;
  }

  // ---------- tenders ----------
  let _tenders = null;
  // The optional pack contains only rows with a verified civic-body ID: the other 28,706
  // procurement rows could never enter the matcher. Failure omits contract context but
  // never blocks a valid road-damage report.
  async function tenders() {
    if (_tenders) return _tenders;
    try {
      const pack = await loadStatePack("in-ka-tenders");
      const loaded = pack && pack.tenders;
      if (Array.isArray(loaded) && loaded.length) { _tenders = loaded; return _tenders; }
    } catch (e) { /* fall through and retry on the next call */ }
    return [];
  }

  // Contracts grouped by the body that awarded them, built once. Each municipal row
  // carries the LGD code of its body (stamped by tools/index-tenders.py), and the state
  // GIS gives the same code for the pothole, so picking the right shortlist is a lookup
  // rather than a scan of every municipal contract in Karnataka.
  let _byBody = null;
  async function tendersFor(lgd) {
    if (!_byBody) {
      // Built from whatever tenders() returned, and only kept if that was a real load.
      // Caching an index built from a failed read repeats the same fault one level up.
      const index = new Map();
      for (const t of await tenders()) {
        if (!t.b) continue;
        let list = index.get(t.b);
        if (!list) { list = []; index.set(t.b, list); }
        list.push(t);
      }
      if (!index.size) return [];
      _byBody = index;
    }
    if (!lgd) return [];
    const own = _byBody.get(String(lgd)) || [];
    // Bengaluru's five corporations replaced BBMP in 2025 and inherited its works, which
    // the award records still file under BBMP zones. Zone to corporation is not
    // published, so all five share that legacy pool.
    const legacy = BLR_BODIES.has(String(lgd)) ? (_byBody.get("BLR") || []) : [];
    return legacy.length ? own.concat(legacy) : own;
  }


  // The five corporations that replaced BBMP in 2025 and share its legacy contract pool.
  const BLR_BODIES = new Set(["305850", "305851", "305852", "305853", "305854"]);

  const TENDER_STOP = new Set(["road", "roads", "street", "cross", "main", "layout", "bengaluru", "bangalore",
    "karnataka", "india", "ward", "city", "corporation", "south", "north", "east",
    "west", "central", "urban", "sector", "stage", "block", "phase"]);

  // Award records carry no defect liability period, so it is inferred from how recent the
  // tender is and must stay worded as a possibility. Pulled out of matchTender so it can be
  // tested directly: it decides a sentence in a letter naming a private company.
  function warrantyFor(published, now) {
    const dm = /^(\d{2})-(\d{2})-(\d{4})$/.exec(String(published || "").trim());
    if (!dm) return { warranty: "recorded for this stretch", warranty_code: "record" };
    const when = Date.UTC(+dm[3], +dm[2] - 1, +dm[1]);
    if (!isFinite(when) || +dm[2] < 1 || +dm[2] > 12 || +dm[1] < 1 || +dm[1] > 31) {
      return { warranty: "recorded for this stretch", warranty_code: "record" };
    }
    const ageYears = ((now === undefined ? Date.now() : now) - when) / (365.25 * 24 * 3600 * 1000);
    if (ageYears < 0) return { warranty: "recorded for this stretch", warranty_code: "record" };
    if (ageYears <= 1) return { warranty: "within the defect liability period", warranty_code: "dlp" };
    if (ageYears <= 3) return { warranty: "within the maintenance period", warranty_code: "maint" };
    return { warranty: "recorded for this stretch", warranty_code: "record" };
  }

  // The ranked candidate list, split out from matchTender so it can be tested on its own.
  // It is entirely local and must be deterministic: the same address and body must give
  // the same list in the same order every time, or the app cannot justify the contract it
  // eventually prints in a letter naming a real company.
  async function shortlistFor(address, lgd) {
    if (!address || !lgd) return [];
    const tokens = new Set();
    for (const part of address.split(",").slice(0, 4)) {
      for (const w of part.trim().toLowerCase().replace(/[()]/g, " ").split(/\s+/)) {
        if (w.length > 2 && !TENDER_STOP.has(w)) tokens.add(w);
      }
    }
    if (!tokens.size) return [];
    const pool = await tendersFor(lgd);
    if (!pool.length) return [];

    // Scored on the work description alone. The location field is the body's own name,
    // identical in every one of its rows, so it cannot tell one of the body's roads from
    // another: including it only added the town's name to every candidate equally.
    const hays = pool.map((t) => (t.t || "").toLowerCase());

    // The body's own name is not evidence about which of its roads this is, and it turns
    // up in some work titles as well as in every location, so counting alone will not
    // remove it. Krishnamurtipuram, Mysuru matched a Mysuru water-supply contract purely
    // on the word Mysuru.
    const bodyWords = new Set();
    for (const w of (pool[0].loc || "").toLowerCase().split(/[^a-z]+/)) {
      if (w.length > 2) bodyWords.add(w);
    }
    for (const w of bodyWords) tokens.delete(w);
    if (!tokens.size) return [];

    const idf = new Map();
    for (const tok of tokens) {
      let df = 0;
      for (const hay of hays) if (hay.includes(tok)) df++;
      // A word in none of this body's contracts is no evidence, and a word in every one
      // of them cannot distinguish one road from another. The "more than half" cut only
      // means something once there are enough contracts to count: a town with three had
      // every matching word exceed half, so it could never match anything at all.
      if (df === 0) continue;
      if (pool.length > 1 && df === pool.length) continue;
      if (pool.length >= 8 && df > pool.length * 0.5) continue;
      idf.set(tok, Math.log((pool.length + 1) / (df + 0.5)));
    }
    if (!idf.size) return [];

    const scored = [];
    for (let i = 0; i < pool.length; i++) {
      let score = 0;
      for (const [tok, w] of idf) if (hays[i].includes(tok)) score += w;
      if (score > 0) scored.push({ score, t: pool[i] });
    }
    // Deterministic all the way down. Scores tie often, because a locality word may be the
    // only thing that matched and every ward contract for that locality then scores the
    // same: measured, all ten HSR Layout candidates tied at exactly 5.756. Without an
    // explicit order the list arrived differently on different runs and the same pothole
    // was reported under different contracts. Ties break by most recent first, since a
    // newer award is likelier to still carry an obligation, then by tender number.
    const stamp = (t) => {
      const m = /^(\d{2})-(\d{2})-(\d{4})$/.exec(String(t.d || "").trim());
      return m ? Date.UTC(+m[3], +m[2] - 1, +m[1]) : 0;
    };
    scored.sort((a, b) =>
      (b.score - a.score) || (stamp(b.t) - stamp(a.t)) || String(a.t.tn).localeCompare(String(b.t.tn)));
    return scored.slice(0, 25).map((x) => ({ score: x.score, tn: x.t.tn, t: x.t }));
  }

  async function matchTender(address, lgd) {
    if (!address || !S.key || !lgd) return null;
    // Only this body's own contracts are candidates. That is what makes naming one safe:
    // the officer receiving the letter awarded the work. It also rules out a contract from
    // a different town whose road name happened to match, and state PWD, panchayat and
    // irrigation contracts, which a municipal officer has no standing over.
    const ranked = await shortlistFor(address, lgd);
    if (!ranked.length) return null;
    const candidates = ranked.map((x) => x.t);
    const listing = candidates.map((t, i) =>
      `${i}: ${t.t.slice(0, 150)} | ${t.loc} | contractor: ${t.c || "not named"} | published: ${t.d}`).join("\n");
    const prompt = `You match a reported road defect's location to road-work contracts awarded by the
local body that owns this road. Every candidate below was awarded by that same body, so
the town is already correct and your only job is whether the work covers this stretch.
The road defect's reverse-geocoded address is:
${address}

Candidate contracts (index: work description | division | contractor | published):
${listing}

Pick the single contract whose work description covers this exact road stretch or
its immediate locality (same layout, ward or named road). Road names repeat across
localities within a town, so the locality or ward context must agree, not just the
road name. A ward-wide maintenance or pothole-filling contract for the pothole's own
locality or ward is a valid match. A ward-wide maintenance or pothole-filling contract for the pothole's
own layout or ward is a valid match. If no candidate clearly covers this location,
match_index must be null. confidence is your 0 to 1 confidence in the match.`;
    let m;
    try {
      // Minimal effort suits a verdict on one photo. Picking one contract out of 25
      // near-identical road-works descriptions is the opposite job, and it names a
      // real contractor in a complaint, so this call keeps room to think.
      m = await oai({
        model: DEFAULT_MODEL, input: prompt,
        reasoning: { effort: "medium" },
        text: fmt("tender_match", TENDER_SCHEMA),
      });
    } catch (e) { return null; }
    if (!m || m.match_index === null || m.match_index < 0 || m.match_index >= candidates.length || m.confidence < 0.6) return null;
    const t = candidates[m.match_index];
    const { warranty, warranty_code } = warrantyFor(t.d);
    // Records without a winner are common in this dataset. Naming nobody is correct;
    // a placeholder sentence read as a person's name in the Kannada draft.
    const contractor = t.c || null;
    return {
      tender_number: t.tn, contractor, title: t.t, published: t.d, warranty, warranty_code,
      note: contractor
        ? `Probable contract: ${t.tn}, ${contractor}, published ${t.d}`
        : `Probable contract: ${t.tn}, contractor not listed, published ${t.d}`,
      ...statePackProvenance("in-ka-tenders", "tender"),
    };
  }

  // ---------- drafting (English / Kannada / Marathi / Bengali) ----------
  function damageTypeOf(value) {
    if (value && value.damage_type) return value.damage_type;
    return value && value.is_pothole ? "pothole_cavity" : "none";
  }

  function assessmentOf(value) {
    if (value && value.assessment) return value.assessment;
    if (value && value.is_pothole) return "clear";
    return "absent";
  }

  function draftEmail(a, lat, lng, address, officerName, tender, route = null) {
    const lang = LANG(), kn = lang === "kn", mr = lang === "mr", bn = lang === "bn";
    const sender = S.name === "A concerned citizen"
      ? (kn ? "ಕಾಳಜಿಯುಳ್ಳ ನಾಗರಿಕ" : mr ? "एक जागरूक नागरिक" : bn ? "একজন সচেতন নাগরিক" : S.name)
      : S.name;
    const sizeNames = kn
      ? { small: "ಸಣ್ಣ", medium: "ಮಧ್ಯಮ", large: "ದೊಡ್ಡ" }
      : mr ? { small: "लहान", medium: "मध्यम", large: "मोठा" }
        : bn ? { small: "ছোট", medium: "মাঝারি", large: "বড়" } : null;
    const sizeName = (s) => (sizeNames && sizeNames[s]) || s;
    const size = a.size ? sizeName(a.size)
      : (kn ? "ಗಾತ್ರ ನಿರ್ಧರಿಸದ" : mr ? "आकार ठरलेला नाही" : bn ? "আকার নির্ধারণ করা যায়নি" : "unclassified");
    const road = address ? address.split(",")[0].trim() : null;
    const type = damageTypeOf(a);
    const typeNames = kn ? {
      pothole_cavity: "ರಸ್ತೆ ಗುಂಡಿ", failed_patch: "ವಿಫಲವಾದ ರಸ್ತೆ ದುರಸ್ತಿ",
      surface_breakup: "ಹಾಳಾದ ರಸ್ತೆ ಮೇಲ್ಮೈ", rut_or_depression: "ರಸ್ತೆ ಕುಸಿತ",
      other_road_damage: "ರಸ್ತೆ ಹಾನಿ", none: "ರಸ್ತೆ ಹಾನಿ",
    } : mr ? {
      pothole_cavity: "खड्डा", failed_patch: "निकामी झालेली रस्ता दुरुस्ती",
      surface_breakup: "तुटलेला रस्त्याचा पृष्ठभाग", rut_or_depression: "रस्त्यातील खोलगट भाग",
      other_road_damage: "रस्त्याचे नुकसान", none: "रस्त्याचे नुकसान",
    } : bn ? {
      pothole_cavity: "রাস্তার গর্ত", failed_patch: "ভেঙে যাওয়া রাস্তা মেরামত",
      surface_breakup: "ভাঙা রাস্তার উপরিভাগ", rut_or_depression: "চাকার খাঁজ বা দেবে যাওয়া অংশ",
      other_road_damage: "রাস্তার অন্যান্য ক্ষতি", none: "রাস্তার ক্ষতি",
    } : {
      pothole_cavity: "pothole", failed_patch: "failed road repair",
      surface_breakup: "broken road surface", rut_or_depression: "road rut or depression",
      other_road_damage: "road damage", none: "road damage",
    };
    const typeName = typeNames[type] || typeNames.other_road_damage;

    let locLines;
    if (lat != null) {
      const la = lat.toFixed(6), ln = lng.toFixed(6);
      locLines = kn
        ? `ಸ್ಥಳ: ${address || "ಕೆಳಗಿನ ನಿರ್ದೇಶಾಂಕ ನೋಡಿ"}\nನಿರ್ದೇಶಾಂಕಗಳು: ${la}, ${ln}\nನಕ್ಷೆ ಲಿಂಕ್: https://maps.google.com/?q=${la},${ln}`
        : mr
          ? `ठिकाण: ${address || "खालील निर्देशांक पहा"}\nनिर्देशांक: ${la}, ${ln}\nनकाशा: https://maps.google.com/?q=${la},${ln}`
          : bn
            ? `স্থান: ${address || "নীচের স্থানাঙ্ক দেখুন"}\nস্থানাঙ্ক: ${la}, ${ln}\nমানচিত্রের লিঙ্ক: https://maps.google.com/?q=${la},${ln}`
          : `Location: ${address || "see coordinates below"}\nCoordinates: ${la}, ${ln}\nMap link: https://maps.google.com/?q=${la},${ln}`;
    } else {
      locLines = kn
        ? "ಸ್ಥಳ: ಸ್ವಯಂಚಾಲಿತವಾಗಿ ನಿರ್ಧರಿಸಲಾಗಲಿಲ್ಲ. ದಯವಿಟ್ಟು ಲಗತ್ತಿಸಿದ ಫೋಟೋ ನೋಡಿ."
        : mr
          ? "ठिकाण: आपोआप निश्चित करता आले नाही. कृपया जोडलेला फोटो पहा."
          : bn
            ? "স্থান: স্বয়ংক্রিয়ভাবে নির্ধারণ করা যায়নি। চেনার জন্য সংযুক্ত ছবিটি দেখুন।"
          : "Location: could not be determined automatically. Please see the attached photo for landmarks.";
    }

    const subject = kn
      ? `${typeName} ದೂರು` + (type === "pothole_cavity" ? `: ${size}` : "") + (road ? ` (${road})` : "")
      : mr
        ? `${typeName} तक्रार` + (type === "pothole_cavity" ? `: ${size}` : "") + (road ? ` (${road})` : "")
      : bn
        ? `${type === "pothole_cavity" ? `রাস্তার গর্ত মেরামতের অভিযোগ: ${size}`
            : type === "failed_patch" ? "ভেঙে যাওয়া রাস্তা মেরামতের অভিযোগ"
            : type === "surface_breakup" ? "ভাঙা রাস্তার উপরিভাগ মেরামতের অভিযোগ"
            : type === "rut_or_depression" ? "রাস্তা দেবে যাওয়া বা চাকার খাঁজের অভিযোগ"
            : "রাস্তার ক্ষতি মেরামতের অভিযোগ"}` + (road ? ` — ${road}` : "")
      : `${type === "pothole_cavity" ? `Pothole complaint: ${size} pothole`
          : type === "failed_patch" ? "Broken road repair complaint"
          : type === "surface_breakup" ? "Road surface failure complaint"
          : type === "rut_or_depression" ? "Road depression complaint"
          : "Road damage complaint"}` + (road ? ` near ${road}` : "");

    // The AI's own description of the photo used to be pasted in as a "Details:" line.
    // The photo is attached and the officer can see it, so the sentence added length
    // without adding information. Same reasoning for dropping the mention of filing on
    // Sahaaya: an officer reading this does not need to be told about a parallel filing.
    const paras = kn
      ? [
          `ಮಾನ್ಯ ${officerName || "ಅಧಿಕಾರಿಗಳೇ"} ಅವರಿಗೆ,`,
          `ದುರಸ್ತಿ ಅಗತ್ಯವಿರುವ ${typeName} ಬಗ್ಗೆ ದೂರು ಸಲ್ಲಿಸುತ್ತಿದ್ದೇನೆ.`,
          `${locLines}\nಹಾನಿಯ ಪ್ರಕಾರ: ${typeName}${a.size ? `\nಅಂದಾಜು ಗಾತ್ರ: ${size}` : ""}`,
          "ಫೋಟೋ ಲಗತ್ತಿಸಲಾಗಿದೆ. ಈ ರಸ್ತೆ ಹಾನಿ ದ್ವಿಚಕ್ರ ವಾಹನ ಸವಾರರಿಗೆ ಮತ್ತು ಇತರ ರಸ್ತೆ ಬಳಕೆದಾರರಿಗೆ ಅಪಾಯಕಾರಿ. ಇದನ್ನು ಶೀಘ್ರ ಪರಿಶೀಲಿಸಿ ದುರಸ್ತಿ ಮಾಡಬೇಕೆಂದು, ಮತ್ತು ಈ ರಸ್ತೆ ಭಾಗ ನಿರ್ವಹಣಾ ವಾರಂಟಿ ಅಡಿಯಲ್ಲಿದ್ದರೆ ಜವಾಬ್ದಾರ ಗುತ್ತಿಗೆದಾರರಿಗೆ ವರ್ಗಾಯಿಸಬೇಕೆಂದು ವಿನಂತಿಸುತ್ತೇನೆ.",
        ]
      : mr
        ? [
            `प्रति ${officerName || "संबंधित अधिकारी"},`,
            `दुरुस्ती आवश्यक असलेला ${typeName} नोंदवत आहे.`,
            `${locLines}\nनुकसानीचा प्रकार: ${typeName}${a.size ? `\nअंदाजे आकार: ${size}` : ""}`,
            "फोटो जोडला आहे. या नुकसानीमुळे दुचाकीस्वार आणि इतर रस्ता वापरणाऱ्यांना धोका होऊ शकतो. कृपया तपासणी करून लवकरात लवकर दुरुस्ती करावी आणि लागू असल्यास जबाबदार कंत्राटदाराकडे पाठवावे.",
          ]
      : bn
        ? [
            `মাননীয় ${officerName || "সংশ্লিষ্ট আধিকারিক"},`,
            `${typeName} মেরামতের জন্য এই অভিযোগ জানাচ্ছি।`,
            `${locLines}\nক্ষতির ধরন: ${typeName}${a.size ? `\nআনুমানিক আকার: ${size}` : ""}`,
            "ছবি সংযুক্ত করা হল। রাস্তার এই ক্ষতি বিশেষ করে দু’চাকার যানচালক ও অন্যান্য পথ ব্যবহারকারীর জন্য বিপজ্জনক। অনুগ্রহ করে দ্রুত স্থানটি পরিদর্শন করে মেরামতের ব্যবস্থা করুন।",
          ]
      : [
          `Dear ${officerName || "Sir or Madam"},`,
          `I would like to report a ${typeName} that needs repair.`,
          `${locLines}\nDamage type: ${typeName}${a.size ? `\nApproximate size: ${size}` : ""}`,
          "PFA image. This road damage poses a danger to two wheeler riders and other road users. I request your office to inspect and repair it at the earliest, and to route it to the contractor responsible if this road section is still under a maintenance warranty.",
        ];

    if (route && route.ownership_unverified) {
      const ward = route.ward_code;
      if (route.authority_id === "in-national-highway") {
        const highway = route.highway_ref || "National Highway";
        if (kn) {
          paras.push(`ನಕ್ಷೆಯ ಪ್ರಕಾರ ರಸ್ತೆ ಉಲ್ಲೇಖ: ${highway}. ನಿರ್ವಹಣಾ ಸಂಸ್ಥೆಯನ್ನು ಈ ಆ್ಯಪ್ ದೃಢಪಡಿಸಿಲ್ಲ. ಸಾಕ್ಷ್ಯವನ್ನು ಪರಿಶೀಲಿಸಿ ರಾಜಮಾರ್ಗಯಾತ್ರಾ ಅಥವಾ 1033 ಮೂಲಕ ನೀವೇ ದೂರು ದಾಖಲಿಸಿ; ಅಗತ್ಯವಿದ್ದರೆ ಸರಿಯಾದ NHAI, NHIDCL, BRO ಅಥವಾ ರಾಜ್ಯ PWD ಘಟಕಕ್ಕೆ ವರ್ಗಾಯಿಸಲು ಕೇಳಿ.`);
        } else if (mr) {
          paras.push(`नकाशावरील रस्ता संदर्भ: ${highway}. देखभाल करणारी संस्था या अॅपने पडताळलेली नाही. पुरावा तपासून राजमार्गयात्रा किंवा 1033 द्वारे स्वतः तक्रार नोंदवा आणि आवश्यक असल्यास योग्य NHAI, NHIDCL, BRO किंवा राज्य PWD विभागाकडे पाठवण्याची विनंती करा.`);
        } else if (bn) {
          paras.push(`মানচিত্রে রাস্তার পরিচয়: ${highway}। রক্ষণাবেক্ষণকারী সংস্থা এই অ্যাপ যাচাই করেনি। প্রমাণ দেখে রাজমার্গযাত্রা বা ১০৩৩-এর মাধ্যমে নিজে অভিযোগ নথিভুক্ত করুন এবং প্রয়োজনে সঠিক NHAI, NHIDCL, BRO বা রাজ্য PWD দপ্তরে পাঠাতে বলুন।`);
        } else {
          paras.push(`Mapped road reference: ${highway}. This app has not verified the maintaining agency. Review the evidence and submit it yourself through Rajmargyatra or 1033; ask for transfer to the correct NHAI, NHIDCL, BRO or State PWD unit when necessary.`);
        }
      } else if (route.authority_id === "wb-statewide-unverified") {
        if (bn) {
          paras.push("স্থানটি পিন-করা OpenStreetMap পশ্চিমবঙ্গ সীমানার ভিতরে মানচিত্রভুক্ত, কিন্তু দায়িত্বপ্রাপ্ত জেলা, দপ্তর বা রাস্তার মালিক চিহ্নিত করা হয়নি। এই স্বাধীন অ্যাপটি অভিযোগ জমা দেয় না; প্রমাণ যাচাই করে West Bengal PGRS-এ দায়িত্বপ্রাপ্ত জেলা বা দপ্তর নিজে নির্বাচন ও যাচাই করুন এবং অভিযোগ নম্বরটি সংরক্ষণ করুন।");
        } else if (kn) {
          paras.push("ಸ್ಥಳವು ಪಿನ್ ಮಾಡಿದ OpenStreetMap ಪಶ್ಚಿಮ ಬಂಗಾಳ ಗಡಿಯೊಳಗೆ ನಕ್ಷೆಗೊಂಡಿದೆ; ಆದರೆ ಜವಾಬ್ದಾರ ಜಿಲ್ಲೆ, ಇಲಾಖೆ ಅಥವಾ ರಸ್ತೆ ಮಾಲೀಕರನ್ನು ಗುರುತಿಸಲಾಗಿಲ್ಲ. ಈ ಸ್ವತಂತ್ರ ಆ್ಯಪ್ ದೂರು ಸಲ್ಲಿಸುವುದಿಲ್ಲ; ಸಾಕ್ಷ್ಯವನ್ನು ಪರಿಶೀಲಿಸಿ West Bengal PGRS ನಲ್ಲಿ ಸರಿಯಾದ ಜಿಲ್ಲೆ ಅಥವಾ ಇಲಾಖೆಯನ್ನು ನೀವೇ ಆಯ್ದು ದೃಢಪಡಿಸಿ.");
        } else if (mr) {
          paras.push("हे ठिकाण पिन केलेल्या OpenStreetMap पश्चिम बंगाल सीमेत नकाशित आहे; परंतु जबाबदार जिल्हा, विभाग किंवा रस्त्याचा मालक ओळखलेला नाही. हे स्वतंत्र अॅप तक्रार दाखल करत नाही; पुरावा तपासा आणि West Bengal PGRS मध्ये योग्य जिल्हा किंवा विभाग स्वतः निवडून पडताळा.");
        } else {
          paras.push("The location is mapped inside the pinned OpenStreetMap West Bengal boundary, but the responsible district, department and road owner have not been identified. This independent app does not submit the grievance; review the evidence, then select and verify the responsible district or department in West Bengal PGRS.");
        }
      } else if (kn) {
        if (ward) paras.push(`ಸೂಚಿಸಿದ ಬಿಎಂಸಿ ಆಡಳಿತ ವಾರ್ಡ್: ${ward}. ಇದು OpenStreetMap ಆಡಳಿತ ಗಡಿಯಿಂದ ಪಡೆದ ಸೂಚನೆ ಮಾತ್ರ; ಅಧಿಕೃತ BMC ಆ್ಯಪ್‌ನಲ್ಲಿ ಪರಿಶೀಲಿಸಿ.`);
        paras.push(OFFICIAL_HANDOFF_CHANNELS.has(route.delivery_channel)
          ? `ಸೂಚಿಸಿದ ನಾಗರಿಕ ಸಂಸ್ಥೆ: ${route.authority_name || "ಅಧಿಕಾರಿಯನ್ನು ಪರಿಶೀಲಿಸಿ"}. ಇದು ರಸ್ತೆ ಮಾಲೀಕತ್ವದ ದೃಢೀಕರಣವಲ್ಲ. ಈ ಸ್ವತಂತ್ರ ಆ್ಯಪ್ ದೂರು ಸಲ್ಲಿಸುವುದಿಲ್ಲ; ಸಾಕ್ಷ್ಯವನ್ನು ಪರಿಶೀಲಿಸಿ ಮತ್ತು ${route.handoff_name || "ಅಧಿಕೃತ ಸೇವೆ"} ಮೂಲಕ ನೀವೇ ಸಲ್ಲಿಸಿ.`
          : `ಸ್ಥಳದ ಆಧಾರದ ಮೇಲೆ ಸೂಚಿಸಿದ ನಾಗರಿಕ ಸಂಸ್ಥೆ: ${route.authority_name || "ಅಧಿಕಾರಿಯನ್ನು ಪರಿಶೀಲಿಸಿ"}. ಇದು ರಸ್ತೆ ಮಾಲೀಕತ್ವದ ದೃಢೀಕರಣವಲ್ಲ; ಬೇರೆ ಸಂಸ್ಥೆ ಜವಾಬ್ದಾರಿಯಾಗಿದ್ದರೆ ದಯವಿಟ್ಟು ಈ ದೂರನ್ನು ಆ ಸಂಸ್ಥೆಗೆ ವರ್ಗಾಯಿಸಿ.`);
      } else if (mr) {
        if (ward) paras.push(`सुचवलेला BMC प्रशासकीय विभाग: ${ward}. हा OpenStreetMap प्रशासकीय सीमेवर आधारित अंदाज आहे; अधिकृत BMC अॅपमध्ये पडताळा करा.`);
        paras.push(OFFICIAL_HANDOFF_CHANNELS.has(route.delivery_channel)
          ? `सुचवलेली नागरी संस्था: ${route.authority_name || "संस्था पडताळा"}. यावरून त्या रस्त्याची मालकी सिद्ध होत नाही. हे स्वतंत्र अॅप तक्रार दाखल करत नाही; पुरावा तपासा आणि ${route.handoff_name || "अधिकृत सेवेत"} स्वतः नोंदवा.`
          : `स्थानावरून सुचवलेली नागरी संस्था: ${route.authority_name || "संस्था पडताळा"}. यावरून रस्त्याची मालकी सिद्ध होत नाही; दुसरी संस्था जबाबदार असल्यास कृपया तक्रार तिच्याकडे पाठवा.`);
      } else if (bn) {
        const authorityName = route.authority_id === "wb-kmc"
          ? "কলকাতা পৌরসংস্থা (KMC)"
          : (route.authority_name || "কর্তৃপক্ষ যাচাই করুন");
        if (ward) {
          const wardAuthority = route.authority_id === "wb-kmc" ? "KMC" : "BMC";
          paras.push(`প্রস্তাবিত ${wardAuthority} প্রশাসনিক ওয়ার্ড: ${ward}। এটি OpenStreetMap-এর প্রশাসনিক সীমানা থেকে অনুমান করা; সরকারি পরিষেবায় যাচাই করুন।`);
        }
        paras.push(OFFICIAL_HANDOFF_CHANNELS.has(route.delivery_channel)
          ? `অবস্থানের ভিত্তিতে প্রস্তাবিত পৌর কর্তৃপক্ষ: ${authorityName}। এতে রাস্তার মালিকানা প্রমাণিত হয় না। এই স্বাধীন অ্যাপটি অভিযোগ জমা দেয় না; প্রমাণ যাচাই করে ${route.handoff_name || "সরকারি পরিষেবা"}-এ নিজে অভিযোগ নথিভুক্ত করুন এবং অভিযোগ নম্বরটি সংরক্ষণ করুন।`
          : `অবস্থানের ভিত্তিতে প্রস্তাবিত পৌর কর্তৃপক্ষ: ${authorityName}। এতে রাস্তার মালিকানা প্রমাণিত হয় না; অন্য কোনও সংস্থা দায়িত্বে থাকলে অভিযোগটি তাদের কাছে পাঠিয়ে দেওয়ার অনুরোধ রইল।`);
      } else {
        if (ward) paras.push(`Suggested BMC administrative ward: ${ward}. This is inferred from an OpenStreetMap administrative boundary; verify it in the official BMC app.`);
        paras.push(OFFICIAL_HANDOFF_CHANNELS.has(route.delivery_channel)
          ? `Suggested civic authority: ${route.authority_name || "verify the authority"}. This does not prove who owns this road. This independent app does not submit a grievance; review the evidence and finish it yourself in ${route.handoff_name || "the official service"}.`
          : `Suggested civic authority from the location: ${route.authority_name || "verify the authority"}. This does not prove road ownership; please forward this complaint if another agency owns the road.`);
      }
    }

    if (tender) {
      const warrantyKn = ({ dlp: "ದೋಷ ಹೊಣೆಗಾರಿಕೆ ಅವಧಿಯಲ್ಲಿ ಇನ್ನೂ ಇರುವ ಸಾಧ್ಯತೆ ಇದೆ",
                            maint: "ನಿರ್ವಹಣಾ ಅವಧಿಯಲ್ಲಿ ಇನ್ನೂ ಇರುವ ಸಾಧ್ಯತೆ ಇದೆ",
                            record: "ಈ ಭಾಗದ ದಾಖಲೆಯಲ್ಲಿದೆ" })[tender.warranty_code || "record"];
      const title = tender.title.slice(0, 140).trim();
      // Two paragraphs, not one: the first states what the records say, the second makes
      // the request. Published, never "awarded": the bundled field is the publication
      // date, and this letter names a real company to a government officer.
      if (kn) {
        paras.push(`ಸಾರ್ವಜನಿಕ ಖರೀದಿ ದಾಖಲೆಗಳ ಪ್ರಕಾರ ಈ ರಸ್ತೆ ಭಾಗ ಟೆಂಡರ್ ${tender.tender_number} ("${title}") ಅಡಿಯಲ್ಲಿ ಬರುವ ಸಾಧ್ಯತೆ ಇದೆ. ಇದು ${tender.published} ರಂದು ಪ್ರಕಟವಾಗಿದೆ${tender.contractor ? `, ಗೆದ್ದ ಬಿಡ್‌ದಾರರಾಗಿ ${tender.contractor} ಎಂದು ದಾಖಲಾಗಿದೆ` : ", ಗೆದ್ದ ಬಿಡ್‌ದಾರರ ಹೆಸರು ದಾಖಲೆಯಲ್ಲಿ ಇಲ್ಲ"}, ಮತ್ತು ${warrantyKn}.`);
        paras.push("ದೋಷ ಹೊಣೆಗಾರಿಕೆ ಅಥವಾ ನಿರ್ವಹಣಾ ಅವಧಿ ಜಾರಿಯಲ್ಲಿದ್ದರೆ, ಸಂಸ್ಥೆಗೆ ಹೆಚ್ಚುವರಿ ವೆಚ್ಚವಿಲ್ಲದೆ ಗುತ್ತಿಗೆದಾರರಿಂದಲೇ ದುರಸ್ತಿ ಮಾಡಿಸಬೇಕೆಂದು ವಿನಂತಿಸುತ್ತೇನೆ. ಇದು ಸಂಭಾವ್ಯ ದಾಖಲೆ ಹೊಂದಾಣಿಕೆ; ದಯವಿಟ್ಟು ಟೆಂಡರ್ ದಾಖಲೆಗಳೊಂದಿಗೆ ಪರಿಶೀಲಿಸಿ.");
      } else if (mr) {
        const warrantyMr = ({ dlp: "दोष दायित्व कालावधीत असण्याची शक्यता आहे",
                              maint: "देखभाल कालावधीत असण्याची शक्यता आहे",
                              record: "या रस्त्याच्या भागासाठी नोंदवलेले आहे" })[tender.warranty_code || "record"];
        paras.push(`सार्वजनिक खरेदी नोंदीनुसार हा रस्त्याचा भाग निविदा ${tender.tender_number} ("${title}") अंतर्गत येण्याची शक्यता आहे. ती ${tender.published} रोजी प्रकाशित झाली${tender.contractor ? ` आणि ${tender.contractor} यांची विजयी बोलीदार म्हणून नोंद आहे` : ", मात्र विजयी बोलीदाराची नोंद उपलब्ध नाही"}; ${warrantyMr}.`);
        paras.push("दोष दायित्व किंवा देखभाल कालावधी लागू असल्यास महानगरपालिकेला अतिरिक्त खर्च न लावता कंत्राटदाराकडून दुरुस्ती करून घ्यावी. ही संभाव्य नोंद-जुळणी आहे; कृपया मूळ निविदा कागदपत्रांशी पडताळा करा.");
      } else if (bn) {
        const warrantyBn = ({ dlp: "এখনও ত্রুটি-দায়ের মেয়াদের মধ্যে থাকতে পারে",
                              maint: "এখনও রক্ষণাবেক্ষণের মেয়াদের মধ্যে থাকতে পারে",
                              record: "এই রাস্তার অংশের জন্য নথিভুক্ত" })[tender.warranty_code || "record"];
        paras.push(`সরকারি ক্রয়-সংক্রান্ত নথি অনুযায়ী রাস্তার এই অংশটি টেন্ডার ${tender.tender_number} ("${title}")-এর আওতায় পড়তে পারে। টেন্ডারটি ${tender.published}-এ প্রকাশিত হয়েছিল${tender.contractor ? ` এবং ${tender.contractor}-কে সফল দরদাতা হিসেবে নথিভুক্ত করা হয়েছে` : ", তবে সফল দরদাতার নাম নথিতে নেই"}; ${warrantyBn}।`);
        paras.push("ত্রুটি-দায় বা রক্ষণাবেক্ষণের মেয়াদ চালু থাকলে পৌরসংস্থার অতিরিক্ত ব্যয় ছাড়াই ঠিকাদারের মাধ্যমে মেরামত করানোর অনুরোধ করছি। এটি কেবল সম্ভাব্য নথি-মিল; মূল টেন্ডার নথির সঙ্গে যাচাই করুন।");
      } else {
        paras.push(`Public procurement records indicate this road stretch probably falls under tender ${tender.tender_number} ("${title}"), published on ${tender.published}${tender.contractor ? `, with ${tender.contractor} recorded as the winning bidder` : ", with no winning bidder recorded"}, and it may still be ${tender.warranty}.`);
        paras.push("If the defect liability or maintenance period is in force, I request that the repair be carried out by the contractor at no additional cost to the corporation. This is a probable record match; kindly verify against the tender documents.");
      }
    }

    paras.push(kn ? "ನಿಮ್ಮ ಸೇವೆಗೆ ಧನ್ಯವಾದಗಳು." : mr ? "धन्यवाद." : bn ? "ধন্যবাদ।" : "Thank you for your service.");
    paras.push(kn ? `ವಂದನೆಗಳು,\n${sender}` : mr ? `आपला/आपली,\n${sender}` : bn ? `বিনীত,\n${sender}` : `Regards,\n${sender}`);
    return [subject, paras.join("\n\n")];
  }

  function civicIssueName(issueType, lang = "en") {
    const issue = normaliseIssueType(issueType);
    const names = {
      en: { road_damage: "road damage", garbage: "garbage accumulation",
            open_manhole: "open or damaged manhole" },
      kn: { road_damage: "ರಸ್ತೆ ಹಾನಿ", garbage: "ಕಸ ಸಂಗ್ರಹ",
            open_manhole: "ತೆರೆದ ಅಥವಾ ಹಾನಿಗೊಂಡ ಮ್ಯಾನ್‌ಹೋಲ್" },
      mr: { road_damage: "रस्त्याचे नुकसान", garbage: "साचलेला कचरा",
            open_manhole: "उघडे किंवा खराब मॅनहोल" },
      bn: { road_damage: "রাস্তার ক্ষতি", garbage: "জমে থাকা আবর্জনা",
            open_manhole: "খোলা বা ক্ষতিগ্রস্ত ম্যানহোল" },
    };
    return (names[lang] || names.en)[issue];
  }
  const issueFileStem = (issueType) => ({
    road_damage: "road-damage", garbage: "garbage", open_manhole: "open-manhole",
  })[normaliseIssueType(issueType)];

  function draftCivicComplaint(issueType, lat, lng, address, officerName, route = null,
                               captureSource = "manual", locationSource = null) {
    const issue = normaliseIssueType(issueType);
    if (issue === "road_damage") throw new Error("Road damage must use the verified detector draft.");
    const lang = LANG();
    const sender = S.name === "A concerned citizen"
      ? ({ kn: "ಕಾಳಜಿಯುಳ್ಳ ನಾಗರಿಕ", mr: "एक जागरूक नागरिक", bn: "একজন সচেতন নাগরিক" }[lang]
          || S.name) : S.name;
    const issueName = civicIssueName(issue, lang);
    const road = address ? address.split(",")[0].trim() : null;
    const coords = lat != null && lng != null
      ? `${Number(lat).toFixed(6)}, ${Number(lng).toFixed(6)}` : null;
    const map = coords ? `https://maps.google.com/?q=${coords.replace(" ", "")}` : null;
    const subject = lang === "kn"
      ? `${issueName} ದೂರು${road ? ` — ${road}` : ""}`
      : lang === "mr"
        ? `${issueName} तक्रार${road ? ` — ${road}` : ""}`
        : lang === "bn"
          ? `${issueName} সংক্রান্ত অভিযোগ${road ? ` — ${road}` : ""}`
          : `${issue === "open_manhole" ? "Urgent: " : ""}${issueName} complaint${road ? ` near ${road}` : ""}`;
    const location = lang === "kn"
      ? `ಸ್ಥಳ: ${address || "ಲಗತ್ತಿಸಿದ ಚಿತ್ರ ನೋಡಿ"}${coords ? `\nನಿರ್ದೇಶಾಂಕಗಳು: ${coords}\nನಕ್ಷೆ: ${map}` : ""}`
      : lang === "mr"
        ? `ठिकाण: ${address || "जोडलेला फोटो पहा"}${coords ? `\nनिर्देशांक: ${coords}\nनकाशा: ${map}` : ""}`
        : lang === "bn"
          ? `স্থান: ${address || "সংযুক্ত ছবিটি দেখুন"}${coords ? `\nস্থানাঙ্ক: ${coords}\nমানচিত্র: ${map}` : ""}`
          : `Location: ${address || "please see the attached photo"}${coords ? `\nCoordinates: ${coords}\nMap: ${map}` : ""}`;
    const request = issue === "open_manhole"
      ? {
          en: "The attached photo shows an open or damaged manhole. It is an immediate risk to pedestrians and road users. Please barricade or secure the location urgently, inspect it, and replace or repair the cover.",
          kn: "ಲಗತ್ತಿಸಿದ ಚಿತ್ರದಲ್ಲಿ ತೆರೆದ ಅಥವಾ ಹಾನಿಗೊಂಡ ಮ್ಯಾನ್‌ಹೋಲ್ ಇದೆ. ಇದು ಪಾದಚಾರಿಗಳು ಮತ್ತು ರಸ್ತೆ ಬಳಕೆದಾರರಿಗೆ ತಕ್ಷಣದ ಅಪಾಯ. ದಯವಿಟ್ಟು ಸ್ಥಳವನ್ನು ತುರ್ತಾಗಿ ಭದ್ರಪಡಿಸಿ, ಪರಿಶೀಲಿಸಿ ಮತ್ತು ಮುಚ್ಚಳವನ್ನು ಬದಲಿಸಿ ಅಥವಾ ದುರಸ್ತಿ ಮಾಡಿ.",
          mr: "जोडलेल्या फोटोमध्ये उघडे किंवा खराब मॅनहोल दिसत आहे. पादचारी आणि रस्ता वापरणाऱ्यांसाठी हा तातडीचा धोका आहे. कृपया जागा त्वरित सुरक्षित करून तपासणी करा आणि झाकण बदला किंवा दुरुस्त करा.",
          bn: "সংযুক্ত ছবিতে একটি খোলা বা ক্ষতিগ্রস্ত ম্যানহোল দেখা যাচ্ছে। এটি পথচারী ও রাস্তা ব্যবহারকারীদের জন্য তাৎক্ষণিক বিপদ। অনুগ্রহ করে দ্রুত স্থানটি ঘিরে নিরাপদ করুন, পরিদর্শন করুন এবং ঢাকনাটি বদল বা মেরামত করুন।",
        }
      : {
          en: "The attached photo shows accumulated or uncollected garbage at this location. Please inspect the site, remove the waste, clean the area, and address the cause of repeated dumping if applicable.",
          kn: "ಲಗತ್ತಿಸಿದ ಚಿತ್ರದಲ್ಲಿ ಈ ಸ್ಥಳದಲ್ಲಿ ಸಂಗ್ರಹವಾದ ಅಥವಾ ತೆರವುಗೊಳಿಸದ ಕಸ ಇದೆ. ದಯವಿಟ್ಟು ಸ್ಥಳವನ್ನು ಪರಿಶೀಲಿಸಿ, ಕಸವನ್ನು ತೆಗೆದು ಸ್ವಚ್ಛಗೊಳಿಸಿ ಮತ್ತು ಅನ್ವಯಿಸಿದರೆ ಮರುಮರು ಕಸ ಹಾಕುವ ಕಾರಣವನ್ನು ಪರಿಹರಿಸಿ.",
          mr: "जोडलेल्या फोटोमध्ये या ठिकाणी साचलेला किंवा न उचललेला कचरा दिसत आहे. कृपया जागेची पाहणी करून कचरा उचला, परिसर स्वच्छ करा आणि लागू असल्यास वारंवार कचरा टाकण्याचे कारण दूर करा.",
          bn: "সংযুক্ত ছবিতে এই স্থানে জমে থাকা বা না-তোলা আবর্জনা দেখা যাচ্ছে। অনুগ্রহ করে স্থানটি পরিদর্শন করে বর্জ্য সরান, এলাকা পরিষ্কার করুন এবং প্রযোজ্য হলে বারবার আবর্জনা ফেলার কারণটি সমাধান করুন।",
        };
    const greeting = ({ kn: `ಮಾನ್ಯ ${officerName || "ಅಧಿಕಾರಿಗಳೇ"} ಅವರಿಗೆ,`,
      mr: `प्रति ${officerName || "संबंधित अधिकारी"},`,
      bn: `মাননীয় ${officerName || "সংশ্লিষ্ট আধিকারিক"},` }[lang]
      || `Dear ${officerName || "Sir or Madam"},`);
    const close = ({ kn: `ಧನ್ಯವಾದಗಳು.\n\nವಂದನೆಗಳು,\n${sender}`,
      mr: `धन्यवाद.\n\nआपला/आपली,\n${sender}`,
      bn: `ধন্যবাদ।\n\nবিনীত,\n${sender}` }[lang]
      || `Thank you.\n\nRegards,\n${sender}`);
    const statewideWestBengal = route && route.authority_id === "wb-statewide-unverified";
    const authorityNote = route && route.authority_name
      ? (statewideWestBengal
        ? (lang === "kn" ? "ಸ್ಥಳವು ಪಶ್ಚಿಮ ಬಂಗಾಳದೊಳಗಿದೆ; ಜವಾಬ್ದಾರ ಸಂಸ್ಥೆಯನ್ನು ಗುರುತಿಸಲಾಗಿಲ್ಲ. West Bengal PGRS ನಲ್ಲಿ ಸರಿಯಾದ ಜಿಲ್ಲೆ ಅಥವಾ ಇಲಾಖೆಯನ್ನು ಆಯ್ದು ದೃಢಪಡಿಸಿ."
          : lang === "mr" ? "ठिकाण पश्चिम बंगालमध्ये आहे; जबाबदार संस्था ओळखलेली नाही. West Bengal PGRS मध्ये योग्य जिल्हा किंवा विभाग निवडून पडताळा."
          : lang === "bn" ? "স্থানটি পিন-করা OpenStreetMap পশ্চিমবঙ্গ সীমানার মধ্যে; দায়িত্বপ্রাপ্ত সংস্থা চিহ্নিত করা হয়নি। West Bengal PGRS-এ দায়িত্বপ্রাপ্ত জেলা বা দপ্তর নির্বাচন ও যাচাই করুন।"
          : "The location is inside the pinned OpenStreetMap West Bengal boundary; the responsible authority has not been identified. Select and verify the responsible district or department in West Bengal PGRS.")
        : lang === "kn" ? `ಸ್ಥಳದ ಆಧಾರದ ಮೇಲೆ ಸೂಚಿಸಿದ ನಾಗರಿಕ ಸಂಸ್ಥೆ: ${route.authority_name}. ಅಧಿಕೃತ ಸೇವೆಯಲ್ಲಿ ಪರಿಶೀಲಿಸಿ.`
        : lang === "mr" ? `ठिकाणावरून सुचवलेली नागरी संस्था: ${route.authority_name}. अधिकृत सेवेत पडताळा करा.`
        : lang === "bn" ? `অবস্থানের ভিত্তিতে প্রস্তাবিত পৌর কর্তৃপক্ষ: ${route.authority_name}। সরকারি পরিষেবায় যাচাই করুন।`
        : `Suggested civic authority from the location: ${route.authority_name}. Please verify it in the official service.`)
      : null;
    const imported = captureSource === "manual_import";
    const provenanceNote = imported
      ? (lang === "kn"
          ? `ಈ ಚಿತ್ರವನ್ನು ಬಳಕೆದಾರರು ಆಯ್ಕೆಮಾಡಿ/ಆಮದು ಮಾಡಿದ್ದಾರೆ; ಅದು ಯಾವಾಗ ತೆಗೆದದ್ದು ಎಂಬುದನ್ನು ಆ್ಯಪ್ ಪರಿಶೀಲಿಸಿಲ್ಲ.${locationSource === "current_confirmed_for_import" ? " ಬಳಕೆದಾರರ ದೃಢೀಕರಣದ ನಂತರ ಪ್ರಸ್ತುತ ಸ್ಥಳವನ್ನು ಚಿತ್ರಕ್ಕೆ ಜೋಡಿಸಲಾಗಿದೆ." : " ಚಿತ್ರಕ್ಕೆ ಪ್ರಸ್ತುತ ಸ್ಥಳವನ್ನು ಜೋಡಿಸಲಾಗಿಲ್ಲ."}`
        : lang === "mr"
          ? `हे छायाचित्र वापरकर्त्याने निवडले/आयात केले आहे; ते केव्हा घेतले याची अॅपने पडताळणी केलेली नाही.${locationSource === "current_confirmed_for_import" ? " वापरकर्त्याच्या पुष्टीनंतर सध्याचे स्थान छायाचित्राशी जोडले आहे." : " छायाचित्राशी सध्याचे स्थान जोडलेले नाही."}`
        : lang === "bn"
          ? `ছবিটি ব্যবহারকারী বেছে নিয়েছেন/আমদানি করেছেন; এটি কখন তোলা হয়েছিল অ্যাপ তা যাচাই করেনি।${locationSource === "current_confirmed_for_import" ? " ব্যবহারকারীর নিশ্চিতকরণের পরে বর্তমান অবস্থানটি ছবির সঙ্গে যুক্ত হয়েছে।" : " ছবির সঙ্গে বর্তমান অবস্থান যুক্ত করা হয়নি।"}`
        : `This photo was selected/imported by the user; the app has not verified when it was taken.${locationSource === "current_confirmed_for_import" ? " The current location was linked only after the user's confirmation." : " No current location was linked to the photo."}`)
      : null;
    return [subject, [greeting, location, request[lang] || request.en, provenanceNote,
      authorityNote, close]
      .filter(Boolean).join("\n\n")];
  }

  // ---------- storage (IndexedDB) ----------
  let _db = null;
  function idb() {
    return new Promise((resolve, reject) => {
      if (_db) return resolve(_db);
      const req = indexedDB.open("potholes", 6);
      req.onupgradeneeded = () => {
        const d = req.result;
        const reports = d.objectStoreNames.contains("reports")
          ? req.transaction.objectStore("reports")
          : d.createObjectStore("reports", { keyPath: "id", autoIncrement: true });
        // Cursor over lightweight candidate ranges instead of getAll(): report records
        // contain photos, so cloning every old image for every accepted frame would make
        // long footage analysis slower and more memory-hungry as history grows.
        if (!reports.indexNames.contains("by_lat")) reports.createIndex("by_lat", "lat");
        if (!reports.indexNames.contains("by_drive")) reports.createIndex("by_drive", "drive_id");
        // A canonical event can be observed on later drives without changing its original
        // drive_id. Index every drive that has seen it so the next adjacent observation is
        // found even when it lies outside the stricter cross-drive radius or has no GPS.
        if (!reports.indexNames.contains("by_sighting_drive")) {
          reports.createIndex("by_sighting_drive", "sighting_drive_ids", { multiEntry: true });
        }
        // How many frames a drive actually checked is only known while it runs:
        // rejected frames are not kept unless debug mode is on, so the count has to
        // be recorded at the end or it is lost.
        if (!d.objectStoreNames.contains("drives")) d.createObjectStore("drives", { keyPath: "id" });
        // Continuous footage: capture stops guessing an interval, and a drive can be
        // re-analysed later, more densely or by a better model. Discarded frames are gone.
        if (!d.objectStoreNames.contains("footage")) {
          const f = d.createObjectStore("footage", { keyPath: "key" });
          f.createIndex("by_drive", "drive_id");
        }
        if (!d.objectStoreNames.contains("state_packs")) {
          const packs = d.createObjectStore("state_packs", { keyPath: "cache_key" });
          packs.createIndex("by_last_used", "last_used_at");
          packs.createIndex("by_state", "state_code");
        }
      };
      req.onsuccess = () => {
        _db = req.result;
        _db.onversionchange = () => { _db.close(); _db = null; };
        resolve(_db);
      };
      req.onerror = () => reject(req.error);
    });
  }
  // A write is not done when the request succeeds, it is done when the transaction
  // commits. Chrome reports a full disk by aborting the transaction, and the request
  // itself still succeeds, so resolving on req.onsuccess reported success for writes that
  // rolled back: measured, 672 MB of footage reported stored and absent afterwards. A
  // read has nothing to commit, so it still resolves on the request.
  function op(mode, fn, storeName = "reports") {
    return idb().then((d) => new Promise((resolve, reject) => {
      const tx = d.transaction(storeName, mode);
      const req = fn(tx.objectStore(storeName));
      if (mode === "readonly") {
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
        return;
      }
      let value, failure = null;
      req.onsuccess = () => { value = req.result; };
      req.onerror = (e) => { failure = req.error; e.preventDefault(); };
      tx.oncomplete = () => resolve(value);
      const died = () => reject(storageError(failure || tx.error));
      tx.onabort = died;
      tx.onerror = died;
    }));
  }

  // A full device is the common cause and the only one the user can act on, so it says so
  // rather than surfacing a DOMException name.
  function storageError(err) {
    const name = err && err.name;
    if (name === "QuotaExceededError") {
      return new Error("This phone is out of storage, so nothing more can be saved. Free some space, or delete old drives and their video from the app.");
    }
    return new Error((err && err.message) || "Could not save to this device's storage.");
  }
  const allReports = () => op("readonly", (s) => s.getAll());
  const getReport = (id) => op("readonly", (s) => s.get(Number(id)));
  const putReport = (r) => op("readwrite", (s) => s.put(r));
  const addReport = (r) => op("readwrite", (s) => s.add(r));
  const delReport = (id) => op("readwrite", (s) => s.delete(Number(id)));
  const allDrives = () => op("readonly", (s) => s.getAll(), "drives");
  const getDrive = (id) => op("readonly", (s) => s.get(String(id)), "drives");
  const putFootage = (seg) => op("readwrite", (s) => s.put(seg), "footage");
  const footageFor = (driveId) => op("readonly", (s) => s.index("by_drive").getAll(String(driveId)), "footage");
  const allFootage = () => op("readonly", (s) => s.getAll(), "footage");
  const putDrive = (d) => op("readwrite", (s) => s.put(d), "drives");

  // Accepted Drive jobs finish concurrently. A separate getAll() followed by add()
  // lets two nearby jobs both observe "none" and both write. Keep the final check and
  // insert in one read-write transaction; IndexedDB serialises these transactions on the
  // reports store, so exactly one concurrent detection becomes the saved event.
  function addReportUnlessDuplicate(rec, dedupe) {
    if (!dedupe) return addReport(rec).then((id) => ({ id, duplicate: null }));
    return idb().then((d) => new Promise((resolve, reject) => {
      const tx = d.transaction("reports", "readwrite");
      const store = tx.objectStore("reports");
      let result = null, failure = null;
      const addNew = () => {
        const add = store.add(rec);
        add.onsuccess = () => { result = { id: add.result, duplicate: null }; };
        add.onerror = () => { failure = add.error; };
      };
      const scan = (request, next) => {
        request.onsuccess = () => {
          const cursor = request.result;
          if (!cursor) { next(); return; }
          const match = roadEventMatch(rec, cursor.value);
          if (match) {
            const prior = cursor.value;
            const keys = Array.isArray(prior.source_event_keys)
              ? prior.source_event_keys.slice() : (prior.source_event_key ? [prior.source_event_key] : []);
            if (rec.source_event_key && !keys.includes(rec.source_event_key)) keys.push(rec.source_event_key);
            const exactReplay = match.kind === "same_source";
            const observedAt = eventTime(rec);
            // Keep a bounded envelope per drive, not a global 64-item cap: popular
            // locations can be revisited many times, and a full old global array would
            // otherwise stop recording the first sighting of a new drive. Sightings older
            // than the cross-drive horizon are no longer useful for approximate matching;
            // exact retained-footage replays remain covered by source_event_keys.
            const currentDrive = rec.drive_id == null ? null : String(rec.drive_id);
            const cutoff = Number.isFinite(observedAt) ? observedAt - DEDUPE_HISTORY_S : -Infinity;
            const sightings = storedSightings(prior).filter((seen) => {
              const seenAt = Number.isFinite(seen.captured_at) ? seen.captured_at : null;
              return (seen.drive_id != null && String(seen.drive_id) === currentDrive)
                || seenAt == null || seenAt >= cutoff;
            });
            const sameDriveCount = currentDrive == null ? 0 : sightings.filter((seen) =>
              seen.drive_id != null && String(seen.drive_id) === currentDrive).length;
            if ((match.kind === "same_drive" || match.kind === "prior_drive")
                && !exactReplay && (currentDrive == null || sameDriveCount < 64)) {
              sightings.push(eventSighting(rec));
            }
            const sightingDriveIds = [...new Set(sightings
              .map((seen) => seen.drive_id == null ? null : String(seen.drive_id)).filter(Boolean))];
            const updated = {
              ...prior,
              source_event_keys: keys.slice(-64),
              event_sightings: sightings,
              sighting_drive_ids: sightingDriveIds,
              seen_count: exactReplay ? (prior.seen_count || 1) : (prior.seen_count || 1) + 1,
              last_seen_at: Math.max(eventTime(prior) || 0, eventTime(rec) || 0),
            };
            const write = cursor.update(updated);
            write.onsuccess = () => { result = { id: null, duplicate: updated, match: match.kind }; };
            write.onerror = () => { failure = write.error; };
            return;
          }
          cursor.continue();
        };
        request.onerror = () => { failure = request.error; };
      };
      const scanLocation = () => {
        if (!finiteCoord(rec.lat) || !finiteCoord(rec.lng)) { addNew(); return; }
        const latitudeBand = DEDUPE_HISTORY_RADIUS_M / 110900;
        scan(store.index("by_lat").openCursor(
          IDBKeyRange.bound(rec.lat - latitudeBand, rec.lat + latitudeBand)), addNew);
      };
      try {
        if (rec.drive_id != null) {
          const driveKey = String(rec.drive_id);
          const scanOriginalDrive = () => scan(
            store.index("by_drive").openCursor(IDBKeyRange.only(driveKey)), scanLocation);
          scan(store.index("by_sighting_drive").openCursor(IDBKeyRange.only(driveKey)), scanOriginalDrive);
        } else scanLocation();
      } catch (e) {
        failure = e;
        try { tx.abort(); } catch (_) {}
      }
      tx.oncomplete = () => result ? resolve(result)
        : reject(storageError(failure || new Error("Could not save this report.")));
      const died = () => reject(storageError(failure || tx.error));
      tx.onabort = died;
      tx.onerror = () => {};
    }));
  }

  // Photos are stored as blobs, not base64. Measured on a device with a hundred 1024px
  // thumbnails: reading them back took 177 ms as base64 strings and 3 ms as blobs, writing
  // took 253 ms against 90 ms, and each one is 88 KB as text against 66 KB binary. Every
  // screen that lists reports paid that difference, which is why the app felt slow
  // everywhere rather than in one place.
  //
  // Records written before this change hold a data URL string. Everything that reads a
  // photo accepts either, so nothing has to be migrated or rewritten.
  const dataUrlToBlob = async (u) => {
    if (!u || typeof u !== "string") return u || null;
    try { return await (await fetch(u)).blob(); } catch (e) { return u; }
  };
  const photoToBase64 = async (v) => {
    if (!v) return null;
    if (typeof v === "string") return v.split(",")[1];
    return await new Promise((res) => {
      const fr = new FileReader();
      fr.onload = () => res(String(fr.result).split(",")[1]);
      fr.onerror = () => res(null);
      fr.readAsDataURL(v);
    });
  };

  const toDict = (r) => ({ ...r, photo_url: r.photo });
  // The list never renders the evidence copy, so it never receives it.
  const listDict = (r) => { const d = toDict(r); delete d.photo_full; return d; };

  // ---------- image ----------

  // The fraction of a dashcam frame kept for detection. A phone mounted in a car points at
  // the horizon, so the top of the frame is sky, trees and parked cars and the road worth
  // inspecting is underneath. Measured on frames from a real drive: keeping the lower 60%
  // took detection from 18% of frames to 27%, and every dashcam pothole in the eval set
  // still passed. Keeping less than half loses the damage itself, and this must not be
  // applied to a single shot, where the photographer has already aimed at the defect.
  const ROAD_BAND = 0.6;

  function averageLuminance(ctx, width, height) {
    const data = ctx.getImageData(0, 0, width, height).data;
    const step = Math.max(1, Math.floor(Math.sqrt((width * height) / 12000)));
    let total = 0, count = 0, clippedDark = 0, clippedBright = 0;
    for (let y = 0; y < height; y += step) {
      for (let x = 0; x < width; x += step) {
        const i = (y * width + x) * 4;
        const lum = 0.2126 * data[i] + 0.7152 * data[i + 1] + 0.0722 * data[i + 2];
        total += lum; count++;
        if (lum < 12) clippedDark++;
        if (lum > 245) clippedBright++;
      }
    }
    return { mean: count ? total / count : 0,
             dark: count ? clippedDark / count : 1,
             bright: count ? clippedBright / count : 0 };
  }

  async function toDataUrl(blob, maxDim, quality = 0.85, boost = false, band = 1) {
    const bmp = await createImageBitmap(blob, { imageOrientation: "from-image" });
    const sx = 0, sw = bmp.width;
    const sh = Math.max(1, Math.round(bmp.height * band));
    const sy = bmp.height - sh;
    const scale = Math.min(1, maxDim / Math.max(sw, sh));
    const c = document.createElement("canvas");
    c.width = Math.round(sw * scale);
    c.height = Math.round(sh * scale);
    const ctx = c.getContext("2d");
    ctx.drawImage(bmp, sx, sy, sw, sh, 0, 0, c.width, c.height);
    // Enhancement follows the pixels, not the wall clock. Fixed evening hours boosted
    // bright street-lit frames and amplified noise. Preserve the original evidence copy;
    // this is only the small image used for detection.
    const light = boost ? averageLuminance(ctx, c.width, c.height) : null;
    if (boost && light.mean < 72 && light.bright < 0.08) {
      const lift = Math.min(1.65, Math.max(1.15, 85 / Math.max(35, light.mean)));
      ctx.filter = `brightness(${lift.toFixed(2)}) contrast(1.10)`;
      ctx.drawImage(bmp, sx, sy, sw, sh, 0, 0, c.width, c.height);
      ctx.filter = "none";
    }
    const out = c.toDataURL("image/jpeg", quality);
    if (bmp.close) bmp.close();
    return out;
  }

  // ---------- pipeline ----------
  // Detection requests stay concurrent, but their final storage decisions must follow
  // capture order within one drive. Otherwise a later frame can finish inference first,
  // sit just outside the historical radius, and briefly become a second canonical before
  // the earlier bridging frame is known. Callers submit live/VOD frames chronologically;
  // this queue serialises only the short post-detection commit, not model inference.
  const driveCommitTails = new Map();
  function reserveDriveCommit(driveId) {
    if (driveId == null) return null;
    const key = String(driveId);
    const wait = driveCommitTails.get(key) || Promise.resolve();
    let release;
    const own = new Promise((resolve) => { release = resolve; });
    const tail = wait.then(() => own);
    driveCommitTails.set(key, tail);
    tail.finally(() => { if (driveCommitTails.get(key) === tail) driveCommitTails.delete(key); });
    let finished = false;
    return {
      wait,
      done() {
        if (finished) return;
        finished = true;
        release();
      },
    };
  }

  async function createReport(fd, driveMode) {
    const photos = (fd.getAll ? fd.getAll("photo") : [fd.get("photo")])
      .filter((p) => p && p.size).slice(0, 3);
    if (!photos.length) throw new Error("Empty photo.");
    const requestedPrimary = parseInt(fd.get("primary_index"), 10);
    const primaryIndex = Number.isInteger(requestedPrimary) && requestedPrimary >= 0 && requestedPrimary < photos.length
      ? requestedPrimary : 0;
    const photo = photos[primaryIndex];
    const latRaw = fd.get("lat"), lngRaw = fd.get("lng");
    const lat = latRaw != null && latRaw !== "" ? parseFloat(latRaw) : null;
    const lng = lngRaw != null && lngRaw !== "" ? parseFloat(lngRaw) : null;
    const driveId = driveMode ? (fd.get("drive_id") || null) : null;
    const commitTurn = driveMode ? reserveDriveCommit(driveId) : null;
    try {
    const capturedAtRaw = parseInt(fd.get("captured_at_ms"), 10);
    const sourceOffsetRaw = parseInt(fd.get("source_offset_ms"), 10);
    const gpsAccuracyRaw = parseFloat(fd.get("gps_accuracy"));
    const speedRaw = parseFloat(fd.get("speed"));
    const headingRaw = parseFloat(fd.get("heading"));
    const requestedSource = String(fd.get("capture_source") || "");
    const captureSource = driveMode
      ? (requestedSource === "drive_vod" ? "drive_vod" : "drive_live")
      : normaliseManualCaptureSource(requestedSource);
    const sourceEventKey = driveMode && fd.get("source_event_key")
      ? String(fd.get("source_event_key")).slice(0, 180) : null;
    // Bind the request to the mode in which it began. A Settings change while a slow
    // request is finishing must not unpredictably change whether that observation saves.
    const dedupe = !S.debug;
    let frameQuality = null;
    try { frameQuality = JSON.parse(fd.get("frame_quality") || "null"); } catch (e) {}

    progress(driveMode ? pmsg("capture") : pmsg("compress"));
    // Measured on a real device: a 2000px frame is ~1.1 MB of base64 and every request
    // is marshalled across the JS-to-native bridge, which made a live detection call
    // take 13.5s median and stuttered the preview. The live pass therefore runs on a
    // smaller frame; the recorded footage keeps full quality, so a pothole missed live
    // is still recoverable by re-analysing the video. Single shots stay at full size:
    // one photo, someone waiting, and no footage behind it.
    // Drive Mode supplies one short burst. The model sees a full context view of the
    // sharpest frame plus three road-band crops in chronological order. Context keeps
    // lane/edge geometry; the crops give a distant defect enough pixels to judge. A
    // manual photo remains one full-resolution view because the user already aimed it.
    let imageInputs, dataUrl;
    if (driveMode) {
      const roadViews = await Promise.all(photos.map((p) => toDataUrl(p, 1024, 0.85, true, ROAD_BAND)));
      const context = await toDataUrl(photo, 768, 0.82, false, 1);
      imageInputs = [{ url: context }, ...roadViews.map((url) => ({ url }))];
      dataUrl = roadViews[primaryIndex];
    } else {
      dataUrl = await toDataUrl(photo, 2000, 0.85, true, 1);
      imageInputs = [{ url: dataUrl }];
    }
    // A waiting single-shot user benefits from speculative geocoding. Drive Mode rejects
    // most bursts, so starting a location lookup for every road sample would hammer the
    // public geocoder; it starts only after a burst is accepted.
    const geoP = !driveMode && lat != null
      ? reverseGeocode(lat, lng).catch(() => null) : null;
    const shortOf = (g) => (g && g.short) || null;
    progress(pmsg("detect"));
    // Single shot: contract adjudication runs speculatively alongside confirmation,
    // so the wait is the max of the two rather than their sum, and one watching user
    // feels it. Drive Mode rejects most frames and reports through the HUD, so
    // speculating there would buy nothing and bill a text call per frame.
    // Coordinates settle coverage on their own; only a missing fix has to wait for the
    // address. The contract shortlist needs the owning body, so the GIS lookup starts
    // here rather than after detection: it is one short request and it runs while the
    // photo is being analysed, so it costs nothing on the clock. routeOfficer shares
    // this same answer instead of asking again.
    const coordCoverage = (lat != null && lng != null) ? inCoverage(lat, lng, null) : null;
    const tenderP = (driveMode || coordCoverage === false) ? null
      : Promise.all([geoP, jurisdictionOf(lat, lng)])
          .then(([g, w]) => (w && w.kind === "town" && w.lgd ? matchTender(shortOf(g), w.lgd) : null))
          .catch(() => null);
    const sequenceNote = driveMode
      ? `\n- Capture layout: image 1 is full-frame context from the sharpest burst frame. Images 2-${imageInputs.length} are lower-road crops in chronological order; the sharpest crop is chronological frame ${primaryIndex + 1}.`
      : "\n- Capture layout: one user-framed full image.";
    const detectPrompt = DETECT_PROMPT + sequenceNote + (LANG() === "kn"
      ? "\n- Write the description field in formal Kannada (ಕನ್ನಡ ಭಾಷೆಯಲ್ಲಿ ಬರೆಯಿರಿ)."
      : LANG() === "mr"
        ? "\n- Write the description field in clear formal Marathi (मराठी भाषेत लिहा)."
        : LANG() === "bn"
          ? "\n- Write the description field in clear formal Bengali (পরিষ্কার, প্রমিত বাংলায় লিখুন)."
        : "");
    const detectionModel = S.model, detectionDetail = S.detail;
    // Single shot has one verdict on screen, so show it the moment it streams in.
    // Drive Mode analyses run concurrently and report through the HUD instead.
    // Drive Mode has no verdict on screen to update, so it passed no callback and took
    // the unstreamed path, waiting for a description it discards on every rejected frame.
    // It streams now purely to stop as soon as the frame is known to be rejected.
    const a = await analyzeImage(imageInputs, detectPrompt, "assessment", ASSESS_SCHEMA, detectionModel,
      driveMode ? null : emitVerdict, driveMode && !S.debug, detectionDetail);
    const decision = decisionFor(a);
    const accepted = decision === "accept";
    const detector = { model: detectionModel, detail: detectionDetail, prompt_version: PROMPT_VERSION,
                       schema_version: SCHEMA_VERSION, evidence_count: imageInputs.length };
    if (driveMode && !accepted) {
      return { analyzed: true, accepted: false, stored: false, found: false,
               duplicate: false, duplicate_of: null, decision, review: decision === "review",
               ...a, observation: { ...a }, detector };
    }

    const duplicateResult = (existing) => driveMode
      ? { analyzed: true, accepted: true, stored: false, found: false,
          duplicate: true, duplicate_of: existing.id, existing_report_id: existing.id,
          skipped: "already reported nearby", decision, review: false,
          ...a, observation: { ...a }, detector }
      : { ...toDict(existing), duplicate: true, duplicate_of: existing.id };

    if (accepted) progress(pmsg("finalize"));
    const geo = accepted
      ? await (geoP || (lat != null ? reverseGeocode(lat, lng).catch(() => null) : Promise.resolve(null)))
      : null;
    const address = shortOf(geo);
    const route = accepted
      ? await routeOfficer(geo || address, lat, lng, gpsAccuracyRaw, headingRaw, speedRaw)
      : unroutedRoute(null);
    const covered = accepted && route.routed;
    // Drive Mode does not speculate (it would bill a text call for every frame, and most
    // frames are rejected), so an accepted drive pothole matches its contract here, once
    // it is known to be worth a complaint. The GIS answer is already memoised, so this
    // costs no extra network call.
    const tender = accepted && covered && route.tender_eligible === true
      ? await (tenderP || jurisdictionOf(lat, lng)
          .then((w) => (w && w.kind === "town" && w.lgd ? matchTender(address, w.lgd) : null))
          .catch(() => null))
      : null;
    if (accepted) progress(pmsg("write"));
    // No authority means no complaint. The photo, verdict and location are still kept,
    // so nothing is lost if coverage later extends to this place.
    const [subject, body] = accepted && covered
      ? draftEmail(a, lat, lng, address, route.officer_name, tender, route)
      : [null, null];
    // The evidence copy: what the officer receives. Detection works on a small
    // frame for speed and token cost, but the complaint deserves the full capture,
    // unmodified. Only kept for reports that can be handed to a supported authority.
    const photoFull = accepted && covered ? await toDataUrl(photo, 4000, 0.92, false) : null;

    const rec = {
      created_at: Date.now() / 1000, lat, lng, address,
      photo: await dataUrlToBlob(dataUrl), photo_full: await dataUrlToBlob(photoFull),
      issue_type: "road_damage",
      report_origin: "ai_detection",
      is_reportable: a.reportable ? 1 : 0,
      is_pothole: a.damage_type === "pothole_cavity" ? 1 : 0,
      damage_type: a.damage_type, assessment: a.assessment, image_quality: a.image_quality,
      on_drivable_surface: !!a.on_drivable_surface,
      has_broken_edge_or_rim: !!a.has_broken_edge_or_rim,
      has_depth_or_surface_loss: !!a.has_depth_or_surface_loss,
      temporal_consistency: a.temporal_consistency,
      size: a.size,
      decision,
      description: a.description, email_subject: subject, email_body: body,
      status: accepted ? (covered ? "draft" : "unrouted") : (decision === "review" ? "review" : "rejected"),
      detection_model: detectionModel, image_detail: detectionDetail, prompt_version: PROMPT_VERSION,
      schema_version: SCHEMA_VERSION, evidence_count: imageInputs.length,
      // Distinguishes "we know where this is and do not cover it" from "we never got a
      // fix", which are the same status but very different things to tell someone.
      unrouted_reason: accepted && !covered ? (route.unrouted_reason || "outside_area") : null,
      unrouted_body: accepted && !covered ? (route.authority_name || null) : null,
      officer_name: covered ? (route.officer_name || null) : null,
      officer_email: covered ? (route.officer_email || null) : null,
      authority_id: covered ? (route.authority_id || null) : null,
      authority_name: covered ? (route.authority_name || null) : null,
      authority_registry_version: covered ? (route.authority_registry_version || null) : null,
      delivery_channel: covered ? (route.delivery_channel || "email") : null,
      ward_code: covered ? (route.ward_code || null) : null,
      routing_source: covered ? (route.routing_source || null) : null,
      routing_match_field: covered ? (route.routing_match_field || null) : null,
      routing_match_value: covered ? (route.routing_match_value || null) : null,
      highway_ref: covered ? (route.highway_ref || null) : null,
      routing_pack_id: covered ? (route.routing_pack_id || null) : null,
      routing_pack_version: covered ? (route.routing_pack_version || null) : null,
      routing_pack_sha256: covered ? (route.routing_pack_sha256 || null) : null,
      routing_pack_state_code: covered ? (route.routing_pack_state_code || null) : null,
      region: covered ? (route.region || null) : null,
      ownership_unverified: covered ? !!route.ownership_unverified : null,
      handoff_name: covered ? (route.handoff_name || null) : null,
      handoff_url: covered ? (route.handoff_url || null) : null,
      handoff_package: covered ? (route.handoff_package || null) : null,
      alternate_handoff_name: covered ? (route.alternate_handoff_name || null) : null,
      alternate_handoff_url: covered ? (route.alternate_handoff_url || null) : null,
      whatsapp_url: covered ? (route.whatsapp_url || null) : null,
      helpline: covered ? (route.helpline || null) : null,
      requires_official_reference: covered ? !!route.requires_official_reference : false,
      official_grievance_id: null,
      submitted_at: null,
      tender_number: tender ? tender.tender_number : null,
      contractor: tender ? tender.contractor : null,
      tender_note: tender ? tender.note : null,
      tender_pack_id: tender ? (tender.tender_pack_id || null) : null,
      tender_pack_version: tender ? (tender.tender_pack_version || null) : null,
      tender_pack_sha256: tender ? (tender.tender_pack_sha256 || null) : null,
      tender_pack_state_code: tender ? (tender.tender_pack_state_code || null) : null,
      sent_at: null,
      drive_id: driveId,
      capture_source: captureSource,
      location_source: driveMode ? "drive_gps" : (fd.get("location_source") || null),
      source_event_key: sourceEventKey,
      source_event_keys: sourceEventKey ? [sourceEventKey] : [],
      captured_at: Number.isFinite(capturedAtRaw) ? capturedAtRaw / 1000 : null,
      source_offset_s: Number.isFinite(sourceOffsetRaw) ? sourceOffsetRaw / 1000 : null,
      gps_accuracy: Number.isFinite(gpsAccuracyRaw) ? gpsAccuracyRaw : null,
      speed_mps: Number.isFinite(speedRaw) ? speedRaw : null,
      heading: Number.isFinite(headingRaw) ? ((headingRaw % 360) + 360) % 360 : null,
      frame_quality: Array.isArray(frameQuality) ? frameQuality : null,
      primary_frame_index: primaryIndex,
      debug_capture: !dedupe,
      dedupe_eligible: accepted && dedupe,
      event_sightings: accepted ? [eventSighting({
        drive_id: driveId, lat, lng,
        source_offset_s: Number.isFinite(sourceOffsetRaw) ? sourceOffsetRaw / 1000 : null,
        captured_at: Number.isFinite(capturedAtRaw) ? capturedAtRaw / 1000 : null,
        gps_accuracy: Number.isFinite(gpsAccuracyRaw) ? gpsAccuracyRaw : null,
        speed_mps: Number.isFinite(speedRaw) ? speedRaw : null,
        heading: Number.isFinite(headingRaw) ? ((headingRaw % 360) + 360) % 360 : null,
        source_event_key: sourceEventKey,
      })] : [],
      sighting_drive_ids: accepted && driveId != null ? [String(driveId)] : [],
      seen_count: accepted ? 1 : 0,
      last_seen_at: accepted
        ? (Number.isFinite(capturedAtRaw) ? capturedAtRaw / 1000 : Date.now() / 1000) : null,
    };
    if (accepted) {
      if (commitTurn) await commitTurn.wait;
      const committed = await addReportUnlessDuplicate(rec, dedupe);
      if (committed.duplicate) return duplicateResult(committed.duplicate);
      rec.id = committed.id;
    } else {
      rec.id = await addReport(rec);
    }
    return driveMode
      ? { analyzed: true, accepted: true, stored: true, found: true,
          duplicate: false, duplicate_of: null, decision, review: false,
          ...a, observation: { ...a }, detector, report: toDict(rec) }
      : toDict(rec);
    } finally {
      if (commitTurn) commitTurn.done();
    }
  }

  async function createCivicReport(fd) {
    const issueType = String(fd.get("issue_type") || "");
    if (!ISSUE_TYPE_SET.has(issueType) || issueType === "road_damage") {
      throw new Error("Choose garbage or open/damaged manhole for a civic report.");
    }
    const photo = fd.get("photo");
    if (!photo || !photo.size) throw new Error("Empty photo.");
    const latRaw = fd.get("lat"), lngRaw = fd.get("lng");
    const lat = latRaw != null && latRaw !== "" ? parseFloat(latRaw) : null;
    const lng = lngRaw != null && lngRaw !== "" ? parseFloat(lngRaw) : null;
    const gpsAccuracyRaw = parseFloat(fd.get("gps_accuracy"));
    const speedRaw = parseFloat(fd.get("speed"));
    const headingRaw = parseFloat(fd.get("heading"));
    const capturedAtRaw = parseInt(fd.get("captured_at_ms"), 10);
    const captureSource = normaliseManualCaptureSource(String(fd.get("capture_source") || ""));
    const locationSource = String(fd.get("location_source") || "") || null;
    const issueConfirmation = captureSource === "manual_camera"
      ? "user_selected_before_capture" : "user_selected_for_import";

    progress(pmsg("compress"));
    const dataUrl = await toDataUrl(photo, 2000, 0.85, true, 1);
    progress(pmsg("finalize"));
    const geo = lat != null ? await reverseGeocode(lat, lng).catch(() => null) : null;
    const address = (geo && geo.short) || null;
    const route = await routeOfficer(
      geo || address, lat, lng, gpsAccuracyRaw, headingRaw, speedRaw, issueType);
    const covered = !!route.routed;
    progress(pmsg("write"));
    const [subject, body] = covered
      ? draftCivicComplaint(issueType, lat, lng, address, route.officer_name, route,
          captureSource, locationSource)
      : [null, null];
    const capturedAt = Number.isFinite(capturedAtRaw) ? capturedAtRaw / 1000 : null;
    const rec = {
      created_at: Date.now() / 1000,
      captured_at: capturedAt,
      lat, lng, address,
      photo: await dataUrlToBlob(dataUrl),
      // Keep the original evidence even when routing is temporarily unavailable. The
      // resized copy above is only for fast lists/previews; retrying must not depend on
      // the user still having the source file.
      photo_full: photo,
      issue_type: issueType,
      issue_confirmation: issueConfirmation,
      report_origin: "user_reported",
      is_reportable: 1,
      is_pothole: 0,
      damage_type: "none",
      assessment: "manual",
      image_quality: null,
      on_drivable_surface: false,
      has_broken_edge_or_rim: false,
      has_depth_or_surface_loss: false,
      temporal_consistency: null,
      size: null,
      decision: "manual",
      description: civicIssueName(issueType, LANG()),
      email_subject: subject,
      email_body: body,
      status: covered ? "draft" : "unrouted",
      detection_model: null,
      image_detail: null,
      prompt_version: null,
      schema_version: null,
      evidence_count: 1,
      unrouted_reason: covered ? null : (route.unrouted_reason || "outside_area"),
      unrouted_body: covered ? null : (route.authority_name || null),
      officer_name: covered ? (route.officer_name || null) : null,
      officer_email: covered ? (route.officer_email || null) : null,
      authority_id: covered ? (route.authority_id || null) : null,
      authority_name: covered ? (route.authority_name || null) : null,
      authority_registry_version: covered ? (route.authority_registry_version || null) : null,
      delivery_channel: covered ? (route.delivery_channel || "email") : null,
      ward_code: covered ? (route.ward_code || null) : null,
      routing_source: covered ? (route.routing_source || null) : null,
      routing_match_field: covered ? (route.routing_match_field || null) : null,
      routing_match_value: covered ? (route.routing_match_value || null) : null,
      highway_ref: null,
      routing_pack_id: covered ? (route.routing_pack_id || null) : null,
      routing_pack_version: covered ? (route.routing_pack_version || null) : null,
      routing_pack_sha256: covered ? (route.routing_pack_sha256 || null) : null,
      routing_pack_state_code: covered ? (route.routing_pack_state_code || null) : null,
      region: covered ? (route.region || null) : null,
      ownership_unverified: covered ? !!route.ownership_unverified : null,
      handoff_name: covered ? (route.handoff_name || null) : null,
      handoff_url: covered ? (route.handoff_url || null) : null,
      handoff_package: covered ? (route.handoff_package || null) : null,
      alternate_handoff_name: covered ? (route.alternate_handoff_name || null) : null,
      alternate_handoff_url: covered ? (route.alternate_handoff_url || null) : null,
      whatsapp_url: covered ? (route.whatsapp_url || null) : null,
      helpline: covered ? (route.helpline || null) : null,
      requires_official_reference: covered ? !!route.requires_official_reference : false,
      official_grievance_id: null,
      submitted_at: null,
      tender_number: null,
      contractor: null,
      tender_note: null,
      tender_pack_id: null,
      tender_pack_version: null,
      tender_pack_sha256: null,
      tender_pack_state_code: null,
      sent_at: null,
      drive_id: null,
      capture_source: captureSource,
      location_source: locationSource,
      capture_time_source: Number.isFinite(capturedAtRaw)
        ? (captureSource === "manual_camera" ? "camera_return_time"
          : captureSource === "manual_import" ? "file_last_modified" : "provided_time")
        : null,
      source_event_key: null,
      source_event_keys: [],
      source_offset_s: null,
      gps_accuracy: Number.isFinite(gpsAccuracyRaw) ? gpsAccuracyRaw : null,
      speed_mps: Number.isFinite(speedRaw) ? speedRaw : null,
      heading: Number.isFinite(headingRaw) ? ((headingRaw % 360) + 360) % 360 : null,
      frame_quality: null,
      primary_frame_index: 0,
      debug_capture: false,
      dedupe_eligible: false,
      event_sightings: [],
      sighting_drive_ids: [],
      seen_count: 1,
      last_seen_at: capturedAt || Date.now() / 1000,
    };
    rec.id = await addReport(rec);
    return toDict(rec);
  }

  async function retryCivicRouting(rec) {
    if (!rec || rec.status !== "unrouted"
        || normaliseIssueType(rec.issue_type) === "road_damage") {
      throw new Error("Only an unrouted civic report can retry routing.");
    }
    if (!finiteCoord(rec.lat) || !finiteCoord(rec.lng)) {
      throw new Error("This report has no stored coordinates. Retake it with location enabled.");
    }
    if (!["jurisdiction_unavailable", "no_address_for_body"].includes(rec.unrouted_reason)) {
      throw new Error(
        "Retry cannot change this saved location or issue category. Retake the report at the correct location instead."
      );
    }

    const geo = await reverseGeocode(rec.lat, rec.lng).catch(() => null);
    const address = (geo && geo.short) || rec.address || null;
    const route = await routeOfficer(geo || address, rec.lat, rec.lng, rec.gps_accuracy,
      rec.heading, rec.speed_mps, rec.issue_type);
    rec.routing_retry_at = Date.now() / 1000;
    rec.routing_retry_count = Math.max(0, Number(rec.routing_retry_count) || 0) + 1;
    if (address) rec.address = address;

    if (!route.routed) {
      rec.unrouted_reason = route.unrouted_reason || rec.unrouted_reason || "outside_area";
      rec.unrouted_body = route.authority_name || null;
      await putReport(rec);
      return toDict(rec);
    }

    const [subject, body] = draftCivicComplaint(rec.issue_type, rec.lat, rec.lng,
      rec.address, route.officer_name, route, rec.capture_source, rec.location_source);
    const fields = [
      "officer_name", "officer_email", "authority_id", "authority_name",
      "authority_registry_version", "delivery_channel", "ward_code", "routing_source",
      "routing_match_field", "routing_match_value", "routing_pack_id",
      "routing_pack_version", "routing_pack_sha256", "routing_pack_state_code", "region",
      "ownership_unverified", "handoff_name", "handoff_url", "handoff_package",
      "alternate_handoff_name", "alternate_handoff_url", "whatsapp_url", "helpline",
      "requires_official_reference",
    ];
    for (const field of fields) {
      rec[field] = route[field] === undefined ? null : route[field];
    }
    rec.email_subject = subject;
    rec.email_body = body;
    rec.status = "draft";
    rec.unrouted_reason = null;
    rec.unrouted_body = null;
    await putReport(rec);
    return toDict(rec);
  }

  // Imports an accepted detection produced by Android's foreground Drive service.
  // Android owns capture and inference while the WebView is backgrounded; this layer
  // remains the single authority for routing, complaint drafting and the user's report
  // history. The stable source key makes retries safe if the WebView closes mid-sync.
  async function importNativeReport(native) {
    if (!native || typeof native !== "object") throw new Error("Native report missing.");
    const nativeId = Number(native.id);
    const lat = Number(native.lat), lng = Number(native.lng);
    if (!Number.isFinite(nativeId) || nativeId <= 0) throw new Error("Native report id missing.");
    if (!Number.isFinite(lat) || !Number.isFinite(lng) || Math.abs(lat) > 90 || Math.abs(lng) > 180) {
      throw new Error("Native report location is invalid.");
    }
    const sourceEventKey = String(native.source_event_key || `native:${nativeId}`).slice(0, 180);
    const gpsAccuracy = Number(native.gps_accuracy);
    const speed = Number(native.speed_mps);
    const heading = Number(native.heading);
    const geo = await reverseGeocode(lat, lng).catch(() => null);
    const address = (geo && geo.short) || native.address || `${lat.toFixed(5)}, ${lng.toFixed(5)}`;
    const route = await routeOfficer(
      geo || address, lat, lng,
      Number.isFinite(gpsAccuracy) ? gpsAccuracy : null,
      Number.isFinite(heading) ? heading : null,
      Number.isFinite(speed) ? speed : null
    );
    const covered = !!route.routed;
    const tender = covered && route.tender_eligible === true
      ? await jurisdictionOf(lat, lng)
          .then((w) => w && w.kind === "town" && w.lgd ? matchTender(address, w.lgd) : null)
          .catch(() => null)
      : null;
    const assessment = {
      reportable: native.is_reportable === true || Number(native.is_reportable) === 1,
      assessment: native.assessment || "clear",
      image_quality: native.image_quality || "usable",
      damage_type: native.damage_type || "pothole_cavity",
      on_drivable_surface: native.on_drivable_surface !== false,
      has_broken_edge_or_rim: native.has_broken_edge_or_rim !== false,
      has_depth_or_surface_loss: native.has_depth_or_surface_loss !== false,
      temporal_consistency: native.temporal_consistency || "consistent",
      size: native.size || null,
      description: native.description || "Reportable road damage detected during Drive Mode.",
    };
    const [subject, body] = covered
      ? draftEmail(assessment, lat, lng, address, route.officer_name, tender, route)
      : [null, null];
    const capturedAt = Number(native.captured_at);
    const offset = Number(native.source_offset_s);
    const driveId = native.drive_id == null ? null : String(native.drive_id);
    const debug = !!native.debug_capture;
    const rec = {
      created_at: Number(native.created_at) || Date.now() / 1000,
      lat, lng, address,
      photo: await dataUrlToBlob(native.photo_data_url),
      photo_full: await dataUrlToBlob(native.photo_full_data_url || native.photo_data_url),
      issue_type: "road_damage",
      report_origin: "ai_detection",
      is_reportable: assessment.reportable ? 1 : 0,
      is_pothole: assessment.damage_type === "pothole_cavity" ? 1 : 0,
      damage_type: assessment.damage_type, assessment: assessment.assessment,
      image_quality: assessment.image_quality,
      on_drivable_surface: assessment.on_drivable_surface,
      has_broken_edge_or_rim: assessment.has_broken_edge_or_rim,
      has_depth_or_surface_loss: assessment.has_depth_or_surface_loss,
      temporal_consistency: assessment.temporal_consistency,
      size: assessment.size, decision: native.decision || "accept",
      description: assessment.description, email_subject: subject, email_body: body,
      status: covered ? "draft" : "unrouted",
      detection_model: native.detection_model || S.model,
      image_detail: native.image_detail || S.detail,
      prompt_version: native.prompt_version || PROMPT_VERSION,
      schema_version: Number(native.schema_version) || SCHEMA_VERSION,
      evidence_count: Number(native.evidence_count) || 1,
      unrouted_reason: covered ? null : (route.unrouted_reason || "outside_area"),
      unrouted_body: covered ? null : (route.authority_name || null),
      officer_name: covered ? (route.officer_name || null) : null,
      officer_email: covered ? (route.officer_email || null) : null,
      authority_id: covered ? (route.authority_id || null) : null,
      authority_name: covered ? (route.authority_name || null) : null,
      authority_registry_version: covered ? (route.authority_registry_version || null) : null,
      delivery_channel: covered ? (route.delivery_channel || "email") : null,
      ward_code: covered ? (route.ward_code || null) : null,
      routing_source: covered ? (route.routing_source || null) : null,
      routing_match_field: covered ? (route.routing_match_field || null) : null,
      routing_match_value: covered ? (route.routing_match_value || null) : null,
      highway_ref: covered ? (route.highway_ref || null) : null,
      routing_pack_id: covered ? (route.routing_pack_id || null) : null,
      routing_pack_version: covered ? (route.routing_pack_version || null) : null,
      routing_pack_sha256: covered ? (route.routing_pack_sha256 || null) : null,
      routing_pack_state_code: covered ? (route.routing_pack_state_code || null) : null,
      region: covered ? (route.region || null) : null,
      ownership_unverified: covered ? !!route.ownership_unverified : null,
      handoff_name: covered ? (route.handoff_name || null) : null,
      handoff_url: covered ? (route.handoff_url || null) : null,
      handoff_package: covered ? (route.handoff_package || null) : null,
      alternate_handoff_name: covered ? (route.alternate_handoff_name || null) : null,
      alternate_handoff_url: covered ? (route.alternate_handoff_url || null) : null,
      whatsapp_url: covered ? (route.whatsapp_url || null) : null,
      helpline: covered ? (route.helpline || null) : null,
      requires_official_reference: covered ? !!route.requires_official_reference : false,
      official_grievance_id: null, submitted_at: null,
      tender_number: tender ? tender.tender_number : null,
      contractor: tender ? tender.contractor : null,
      tender_note: tender ? tender.note : null,
      tender_pack_id: tender ? (tender.tender_pack_id || null) : null,
      tender_pack_version: tender ? (tender.tender_pack_version || null) : null,
      tender_pack_sha256: tender ? (tender.tender_pack_sha256 || null) : null,
      tender_pack_state_code: tender ? (tender.tender_pack_state_code || null) : null,
      sent_at: null, drive_id: driveId, capture_source: "drive_live",
      source_event_key: sourceEventKey, source_event_keys: [sourceEventKey],
      captured_at: Number.isFinite(capturedAt) ? capturedAt : null,
      source_offset_s: Number.isFinite(offset) ? offset : null,
      gps_accuracy: Number.isFinite(gpsAccuracy) ? gpsAccuracy : null,
      speed_mps: Number.isFinite(speed) ? speed : null,
      heading: Number.isFinite(heading) ? ((heading % 360) + 360) % 360 : null,
      frame_quality: null, primary_frame_index: Number(native.primary_frame_index) || 0,
      debug_capture: debug, dedupe_eligible: !debug,
      event_sightings: [eventSighting({
        drive_id: driveId, lat, lng,
        source_offset_s: Number.isFinite(offset) ? offset : null,
        captured_at: Number.isFinite(capturedAt) ? capturedAt : null,
        gps_accuracy: Number.isFinite(gpsAccuracy) ? gpsAccuracy : null,
        speed_mps: Number.isFinite(speed) ? speed : null,
        heading: Number.isFinite(heading) ? heading : null,
        source_event_key: sourceEventKey,
      })],
      sighting_drive_ids: driveId ? [driveId] : [], seen_count: 1,
      last_seen_at: Number.isFinite(capturedAt) ? capturedAt : Date.now() / 1000,
    };
    const committed = await addReportUnlessDuplicate(rec, !debug);
    return { native_id: nativeId, id: committed.duplicate ? committed.duplicate.id : committed.id,
             duplicate: !!committed.duplicate };
  }


  async function openInGmail(rec) {
    // Always the routed officer. The app never sends; the user does, in their email app.
    // No fallback recipient: an unrouted report must not borrow Bengaluru's address.
    if (!rec.officer_email) {
      throw new Error("No responsible authority is known for this location, so there is nobody to address.");
    }
    const to = rec.officer_email;
    progress(pmsg("email"));
    if (NATIVE) {
      // Vanilla-JS WebView: the injected runtime exposes plugins via Capacitor.Plugins
      // and has no registerPlugin. Support both for bundler compatibility.
      const EmailComposer = Capacitor.registerPlugin
        ? Capacitor.registerPlugin("EmailComposer")
        : Capacitor.Plugins.EmailComposer;
      await EmailComposer.open({
        to: [to],
        subject: rec.email_subject || "",
        body: rec.email_body || "",
        // Full capture where we kept one; the working copy is only a fallback.
        attachments: [{ type: "base64", name: `${issueFileStem(rec.issue_type)}.jpg`,
                        path: await photoToBase64(rec.photo_full || rec.photo) }],
      });
    } else {
      // A browser cannot attach the saved photo to a draft, but it can still open a
      // real addressed composer. Keep this a deliberate external handoff: the user
      // reviews and presses Send, and can add the photo or use Share evidence.
      const query = new URLSearchParams({
        subject: rec.email_subject || "",
        body: rec.email_body || "",
      });
      const link = document.createElement("a");
      link.href = `mailto:${encodeURIComponent(to)}?${query.toString()}`;
      link.rel = "noopener noreferrer";
      link.style.display = "none";
      document.body.appendChild(link);
      link.click();
      link.remove();
    }
    rec.status = "queued";
    rec.handoff_opened_at = Date.now() / 1000;
    await putReport(rec);
    return toDict(rec);
  }

  const isOfficialHandoff = (rec) => !!rec && OFFICIAL_HANDOFF_CHANNELS.has(rec.delivery_channel);

  function routingPackForAuthority(authorityId) {
    const id = String(authorityId || "");
    if (PACK_ID_BY_AUTHORITY.has(id)) return PACK_ID_BY_AUTHORITY.get(id);
    const match = id.match(/^([a-z]{2})-/);
    if (!match) return null;
    const stateCode = match[1].toUpperCase();
    const candidates = Object.entries(SUPPORTED_STATE_PACKS)
      .filter(([, spec]) => spec.kind === "routing" && spec.state_code === stateCode)
      .map(([packId]) => packId);
    return candidates.length === 1 ? candidates[0] : null;
  }

  function currentOfficialRouteBinding(packId, authorityId, pack) {
    const municipal = MUNICIPAL_CITY_CONFIGS[packId];
    if (municipal) {
      const region = pack && pack.payload && Array.isArray(pack.payload.regions)
        ? pack.payload.regions.find((item) => item && item.id === municipal.region_id) : null;
      if (!region || authorityId !== municipal.authority_id
          || region.authority_id !== authorityId) return null;
      return { region: municipal.region_id, routing_source: municipal.routing_source };
    }
    if (packId === "in-dl-routing" && authorityId === "dl-pwd-sewa") {
      return { region: "delhi", routing_source: "osm_delhi_nct_boundary" };
    }
    if (packId === "in-wb-routing" && authorityId === "wb-kmc") {
      return { region: "kolkata", routing_source: "wb_udma_official_gis" };
    }
    if (packId === "in-wb-routing" && authorityId === "wb-statewide-unverified") {
      return { region: "west-bengal", routing_source: "osm_west_bengal_state_boundary" };
    }
    if (packId === "in-mh-routing" && authorityId === "mh-pmc") {
      return { region: "pune", routing_source: "pmc_official_gis" };
    }
    if (packId === "in-mh-routing" && authorityId === "mh-statewide-unverified") {
      return { region: "maharashtra", routing_source: "osm_maharashtra_state_boundary" };
    }
    if (packId === "in-mh-routing" && authorityId.startsWith("mh-")) {
      return { region: "mmr", routing_source: authorityId === "mh-mmr-unverified"
        ? "mmr_boundary_fallback" : "osm_ulb_boundary" };
    }
    return null;
  }

  async function savedMunicipalLocationMatches(rec, config, pack) {
    const lat = rec.lat, lng = rec.lng, accuracy = rec.gps_accuracy;
    if (!Number.isFinite(lat) || !Number.isFinite(lng)
        || !Number.isFinite(accuracy) || accuracy < 0 || accuracy > 30) return false;
    const region = pack && pack.payload && Array.isArray(pack.payload.regions)
      ? pack.payload.regions.find((item) => item && item.id === config.region_id) : null;
    if (!region) return false;
    if (config.routing_mode === "official_point_query") {
      if (rec.routing_match_field !== "official_accuracy_envelope") return false;
      const result = await officialPointRegionMatch(region, lat, lng, accuracy);
      return result.kind === "match";
    }
    if (config.routing_mode === "boundary") {
      if (!pointInGeometry(lng, lat, region.geometry)
          || geometryBoundaryDistanceMeters(lng, lat, region.geometry) <= accuracy) return false;
      for (const exclusion of region.exclusions) {
        if (pointInEnvelope(lat, lng, exclusion.bbox)
            || geometryBoundaryDistanceMeters(lng, lat,
              envelopeGeometry(exclusion.bbox)) <= accuracy) return false;
      }
      return true;
    }
    if (!pointInEnvelope(lat, lng, region.envelope)
        || rec.routing_match_field !== "structured_place") return false;
    const match = String(rec.routing_match_value || "").match(/^(city|municipality): (.+)$/);
    const aliases = new Set(config.place_aliases.map(normaliseAuthorityValue));
    return !!match && aliases.has(normaliseAuthorityValue(match[2]));
  }

  function savedBoundaryLocationMatches(rec, geometry, allowMissingAccuracy = false) {
    const lat = rec.lat, lng = rec.lng, accuracy = rec.gps_accuracy;
    if (!Number.isFinite(lat) || !Number.isFinite(lng)
        || !pointInGeometry(lng, lat, geometry)) return false;
    if (!Number.isFinite(accuracy)) return allowMissingAccuracy;
    return accuracy >= 0 && accuracy <= 30
      && geometryBoundaryDistanceMeters(lng, lat, geometry) > accuracy;
  }

  function savedNonMunicipalLocationMatches(rec, packId, authorityId, pack) {
    const payload = pack && pack.payload;
    if (packId === "in-dl-routing") {
      return savedBoundaryLocationMatches(rec, payload && payload.region && payload.region.geometry);
    }
    if (packId === "in-wb-routing") {
      const regions = payload && payload.regions;
      if (!regions) return false;
      if (authorityId === "wb-kmc") {
        return savedBoundaryLocationMatches(rec,
          regions.kmc && regions.kmc.geometry);
      }
      if (authorityId === "wb-statewide-unverified") {
        const stateMatches = savedBoundaryLocationMatches(rec,
          regions.west_bengal && regions.west_bengal.geometry);
        if (!stateMatches) return false;
        // A statewide fallback report must still be outside KMC by more than its stated
        // accuracy. Otherwise revalidation could silently change the exact recipient.
        const accuracy = rec.gps_accuracy;
        const kmcGeometry = regions.kmc && regions.kmc.geometry;
        if (!kmcGeometry || pointInGeometry(rec.lng, rec.lat, kmcGeometry)) return false;
        return geometryBoundaryDistanceMeters(rec.lng, rec.lat, kmcGeometry) > accuracy;
      }
      return false;
    }
    if (packId !== "in-mh-routing" || !payload || !payload.regions) return false;
    if (authorityId === "mh-pmc") {
      return savedBoundaryLocationMatches(rec,
        payload.regions.pmc && payload.regions.pmc.geometry);
    }
    if (authorityId === "mh-statewide-unverified") {
      return savedBoundaryLocationMatches(rec,
        payload.regions.maharashtra && payload.regions.maharashtra.geometry);
    }
    const mmr = payload.regions.mmr;
    if (!mmr || !savedBoundaryLocationMatches(rec, mmr.geometry,
      rec.delivery_channel === "bmc_quickfix")) return false;
    if (authorityId === "mh-mmr-unverified") return true;
    const boundary = mmr.authority_boundaries && mmr.authority_boundaries[authorityId];
    return savedBoundaryLocationMatches(rec, boundary && boundary.geometry,
      rec.delivery_channel === "bmc_quickfix");
  }

  async function savedOfficialRouteBinding(rec, packId, authorityId, pack) {
    const binding = currentOfficialRouteBinding(packId, authorityId, pack);
    if (!binding) return null;
    const provenanceFields = [
      "routing_pack_id", "routing_pack_version", "routing_pack_sha256",
      "routing_pack_state_code",
    ];
    const present = provenanceFields.filter((field) => rec[field] !== undefined
      && rec[field] !== null && rec[field] !== "");
    // Old reports predate pack provenance and are upgraded after validation. Newer
    // records must retain the complete binding; a partial mix is not trustworthy.
    if (present.length && present.length !== provenanceFields.length) return null;
    const municipal = MUNICIPAL_CITY_CONFIGS[packId];
    if (municipal && present.length !== provenanceFields.length) return null;
    // The statewide West Bengal route did not exist before this pack release, so there
    // is no legitimate provenance-free legacy record to upgrade.
    if (authorityId === "wb-statewide-unverified"
        && present.length !== provenanceFields.length) return null;
    if (present.length) {
      const resource = _statePackManifest && _statePackManifest.resources
        && _statePackManifest.resources[packId];
      if (!resource || rec.routing_pack_id !== packId
          || rec.routing_pack_state_code !== resource.state_code
          || !Number.isInteger(rec.routing_pack_version) || rec.routing_pack_version < 1
          || rec.routing_pack_version > resource.pack_version
          || !/^[0-9a-f]{64}$/.test(String(rec.routing_pack_sha256 || ""))) {
        return null;
      }
      const digestOwner = Object.values(_statePackManifest.resources)
        .find((item) => item.sha256 === rec.routing_pack_sha256);
      if (digestOwner && digestOwner.pack_id !== packId) return null;
    }
    if (rec.region && rec.region !== binding.region) return null;
    if (packId === "in-wb-routing" && rec.routing_source
        && rec.routing_source !== binding.routing_source) return null;
    if (packId === "in-wb-routing" && rec.routing_match_field
        && rec.routing_match_field !== "boundary") return null;
    if (authorityId === "wb-kmc" && rec.routing_match_value
        && rec.routing_match_value !== "wb_municipal_boundary:250299_0000001") return null;
    if (authorityId === "wb-statewide-unverified"
        && (rec.routing_match_field !== "boundary"
          || rec.routing_match_value !== "West Bengal (OpenStreetMap relation 1960177)")) {
      return null;
    }
    if (municipal && rec.routing_source && rec.routing_source !== binding.routing_source) return null;
    if (municipal && !await savedMunicipalLocationMatches(rec, municipal, pack)) return null;
    if (!municipal && !savedNonMunicipalLocationMatches(rec, packId, authorityId, pack)) return null;
    return binding;
  }

  const VERIFIED_HANDOFF_FIELDS = [
    "officer_name", "authority_id", "authority_name", "authority_registry_version",
    "routing_source", "region", "handoff_name", "handoff_url", "handoff_package",
    "alternate_handoff_name", "alternate_handoff_url", "whatsapp_url", "helpline",
    "requires_official_reference", "routing_pack_id", "routing_pack_version",
    "routing_pack_sha256", "routing_pack_state_code", "routing_match_field",
    "routing_match_value", "highway_ref",
  ];

  function applyVerifiedHandoff(rec, verified) {
    for (const field of VERIFIED_HANDOFF_FIELDS) {
      rec[field] = verified[field] === undefined ? null : verified[field];
    }
    return rec;
  }

  async function openNationalHighwayHandoff(rec) {
    if (!rec || rec.authority_id !== "in-national-highway") {
      throw new Error("This report is not bound to the National Highway handoff.");
    }
    const provenance = ["routing_pack_id", "routing_pack_version", "routing_pack_sha256",
      "routing_pack_state_code"];
    const present = provenance.filter((field) => rec[field] !== undefined
      && rec[field] !== null && rec[field] !== "");
    if (present.length !== provenance.length
        || !/^in-nh-e[0-9]{3}n[0-9]{2}$/.test(String(rec.routing_pack_id || ""))
        || rec.routing_pack_state_code !== "IN"
        || !Number.isInteger(rec.routing_pack_version) || rec.routing_pack_version < 1
        || !/^[0-9a-f]{64}$/.test(String(rec.routing_pack_sha256 || ""))) {
      throw new Error("This saved highway report has incomplete routing provenance.");
    }
    const current = await nationalHighwayRoute(
      rec.lat, rec.lng, rec.gps_accuracy, rec.heading, rec.speed_mps);
    if (!current || !current.routed || current.authority_id !== "in-national-highway"
        || current.region !== "national-highway" || !current.highway_ref) {
      throw new Error("This saved report no longer matches a verified National Highway tile.");
    }
    const oldRefs = new Set(String(rec.highway_ref || "").split(" / ").filter(Boolean));
    const currentRefs = String(current.highway_ref).split(" / ").filter(Boolean);
    if (oldRefs.size && !currentRefs.some((ref) => oldRefs.has(ref))) {
      throw new Error("This saved report's highway reference changed; review the location again.");
    }
    return { ...toDict(rec), ...current };
  }

  async function openBengaluruHandoff(rec) {
    if (normaliseIssueType(rec && rec.issue_type) === "road_damage") {
      throw new Error("Bengaluru road reports use the verified municipal email route.");
    }
    const match = String(rec.authority_id || "").match(/^ka-lgd-([0-9]+)$/);
    if (!match || rec.routing_pack_id !== "in-ka-routing"
        || rec.routing_pack_state_code !== "KA"
        || rec.routing_match_field !== "lgd"
        || String(rec.routing_match_value || "") !== match[1]) {
      throw new Error("This saved Bengaluru report has incomplete routing provenance.");
    }
    const pack = await loadStatePack("in-ka-routing");
    const entry = pack && pack.payload && pack.payload.bodies
      ? pack.payload.bodies[match[1]] : null;
    if (!entry || !BENGALURU_AUTHORITY_NAMES.has(normaliseAuthorityValue(entry.name))
        || normaliseAuthorityValue(rec.authority_name) !== normaliseAuthorityValue(entry.name)) {
      throw new Error("This saved report no longer matches a verified Bengaluru civic body.");
    }
    // Re-check the live civic boundary before launching the generic city service. This
    // prevents a locally altered saved record from borrowing a Bengaluru handoff while
    // retaining unrelated coordinates or another body's LGD code.
    const current = await kgisCivicJurisdiction(rec.lat, rec.lng);
    if (!current || current.kind !== "town" || String(current.lgd || "") !== match[1]) {
      throw new Error("This saved report no longer matches the Bengaluru civic jurisdiction.");
    }
    const verified = routeForIssue({
      ...toDict(rec),
      routed: true,
      officer_name: `Civic complaint desk, ${entry.name}`,
      officer_email: entry.email,
      authority_id: `ka-lgd-${match[1]}`,
      authority_name: entry.name,
      authority_registry_version: AUTHORITY_REGISTRY_VERSION,
      delivery_channel: "email",
      region: "karnataka",
      routing_source: "kgis",
      routing_match_field: "lgd",
      routing_match_value: match[1],
      ownership_unverified: false,
      requires_official_reference: false,
      tender_eligible: true,
      ...statePackProvenance("in-ka-routing"),
    }, rec.issue_type);
    if (!verified.handoff_url || !verified.handoff_url.startsWith("https://")) {
      throw new Error("The verified Bengaluru official handoff is unavailable.");
    }
    return verified;
  }

  async function openOfficialHandoff(rec) {
    if (!isOfficialHandoff(rec)) throw new Error("This report has no official app or portal handoff.");
    // v1.14 BMC records did not persist pack metadata. Keep them usable, but never trust
    // the URL saved in the report: reload the current app-pinned pack and find the same
    // stable authority ID inside its freshly validated registry.
    const legacyBmc = rec.delivery_channel === "bmc_quickfix";
    const authorityId = legacyBmc ? "mh-bmc" : rec.authority_id;
    if (authorityId === "in-national-highway") {
      return openNationalHighwayHandoff(rec);
    }
    if (/^ka-lgd-[0-9]+$/.test(String(authorityId || ""))) {
      return openBengaluruHandoff(rec);
    }
    const packId = routingPackForAuthority(authorityId);
    if (rec.routing_pack_id && rec.routing_pack_id !== packId) {
      throw new Error("This saved report's authority does not match its verified routing provenance.");
    }
    const pack = packId ? await loadStatePack(packId) : null;
    const current = pack && Array.isArray(pack.authorities)
      ? pack.authorities.find((authority) => authority.id === authorityId) : null;
    if (!current) {
      throw new Error("This saved report's verified official handoff is unavailable. Connect and try again.");
    }
    const binding = await savedOfficialRouteBinding(rec, packId, authorityId, pack);
    if (!binding) {
      throw new Error("This saved report's authority does not match its verified routing provenance.");
    }
    const verified = routeForIssue({
      ...toDict(rec),
      officer_name: `${current.handoff_name}, ${current.name}`,
      authority_id: current.id,
      authority_name: current.name,
      authority_registry_version: AUTHORITY_REGISTRY_VERSION,
      routing_source: rec.routing_source || binding.routing_source,
      region: binding.region,
      handoff_name: current.handoff_name,
      handoff_url: current.handoff_url,
      handoff_package: current.handoff_package || null,
      alternate_handoff_name: current.alternate_handoff_name || null,
      alternate_handoff_url: current.alternate_handoff_url || null,
      whatsapp_url: current.whatsapp_url || null,
      helpline: current.helpline || null,
      requires_official_reference: true,
      ...statePackProvenance(packId),
    }, rec.issue_type);
    if (!verified.handoff_url || !String(verified.handoff_url).startsWith("https://")) {
      throw new Error("The verified official handoff for this saved report is unavailable.");
    }
    return verified;
  }

  async function refreshAndPersistOfficialHandoff(rec) {
    const verified = await openOfficialHandoff(rec);
    applyVerifiedHandoff(rec, verified);
    await putReport(rec);
    return toDict(rec);
  }

  async function evidenceForReport(rec) {
    if (!rec || !ACCEPTED_REPORT_STATUSES.has(rec.status)) {
      throw new Error("Only an accepted report has shareable evidence.");
    }
    const source = await dataUrlToBlob(rec.photo_full || rec.photo);
    const wideUrl = await toDataUrl(source, 1280, 0.86, false, 1);
    const cropUrl = rec.capture_source && !isManualCaptureSource(rec.capture_source)
      ? await toDataUrl(source, 1280, 0.86, false, ROAD_BAND) : null;
    const base64 = wideUrl && wideUrl.split(",")[1];
    const cropBase64 = cropUrl && cropUrl.split(",")[1];
    if (!base64) throw new Error("The report photo could not be read.");
    const safeId = String(rec.id || "report").replace(/[^a-zA-Z0-9_-]/g, "");
    const recordedAt = Number.isFinite(rec.captured_at) ? rec.captured_at : rec.created_at;
    const captured = new Date(recordedAt * 1000);
    const when = Number.isNaN(captured.getTime()) ? "" : captured.toLocaleString("en-IN", {
      timeZone: "Asia/Kolkata", dateStyle: "medium", timeStyle: "medium",
    });
    const handoff = isOfficialHandoff(rec);
    const submissionTruth = rec.status === "sent"
      ? (rec.official_grievance_id
          ? `Locally marked submitted; official grievance/reference ID: ${rec.official_grievance_id}`
          : "Locally marked submitted by the user; this app has not independently verified delivery.")
      : (handoff
          ? "Prepared by an independent app; no official grievance submission is confirmed."
          : "Prepared by an independent app; email delivery is not confirmed.");
    const issueStem = issueFileStem(rec.issue_type);
    const meta = [
      rec.email_subject || `${civicIssueName(rec.issue_type)} report`,
      rec.email_body || "",
      when ? `${rec.capture_source === "manual_import" ? "Selected photo file date"
        : Number.isFinite(rec.captured_at) ? "Captured" : "Report created"} (IST): ${when}` : "",
      rec.capture_source === "manual_import"
        ? "Photo provenance: selected/imported by the user; original capture time is not independently verified."
        : rec.capture_source === "manual_camera"
          ? "Photo provenance: taken through the app camera." : "",
      Number.isFinite(rec.gps_accuracy) ? `GPS accuracy: ±${Math.round(rec.gps_accuracy)} m` : "",
      rec.authority_id === "in-national-highway"
        ? `Mapped road reference: ${rec.highway_ref || "National Highway"}; verify the maintaining agency in the official service.`
        : rec.authority_name
          ? `Suggested civic authority: ${rec.authority_name} (${normaliseIssueType(rec.issue_type) === "road_damage"
              ? "verify road ownership" : "verify civic jurisdiction and complaint category"})` : "",
      rec.ward_code ? `Suggested BMC ward: ${rec.ward_code} (verify in the official app)` : "",
      `Local event ID: ${safeId}`,
      submissionTruth,
    ].filter(Boolean).join("\n\n");
    return { name: `${issueStem}-${safeId}.jpg`, base64,
             crop_name: cropBase64 ? `${issueStem}-${safeId}-road-crop.jpg` : null,
             crop_base64: cropBase64, text: meta };
  }

  // ---------- dataset export ----------
  // A stored-entry ZIP, written by hand: JPEGs are already compressed, so there is
  // nothing to gain from deflate and no reason to pull in a zip library.
  const CRC = (() => {
    const t = new Uint32Array(256);
    for (let n = 0; n < 256; n++) {
      let c = n;
      for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      t[n] = c >>> 0;
    }
    return (buf) => {
      let c = 0xffffffff;
      for (let i = 0; i < buf.length; i++) c = t[(c ^ buf[i]) & 0xff] ^ (c >>> 8);
      return (c ^ 0xffffffff) >>> 0;
    };
  })();

  function zip(files) {
    const enc = new TextEncoder();
    const chunks = [], central = [];
    let offset = 0;
    const u16 = (n) => [n & 255, (n >> 8) & 255];
    const u32 = (n) => [n & 255, (n >> 8) & 255, (n >> 16) & 255, (n >>> 24) & 255];
    for (const f of files) {
      const name = enc.encode(f.name);
      const crc = CRC(f.data);
      const local = new Uint8Array([...u32(0x04034b50), ...u16(20), ...u16(0), ...u16(0),
        ...u16(0), ...u16(0), ...u32(crc), ...u32(f.data.length), ...u32(f.data.length),
        ...u16(name.length), ...u16(0)]);
      chunks.push(local, name, f.data);
      central.push(new Uint8Array([...u32(0x02014b50), ...u16(20), ...u16(20), ...u16(0),
        ...u16(0), ...u16(0), ...u16(0), ...u32(crc), ...u32(f.data.length), ...u32(f.data.length),
        ...u16(name.length), ...u16(0), ...u16(0), ...u16(0), ...u16(0), ...u32(0),
        ...u32(offset)]), name);
      offset += local.length + name.length + f.data.length;
    }
    const centralSize = central.reduce((n, c) => n + c.length, 0);
    const end = new Uint8Array([...u32(0x06054b50), ...u16(0), ...u16(0),
      ...u16(files.length), ...u16(files.length), ...u32(centralSize), ...u32(offset), ...u16(0)]);
    const all = [...chunks, ...central, end];
    const out = new Uint8Array(all.reduce((n, c) => n + c.length, 0));
    let at = 0;
    for (const c of all) { out.set(c, at); at += c.length; }
    return out;
  }

  const b64ToBytes = (b64) => {
    const bin = atob(b64);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  };
  const bytesToB64 = (bytes) => {
    let s = "";
    for (let i = 0; i < bytes.length; i += 0x8000) {
      s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    }
    return btoa(s);
  };

  // Exports only what a human actually labelled: a model verdict is not ground truth,
  // and a benchmark built from the detector's own opinions cannot measure the detector.
  async function exportDataset() {
    const labelled = (await allReports()).filter((r) =>
      normaliseIssueType(r.issue_type) === "road_damage" && r.human_label);
    if (!labelled.length) throw new Error("Nothing labelled yet. Open Review frames and tag some first.");
    const files = [], index = [];
    for (const r of labelled) {
      const name = `images/frame-${r.id}.jpg`;
      files.push({ name, data: b64ToBytes(await photoToBase64(r.photo)) });
      index.push({
        path: name,
        label: r.human_label,
        labelled_by: "owner",
        model_said: {
          is_reportable: r.is_reportable == null ? !!r.is_pothole : !!r.is_reportable,
          damage_type: damageTypeOf(r), assessment: assessmentOf(r),
          image_quality: r.image_quality || null,
          on_drivable_surface: r.on_drivable_surface == null ? null : !!r.on_drivable_surface,
          has_broken_edge_or_rim: r.has_broken_edge_or_rim == null ? null : !!r.has_broken_edge_or_rim,
          has_depth_or_surface_loss: r.has_depth_or_surface_loss == null ? null : !!r.has_depth_or_surface_loss,
          temporal_consistency: r.temporal_consistency || null,
          decision: r.decision || (r.status === "rejected" ? "reject" : "accept"),
          size: r.size, description: r.description,
        },
        detector: { model: r.detection_model || "legacy", detail: r.image_detail || null,
                    prompt_version: r.prompt_version || "legacy", schema_version: r.schema_version || 1,
                    evidence_count: r.evidence_count || 1 },
        lat: r.lat, lng: r.lng, address: r.address,
        drive_id: r.drive_id, captured_at: new Date(r.created_at * 1000).toISOString(),
      });
    }
    files.push({ name: "labels.json", data: new TextEncoder().encode(
      JSON.stringify({ exported_at: new Date().toISOString(), count: index.length, images: index }, null, 1)) });
    const bytes = zip(files);
    return { name: `road-damage-dataset-${Date.now()}.zip`, base64: bytesToB64(bytes),
             count: index.length, bytes: bytes.length };
  }

  // ---------- API dispatch ----------
  async function handle(path, opts) {
    const method = ((opts && opts.method) || "GET").toUpperCase();
    let m;
    if (path === "/api/health") {
      return { ai_configured: !!S.key, provider: "openai", delivery: "email_or_official_handoff", email_configured: true,
               detection_model: S.model, image_detail: S.detail, prompt_version: PROMPT_VERSION };
    }
    if (path === "/api/reports" && method === "GET") {
      // Without photo_full. The evidence copy is a 4000px JPEG and the list only shows a
      // thumbnail, so shipping it here cost about a megabyte per report on every return
      // to the home screen, and the cost grew with every pothole ever reported. The only
      // reader of it is the email attachment, which loads the record by id anyway.
      return (await allReports()).sort((a, b) => b.id - a.id).map(listDict);
    }
    if (path === "/api/reports" && method === "DELETE") {
      await op("readwrite", (s) => s.clear());
      await op("readwrite", (s) => s.clear(), "drives");
      await op("readwrite", (s) => s.clear(), "footage");
      await clearPackCache();
      return { ok: true };
    }
    if (path === "/api/drives" && method === "GET") return allDrives();
    if (path === "/api/footage" && method === "POST") {
      const fd = opts.body;
      const blob = fd.get("segment"), driveId = String(fd.get("drive_id"));
      const seq = parseInt(fd.get("seq"), 10) || 0;
      const recordingStartedRaw = parseInt(fd.get("recording_started_at_ms"), 10);
      const sourceOffsetRaw = parseInt(fd.get("source_offset_ms"), 10);
      if (!blob || !blob.size) throw new Error("Empty footage segment.");
      await putFootage({ key: `${driveId}#${String(seq).padStart(5, "0")}`, drive_id: driveId,
                         seq, blob, mime: blob.type || "video/mp4", bytes: blob.size,
                         recording_started_at_ms: Number.isFinite(recordingStartedRaw) ? recordingStartedRaw : null,
                         source_offset_s: Number.isFinite(sourceOffsetRaw) ? sourceOffsetRaw / 1000 : null,
                         at: Date.now() / 1000 });
      return { ok: true, bytes: blob.size };
    }
    // Summaries only: the caller asks for the blobs separately, because a drive's
    // footage is hundreds of megabytes and must never be materialised by accident.
    if (path === "/api/footage" && method === "GET") {
      const byDrive = {};
      for (const f of await allFootage()) {
        const clipStart = Number.isFinite(f.recording_started_at_ms)
          ? f.recording_started_at_ms / 1000 : f.at;
        const d = byDrive[f.drive_id] || (byDrive[f.drive_id] = {
          drive_id: f.drive_id, segments: 0, bytes: 0, mime: f.mime,
          started_at: clipStart || null, ended_at: f.at || clipStart || null,
        });
        d.segments++; d.bytes += f.bytes;
        if (clipStart) {
          d.started_at = d.started_at == null ? clipStart : Math.min(d.started_at, clipStart);
        }
        if (f.at || clipStart) {
          const clipEnd = f.at || clipStart;
          d.ended_at = d.ended_at == null ? clipEnd : Math.max(d.ended_at, clipEnd);
        }
      }
      return Object.values(byDrive);
    }
    if ((m = path.match(/^\/api\/footage\/([^/]+)\/blobs$/)) && method === "GET") {
      const segs = (await footageFor(decodeURIComponent(m[1]))).sort((a, b) => a.seq - b.seq);
      if (!segs.length) throw new Error("No footage stored for that drive.");
      return {
        mime: segs[0].mime,
        // `blobs` keeps the old API shape for callers/tests. `clips` carries the true
        // recorder timeline, including gaps and failed sequence numbers.
        blobs: segs.map((x) => x.blob),
        clips: segs.map((x) => ({ seq: x.seq, blob: x.blob,
          recording_started_at_ms: x.recording_started_at_ms || null,
          source_offset_s: Number.isFinite(x.source_offset_s) ? x.source_offset_s : null })),
      };
    }
    if ((m = path.match(/^\/api\/footage\/([^/]+)$/)) && method === "DELETE") {
      const id = decodeURIComponent(m[1]);
      for (const f of await footageFor(id)) await op("readwrite", (s) => s.delete(f.key), "footage");
      return { ok: true };
    }
    if (path === "/api/drives" && method === "POST") {
      const d = JSON.parse(opts.body);
      if (!d || !d.id) throw new Error("Drive id missing.");
      const alreadyIds = Array.isArray(d.already_ids)
        ? [...new Set(d.already_ids.map((x) => String(x).slice(0, 64)))] : [];
      await putDrive({ id: String(d.id), started_at: d.started_at || null,
                       ended_at: Date.now() / 1000, checked: d.checked | 0, found: d.found | 0,
                       already: Math.max(d.already | 0, alreadyIds.length), already_ids: alreadyIds,
                       gps_track: Array.isArray(d.gps_track) ? d.gps_track : [] });
      return { ok: true };
    }
    if ((m = path.match(/^\/api\/drives\/([^/]+)\/analysis$/)) && method === "POST") {
      const id = decodeURIComponent(m[1]);
      const stats = JSON.parse(opts.body || "{}");
      const prior = await getDrive(id) || {
        id, started_at: stats.started_at || null, ended_at: Date.now() / 1000,
        checked: 0, found: 0, already: 0, already_ids: [], gps_track: [],
      };
      const priorIds = Array.isArray(prior.already_ids) ? prior.already_ids : [];
      const incomingIds = Array.isArray(stats.already_ids) ? stats.already_ids : [];
      prior.already_ids = [...new Set([...priorIds, ...incomingIds]
        .map((x) => String(x).slice(0, 64)))];
      prior.already = Math.max(prior.already | 0, prior.already_ids.length);
      prior.analysis_checked = Math.max(0, stats.checked | 0);
      prior.analysis_found = Math.max(0, stats.found | 0);
      prior.analysis_already = Math.max(0, stats.already | 0, incomingIds.length);
      prior.analysis_at = Date.now() / 1000;
      await putDrive(prior);
      return { ok: true };
    }
    if (path === "/api/export" && method === "POST") return exportDataset();
    if ((m = path.match(/^\/api\/reports\/(\d+)\/evidence$/)) && method === "GET") {
      const rec = await getReport(m[1]);
      if (!rec) throw new Error("Report not found.");
      return evidenceForReport(rec);
    }
    if ((m = path.match(/^\/api\/reports\/(\d+)\/handoff$/)) && method === "GET") {
      const rec = await getReport(m[1]);
      if (!rec) throw new Error("Report not found.");
      return refreshAndPersistOfficialHandoff(rec);
    }
    if ((m = path.match(/^\/api\/reports\/(\d+)\/retry-routing$/)) && method === "POST") {
      const rec = await getReport(m[1]);
      if (!rec) throw new Error("Report not found.");
      return retryCivicRouting(rec);
    }
    if ((m = path.match(/^\/api\/reports\/(\d+)\/label$/)) && method === "POST") {
      const rec = await getReport(m[1]);
      if (!rec) throw new Error("Report not found.");
      const want = JSON.parse(opts.body).label;
      if (!["pothole_cavity", "failed_patch", "surface_breakup", "rut_or_depression",
            "other_road_damage", "not_reportable", "pothole", "not_pothole", null].includes(want)) {
        throw new Error("Bad label.");
      }
      rec.human_label = want;
      await putReport(rec);
      return toDict(rec);
    }
    if (path === "/api/report" && method === "POST") return createReport(opts.body, false);
    if (path === "/api/civic-report" && method === "POST") return createCivicReport(opts.body);
    if (path === "/api/frame" && method === "POST") return createReport(opts.body, true);
    if (path === "/api/native-report" && method === "POST") {
      return importNativeReport(JSON.parse(opts.body || "{}"));
    }
    if ((m = path.match(/^\/api\/reports\/(\d+)\/send$/)) && method === "POST") {
      const rec = await getReport(m[1]);
      if (!rec) throw new Error("Report not found.");
      if (rec.status === "unrouted") {
        // Say which of the four reasons it was. "Outside the area" is wrong and
        // confusing when the real problem is that the phone never got a GPS fix.
        const civic = normaliseIssueType(rec.issue_type) !== "road_damage";
        const civicErrors = {
          no_location: "This civic report has no location, so the app cannot choose a responsible authority. Retake it with location switched on.",
          location_uncertain: "The GPS fix is too imprecise to choose a civic authority safely. Retake it with a fresh, more accurate location.",
          rural_road: "This location is outside a supported urban body. The app saved the report but will not guess a civic recipient.",
          no_address_for_body: "The civic body is known, but the app has no verified complaint recipient for it.",
          unsupported_issue_type: "The suggested civic body has no verified complaint channel for this issue category.",
          jurisdiction_unavailable: "The required verified civic-routing data could not be downloaded or read. Check the connection and try again.",
          outside_area: "This civic issue is outside the enabled state, city, and urban-body coverage, so the app has no verified authority to open.",
        };
        const roadErrors = {
          no_location: "This report has no location, so there is no way to tell which office is responsible. Retake it with location switched on.",
          location_uncertain: "The GPS fix is too imprecise to choose an authority safely. Retake it with a fresh, more accurate location.",
          road_class_unknown: "The app could not check whether this road is a national highway, and it will not name a city officer for a road that may not be theirs. Try again when you have a signal.",
          national_highway: "This stretch is a national highway. It is maintained by NHAI or the state PWD National Highways division, not by the city or town body, so there is no municipal officer to address.",
          rural_road: "This road is outside every town boundary, so it belongs to the state PWD or a panchayat rather than a city body. The app will not guess an office.",
          no_address_for_body: "This town's body is known, but no official email address for it has been published, so there is no verified recipient to address.",
          jurisdiction_unavailable: "The required verified routing data could not be downloaded or read. Check the connection and try again; the app will not guess an authority.",
          outside_area: "This road damage is outside the enabled Maharashtra, West Bengal, Karnataka urban-body, Delhi NCT, Greater Chennai Corporation, Android-verified 2,053 km² Hyderabad CURE, and Ahmedabad Municipal Corporation coverage, so there is no verified authority to address.",
        };
        throw new Error((civic ? civicErrors : roadErrors)[rec.unrouted_reason]
          || "This report could not be routed to a responsible office, so there is nothing to send.");
      }
      // "queued" stays reopenable: canceling the external composer/app must not strand
      // the report or falsely mark it submitted.
      if (rec.status !== "draft" && rec.status !== "queued") throw new Error("This report is not a sendable draft.");
      return isOfficialHandoff(rec) ? openOfficialHandoff(rec) : openInGmail(rec);
    }
    if ((m = path.match(/^\/api\/reports\/(\d+)\/submitted$/)) && method === "POST") {
      const rec = await getReport(m[1]);
      if (!rec) throw new Error("Report not found.");
      if (rec.status !== "draft" && rec.status !== "queued" && rec.status !== "sent") {
        throw new Error("This report cannot be marked submitted.");
      }
      const body = JSON.parse(opts.body || "{}");
      const reference = String(body.official_grievance_id || "").trim().slice(0, 100);
      const referenceRequired = isOfficialHandoff(rec)
        && rec.requires_official_reference !== false;
      if (referenceRequired && reference.length < 4) {
        if (rec.delivery_channel === "bmc_quickfix") {
          throw new Error("Enter the official BMC grievance ID before marking this submitted.");
        }
        const authority = rec.authority_name || "the authority";
        throw new Error(`Enter the official grievance/reference ID from ${authority} before marking this submitted.`);
      }
      rec.official_grievance_id = reference || null;
      rec.status = "sent";
      rec.submitted_at = Date.now() / 1000;
      rec.sent_at = rec.submitted_at;
      await putReport(rec);
      return toDict(rec);
    }
    if ((m = path.match(/^\/api\/reports\/(\d+)\/handoff-opened$/)) && method === "POST") {
      const rec = await getReport(m[1]);
      if (!rec) throw new Error("Report not found.");
      if (!isOfficialHandoff(rec)) throw new Error("This report has no official app or portal handoff.");
      if (rec.status !== "draft" && rec.status !== "queued") {
        throw new Error("This report cannot record an official handoff.");
      }
      applyVerifiedHandoff(rec, await openOfficialHandoff(rec));
      rec.status = "queued";
      rec.handoff_opened_at = Date.now() / 1000;
      await putReport(rec);
      return toDict(rec);
    }
    if ((m = path.match(/^\/api\/reports\/(\d+)$/))) {
      const rec = await getReport(m[1]);
      if (!rec) throw new Error("Report not found.");
      if (method === "PATCH") {
        if (rec.status !== "draft" && rec.status !== "queued") throw new Error("Only drafts can be edited.");
        const upd = JSON.parse(opts.body);
        rec.email_subject = upd.email_subject;
        rec.email_body = upd.email_body;
        await putReport(rec);
        return toDict(rec);
      }
      if (method === "DELETE") {
        if (rec.status === "sent") throw new Error("Sent reports cannot be discarded.");
        await delReport(rec.id);
        return { ok: true };
      }
    }
    throw new Error(`Unhandled: ${method} ${path}`);
  }

  // Native hardware back button routes through window.handleAppBack (defined by the UI).
  if (NATIVE) {
    try {
      const App = Capacitor.Plugins.App;
      if (App && App.addListener) {
        App.addListener("backButton", () => {
          if (!(window.handleAppBack && window.handleAppBack())) App.exitApp();
        });
      }
    } catch (e) {}
  }

  // Pure helpers, exposed for tests. These are references, not copies: a test exercises
  // exactly the code that runs in production. Nothing here holds state or a secret.
  const __pure = { inCoverage, peekVerdict, peekReject, rejectedVerdict, decisionFor,
                   damageTypeOf, assessmentOf, normaliseModel, normaliseDetail,
                   normaliseIssueType, civicIssueName, issueFileStem,
                   buildDetectionRequest, ASSESS_SCHEMA, DETECT_PROMPT, PROMPT_VERSION,
                   SCHEMA_VERSION, MAX_DETECTION_IMAGES, ROAD_BAND, averageLuminance,
                   distMeters, roadEventMatch, sameRoadEvent, findDuplicateReport,
                   draftEmail, dataUrlToBlob, photoToBase64, toDict, listDict,
                   warrantyFor, shortlistFor, matchTenderFor: matchTender,
                   mumbaiWardFromName, mumbaiFromGeocode, evidenceForReport,
                   normaliseAuthorityValue, validateAuthorityRegistry,
                   validateOfficialHandoffRegistry,
                   matchedMmrAuthorities, containingMmrAuthorities, bmcWardFromBoundary,
                   pointInGeometry, geometryBoundaryDistanceMeters,
                   validMmrAuthorityBoundaries,
                   validateStatePackManifest, getStatePackManifest, resolvePackUrl,
                   loadStatePack, pruneStatePacks, resetStatePackMemory,
                   sha256Bytes, statePackProvenance,
                   validateHighwayManifest, getHighwayPackManifest, loadHighwayTile,
                   validateHighwayTile, highwayTileIdFor, matchHighwayTile,
                   nationalHighwayRoute, highwayPackProvenance, openNationalHighwayHandoff,
                   maharashtraCoverage,
                   delhiCoverage, delhiRouteFromGeocode, inDelhiEnvelope,
                   westBengalCoverage, kolkataCoverage, kolkataRouteFromGeocode,
                   isWestBengalGeocode, inWestBengalRoutingEnvelope,
                   municipalCityCoverage, municipalCityRouteFromGeocode,
                   gpsAccuracyEnvelope, officialArcGisCount, officialPointRegionMatch,
                   savedMunicipalLocationMatches,
                   validateMunicipalCityPayload, MUNICIPAL_CITY_CONFIGS,
                   maharashtraRouteFromGeocode, inMaharashtraRoutingEnvelope,
                   isKarnatakaGeocode, routeOfficer, routeForIssue,
                   MMR_AUTHORITIES, PMC_AUTHORITY, MMR_FALLBACK_AUTHORITY,
                   MAHARASHTRA_STATE_AUTHORITY, KMC_AUTHORITY,
                   WEST_BENGAL_STATE_AUTHORITY,
                   DELHI_PWD_AUTHORITY, OFFICIAL_AUTHORITIES,
                   NATIONAL_HIGHWAY_AUTHORITY,
                   DELHI_GEOMETRY_SHA256, KMC_GEOMETRY_SHA256,
                   WEST_BENGAL_STATE_GEOMETRY_SHA256,
                   MAHARASHTRA_STATE_GEOMETRY_SHA256,
                   AUTHORITY_REGISTRY_VERSION, ISSUE_TYPES, CIVIC_HANDOFF_OVERRIDES,
                   BENGALURU_HANDOFF, BENGALURU_AUTHORITY_NAMES,
                   GENERAL_CIVIC_AUTHORITY_IDS };

  window.StandaloneAPI = { __pure, handle, prewarm };

  // The home screen remains usable without an AI key because Garbage and Manhole are
  // explicit user reports. Pothole and Drive open Settings when their key is missing.
})();
