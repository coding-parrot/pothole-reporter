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
          detect: "AI checking for reportable road damage...", finalize: "Finalizing address and contract...",
          write: "Writing the complaint...", email: "Opening your email app..." },
    kn: { compress: "ಫೋಟೋ ಸಂಕುಚಿಸಲಾಗುತ್ತಿದೆ...", capture: "ಫ್ರೇಮ್ ಸೆರೆಹಿಡಿಯಲಾಗುತ್ತಿದೆ...",
          detect: "AI ವರದಿ ಮಾಡಬಹುದಾದ ರಸ್ತೆ ಹಾನಿ ಪರಿಶೀಲಿಸುತ್ತಿದೆ...", finalize: "ವಿಳಾಸ ಮತ್ತು ಗುತ್ತಿಗೆ ಖಚಿತಪಡಿಸಲಾಗುತ್ತಿದೆ...",
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

  // Maharashtra complaints remain inside each authority's official workflow. This app
  // only prepares evidence and opens a verified app, portal or email composer; it has no
  // civic credentials and never claims that opening a channel submitted a case.
  const BMC_QUICKFIX_URL = "https://play.google.com/store/apps/details?id=com.bmc.potholequickfix";
  const BMC_QUICKFIX_PACKAGE = "com.bmc.potholequickfix";
  const BMC_WHATSAPP_URL = "https://wa.me/918999228999";
  const BMC_HELPLINE = "1916";
  const AAPLE_SARKAR_URL = "https://grievances.maharashtra.gov.in/en";
  const AUTHORITY_REGISTRY_VERSION = 3;
  const OFFICIAL_HANDOFF_CHANNELS = new Set(["official_handoff", "bmc_quickfix"]);
  const MUMBAI_STATES = new Set(["maharashtra", "महाराष्ट्र"]);
  const WEST_BENGAL_STATES = new Set(["west bengal", "পশ্চিমবঙ্গ"]);
  const MUMBAI_DISTRICTS = new Set([
    "mumbai city district", "mumbai city", "mumbai suburban district", "mumbai suburban",
    "मुंबई शहर जिल्हा", "मुंबई शहर", "मुंबई उपनगर जिल्हा", "मुंबई उपनगर",
  ]);
  const MUMBAI_WARDS = new Set([
    "A", "B", "C", "D", "E", "F/N", "F/S", "G/N", "G/S", "H/E", "H/W",
    "K/E", "K/W", "L", "M/E", "M/W", "N", "P/N", "P/S", "R/C", "R/N",
    "R/S", "S", "T",
  ]);

  // The MMR roster is the one published by MMRDA. Civic polygons alone may select a
  // specific authority. Nominatim's structured place fields remain display-only clues;
  // they never replace containment or prove who owns a road.
  const MMR_AUTHORITIES = [
    {
      id: "mh-bmc", name: "Brihanmumbai Municipal Corporation",
      aliases: ["mumbai", "greater mumbai", "brihanmumbai", "bombay", "मुंबई", "बृहन्मुंबई"],
      handoff_name: "BMC Pothole QuickFix", handoff_url: BMC_QUICKFIX_URL,
      handoff_package: BMC_QUICKFIX_PACKAGE, whatsapp_url: BMC_WHATSAPP_URL,
      helpline: BMC_HELPLINE,
    },
    {
      id: "mh-tmc", name: "Thane Municipal Corporation",
      aliases: ["thane", "thana", "ठाणे"], handoff_name: "Aaple Sarkar",
      handoff_url: AAPLE_SARKAR_URL,
    },
    {
      id: "mh-kdmc", name: "Kalyan-Dombivli Municipal Corporation",
      aliases: ["kalyan-dombivli", "kalyan-dombivali", "kalyan dombivli", "kalyan dombivali", "kalyan", "dombivli", "dombivali", "कल्याण", "डोंबिवली"],
      handoff_name: "KDMC Citizen Grievance",
      handoff_url: "https://kdmc.gov.in/kdmc/CitizenHome.html",
    },
    {
      id: "mh-nmmc", name: "Navi Mumbai Municipal Corporation",
      aliases: ["navi mumbai", "new bombay", "नवी मुंबई"], handoff_name: "My NMMC",
      handoff_url: "https://online.nmmc.gov.in/Grievance", handoff_package: "com.newnmmc.app",
    },
    {
      id: "mh-umc", name: "Ulhasnagar Municipal Corporation",
      aliases: ["ulhasnagar", "ulhas nagar", "उल्हासनगर"], handoff_name: "UMC official website",
      handoff_url: "https://www.umc.gov.in/",
    },
    {
      id: "mh-bncmc", name: "Bhiwandi-Nizampur Municipal Corporation",
      aliases: ["bhiwandi-nizampur", "bhiwandi-nizamapur", "bhiwandi nizampur", "bhiwandi nizamapur", "bhiwandi", "bhivandi", "nizampur", "भिवंडी", "निजामपूर"],
      handoff_name: "BNCMC Grievance Portal", handoff_url: "https://grp.bncmc.gov.in/en/home",
    },
    {
      id: "mh-vvcmc", name: "Vasai-Virar City Municipal Corporation",
      aliases: ["vasai-virar", "vasai virar", "vasai", "virar", "nala sopara", "nalasopara", "वसई", "विरार", "नालासोपारा"],
      handoff_name: "VClick - VVCMC", handoff_url: "https://onlinevvcmc.in/CRM/",
      whatsapp_url: "https://wa.me/919665877727",
    },
    {
      id: "mh-mbmc", name: "Mira-Bhayandar Municipal Corporation",
      aliases: ["mira-bhayandar", "mira bhayandar", "mira bhayander", "mira road", "bhayandar", "bhayander", "मीरा-भाईंदर", "भाईंदर"],
      handoff_name: "MyMBMC Complaint Portal", handoff_url: "https://crm.mymbmc.in/",
    },
    {
      id: "mh-panvel", name: "Panvel Municipal Corporation",
      aliases: ["panvel", "new panvel", "पनवेल"], handoff_name: "Panvel Connect",
      handoff_url: "https://grievance.panvelcorporation.in/",
      helpline: "1800227701",
    },
    {
      id: "mh-ambarnath", name: "Ambarnath Municipal Council",
      aliases: ["ambarnath", "ambernath", "अंबरनाथ"], officer_email: "coud.ambernath@maharashtra.gov.in",
    },
    {
      id: "mh-badlapur", name: "Kulgaon-Badlapur Municipal Council",
      aliases: ["kulgaon-badlapur", "kulgaon-badalapur", "kulgaon badlapur", "kulgaon badalapur", "badlapur", "badalapur", "kulgaon", "बदलापूर", "कुळगाव-बदलापूर"],
      officer_email: "support@kbmc.gov.in", helpline: "18002129032",
    },
    {
      id: "mh-matheran", name: "Matheran Municipal Council",
      aliases: ["matheran", "माथेरान"], officer_email: "mcomatheran@gmail.com",
    },
    {
      id: "mh-karjat", name: "Karjat Municipal Council",
      aliases: ["karjat", "कर्जत"], officer_email: "karjatcouncil@gmail.com",
    },
    {
      id: "mh-khopoli", name: "Khopoli Municipal Council",
      aliases: ["khopoli", "खोपोली"], officer_email: "cokmckhopoli@gmail.com",
    },
    {
      id: "mh-pen", name: "Pen Municipal Council",
      aliases: ["pen", "पेण"], officer_email: "copenmc@gmail.com",
    },
    {
      id: "mh-uran", name: "Uran Municipal Council",
      aliases: ["uran", "उरण"], officer_email: "uranmunicipal@gmail.com",
    },
    {
      id: "mh-alibag", name: "Alibag Municipal Council",
      aliases: ["alibag", "alibaug", "अलिबाग"], officer_email: "nagarparishadalibag@gmail.com",
    },
    {
      id: "mh-palghar", name: "Palghar Municipal Council",
      aliases: ["palghar", "पालघर"], handoff_name: "Aaple Sarkar",
      handoff_url: AAPLE_SARKAR_URL,
    },
    {
      id: "mh-khalapur", name: "Khalapur Nagar Panchayat",
      aliases: ["khalapur", "खालापूर"], officer_email: "cokmckhalapur@gmail.com",
    },
  ];

  const PMC_AUTHORITY = {
    id: "mh-pmc", name: "Pune Municipal Corporation", handoff_name: "PMC Road Mitra",
    handoff_url: "https://play.google.com/store/apps/details?id=com.nyatitechnologies.pmcroadmitra",
    handoff_package: "com.nyatitechnologies.pmcroadmitra",
    alternate_handoff_name: "PMC CARE", alternate_handoff_url: "https://pmccare.in/",
    helpline: "18001030222",
  };

  const KMC_AUTHORITY = {
    id: "wb-kmc", name: "Kolkata Municipal Corporation",
    aliases: ["kolkata", "calcutta", "kolkata municipal corporation", "কলকাতা", "কলকাতা পৌরসংস্থা"],
    handoff_name: "KMC Grievance 2.0",
    handoff_url: "https://kmc.wb.gov.in/citizen/language-selection",
    handoff_package: "com.kmc.app",
    alternate_handoff_name: "KMC APP",
    alternate_handoff_url: "https://play.google.com/store/apps/details?id=com.kmc.app",
    whatsapp_url: "https://wa.me/918335988888",
    helpline: "18003453375",
  };

  // Delhi's civic and road-maintenance boundaries are not road-ownership maps. PWD
  // Sewa is the cross-agency road grievance workflow: it can forward a complaint to
  // the appropriate Delhi body instead of this client pretending to know the owner.
  const DELHI_PWD_AUTHORITY = {
    id: "dl-pwd-sewa", name: "Delhi road grievance coordination",
    handoff_name: "PWD Sewa",
    handoff_url: "https://www.pwddelhi.gov.in/sewa/complaint",
    handoff_package: "com.sis.pwdsewaapp",
    alternate_handoff_name: "Delhi PGMS",
    alternate_handoff_url: "https://pgms.delhi.gov.in/",
    whatsapp_url: "https://wa.me/918130188222",
    helpline: "1908",
  };

  const MMR_FALLBACK_AUTHORITY = {
    id: "mh-mmr-unverified", name: "MMR road authority (verify in Aaple Sarkar)",
    handoff_name: "Aaple Sarkar", handoff_url: AAPLE_SARKAR_URL,
  };
  const OFFICIAL_AUTHORITIES = [
    ...MMR_AUTHORITIES, PMC_AUTHORITY, MMR_FALLBACK_AUTHORITY, KMC_AUTHORITY,
    DELHI_PWD_AUTHORITY,
  ];
  validateOfficialHandoffRegistry(OFFICIAL_AUTHORITIES);
  const OFFICIAL_AUTHORITY_INDEX = new Map(
    OFFICIAL_AUTHORITIES.map((authority) => [authority.id, authority]),
  );
  const MMR_DIRECT_AUTHORITY_IDS = new Set([
    "mh-bmc", "mh-tmc", "mh-kdmc", "mh-nmmc", "mh-umc", "mh-bncmc",
    "mh-vvcmc", "mh-mbmc", "mh-panvel", "mh-ambarnath", "mh-badlapur",
  ]);
  const MMR_FALLBACK_AUTHORITY_IDS = new Set(
    MMR_AUTHORITIES.map((authority) => authority.id)
      .filter((id) => !MMR_DIRECT_AUTHORITY_IDS.has(id)),
  );

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
  // A failure is never cached. Caching one meant a single slow read of a file that ships
  // inside the APK disabled routing for the rest of the session, and the app then refused
  // every report as having no address for its body. Local reads were measured at over four
  // seconds on a cold start, so this is not a remote possibility.
  async function bodies() {
    if (_bodies) return _bodies;
    try {
      const res = await fetchWithTimeout("karnataka-bodies.json", {}, 15000);
      const loaded = (await readJson(res)).bodies;
      if (loaded && Object.keys(loaded).length) { _bodies = loaded; return _bodies; }
    } catch (e) { /* fall through and retry on the next call */ }
    return {};
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
    for (const authority of authorities || []) {
      if (!authority || !authority.id || ids.has(authority.id)) {
        throw new Error("Duplicate or missing official authority ID.");
      }
      ids.add(authority.id);
      if (!authority.name) throw new Error(`Official authority ${authority.id} has no name.`);
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
      if (authority.handoff_package && !packageName.test(authority.handoff_package)) {
        throw new Error(`Official authority ${authority.id} has an invalid Android package.`);
      }
      if (authority.whatsapp_url
          && !/^https:\/\/wa\.me\/[1-9][0-9]{7,14}$/.test(authority.whatsapp_url)) {
        throw new Error(`Official authority ${authority.id} has an invalid WhatsApp route.`);
      }
      if (authority.helpline && !/^[0-9]{3,15}$/.test(authority.helpline)) {
        throw new Error(`Official authority ${authority.id} has an invalid helpline.`);
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
  validateAuthorityRegistry();

  const MMR_ALIAS_INDEX = (() => {
    const index = new Map();
    for (const authority of MMR_AUTHORITIES) {
      for (const alias of authority.aliases) index.set(normaliseAuthorityValue(alias), authority);
    }
    return index;
  })();

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

  function validMmrAuthorityBoundaries(mmr) {
    const boundaries = mmr && mmr.authority_boundaries;
    if (!boundaries || typeof boundaries !== "object") return false;
    const entries = Object.entries(boundaries);
    const sameIds = (values, expected) => Array.isArray(values)
      && values.length === expected.size
      && values.every((id) => expected.has(id));
    if (entries.length !== MMR_DIRECT_AUTHORITY_IDS.size
        || !entries.every(([id]) => MMR_DIRECT_AUTHORITY_IDS.has(id))
        || !sameIds(mmr.boundary_complete_authority_ids, MMR_DIRECT_AUTHORITY_IDS)
        || !sameIds(mmr.boundary_missing_authority_ids, MMR_FALLBACK_AUTHORITY_IDS)) {
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

  let _maharashtraCoverage = null, _maharashtraCoveragePromise = null;
  async function maharashtraCoverage() {
    if (_maharashtraCoverage) return _maharashtraCoverage;
    if (_maharashtraCoveragePromise) return _maharashtraCoveragePromise;
    _maharashtraCoveragePromise = (async () => {
      try {
        const res = await fetchWithTimeout("maharashtra-coverage.json", {}, 15000);
        if (!res.ok) return null;
        const data = await readJson(res);
        if (data && data.version === 1 && data.regions
            && data.regions.mmr && data.regions.pmc
            && hasCoverageGeometry(data.regions.mmr.geometry)
            && hasCoverageGeometry(data.regions.pmc.geometry)
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

  // The relevance envelope is intentionally wider than Delhi NCT. A point in nearby
  // Noida, Gurugram, Ghaziabad or Faridabad must get an explicit outside-area result,
  // not fall through to an unrelated state's GIS. Only the pinned polygon can accept.
  const DELHI_ENVELOPE = { minLat: 28.10, maxLat: 29.10, minLng: 76.65, maxLng: 77.65 };
  const DELHI_GEOMETRY_SHA256 = "3462ba68bdbbc1fdebc99403aa9e1f9db5e0b78e30ca138b2d25df7463506ab3";
  const inDelhiEnvelope = (lat, lng) => Number.isFinite(lat) && Number.isFinite(lng)
    && lat >= DELHI_ENVELOPE.minLat && lat <= DELHI_ENVELOPE.maxLat
    && lng >= DELHI_ENVELOPE.minLng && lng <= DELHI_ENVELOPE.maxLng;

  const KOLKATA_ENVELOPE = { minLat: 22.35, maxLat: 22.70, minLng: 88.15, maxLng: 88.55 };
  // This digest is over JSON.stringify(region.geometry). IDs and a closed ring are not
  // enough: a valid-shaped but wrong polygon could otherwise send Howrah to KMC. Updating
  // the boundary is therefore an explicit code-and-data release, never a silent asset swap.
  const KMC_GEOMETRY_SHA256 = "fa9e157d8cdc8d918dd934a77a5dcde375d3108598412cb8ca3e19ca2d916bf5";
  const isWestBengalGeocode = (geo) => !!geo
    && String(geo.country_code || "").toLowerCase() === "in"
    && WEST_BENGAL_STATES.has(normaliseAuthorityValue(geo.state));
  const inKolkataEnvelope = (lat, lng) => Number.isFinite(lat) && Number.isFinite(lng)
    && lat >= KOLKATA_ENVELOPE.minLat && lat <= KOLKATA_ENVELOPE.maxLat
    && lng >= KOLKATA_ENVELOPE.minLng && lng <= KOLKATA_ENVELOPE.maxLng;

  async function sha256Hex(value) {
    if (!(window.crypto && window.crypto.subtle && window.TextEncoder)) return null;
    const bytes = new TextEncoder().encode(value);
    const digest = await crypto.subtle.digest("SHA-256", bytes);
    return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
  }

  let _delhiCoverage = null, _delhiCoveragePromise = null;
  async function delhiCoverage() {
    if (_delhiCoverage) return _delhiCoverage;
    if (_delhiCoveragePromise) return _delhiCoveragePromise;
    _delhiCoveragePromise = (async () => {
      try {
        const res = await fetchWithTimeout("delhi-coverage.json", {}, 15000);
        if (!res.ok) return null;
        const data = await readJson(res);
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

  let _kolkataCoverage = null, _kolkataCoveragePromise = null;
  async function kolkataCoverage() {
    if (_kolkataCoverage) return _kolkataCoverage;
    if (_kolkataCoveragePromise) return _kolkataCoveragePromise;
    _kolkataCoveragePromise = (async () => {
      try {
        const res = await fetchWithTimeout("kolkata-coverage.json", {}, 15000);
        if (!res.ok) return null;
        const data = await readJson(res);
        const region = data && data.region;
        const geometryDigest = region && hasCoverageGeometry(region.geometry)
          ? await sha256Hex(JSON.stringify(region.geometry)) : null;
        if (data && data.version === 1 && region
            && region.authority_id === KMC_AUTHORITY.id
            && String(region.ulb_code) === "250299"
            && String(region.mun_id) === "250299_0000001"
            && geometryDigest === KMC_GEOMETRY_SHA256) {
          _kolkataCoverage = data;
        }
      } catch (e) { /* fail closed; a retry is allowed on the next report */ }
      return _kolkataCoverage;
    })();
    const result = await _kolkataCoveragePromise;
    _kolkataCoveragePromise = null;
    return result;
  }

  async function kolkataRouteFromGeocode(geo, lat, lng, gpsAccuracy) {
    if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null;
    const relevant = inKolkataEnvelope(lat, lng) || isWestBengalGeocode(geo);
    if (!relevant) return null;
    const coverage = await kolkataCoverage();
    if (!coverage) return unroutedRoute("jurisdiction_unavailable");

    const geometry = coverage.region.geometry;
    const inside = pointInGeometry(lng, lat, geometry);
    const enforceGpsAccuracy = gpsAccuracy !== undefined;
    if (enforceGpsAccuracy
        && (!Number.isFinite(gpsAccuracy) || gpsAccuracy < 0 || gpsAccuracy > 30)) {
      return unroutedRoute("location_uncertain");
    }
    if (Number.isFinite(gpsAccuracy)
        && geometryBoundaryDistanceMeters(lng, lat, geometry) <= gpsAccuracy) {
      return unroutedRoute("location_uncertain");
    }
    if (!inside) return unroutedRoute("outside_area");

    return authorityRoute(KMC_AUTHORITY, {
      routing_source: "wb_udma_official_gis",
      match_field: "boundary",
      match_value: "wb_municipal_boundary:250299_0000001",
      region: "kolkata",
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
    };
  }

  async function maharashtraRouteFromGeocode(geo, lat, lng, gpsAccuracy) {
    if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null;
    const coverage = await maharashtraCoverage();
    if (!coverage) {
      return isMaharashtraGeocode(geo) ? unroutedRoute("jurisdiction_unavailable") : null;
    }
    const inPmc = pointInGeometry(lng, lat, coverage.regions.pmc.geometry);
    const inMmr = pointInGeometry(lng, lat, coverage.regions.mmr.geometry);
    const enforceGpsAccuracy = gpsAccuracy !== undefined;
    if (enforceGpsAccuracy
        && (!Number.isFinite(gpsAccuracy) || gpsAccuracy < 0 || gpsAccuracy > 30)) {
      return (inPmc || inMmr
        || isMaharashtraGeocode(geo)) ? unroutedRoute("location_uncertain") : null;
    }
    if (Number.isFinite(gpsAccuracy)) {
      const pmcEdgeDistance = geometryBoundaryDistanceMeters(
        lng, lat, coverage.regions.pmc.geometry);
      const mmrEdgeDistance = geometryBoundaryDistanceMeters(
        lng, lat, coverage.regions.mmr.geometry);
      if (pmcEdgeDistance <= gpsAccuracy || mmrEdgeDistance <= gpsAccuracy) {
        return unroutedRoute("location_uncertain");
      }
    }
    if (inPmc) {
      return authorityRoute(PMC_AUTHORITY, {
        routing_source: "pmc_official_gis", match_field: "boundary",
        match_value: "PMC_Boundary", region: "pune",
      });
    }
    if (!inMmr) return null;

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
    // polygon all go to a neutral state portal. A nearby/postal town name is only a clue:
    // Nominatim explicitly returns the nearest suitable object, not a civic containment
    // decision, so it must never select the recipient by itself.
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

  async function routeOfficer(geoOrAddress, lat, lng, gpsAccuracy) {
    if (lat == null || lng == null || Number.isNaN(lat) || Number.isNaN(lng)) {
      return unroutedRoute("no_location");
    }

    const geo = geoOrAddress && typeof geoOrAddress === "object" ? geoOrAddress : null;
    const delhi = await delhiRouteFromGeocode(geo, lat, lng, gpsAccuracy);
    if (delhi) return delhi;

    const kolkata = await kolkataRouteFromGeocode(geo, lat, lng, gpsAccuracy);
    if (kolkata) return kolkata;

    const maharashtra = await maharashtraRouteFromGeocode(geo, lat, lng, gpsAccuracy);
    if (maharashtra) return maharashtra;
    // A Maharashtra geocode outside the two enabled polygons must not fall through to
    // Karnataka GIS and come back with a misleading state-service failure.
    if (isMaharashtraGeocode(geo)) return unroutedRoute("outside_area");

    const registry = await bodies();

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
    try { where = await jurisdictionOf(lat, lng); }
    catch (e) { return unroutedRoute("road_class_unknown"); }

    if (where.kind === "outside_state") return unroutedRoute("outside_area");
    if (where.kind === "national_highway") return unroutedRoute("national_highway", where.name);
    if (where.kind === "road_class_unknown") return unroutedRoute("road_class_unknown");
    if (where.kind === "rural") return unroutedRoute("rural_road", where.name);

    const entry = where.lgd && registry[where.lgd];
    if (!entry || !entry.email) return unroutedRoute("no_address_for_body", where.name);
    const title = entry.officer || OFFICER_TITLES[entry.type || where.type] || "Chief Officer";
    return {
      routed: true,
      officer_name: `${title}, ${entry.name}${entry.short ? ` (${entry.short})` : ""}`,
      officer_email: entry.email,
      authority_id: `ka-lgd-${where.lgd}`,
      authority_name: entry.name,
      delivery_channel: "email",
      ward_code: null,
      routing_source: "kgis",
      routing_match_field: "lgd",
      routing_match_value: where.lgd,
      region: "karnataka",
      ownership_unverified: false,
      requires_official_reference: false,
      tender_eligible: true,
    };
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
    if (candidate.capture_source === "manual") return null;
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
  // Parsed once per app session, which matters far more now the file is 9.5 MB: the
  // bundled data cannot change while the app runs, so one parse is correct.
  // As with the registry: a failed read is not cached, or one slow start would silently
  // stop every complaint naming a contract for the rest of the session.
  async function tenders() {
    if (_tenders) return _tenders;
    try {
      const res = await fetchWithTimeout("tenders.json", {}, 30000);
      const loaded = await readJson(res);
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
      if (kn) {
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

  // ---------- storage (IndexedDB) ----------
  let _db = null;
  function idb() {
    return new Promise((resolve, reject) => {
      if (_db) return resolve(_db);
      const req = indexedDB.open("potholes", 5);
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
      };
      req.onsuccess = () => { _db = req.result; resolve(_db); };
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
      ? (requestedSource === "drive_vod" ? "drive_vod" : "drive_live") : "manual";
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
      ? await routeOfficer(geo || address, lat, lng, gpsAccuracyRaw)
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
      sent_at: null,
      drive_id: driveId,
      capture_source: captureSource,
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
        attachments: [{ type: "base64", name: "road-damage.jpg",
                        path: await photoToBase64(rec.photo_full || rec.photo) }],
      });
    } else {
      console.log("[harness] would open native compose to:", to);
    }
    rec.status = "queued";
    rec.handoff_opened_at = Date.now() / 1000;
    await putReport(rec);
    return toDict(rec);
  }

  const isOfficialHandoff = (rec) => !!rec && OFFICIAL_HANDOFF_CHANNELS.has(rec.delivery_channel);

  async function openOfficialHandoff(rec) {
    if (!isOfficialHandoff(rec)) throw new Error("This report has no official app or portal handoff.");
    // v1.14 BMC records did not persist channel metadata. Keep them usable without
    // rewriting history or silently re-routing them through today's registry.
    const legacyBmc = rec.delivery_channel === "bmc_quickfix";
    const current = legacyBmc ? OFFICIAL_AUTHORITY_INDEX.get("mh-bmc")
      : OFFICIAL_AUTHORITY_INDEX.get(rec.authority_id);
    if (!current) {
      throw new Error("This saved report's official handoff is no longer in the verified registry.");
    }
    const handoffUrl = current.handoff_url;
    if (!handoffUrl || !String(handoffUrl).startsWith("https://")) {
      throw new Error("The verified official handoff for this saved report is unavailable.");
    }
    return {
      ...toDict(rec),
      authority_name: current.name,
      authority_registry_version: AUTHORITY_REGISTRY_VERSION,
      handoff_name: current.handoff_name,
      handoff_url: handoffUrl,
      handoff_package: current.handoff_package || null,
      alternate_handoff_name: current.alternate_handoff_name || null,
      alternate_handoff_url: current.alternate_handoff_url || null,
      whatsapp_url: current.whatsapp_url || null,
      helpline: current.helpline || null,
      requires_official_reference: true,
    };
  }

  async function evidenceForReport(rec) {
    if (!rec || !ACCEPTED_REPORT_STATUSES.has(rec.status)) {
      throw new Error("Only an accepted report has shareable evidence.");
    }
    const source = await dataUrlToBlob(rec.photo_full || rec.photo);
    const wideUrl = await toDataUrl(source, 1280, 0.86, false, 1);
    const cropUrl = rec.capture_source && rec.capture_source !== "manual"
      ? await toDataUrl(source, 1280, 0.86, false, ROAD_BAND) : null;
    const base64 = wideUrl && wideUrl.split(",")[1];
    const cropBase64 = cropUrl && cropUrl.split(",")[1];
    if (!base64) throw new Error("The report photo could not be read.");
    const safeId = String(rec.id || "report").replace(/[^a-zA-Z0-9_-]/g, "");
    const captured = new Date((rec.captured_at || rec.created_at) * 1000);
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
    const meta = [
      rec.email_subject || "Road-damage report",
      rec.email_body || "",
      when ? `Captured (IST): ${when}` : "",
      Number.isFinite(rec.gps_accuracy) ? `GPS accuracy: ±${Math.round(rec.gps_accuracy)} m` : "",
      rec.authority_name ? `Suggested civic authority: ${rec.authority_name} (verify road ownership)` : "",
      rec.ward_code ? `Suggested BMC ward: ${rec.ward_code} (verify in the official app)` : "",
      `Local event ID: ${safeId}`,
      submissionTruth,
    ].filter(Boolean).join("\n\n");
    return { name: `road-damage-${safeId}.jpg`, base64,
             crop_name: cropBase64 ? `road-damage-${safeId}-road-crop.jpg` : null,
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
    const labelled = (await allReports()).filter((r) => r.human_label);
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
      return openOfficialHandoff(rec);
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
    if (path === "/api/frame" && method === "POST") return createReport(opts.body, true);
    if ((m = path.match(/^\/api\/reports\/(\d+)\/send$/)) && method === "POST") {
      const rec = await getReport(m[1]);
      if (!rec) throw new Error("Report not found.");
      if (rec.status === "unrouted") {
        // Say which of the four reasons it was. "Outside the area" is wrong and
        // confusing when the real problem is that the phone never got a GPS fix.
        throw new Error({
          no_location: "This report has no location, so there is no way to tell which office is responsible. Retake it with location switched on.",
          location_uncertain: "The GPS fix is too imprecise to choose an authority safely. Retake it with a fresh, more accurate location.",
          road_class_unknown: "The app could not check whether this road is a national highway, and it will not name a city officer for a road that may not be theirs. Try again when you have a signal.",
          national_highway: "This stretch is a national highway. It is maintained by NHAI or the state PWD National Highways division, not by the city or town body, so there is no municipal officer to address.",
          rural_road: "This road is outside every town boundary, so it belongs to the state PWD or a panchayat rather than a city body. The app will not guess an office.",
          no_address_for_body: "This town's body is known, but no official email address for it has been published, so there is no verified recipient to address.",
          jurisdiction_unavailable: "The bundled civic jurisdiction map could not be read. Restart the app and try again; the app will not guess an authority.",
          outside_area: "This road damage is outside the supported Karnataka, Mumbai Metropolitan Region, Pune Municipal Corporation, Kolkata Municipal Corporation and Delhi NCT routes, so there is no authority to address.",
        }[rec.unrouted_reason] || "This report could not be routed to a responsible office, so there is nothing to send.");
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
                   maharashtraCoverage,
                   delhiCoverage, delhiRouteFromGeocode, inDelhiEnvelope,
                   kolkataCoverage, kolkataRouteFromGeocode, isWestBengalGeocode,
                   maharashtraRouteFromGeocode, routeOfficer,
                   MMR_AUTHORITIES, PMC_AUTHORITY, MMR_FALLBACK_AUTHORITY, KMC_AUTHORITY,
                   DELHI_PWD_AUTHORITY, OFFICIAL_AUTHORITIES,
                   DELHI_GEOMETRY_SHA256, KMC_GEOMETRY_SHA256,
                   AUTHORITY_REGISTRY_VERSION };

  window.StandaloneAPI = { __pure, handle, prewarm };

  // First run: open settings if no key yet (after the main script wires the UI).
  window.addEventListener("load", () => {
    if (!S.key && typeof window.openSettings === "function") window.openSettings(true);
  });
})();
