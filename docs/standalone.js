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
          detect: "AI checking: pothole YES or NO...", finalize: "Checking location and complaint route...",
          repair: "Comparing this revisit with the saved pothole...",
          write: "Writing the complaint...", email: "Opening your email app..." },
    kn: { compress: "ಫೋಟೋ ಸಂಕುಚಿಸಲಾಗುತ್ತಿದೆ...", capture: "ಫ್ರೇಮ್ ಸೆರೆಹಿಡಿಯಲಾಗುತ್ತಿದೆ...",
          detect: "AI ಗುಂಡಿ: ಹೌದು ಅಥವಾ ಇಲ್ಲ ಎಂದು ಪರಿಶೀಲಿಸುತ್ತಿದೆ...", finalize: "ಸ್ಥಳ ಮತ್ತು ದೂರು ಮಾರ್ಗ ಪರಿಶೀಲಿಸಲಾಗುತ್ತಿದೆ...",
          write: "ದೂರು ಬರೆಯಲಾಗುತ್ತಿದೆ...", email: "ನಿಮ್ಮ ಇಮೇಲ್ ಆ್ಯಪ್ ತೆರೆಯಲಾಗುತ್ತಿದೆ..." },
    mr: { compress: "फोटो तयार करत आहे...", capture: "रस्त्याची दृश्ये तयार करत आहे...",
          detect: "AI खड्डा: होय किंवा नाही हे तपासत आहे...", finalize: "पत्ता आणि मार्ग निश्चित करत आहे...",
          write: "तक्रारीचा मसुदा तयार करत आहे...", email: "ईमेल अॅप उघडत आहे..." },
    bn: { compress: "ছবি প্রস্তুত করা হচ্ছে...", capture: "রাস্তার দৃশ্য প্রস্তুত করা হচ্ছে...",
          detect: "AI রাস্তার গর্ত: হ্যাঁ অথবা না পরীক্ষা করছে...", finalize: "ঠিকানা ও অভিযোগের পথ চূড়ান্ত করা হচ্ছে...",
          write: "অভিযোগের খসড়া তৈরি হচ্ছে...", email: "ইমেল অ্যাপ খোলা হচ্ছে..." },
  };
  const pmsg = (k) => (PROGRESS[LANG()] && PROGRESS[LANG()][k]) || PROGRESS.en[k];

  const DEFAULT_MODEL = "gpt-5-mini";
  // Live private-media regression on the exact Drive contract found materially better
  // recall from gpt-5.6 while preserving every supplied hard negative. Manual Photo kept
  // better edge-cavity recall on gpt-5-mini. Keep the modes explicit and auditable.
  const DRIVE_DETECTION_MODEL = "gpt-5.6";
  const DRIVE_DETECTION_DETAIL = "high";
  const ALLOWED_MODELS = new Set([DEFAULT_MODEL, "gpt-5.6"]);
  const ALLOWED_DETAILS = new Set(["high", "original"]);
  const PROMPT_VERSION = "pothole-binary-v10";
  const PHOTO_PROMPT_VERSION = "pothole-photo-only-v3";
  const SCHEMA_VERSION = 7;
  const REPAIR_PROMPT_VERSION = "road-repair-v1";
  const REPAIR_SCHEMA_VERSION = 1;
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
  const REPAIR_MAX_ACCURACY_M = 12;
  const REPAIR_RADIUS_M = 5;
  const REPAIR_MISSING_HEADING_RADIUS_M = 3;
  const REPAIR_MAX_HEADING_DIFFERENCE_DEG = 35;
  const REPAIR_EVIDENCE_MIN_BYTES = 256;
  const REPAIR_EVIDENCE_MAX_BYTES = 8 * 1024 * 1024;
  const REPAIR_EVIDENCE_MIN_DIMENSION = 32;
  const REPAIR_EVIDENCE_MAX_DIMENSION = 8192;
  const REPAIR_EVIDENCE_MAX_PIXELS = 40 * 1024 * 1024;
  const REPAIR_EVIDENCE_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);

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
  const AUTHORITY_REGISTRY_VERSION = 18;
  const LAUNCHABLE_PACKAGES = new Set([
    "com.bmc.potholequickfix", "com.sis.pwdsewaapp", "com.kmc.app",
    "com.newnmmc.app", "com.nyatitechnologies.pmcroadmitra",
    "com.ceedeev.grivenancev2", "org.tnega.cmhelpline.citizen",
    "cgg.gov.ghmc", "com.amplvb.ccrs",
    "com.nhai.rajmargyatra", "com.nammabengaluruNew.org",
    "com.esri.ugms_bmc", "in.gov.pmc.pmccare", "com.nic.dl.delhijanmitra",
    "in.nic.up.jansunwai.upjansunwai", "com.rajsampark.versiontwo",
    "com.magnum.helpline", "com.bpsms.jansamadhan",
    "in.gov.dpg.cmhelpline", "com.sociomatic.janasunani",
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
  // Complaint profiles shape the copy an authority receives; routing packs still decide
  // only which geographic intake body contains the point. A municipal boundary is not
  // evidence that the same body owns or maintains the road.
  const AUTHORITY_COMPLAINT_PROFILES = Object.freeze({
    "mh-bmc": Object.freeze({
      profile_id: "mh-bmc", authority_name: "Brihanmumbai Municipal Corporation (BMC)",
      portal_name: "BMC Pothole QuickFix / MARG", portal_url: "https://marg.mcgm.gov.in/MARG/welcomePage.html",
      portal_category: "Pothole", request: "Please register this grievance, inspect and repair the defect, return the grievance number, and transfer it if another agency maintains the road.",
      source_urls: ["https://play.google.com/store/apps/details?id=com.bmc.potholequickfix",
        "https://marg.mcgm.gov.in/MARG/welcomePage.html"],
    }),
    "ka-lgd-305851": Object.freeze({ profile_id: "ka-bengaluru-central", authority_name: "Bengaluru Central City Corporation", boundary_body_code: "305851", portal_name: "Namma Bengaluru (Sahaaya 2.0)", portal_url: "https://nammabengaluru.org.in/login", portal_category: "Road Maintenance(Engg) / Pothole", recipient: "commissionerbccc@gmail.com" }),
    "ka-lgd-305850": Object.freeze({ profile_id: "ka-bengaluru-east", authority_name: "Bengaluru East City Corporation", boundary_body_code: "305850", portal_name: "Namma Bengaluru (Sahaaya 2.0)", portal_url: "https://nammabengaluru.org.in/login", portal_category: "Road Maintenance(Engg) / Pothole", recipient: "commissioner.becc@gmail.com" }),
    "ka-lgd-305853": Object.freeze({ profile_id: "ka-bengaluru-north", authority_name: "Bengaluru North City Corporation", boundary_body_code: "305853", portal_name: "Namma Bengaluru (Sahaaya 2.0)", portal_url: "https://nammabengaluru.org.in/login", portal_category: "Road Maintenance(Engg) / Pothole", recipient: "bengalurunorthcitycorporation@gmail.com" }),
    "ka-lgd-305852": Object.freeze({ profile_id: "ka-bengaluru-south", authority_name: "Bengaluru South City Corporation", boundary_body_code: "305852", portal_name: "Namma Bengaluru (Sahaaya 2.0)", portal_url: "https://nammabengaluru.org.in/login", portal_category: "Road Maintenance(Engg) / Pothole", recipient: "comm.south.gba@gmail.com" }),
    "ka-lgd-305854": Object.freeze({ profile_id: "ka-bengaluru-west", authority_name: "Bengaluru West City Corporation", boundary_body_code: "305854", portal_name: "Namma Bengaluru (Sahaaya 2.0)", portal_url: "https://nammabengaluru.org.in/login", portal_category: "Road Maintenance(Engg) / Pothole", recipient: "commissioner.bwcc@gmail.com" }),
    "ka-bengaluru-bda": Object.freeze({
      profile_id: "ka-bengaluru-bda", authority_name: "Bangalore Development Authority (BDA)",
      portal_name: "Karnataka iPGRS", portal_url: "https://ipgrs.karnataka.gov.in/",
      portal_category: "Road / Pothole", recipient: "com@bdabangalore.org",
      ownership_evidence_required: true,
    }),
  });

  function verifiedBdaResponsibility(route) {
    const evidence = route && (route.bda_responsibility_evidence || route.road_owner_evidence);
    return !!(route && (route.road_owner_id === "ka-bengaluru-bda"
      || route.intake_authority_id === "ka-bengaluru-bda")
      && evidence && evidence.verified === true
      && String(evidence.segment_identity || "").trim()
      && String(evidence.reference || evidence.document_id || "").trim()
      && /^https:\/\//.test(String(evidence.source_url || "")));
  }

  function authorityComplaintProfile(route) {
    if (verifiedBdaResponsibility(route)) return AUTHORITY_COMPLAINT_PROFILES["ka-bengaluru-bda"];
    const id = route && (route.intake_authority_id || route.authority_id);
    return AUTHORITY_COMPLAINT_PROFILES[id] || Object.freeze({
      profile_id: id || "generic", authority_name: conciseRouteLabel(route && route.authority_name) || "Concerned road authority",
      portal_name: route && route.handoff_name || null, portal_url: route && route.handoff_url || null,
      portal_category: "Road / Pothole",
    });
  }

  function separateRoadResponsibility(route) {
    if (!route || !route.routed) return route;
    const supplied = route.road_owner_evidence;
    const evidenceVerified = !!(supplied && supplied.verified === true
      && String(supplied.segment_identity || "").trim()
      && String(supplied.reference || supplied.document_id || "").trim()
      && /^https:\/\//.test(String(supplied.source_url || "")));
    const bdaClaim = route.road_owner_id === "ka-bengaluru-bda";
    const ownerVerified = evidenceVerified && (!bdaClaim || verifiedBdaResponsibility(route));
    const useBdaIntake = ownerVerified && bdaClaim;
    const bdaProfile = AUTHORITY_COMPLAINT_PROFILES["ka-bengaluru-bda"];
    return {
      ...route,
      geographic_authority_id: route.geographic_authority_id || route.authority_id || null,
      geographic_authority_name: route.geographic_authority_name || route.authority_name || null,
      // When segment-level official evidence proves BDA maintenance, BDA becomes the
      // intake route while the containing corporation remains geographic context.
      authority_id: useBdaIntake ? bdaProfile.profile_id : route.authority_id,
      authority_name: useBdaIntake ? bdaProfile.authority_name : route.authority_name,
      officer_name: useBdaIntake ? `Commissioner, ${bdaProfile.authority_name}` : route.officer_name,
      officer_email: useBdaIntake ? bdaProfile.recipient : route.officer_email,
      delivery_channel: useBdaIntake ? "email" : route.delivery_channel,
      handoff_name: useBdaIntake ? bdaProfile.portal_name : route.handoff_name,
      handoff_url: useBdaIntake ? bdaProfile.portal_url : route.handoff_url,
      intake_authority_id: useBdaIntake ? bdaProfile.profile_id
        : (route.intake_authority_id || route.authority_id || null),
      intake_authority_name: useBdaIntake ? bdaProfile.authority_name
        : (route.intake_authority_name || route.authority_name || null),
      road_owner_id: ownerVerified ? (route.road_owner_id || null) : null,
      road_owner_name: ownerVerified ? (route.road_owner_name || null) : null,
      road_owner_status: ownerVerified ? "verified" : "unverified",
      road_owner_evidence: ownerVerified ? supplied : null,
      ownership_unverified: !ownerVerified,
    };
  }
  // Only these reviewed general-grievance channels are allowed to inherit their base
  // route for garbage and manhole reports. Other municipal email/road routes fail
  // closed instead of assuming that a recipient accepts an unrelated category.
  const GENERAL_CIVIC_AUTHORITY_IDS = new Set([
    "wb-kmc", "wb-statewide-unverified", "tn-gcc", "tn-statewide-unverified",
    "tg-cure-shared", "tg-statewide-unverified", "gj-amc", "pb-statewide-unverified",
    "ap-statewide-unverified", "ka-statewide-unverified", "kl-statewide-unverified",
    "up-statewide-unverified", "cg-statewide-unverified", "rj-statewide-unverified",
    "ga-statewide-unverified", "mp-statewide-unverified", "br-statewide-unverified",
    "od-statewide-unverified",
    "ar-statewide-unverified", "as-statewide-unverified", "gj-statewide-unverified",
    "hr-statewide-unverified", "hp-statewide-unverified", "jh-statewide-unverified",
    "mn-statewide-unverified", "ml-statewide-unverified", "mz-statewide-unverified",
    "nl-statewide-unverified", "sk-statewide-unverified", "tr-statewide-unverified",
    "uk-statewide-unverified", "an-statewide-unverified", "ch-statewide-unverified",
    "dh-statewide-unverified", "jk-statewide-unverified", "la-statewide-unverified",
    "ld-statewide-unverified", "py-statewide-unverified",
    "in-gj-enagar", "in-rj-sampark", "in-up-jansunwai", "in-mp-cm-helpline",
    "in-tn-cm-helpline", "in-kl-ksmart", "in-br-lok-shikayat", "in-ap-puramithra",
    "in-hr-nagar-darshan", "in-jh-municipal-grievance", "in-jk-samadhan", "in-cg-nidaan",
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
  const PUNJAB_STATE_AUTHORITY = {};
  const TAMIL_NADU_STATE_AUTHORITY = {};
  const ANDHRA_PRADESH_STATE_AUTHORITY = {};
  const TELANGANA_STATE_AUTHORITY = {};
  const KARNATAKA_STATE_AUTHORITY = {};
  const KERALA_STATE_AUTHORITY = {};
  const UTTAR_PRADESH_STATE_AUTHORITY = {};
  const CHHATTISGARH_STATE_AUTHORITY = {};
  const RAJASTHAN_STATE_AUTHORITY = {};
  const GOA_STATE_AUTHORITY = {};
  const MADHYA_PRADESH_STATE_AUTHORITY = {};
  const BIHAR_STATE_AUTHORITY = {};
  const ODISHA_STATE_AUTHORITY = {};
  const REMAINING_STATE_AUTHORITIES = new Map();
  const DELHI_PWD_AUTHORITY = {};
  const NATIONAL_HIGHWAY_AUTHORITY = {};
  const OFFICIAL_AUTHORITIES = [];
  const OFFICIAL_AUTHORITY_INDEX = new Map();
  const MMR_ALIAS_INDEX = new Map();
  // Keyed by pack ID, rather than state code: one state can have both exact municipal
  // and separate statewide fallback packs.
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

  const DETECT_PROMPT = `You are a high-precision binary pothole detector inspecting one or more chronologically ordered road views for a civic complaint app.

Return one decision only: is_pothole true (YES) or false (NO). There is no confidence score, probability, probable result, review result, or general road-damage category. False positives are more harmful than false negatives, so any ambiguity must be NO.

A pothole is a localized concave open cavity in the surface currently used by moving road traffic, with surface material visibly missing, displaced, or disintegrated. A YES requires all of these:
- the feature is on the drivable surface used by moving traffic;
- it has a distinct local edge, lip, or abrupt height discontinuity enclosing a depressed opening;
- it has visible depth or localized material loss; and
- when several chronological views are supplied, they consistently show the same concave geometry as the vehicle approaches.

Position near the side of a lane is not itself a rejection. A localized cavity whose opening intrudes into the drivable surface actually used by moving traffic may be YES when every physical gate above is satisfied, including when the cavity adjoins a raised kerb or roadside slab. This exception is only for the cavity footprint inside the active traffic surface: damage confined to a kerb, gutter, drain, footpath, shoulder, verge, roadside ground, or a broken outer edge that vehicles do not traverse is NO.

Return NO for a speed breaker, road hump, rumble strip, shadow, stain, glare, dust, loose debris, lane marking, intact patch, crack, broad surface breakup without a distinct cavity, wheel rut, smooth depression, manhole, drain, expansion joint, shoulder erosion, construction obstacle, or broken edge outside the active traffic surface. A failed patch is YES only when it now contains a distinct cavity satisfying every rule.

Speed-breaker rule:
- Set looks_like_speed_breaker true whenever the feature is or could reasonably be an intentional raised speed breaker, hump, or rumble strip. Painted rectangles or stripes, reflectors, a transverse ridge across the lane, parallel leading/trailing edges, camera pitch, and a vehicle jolt support NO, not YES.
- A separate cavity on or beside a breaker is YES only when it is visually unambiguous and distinct from the raised ridge. If raised-versus-concave geometry is uncertain, return NO.

Classify surface_type as:
- bituminous_asphalt for conventional asphalt or blacktop;
- cement_concrete for a concrete slab;
- mastic_asphalt only when that pavement is visually identifiable;
- paver_blocks for interlocking paved blocks;
- temporary_drivable_surface only for an unsealed, unfinished, or construction-stage lane that the chronological views clearly show is currently carrying road traffic;
- unpaved_or_nonroad for a dirt or gravel shoulder, construction bed, work area, service path, roadside ground, or other non-carriageway surface; or
- unknown whenever the material or road use is uncertain.

Camera position alone does not prove that an unsealed surface is a traffic lane. The four named paved surfaces may be YES only when every physical gate is satisfied. For temporary_drivable_surface, distinguish a local cavity from the surrounding unfinished texture:
- A pothole can exist inside a generally rough, failed, or gravel-covered traffic lane. Do not reject a discrete cavity merely because nearby surface is also damaged or unfinished.
- On this surface, a broken edge or rim can be an eroded lip or abrupt localized material-height change; it need not be a fractured asphalt edge.
- A water-filled cavity can be YES when a localized enclosing lip and depressed opening remain visible and preserve their geometry across the approach. Water or a dark patch without that independent boundary evidence is NO; the cavity floor need not be visible through opaque water.
- Do not require dramatic depth, a black interior, or an exposed cavity floor. A shallow opening is YES only when an irregular eroded lip or abrupt material-height change bounds a visibly lower local interior and that same footprint remains coherent as it grows across the approach views. A flat discoloration, intact repair, soft shadow, loose-gravel texture, or broad unevenness without that bounded lower interior is NO.
- A long or open-ended eroded edge, a seam or step between paver blocks and loose aggregate, missing edge blocks, and a transition between paved and unfinished material are NO. Do not reinterpret one of these boundaries as a cavity cluster merely because the same rough edge persists across the approach.
- On loose gravel, changes in colour, aggregate density, wheel-track texture, or grading do not prove a cavity. Require a separate compact concave opening with its own localized enclosing lip and visibly lower interior; never infer either feature from aggregate texture alone.
- Two or more adjacent discrete bowl-like material-loss openings are one connected cavity-cluster event. Do not relabel them as broad breakup when their local boundaries remain distinct.
- Two adjacent compact oval material-loss openings may be one shallow cavity-cluster even when their floors are similar in colour to the surrounding lane. This is YES only when each opening has a stable irregular boundary and visibly lower interior across the approach; patch outlines and stains remain NO.
- General roughness, corrugation, wheel ruts, broad breakup, loose aggregate, normal gravel texture, grading, and smooth depressions are NO.

Road-edge boundary interpretation:
- A cavity at the meeting line of a flat roadway foreground and a raised roadside slab is not confined to the footpath or gutter when its broken opening removes part of that flat road edge or creates an abrupt open drop reachable by a vehicle wheel. In that case set on_drivable_surface true even when much of the visible void or rubble extends beside or underneath the slab. Reject it as confined outside the road only when an intact continuous kerb or gutter clearly separates the entire cavity opening from the traffic surface.

unpaved_or_nonroad and unknown must always be NO.

Set image_quality unusable when blur, darkness, glare, obstruction, or distance prevents a defensible judgment. For multiple views use temporal_consistency consistent only when they agree; use inconsistent when they do not. For a single user-framed image use single_view.

Only after YES, classify approximate visual size using the app's simple bands:
- small: maximum visible opening width below 30 cm;
- medium: 30 to 60 cm;
- large: above 60 cm or a connected cavity cluster.
For NO, size must be null. These are app visual classes only, not measured dimensions and not BMC, BDA, GBA, or any other authority's official categories.

description must be one or two factual sentences. For YES, name the visible cavity evidence, position, and road-user hazard. For NO, briefly name the disqualifying feature. Never output a confidence percentage.`;

  const PHOTO_ONLY_PROMPT_SUFFIX = `

Photo feature scope: detect potholes only. Garbage, litter, dumped waste, open or damaged manholes, drains, footpaths, and every other civic issue are not reportable in this feature and must return is_pothole false (NO). Never reinterpret them as another complaint category.`;

  // Key order is the streaming order. The binary decision arrives first so a clear NO
  // can stop generation immediately; a YES is accepted only after every physical gate.
  const ASSESS_SCHEMA = {
    type: "object", additionalProperties: false,
    required: ["is_pothole", "looks_like_speed_breaker", "image_quality", "surface_type", "on_drivable_surface",
      "has_localized_cavity", "has_broken_edge_or_rim", "has_depth_or_surface_loss",
      "temporal_consistency", "size", "description"],
    properties: {
      is_pothole: { type: "boolean" },
      looks_like_speed_breaker: { type: "boolean" },
      image_quality: { type: "string", enum: ["usable", "unusable"] },
      surface_type: { type: "string", enum: ["bituminous_asphalt", "cement_concrete",
        "mastic_asphalt", "paver_blocks", "temporary_drivable_surface",
        "unpaved_or_nonroad", "unknown"] },
      on_drivable_surface: { type: "boolean" },
      has_localized_cavity: { type: "boolean" },
      has_broken_edge_or_rim: { type: "boolean" },
      has_depth_or_surface_loss: { type: "boolean" },
      temporal_consistency: { type: "string", enum: ["consistent", "single_view", "inconsistent", "not_applicable"] },
      size: { type: ["string", "null"], enum: ["small", "medium", "large", null] },
      description: { type: "string" },
    },
  };
  const REPAIR_PROMPT = `Compare a saved pothole photograph with new road views from a later live drive.

Image 1 is the older saved road-damage evidence. Image 2 is the current full-frame context. The remaining images are current orientation-aware road-region crops.

This is a strict before/after verification, not ordinary pothole detection:
- Set same_location_visible true only when stable road geometry and surrounding features show that the old damaged footprint itself is visible in the current views. Nearby clean asphalt, a different lane, or a similar-looking road is not the same footprint.
- Set completed_repair_visible true only when that exact old footprint is now covered by completed, intact asphalt, concrete, or a sealed level patch on the drivable surface.
- The absence of a visible cavity is never repair evidence by itself. Blur, distance, glare, traffic, water, occlusion, a changed viewpoint, or failure to locate the old footprint must produce current_condition uncertain or not_visible.
- Use still_damaged if the old defect or a failed repair remains visible.
- Use repaired only when the same footprint and the completed intact repair are both clear. Do not infer repairs from time, GPS, or a generally smooth road.
- description must state the stable same-place cues and the visible repair material, or state why verification is inconclusive.`;
  const REPAIR_SCHEMA = {
    type: "object", additionalProperties: false,
    required: ["same_location_visible", "completed_repair_visible", "current_condition",
      "assessment", "image_quality", "description"],
    properties: {
      same_location_visible: { type: "boolean" },
      completed_repair_visible: { type: "boolean" },
      current_condition: { type: "string",
        enum: ["repaired", "still_damaged", "not_visible", "uncertain"] },
      assessment: { type: "string", enum: ["clear", "probable", "uncertain"] },
      image_quality: { type: "string", enum: ["usable", "degraded", "unusable"] },
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

  // Structured outputs stream in schema order. The same hard binary gate is used for
  // streamed and final paths, so the UI cannot announce YES and later reverse it.
  const IS_POTHOLE_RE = /"is_pothole"\s*:\s*(true|false)/;
  const SPEED_BREAKER_RE = /"looks_like_speed_breaker"\s*:\s*(true|false)/;
  const QUALITY_RE = /"image_quality"\s*:\s*"(usable|unusable)"/;
  const SURFACE_RE = /"surface_type"\s*:\s*"(bituminous_asphalt|cement_concrete|mastic_asphalt|paver_blocks|temporary_drivable_surface|unpaved_or_nonroad|unknown)"/;
  const ROAD_RE = /"on_drivable_surface"\s*:\s*(true|false)/;
  const CAVITY_RE = /"has_localized_cavity"\s*:\s*(true|false)/;
  const EDGE_RE = /"has_broken_edge_or_rim"\s*:\s*(true|false)/;
  const DEPTH_RE = /"has_depth_or_surface_loss"\s*:\s*(true|false)/;
  const TEMPORAL_RE = /"temporal_consistency"\s*:\s*"(consistent|single_view|inconsistent|not_applicable)"/;
  const SIZE_RE = /"size"\s*:\s*(?:"(small|medium|large)"|null)/;
  const POTHOLE_SIZES = new Set(["small", "medium", "large"]);
  const PAVED_SURFACES = new Set(["bituminous_asphalt", "cement_concrete",
    "mastic_asphalt", "paver_blocks"]);
  const TEMPORARY_DRIVABLE_SURFACE = "temporary_drivable_surface";
  const REPORTABLE_SURFACES = new Set([...PAVED_SURFACES, TEMPORARY_DRIVABLE_SURFACE]);
  const KNOWN_SURFACES = new Set([...REPORTABLE_SURFACES, "unpaved_or_nonroad", "unknown"]);
  const LEGACY_NATIVE_V9_PROMPT_VERSION = "pothole-binary-v9";
  const LEGACY_NATIVE_V9_SCHEMA_VERSION = 7;
  const LEGACY_NATIVE_V8_PROMPT_VERSION = "pothole-binary-v8";
  const LEGACY_NATIVE_V8_SCHEMA_VERSION = 7;
  const LEGACY_NATIVE_V7_PROMPT_VERSION = "pothole-binary-v7";
  const LEGACY_NATIVE_V7_SCHEMA_VERSION = 7;
  const LEGACY_NATIVE_V6_PROMPT_VERSION = "pothole-binary-v6";
  const LEGACY_NATIVE_V6_SCHEMA_VERSION = 6;

  function nativeDetectorContract(native) {
    if (!native || typeof native !== "object") return null;
    if (native.prompt_version === PROMPT_VERSION
        && Number(native.schema_version) === SCHEMA_VERSION) {
      return { kind: "current_v10", surfaceTypes: REPORTABLE_SURFACES };
    }
    // v9 used the same strict fields and remains valid for unsynced rows captured by
    // the previous GitHub release. Its prompt and crop differed, so retain provenance.
    if (native.prompt_version === LEGACY_NATIVE_V9_PROMPT_VERSION
        && Number(native.schema_version) === LEGACY_NATIVE_V9_SCHEMA_VERSION) {
      return { kind: "legacy_v9", surfaceTypes: REPORTABLE_SURFACES };
    }
    // v8 used the same strict fields and remains valid for unsynced rows captured by
    // the previous GitHub release. Its prompt differed, so keep the provenance explicit.
    if (native.prompt_version === LEGACY_NATIVE_V8_PROMPT_VERSION
        && Number(native.schema_version) === LEGACY_NATIVE_V8_SCHEMA_VERSION) {
      return { kind: "legacy_v8", surfaceTypes: REPORTABLE_SURFACES };
    }
    // v7 used the same strict schema and temporary-surface vocabulary. Preserve pending
    // and accepted rows captured immediately before the prompt/crop upgrade.
    if (native.prompt_version === LEGACY_NATIVE_V7_PROMPT_VERSION
        && Number(native.schema_version) === LEGACY_NATIVE_V7_SCHEMA_VERSION) {
      return { kind: "legacy_v7", surfaceTypes: REPORTABLE_SURFACES };
    }
    // v6 knew only sealed paved surfaces and cannot authorize the temporary class.
    if (native.prompt_version === LEGACY_NATIVE_V6_PROMPT_VERSION
        && Number(native.schema_version) === LEGACY_NATIVE_V6_SCHEMA_VERSION) {
      return { kind: "legacy_v6", surfaceTypes: PAVED_SURFACES };
    }
    return null;
  }

  function decisionFor(a, driveMode = false, sourceViewCount = null) {
    // The model supplies YES/NO, but YES still has to satisfy every physical invariant.
    // Anything missing, ambiguous, off-road, raised, or poorly visible becomes NO.
    if (!a || a.is_pothole !== true || a.looks_like_speed_breaker !== false) return "reject";
    if (a.image_quality !== "usable" || !REPORTABLE_SURFACES.has(a.surface_type)
        || a.on_drivable_surface !== true ||
        a.has_localized_cavity !== true) return "reject";
    if (a.has_broken_edge_or_rim !== true || a.has_depth_or_surface_loss !== true) return "reject";
    // An unfinished surface can be distinguished from ordinary gravel/ruts only from a
    // chronological Drive burst. A single user-framed Photo must fail closed here even
    // if the model contradicts the prompt and calls it reportable.
    if (a.surface_type === TEMPORARY_DRIVABLE_SURFACE && !driveMode) return "reject";
    if (driveMode) {
      // A full scene and a crop of the same frame are not temporal corroboration.
      if (a.temporal_consistency !== "consistent" || sourceViewCount < 2) return "reject";
    } else if (a.temporal_consistency !== "consistent" && a.temporal_consistency !== "single_view") {
      return "reject";
    }
    if (!POTHOLE_SIZES.has(a.size)) return "reject";
    return "accept";
  }

  function binaryAssessment(a, driveMode = false, sourceViewCount = null) {
    const accepted = decisionFor(a, driveMode, sourceViewCount) === "accept";
    return {
      ...(a || {}),
      is_pothole: accepted,
      // Compatibility fields keep existing reports, complaint rendering and Room sync
      // readable. They are derived locally; the model no longer predicts them.
      reportable: accepted,
      assessment: accepted ? "clear" : "absent",
      damage_type: accepted ? "pothole_cavity" : "none",
      defect_type: accepted ? "pothole" : "not_pothole",
      surface_type: KNOWN_SURFACES.has(a && a.surface_type) ? a.surface_type : "unknown",
      // No pixel-to-centimetre conversion is defensible without a scale reference.
      // Keep the useful visual class, but make the absent physical measurements explicit.
      measurement_provenance: accepted ? "visual_estimate_no_scale" : "not_applicable",
      measurement_confidence: accepted ? "low" : "not_applicable",
      measurement_length_cm: null,
      measurement_width_cm: null,
      measurement_depth_cm: null,
      size: accepted ? a.size : null,
    };
  }

  const clearAbsenceForRepair = (a) => !!a
    && a.is_pothole === false
    && a.looks_like_speed_breaker === false
    && a.image_quality === "usable"
    && !a.has_localized_cavity
    && !a.has_broken_edge_or_rim
    && !a.has_depth_or_surface_loss
    && a.size == null;

  function repairConditionFor(observation) {
    if (!observation || observation.current_condition !== "repaired"
        || observation.same_location_visible !== true
        || observation.completed_repair_visible !== true
        || observation.image_quality !== "usable") return null;
    if (observation.assessment === "clear") return "fixed";
    if (observation.assessment === "probable") return "repair_review";
    return null;
  }

  function partialAssessment(text) {
    const verdict = IS_POTHOLE_RE.exec(text);
    if (!verdict) return null;
    if (verdict[1] === "false") return binaryAssessment({
      is_pothole: false, looks_like_speed_breaker: false, image_quality: "usable",
      surface_type: "unknown",
      on_drivable_surface: false, has_localized_cavity: false, has_broken_edge_or_rim: false,
      has_depth_or_surface_loss: false, temporal_consistency: "not_applicable", size: null,
    });
    const breaker = SPEED_BREAKER_RE.exec(text), quality = QUALITY_RE.exec(text);
    const surface = SURFACE_RE.exec(text);
    const road = ROAD_RE.exec(text), cavity = CAVITY_RE.exec(text);
    const edge = EDGE_RE.exec(text), depth = DEPTH_RE.exec(text);
    const temporal = TEMPORAL_RE.exec(text), size = SIZE_RE.exec(text);
    if (!breaker || !quality || !surface || !road || !cavity || !edge || !depth || !temporal || !size) return null;
    return {
      is_pothole: true,
      looks_like_speed_breaker: breaker[1] === "true",
      image_quality: quality[1],
      surface_type: surface[1],
      on_drivable_surface: road[1] === "true",
      has_localized_cavity: cavity[1] === "true",
      has_broken_edge_or_rim: edge[1] === "true",
      has_depth_or_surface_loss: depth[1] === "true",
      temporal_consistency: temporal[1],
      size: size[1] || null,
    };
  }

  const peekVerdict = (partial) => {
    const a = partialAssessment(partial);
    if (!a) return null;
    const decision = decisionFor(a);
    return { accepted: decision === "accept", review: false,
             damage_type: decision === "accept" ? "pothole_cavity" : "none",
             assessment: decision === "accept" ? "clear" : "absent" };
  };

  // True once the response has proved that Drive Mode will not create a complaint.
  // Debug/evaluation calls do not enable cancellation because they need the exact full
  // verdict, including the reason for a miss.
  const peekReject = (partial, driveMode = false) => {
    const verdict = IS_POTHOLE_RE.exec(partial);
    if (!verdict) return false;
    if (verdict[1] === "false") return true;
    const breaker = SPEED_BREAKER_RE.exec(partial);
    if (breaker && breaker[1] === "true") return true;
    const a = partialAssessment(partial);
    return !!a && decisionFor(a, driveMode, driveMode ? 2 : null) !== "accept";
  };

  function drainSSE(chunk, state, onEarly, stopWhenRejected) {
    state.buf += chunk;
    let i;
    while ((i = state.buf.indexOf("\n")) >= 0) {
      const line = state.buf.slice(0, i).trim();
      state.buf = state.buf.slice(i + 1);
      if (!line.startsWith("data:")) continue;
      const payload = line.slice(5).trim();
      if (!payload) continue;
      if (payload === "[DONE]") {
        state.transportCompleted = true;
        continue;
      }
      let ev;
      try { ev = JSON.parse(payload); } catch (e) { continue; }
      if (ev.type === "response.completed") state.transportCompleted = true;
      if (ev.type === "response.output_text.delta" && typeof ev.delta === "string") {
        state.text += ev.delta;
        if (!state.early && onEarly) {
          const v = peekVerdict(state.text);
          if (v) { state.early = true; try { onEarly(v); } catch (e) {} }
        }
        if (stopWhenRejected && !state.stop && peekReject(state.text, true)) state.stop = true;
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

    const state = { buf: "", text: "", early: false, stop: false,
                    transportCompleted: false };
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
    // Flush an unterminated last SSE line before deciding whether the stream itself
    // completed. A parseable JSON delta is not proof that the HTTP body was complete.
    drainSSE("\n", state, onEarly, stopWhenRejected);
    if (state.stop) return rejectedVerdict(state.text);
    if (!state.transportCompleted) {
      const incomplete = new Error("Detection stream ended before OpenAI confirmed completion.");
      incomplete.incompleteStream = true;
      throw incomplete;
    }
    if (!state.text) throw new Error("Empty model response.");
    return JSON.parse(state.text);
  }

  // Reconstructed from the closed fields that arrived before Drive Mode cancelled the
  // remaining description. It deliberately has the complete new schema shape.
  function rejectedVerdict(text) {
    const verdict = IS_POTHOLE_RE.exec(text), breaker = SPEED_BREAKER_RE.exec(text);
    const quality = QUALITY_RE.exec(text), surface = SURFACE_RE.exec(text);
    const road = ROAD_RE.exec(text), cavity = CAVITY_RE.exec(text);
    const edge = EDGE_RE.exec(text);
    const depth = DEPTH_RE.exec(text), temporal = TEMPORAL_RE.exec(text), size = SIZE_RE.exec(text);
    return binaryAssessment({
      is_pothole: !!verdict && verdict[1] === "true",
      looks_like_speed_breaker: breaker ? breaker[1] === "true" : true,
      image_quality: quality ? quality[1] : "unusable",
      surface_type: surface ? surface[1] : "unknown",
      on_drivable_surface: !!road && road[1] === "true",
      has_localized_cavity: !!cavity && cavity[1] === "true",
      has_broken_edge_or_rim: !!edge && edge[1] === "true",
      has_depth_or_surface_loss: !!depth && depth[1] === "true",
      temporal_consistency: temporal ? temporal[1] : "not_applicable",
      size: size ? (size[1] || null) : null,
      description: "",
    });
  }

  const fmt = (name, schema) => ({
    format: { type: "json_schema", name, schema, strict: true },
    verbosity: "low",
  });
  const progress = (m) => { try { window.dispatchEvent(new CustomEvent("pipeline-progress", { detail: m })); } catch (e) {} };
  const emitVerdict = (v) => { try { window.dispatchEvent(new CustomEvent("pipeline-verdict", { detail: v })); } catch (e) {} };

  function buildDetectionRequest(imageInputs, prompt, model = S.model, detail = S.detail,
                                 formatName = "pothole_binary_assessment", schema = ASSESS_SCHEMA) {
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
      text: fmt(formatName, schema),
    };
  }

  let streamBroken = false;
  async function analyzeImage(imageInputs, prompt, name, schema, model, onEarly,
                              stopWhenRejected, detail, reasoningEffort = null) {
    const body = (schema === ASSESS_SCHEMA || schema === REPAIR_SCHEMA)
      ? buildDetectionRequest(imageInputs, prompt, model, detail, name, schema)
      : {
          model,
          input: [{ role: "user", content: [
            { type: "input_image", image_url: Array.isArray(imageInputs) ? imageInputs[0] : imageInputs },
            { type: "input_text", text: prompt },
          ] }],
          text: fmt(name, schema),
        };
    if (reasoningEffort) body.reasoning = { effort: reasoningEffort };
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
      if (e && e.incompleteStream) throw e;
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
          // A neutral statewide channel may be referenced by more than one independently
          // pinned geographic pack. Only a byte-for-byte-equivalent authority object may
          // share an ID; divergent URLs or labels still invalidate the registry.
          const installed = OFFICIAL_AUTHORITY_INDEX.get(authority.id);
          if (canonicalJson(installed) !== canonicalJson(authority)) {
            throw new Error(`Official authority ${authority.id} differs across state packs.`);
          }
          continue;
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
    const remainingConfig = REMAINING_STATE_ROUTE_CONFIGS[pack.pack_id];
    if (remainingConfig) {
      if (pack.state_code !== remainingConfig.state_code || authorities.length !== 1
          || !byId.has(remainingConfig.authority_id)) {
        throw new Error("State/UT routing pack has an invalid authority registry.");
      }
      REMAINING_STATE_AUTHORITIES.set(pack.pack_id, byId.get(remainingConfig.authority_id));
      PACK_AUTHORITIES_BY_STATE.set(pack.pack_id,
        [REMAINING_STATE_AUTHORITIES.get(pack.pack_id)]);
    } else if (pack.state_code === "MH") {
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
      PACK_AUTHORITIES_BY_STATE.set(pack.pack_id, [
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
      PACK_AUTHORITIES_BY_STATE.set(pack.pack_id,
        [KMC_AUTHORITY, WEST_BENGAL_STATE_AUTHORITY]);
    } else if (pack.state_code === "DL") {
      if (authorities.length !== 1 || !byId.has("dl-pwd-sewa")) {
        throw new Error("Delhi routing pack has an invalid authority registry.");
      }
      replaceStableObject(DELHI_PWD_AUTHORITY, byId.get("dl-pwd-sewa"));
      PACK_AUTHORITIES_BY_STATE.set(pack.pack_id, [DELHI_PWD_AUTHORITY]);
    } else if (pack.state_code === "PB") {
      if (authorities.length !== 1 || !byId.has("pb-statewide-unverified")) {
        throw new Error("Punjab routing pack has an invalid authority registry.");
      }
      replaceStableObject(PUNJAB_STATE_AUTHORITY, byId.get("pb-statewide-unverified"));
      PACK_AUTHORITIES_BY_STATE.set(pack.pack_id, [PUNJAB_STATE_AUTHORITY]);
    } else if (pack.pack_id === "in-tn-state-routing") {
      if (authorities.length !== 1 || !byId.has("tn-statewide-unverified")) {
        throw new Error("Tamil Nadu statewide routing pack has an invalid authority registry.");
      }
      replaceStableObject(TAMIL_NADU_STATE_AUTHORITY, byId.get("tn-statewide-unverified"));
      PACK_AUTHORITIES_BY_STATE.set(pack.pack_id, [TAMIL_NADU_STATE_AUTHORITY]);
    } else if (pack.pack_id === "in-ap-routing") {
      if (authorities.length !== 1 || !byId.has("ap-statewide-unverified")) {
        throw new Error("Andhra Pradesh routing pack has an invalid authority registry.");
      }
      replaceStableObject(ANDHRA_PRADESH_STATE_AUTHORITY,
        byId.get("ap-statewide-unverified"));
      PACK_AUTHORITIES_BY_STATE.set(pack.pack_id, [ANDHRA_PRADESH_STATE_AUTHORITY]);
    } else if (pack.pack_id === "in-tg-state-routing") {
      if (authorities.length !== 1 || !byId.has("tg-statewide-unverified")) {
        throw new Error("Telangana statewide routing pack has an invalid authority registry.");
      }
      replaceStableObject(TELANGANA_STATE_AUTHORITY,
        byId.get("tg-statewide-unverified"));
      PACK_AUTHORITIES_BY_STATE.set(pack.pack_id, [TELANGANA_STATE_AUTHORITY]);
    } else if (pack.pack_id === "in-ka-state-routing") {
      if (authorities.length !== 1 || !byId.has("ka-statewide-unverified")) {
        throw new Error("Karnataka statewide routing pack has an invalid authority registry.");
      }
      replaceStableObject(KARNATAKA_STATE_AUTHORITY,
        byId.get("ka-statewide-unverified"));
      PACK_AUTHORITIES_BY_STATE.set(pack.pack_id, [KARNATAKA_STATE_AUTHORITY]);
    } else if (pack.pack_id === "in-kl-routing") {
      if (authorities.length !== 1 || !byId.has("kl-statewide-unverified")) {
        throw new Error("Kerala statewide routing pack has an invalid authority registry.");
      }
      replaceStableObject(KERALA_STATE_AUTHORITY,
        byId.get("kl-statewide-unverified"));
      PACK_AUTHORITIES_BY_STATE.set(pack.pack_id, [KERALA_STATE_AUTHORITY]);
    } else if (pack.pack_id === "in-up-routing") {
      if (authorities.length !== 1 || !byId.has("up-statewide-unverified")) {
        throw new Error("Uttar Pradesh statewide routing pack has an invalid authority registry.");
      }
      replaceStableObject(UTTAR_PRADESH_STATE_AUTHORITY,
        byId.get("up-statewide-unverified"));
      PACK_AUTHORITIES_BY_STATE.set(pack.pack_id, [UTTAR_PRADESH_STATE_AUTHORITY]);
    } else if (pack.pack_id === "in-cg-routing") {
      if (authorities.length !== 1 || !byId.has("cg-statewide-unverified")) {
        throw new Error("Chhattisgarh statewide routing pack has an invalid authority registry.");
      }
      replaceStableObject(CHHATTISGARH_STATE_AUTHORITY,
        byId.get("cg-statewide-unverified"));
      PACK_AUTHORITIES_BY_STATE.set(pack.pack_id, [CHHATTISGARH_STATE_AUTHORITY]);
    } else if (pack.pack_id === "in-rj-routing") {
      if (authorities.length !== 1 || !byId.has("rj-statewide-unverified")) {
        throw new Error("Rajasthan statewide routing pack has an invalid authority registry.");
      }
      replaceStableObject(RAJASTHAN_STATE_AUTHORITY,
        byId.get("rj-statewide-unverified"));
      PACK_AUTHORITIES_BY_STATE.set(pack.pack_id, [RAJASTHAN_STATE_AUTHORITY]);
    } else if (pack.pack_id === "in-ga-routing") {
      if (authorities.length !== 1 || !byId.has("ga-statewide-unverified")) {
        throw new Error("Goa statewide routing pack has an invalid authority registry.");
      }
      replaceStableObject(GOA_STATE_AUTHORITY, byId.get("ga-statewide-unverified"));
      PACK_AUTHORITIES_BY_STATE.set(pack.pack_id, [GOA_STATE_AUTHORITY]);
    } else if (pack.pack_id === "in-mp-routing") {
      if (authorities.length !== 1 || !byId.has("mp-statewide-unverified")) {
        throw new Error("Madhya Pradesh statewide routing pack has an invalid authority registry.");
      }
      replaceStableObject(MADHYA_PRADESH_STATE_AUTHORITY,
        byId.get("mp-statewide-unverified"));
      PACK_AUTHORITIES_BY_STATE.set(pack.pack_id, [MADHYA_PRADESH_STATE_AUTHORITY]);
    } else if (pack.pack_id === "in-br-routing") {
      if (authorities.length !== 1 || !byId.has("br-statewide-unverified")) {
        throw new Error("Bihar statewide routing pack has an invalid authority registry.");
      }
      replaceStableObject(BIHAR_STATE_AUTHORITY, byId.get("br-statewide-unverified"));
      PACK_AUTHORITIES_BY_STATE.set(pack.pack_id, [BIHAR_STATE_AUTHORITY]);
    } else if (pack.pack_id === "in-od-routing") {
      if (authorities.length !== 1 || !byId.has("od-statewide-unverified")) {
        throw new Error("Odisha statewide routing pack has an invalid authority registry.");
      }
      replaceStableObject(ODISHA_STATE_AUTHORITY, byId.get("od-statewide-unverified"));
      PACK_AUTHORITIES_BY_STATE.set(pack.pack_id, [ODISHA_STATE_AUTHORITY]);
    } else if (pack.state_code === "KA") {
      if (authorities.length) throw new Error("Karnataka contacts must use the LGD registry.");
      PACK_AUTHORITIES_BY_STATE.set(pack.pack_id, []);
    } else if (pack.adapter === "municipal-city-v1"
        || pack.adapter === "major-city-structured-v1") {
      if (!authorities.length || authorities.length > 100) {
        throw new Error("Municipal-city routing pack has an invalid authority registry.");
      }
      PACK_AUTHORITIES_BY_STATE.set(pack.pack_id, authorities);
    } else {
      throw new Error("Unsupported routing-pack state.");
    }
    for (const [authorityId, packIds] of PACK_ID_BY_AUTHORITY) {
      packIds.delete(pack.pack_id);
      if (!packIds.size) PACK_ID_BY_AUTHORITY.delete(authorityId);
    }
    for (const authority of authorities) {
      if (!PACK_ID_BY_AUTHORITY.has(authority.id)) {
        PACK_ID_BY_AUTHORITY.set(authority.id, new Set());
      }
      PACK_ID_BY_AUTHORITY.get(authority.id).add(pack.pack_id);
    }
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
  const REMAINING_STATE_ROUTE_CONFIGS = Object.freeze({
    "in-ar-routing": Object.freeze({
      state_code: "AR", authority_id: "ar-statewide-unverified", name: "Arunachal Pradesh",
      scope: "Full State of Arunachal Pradesh", relation_id: 2027346,
      region_id: "arunachal-pradesh-state", routing_source: "osm_ar_state_boundary",
      geometry_sha256: "cf8ed446e02b71170c565a43fec2ac4dc730f10f13adf34ab6e95ea4e898134d",
      envelope: Object.freeze({ min_lng: 91.5623082, min_lat: 26.650863, max_lng: 97.3950905, max_lat: 29.3745566 }),
    }),
    "in-as-routing": Object.freeze({
      state_code: "AS", authority_id: "as-statewide-unverified", name: "Assam",
      scope: "Full State of Assam", relation_id: 2025886,
      region_id: "assam-state", routing_source: "osm_as_state_boundary",
      geometry_sha256: "fd8ac250fc17bfb9c07ab2035d84d874195bd98db60e5bc98731c136a83a59f4",
      envelope: Object.freeze({ min_lng: 89.6986005, min_lat: 24.136033, max_lng: 96.0124397, max_lat: 27.9712428 }),
    }),
    "in-gj-state-routing": Object.freeze({
      state_code: "GJ", authority_id: "gj-statewide-unverified", name: "Gujarat",
      scope: "Full State of Gujarat", relation_id: 1949080,
      region_id: "gujarat-state", routing_source: "osm_gj_state_boundary",
      geometry_sha256: "11539b5cd872f93d70f7f1ba5fad4ef6fa237e808ff9721d0d6523ec5850024f",
      envelope: Object.freeze({ min_lng: 68.1756585, min_lat: 20.1195321, max_lng: 74.4764325, max_lat: 24.7118932 }),
    }),
    "in-hr-routing": Object.freeze({
      state_code: "HR", authority_id: "hr-statewide-unverified", name: "Haryana",
      scope: "Full State of Haryana", relation_id: 1942601,
      region_id: "haryana-state", routing_source: "osm_hr_state_boundary",
      geometry_sha256: "3c0c4ddb274c2794ae25aaae3a827c2cbf3d846a49a81cf57ede9161ea5aab14",
      envelope: Object.freeze({ min_lng: 74.4735074, min_lat: 27.6526273, max_lng: 77.6021432, max_lat: 30.9287706 }),
    }),
    "in-hp-routing": Object.freeze({
      state_code: "HP", authority_id: "hp-statewide-unverified", name: "Himachal Pradesh",
      scope: "Full State of Himachal Pradesh", relation_id: 364186,
      region_id: "himachal-pradesh-state", routing_source: "osm_hp_state_boundary",
      geometry_sha256: "6dd4a1580243a28e582a9eed78d8c005c07654602c3318c846c1077b13221b66",
      envelope: Object.freeze({ min_lng: 75.5940055, min_lat: 30.3771701, max_lng: 79.0123843, max_lat: 33.2556686 }),
    }),
    "in-jh-routing": Object.freeze({
      state_code: "JH", authority_id: "jh-statewide-unverified", name: "Jharkhand",
      scope: "Full State of Jharkhand", relation_id: 1960191,
      region_id: "jharkhand-state", routing_source: "osm_jh_state_boundary",
      geometry_sha256: "14279df76d745224f5bb9c4076c73a7b5bdaa6f48aa56253f41507500ee46fd7",
      envelope: Object.freeze({ min_lng: 83.3281137, min_lat: 21.9700317, max_lng: 87.9628253, max_lat: 25.3489225 }),
    }),
    "in-mn-routing": Object.freeze({
      state_code: "MN", authority_id: "mn-statewide-unverified", name: "Manipur",
      scope: "Full State of Manipur", relation_id: 2027869,
      region_id: "manipur-state", routing_source: "osm_mn_state_boundary",
      geometry_sha256: "1e1fac685f3309d7692c2b3164a5e6b69d26b51e7dd57cae5399ed21cbd984e3",
      envelope: Object.freeze({ min_lng: 92.9707074, min_lat: 23.8336205, max_lng: 94.745244, max_lat: 25.6921015 }),
    }),
    "in-ml-routing": Object.freeze({
      state_code: "ML", authority_id: "ml-statewide-unverified", name: "Meghalaya",
      scope: "Full State of Meghalaya", relation_id: 2027521,
      region_id: "meghalaya-state", routing_source: "osm_ml_state_boundary",
      geometry_sha256: "db598e097615cee41266f4002c503a0b26ca6926eb470360dc38bd6a1d563bcf",
      envelope: Object.freeze({ min_lng: 89.814444, min_lat: 25.0306475, max_lng: 92.8027367, max_lat: 26.1181651 }),
    }),
    "in-mz-routing": Object.freeze({
      state_code: "MZ", authority_id: "mz-statewide-unverified", name: "Mizoram",
      scope: "Full State of Mizoram", relation_id: 2029046,
      region_id: "mizoram-state", routing_source: "osm_mz_state_boundary",
      geometry_sha256: "1b968536683a5c4940b0da3365c8bb682a145be2f11aff152c498edbd6af2802",
      envelope: Object.freeze({ min_lng: 92.2602224, min_lat: 21.9400528, max_lng: 93.4373696, max_lat: 24.5231304 }),
    }),
    "in-nl-routing": Object.freeze({
      state_code: "NL", authority_id: "nl-statewide-unverified", name: "Nagaland",
      scope: "Full State of Nagaland", relation_id: 2027973,
      region_id: "nagaland-state", routing_source: "osm_nl_state_boundary",
      geometry_sha256: "af89c3e42da64a065bf363339818b8d547aec93073ffd6e2f08680abc3777949",
      envelope: Object.freeze({ min_lng: 93.3267005, min_lat: 25.1984274, max_lng: 95.2423775, max_lat: 27.035801 }),
    }),
    "in-sk-routing": Object.freeze({
      state_code: "SK", authority_id: "sk-statewide-unverified", name: "Sikkim",
      scope: "Full State of Sikkim", relation_id: 1791324,
      region_id: "sikkim-state", routing_source: "osm_sk_state_boundary",
      geometry_sha256: "4eaaf81eec2214272b63a7285f8a02bb6ccf008a3f51ef57c4f888574b3c04e8",
      envelope: Object.freeze({ min_lng: 88.0120333, min_lat: 27.0792596, max_lng: 88.9211683, max_lat: 28.1240465 }),
    }),
    "in-tr-routing": Object.freeze({
      state_code: "TR", authority_id: "tr-statewide-unverified", name: "Tripura",
      scope: "Full State of Tripura", relation_id: 2026458,
      region_id: "tripura-state", routing_source: "osm_tr_state_boundary",
      geometry_sha256: "83f92257ffa7e8b496f3974b671fb68c29c212c0780c9170a189d50a7cd1fde8",
      envelope: Object.freeze({ min_lng: 91.1508098, min_lat: 22.9376106, max_lng: 92.33585, max_lat: 24.530878 }),
    }),
    "in-uk-routing": Object.freeze({
      state_code: "UK", authority_id: "uk-statewide-unverified", name: "Uttarakhand",
      scope: "Full State of Uttarakhand", relation_id: 9987086,
      region_id: "uttarakhand-state", routing_source: "osm_uk_state_boundary",
      geometry_sha256: "b80016e74aee194fe734062b237855b3a3c8458a74dc345c07657fbc9cdd620a",
      envelope: Object.freeze({ min_lng: 77.57133, min_lat: 28.7243243, max_lng: 81.044789, max_lat: 31.459016 }),
    }),
    "in-an-routing": Object.freeze({
      state_code: "AN", authority_id: "an-statewide-unverified", name: "Andaman and Nicobar Islands",
      scope: "Full Union Territory of Andaman and Nicobar Islands", relation_id: 2025855,
      region_id: "andaman-and-nicobar-islands-ut", routing_source: "osm_an_ut_boundary",
      geometry_sha256: "8fef75a710cb1c1f3b33f5c13253870a2c9d077768c354f65d4558bac0a2b090",
      envelope: Object.freeze({ min_lng: 92.2042072, min_lat: 6.7562674, max_lng: 94.2773214, max_lat: 13.6753133 }),
    }),
    "in-ch-routing": Object.freeze({
      state_code: "CH", authority_id: "ch-statewide-unverified", name: "Chandigarh",
      scope: "Full Union Territory of Chandigarh", relation_id: 1942809,
      region_id: "chandigarh-ut", routing_source: "osm_ch_ut_boundary",
      geometry_sha256: "937c65d7f4fb34ddace22a55f32cc87fb6ca409b1f2586babbe4ca185cc6074d",
      envelope: Object.freeze({ min_lng: 76.7049857, min_lat: 30.664974, max_lng: 76.849028, max_lat: 30.7949512 }),
    }),
    "in-dh-routing": Object.freeze({
      state_code: "DH", authority_id: "dh-statewide-unverified", name: "Dadra and Nagar Haveli and Daman and Diu",
      scope: "Full Union Territory of Dadra and Nagar Haveli and Daman and Diu", relation_id: 1952530,
      region_id: "dadra-nagar-haveli-daman-diu-ut", routing_source: "osm_dh_ut_boundary",
      geometry_sha256: "64dd2ec5067967a41915ba7e3ea1f7c6f29da0efd8fb91a1ad05ecf750d659bb",
      envelope: Object.freeze({ min_lng: 70.8734588, min_lat: 20.0473907, max_lng: 73.2178258, max_lat: 20.7677936 }),
    }),
    "in-jk-routing": Object.freeze({
      state_code: "JK", authority_id: "jk-statewide-unverified", name: "Jammu and Kashmir",
      scope: "Full Union Territory of Jammu and Kashmir", relation_id: 1943188,
      region_id: "jammu-and-kashmir-ut", routing_source: "osm_jk_ut_boundary",
      geometry_sha256: "50af753f0a7a0ddcc52151bba8abb22024bf1d3629a5b2ead431524f800b9754",
      envelope: Object.freeze({ min_lng: 73.7500338, min_lat: 32.2763569, max_lng: 76.7803165, max_lat: 34.7871414 }),
    }),
    "in-la-routing": Object.freeze({
      state_code: "LA", authority_id: "la-statewide-unverified", name: "Ladakh",
      scope: "Full Union Territory of Ladakh", relation_id: 5515045,
      region_id: "ladakh-ut", routing_source: "osm_la_ut_boundary",
      geometry_sha256: "816c039eb05986e1a75f53e7f4812b334bba73b32b73196b8813c8e90c9ac28c",
      envelope: Object.freeze({ min_lng: 75.3269726, min_lat: 32.33574, max_lng: 79.460728, max_lat: 35.6729307 }),
    }),
    "in-ld-routing": Object.freeze({
      state_code: "LD", authority_id: "ld-statewide-unverified", name: "Lakshadweep",
      scope: "Full Union Territory of Lakshadweep", relation_id: 2027460,
      region_id: "lakshadweep-ut", routing_source: "osm_ld_ut_boundary",
      geometry_sha256: "d58545bdcecaa056484fb3ea7c6b3531ce744b5b120f6bac0185c3346cfd3a04",
      envelope: Object.freeze({ min_lng: 71.5180377, min_lat: 8.0648198, max_lng: 73.9061436, max_lat: 12.6010064 }),
    }),
    "in-py-routing": Object.freeze({
      state_code: "PY", authority_id: "py-statewide-unverified", name: "Puducherry",
      scope: "Full Union Territory of Puducherry", relation_id: 107001,
      region_id: "puducherry-ut", routing_source: "osm_py_ut_boundary",
      geometry_sha256: "8bbb3f321588dacbce22ab379d9ba187d8c97a6a61b553f076a6da06a54c7c2d",
      envelope: Object.freeze({ min_lng: 75.5265863, min_lat: 10.827721, max_lng: 82.3137136, max_lat: 16.7617112 }),
    }),
  });
  const PACK_SITE_ROOT = "https://coding-parrot.github.io/pothole-reporter/";
  const SUPPORTED_STATE_PACKS = Object.freeze({
    "in-dl-routing": { state_code: "DL", kind: "routing", adapter: "delhi-nct-v1" },
    "in-mh-routing": { state_code: "MH", kind: "routing", adapter: "maharashtra-statewide-v1" },
    "in-wb-routing": { state_code: "WB", kind: "routing", adapter: "west-bengal-statewide-v1" },
    "in-pb-routing": { state_code: "PB", kind: "routing", adapter: "statewide-general-v1" },
    "in-top50-routing": { state_code: "IN", kind: "routing", adapter: "major-city-structured-v1" },
    "in-ka-routing": { state_code: "KA", kind: "routing", adapter: "karnataka-kgis-v1" },
    "in-ka-state-routing": { state_code: "KA", kind: "routing", adapter: "statewide-general-v1" },
    "in-ka-tenders": { state_code: "KA", kind: "tenders", adapter: "karnataka-carriageway-indexed-v2" },
    "in-kl-routing": { state_code: "KL", kind: "routing", adapter: "statewide-general-v1" },
    "in-up-routing": { state_code: "UP", kind: "routing", adapter: "statewide-general-v1" },
    "in-cg-routing": { state_code: "CG", kind: "routing", adapter: "statewide-general-v1" },
    "in-rj-routing": { state_code: "RJ", kind: "routing", adapter: "statewide-general-v1" },
    "in-ga-routing": { state_code: "GA", kind: "routing", adapter: "statewide-general-v1" },
    "in-mp-routing": { state_code: "MP", kind: "routing", adapter: "statewide-general-v1" },
    "in-br-routing": { state_code: "BR", kind: "routing", adapter: "statewide-general-v1" },
    "in-od-routing": { state_code: "OD", kind: "routing", adapter: "statewide-general-v1" },
    "in-tn-routing": { state_code: "TN", kind: "routing", adapter: "municipal-city-v1" },
    "in-tn-state-routing": { state_code: "TN", kind: "routing", adapter: "statewide-general-v1" },
    "in-ap-routing": { state_code: "AP", kind: "routing", adapter: "statewide-general-v1" },
    "in-tg-state-routing": { state_code: "TG", kind: "routing", adapter: "statewide-general-v1" },
    "in-tg-routing": { state_code: "TG", kind: "routing", adapter: "municipal-city-v1" },
    "in-gj-routing": { state_code: "GJ", kind: "routing", adapter: "municipal-city-v1" },
    ...Object.fromEntries(Object.entries(REMAINING_STATE_ROUTE_CONFIGS).map(
      ([packId, config]) => [packId, {
        state_code: config.state_code, kind: "routing", adapter: "statewide-general-v1",
      }],
    )),
  });
  const STATE_PACK_MAX_BYTES = 16 * 1024 * 1024;
  const STATE_PACK_FETCH_TIMEOUT_MS = 30000;
  // Contract context is optional and must never make an accepted report feel stuck.
  // The required routing packs retain their longer timeout; these catalogs get a short
  // network deadline and the matcher also bounds the complete lookup below.
  const OPTIONAL_CATALOG_TIMEOUT_MS = 5000;
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
        const response = await fetch("pack-manifest-v1.35.json", { cache: "no-store" });
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

  const TOP50_MAJOR_CITY_RANKS = new Set([
    9, 10, 11, 12, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26,
    27, 30, 31, 32, 33, 34, 35, 36, 37, 38, 41, 42, 45, 46, 47, 48, 49, 50,
  ]);
  const TOP50_AUTHORITY_BY_STATE = Object.freeze({
    GJ: "in-gj-enagar", RJ: "in-rj-sampark", UP: "in-up-jansunwai",
    MP: "in-mp-cm-helpline", TN: "in-tn-cm-helpline", KL: "in-kl-ksmart",
    BR: "in-br-lok-shikayat", AP: "in-ap-puramithra", HR: "in-hr-nagar-darshan",
    JH: "in-jh-municipal-grievance", JK: "in-jk-samadhan", CG: "in-cg-nidaan",
  });

  async function validatePunjabPayload(pack) {
    const payload = pack.payload;
    const region = payload && payload.region;
    const fields = [
      "id", "authority_id", "name", "scope", "osm_relation_id", "source_name",
      "source_home_url", "source_url", "source_license", "attribution", "routing_note",
      "limitations", "coordinate_precision", "bbox", "geometry_sha256", "geometry",
    ];
    const digest = region && hasCoverageGeometry(region.geometry)
      ? await sha256Hex(JSON.stringify(region.geometry)) : null;
    const calculated = region && municipalGeometryBounds(region.geometry);
    const near = (first, second) => Math.abs(first - second) <= 1e-7;
    return exactObjectKeys(payload, ["version", "retrieved_at", "region"])
      && payload.version === 1 && payload.retrieved_at === pack.generated_at
      && exactObjectKeys(region, fields)
      && region.id === "punjab-state"
      && region.authority_id === "pb-statewide-unverified"
      && region.name === "Punjab" && Number(region.osm_relation_id) === 1942686
      && Number.isInteger(region.coordinate_precision) && region.coordinate_precision === 7
      && validMunicipalEnvelope(region.bbox) && calculated
      && Object.keys(calculated).every((key) => near(calculated[key], region.bbox[key]))
      && region.geometry_sha256 === PUNJAB_STATE_GEOMETRY_SHA256
      && digest === PUNJAB_STATE_GEOMETRY_SHA256
      && ["scope", "source_name", "source_license", "attribution", "routing_note"]
        .every((field) => typeof region[field] === "string"
          && !!region[field] && region[field].length <= 1000)
      && ["source_home_url", "source_url"]
        .every((field) => /^https:\/\/[^\s]+$/.test(String(region[field] || "")))
      && Array.isArray(region.limitations) && region.limitations.length > 0
      && region.limitations.length <= 10
      && region.limitations.every((item) => typeof item === "string"
        && !!item && item.length <= 500);
  }

  async function validateTamilNaduPayload(pack) {
    const payload = pack.payload;
    const region = payload && payload.region;
    const fields = [
      "id", "authority_id", "name", "scope", "osm_relation_id", "source_name",
      "source_home_url", "source_url", "source_license", "attribution", "routing_note",
      "limitations", "coordinate_precision", "bbox", "geometry_sha256", "geometry",
    ];
    const digest = region && hasCoverageGeometry(region.geometry)
      ? await sha256Hex(JSON.stringify(region.geometry)) : null;
    const calculated = region && municipalGeometryBounds(region.geometry);
    const near = (first, second) => Math.abs(first - second) <= 1e-7;
    return exactObjectKeys(payload, ["version", "retrieved_at", "region"])
      && payload.version === 1 && payload.retrieved_at === pack.generated_at
      && exactObjectKeys(region, fields)
      && region.id === "tamil-nadu-state"
      && region.authority_id === "tn-statewide-unverified"
      && region.name === "Tamil Nadu" && Number(region.osm_relation_id) === 96905
      && Number.isInteger(region.coordinate_precision) && region.coordinate_precision === 7
      && validMunicipalEnvelope(region.bbox) && calculated
      && Object.keys(calculated).every((key) => near(calculated[key], region.bbox[key]))
      && region.geometry_sha256 === TAMIL_NADU_STATE_GEOMETRY_SHA256
      && digest === TAMIL_NADU_STATE_GEOMETRY_SHA256
      && ["scope", "source_name", "source_license", "attribution", "routing_note"]
        .every((field) => typeof region[field] === "string"
          && !!region[field] && region[field].length <= 1000)
      && ["source_home_url", "source_url"]
        .every((field) => /^https:\/\/[^\s]+$/.test(String(region[field] || "")))
      && Array.isArray(region.limitations) && region.limitations.length > 0
      && region.limitations.length <= 10
      && region.limitations.every((item) => typeof item === "string"
        && !!item && item.length <= 500);
  }

  async function validateAndhraPradeshPayload(pack) {
    const payload = pack.payload;
    const region = payload && payload.region;
    const fields = [
      "id", "authority_id", "name", "scope", "osm_relation_id", "source_name",
      "source_home_url", "source_url", "source_license", "attribution", "routing_note",
      "limitations", "coordinate_precision", "bbox", "geometry_sha256", "geometry",
    ];
    const digest = region && hasCoverageGeometry(region.geometry)
      ? await sha256Hex(JSON.stringify(region.geometry)) : null;
    const calculated = region && municipalGeometryBounds(region.geometry);
    const near = (first, second) => Math.abs(first - second) <= 1e-7;
    return exactObjectKeys(payload, ["version", "retrieved_at", "region"])
      && payload.version === 1 && payload.retrieved_at === pack.generated_at
      && exactObjectKeys(region, fields)
      && region.id === "andhra-pradesh-state"
      && region.authority_id === "ap-statewide-unverified"
      && region.name === "Andhra Pradesh" && Number(region.osm_relation_id) === 2022095
      && Number.isInteger(region.coordinate_precision) && region.coordinate_precision === 7
      && validMunicipalEnvelope(region.bbox) && calculated
      && Object.keys(calculated).every((key) => near(calculated[key], region.bbox[key]))
      && region.geometry_sha256 === ANDHRA_PRADESH_STATE_GEOMETRY_SHA256
      && digest === ANDHRA_PRADESH_STATE_GEOMETRY_SHA256
      && ["scope", "source_name", "source_license", "attribution", "routing_note"]
        .every((field) => typeof region[field] === "string"
          && !!region[field] && region[field].length <= 1000)
      && ["source_home_url", "source_url"]
        .every((field) => /^https:\/\/[^\s]+$/.test(String(region[field] || "")))
      && Array.isArray(region.limitations) && region.limitations.length > 0
      && region.limitations.length <= 10
      && region.limitations.every((item) => typeof item === "string"
        && !!item && item.length <= 500);
  }

  async function validateTelanganaPayload(pack) {
    const payload = pack.payload;
    const region = payload && payload.region;
    const fields = [
      "id", "authority_id", "name", "scope", "osm_relation_id", "source_name",
      "source_home_url", "source_url", "source_license", "attribution", "routing_note",
      "limitations", "coordinate_precision", "bbox", "geometry_sha256", "geometry",
    ];
    const digest = region && hasCoverageGeometry(region.geometry)
      ? await sha256Hex(JSON.stringify(region.geometry)) : null;
    const calculated = region && municipalGeometryBounds(region.geometry);
    const near = (first, second) => Math.abs(first - second) <= 1e-7;
    return exactObjectKeys(payload, ["version", "retrieved_at", "region"])
      && payload.version === 1 && payload.retrieved_at === pack.generated_at
      && exactObjectKeys(region, fields)
      && region.id === "telangana-state"
      && region.authority_id === "tg-statewide-unverified"
      && region.name === "Telangana" && Number(region.osm_relation_id) === 3250963
      && Number.isInteger(region.coordinate_precision) && region.coordinate_precision === 7
      && validMunicipalEnvelope(region.bbox) && calculated
      && Object.keys(calculated).every((key) => near(calculated[key], region.bbox[key]))
      && region.geometry_sha256 === TELANGANA_STATE_GEOMETRY_SHA256
      && digest === TELANGANA_STATE_GEOMETRY_SHA256
      && ["scope", "source_name", "source_license", "attribution", "routing_note"]
        .every((field) => typeof region[field] === "string"
          && !!region[field] && region[field].length <= 1000)
      && ["source_home_url", "source_url"]
        .every((field) => /^https:\/\/[^\s]+$/.test(String(region[field] || "")))
      && Array.isArray(region.limitations) && region.limitations.length > 0
      && region.limitations.length <= 10
      && region.limitations.every((item) => typeof item === "string"
        && !!item && item.length <= 500);
  }

  async function validatePinnedStatewidePayload(pack, expected) {
    const payload = pack.payload;
    const region = payload && payload.region;
    const fields = [
      "id", "authority_id", "name", "scope", "osm_relation_id", "source_name",
      "source_home_url", "source_url", "source_license", "attribution", "routing_note",
      "limitations", "coordinate_precision", "bbox", "geometry_sha256", "geometry",
    ];
    const digest = region && hasCoverageGeometry(region.geometry)
      ? await sha256Hex(JSON.stringify(region.geometry)) : null;
    const calculated = region && municipalGeometryBounds(region.geometry);
    const near = (first, second) => Math.abs(first - second) <= 1e-7;
    return exactObjectKeys(payload, ["version", "retrieved_at", "region"])
      && payload.version === 1 && payload.retrieved_at === pack.generated_at
      && exactObjectKeys(region, fields)
      && region.id === expected.region_id && region.authority_id === expected.authority_id
      && region.name === expected.name && region.scope === expected.scope
      && Number(region.osm_relation_id) === expected.relation_id
      && region.source_home_url === `https://www.openstreetmap.org/relation/${expected.relation_id}`
      && region.source_url.startsWith("https://nominatim.openstreetmap.org/lookup?")
      && region.source_url.includes(`osm_ids=R${expected.relation_id}`)
      && Number.isInteger(region.coordinate_precision) && region.coordinate_precision === 7
      && validMunicipalEnvelope(region.bbox) && calculated
      && Object.keys(calculated).every((key) => near(calculated[key], region.bbox[key]))
      && region.geometry_sha256 === expected.geometry_sha256
      && digest === expected.geometry_sha256
      && ["source_name", "source_license", "attribution", "routing_note"]
        .every((field) => typeof region[field] === "string"
          && !!region[field] && region[field].length <= 1000)
      && Array.isArray(region.limitations) && region.limitations.length > 0
      && region.limitations.length <= 10
      && region.limitations.every((item) => typeof item === "string"
        && !!item && item.length <= 500);
  }

  const validateKarnatakaStatePayload = (pack) => validatePinnedStatewidePayload(pack, {
    region_id: "karnataka-state", authority_id: "ka-statewide-unverified",
    name: "Karnataka", scope: "Full State of Karnataka", relation_id: 2019939,
    geometry_sha256: KARNATAKA_STATE_GEOMETRY_SHA256,
  });

  const validateKeralaPayload = (pack) => validatePinnedStatewidePayload(pack, {
    region_id: "kerala-state", authority_id: "kl-statewide-unverified",
    name: "Kerala", scope: "Full State of Kerala; excludes Mahe, Puducherry Union Territory",
    relation_id: 2018151, geometry_sha256: KERALA_STATE_GEOMETRY_SHA256,
  });

  const validateUttarPradeshPayload = (pack) => validatePinnedStatewidePayload(pack, {
    region_id: "uttar-pradesh-state", authority_id: "up-statewide-unverified",
    name: "Uttar Pradesh",
    scope: "Full State of Uttar Pradesh; excludes Delhi National Capital Territory",
    relation_id: 1942587, geometry_sha256: UTTAR_PRADESH_STATE_GEOMETRY_SHA256,
  });

  const validateChhattisgarhPayload = (pack) => validatePinnedStatewidePayload(pack, {
    region_id: "chhattisgarh-state", authority_id: "cg-statewide-unverified",
    name: "Chhattisgarh", scope: "Full State of Chhattisgarh",
    relation_id: 1972004, geometry_sha256: CHHATTISGARH_STATE_GEOMETRY_SHA256,
  });

  const validateRajasthanPayload = (pack) => validatePinnedStatewidePayload(pack, {
    region_id: "rajasthan-state", authority_id: "rj-statewide-unverified",
    name: "Rajasthan", scope: "Full State of Rajasthan",
    relation_id: 1942920, geometry_sha256: RAJASTHAN_STATE_GEOMETRY_SHA256,
  });

  const validateGoaPayload = (pack) => validatePinnedStatewidePayload(pack, {
    region_id: "goa-state", authority_id: "ga-statewide-unverified",
    name: "Goa", scope: "Full State of Goa",
    relation_id: 11251493, geometry_sha256: GOA_STATE_GEOMETRY_SHA256,
  });

  const validateMadhyaPradeshPayload = (pack) => validatePinnedStatewidePayload(pack, {
    region_id: "madhya-pradesh-state", authority_id: "mp-statewide-unverified",
    name: "Madhya Pradesh", scope: "Full State of Madhya Pradesh",
    relation_id: 1950071, geometry_sha256: MADHYA_PRADESH_STATE_GEOMETRY_SHA256,
  });

  const validateBiharPayload = (pack) => validatePinnedStatewidePayload(pack, {
    region_id: "bihar-state", authority_id: "br-statewide-unverified",
    name: "Bihar", scope: "Full State of Bihar",
    relation_id: 1958982, geometry_sha256: BIHAR_STATE_GEOMETRY_SHA256,
  });

  const validateOdishaPayload = (pack) => validatePinnedStatewidePayload(pack, {
    region_id: "odisha-state", authority_id: "od-statewide-unverified",
    name: "Odisha", scope: "Full State of Odisha",
    relation_id: 1984022, geometry_sha256: ODISHA_STATE_GEOMETRY_SHA256,
  });

  function validateMajorCityPayload(pack) {
    const payload = pack.payload;
    const expectedAuthorityIds = new Set(Object.values(TOP50_AUTHORITY_BY_STATE));
    const authorityIds = new Set(pack.authorities.map((authority) => authority.id));
    if (!exactObjectKeys(payload, ["version", "retrieved_at", "regions"])
        || payload.version !== 1 || payload.retrieved_at !== pack.generated_at
        || !Array.isArray(payload.regions) || payload.regions.length !== 35
        || !sameSet([...authorityIds], expectedAuthorityIds)) return false;
    const regionFields = [
      "rank", "id", "authority_id", "name", "state_code", "scope", "routing_mode",
      "routing_source", "match_value", "state_aliases", "place_aliases", "envelope",
      "source_name", "source_home_url", "source_url", "source_license", "attribution",
      "official_scope_reference", "routing_note", "limitations", "exclusions",
      "source_object_id", "supported_issue_types",
    ];
    const seenRanks = new Set(), seenIds = new Set(), usedAuthorities = new Set();
    for (const region of payload.regions) {
      const expectedAuthority = region && TOP50_AUTHORITY_BY_STATE[region.state_code];
      const sourceMatch = region && typeof region.match_value === "string"
        ? region.match_value.match(/^OpenStreetMap (node|way|relation) ([1-9][0-9]*)$/) : null;
      const strings = [
        "id", "authority_id", "name", "state_code", "scope", "routing_source",
        "match_value", "source_name", "source_home_url", "source_url", "source_license",
        "attribution", "official_scope_reference", "routing_note", "source_object_id",
      ];
      if (!exactObjectKeys(region, regionFields)
          || !Number.isInteger(region.rank) || !TOP50_MAJOR_CITY_RANKS.has(region.rank)
          || seenRanks.has(region.rank)
          || !/^[a-z0-9][a-z0-9-]{2,100}$/.test(String(region.id || ""))
          || seenIds.has(region.id)
          || !expectedAuthority || region.authority_id !== expectedAuthority
          || !authorityIds.has(region.authority_id)
          || region.routing_mode !== "structured_geocode"
          || region.routing_source !== "nominatim_structured_city"
          || strings.some((field) => typeof region[field] !== "string"
            || !region[field] || region[field].length > 1000)
          || !sourceMatch
          || region.source_object_id !== `osm:${sourceMatch[1]}:${sourceMatch[2]}`
          || !/^https:\/\/[^\s]+$/.test(region.source_home_url)
          || !/^https:\/\/[^\s]+$/.test(region.source_url)
          || !/^https:\/\/[^\s]+$/.test(region.official_scope_reference)
          || !validMunicipalAliasList(region.state_aliases)
          || !validMunicipalAliasList(region.place_aliases)
          || !validMunicipalEnvelope(region.envelope)
          || !Array.isArray(region.limitations) || !region.limitations.length
          || region.limitations.length > 10
          || region.limitations.some((item) => typeof item !== "string"
            || !item || item.length > 500)
          || !Array.isArray(region.exclusions) || region.exclusions.length !== 0
          || !Array.isArray(region.supported_issue_types)
          || new Set(region.supported_issue_types).size !== ISSUE_TYPES.length
          || !sameSet(region.supported_issue_types, new Set(ISSUE_TYPES))) {
        return false;
      }
      seenRanks.add(region.rank);
      seenIds.add(region.id);
      usedAuthorities.add(region.authority_id);
    }
    return sameSet([...seenRanks], TOP50_MAJOR_CITY_RANKS)
      && sameSet([...usedAuthorities], expectedAuthorityIds);
  }

  async function validateRoutingPack(resource, pack) {
    validatePackEnvelope(resource, pack, "routing");
    if (!Array.isArray(pack.authorities) || pack.authorities.length > 1000) {
      throw new Error("Routing pack authority list is invalid.");
    }
    validateOfficialHandoffRegistry(pack.authorities);
    const payload = pack.payload;
    const remainingConfig = REMAINING_STATE_ROUTE_CONFIGS[resource.pack_id];
    if (remainingConfig) {
      if (pack.state_code !== remainingConfig.state_code
          || pack.authorities.length !== 1
          || pack.authorities[0].id !== remainingConfig.authority_id
          || !await validatePinnedStatewidePayload(pack, {
            region_id: remainingConfig.region_id,
            authority_id: remainingConfig.authority_id,
            name: remainingConfig.name,
            scope: remainingConfig.scope,
            relation_id: remainingConfig.relation_id,
            geometry_sha256: remainingConfig.geometry_sha256,
          })) {
        throw new Error("State/UT routing pack failed its boundary, source or authority checks.");
      }
    } else if (resource.pack_id === "in-dl-routing") {
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
    } else if (resource.pack_id === "in-pb-routing") {
      if (pack.authorities.length !== 1
          || pack.authorities[0].id !== "pb-statewide-unverified"
          || !await validatePunjabPayload(pack)) {
        throw new Error("Punjab routing pack failed its boundary, source or authority checks.");
      }
    } else if (resource.pack_id === "in-tn-state-routing") {
      if (pack.authorities.length !== 1
          || pack.authorities[0].id !== "tn-statewide-unverified"
          || !await validateTamilNaduPayload(pack)) {
        throw new Error("Tamil Nadu routing pack failed its boundary, source or authority checks.");
      }
    } else if (resource.pack_id === "in-ap-routing") {
      if (pack.authorities.length !== 1
          || pack.authorities[0].id !== "ap-statewide-unverified"
          || !await validateAndhraPradeshPayload(pack)) {
        throw new Error("Andhra Pradesh routing pack failed its boundary, source or authority checks.");
      }
    } else if (resource.pack_id === "in-tg-state-routing") {
      if (pack.authorities.length !== 1
          || pack.authorities[0].id !== "tg-statewide-unverified"
          || !await validateTelanganaPayload(pack)) {
        throw new Error("Telangana routing pack failed its boundary, source or authority checks.");
      }
    } else if (resource.pack_id === "in-ka-state-routing") {
      if (pack.authorities.length !== 1
          || pack.authorities[0].id !== "ka-statewide-unverified"
          || !await validateKarnatakaStatePayload(pack)) {
        throw new Error("Karnataka statewide routing pack failed its boundary, source or authority checks.");
      }
    } else if (resource.pack_id === "in-kl-routing") {
      if (pack.authorities.length !== 1
          || pack.authorities[0].id !== "kl-statewide-unverified"
          || !await validateKeralaPayload(pack)) {
        throw new Error("Kerala routing pack failed its boundary, source or authority checks.");
      }
    } else if (resource.pack_id === "in-up-routing") {
      if (pack.authorities.length !== 1
          || pack.authorities[0].id !== "up-statewide-unverified"
          || !await validateUttarPradeshPayload(pack)) {
        throw new Error("Uttar Pradesh routing pack failed its boundary, source or authority checks.");
      }
    } else if (resource.pack_id === "in-cg-routing") {
      if (pack.authorities.length !== 1
          || pack.authorities[0].id !== "cg-statewide-unverified"
          || !await validateChhattisgarhPayload(pack)) {
        throw new Error("Chhattisgarh routing pack failed its boundary, source or authority checks.");
      }
    } else if (resource.pack_id === "in-rj-routing") {
      if (pack.authorities.length !== 1
          || pack.authorities[0].id !== "rj-statewide-unverified"
          || !await validateRajasthanPayload(pack)) {
        throw new Error("Rajasthan routing pack failed its boundary, source or authority checks.");
      }
    } else if (resource.pack_id === "in-ga-routing") {
      if (pack.authorities.length !== 1
          || pack.authorities[0].id !== "ga-statewide-unverified"
          || !await validateGoaPayload(pack)) {
        throw new Error("Goa routing pack failed its boundary, source or authority checks.");
      }
    } else if (resource.pack_id === "in-mp-routing") {
      if (pack.authorities.length !== 1
          || pack.authorities[0].id !== "mp-statewide-unverified"
          || !await validateMadhyaPradeshPayload(pack)) {
        throw new Error("Madhya Pradesh routing pack failed its boundary, source or authority checks.");
      }
    } else if (resource.pack_id === "in-br-routing") {
      if (pack.authorities.length !== 1
          || pack.authorities[0].id !== "br-statewide-unverified"
          || !await validateBiharPayload(pack)) {
        throw new Error("Bihar routing pack failed its boundary, source or authority checks.");
      }
    } else if (resource.pack_id === "in-od-routing") {
      if (pack.authorities.length !== 1
          || pack.authorities[0].id !== "od-statewide-unverified"
          || !await validateOdishaPayload(pack)) {
        throw new Error("Odisha routing pack failed its boundary, source or authority checks.");
      }
    } else if (resource.pack_id === "in-top50-routing") {
      if (!validateMajorCityPayload(pack)) {
        throw new Error("Major-city routing pack failed its source, region or authority checks.");
      }
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
    const [manifest, highwayManifest, contractManifest, roadNoticeManifest,
      roadAgreementManifest] = await Promise.all([
      getStatePackManifest(), getHighwayPackManifest(), getContractPackManifest(),
      getRoadNoticeManifest(), getRoadAgreementManifest(),
    ]);
    if (!manifest && !highwayManifest && !contractManifest && !roadNoticeManifest
        && !roadAgreementManifest) {
      return { removed: 0, bytes: 0 };
    }
    let records;
    try { records = await allStatePacks(); } catch (e) { return { removed: 0, bytes: 0 }; }
    const resources = [
      ...Object.values((manifest && manifest.resources) || {}),
      ...Object.values((highwayManifest && highwayManifest.tiles) || {}),
      ...Object.values((contractManifest && contractManifest.resources) || {}),
      ...Object.values((roadNoticeManifest && roadNoticeManifest.resources) || {}),
      ...Object.values((roadAgreementManifest && roadAgreementManifest.resources) || {}),
    ];
    const currentByKey = new Map(resources
      .map((resource) => [statePackCacheKey(resource), resource]));
    const protectedIds = new Set([
      activePackId, ..._statePackPromises.keys(),
      ...[..._highwayTilePromises.keys()].map((id) => `in-nh-${id}`),
      ..._contractPackPromises.keys(),
      ..._roadNoticePackPromises.keys(),
      ..._roadAgreementPackPromises.keys(),
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
        : resource.kind === "highway_contracts"
          ? contractManifest.cache.max_unused_days
          : resource.kind === "road_procurement_notices"
            ? roadNoticeManifest.cache.max_unused_days
          : resource.kind === "road_current_agreements"
            ? roadAgreementManifest.cache.max_unused_days
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
      highwayManifest ? highwayManifest.cache.max_bytes : Infinity,
      contractManifest ? contractManifest.cache.max_bytes : Infinity,
      roadNoticeManifest ? roadNoticeManifest.cache.max_bytes : Infinity,
      roadAgreementManifest ? roadAgreementManifest.cache.max_bytes : Infinity);
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

  // Current/open catalogs are evidence only while their builder-declared review window
  // remains current. A frozen manifest must fail closed instead of turning an old portal
  // status into a claim about today's project or bid state.
  function catalogResourceWithinReview(resource, now = Date.now()) {
    if (!resource || !/^\d{4}-\d{2}-\d{2}$/.test(String(resource.review_after || ""))) {
      return false;
    }
    const deadline = Date.parse(`${resource.review_after}T23:59:59.999Z`);
    return Number.isFinite(deadline) && Number.isFinite(now) && now <= deadline;
  }

  async function fetchOptionalCatalogManifest(filename, validate) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), OPTIONAL_CATALOG_TIMEOUT_MS);
    try {
      const response = await fetch(filename, {
        cache: "no-store", credentials: "omit", referrerPolicy: "no-referrer",
        signal: controller.signal,
      });
      if (!response.ok) return null;
      const text = await response.text();
      if (!text || text.length > 128 * 1024) return null;
      return validate(JSON.parse(text));
    } catch (e) { return null; }
    finally { clearTimeout(timer); }
  }

  // Contract data has a separate, append-only catalog. Keeping it out of the v1.35
  // routing catalog preserves the immutable Play closed-test bundle while allowing the
  // web app and a later Android release to fetch only the nearby State/UT highway pack.
  const CONTRACT_MANIFEST_FILE = "contract-manifest-v1.36.json";
  const CONTRACT_PACK_MAX_BYTES = 8 * 1024 * 1024;
  let _contractPackManifest = null, _contractPackManifestPromise = null;
  const _contractPackMemory = new Map(), _contractPackPromises = new Map();

  function validateContractPackManifest(manifest) {
    if (!exactObjectKeys(manifest,
      ["format", "schema_version", "catalog_version", "generated_at", "cache", "resources"])
        || manifest.format !== "pothole-contract-manifest"
        || manifest.schema_version !== 1 || manifest.catalog_version !== 1
        || !/^\d{4}-\d{2}-\d{2}$/.test(manifest.generated_at)
        || !exactObjectKeys(manifest.cache, ["max_bytes", "max_unused_days"])
        || !Number.isInteger(manifest.cache.max_bytes)
        || manifest.cache.max_bytes < 1024 * 1024 || manifest.cache.max_bytes > 128 * 1024 * 1024
        || !Number.isInteger(manifest.cache.max_unused_days)
        || manifest.cache.max_unused_days < 1 || manifest.cache.max_unused_days > 90
        || !manifest.resources || typeof manifest.resources !== "object"
        || Array.isArray(manifest.resources)) {
      throw new Error("Invalid contract-pack manifest.");
    }
    for (const [packId, resource] of Object.entries(manifest.resources)) {
      const fields = ["pack_id", "state_code", "kind", "pack_version", "schema_version",
        "adapter", "path", "url", "bytes", "sha256", "records", "coverage_scope",
        "source_retrieved_at", "review_after", "licenses"];
      const state = String(resource && resource.state_code || "");
      const pathMatch = resource && typeof resource.path === "string"
        ? resource.path.match(/^packs\/v1\/contracts\/([a-z]{2})\/highways-([0-9a-f]{64})\.json$/)
        : null;
      if (!exactObjectKeys(resource, fields)
          || packId !== `in-nh-contracts-${state.toLowerCase()}`
          || resource.pack_id !== packId || !/^[A-Z]{2}$/.test(state)
          || resource.kind !== "highway_contracts"
          || resource.adapter !== "nhai-nhidcl-public-projects-v1"
          || resource.pack_version !== 1 || resource.schema_version !== 1
          || !pathMatch || pathMatch[1] !== state.toLowerCase()
          || pathMatch[2] !== resource.sha256
          || resource.url !== PACK_SITE_ROOT + resource.path
          || !Number.isInteger(resource.bytes) || resource.bytes <= 0
          || resource.bytes > CONTRACT_PACK_MAX_BYTES
          || !/^[0-9a-f]{64}$/.test(resource.sha256)
          || !Number.isInteger(resource.records) || resource.records <= 0
          || resource.records > 10000
          || typeof resource.coverage_scope !== "string" || !resource.coverage_scope
          || !/^\d{4}-\d{2}-\d{2}$/.test(resource.source_retrieved_at)
          || !/^\d{4}-\d{2}-\d{2}$/.test(resource.review_after)
          || !Array.isArray(resource.licenses) || !resource.licenses.length
          || resource.licenses.some((item) => typeof item !== "string" || !item)) {
        throw new Error(`Invalid contract-pack resource: ${packId}`);
      }
    }
    return manifest;
  }

  async function getContractPackManifest() {
    if (_contractPackManifest) return _contractPackManifest;
    if (_contractPackManifestPromise) return _contractPackManifestPromise;
    _contractPackManifestPromise = fetchOptionalCatalogManifest(
      CONTRACT_MANIFEST_FILE, validateContractPackManifest).then((manifest) => {
      _contractPackManifest = manifest;
      return manifest;
    });
    const result = await _contractPackManifestPromise;
    _contractPackManifestPromise = null;
    return result;
  }

  const validContractDate = (value) => value === null
    || (typeof value === "string" && value.length <= 40);

  function validateHighwayContractPack(resource, pack) {
    if (!exactObjectKeys(pack, ["format", "schema_version", "pack_id", "pack_version",
      "state_code", "adapter", "generated_at", "contracts"])
        || pack.format !== "pothole-highway-contract-pack"
        || pack.schema_version !== 1 || pack.pack_version !== 1
        || pack.pack_id !== resource.pack_id || pack.state_code !== resource.state_code
        || pack.adapter !== resource.adapter || pack.generated_at !== resource.source_retrieved_at
        || !Array.isArray(pack.contracts) || pack.contracts.length !== resource.records) {
      throw new Error("Highway contract-pack envelope is invalid.");
    }
    const fields = new Set(["record_id", "reference_label", "reference_value", "state_code",
      "agency", "lifecycle", "lifecycle_status", "title", "highway_refs", "chainages",
      "contractor", "published_at", "start_date", "likely_completion_date", "division",
      "source_name", "source_url", "retrieved_at", "scope_verified", "segment_verified",
      "award_verified", "dlp_verified"]);
    const seen = new Set();
    for (const row of pack.contracts) {
      if (!exactObjectKeys(row, fields)
          || typeof row.record_id !== "string" || !row.record_id || row.record_id.length > 160
          || !["UPC", "Tender ID", "NHIDCL notice"].includes(row.reference_label)
          || typeof row.reference_value !== "string" || !row.reference_value
          || row.reference_value.length > 200 || row.state_code !== resource.state_code
          || !["NHAI", "MoRTH", "NHIDCL"].includes(row.agency)
          || !["current_project", "procurement_notice"].includes(row.lifecycle)
          || typeof row.lifecycle_status !== "string" || !row.lifecycle_status
          || row.lifecycle_status.length > 160
          || typeof row.title !== "string" || !row.title || row.title.length > 1200
          || !Array.isArray(row.highway_refs) || !row.highway_refs.length
          || row.highway_refs.length > 12
          || row.highway_refs.some((ref) => typeof ref !== "string" || !HIGHWAY_REF_RE.test(ref))
          || !Array.isArray(row.chainages) || row.chainages.length > 16
          || row.chainages.some((range) => !exactObjectKeys(range, ["start_km", "end_km"])
            || !Number.isFinite(range.start_km) || !Number.isFinite(range.end_km)
            || range.start_km < 0 || range.end_km < range.start_km || range.end_km > 10000)
          || !(row.contractor === null || (typeof row.contractor === "string"
            && row.contractor.length > 0 && row.contractor.length <= 300))
          || ![row.published_at, row.start_date, row.likely_completion_date].every(validContractDate)
          || !(row.division === null || (typeof row.division === "string" && row.division.length <= 300))
          || typeof row.source_name !== "string" || !row.source_name
          || !/^https:\/\//.test(String(row.source_url || ""))
          || !/^\d{4}-\d{2}-\d{2}$/.test(String(row.retrieved_at || ""))
          || row.scope_verified !== true || row.segment_verified !== false
          || typeof row.award_verified !== "boolean" || row.dlp_verified !== false
          || (row.lifecycle === "procurement_notice"
            && (row.contractor !== null || row.award_verified !== false))) {
        throw new Error("Highway contract pack contains an invalid record.");
      }
      if (seen.has(row.record_id)) throw new Error("Highway contract pack contains a duplicate.");
      seen.add(row.record_id);
    }
    return pack;
  }

  async function validateDecodedContractPack(resource, bytes) {
    if (!(bytes instanceof ArrayBuffer) || bytes.byteLength !== resource.bytes) {
      throw new Error("Contract-pack byte length does not match its manifest.");
    }
    const digest = await sha256Bytes(bytes);
    if (!digest || digest !== resource.sha256 || !window.TextDecoder) {
      throw new Error("Contract-pack checksum does not match its manifest.");
    }
    const pack = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
    return validateHighwayContractPack(resource, pack);
  }

  async function fetchContractPack(resource) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), OPTIONAL_CATALOG_TIMEOUT_MS);
    try {
      const response = await fetch(resolvePackUrl(resource), {
        cache: "no-store", credentials: "omit", referrerPolicy: "no-referrer",
        signal: controller.signal,
      });
      if (!response.ok) return null;
      const contentType = response.headers.get("content-type") || "";
      if (contentType && !/json/i.test(contentType)) return null;
      const bytes = await response.arrayBuffer();
      return { pack: await validateDecodedContractPack(resource, bytes), bytes };
    } catch (e) { return null; }
    finally { clearTimeout(timer); }
  }

  async function loadHighwayContractPack(stateCode) {
    if (!/^[A-Z]{2}$/.test(String(stateCode || ""))) return null;
    const packId = `in-nh-contracts-${stateCode.toLowerCase()}`;
    if (_contractPackPromises.has(packId)) return _contractPackPromises.get(packId);
    const task = (async () => {
      const manifest = await getContractPackManifest();
      const resource = manifest && manifest.resources[packId];
      if (!resource || !catalogResourceWithinReview(resource)) return null;
      const cacheKey = statePackCacheKey(resource);
      const memory = _contractPackMemory.get(packId);
      if (memory && memory.cache_key === cacheKey) return memory.pack;
      let cached = null;
      try { cached = await getCachedStatePack(cacheKey); } catch (e) { /* download below */ }
      if (cached) {
        try {
          const pack = await validateDecodedContractPack(resource, await cachedPackBytes(cached));
          _contractPackMemory.set(packId, { cache_key: cacheKey, pack, resource });
          touchStatePack(cached);
          pruneStatePacks(packId);
          return pack;
        } catch (e) {
          try { await deleteCachedStatePack(cacheKey); } catch (_) {}
        }
      }
      const downloaded = await fetchContractPack(resource);
      if (!downloaded) return null;
      const now = Date.now();
      const record = {
        cache_key: cacheKey, pack_id: resource.pack_id, pack_version: resource.pack_version,
        state_code: resource.state_code, kind: resource.kind, sha256: resource.sha256,
        bytes: resource.bytes, installed_at: now, last_used_at: now,
        blob: new Blob([downloaded.bytes], { type: "application/json" }),
      };
      try { await putCachedStatePack(record); }
      catch (e) {
        await pruneStatePacks(packId);
        try { await putCachedStatePack(record); } catch (_) { /* valid for this session */ }
      }
      _contractPackMemory.set(packId, { cache_key: cacheKey, pack: downloaded.pack, resource });
      pruneStatePacks(packId);
      return downloaded.pack;
    })();
    _contractPackPromises.set(packId, task);
    try { return await task; }
    finally { _contractPackPromises.delete(packId); }
  }

  function contractPackProvenance(stateCode) {
    const packId = `in-nh-contracts-${String(stateCode || "").toLowerCase()}`;
    const item = _contractPackMemory.get(packId);
    const resource = item && item.resource;
    if (!resource) return {};
    return {
      tender_pack_id: resource.pack_id,
      tender_pack_version: resource.pack_version,
      tender_pack_sha256: resource.sha256,
      tender_pack_state_code: resource.state_code,
    };
  }

  function resetContractPackMemory() {
    _contractPackManifest = null;
    _contractPackManifestPromise = null;
    _contractPackMemory.clear();
    _contractPackPromises.clear();
  }

  // Current official State/UT portal listings are procurement notices, not awards. They use a
  // separate catalog and schema so a notice can never acquire contractor, segment or
  // warranty fields merely by passing through the National Highway contract loader.
  const ROAD_NOTICE_MANIFEST_FILE = "road-notice-manifest-v1.36.json";
  const ROAD_NOTICE_PACK_MAX_BYTES = 8 * 1024 * 1024;
  let _roadNoticeManifest = null, _roadNoticeManifestPromise = null;
  const _roadNoticePackMemory = new Map(), _roadNoticePackPromises = new Map();

  function validRoadNoticePolicy(policy) {
    return exactObjectKeys(policy, ["award_verified", "candidate_only", "dlp_verified",
      "lifecycle", "scope", "segment_verified"])
      && policy.candidate_only === true && policy.lifecycle === "procurement_notice"
      && policy.scope === "road_surface" && policy.segment_verified === false
      && policy.award_verified === false && policy.dlp_verified === false;
  }

  function validateRoadNoticeManifest(manifest) {
    if (!exactObjectKeys(manifest, ["format", "schema_version", "catalog_version",
      "generated_at", "cache", "inference_policy", "resources"])
        || manifest.format !== "pothole-road-notice-manifest"
        || manifest.schema_version !== 1 || manifest.catalog_version !== 1
        || !/^\d{4}-\d{2}-\d{2}$/.test(String(manifest.generated_at || ""))
        || !exactObjectKeys(manifest.cache, ["max_bytes", "max_unused_days"])
        || !Number.isInteger(manifest.cache.max_bytes)
        || manifest.cache.max_bytes < 1024 * 1024
        || manifest.cache.max_bytes > 128 * 1024 * 1024
        || !Number.isInteger(manifest.cache.max_unused_days)
        || manifest.cache.max_unused_days < 1 || manifest.cache.max_unused_days > 90
        || !validRoadNoticePolicy(manifest.inference_policy)
        || !manifest.resources || typeof manifest.resources !== "object"
        || Array.isArray(manifest.resources)) {
      throw new Error("Invalid road-notice manifest.");
    }
    for (const [packId, resource] of Object.entries(manifest.resources)) {
      const fields = ["adapter", "bytes", "candidate_only", "kind", "licenses",
        "lifecycle", "pack_id", "pack_version", "path", "records", "review_after",
        "rows_excluded_by_scope", "rows_scanned", "schema_version", "sha256",
        "source_retrieved_at", "sources", "state_code", "url"];
      const state = String(resource && resource.state_code || "");
      const pathMatch = resource && typeof resource.path === "string"
        ? resource.path.match(
          /^packs\/v1\/road-notices\/([a-z]{2})\/notices-([0-9a-f]{64})\.json$/)
        : null;
      if (!exactObjectKeys(resource, fields)
          || packId !== `in-road-notices-${state.toLowerCase()}`
          || resource.pack_id !== packId || !/^[A-Z]{2}$/.test(state)
          || resource.kind !== "road_procurement_notices"
          || resource.adapter !== "official-road-notices-v2"
          || resource.lifecycle !== "procurement_notice"
          || resource.candidate_only !== true
          || resource.pack_version !== 1 || resource.schema_version !== 1
          || !pathMatch || pathMatch[1] !== state.toLowerCase()
          || pathMatch[2] !== resource.sha256
          || resource.url !== PACK_SITE_ROOT + resource.path
          || !Number.isInteger(resource.bytes) || resource.bytes <= 0
          || resource.bytes > ROAD_NOTICE_PACK_MAX_BYTES
          || !/^[0-9a-f]{64}$/.test(String(resource.sha256 || ""))
          || !Number.isInteger(resource.records) || resource.records < 0
          || resource.records > 20000
          || !Number.isInteger(resource.sources) || resource.sources < 1
          || !Number.isInteger(resource.rows_scanned) || resource.rows_scanned < 0
          || !Number.isInteger(resource.rows_excluded_by_scope)
          || resource.rows_excluded_by_scope < 0
          || resource.rows_excluded_by_scope + resource.records !== resource.rows_scanned
          || !/^\d{4}-\d{2}-\d{2}$/.test(String(resource.source_retrieved_at || ""))
          || !/^\d{4}-\d{2}-\d{2}$/.test(String(resource.review_after || ""))
          || !Array.isArray(resource.licenses) || !resource.licenses.length
          || resource.licenses.some((item) => typeof item !== "string" || !item)) {
        throw new Error(`Invalid road-notice resource: ${packId}`);
      }
    }
    return manifest;
  }

  async function getRoadNoticeManifest() {
    if (_roadNoticeManifest) return _roadNoticeManifest;
    if (_roadNoticeManifestPromise) return _roadNoticeManifestPromise;
    _roadNoticeManifestPromise = fetchOptionalCatalogManifest(
      ROAD_NOTICE_MANIFEST_FILE, validateRoadNoticeManifest).then((manifest) => {
      _roadNoticeManifest = manifest;
      return manifest;
    });
    const result = await _roadNoticeManifestPromise;
    _roadNoticeManifestPromise = null;
    return result;
  }

  const ROAD_NOTICE_TIMESTAMP_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})$/;

  function validateRoadNoticePack(resource, pack) {
    if (!exactObjectKeys(pack, ["adapter", "format", "generated_at", "inference_policy",
      "notices", "pack_id", "pack_version", "schema_version", "sources", "state_code"])
        || pack.format !== "pothole-official-road-notice-pack"
        || pack.schema_version !== 1 || pack.pack_version !== 1
        || pack.pack_id !== resource.pack_id || pack.state_code !== resource.state_code
        || pack.adapter !== resource.adapter || pack.generated_at !== resource.source_retrieved_at
        || !validRoadNoticePolicy(pack.inference_policy)
        || !Array.isArray(pack.sources) || pack.sources.length !== resource.sources
        || !Array.isArray(pack.notices) || pack.notices.length !== resource.records) {
      throw new Error("Road-notice pack envelope is invalid.");
    }
    const sourceFields = ["retrieved_at", "rows_excluded_by_scope", "rows_scanned",
      "source_id", "source_name", "source_url"];
    const sourceIds = new Set();
    for (const source of pack.sources) {
      if (!exactObjectKeys(source, sourceFields)
          || !/^in-[a-z]{2}-[a-z0-9][a-z0-9-]*$/.test(String(source.source_id || ""))
          || typeof source.source_name !== "string" || !source.source_name
          || source.source_name.length > 300
          || !/^https:\/\//.test(String(source.source_url || ""))
          || !ROAD_NOTICE_TIMESTAMP_RE.test(String(source.retrieved_at || ""))
          || !Number.isInteger(source.rows_scanned) || source.rows_scanned < 0
          || !Number.isInteger(source.rows_excluded_by_scope)
          || source.rows_excluded_by_scope < 0
          || source.rows_excluded_by_scope > source.rows_scanned
          || sourceIds.has(source.source_id)) {
        throw new Error("Road-notice pack source receipt is invalid.");
      }
      sourceIds.add(source.source_id);
    }
    const noticeFields = ["award_verified", "closing_at", "dlp_verified", "lifecycle",
      "opening_at", "organisation_chain", "published_at", "record_id", "scope",
      "segment_verified", "source_id", "source_url", "tender_id", "tender_reference", "title"];
    const seen = new Set();
    for (const row of pack.notices) {
      if (!exactObjectKeys(row, noticeFields)
          || typeof row.record_id !== "string" || !row.record_id
          || row.record_id.length > 280 || seen.has(row.record_id)
          || typeof row.tender_id !== "string" || !row.tender_id
          || row.tender_id.length > 160
          || typeof row.tender_reference !== "string" || !row.tender_reference
          || row.tender_reference.length > 300
          || typeof row.title !== "string" || !row.title || row.title.length > 1200
          || typeof row.organisation_chain !== "string" || !row.organisation_chain
          || row.organisation_chain.length > 800
          || !ROAD_NOTICE_TIMESTAMP_RE.test(String(row.closing_at || ""))
          || ![row.published_at, row.opening_at]
            .every((value) => value === null
              || ROAD_NOTICE_TIMESTAMP_RE.test(String(value || "")))
          || !sourceIds.has(row.source_id)
          || !/^https:\/\//.test(String(row.source_url || ""))
          || row.lifecycle !== "procurement_notice" || row.scope !== "road_surface"
          || row.segment_verified !== false || row.award_verified !== false
          || row.dlp_verified !== false
          || !tenderCoversCarriageway(row.title, row.tender_reference)) {
        throw new Error("Road-notice pack contains an invalid record.");
      }
      seen.add(row.record_id);
    }
    const scanned = pack.sources.reduce((sum, source) => sum + source.rows_scanned, 0);
    const excluded = pack.sources.reduce(
      (sum, source) => sum + source.rows_excluded_by_scope, 0);
    if (scanned !== resource.rows_scanned || excluded !== resource.rows_excluded_by_scope
        || excluded + pack.notices.length !== scanned) {
      throw new Error("Road-notice pack source accounting is invalid.");
    }
    return pack;
  }

  async function validateDecodedRoadNoticePack(resource, bytes) {
    if (!(bytes instanceof ArrayBuffer) || bytes.byteLength !== resource.bytes) {
      throw new Error("Road-notice pack byte length does not match its manifest.");
    }
    const digest = await sha256Bytes(bytes);
    if (!digest || digest !== resource.sha256 || !window.TextDecoder) {
      throw new Error("Road-notice pack checksum does not match its manifest.");
    }
    const pack = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
    return validateRoadNoticePack(resource, pack);
  }

  async function fetchRoadNoticePack(resource) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), OPTIONAL_CATALOG_TIMEOUT_MS);
    try {
      const response = await fetch(resolvePackUrl(resource), {
        cache: "no-store", credentials: "omit", referrerPolicy: "no-referrer",
        signal: controller.signal,
      });
      if (!response.ok) return null;
      const contentType = response.headers.get("content-type") || "";
      if (contentType && !/json/i.test(contentType)) return null;
      const bytes = await response.arrayBuffer();
      return { pack: await validateDecodedRoadNoticePack(resource, bytes), bytes };
    } catch (e) { return null; }
    finally { clearTimeout(timer); }
  }

  async function loadRoadNoticePack(stateCode) {
    if (!/^[A-Z]{2}$/.test(String(stateCode || ""))) return null;
    const packId = `in-road-notices-${stateCode.toLowerCase()}`;
    if (_roadNoticePackPromises.has(packId)) return _roadNoticePackPromises.get(packId);
    const task = (async () => {
      const manifest = await getRoadNoticeManifest();
      const resource = manifest && manifest.resources[packId];
      if (!resource || !catalogResourceWithinReview(resource)) return null;
      const cacheKey = statePackCacheKey(resource);
      const memory = _roadNoticePackMemory.get(packId);
      if (memory && memory.cache_key === cacheKey) return memory.pack;
      let cached = null;
      try { cached = await getCachedStatePack(cacheKey); } catch (e) { /* download below */ }
      if (cached) {
        try {
          const pack = await validateDecodedRoadNoticePack(resource, await cachedPackBytes(cached));
          _roadNoticePackMemory.set(packId, { cache_key: cacheKey, pack, resource });
          touchStatePack(cached);
          pruneStatePacks(packId);
          return pack;
        } catch (e) {
          try { await deleteCachedStatePack(cacheKey); } catch (_) {}
        }
      }
      const downloaded = await fetchRoadNoticePack(resource);
      if (!downloaded) return null;
      const now = Date.now();
      const record = {
        cache_key: cacheKey, pack_id: resource.pack_id, pack_version: resource.pack_version,
        state_code: resource.state_code, kind: resource.kind, sha256: resource.sha256,
        bytes: resource.bytes, installed_at: now, last_used_at: now,
        blob: new Blob([downloaded.bytes], { type: "application/json" }),
      };
      try { await putCachedStatePack(record); }
      catch (e) {
        await pruneStatePacks(packId);
        try { await putCachedStatePack(record); } catch (_) { /* valid for this session */ }
      }
      _roadNoticePackMemory.set(packId,
        { cache_key: cacheKey, pack: downloaded.pack, resource });
      pruneStatePacks(packId);
      return downloaded.pack;
    })();
    _roadNoticePackPromises.set(packId, task);
    try { return await task; }
    finally { _roadNoticePackPromises.delete(packId); }
  }

  function roadNoticePackProvenance(stateCode) {
    const packId = `in-road-notices-${String(stateCode || "").toLowerCase()}`;
    const item = _roadNoticePackMemory.get(packId);
    const resource = item && item.resource;
    if (!resource) return {};
    return {
      tender_pack_id: resource.pack_id,
      tender_pack_version: resource.pack_version,
      tender_pack_sha256: resource.sha256,
      tender_pack_state_code: resource.state_code,
    };
  }

  function resetRoadNoticePackMemory() {
    _roadNoticeManifest = null;
    _roadNoticeManifestPromise = null;
    _roadNoticePackMemory.clear();
    _roadNoticePackPromises.clear();
  }

  // PMGSY/OMMAS publishes road and agreement identifiers for source-reported
  // "In Progress" projects. This is stronger location evidence than a procurement
  // notice, but it still supplies no road geometry, named contractor, completion /
  // maintenance dates or DLP. A separate schema keeps those absences enforceable.
  const ROAD_AGREEMENT_MANIFEST_FILE = "road-agreement-manifest-v1.36.json";
  const ROAD_AGREEMENT_PACK_MAX_BYTES = 8 * 1024 * 1024;
  let _roadAgreementManifest = null, _roadAgreementManifestPromise = null;
  const _roadAgreementPackMemory = new Map(), _roadAgreementPackPromises = new Map();

  function validRoadAgreementPolicy(policy) {
    return exactObjectKeys(policy, ["agreement_verified", "award_verified", "candidate_only",
      "contractor_assignment_verified", "dlp_verified", "freshness_window_years", "lifecycle",
      "scope_verified", "segment_verified", "source_status"])
      && policy.candidate_only === true && policy.lifecycle === "current_project"
      && policy.source_status === "In Progress" && policy.segment_verified === false
      && policy.freshness_window_years === 5 && policy.scope_verified === true
      && policy.agreement_verified === true && policy.award_verified === false
      && policy.contractor_assignment_verified === false && policy.dlp_verified === false;
  }

  function validateRoadAgreementManifest(manifest) {
    if (!exactObjectKeys(manifest, ["format", "schema_version", "catalog_version",
      "generated_at", "cache", "inference_policy", "resources"])
        || manifest.format !== "pothole-road-agreement-manifest"
        || manifest.schema_version !== 1 || manifest.catalog_version !== 1
        || !/^\d{4}-\d{2}-\d{2}$/.test(String(manifest.generated_at || ""))
        || !exactObjectKeys(manifest.cache, ["max_bytes", "max_unused_days"])
        || !Number.isInteger(manifest.cache.max_bytes)
        || manifest.cache.max_bytes < 1024 * 1024
        || manifest.cache.max_bytes > 256 * 1024 * 1024
        || !Number.isInteger(manifest.cache.max_unused_days)
        || manifest.cache.max_unused_days < 1 || manifest.cache.max_unused_days > 90
        || !validRoadAgreementPolicy(manifest.inference_policy)
        || !manifest.resources || typeof manifest.resources !== "object"
        || Array.isArray(manifest.resources)) {
      throw new Error("Invalid road-agreement manifest.");
    }
    for (const [packId, resource] of Object.entries(manifest.resources)) {
      const fields = ["adapter", "bytes", "candidate_only", "kind", "licenses",
        "lifecycle", "pack_id", "pack_version", "path", "records", "review_after",
        "rows_excluded_by_freshness", "rows_excluded_by_status", "rows_excluded_invalid", "rows_scanned",
        "schema_version", "sha256", "source_retrieved_at", "sources", "state_code", "url"];
      const state = String(resource && resource.state_code || "");
      const pathMatch = resource && typeof resource.path === "string"
        ? resource.path.match(
          /^packs\/v1\/road-agreements\/([a-z]{2})\/agreements-([0-9a-f]{64})\.json$/)
        : null;
      if (!exactObjectKeys(resource, fields)
          || packId !== `in-road-agreements-${state.toLowerCase()}`
          || resource.pack_id !== packId || !/^[A-Z]{2}$/.test(state)
          || resource.kind !== "road_current_agreements"
          || resource.adapter !== "pmgsy-ommas-in-progress-v1"
          || resource.lifecycle !== "current_project" || resource.candidate_only !== true
          || resource.pack_version !== 1 || resource.schema_version !== 1
          || !pathMatch || pathMatch[1] !== state.toLowerCase()
          || pathMatch[2] !== resource.sha256
          || resource.url !== PACK_SITE_ROOT + resource.path
          || !Number.isInteger(resource.bytes) || resource.bytes <= 0
          || resource.bytes > ROAD_AGREEMENT_PACK_MAX_BYTES
          || !/^[0-9a-f]{64}$/.test(String(resource.sha256 || ""))
          || !Number.isInteger(resource.records) || resource.records < 0
          || resource.records > 50000
          || !Number.isInteger(resource.sources) || resource.sources < 1
          || !Number.isInteger(resource.rows_scanned) || resource.rows_scanned < 0
          || !Number.isInteger(resource.rows_excluded_by_status)
          || resource.rows_excluded_by_status < 0
          || !Number.isInteger(resource.rows_excluded_by_freshness)
          || resource.rows_excluded_by_freshness < 0
          || !Number.isInteger(resource.rows_excluded_invalid)
          || resource.rows_excluded_invalid < 0
          || resource.rows_excluded_by_status + resource.rows_excluded_by_freshness
            + resource.rows_excluded_invalid
            + resource.records !== resource.rows_scanned
          || !/^\d{4}-\d{2}-\d{2}$/.test(String(resource.source_retrieved_at || ""))
          || !/^\d{4}-\d{2}-\d{2}$/.test(String(resource.review_after || ""))
          || !Array.isArray(resource.licenses) || !resource.licenses.length
          || resource.licenses.some((item) => typeof item !== "string" || !item)) {
        throw new Error(`Invalid road-agreement resource: ${packId}`);
      }
    }
    return manifest;
  }

  async function getRoadAgreementManifest() {
    if (_roadAgreementManifest) return _roadAgreementManifest;
    if (_roadAgreementManifestPromise) return _roadAgreementManifestPromise;
    _roadAgreementManifestPromise = fetchOptionalCatalogManifest(
      ROAD_AGREEMENT_MANIFEST_FILE, validateRoadAgreementManifest).then((manifest) => {
      _roadAgreementManifest = manifest;
      return manifest;
    });
    const result = await _roadAgreementManifestPromise;
    _roadAgreementManifestPromise = null;
    return result;
  }

  const nullableRoadAgreementText = (value, max = 400) => value === null
    || (typeof value === "string" && value.length > 0 && value.length <= max);

  function validateRoadAgreementPack(resource, pack) {
    if (!exactObjectKeys(pack, ["adapter", "agreement_fields", "agreements", "format",
      "generated_at", "inference_policy", "pack_id", "pack_version", "schema_version",
      "sources", "state_code"])
        || pack.format !== "pothole-pmgsy-road-agreement-pack"
        || pack.schema_version !== 1 || pack.pack_version !== 1
        || pack.pack_id !== resource.pack_id || pack.state_code !== resource.state_code
        || pack.adapter !== resource.adapter || pack.generated_at !== resource.source_retrieved_at
        || !validRoadAgreementPolicy(pack.inference_policy)
        || JSON.stringify(pack.agreement_fields) !== JSON.stringify(["record_id", "reference_value",
          "title", "road_id", "district_name", "road_from", "road_to", "agreement_number",
          "agreement_date"])
        || !Array.isArray(pack.sources) || pack.sources.length !== resource.sources
        || !Array.isArray(pack.agreements) || pack.agreements.length !== resource.records) {
      throw new Error("Road-agreement pack envelope is invalid.");
    }
    const sourceFields = ["endpoint", "freshness_window_years", "records_kept", "retrieved_at",
      "rows_excluded_by_freshness", "rows_excluded_by_status", "rows_excluded_invalid",
      "rows_scanned", "source_id", "source_name", "source_state_id", "source_state_name",
      "source_url"];
    const sourceIds = new Set();
    for (const source of pack.sources) {
      if (!exactObjectKeys(source, sourceFields)
          || typeof source.source_id !== "string" || !source.source_id
          || source.source_id.length > 160 || sourceIds.has(source.source_id)
          || typeof source.source_name !== "string" || !source.source_name
          || source.source_name.length > 300
          || !/^https:\/\//.test(String(source.source_url || ""))
          || !/^https:\/\//.test(String(source.endpoint || ""))
          || !Number.isInteger(source.source_state_id) || source.source_state_id < 1
          || typeof source.source_state_name !== "string" || !source.source_state_name
          || source.source_state_name.length > 160
          || !ROAD_NOTICE_TIMESTAMP_RE.test(String(source.retrieved_at || ""))
          || !Number.isInteger(source.rows_scanned) || source.rows_scanned < 0
          || !Number.isInteger(source.rows_excluded_by_status)
          || source.rows_excluded_by_status < 0
          || source.freshness_window_years !== 5
          || !Number.isInteger(source.rows_excluded_by_freshness)
          || source.rows_excluded_by_freshness < 0
          || !Number.isInteger(source.rows_excluded_invalid)
          || source.rows_excluded_invalid < 0
          || !Number.isInteger(source.records_kept) || source.records_kept < 0
          || source.rows_excluded_by_status + source.rows_excluded_by_freshness
            + source.rows_excluded_invalid
            + source.records_kept !== source.rows_scanned) {
        throw new Error("Road-agreement pack source receipt is invalid.");
      }
      sourceIds.add(source.source_id);
    }
    const agreementFields = pack.agreement_fields;
    const seen = new Set();
    const decoded = [];
    const firstSource = pack.sources[0];
    const retrievedAt = resource.source_retrieved_at;
    const latestAgreementDate = Date.parse(`${retrievedAt}T23:59:59.999Z`);
    const earliestDateObject = new Date(`${retrievedAt}T00:00:00Z`);
    earliestDateObject.setUTCFullYear(earliestDateObject.getUTCFullYear()
      - pack.inference_policy.freshness_window_years);
    const earliestAgreementDate = earliestDateObject.getTime();
    for (const values of pack.agreements) {
      if (!Array.isArray(values) || values.length !== agreementFields.length) {
        throw new Error("Road-agreement pack contains an invalid record.");
      }
      const row = Object.fromEntries(agreementFields.map((field, index) => [field, values[index]]));
      const agreementTime = Date.parse(`${row.agreement_date}T00:00:00Z`);
      if (typeof row.record_id !== "string" || !row.record_id
          || row.record_id.length > 240 || seen.has(row.record_id)
          || typeof row.reference_value !== "string" || !row.reference_value
          || row.reference_value.length > 200
          || typeof row.title !== "string" || !row.title || row.title.length > 1200
          || !(Number.isInteger(row.road_id) || (typeof row.road_id === "string"
            && row.road_id.length > 0 && row.road_id.length <= 160))
          || !nullableRoadAgreementText(row.district_name, 240)
          || !nullableRoadAgreementText(row.road_from, 400)
          || !nullableRoadAgreementText(row.road_to, 400)
          || typeof row.agreement_number !== "string" || !row.agreement_number
          || row.agreement_number.length > 300
          || !/^\d{4}-\d{2}-\d{2}$/.test(String(row.agreement_date || ""))
          || !Number.isFinite(agreementTime) || agreementTime < earliestAgreementDate
          || agreementTime > latestAgreementDate) {
        throw new Error("Road-agreement pack contains an invalid record.");
      }
      seen.add(row.record_id);
      decoded.push({
        ...row,
        reference_label: "PMGSY package",
        state_code: resource.state_code,
        agency: "NRIDA / OMMAS",
        lifecycle: "current_project",
        lifecycle_status: "In Progress",
        lifecycle_basis: "source-reported WORK_STATUS; agreement date within five-year snapshot window",
        package_number: row.reference_value,
        contractor: null,
        scope_verified: true,
        segment_verified: false,
        agreement_verified: true,
        award_verified: false,
        contractor_assignment_verified: false,
        dlp_verified: false,
        source_name: firstSource.source_name,
        // The JSON endpoint is POST-only and not a useful citation when opened by an
        // officer. Link the official dashboard root; package/agreement IDs remain in
        // the complaint and the endpoint stays pinned inside the source receipt.
        source_url: firstSource.source_url,
        retrieved_at: retrievedAt,
      });
    }
    const scanned = pack.sources.reduce((sum, source) => sum + source.rows_scanned, 0);
    const excludedStatus = pack.sources.reduce(
      (sum, source) => sum + source.rows_excluded_by_status, 0);
    const excludedFreshness = pack.sources.reduce(
      (sum, source) => sum + source.rows_excluded_by_freshness, 0);
    const excludedInvalid = pack.sources.reduce(
      (sum, source) => sum + source.rows_excluded_invalid, 0);
    const kept = pack.sources.reduce((sum, source) => sum + source.records_kept, 0);
    if (scanned !== resource.rows_scanned
        || excludedStatus !== resource.rows_excluded_by_status
        || excludedFreshness !== resource.rows_excluded_by_freshness
        || excludedInvalid !== resource.rows_excluded_invalid
        || kept !== resource.records || kept !== pack.agreements.length) {
      throw new Error("Road-agreement pack source accounting is invalid.");
    }
    // Keep the downloaded representation compact; only decoded, validated objects enter
    // the in-memory matcher and IndexedDB continues storing the original immutable bytes.
    pack.agreements = decoded;
    return pack;
  }

  async function validateDecodedRoadAgreementPack(resource, bytes) {
    if (!(bytes instanceof ArrayBuffer) || bytes.byteLength !== resource.bytes) {
      throw new Error("Road-agreement pack byte length does not match its manifest.");
    }
    const digest = await sha256Bytes(bytes);
    if (!digest || digest !== resource.sha256 || !window.TextDecoder) {
      throw new Error("Road-agreement pack checksum does not match its manifest.");
    }
    const pack = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
    return validateRoadAgreementPack(resource, pack);
  }

  async function fetchRoadAgreementPack(resource) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), OPTIONAL_CATALOG_TIMEOUT_MS);
    try {
      const response = await fetch(resolvePackUrl(resource), {
        cache: "no-store", credentials: "omit", referrerPolicy: "no-referrer",
        signal: controller.signal,
      });
      if (!response.ok) return null;
      const contentType = response.headers.get("content-type") || "";
      if (contentType && !/json/i.test(contentType)) return null;
      const bytes = await response.arrayBuffer();
      return { pack: await validateDecodedRoadAgreementPack(resource, bytes), bytes };
    } catch (e) { return null; }
    finally { clearTimeout(timer); }
  }

  async function loadRoadAgreementPack(stateCode) {
    if (!/^[A-Z]{2}$/.test(String(stateCode || ""))) return null;
    const packId = `in-road-agreements-${stateCode.toLowerCase()}`;
    if (_roadAgreementPackPromises.has(packId)) return _roadAgreementPackPromises.get(packId);
    const task = (async () => {
      const manifest = await getRoadAgreementManifest();
      const resource = manifest && manifest.resources[packId];
      if (!resource || !catalogResourceWithinReview(resource)) return null;
      const cacheKey = statePackCacheKey(resource);
      const memory = _roadAgreementPackMemory.get(packId);
      if (memory && memory.cache_key === cacheKey) return memory.pack;
      let cached = null;
      try { cached = await getCachedStatePack(cacheKey); } catch (e) { /* download below */ }
      if (cached) {
        try {
          const pack = await validateDecodedRoadAgreementPack(
            resource, await cachedPackBytes(cached));
          _roadAgreementPackMemory.set(packId, { cache_key: cacheKey, pack, resource });
          touchStatePack(cached);
          pruneStatePacks(packId);
          return pack;
        } catch (e) {
          try { await deleteCachedStatePack(cacheKey); } catch (_) {}
        }
      }
      const downloaded = await fetchRoadAgreementPack(resource);
      if (!downloaded) return null;
      const now = Date.now();
      const record = {
        cache_key: cacheKey, pack_id: resource.pack_id, pack_version: resource.pack_version,
        state_code: resource.state_code, kind: resource.kind, sha256: resource.sha256,
        bytes: resource.bytes, installed_at: now, last_used_at: now,
        blob: new Blob([downloaded.bytes], { type: "application/json" }),
      };
      try { await putCachedStatePack(record); }
      catch (e) {
        await pruneStatePacks(packId);
        try { await putCachedStatePack(record); } catch (_) { /* valid for this session */ }
      }
      _roadAgreementPackMemory.set(packId,
        { cache_key: cacheKey, pack: downloaded.pack, resource });
      pruneStatePacks(packId);
      return downloaded.pack;
    })();
    _roadAgreementPackPromises.set(packId, task);
    try { return await task; }
    finally { _roadAgreementPackPromises.delete(packId); }
  }

  function roadAgreementPackProvenance(stateCode) {
    const packId = `in-road-agreements-${String(stateCode || "").toLowerCase()}`;
    const item = _roadAgreementPackMemory.get(packId);
    const resource = item && item.resource;
    if (!resource) return {};
    return {
      tender_pack_id: resource.pack_id,
      tender_pack_version: resource.pack_version,
      tender_pack_sha256: resource.sha256,
      tender_pack_state_code: resource.state_code,
    };
  }

  function resetRoadAgreementPackMemory() {
    _roadAgreementManifest = null;
    _roadAgreementManifestPromise = null;
    _roadAgreementPackMemory.clear();
    _roadAgreementPackPromises.clear();
  }

  function resetStatePackMemory() {
    _statePackManifest = null;
    _statePackManifestPromise = null;
    _statePackMemory.clear();
    _statePackPromises.clear();
    resetContractPackMemory();
    municipalCityCoverageCache.clear();
    municipalCityCoveragePromises.clear();
    _punjabCoverage = null;
    _punjabCoveragePromise = null;
    _tamilNaduCoverage = null;
    _tamilNaduCoveragePromise = null;
    _andhraPradeshCoverage = null;
    _andhraPradeshCoveragePromise = null;
    _telanganaCoverage = null;
    _telanganaCoveragePromise = null;
    _newStateCoverage.clear();
    _newStateCoveragePromises.clear();
    _majorCityCoverage = null;
    _majorCityCoveragePromise = null;
    _tenders = null;
    _byBody = null;
    resetHighwayPackMemory();
    resetRoadNoticePackMemory();
    resetRoadAgreementPackMemory();
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
  const KARNATAKA_ROUTING_ENVELOPE = {
    minLat: 11.40, maxLat: 18.60, minLng: 73.90, maxLng: 78.80,
  };
  const inKarnatakaRoutingEnvelope = (lat, lng) => Number.isFinite(lat) && Number.isFinite(lng)
    && lat >= KARNATAKA_ROUTING_ENVELOPE.minLat
    && lat <= KARNATAKA_ROUTING_ENVELOPE.maxLat
    && lng >= KARNATAKA_ROUTING_ENVELOPE.minLng
    && lng <= KARNATAKA_ROUTING_ENVELOPE.maxLng;

  // The relevance envelope is intentionally wider than Delhi NCT. Nearby Noida,
  // Gurugram, Ghaziabad and Faridabad may enter this download prefilter, but only the
  // pinned polygon can select Delhi; an outside point continues to exact state routes.
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

  const PUNJAB_ROUTING_ENVELOPE = {
    min_lng: 73.8798336, min_lat: 29.5429378, max_lng: 76.9390583, max_lat: 32.5111793,
  };
  const PUNJAB_STATE_GEOMETRY_SHA256 =
    "e113eb774f4f353d3c7a9c98830f4b665f9bd4d166ed3b84e90855bdf38f5782";
  const TAMIL_NADU_ROUTING_ENVELOPE = {
    min_lng: 76.2329467, min_lat: 8.0768938, max_lng: 80.3592971, max_lat: 13.5639111,
  };
  const TAMIL_NADU_STATE_GEOMETRY_SHA256 =
    "b3034527326b1120366adaf4b7c3df4bd0b8c7aab4d82b28e3dde189b39c313e";
  const ANDHRA_PRADESH_ROUTING_ENVELOPE = {
    min_lng: 76.7600837, min_lat: 12.6238599, max_lng: 84.7658033, max_lat: 19.166806,
  };
  const ANDHRA_PRADESH_STATE_GEOMETRY_SHA256 =
    "4e36d9c16fda044dceab7a5b08955cb19046bb1bddd052b7671a8311e90cd71c";
  const TELANGANA_ROUTING_ENVELOPE = {
    min_lng: 77.236585, min_lat: 15.8364246, max_lng: 81.3226246, max_lat: 19.9172962,
  };
  const TELANGANA_STATE_GEOMETRY_SHA256 =
    "77183815e4b698ec1e823f4a94a6f213d1d827ea35de8fec8c0ab3b6a9d15175";
  const KARNATAKA_STATE_ROUTING_ENVELOPE = {
    min_lng: 74.0543908, min_lat: 11.5945587, max_lng: 78.5875761, max_lat: 18.4766494,
  };
  const KARNATAKA_STATE_GEOMETRY_SHA256 =
    "9d7fe3f01a80cb41712c09139efcd43e0e11a644849d5f3bffe125cc0bc1c5ad";
  const KERALA_ROUTING_ENVELOPE = {
    min_lng: 74.8640682, min_lat: 8.2935318, max_lng: 77.4123612, max_lat: 12.7960559,
  };
  const KERALA_STATE_GEOMETRY_SHA256 =
    "51e226750b1d6c08a5030e6074e2641282e01c328f46d8aee741de664bef705c";
  const UTTAR_PRADESH_ROUTING_ENVELOPE = {
    min_lng: 77.0838761, min_lat: 23.8706272, max_lng: 84.6345091, max_lat: 30.4063828,
  };
  const UTTAR_PRADESH_STATE_GEOMETRY_SHA256 =
    "2dbb5237cab5eb029f517c1d79451663c1fc49affe0e0789b11f0565180db015";
  const CHHATTISGARH_ROUTING_ENVELOPE = {
    min_lng: 80.2441803, min_lat: 17.7822157, max_lng: 84.3959641, max_lat: 24.1066864,
  };
  const CHHATTISGARH_STATE_GEOMETRY_SHA256 =
    "827e89a598571ade84db77390bca5daf98c9f67fbae716b17193f4ccdc2876eb";
  const RAJASTHAN_ROUTING_ENVELOPE = {
    min_lng: 69.4844368, min_lat: 23.0586612, max_lng: 78.2720089, max_lat: 30.198253,
  };
  const RAJASTHAN_STATE_GEOMETRY_SHA256 =
    "dcde670675d0fc50e292c6b306b1f80d9d68a1323250c29d6eddc97992491a36";
  const GOA_ROUTING_ENVELOPE = {
    min_lng: 73.6756012, min_lat: 14.7529315, max_lng: 74.3361139, max_lat: 15.8007631,
  };
  const GOA_STATE_GEOMETRY_SHA256 =
    "f4c47a79a3671d333d47f66a597d66b6295a78b1cd7cd3cba7bc2db472190e4f";
  const MADHYA_PRADESH_ROUTING_ENVELOPE = {
    min_lng: 74.029382, min_lat: 21.0706885, max_lng: 82.8126116, max_lat: 26.8695616,
  };
  const MADHYA_PRADESH_STATE_GEOMETRY_SHA256 =
    "24f0c93ed8bd40c4c6b4e1f650c3b9870b1e65ccd5d7b00ea0193a8a5aedc357";
  const BIHAR_ROUTING_ENVELOPE = {
    min_lng: 83.3212566, min_lat: 24.2857164, max_lng: 88.2937958, max_lat: 27.521635,
  };
  const BIHAR_STATE_GEOMETRY_SHA256 =
    "3d846e20cfee28a656d6dd808c4dad37a4f1c95852f9f292b0acefde708f4b24";
  const ODISHA_ROUTING_ENVELOPE = {
    min_lng: 81.3885855, min_lat: 17.8122733, max_lng: 87.4861351, max_lat: 22.5675932,
  };
  const ODISHA_STATE_GEOMETRY_SHA256 =
    "af0fe4941b6cdd2abe5dc5717db8875bec6b68a2d6671002d2afc9c7d37d5179";

  // These coarse centres only decide whether the small, checksum-pinned national pack
  // should be loaded. The pack's own envelope plus an exact structured city/state match
  // decides the route. A 0.30-degree radius deliberately permits harmless overlaps;
  // neither proximity nor a nearest-city guess can ever select an authority.
  const MAJOR_CITY_CANDIDATE_CENTRES = Object.freeze([
    [21.209489, 72.831706], [26.915458, 75.818982], [26.460914, 80.321759],
    [26.8381, 80.9346], [28.671153, 77.412036], [22.720362, 75.8682],
    [11.001812, 76.962842], [9.967903, 76.244438], [25.595002, 85.138016],
    [11.245056, 75.775472], [23.258486, 77.401989], [10.52701, 76.214621],
    [22.297314, 73.194257], [27.175255, 78.009816], [17.693553, 83.29213],
    [11.06136, 76.068589], [8.488227, 76.947551], [11.876384, 75.373797],
    [16.511531, 80.616047], [9.908938, 78.109301], [25.335649, 83.007629],
    [28.99633, 77.706192], [28.403148, 77.310556], [22.305326, 70.802838],
    [22.801519, 86.202958], [23.163565, 79.929642], [34.074744, 74.820444],
    [25.43813, 81.8338], [23.795281, 86.430964], [26.279825, 73.018724],
    [23.37005, 85.325039], [21.238091, 81.633699], [8.887053, 76.59067],
    [26.203725, 78.157363], [21.212068, 81.373285],
  ]);
  const inMajorCityCandidateEnvelope = (lat, lng) => Number.isFinite(lat)
    && Number.isFinite(lng) && MAJOR_CITY_CANDIDATE_CENTRES.some((centre) =>
      Math.abs(lat - centre[0]) <= 0.30 && Math.abs(lng - centre[1]) <= 0.30);

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
    const inNct = pointInGeometry(lng, lat, geometry);
    const enforceGpsAccuracy = gpsAccuracy !== undefined;
    if (enforceGpsAccuracy
        && (!Number.isFinite(gpsAccuracy) || gpsAccuracy < 0 || gpsAccuracy > 30)) {
      return inNct ? unroutedRoute("location_uncertain") : null;
    }
    if (Number.isFinite(gpsAccuracy)
        && geometryBoundaryDistanceMeters(lng, lat, geometry) <= gpsAccuracy) {
      return unroutedRoute("location_uncertain");
    }
    // The broad NCR rectangle is only a download prefilter. Ghaziabad and Faridabad
    // overlap it, so a coordinate outside the exact NCT polygon must be allowed to try
    // their own reviewed route. The helper preserves its legacy outside-area result;
    // routeOfficer treats that result as "no vote" and continues to the next adapter.
    if (!inNct) return unroutedRoute("outside_area");

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
    // This rectangle also reaches neighbouring Jharkhand and Odisha. Only the exact
    // state geometry gets a vote. routeOfficer continues after this legacy helper result.
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

  let _punjabCoverage = null, _punjabCoveragePromise = null;
  async function punjabCoverage() {
    if (_punjabCoverage) return _punjabCoverage;
    if (_punjabCoveragePromise) return _punjabCoveragePromise;
    _punjabCoveragePromise = (async () => {
      try {
        const pack = await loadStatePack("in-pb-routing");
        const payload = pack && pack.payload;
        const region = payload && payload.region;
        if (payload && payload.version === 1 && region
            && region.authority_id === PUNJAB_STATE_AUTHORITY.id
            && Number(region.osm_relation_id) === 1942686
            && region.geometry_sha256 === PUNJAB_STATE_GEOMETRY_SHA256
            && hasCoverageGeometry(region.geometry)) {
          _punjabCoverage = payload;
        }
      } catch (e) { /* fail closed and allow a later retry */ }
      return _punjabCoverage;
    })();
    const result = await _punjabCoveragePromise;
    _punjabCoveragePromise = null;
    return result;
  }

  async function punjabRouteFromGeocode(_geo, lat, lng, gpsAccuracy) {
    if (!pointInEnvelope(lat, lng, PUNJAB_ROUTING_ENVELOPE)) return null;
    const coverage = await punjabCoverage();
    if (!coverage) return unroutedRoute("jurisdiction_unavailable");
    const geometry = coverage.region.geometry;
    const inState = pointInGeometry(lng, lat, geometry);
    const enforceGpsAccuracy = gpsAccuracy !== undefined;
    if (enforceGpsAccuracy
        && (!Number.isFinite(gpsAccuracy) || gpsAccuracy < 0 || gpsAccuracy > 30)) {
      return inState ? unroutedRoute("location_uncertain") : null;
    }
    if (Number.isFinite(gpsAccuracy)
        && geometryBoundaryDistanceMeters(lng, lat, geometry) <= gpsAccuracy) {
      return unroutedRoute("location_uncertain");
    }
    // Chandigarh and neighbouring states sit inside the coarse prefilter. The pinned
    // Punjab polygon is authoritative, so these coordinates continue to later routes.
    if (!inState) return null;
    return authorityRoute(PUNJAB_STATE_AUTHORITY, {
      routing_source: "osm_punjab_state_boundary",
      match_field: "boundary",
      match_value: "Punjab (OpenStreetMap relation 1942686)",
      region: "punjab-state",
      pack_id: "in-pb-routing",
    });
  }

  let _tamilNaduCoverage = null, _tamilNaduCoveragePromise = null;
  async function tamilNaduCoverage() {
    if (_tamilNaduCoverage) return _tamilNaduCoverage;
    if (_tamilNaduCoveragePromise) return _tamilNaduCoveragePromise;
    _tamilNaduCoveragePromise = (async () => {
      try {
        const pack = await loadStatePack("in-tn-state-routing");
        const payload = pack && pack.payload;
        const region = payload && payload.region;
        if (payload && payload.version === 1 && region
            && region.authority_id === TAMIL_NADU_STATE_AUTHORITY.id
            && Number(region.osm_relation_id) === 96905
            && region.geometry_sha256 === TAMIL_NADU_STATE_GEOMETRY_SHA256
            && hasCoverageGeometry(region.geometry)) {
          _tamilNaduCoverage = payload;
        }
      } catch (e) { /* fail closed and allow a later retry */ }
      return _tamilNaduCoverage;
    })();
    const result = await _tamilNaduCoveragePromise;
    _tamilNaduCoveragePromise = null;
    return result;
  }

  async function tamilNaduRouteFromGeocode(_geo, lat, lng, gpsAccuracy) {
    if (!pointInEnvelope(lat, lng, TAMIL_NADU_ROUTING_ENVELOPE)) return null;
    const coverage = await tamilNaduCoverage();
    if (!coverage) return unroutedRoute("jurisdiction_unavailable");
    const geometry = coverage.region.geometry;
    const inState = pointInGeometry(lng, lat, geometry);
    const enforceGpsAccuracy = gpsAccuracy !== undefined;
    if (enforceGpsAccuracy
        && (!Number.isFinite(gpsAccuracy) || gpsAccuracy < 0 || gpsAccuracy > 30)) {
      return inState ? unroutedRoute("location_uncertain") : null;
    }
    if (Number.isFinite(gpsAccuracy)
        && geometryBoundaryDistanceMeters(lng, lat, geometry) <= gpsAccuracy) {
      return unroutedRoute("location_uncertain");
    }
    // Puducherry's enclaves and neighbouring states sit inside the coarse download
    // rectangle. Only the checksum-pinned Tamil Nadu polygon may select this handoff.
    if (!inState) return null;
    return authorityRoute(TAMIL_NADU_STATE_AUTHORITY, {
      routing_source: "osm_tamil_nadu_state_boundary",
      match_field: "boundary",
      match_value: "Tamil Nadu (OpenStreetMap relation 96905)",
      region: "tamil-nadu-state",
      pack_id: "in-tn-state-routing",
    });
  }

  let _andhraPradeshCoverage = null, _andhraPradeshCoveragePromise = null;
  async function andhraPradeshCoverage() {
    if (_andhraPradeshCoverage) return _andhraPradeshCoverage;
    if (_andhraPradeshCoveragePromise) return _andhraPradeshCoveragePromise;
    _andhraPradeshCoveragePromise = (async () => {
      try {
        const pack = await loadStatePack("in-ap-routing");
        const payload = pack && pack.payload;
        const region = payload && payload.region;
        if (payload && payload.version === 1 && region
            && region.authority_id === ANDHRA_PRADESH_STATE_AUTHORITY.id
            && Number(region.osm_relation_id) === 2022095
            && region.geometry_sha256 === ANDHRA_PRADESH_STATE_GEOMETRY_SHA256
            && hasCoverageGeometry(region.geometry)) {
          _andhraPradeshCoverage = payload;
        }
      } catch (e) { /* fail closed and allow a later retry */ }
      return _andhraPradeshCoverage;
    })();
    const result = await _andhraPradeshCoveragePromise;
    _andhraPradeshCoveragePromise = null;
    return result;
  }

  async function andhraPradeshRouteFromGeocode(_geo, lat, lng, gpsAccuracy) {
    if (!pointInEnvelope(lat, lng, ANDHRA_PRADESH_ROUTING_ENVELOPE)) return null;
    const coverage = await andhraPradeshCoverage();
    if (!coverage) return unroutedRoute("jurisdiction_unavailable");
    const geometry = coverage.region.geometry;
    const inState = pointInGeometry(lng, lat, geometry);
    const enforceGpsAccuracy = gpsAccuracy !== undefined;
    if (enforceGpsAccuracy
        && (!Number.isFinite(gpsAccuracy) || gpsAccuracy < 0 || gpsAccuracy > 30)) {
      return inState ? unroutedRoute("location_uncertain") : null;
    }
    if (Number.isFinite(gpsAccuracy)
        && geometryBoundaryDistanceMeters(lng, lat, geometry) <= gpsAccuracy) {
      return unroutedRoute("location_uncertain");
    }
    // Yanam and neighbouring states overlap the coarse download rectangle. Only the
    // checksum-pinned Andhra Pradesh polygon may select the statewide handoff.
    if (!inState) return null;
    return authorityRoute(ANDHRA_PRADESH_STATE_AUTHORITY, {
      routing_source: "osm_andhra_pradesh_state_boundary",
      match_field: "boundary",
      match_value: "Andhra Pradesh (OpenStreetMap relation 2022095)",
      region: "andhra-pradesh-state",
      pack_id: "in-ap-routing",
    });
  }

  let _telanganaCoverage = null, _telanganaCoveragePromise = null;
  async function telanganaCoverage() {
    if (_telanganaCoverage) return _telanganaCoverage;
    if (_telanganaCoveragePromise) return _telanganaCoveragePromise;
    _telanganaCoveragePromise = (async () => {
      try {
        const pack = await loadStatePack("in-tg-state-routing");
        const payload = pack && pack.payload;
        const region = payload && payload.region;
        if (payload && payload.version === 1 && region
            && region.authority_id === TELANGANA_STATE_AUTHORITY.id
            && Number(region.osm_relation_id) === 3250963
            && region.geometry_sha256 === TELANGANA_STATE_GEOMETRY_SHA256
            && hasCoverageGeometry(region.geometry)) {
          _telanganaCoverage = payload;
        }
      } catch (e) { /* fail closed and allow a later retry */ }
      return _telanganaCoverage;
    })();
    const result = await _telanganaCoveragePromise;
    _telanganaCoveragePromise = null;
    return result;
  }

  async function telanganaRouteFromGeocode(_geo, lat, lng, gpsAccuracy) {
    if (!pointInEnvelope(lat, lng, TELANGANA_ROUTING_ENVELOPE)) return null;
    const coverage = await telanganaCoverage();
    if (!coverage) return unroutedRoute("jurisdiction_unavailable");
    const geometry = coverage.region.geometry;
    const inState = pointInGeometry(lng, lat, geometry);
    const enforceGpsAccuracy = gpsAccuracy !== undefined;
    if (enforceGpsAccuracy
        && (!Number.isFinite(gpsAccuracy) || gpsAccuracy < 0 || gpsAccuracy > 30)) {
      return inState ? unroutedRoute("location_uncertain") : null;
    }
    if (Number.isFinite(gpsAccuracy)
        && geometryBoundaryDistanceMeters(lng, lat, geometry) <= gpsAccuracy) {
      return unroutedRoute("location_uncertain");
    }
    // The coarse rectangle overlaps Andhra Pradesh, Karnataka, Maharashtra and
    // Chhattisgarh. Only the checksum-pinned Telangana polygon can select Prajavani.
    if (!inState) return null;
    return authorityRoute(TELANGANA_STATE_AUTHORITY, {
      routing_source: "osm_telangana_state_boundary",
      match_field: "boundary",
      match_value: "Telangana (OpenStreetMap relation 3250963)",
      region: "telangana-state",
      pack_id: "in-tg-state-routing",
    });
  }

  const _newStateCoverage = new Map();
  const _newStateCoveragePromises = new Map();
  async function pinnedStateCoverage(packId, authority, relationId, geometrySha256) {
    if (_newStateCoverage.has(packId)) return _newStateCoverage.get(packId);
    if (_newStateCoveragePromises.has(packId)) return _newStateCoveragePromises.get(packId);
    const request = (async () => {
      try {
        const pack = await loadStatePack(packId);
        const payload = pack && pack.payload;
        const region = payload && payload.region;
        const authorityId = typeof authority === "string" ? authority : authority.id;
        if (payload && payload.version === 1 && region
            && region.authority_id === authorityId
            && Number(region.osm_relation_id) === relationId
            && region.geometry_sha256 === geometrySha256
            && hasCoverageGeometry(region.geometry)) {
          _newStateCoverage.set(packId, payload);
          return payload;
        }
      } catch (e) { /* fail closed and allow a later retry */ }
      return null;
    })();
    _newStateCoveragePromises.set(packId, request);
    try { return await request; }
    finally { _newStateCoveragePromises.delete(packId); }
  }

  const karnatakaStateCoverage = () => pinnedStateCoverage(
    "in-ka-state-routing", KARNATAKA_STATE_AUTHORITY, 2019939,
    KARNATAKA_STATE_GEOMETRY_SHA256);
  const keralaCoverage = () => pinnedStateCoverage(
    "in-kl-routing", KERALA_STATE_AUTHORITY, 2018151, KERALA_STATE_GEOMETRY_SHA256);
  const uttarPradeshCoverage = () => pinnedStateCoverage(
    "in-up-routing", UTTAR_PRADESH_STATE_AUTHORITY, 1942587,
    UTTAR_PRADESH_STATE_GEOMETRY_SHA256);
  const chhattisgarhCoverage = () => pinnedStateCoverage(
    "in-cg-routing", CHHATTISGARH_STATE_AUTHORITY, 1972004,
    CHHATTISGARH_STATE_GEOMETRY_SHA256);
  const rajasthanCoverage = () => pinnedStateCoverage(
    "in-rj-routing", RAJASTHAN_STATE_AUTHORITY, 1942920,
    RAJASTHAN_STATE_GEOMETRY_SHA256);
  const goaCoverage = () => pinnedStateCoverage(
    "in-ga-routing", GOA_STATE_AUTHORITY, 11251493,
    GOA_STATE_GEOMETRY_SHA256);
  const madhyaPradeshCoverage = () => pinnedStateCoverage(
    "in-mp-routing", MADHYA_PRADESH_STATE_AUTHORITY, 1950071,
    MADHYA_PRADESH_STATE_GEOMETRY_SHA256);
  const biharCoverage = () => pinnedStateCoverage(
    "in-br-routing", BIHAR_STATE_AUTHORITY, 1958982,
    BIHAR_STATE_GEOMETRY_SHA256);
  const odishaCoverage = () => pinnedStateCoverage(
    "in-od-routing", ODISHA_STATE_AUTHORITY, 1984022,
    ODISHA_STATE_GEOMETRY_SHA256);

  async function pinnedStateRoute(lat, lng, gpsAccuracy, config) {
    if (!pointInEnvelope(lat, lng, config.envelope)) return null;
    const coverage = await config.coverage();
    if (!coverage) return unroutedRoute("jurisdiction_unavailable");
    const geometry = coverage.region.geometry;
    const inState = pointInGeometry(lng, lat, geometry);
    const enforceGpsAccuracy = gpsAccuracy !== undefined;
    if (enforceGpsAccuracy
        && (!Number.isFinite(gpsAccuracy) || gpsAccuracy < 0 || gpsAccuracy > 30)) {
      return inState ? unroutedRoute("location_uncertain") : null;
    }
    if (Number.isFinite(gpsAccuracy)
        && geometryBoundaryDistanceMeters(lng, lat, geometry) <= gpsAccuracy) {
      return unroutedRoute("location_uncertain");
    }
    if (!inState) return null;
    const authority = config.authority || REMAINING_STATE_AUTHORITIES.get(config.pack_id);
    if (!authority || authority.id !== coverage.region.authority_id) {
      return unroutedRoute("jurisdiction_unavailable");
    }
    return authorityRoute(authority, {
      routing_source: config.routing_source,
      match_field: "boundary",
      match_value: `${config.name} (OpenStreetMap relation ${config.relation_id})`,
      region: config.region_id,
      pack_id: config.pack_id,
    });
  }

  const karnatakaStateRouteFromGeocode = (_geo, lat, lng, gpsAccuracy) =>
    pinnedStateRoute(lat, lng, gpsAccuracy, {
      envelope: KARNATAKA_STATE_ROUTING_ENVELOPE,
      coverage: karnatakaStateCoverage,
      authority: KARNATAKA_STATE_AUTHORITY,
      routing_source: "osm_karnataka_state_boundary",
      name: "Karnataka",
      relation_id: 2019939,
      region_id: "karnataka-state",
      pack_id: "in-ka-state-routing",
    });

  const keralaRouteFromGeocode = (_geo, lat, lng, gpsAccuracy) =>
    pinnedStateRoute(lat, lng, gpsAccuracy, {
      envelope: KERALA_ROUTING_ENVELOPE,
      coverage: keralaCoverage,
      authority: KERALA_STATE_AUTHORITY,
      routing_source: "osm_kerala_state_boundary",
      name: "Kerala",
      relation_id: 2018151,
      region_id: "kerala-state",
      pack_id: "in-kl-routing",
    });

  const uttarPradeshRouteFromGeocode = (_geo, lat, lng, gpsAccuracy) =>
    pinnedStateRoute(lat, lng, gpsAccuracy, {
      envelope: UTTAR_PRADESH_ROUTING_ENVELOPE,
      coverage: uttarPradeshCoverage,
      authority: UTTAR_PRADESH_STATE_AUTHORITY,
      routing_source: "osm_uttar_pradesh_state_boundary",
      name: "Uttar Pradesh",
      relation_id: 1942587,
      region_id: "uttar-pradesh-state",
      pack_id: "in-up-routing",
    });

  const chhattisgarhRouteFromGeocode = (_geo, lat, lng, gpsAccuracy) =>
    pinnedStateRoute(lat, lng, gpsAccuracy, {
      envelope: CHHATTISGARH_ROUTING_ENVELOPE,
      coverage: chhattisgarhCoverage,
      authority: CHHATTISGARH_STATE_AUTHORITY,
      routing_source: "osm_chhattisgarh_state_boundary",
      name: "Chhattisgarh",
      relation_id: 1972004,
      region_id: "chhattisgarh-state",
      pack_id: "in-cg-routing",
    });

  const rajasthanRouteFromGeocode = (_geo, lat, lng, gpsAccuracy) =>
    pinnedStateRoute(lat, lng, gpsAccuracy, {
      envelope: RAJASTHAN_ROUTING_ENVELOPE,
      coverage: rajasthanCoverage,
      authority: RAJASTHAN_STATE_AUTHORITY,
      routing_source: "osm_rajasthan_state_boundary",
      name: "Rajasthan",
      relation_id: 1942920,
      region_id: "rajasthan-state",
      pack_id: "in-rj-routing",
    });

  const goaRouteFromGeocode = (_geo, lat, lng, gpsAccuracy) =>
    pinnedStateRoute(lat, lng, gpsAccuracy, {
      envelope: GOA_ROUTING_ENVELOPE,
      coverage: goaCoverage,
      authority: GOA_STATE_AUTHORITY,
      routing_source: "osm_goa_state_boundary",
      name: "Goa",
      relation_id: 11251493,
      region_id: "goa-state",
      pack_id: "in-ga-routing",
    });

  const madhyaPradeshRouteFromGeocode = (_geo, lat, lng, gpsAccuracy) =>
    pinnedStateRoute(lat, lng, gpsAccuracy, {
      envelope: MADHYA_PRADESH_ROUTING_ENVELOPE,
      coverage: madhyaPradeshCoverage,
      authority: MADHYA_PRADESH_STATE_AUTHORITY,
      routing_source: "osm_madhya_pradesh_state_boundary",
      name: "Madhya Pradesh",
      relation_id: 1950071,
      region_id: "madhya-pradesh-state",
      pack_id: "in-mp-routing",
    });

  const biharRouteFromGeocode = (_geo, lat, lng, gpsAccuracy) =>
    pinnedStateRoute(lat, lng, gpsAccuracy, {
      envelope: BIHAR_ROUTING_ENVELOPE,
      coverage: biharCoverage,
      authority: BIHAR_STATE_AUTHORITY,
      routing_source: "osm_bihar_state_boundary",
      name: "Bihar",
      relation_id: 1958982,
      region_id: "bihar-state",
      pack_id: "in-br-routing",
    });

  const odishaRouteFromGeocode = (_geo, lat, lng, gpsAccuracy) =>
    pinnedStateRoute(lat, lng, gpsAccuracy, {
      envelope: ODISHA_ROUTING_ENVELOPE,
      coverage: odishaCoverage,
      authority: ODISHA_STATE_AUTHORITY,
      routing_source: "osm_odisha_state_boundary",
      name: "Odisha",
      relation_id: 1984022,
      region_id: "odisha-state",
      pack_id: "in-od-routing",
    });

  const remainingStateCoverage = (packId) => {
    const config = REMAINING_STATE_ROUTE_CONFIGS[packId];
    return config ? pinnedStateCoverage(packId, config.authority_id,
      config.relation_id, config.geometry_sha256) : Promise.resolve(null);
  };

  async function remainingStateRouteFromGeocode(_geo, lat, lng, gpsAccuracy) {
    let deferredFailure = null;
    for (const [packId, config] of Object.entries(REMAINING_STATE_ROUTE_CONFIGS)) {
      if (!pointInEnvelope(lat, lng, config.envelope)) continue;
      const route = await pinnedStateRoute(lat, lng, gpsAccuracy, {
        ...config,
        pack_id: packId,
        coverage: () => remainingStateCoverage(packId),
      });
      if (!route) continue;
      if (route.unrouted_reason === "jurisdiction_unavailable") {
        if (!deferredFailure) deferredFailure = route;
        continue;
      }
      return route;
    }
    return deferredFailure;
  }

  let _majorCityCoverage = null, _majorCityCoveragePromise = null;
  async function majorCityCoverage() {
    if (_majorCityCoverage) return _majorCityCoverage;
    if (_majorCityCoveragePromise) return _majorCityCoveragePromise;
    _majorCityCoveragePromise = (async () => {
      try {
        const pack = await loadStatePack("in-top50-routing");
        const payload = pack && pack.payload;
        if (payload && payload.version === 1 && Array.isArray(payload.regions)
            && payload.regions.length === 35) _majorCityCoverage = payload;
      } catch (e) { /* fail closed and allow a later retry */ }
      return _majorCityCoverage;
    })();
    const result = await _majorCityCoveragePromise;
    _majorCityCoveragePromise = null;
    return result;
  }

  async function majorCityRouteFromGeocode(geo, lat, lng, gpsAccuracy) {
    if (!inMajorCityCandidateEnvelope(lat, lng)) return null;
    const coverage = await majorCityCoverage();
    if (!coverage) return unroutedRoute("jurisdiction_unavailable");
    const candidates = coverage.regions.filter((region) =>
      pointInEnvelope(lat, lng, region.envelope));
    if (!candidates.length) return null;
    const matches = candidates.map((region) => ({ region, match: structuredPlaceMatch(geo, region) }))
      .filter((item) => !!item.match);
    // A stale or missing geocoder response cannot select a portal. Let exact polygon
    // routes and the final statewide check continue rather than letting a search box
    // masquerade as a municipal boundary.
    if (!matches.length) return null;
    if (!Number.isFinite(gpsAccuracy) || gpsAccuracy < 0 || gpsAccuracy > 30
        || matches.length > 1
        || !accuracyCircleWithinEnvelope(lat, lng, gpsAccuracy, matches[0].region.envelope)) {
      return unroutedRoute("location_uncertain");
    }
    const selected = matches[0];
    const authority = OFFICIAL_AUTHORITY_INDEX.get(selected.region.authority_id);
    if (!authority) return unroutedRoute("jurisdiction_unavailable");
    return authorityRoute(authority, {
      routing_source: selected.region.routing_source,
      match_field: "structured_place",
      match_value: `${selected.match.field}: ${selected.match.value}`,
      region: selected.region.id,
      pack_id: "in-top50-routing",
    });
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

  function accuracyCircleWithinEnvelope(lat, lng, accuracy, envelope) {
    if (!Number.isFinite(lat) || !Number.isFinite(lng)
        || !Number.isFinite(accuracy) || accuracy < 0 || accuracy > 30
        || !validMunicipalEnvelope(envelope)) return false;
    const circle = gpsAccuracyEnvelope(lat, lng, accuracy);
    return circle.xmin >= envelope.min_lng && circle.xmax <= envelope.max_lng
      && circle.ymin >= envelope.min_lat && circle.ymax <= envelope.max_lat;
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
    // A geocoder label is never allowed to make a distant city pack relevant. This is
    // especially important when Android returns a stale address for a fresh GPS fix.
    const relevant = pointInEnvelope(lat, lng, config.envelope);
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
      ...(issue === "road_damage" ? separateRoadResponsibility(route) : route),
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
    const relevant = inMaharashtraRoutingEnvelope(lat, lng);
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
      return (inState || inPmc || inMmr) ? unroutedRoute("location_uncertain") : null;
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

  // NHAI's public project inventory is partitioned by the State/UT named in each
  // official record. A highway route is resolved before the containing civic route,
  // so preserve the reverse-geocoder's State/UT only as a download key; the mapped NH
  // geometry still decides that this is a national highway. Unknown labels fail closed
  // instead of searching another jurisdiction's contracts.
  const INDIA_STATE_CODE_BY_NAME = new Map(Object.entries({
    "andaman and nicobar islands": "AN", "andaman nicobar islands": "AN",
    "andhra pradesh": "AP", "arunachal pradesh": "AR", assam: "AS", bihar: "BR",
    chhattisgarh: "CG", chattisgarh: "CG", chandigarh: "CH",
    "dadra and nagar haveli and daman and diu": "DH",
    "dadra nagar haveli daman diu": "DH", delhi: "DL",
    "national capital territory of delhi": "DL", "nct of delhi": "DL",
    goa: "GA", gujarat: "GJ", haryana: "HR", "himachal pradesh": "HP",
    jharkhand: "JH", "jammu and kashmir": "JK", karnataka: "KA", kerala: "KL",
    ladakh: "LA", lakshadweep: "LD", maharashtra: "MH", manipur: "MN",
    meghalaya: "ML", mizoram: "MZ", nagaland: "NL", odisha: "OD", orissa: "OD",
    puducherry: "PY", pondicherry: "PY", punjab: "PB", rajasthan: "RJ",
    sikkim: "SK", "tamil nadu": "TN", telangana: "TG", tripura: "TR",
    "uttar pradesh": "UP", uttarakhand: "UK", uttaranchal: "UK",
    "west bengal": "WB",
  }));

  const stateCodeForGeocode = (geo) => {
    if (!geo || String(geo.country_code || "").toLowerCase() !== "in") return null;
    return INDIA_STATE_CODE_BY_NAME.get(normaliseAuthorityValue(geo.state)) || null;
  };

  // A reverse-geocoder State label is only a hint near a border. National Highway routing
  // happens before the civic route, so independently verify that hint against the same
  // checksum-pinned outer State/UT boundary used by nationwide routing before it can select
  // a State-partitioned contract catalog.
  const CONTRACT_STATE_BOUNDARY_PACKS = Object.freeze({
    MH: "in-mh-routing", WB: "in-wb-routing", DL: "in-dl-routing",
    PB: "in-pb-routing", TN: "in-tn-state-routing", AP: "in-ap-routing",
    TG: "in-tg-state-routing", KA: "in-ka-state-routing", KL: "in-kl-routing",
    UP: "in-up-routing", CG: "in-cg-routing", RJ: "in-rj-routing",
    GA: "in-ga-routing", MP: "in-mp-routing", BR: "in-br-routing",
    OD: "in-od-routing",
    ...Object.fromEntries(Object.entries(REMAINING_STATE_ROUTE_CONFIGS)
      .map(([packId, config]) => [config.state_code, packId])),
  });

  function outerStateBoundaryGeometry(pack, stateCode) {
    if (!pack || pack.state_code !== stateCode || !pack.payload) return null;
    if (stateCode === "MH") {
      return pack.payload.regions && pack.payload.regions.maharashtra
        ? pack.payload.regions.maharashtra.geometry : null;
    }
    if (stateCode === "WB") {
      return pack.payload.regions && pack.payload.regions.west_bengal
        ? pack.payload.regions.west_bengal.geometry : null;
    }
    return pack.payload.region ? pack.payload.region.geometry : null;
  }

  async function exactPinnedContractStateCode(stateCode, lat, lng, gpsAccuracy) {
    const code = String(stateCode || "");
    const packId = CONTRACT_STATE_BOUNDARY_PACKS[code];
    if (!packId || !Number.isFinite(lat) || !Number.isFinite(lng)
        || !Number.isFinite(gpsAccuracy) || gpsAccuracy < 0 || gpsAccuracy > 30) return null;
    try {
      const pack = await loadStatePack(packId);
      const geometry = outerStateBoundaryGeometry(pack, code);
      if (!hasCoverageGeometry(geometry) || !pointInGeometry(lng, lat, geometry)
          || geometryBoundaryDistanceMeters(lng, lat, geometry) <= gpsAccuracy) return null;
      return code;
    } catch (e) { return null; }
  }

  async function routeOfficerCore(geoOrAddress, lat, lng, gpsAccuracy, heading, speed,
                                   requestedIssueType) {
    const issueType = normaliseIssueType(requestedIssueType);
    if (lat == null || lng == null || Number.isNaN(lat) || Number.isNaN(lng)) {
      return routeForIssue(unroutedRoute("no_location"), issueType);
    }

    const geo = geoOrAddress && typeof geoOrAddress === "object" ? geoOrAddress : null;
    const geocodeStateCode = stateCodeForGeocode(geo);
    const exactContractStateP = issueType === "road_damage" && geocodeStateCode
      ? optionalCatalogResult(
          exactPinnedContractStateCode(geocodeStateCode, lat, lng, gpsAccuracy))
      : Promise.resolve(null);
    // Road class outranks the containing city. Without this first check, a pothole on an
    // NH passing through Delhi, Kolkata, Chennai, Hyderabad, Ahmedabad, MMR or Pune can
    // be addressed to the municipal body even though the highway has another maintainer.
    const highway = issueType === "road_damage"
      ? await nationalHighwayRoute(lat, lng, gpsAccuracy, heading, speed) : null;
    if (highway) {
      const contractStateCode = await exactContractStateP;
      return routeForIssue({
        ...highway,
        contract_state_code: contractStateCode,
        // A pack is loaded only when an official project source has records for this
        // jurisdiction. Eligibility means "candidate search allowed", never that this
        // point has already been assigned to a contract.
        tender_eligible: !!contractStateCode,
      }, issueType);
    }

    // Coarse download envelopes overlap neighbouring jurisdictions. Preserve a pack
    // failure as the eventual answer, but keep evaluating independent exact polygons.
    // In particular, Delhi's prefilter contains Noida and Ghaziabad in Uttar Pradesh.
    let deferredJurisdictionFailure = null;
    const delhi = await delhiRouteFromGeocode(geo, lat, lng, gpsAccuracy);
    if (delhi && delhi.unrouted_reason !== "outside_area") {
      if (delhi.unrouted_reason === "jurisdiction_unavailable") {
        deferredJurisdictionFailure = delhi;
      } else {
        return routeForIssue(delhi, issueType);
      }
    }

    const kolkata = await kolkataRouteFromGeocode(geo, lat, lng, gpsAccuracy);
    if (kolkata && kolkata.unrouted_reason !== "outside_area") {
      return routeForIssue(kolkata, issueType);
    }

    // A missing pack inside a coarse prefilter is not proof that the point belongs to
    // that jurisdiction. Remember the transient failure, but let independent exact
    // routes continue. This is especially important near Tamil Nadu: its download
    // envelope also contains Bengaluru and Kochi.
    for (const packId of ["in-tn-routing", "in-tg-routing", "in-gj-routing"]) {
      const municipal = await municipalCityRouteFromGeocode(packId, geo, lat, lng, gpsAccuracy);
      if (municipal && municipal.unrouted_reason !== "outside_area") {
        if (municipal.unrouted_reason === "jurisdiction_unavailable") {
          if (!deferredJurisdictionFailure) deferredJurisdictionFailure = municipal;
          continue;
        }
        return routeForIssue(municipal, issueType);
      }
    }

    // My Cure remains the exact route inside verified Hyderabad CURE coverage.
    // Everywhere else in the exact Telangana state polygon—including a CURE service
    // failure or Cantonment exclusion—may use the neutral statewide Prajavani route.
    const telangana = await telanganaRouteFromGeocode(geo, lat, lng, gpsAccuracy);
    if (telangana) {
      if (telangana.unrouted_reason === "jurisdiction_unavailable") {
        if (!deferredJurisdictionFailure) deferredJurisdictionFailure = telangana;
      } else {
        return routeForIssue(telangana, issueType);
      }
    }

    // GCC's exact municipal polygon above keeps precedence inside Chennai. Everywhere
    // else in the exact Tamil Nadu state polygon uses the neutral statewide channel.
    const tamilNadu = await tamilNaduRouteFromGeocode(geo, lat, lng, gpsAccuracy);
    if (tamilNadu) {
      if (tamilNadu.unrouted_reason === "jurisdiction_unavailable") {
        if (!deferredJurisdictionFailure) deferredJurisdictionFailure = tamilNadu;
      } else {
        return routeForIssue(tamilNadu, issueType);
      }
    }

    const andhraPradesh = await andhraPradeshRouteFromGeocode(
      geo, lat, lng, gpsAccuracy);
    if (andhraPradesh) {
      if (andhraPradesh.unrouted_reason === "jurisdiction_unavailable") {
        if (!deferredJurisdictionFailure) deferredJurisdictionFailure = andhraPradesh;
      } else {
        return routeForIssue(andhraPradesh, issueType);
      }
    }

    const maharashtra = await maharashtraRouteFromGeocode(geo, lat, lng, gpsAccuracy);
    if (maharashtra) {
      // Maharashtra's coarse download envelope overlaps northern Karnataka. A missing
      // Maharashtra pack is not evidence that a Kalaburagi-area point belongs there;
      // preserve the failure but allow Karnataka's independent exact polygon to decide.
      if (maharashtra.unrouted_reason === "jurisdiction_unavailable") {
        if (!deferredJurisdictionFailure) deferredJurisdictionFailure = maharashtra;
      } else {
        return routeForIssue(maharashtra, issueType);
      }
    }

    const punjab = await punjabRouteFromGeocode(geo, lat, lng, gpsAccuracy);
    if (punjab) {
      // Punjab's coarse download envelope reaches into northern Rajasthan. A failed
      // Punjab pack must not turn that overlap into evidence that a Rajasthan point
      // is unroutable; retain the failure while the exact state polygons continue.
      if (punjab.unrouted_reason === "jurisdiction_unavailable") {
        if (!deferredJurisdictionFailure) deferredJurisdictionFailure = punjab;
      } else {
        return routeForIssue(punjab, issueType);
      }
    }

    // The old top-50 pack names seven Kerala cities from geocoder text. The exact state
    // polygon is stronger evidence and now covers every Kerala location, so it gets the
    // vote first while the old pack remains available only for saved-report compatibility.
    const kerala = await keralaRouteFromGeocode(geo, lat, lng, gpsAccuracy);
    if (kerala) {
      if (kerala.unrouted_reason === "jurisdiction_unavailable") {
        if (!deferredJurisdictionFailure) deferredJurisdictionFailure = kerala;
      } else {
        return routeForIssue(kerala, issueType);
      }
    }

    // Exact state polygons supersede the old city-name routes in the immutable top-50
    // compatibility pack. Delhi and every earlier reviewed state/city route keep their
    // first refusal, even where the coarse Uttar Pradesh or Chhattisgarh envelopes overlap.
    const uttarPradesh = await uttarPradeshRouteFromGeocode(
      geo, lat, lng, gpsAccuracy);
    if (uttarPradesh) {
      if (uttarPradesh.unrouted_reason === "jurisdiction_unavailable") {
        if (!deferredJurisdictionFailure) deferredJurisdictionFailure = uttarPradesh;
      } else {
        return routeForIssue(uttarPradesh, issueType);
      }
    }

    const chhattisgarh = await chhattisgarhRouteFromGeocode(
      geo, lat, lng, gpsAccuracy);
    if (chhattisgarh) {
      if (chhattisgarh.unrouted_reason === "jurisdiction_unavailable") {
        if (!deferredJurisdictionFailure) deferredJurisdictionFailure = chhattisgarh;
      } else {
        return routeForIssue(chhattisgarh, issueType);
      }
    }

    const rajasthan = await rajasthanRouteFromGeocode(geo, lat, lng, gpsAccuracy);
    if (rajasthan) {
      if (rajasthan.unrouted_reason === "jurisdiction_unavailable") {
        if (!deferredJurisdictionFailure) deferredJurisdictionFailure = rajasthan;
      } else {
        return routeForIssue(rajasthan, issueType);
      }
    }

    // These exact state polygons sit inside coarse download envelopes that overlap
    // several already-supported neighbours. A failed download is deferred so another
    // independently verified polygon can still win; coordinates never route from the
    // envelope or a reverse-geocoder state label alone.
    for (const statewideRoute of [
      goaRouteFromGeocode, madhyaPradeshRouteFromGeocode,
      biharRouteFromGeocode, odishaRouteFromGeocode,
    ]) {
      const state = await statewideRoute(geo, lat, lng, gpsAccuracy);
      if (!state) continue;
      if (state.unrouted_reason === "jurisdiction_unavailable") {
        if (!deferredJurisdictionFailure) deferredJurisdictionFailure = state;
      } else {
        return routeForIssue(state, issueType);
      }
    }

    const majorCity = await majorCityRouteFromGeocode(geo, lat, lng, gpsAccuracy);
    if (majorCity) {
      // The immutable compatibility pack still contains older city-only state routes so
      // saved reports can be revalidated. A missing current statewide pack must never
      // make a new report silently fall back to those stale channels; retain the
      // transient failure so the user can retry safely.
      const supersededCityRoute = majorCity.authority_id === "in-ap-puramithra"
        || majorCity.authority_id === "in-tn-cm-helpline"
        || majorCity.authority_id === "in-kl-ksmart"
        || majorCity.authority_id === "in-up-jansunwai"
        || majorCity.authority_id === "in-cg-nidaan"
        || majorCity.authority_id === "in-rj-sampark"
        || majorCity.authority_id === "in-mp-cm-helpline"
        || majorCity.authority_id === "in-br-lok-shikayat";
      if (deferredJurisdictionFailure && supersededCityRoute) {
        return routeForIssue(deferredJurisdictionFailure, issueType);
      }
      return routeForIssue(majorCity, issueType);
    }

    // Every State and Union Territory now has an exact checksum-pinned outer boundary.
    // Existing exact municipal routes and reviewed city-specific handoffs above retain
    // precedence; these additions fill every remaining coordinate conservatively.
    const remainingState = await remainingStateRouteFromGeocode(
      geo, lat, lng, gpsAccuracy);
    if (remainingState) {
      if (remainingState.unrouted_reason === "jurisdiction_unavailable") {
        if (!deferredJurisdictionFailure) deferredJurisdictionFailure = remainingState;
      } else {
        return routeForIssue(remainingState, issueType);
      }
    }

    // Karnataka's polygon decides whether a neutral state fallback is permitted. KGIS
    // may still select a more specific reviewed urban body, but neither the old coarse
    // rectangle nor a reverse-geocoder label can establish state membership.
    const karnatakaState = await karnatakaStateRouteFromGeocode(
      geo, lat, lng, gpsAccuracy);
    if (!karnatakaState) {
      return routeForIssue(
        deferredJurisdictionFailure || unroutedRoute("outside_area"), issueType);
    }
    if (karnatakaState.unrouted_reason === "location_uncertain") {
      return routeForIssue(karnatakaState, issueType);
    }
    const karnatakaFallback = karnatakaState.routed ? karnatakaState : null;

    // The nationwide NH geometry has already had first refusal. KGIS can now improve a
    // Karnataka match to a verified urban body; any ambiguous rural/owner result stays
    // with Janaspandana and is explicitly marked ownership-unverified.
    let where;
    try {
      where = issueType === "road_damage"
        ? await jurisdictionOf(lat, lng) : await kgisCivicJurisdiction(lat, lng);
    } catch (e) {
      return routeForIssue(karnatakaFallback || unroutedRoute(
        issueType === "road_damage" ? "road_class_unknown" : "jurisdiction_unavailable"), issueType);
    }

    if (where.kind === "outside_state") {
      return routeForIssue(karnatakaFallback
        || deferredJurisdictionFailure || unroutedRoute("outside_area"), issueType);
    }
    if (where.kind === "jurisdiction_unavailable") {
      return routeForIssue(karnatakaFallback
        || unroutedRoute("jurisdiction_unavailable"), issueType);
    }
    if (where.kind === "national_highway") {
      return routeForIssue(issueType === "road_damage"
        ? unroutedRoute("national_highway", where.name)
        : unroutedRoute("outside_area", where.name), issueType);
    }
    if (where.kind === "road_class_unknown") return routeForIssue(karnatakaFallback
      || unroutedRoute("road_class_unknown"), issueType);
    if (where.kind === "rural") return routeForIssue(karnatakaFallback
      || unroutedRoute("rural_road", where.name), issueType);

    const registry = await bodies();
    if (!registry) return routeForIssue(karnatakaFallback
      || unroutedRoute("jurisdiction_unavailable", where.name), issueType);
    const entry = where.lgd && registry[where.lgd];
    if (!entry || !entry.email) return routeForIssue(karnatakaFallback
      || unroutedRoute("no_address_for_body", where.name), issueType);
    const title = entry.officer || OFFICER_TITLES[entry.type || where.type] || "Chief Officer";
    const exactKarnataka = routeForIssue({
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
      // KGIS proves the containing urban body, not which agency maintains this segment.
      ownership_unverified: true,
      requires_official_reference: false,
      tender_eligible: true,
      ...statePackProvenance("in-ka-routing"),
    }, issueType);
    if (issueType !== "road_damage" && !exactKarnataka.routed) {
      return routeForIssue(karnatakaFallback || exactKarnataka, issueType);
    }
    return exactKarnataka;
  }

  async function routeOfficer(geoOrAddress, lat, lng, gpsAccuracy, heading, speed,
                              requestedIssueType) {
    const route = await routeOfficerCore(
      geoOrAddress, lat, lng, gpsAccuracy, heading, speed, requestedIssueType);
    const issueType = normaliseIssueType(requestedIssueType);
    const stateCode = stateCodeForGeocode(
      geoOrAddress && typeof geoOrAddress === "object" ? geoOrAddress : null);
    const contractStateCode = trustedContractStateCode(route, stateCode);
    // A State/UT routing pack is stronger jurisdiction evidence than reverse geocoding
    // near a border. The geocoder may key nationwide/NH routes, but a State pack must
    // agree exactly; disagreement disables contract lookup instead of crossing borders.
    if (!route || !route.routed || issueType !== "road_damage") return route;
    const updated = { ...route, contract_state_code: contractStateCode };
    if (!contractStateCode && route.region === "national-highway") {
      updated.tender_eligible = false;
    }
    return updated;
  }

  function trustedContractStateCode(route, geocodeStateCode) {
    if (!route || route.routed !== true
        || !/^[A-Z]{2}$/.test(String(geocodeStateCode || ""))) return null;
    const packState = String(route.routing_pack_state_code || "");
    if (["IN", "NH"].includes(packState)) {
      if (route.region === "national-highway") {
        return route.contract_state_code === geocodeStateCode ? geocodeStateCode : null;
      }
      return geocodeStateCode;
    }
    if (/^[A-Z]{2}$/.test(packState)) {
      return packState === geocodeStateCode ? packState : null;
    }
    return null;
  }

  function distMeters(lat1, lng1, lat2, lng2) {
    const rad = Math.PI / 180;
    const dLat = (lat2 - lat1) * rad, dLng = (lng2 - lng1) * rad;
    const a = Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(dLng / 2) ** 2;
    return 2 * 6371000 * Math.asin(Math.sqrt(a));
  }

  const finiteCoord = (v) => typeof v === "number" && Number.isFinite(v);
  const conditionStatus = (r) => r && (r.condition_status === "fixed"
    || r.condition_status === "repair_review") ? r.condition_status : "open";
  const acceptedReport = (r) => !!r && conditionStatus(r) !== "fixed"
    && (r.decision === "accept" || ACCEPTED_REPORT_STATUSES.has(r.status));
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

  function repairTargetMatch(observation, prior) {
    if (!observation || observation.capture_source !== "drive_live" || observation.debug_capture
        || !prior || normaliseIssueType(prior.issue_type) !== "road_damage"
        || !acceptedReport(prior) || prior.debug_capture || prior.dedupe_eligible === false
        || !prior.photo || !finiteCoord(observation.lat) || !finiteCoord(observation.lng)
        || !finiteCoord(prior.lat) || !finiteCoord(prior.lng)
        || !Number.isFinite(observation.gps_accuracy) || observation.gps_accuracy < 0
        || observation.gps_accuracy > REPAIR_MAX_ACCURACY_M
        || !Number.isFinite(prior.gps_accuracy) || prior.gps_accuracy < 0
        || prior.gps_accuracy > REPAIR_MAX_ACCURACY_M) return null;
    // A repair is a later physical observation, never a reinterpretation of the
    // original frame. Missing clocks and equal/older timestamps therefore fail closed.
    const observedAt = observation.observed_at;
    const damageObservedAt = eventTime(prior);
    if (!Number.isFinite(observedAt) || !Number.isFinite(damageObservedAt)
        || observedAt <= damageObservedAt) return null;
    const driveId = observation.drive_id == null ? null : String(observation.drive_id);
    if (!driveId) return null;
    const priorDrives = new Set(Array.isArray(prior.sighting_drive_ids)
      ? prior.sighting_drive_ids.map(String) : []);
    if ((prior.drive_id != null && String(prior.drive_id) === driveId) || priorDrives.has(driveId)) {
      return null;
    }
    const distance = distMeters(observation.lat, observation.lng, prior.lat, prior.lng);
    let radius = REPAIR_RADIUS_M;
    const headingsKnown = Number.isFinite(observation.heading) && Number.isFinite(prior.heading);
    if (headingsKnown) {
      if (headingDifference(observation.heading, prior.heading)
          > REPAIR_MAX_HEADING_DIFFERENCE_DEG) return null;
    } else {
      radius = REPAIR_MISSING_HEADING_RADIUS_M;
    }
    return distance <= radius ? { distance } : null;
  }

  function findRepairCandidateFromReports(observation, reports) {
    const matches = [];
    for (const prior of reports || []) {
      const match = repairTargetMatch(observation, prior);
      if (match) matches.push({ prior, distance: match.distance });
      if (matches.length > 1) return null;
    }
    return matches.length === 1 ? matches[0].prior : null;
  }

  function findDuplicateReport(candidate, reports) {
    for (let i = reports.length - 1; i >= 0; i--) {
      if (sameRoadEvent(candidate, reports[i])) return reports[i];
    }
    return null;
  }

  // ---------- tenders ----------
  let _tenders = null;
  // A road name in a tender can be only the address of a drain, footpath, light or
  // building. This fail-closed classifier mirrors tools/tender_scope.py so even an old
  // cached pack can never offer those rows to the model or complaint writer.
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
  const nonWorksServiceRe = /\bconsult(?:ant|ancy|ants|ing)\b|\b(?:authority|independent)\s+engineer(?:ing)?\b|\bproject\s+management\s+(?:consult(?:ant|ancy|ing)|services?)\b|\b(?:preparation|prepare|preparing|revision|review)\s+of\s+(?:a\s+|the\s+)?(?:detailed\s+project\s+report|dpr)\b|\b(?:detailed\s+project\s+report|dpr)\s+(?:preparation|consultancy|services?)\b|\b(?:feasibility|traffic)\s+(?:study|studies|survey|surveys)\b|\bsurvey\s+(?:and|&)\s+investigation\b|\bthird\s+party\s+(?:inspection|quality\s+(?:audit|monitoring))\b|\b(?:quality\s+control|proof\s+checking)\s+(?:consultancy|services?)\b|\bsupply(?:ing)?\s+of\b.*\b(?:aggregate|asphalt|bitumen|cold\s+mix|stone\s+dust)\b/;
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
    const text = tenderTokens(title).join(" ");
    if (!text) return false;
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
        if (!priorAssets || coordinatedRoadNoun(tokens, roadIndex)) return true;
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

  // The optional pack contains only rows with a verified civic-body ID: the other 28,706
  // procurement rows could never enter the matcher. Failure omits contract context but
  // never blocks a valid road-damage report.
  async function tenders() {
    if (_tenders) return _tenders;
    try {
      const pack = await loadStatePack("in-ka-tenders");
      const loaded = pack && pack.tenders;
      if (Array.isArray(loaded) && loaded.length) {
        _tenders = loaded.filter((row) => tenderCoversCarriageway(row.t, row.tn));
        return _tenders;
      }
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
    // The procurement snapshot still indexes pre-reorganisation BBMP work as BLR. Make
    // that pool searchable from each successor corporation, but only as candidates: the
    // shortlist and complaint must not treat this lookup as ownership, segment, award or
    // DLP evidence. Explicit non-carriageway work has already been removed by tenders().
    const legacy = BLR_BODIES.has(String(lgd)) ? (_byBody.get("BLR") || []) : [];
    return legacy.length ? own.concat(legacy) : own;
  }


  // The five corporations that replaced BBMP and may search its legacy candidate pool.
  const BLR_BODIES = new Set(["305850", "305851", "305852", "305853", "305854"]);

  const TENDER_STOP = new Set(["road", "roads", "street", "cross", "main", "layout", "bengaluru", "bangalore",
    "karnataka", "india", "ward", "city", "corporation", "south", "north", "east",
    "west", "central", "urban", "sector", "stage", "block", "phase"]);

  // The compact procurement snapshot proves only what its row actually contains.
  // Publication predates award, execution and completion and therefore proves no DLP.
  function contractVerificationFor(record) {
    const title = String(record && (record.title || record.t) || "").replace(/\s+/g, " ").trim();
    const tenderNumber = String(record && (record.tender_number || record.tn) || "").trim();
    return {
      candidate_status: "candidate",
      scope_status: tenderCoversCarriageway(title, tenderNumber)
        ? "carriageway_scope_present" : "ineligible_scope",
      scope_verified: tenderCoversCarriageway(title, tenderNumber),
      segment_status: "unverified",
      segment_verified: false,
      award_status: "unverified",
      award_verified: false,
      dlp_status: "unverified",
      dlp_verified: false,
    };
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
    const pool = (await tendersFor(lgd)).filter((row) => tenderCoversCarriageway(row.t, row.tn));
    if (!pool.length) return [];

    // Scored on the work description alone. The location field is the body's own name,
    // identical in every one of its rows, so it cannot tell one of the body's roads from
    // another: including it only added the town's name to every candidate equally.
    const hays = pool.map((t) => new Set(tenderTokens(t.t)));

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
      for (const hay of hays) if (hay.has(tok)) df++;
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
      const matchTokens = [];
      for (const [tok, w] of idf) {
        if (!hays[i].has(tok)) continue;
        score += w;
        matchTokens.push(tok);
      }
      if (score > 0) scored.push({ score, match_tokens: matchTokens.sort(), t: pool[i] });
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
    return scored.slice(0, 25).map((x) => ({
      score: x.score, match_tokens: x.match_tokens, tn: x.t.tn, t: x.t,
    }));
  }

  async function matchTender(address, lgd) {
    if (!address || !S.key || !lgd) return null;
    // The pool is limited to this indexed body, plus legacy BBMP rows for Bengaluru's
    // five successor corporations. That blocks cross-city records, but proves neither
    // road ownership nor that a tender became an awarded contract.
    const ranked = await shortlistFor(address, lgd);
    if (!ranked.length) return null;
    const candidates = ranked.map((x) => x.t);
    const listing = candidates.map((t, i) =>
      `${i}: ${t.t.slice(0, 150)} | ${t.loc} | published: ${t.d}`).join("\n");
    const prompt = `You screen public procurement listings for a possible road-work contract candidate.
The candidate pool is indexed to the containing civic body or, in Bengaluru, its legacy
BBMP area. This does not prove road ownership, award, execution, completion, DLP, or that
the photographed segment is covered. Your only job is whether the work description clearly
covers this exact road stretch or immediate locality.
The road defect's reverse-geocoded address is:
${address}

Candidate records (index: work description | division | published):
${listing}

Pick the single contract whose work description covers this exact road stretch or
its immediate locality (same layout, ward or named road). Road names repeat across
localities within a town, so the locality or ward context must agree, not just the
road name. A ward-wide maintenance or pothole-filling contract for the pothole's own
locality or ward is a valid match. A ward-wide maintenance or pothole-filling contract for the pothole's
own layout or ward is a valid match. If no candidate clearly covers this location,
match_index must be null. Never select a contract whose actual work is only a drain,
footpath, sewer, pipeline, light, building, bridge, culvert or other roadside asset;
the road name can merely describe that asset's location. Mixed scope is valid only when
work on the carriageway itself is explicit. confidence is your 0 to 1 confidence in the match.`;
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
    if (!m || m.match_index === null || m.match_index < 0
        || m.match_index >= candidates.length || m.confidence < 0.6) return null;
    // The model may veto the lexical leader, but it may not promote a weaker row. Require
    // two independent address words and a clear lead over the runner-up. False negatives
    // are safer than showing an unrelated public record as a road-contract candidate.
    const selected = ranked[m.match_index], second = ranked[1];
    if (m.match_index !== 0 || selected.match_tokens.length < 2
        || (second && selected.score - second.score
          < Math.max(0.75, selected.score * 0.15))) return null;
    const t = selected.t;
    if (!tenderCoversCarriageway(t.t, t.tn)) return null;
    // The compact legacy snapshot's name field is not an official award/work-order
    // receipt. Never attach it to a photographed road or carry it into complaint data.
    const contractor = null;
    const title = String(t.t || "").replace(/\s+/g, " ").trim();
    return {
      tender_number: t.tn, contractor, title, published: t.d,
      organisation: t.loc || "Karnataka procuring body not listed",
      detail_url: "https://kppp.karnataka.gov.in/",
      bid_closing: null,
      bid_opening: null,
      project_start: null,
      project_completion: null,
      agreement_number: null,
      agreement_date: null,
      package_reference: null,
      highway_reference: null,
      published_chainage: null,
      lifecycle: "procurement_record",
      lifecycle_status: "Published procurement record; award/work-order status unverified",
      match_basis: `Containing civic body ${lgd}; model-selected exact road/locality wording`,
      source_name: "Karnataka Public Procurement Portal (KPPP) snapshot",
      source_url: "https://kppp.karnataka.gov.in/",
      match_confidence: m.confidence,
      ...contractVerificationFor({ tender_number: t.tn, title }),
      note: `Unverified research lead (not included in the complaint): ${t.tn} — ${title}`,
      ...statePackProvenance("in-ka-tenders", "tender"),
    };
  }

  const highwayRefsOf = (value) => String(value || "").split(" / ")
    .map((ref) => ref.trim().toUpperCase()).filter((ref) => HIGHWAY_REF_RE.test(ref));

  const HIGHWAY_CONTRACT_LOCATION_STOP = new Set([
    ...TENDER_STOP,
    ...[...INDIA_STATE_CODE_BY_NAME.keys()].flatMap((name) => tenderTokens(name)),
    "area", "at", "district", "from", "highway", "junction", "near", "number", "route",
    "state", "towards", "via",
  ]);

  function highwayContractCandidates(records, highwayRef, address = "") {
    const routeRefs = new Set(highwayRefsOf(highwayRef));
    if (!routeRefs.size || !Array.isArray(records)) return [];
    const addressParts = String(address || "").split(",").slice(0, 3).map((part) =>
      tenderTokens(part).filter((token) => token.length > 2
        && !HIGHWAY_CONTRACT_LOCATION_STOP.has(token))).filter((part) => part.length);
    const addressTokens = new Set(addressParts.flat());
    if (!addressTokens.size) return [];
    const eligible = [];
    for (const record of records) {
      if (!record || record.scope_verified !== true
          || !tenderCoversCarriageway(record.title, record.reference_value)) continue;
      const matchingRefs = (record.highway_refs || []).filter((ref) => routeRefs.has(ref));
      if (matchingRefs.length) eligible.push({ record, matching_refs: matchingRefs });
    }
    const titleTokensByRecord = eligible.map(({ record }) =>
      new Set(tenderTokens(record.title)));
    const frequencies = new Map();
    for (const token of addressTokens) {
      frequencies.set(token, titleTokensByRecord.reduce(
        (count, tokens) => count + (tokens.has(token) ? 1 : 0), 0));
    }
    const scored = [];
    for (let index = 0; index < eligible.length; index++) {
      const { record, matching_refs: matchingRefs } = eligible[index];
      const titleTokens = titleTokensByRecord[index];
      const localityHits = [...addressTokens].filter((token) => titleTokens.has(token));
      const normalisedTitle = tenderTokens(record.title).join(" ");
      const phraseHits = addressParts.filter((part) => part.length >= 2
        && normalisedTitle.includes(part.join(" ")));
      const uniqueLongHits = localityHits.filter((token) => token.length >= 6
        && frequencies.get(token) === 1);
      // An NH reference identifies a route, not which package covers this point; feeder
      // roads also cite the NH they meet. A single place word is never enough, even when
      // unique in this snapshot: require a multi-word phrase or two address words.
      if (!phraseHits.length && localityHits.length < 2) continue;
      let score = matchingRefs.length * 100 + localityHits.length * 8;
      score += phraseHits.length * 30 + uniqueLongHits.length * 16;
      if (record.lifecycle === "current_project") score += 30;
      if (record.award_verified && record.contractor) score += 15;
      if (/maintenance|o\s*&\s*m|under construction/i.test(record.lifecycle_status)) score += 8;
      if (record.chainages && record.chainages.length) score += 2;
      scored.push({ record, matching_refs: matchingRefs, locality_hits: localityHits,
        phrase_hits: phraseHits, unique_long_hits: uniqueLongHits, score });
    }
    scored.sort((left, right) => (right.score - left.score)
      || (right.phrase_hits.length - left.phrase_hits.length)
      || (right.locality_hits.length - left.locality_hits.length)
      || String(left.record.record_id).localeCompare(String(right.record.record_id)));
    return scored;
  }

  function candidateLeadIsUnambiguous(ranked, minimumGap) {
    if (!Array.isArray(ranked) || !ranked.length) return false;
    return !ranked[1]
      || Number(ranked[0].score) - Number(ranked[1].score) >= minimumGap;
  }

  async function matchHighwayContract(address, route) {
    const stateCode = route && route.contract_state_code;
    if (!route || route.region !== "national-highway" || route.tender_eligible !== true
        || !stateCode || !route.highway_ref) return null;
    const pack = await loadHighwayContractPack(stateCode);
    const ranked = highwayContractCandidates(pack && pack.contracts, route.highway_ref, address);
    if (!ranked.length) return null;
    const best = ranked[0];
    // No authoritative geometry connects the phone's point to a published chainage.
    // Suppress near-ties instead of presenting a deterministic but arbitrary package.
    if (!candidateLeadIsUnambiguous(ranked, 20)) return null;
    const { record, matching_refs: matchingRefs, locality_hits: localityHits } = best;
    const lifecycleNote = record.lifecycle === "procurement_notice"
      ? "Open procurement notice; no contractor or award is asserted"
      : `Official project lifecycle: ${record.lifecycle_status}`;
    return {
      tender_number: record.reference_value,
      reference_label: record.reference_label,
      // This source-reported project contractor is not attached to the GPS observation:
      // published highway chainage is not mapped to an authoritative point geometry and
      // active maintenance/DLP responsibility is absent.
      contractor: null,
      title: record.title,
      published: record.published_at || record.start_date,
      organisation: [record.agency, record.division].filter(Boolean).join(" — ") || null,
      detail_url: record.source_url,
      bid_closing: null,
      bid_opening: null,
      project_start: record.start_date,
      project_completion: record.likely_completion_date,
      agreement_number: null,
      agreement_date: null,
      package_reference: record.reference_value,
      highway_reference: (record.highway_refs || []).join(" / ") || null,
      published_chainage: (record.chainages || []).map((range) =>
        `km ${range.start_km}–${range.end_km}`).join("; ") || null,
      source_name: record.source_name,
      source_url: record.source_url,
      lifecycle: record.lifecycle,
      lifecycle_status: record.lifecycle_status,
      match_basis: `State/UT ${stateCode}; mapped ${matchingRefs.join(" / ")}`
        + (localityHits.length ? `; title/address ${localityHits.join(", ")}` : ""),
      candidate_status: "candidate",
      scope_status: "carriageway_scope_present",
      scope_verified: true,
      // The source publishes a highway/package chainage, but the OSM route geometry has
      // no authoritative chainage origin. Do not claim this GPS point lies in that range.
      segment_status: "unverified_chainage",
      segment_verified: false,
      award_status: record.award_verified
        ? "source_project_award_not_attributed_to_gps_segment" : "unverified",
      award_verified: false,
      dlp_status: "unverified",
      dlp_verified: false,
      note: `Unverified research lead (not included in the complaint): `
        + `${record.reference_label} ${record.reference_value}. ${lifecycleNote}.`,
      ...contractPackProvenance(stateCode),
    };
  }

  const ROAD_NOTICE_STOP = new Set([...TENDER_STOP,
    "area", "avenue", "bazaar", "bazar", "bridge", "chowk", "circle", "colony",
    "district", "extension", "galli", "lane", "locality", "market", "municipal",
    "municipality", "nagar", "near", "number", "path", "place", "sector", "state",
    "village", "zone"]);

  function highwayRefsInNotice(value) {
    const refs = new Set();
    const pattern = /\bN([HE])\s*[-:]?\s*([0-9]{1,4}[A-Z]{0,3})\b/gi;
    for (const match of String(value || "").matchAll(pattern)) {
      refs.add(`N${match[1].toUpperCase()}-${match[2].toUpperCase()}`);
    }
    return refs;
  }

  function roadNoticeAddressParts(address) {
    // Nominatim's compact address ends with the city. A city name is shared by hundreds
    // of unrelated notices and once made Kanjur, Mumbai select a Pune road whose title
    // merely contained "old Mumbai-Pune". Road plus immediate locality are the evidence.
    return String(address || "").split(",").slice(0, 2).map((part) =>
      tenderTokens(part).filter((token) => token.length >= 3
        && !/^\d{5,6}$/.test(token) && !ROAD_NOTICE_STOP.has(token)))
      .filter((tokens) => tokens.length);
  }

  function roadNoticeCandidates(records, address, route = null, now = Date.now()) {
    if (!Array.isArray(records) || !records.length) return [];
    const addressParts = roadNoticeAddressParts(address);
    const addressTokens = new Set(addressParts.flat());
    const routeRefs = new Set(highwayRefsOf(route && route.highway_ref));
    if (!addressTokens.size && !routeRefs.size) return [];

    const titleTokens = records.map((record) => new Set(tenderTokens(record && record.title)));
    const frequencies = new Map();
    for (const token of addressTokens) {
      frequencies.set(token, titleTokens.reduce(
        (count, tokens) => count + (tokens.has(token) ? 1 : 0), 0));
    }
    const routeAuthorityTokens = new Set(tenderTokens(route && route.authority_name)
      .filter((token) => token.length >= 4 && !ROAD_NOTICE_STOP.has(token)));
    const scored = [];
    for (let index = 0; index < records.length; index++) {
      const record = records[index];
      if (!record || record.lifecycle !== "procurement_notice" || record.scope !== "road_surface"
          || record.segment_verified !== false || record.award_verified !== false
          || record.dlp_verified !== false
          || !Number.isFinite(Date.parse(String(record.closing_at || "")))
          || Date.parse(record.closing_at) < now
          || !tenderCoversCarriageway(record.title, record.tender_reference)) continue;
      const tokens = titleTokens[index];
      const tokenHits = [...addressTokens].filter((token) => tokens.has(token));
      const phraseHits = addressParts.filter((part) => {
        const phrase = part.join(" ");
        return part.length >= 2 && phrase.length >= 6
          && tenderTokens(record.title).join(" ").includes(phrase);
      });
      const rareHits = tokenHits.filter((token) => token.length >= 6
        && frequencies.get(token) > 0 && frequencies.get(token) <= 2);
      const noticeRefs = highwayRefsInNotice(`${record.title} ${record.tender_reference}`);
      const highwayHits = [...routeRefs].filter((ref) => noticeRefs.has(ref));
      // One locality word is too weak for a statewide title index, even if it happens to
      // be rare in today's snapshot. Require an exact multi-word phrase or two distinct
      // address words; the rare-word signal may rank, but never admit, a record.
      const locationEvidence = phraseHits.length > 0 || tokenHits.length >= 2;
      if (!locationEvidence) continue;
      const organisationTokens = new Set(tenderTokens(record.organisation_chain));
      const authorityHits = [...routeAuthorityTokens].filter(
        (token) => organisationTokens.has(token));
      const rarity = tokenHits.reduce((sum, token) => {
        const frequency = frequencies.get(token) || records.length;
        return sum + Math.log((records.length + 1) / (frequency + 0.5));
      }, 0);
      const score = highwayHits.length * 100 + phraseHits.length * 30
        + rareHits.length * 16 + tokenHits.length * 8 + rarity + authorityHits.length * 3;
      scored.push({ record, score, token_hits: tokenHits, phrase_hits: phraseHits,
        rare_hits: rareHits, highway_hits: highwayHits, authority_hits: authorityHits });
    }
    scored.sort((left, right) => (right.score - left.score)
      || (right.phrase_hits.length - left.phrase_hits.length)
      || (right.token_hits.length - left.token_hits.length)
      || String(left.record.record_id).localeCompare(String(right.record.record_id)));
    return scored;
  }

  function roadAgreementAddressParts(address) {
    return String(address || "").split(",").slice(0, 4).map((part) =>
      tenderTokens(part).filter((token) => token.length >= 3
        && !/^\d{5,6}$/.test(token) && !ROAD_NOTICE_STOP.has(token)))
      .filter((tokens) => tokens.length);
  }

  function roadAgreementCandidates(records, address) {
    if (!Array.isArray(records) || !records.length) return [];
    const addressParts = roadAgreementAddressParts(address);
    const addressTokens = new Set(addressParts.flat());
    if (!addressTokens.size) return [];
    const districtTokensByRecord = records.map((record) => new Set(
      tenderTokens(record && record.district_name)
        .filter((token) => token.length >= 3 && !ROAD_NOTICE_STOP.has(token))));
    const roadTokensByRecord = records.map((record, index) => new Set(tenderTokens([
      record && record.title, record && record.road_from, record && record.road_to,
    ].filter(Boolean).join(" ")).filter((token) => token.length >= 3
      && !ROAD_NOTICE_STOP.has(token) && !districtTokensByRecord[index].has(token))));
    const frequencies = new Map();
    for (const token of addressTokens) {
      frequencies.set(token, roadTokensByRecord.reduce(
        (count, tokens) => count + (tokens.has(token) ? 1 : 0), 0));
    }
    const scored = [];
    for (let index = 0; index < records.length; index++) {
      const record = records[index];
      if (!record || record.lifecycle !== "current_project"
          || record.lifecycle_status !== "In Progress" || record.scope_verified !== true
          || record.segment_verified !== false || record.contractor !== null
          || record.contractor_assignment_verified !== false || record.dlp_verified !== false) {
        continue;
      }
      const roadTokens = roadTokensByRecord[index];
      const roadHits = [...addressTokens].filter((token) => roadTokens.has(token));
      const districtTokens = districtTokensByRecord[index];
      const districtHits = [...addressTokens].filter((token) => districtTokens.has(token));
      const normalisedRoad = tenderTokens([
        record.title, record.road_from, record.road_to,
      ].filter(Boolean).join(" ")).join(" ");
      const phraseHits = addressParts.filter((part) => {
        const phrase = part.join(" ");
        return part.some((token) => !districtTokens.has(token))
          && phrase.length >= 6 && normalisedRoad.includes(phrase);
      });
      const multiTokenPhrase = phraseHits.some((part) => part.length >= 2);
      const uniqueLongHits = roadHits.filter((token) => token.length >= 6
        && frequencies.get(token) === 1);
      // The source has no geometry. A State match or district name alone is never enough:
      // require an exact multi-word road phrase, two road-name words, or a unique long
      // road word corroborated by the district in the reverse-geocoded address.
      const strongLocationEvidence = multiTokenPhrase || roadHits.length >= 2
        || (uniqueLongHits.length > 0 && districtHits.length > 0);
      if (!strongLocationEvidence) continue;
      const rarity = roadHits.reduce((sum, token) => {
        const frequency = frequencies.get(token) || records.length;
        return sum + Math.log((records.length + 1) / (frequency + 0.5));
      }, 0);
      const score = (multiTokenPhrase ? 80 : 0) + phraseHits.length * 20
        + roadHits.length * 16 + uniqueLongHits.length * 12
        + districtHits.length * 10 + rarity;
      scored.push({ record, score, road_hits: roadHits, district_hits: districtHits,
        phrase_hits: phraseHits, unique_long_hits: uniqueLongHits });
    }
    scored.sort((left, right) => (right.score - left.score)
      || (right.phrase_hits.length - left.phrase_hits.length)
      || (right.road_hits.length - left.road_hits.length)
      || String(left.record.record_id).localeCompare(String(right.record.record_id)));
    return scored;
  }

  async function matchRoadAgreement(address, route) {
    const stateCode = route && route.contract_state_code;
    if (!route || route.routed !== true || !stateCode
        || (route.issue_type && route.issue_type !== "road_damage")) return null;
    const pack = await loadRoadAgreementPack(stateCode);
    const ranked = roadAgreementCandidates(pack && pack.agreements, address);
    if (!ranked.length) return null;
    const best = ranked[0], second = ranked[1];
    // Two equally supported road records cannot be disambiguated without geometry.
    if (second && Math.abs(best.score - second.score) < 8
        && best.phrase_hits.length === second.phrase_hits.length
        && best.road_hits.length === second.road_hits.length
        && best.district_hits.length === second.district_hits.length) return null;
    const record = best.record;
    const agreement = record.agreement_verified && record.agreement_number
      && record.agreement_date
      ? `; agreement ${record.agreement_number} dated ${record.agreement_date}` : "";
    const evidence = [...new Set([
      ...best.phrase_hits.map((part) => part.join(" ")),
      ...best.road_hits, ...best.district_hits,
    ])];
    return {
      tender_number: `${record.reference_value}${agreement}`,
      reference_label: agreement ? "PMGSY package / agreement" : record.reference_label,
      contractor: null,
      title: record.title,
      published: null,
      organisation: [record.agency, record.district_name].filter(Boolean).join(" — ") || null,
      detail_url: record.source_url,
      bid_closing: null,
      bid_opening: null,
      project_start: null,
      project_completion: null,
      agreement_number: record.agreement_number || null,
      agreement_date: record.agreement_date || null,
      package_reference: record.package_number || record.reference_value,
      highway_reference: null,
      published_chainage: null,
      road_from: record.road_from || null,
      road_to: record.road_to || null,
      source_name: record.source_name,
      source_url: record.source_url,
      lifecycle: "current_project",
      lifecycle_status: `Source-reported In Progress as retrieved ${record.retrieved_at}; `
        + "not independently freshness-verified",
      match_basis: `State/UT ${stateCode}; title/from/to/district evidence ${evidence.join(", ")}`,
      candidate_status: "candidate",
      scope_status: "official_road_record",
      scope_verified: true,
      segment_status: "unverified_title_match_no_geometry",
      segment_verified: false,
      // An agreement number/date does not identify a contractor assignment in this feed.
      agreement_verified: record.agreement_verified === true,
      award_status: "unverified_contractor_assignment",
      award_verified: false,
      dlp_status: "unverified_no_maintenance_dates",
      dlp_verified: false,
      note: `Unverified research lead (not included in the complaint): PMGSY road record `
        + `${record.reference_value}${agreement}. No geometry, contractor assignment, `
        + "completion, maintenance or DLP is asserted.",
      ...roadAgreementPackProvenance(stateCode),
    };
  }

  async function matchRoadNotice(address, route) {
    const stateCode = route && route.contract_state_code;
    if (!route || route.routed !== true || !stateCode
        || (route.issue_type && route.issue_type !== "road_damage")) return null;
    const pack = await loadRoadNoticePack(stateCode);
    const ranked = roadNoticeCandidates(pack && pack.notices, address, route);
    if (!ranked.length) return null;
    const best = ranked[0];
    if (!candidateLeadIsUnambiguous(ranked, 12)) return null;
    const record = best.record;
    const source = (pack.sources || []).find((item) => item.source_id === record.source_id);
    const locationEvidence = [...new Set([...best.phrase_hits.map((part) => part.join(" ")),
      ...best.token_hits])];
    const reference = record.tender_reference === record.tender_id
      ? record.tender_id : `${record.tender_reference} [${record.tender_id}]`;
    return {
      tender_number: reference,
      reference_label: record.tender_reference === record.tender_id
        ? "Tender ID" : "Tender reference / ID",
      contractor: null,
      title: record.title,
      published: record.published_at,
      organisation: record.organisation_chain,
      detail_url: record.source_url,
      bid_closing: record.closing_at,
      bid_opening: record.opening_at,
      project_start: null,
      project_completion: null,
      agreement_number: null,
      agreement_date: null,
      package_reference: null,
      highway_reference: best.highway_hits.length ? best.highway_hits.join(" / ") : null,
      published_chainage: null,
      source_name: source ? source.source_name : "Official State/UT e-Procurement portal",
      // Some official portal detail links contain session-shaped tokens and can expire.
      // Cite the stable official portal root plus the tender reference/ID above; keep
      // the exact captured detail URL inside the immutable pack for audit/fresh lookup.
      source_url: source ? source.source_url : record.source_url,
      lifecycle: "procurement_notice",
      lifecycle_status: `Open procurement notice; bid closing ${record.closing_at}`,
      match_basis: `State/UT ${stateCode}`
        + (best.highway_hits.length ? `; mapped ${best.highway_hits.join(" / ")}` : "")
        + (locationEvidence.length ? `; title/address ${locationEvidence.join(", ")}` : ""),
      candidate_status: "candidate",
      scope_status: "carriageway_scope_present",
      scope_verified: true,
      segment_status: "unverified_title_match",
      segment_verified: false,
      award_status: "unverified_procurement_notice",
      award_verified: false,
      dlp_status: "unverified",
      dlp_verified: false,
      note: `Unverified research lead (not included in the complaint): open procurement `
        + `notice ${record.tender_id}; no award or contractor is asserted.`,
      ...roadNoticePackProvenance(stateCode),
    };
  }

  function canSearchTenderCatalog(route) {
    if (!route || route.routed !== true
        || (route.issue_type && route.issue_type !== "road_damage")) return false;
    return route.tender_eligible === true
      || /^[A-Z]{2}$/.test(String(route.contract_state_code || ""));
  }

  function optionalCatalogResult(promise) {
    let timer = null;
    const timeout = new Promise((resolve) => {
      timer = setTimeout(() => resolve(null), OPTIONAL_CATALOG_TIMEOUT_MS);
    });
    return Promise.race([Promise.resolve(promise).catch(() => null), timeout])
      .finally(() => clearTimeout(timer));
  }

  function startLowerCatalogMatches(address, route) {
    // Start both downloads immediately. Awaiting the preferred PMGSY answer first keeps
    // deterministic result priority without paying two serial network deadlines.
    return {
      agreement: optionalCatalogResult(matchRoadAgreement(address, route)),
      notice: optionalCatalogResult(matchRoadNotice(address, route)),
    };
  }

  async function preferredLowerCatalogMatch(matches) {
    const agreement = await matches.agreement;
    return agreement || await matches.notice;
  }

  async function matchTenderForRoute(address, route, lgd = null) {
    if (!canSearchTenderCatalog(route)) return null;
    const lower = startLowerCatalogMatches(address, route);
    const highwayP = route.region === "national-highway"
      ? optionalCatalogResult(matchHighwayContract(address, route)) : Promise.resolve(null);
    const karnatakaP = lgd
      && (route.routing_pack_state_code === "KA" || route.contract_state_code === "KA")
      ? optionalCatalogResult(matchTender(address, lgd)) : Promise.resolve(null);
    const highway = await highwayP;
    if (highway) return highway;
    const karnataka = await karnatakaP;
    if (karnataka) return karnataka;
    return preferredLowerCatalogMatch(lower);
  }

  async function matchTenderAt(address, route, lat, lng, provisional = null) {
    if (!canSearchTenderCatalog(route)) return null;
    const lower = startLowerCatalogMatches(address, route);
    const highwayP = route.region === "national-highway"
      ? optionalCatalogResult(matchHighwayContract(address, route)) : Promise.resolve(null);
    const karnatakaP = provisional ? optionalCatalogResult(provisional)
      : (route.routing_pack_state_code === "KA" || route.contract_state_code === "KA")
        ? optionalCatalogResult((async () => {
        const where = await jurisdictionOf(lat, lng);
        return where && where.kind === "town" && where.lgd
          ? matchTender(address, where.lgd) : null;
      })()) : Promise.resolve(null);
    const highway = await highwayP;
    if (highway) return highway;
    const karnataka = await karnatakaP;
    if (karnataka) return karnataka;
    return preferredLowerCatalogMatch(lower);
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

  function complaintFooter(lang, roadDamage = true) {
    const road = {
      kn: "Pothole Reporter ಒಂದು ಸ್ವತಂತ್ರ ಆ್ಯಪ್. ಸೂಚಿಸಲಾದ ಸಂಸ್ಥೆ, ವಾರ್ಡ್, ರಸ್ತೆ ಮಾಲೀಕತ್ವ ಮತ್ತು ಯಾವುದೇ ಟೆಂಡರ್ ವಿವರಗಳನ್ನು ದಯವಿಟ್ಟು ಪರಿಶೀಲಿಸಿ.",
      mr: "Pothole Reporter हे स्वतंत्र अॅप आहे. सुचवलेली संस्था, विभाग, रस्त्याची मालकी आणि कोणतेही निविदा तपशील कृपया पडताळा.",
      bn: "Pothole Reporter একটি স্বাধীন অ্যাপ। প্রস্তাবিত কর্তৃপক্ষ, ওয়ার্ড, রাস্তার মালিকানা এবং টেন্ডারের তথ্য অনুগ্রহ করে যাচাই করুন।",
      en: "Pothole Reporter is an independent app. Please verify any suggested authority, ward, road ownership, and tender details.",
    };
    const civic = {
      kn: "Pothole Reporter ಒಂದು ಸ್ವತಂತ್ರ ಆ್ಯಪ್. ಸೂಚಿಸಲಾದ ಸಂಸ್ಥೆ, ನಾಗರಿಕ ವ್ಯಾಪ್ತಿ ಮತ್ತು ದೂರು ವರ್ಗವನ್ನು ದಯವಿಟ್ಟು ಪರಿಶೀಲಿಸಿ.",
      mr: "Pothole Reporter हे स्वतंत्र अॅप आहे. सुचवलेली संस्था, नागरी कार्यक्षेत्र आणि तक्रारीचा प्रकार कृपया पडताळा.",
      bn: "Pothole Reporter একটি স্বাধীন অ্যাপ। প্রস্তাবিত কর্তৃপক্ষ, নাগরিক এলাকার দায়িত্ব এবং অভিযোগের ধরন অনুগ্রহ করে যাচাই করুন।",
      en: "Pothole Reporter is an independent app. Please verify any suggested authority, civic jurisdiction, and complaint category.",
    };
    const copy = roadDamage ? road : civic;
    return copy[lang] || copy.en;
  }

  function conciseRouteLabel(value) {
    return String(value || "").replace(/\s*\((?:verify|select)[^)]*\)/ig, "").trim();
  }

  const COMPLAINT_TEMPLATE_VERSION = 4;
  const OUTBOUND_CONTRACT_IDENTITY_FIELDS = Object.freeze([
    "tender_number", "exact_work_name", "organisation_department", "listed_contractor",
    "publication_date", "tender_project_status", "bid_closing", "bid_opening",
    "project_start", "likely_completion", "agreement_number", "agreement_date",
    "package_project_reference", "highway_reference", "published_package_chainage",
    "road_from", "road_to", "candidate_match_basis", "contract_match_basis",
    "contract_source_name", "contract_source_url", "official_tender_detail_url",
    "carriageway_scope", "road_segment_match", "award_work_order_status", "dlp_status",
    "contract_candidate_status",
  ]);
  const NO_VERIFIED_CONTRACT =
    "No verified exact-road public contract found; tender and contractor omitted.";

  function storedComplaintLanguage(body) {
    const text = String(body || "");
    const first = text.trimStart();
    if (first.startsWith("ಮಾನ್ಯ ")) return "kn";
    if (first.startsWith("प्रति ")) return "mr";
    if (first.startsWith("মাননীয় ")) return "bn";
    if (first.startsWith("Dear ")) return "en";
    const labelledLine = (labels) => labels.some((label) =>
      new RegExp(`(?:^|\\n)${label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`).test(text));
    if (labelledLine(["ಸ್ಥಳ:", "ನಿರ್ದೇಶಾಂಕಗಳು:", "ಹಾನಿಯ ಪ್ರಕಾರ:"])) return "kn";
    if (labelledLine(["ठिकाण:", "निर्देशांक:", "नुकसानीचा प्रकार:"])) return "mr";
    if (labelledLine(["স্থান:", "স্থানাঙ্ক:", "ক্ষতির ধরন:"])) return "bn";
    return "en";
  }

  function complaintBodyWithFooter(body, issueType) {
    const text = String(body || "").trim();
    if (!text) return text;
    const roadDamage = normaliseIssueType(issueType) === "road_damage";
    const lang = storedComplaintLanguage(text);
    const footers = ["kn", "mr", "bn", "en"].map((code) => complaintFooter(code, roadDamage));
    const paragraphs = text.split(/\n{2,}/).map((p) => p.trim())
      .filter((paragraph) => paragraph && !footers.includes(paragraph));
    paragraphs.push(complaintFooter(lang, roadDamage));
    return paragraphs.join("\n\n");
  }

  // v1.31 and earlier put routing caveats into every complaint paragraph. IndexedDB
  // survives an app update, so cleaning only the generator would leave existing drafts
  // unchanged. Migrate only unsent app-template text, paragraph by exact paragraph: this
  // preserves anything the user added or rewrote and never rewrites a sent complaint.
  function migrateLegacyComplaintRecord(rec) {
    if (!rec || !["draft", "queued"].includes(rec.status)
        || Number(rec.complaint_template_version) >= COMPLAINT_TEMPLATE_VERSION
        || !String(rec.email_body || "").trim()) return rec;

    const lang = storedComplaintLanguage(rec.email_body);
    const roadDamage = normaliseIssueType(rec.issue_type) === "road_damage";
    let paragraphs = String(rec.email_body).split(/\n{2,}/).map((p) => p.trim()).filter(Boolean);
    let recognised = false;
    const replaceExact = (before, after) => {
      if (!before) return;
      const index = paragraphs.indexOf(before);
      if (index < 0) return;
      recognised = true;
      paragraphs[index] = after || "";
    };

    // v3 printed title/locality candidates—and sometimes a third-party contractor name—
    // into outward copy even while segment, award and DLP were unverified. IndexedDB
    // survives app upgrades, so remove that entire generated allegation from every unsent
    // draft. Candidate metadata may remain on the local report for research/audit.
    const candidateBlock = paragraphs.findIndex((paragraph) =>
      /^CONTRACT CANDIDATE(?:\n|$)/.test(paragraph));
    if (candidateBlock >= 0) {
      paragraphs[candidateBlock] = `CONTRACT VERIFICATION\nStatus: ${NO_VERIFIED_CONTRACT}`;
      recognised = true;
    }
    const legacyTenderNumber = String(rec.tender_number || "").trim();
    const legacyTenderTitle = String(rec.tender_title || "").trim();
    if (legacyTenderNumber && legacyTenderTitle) {
      const legacyTenderBlock = paragraphs.findIndex((paragraph) =>
        paragraph.includes(legacyTenderNumber) && paragraph.includes(legacyTenderTitle));
      if (legacyTenderBlock >= 0) {
        paragraphs[legacyTenderBlock] = `CONTRACT VERIFICATION\nStatus: ${NO_VERIFIED_CONTRACT}`;
        recognised = true;
      }
    }

    if (roadDamage) {
      const oldRequest = {
        kn: "ಫೋಟೋ ಲಗತ್ತಿಸಲಾಗಿದೆ. ಈ ರಸ್ತೆ ಹಾನಿ ದ್ವಿಚಕ್ರ ವಾಹನ ಸವಾರರಿಗೆ ಮತ್ತು ಇತರ ರಸ್ತೆ ಬಳಕೆದಾರರಿಗೆ ಅಪಾಯಕಾರಿ. ಇದನ್ನು ಶೀಘ್ರ ಪರಿಶೀಲಿಸಿ ದುರಸ್ತಿ ಮಾಡಬೇಕೆಂದು, ಮತ್ತು ಈ ರಸ್ತೆ ಭಾಗ ನಿರ್ವಹಣಾ ವಾರಂಟಿ ಅಡಿಯಲ್ಲಿದ್ದರೆ ಜವಾಬ್ದಾರ ಗುತ್ತಿಗೆದಾರರಿಗೆ ವರ್ಗಾಯಿಸಬೇಕೆಂದು ವಿನಂತಿಸುತ್ತೇನೆ.",
        mr: "फोटो जोडला आहे. या नुकसानीमुळे दुचाकीस्वार आणि इतर रस्ता वापरणाऱ्यांना धोका होऊ शकतो. कृपया तपासणी करून लवकरात लवकर दुरुस्ती करावी आणि लागू असल्यास जबाबदार कंत्राटदाराकडे पाठवावे.",
        bn: "ছবি সংযুক্ত করা হল। রাস্তার এই ক্ষতি বিশেষ করে দু’চাকার যানচালক ও অন্যান্য পথ ব্যবহারকারীর জন্য বিপজ্জনক। অনুগ্রহ করে দ্রুত স্থানটি পরিদর্শন করে মেরামতের ব্যবস্থা করুন।",
        en: "PFA image. This road damage poses a danger to two wheeler riders and other road users. I request your office to inspect and repair it at the earliest, and to route it to the contractor responsible if this road section is still under a maintenance warranty.",
      };
      const newRequest = {
        kn: "ಫೋಟೋ ಲಗತ್ತಿಸಲಾಗಿದೆ. ಈ ರಸ್ತೆ ಹಾನಿ ದ್ವಿಚಕ್ರ ವಾಹನ ಸವಾರರಿಗೆ ಮತ್ತು ಇತರ ರಸ್ತೆ ಬಳಕೆದಾರರಿಗೆ ಅಪಾಯಕಾರಿ. ಇದನ್ನು ಶೀಘ್ರ ಪರಿಶೀಲಿಸಿ ದುರಸ್ತಿ ಮಾಡಬೇಕೆಂದು ವಿನಂತಿಸುತ್ತೇನೆ.",
        mr: "फोटो जोडला आहे. या नुकसानीमुळे दुचाकीस्वार आणि इतर रस्ता वापरणाऱ्यांना धोका होऊ शकतो. कृपया तपासणी करून लवकरात लवकर दुरुस्ती करावी.",
        bn: oldRequest.bn,
        en: "PFA image. This road damage poses a danger to two-wheeler riders and other road users. I request your office to inspect and repair it at the earliest.",
      };
      replaceExact(oldRequest[lang], newRequest[lang]);

      const ward = rec.ward_code;
      if (ward) {
        const wardAuthority = rec.authority_id === "wb-kmc" ? "KMC" : "BMC";
        const oldWard = {
          kn: `ಸೂಚಿಸಿದ ಬಿಎಂಸಿ ಆಡಳಿತ ವಾರ್ಡ್: ${ward}. ಇದು OpenStreetMap ಆಡಳಿತ ಗಡಿಯಿಂದ ಪಡೆದ ಸೂಚನೆ ಮಾತ್ರ; ಅಧಿಕೃತ BMC ಆ್ಯಪ್‌ನಲ್ಲಿ ಪರಿಶೀಲಿಸಿ.`,
          mr: `सुचवलेला BMC प्रशासकीय विभाग: ${ward}. हा OpenStreetMap प्रशासकीय सीमेवर आधारित अंदाज आहे; अधिकृत BMC अॅपमध्ये पडताळा करा.`,
          bn: `প্রস্তাবিত ${wardAuthority} প্রশাসনিক ওয়ার্ড: ${ward}। এটি OpenStreetMap-এর প্রশাসনিক সীমানা থেকে অনুমান করা; সরকারি পরিষেবায় যাচাই করুন।`,
          en: `Suggested BMC administrative ward: ${ward}. This is inferred from an OpenStreetMap administrative boundary; verify it in the official BMC app.`,
        };
        const newWard = {
          kn: `ಸೂಚಿಸಿದ BMC ಆಡಳಿತ ವಾರ್ಡ್: ${ward}.`,
          mr: `सुचवलेला BMC प्रशासकीय विभाग: ${ward}.`,
          bn: `প্রস্তাবিত ${wardAuthority} প্রশাসনিক ওয়ার্ড: ${ward}।`,
          en: `Suggested BMC administrative ward: ${ward}.`,
        };
        replaceExact(oldWard[lang], newWard[lang]);
      }

      if (rec.authority_id === "in-national-highway") {
        const highway = rec.highway_ref || "National Highway";
        const oldHighway = {
          kn: `ನಕ್ಷೆಯ ಪ್ರಕಾರ ರಸ್ತೆ ಉಲ್ಲೇಖ: ${highway}. ನಿರ್ವಹಣಾ ಸಂಸ್ಥೆಯನ್ನು ಈ ಆ್ಯಪ್ ದೃಢಪಡಿಸಿಲ್ಲ. ಸಾಕ್ಷ್ಯವನ್ನು ಪರಿಶೀಲಿಸಿ ರಾಜಮಾರ್ಗಯಾತ್ರಾ ಅಥವಾ 1033 ಮೂಲಕ ನೀವೇ ದೂರು ದಾಖಲಿಸಿ; ಅಗತ್ಯವಿದ್ದರೆ ಸರಿಯಾದ NHAI, NHIDCL, BRO ಅಥವಾ ರಾಜ್ಯ PWD ಘಟಕಕ್ಕೆ ವರ್ಗಾಯಿಸಲು ಕೇಳಿ.`,
          mr: `नकाशावरील रस्ता संदर्भ: ${highway}. देखभाल करणारी संस्था या अॅपने पडताळलेली नाही. पुरावा तपासून राजमार्गयात्रा किंवा 1033 द्वारे स्वतः तक्रार नोंदवा आणि आवश्यक असल्यास योग्य NHAI, NHIDCL, BRO किंवा राज्य PWD विभागाकडे पाठवण्याची विनंती करा.`,
          bn: `মানচিত্রে রাস্তার পরিচয়: ${highway}। রক্ষণাবেক্ষণকারী সংস্থা এই অ্যাপ যাচাই করেনি। প্রমাণ দেখে রাজমার্গযাত্রা বা ১০৩৩-এর মাধ্যমে নিজে অভিযোগ নথিভুক্ত করুন এবং প্রয়োজনে সঠিক NHAI, NHIDCL, BRO বা রাজ্য PWD দপ্তরে পাঠাতে বলুন।`,
          en: `Mapped road reference: ${highway}. This app has not verified the maintaining agency. Review the evidence and submit it yourself through Rajmargyatra or 1033; ask for transfer to the correct NHAI, NHIDCL, BRO or State PWD unit when necessary.`,
        };
        const newHighway = {
          kn: `ನಕ್ಷೆಯ ಪ್ರಕಾರ ರಸ್ತೆ ಉಲ್ಲೇಖ: ${highway}. ಸೂಚಿಸಿದ ದೂರು ಮಾರ್ಗ: ರಾಜಮಾರ್ಗಯಾತ್ರಾ ಅಥವಾ 1033.`,
          mr: `नकाशावरील रस्ता संदर्भ: ${highway}. सुचवलेला तक्रार मार्ग: राजमार्गयात्रा किंवा 1033.`,
          bn: `মানচিত্রে রাস্তার পরিচয়: ${highway}। প্রস্তাবিত অভিযোগের মাধ্যম: রাজমার্গযাত্রা বা ১০৩৩।`,
          en: `Mapped road reference: ${highway}. Suggested complaint channel: Rajmargyatra or 1033.`,
        };
        replaceExact(oldHighway[lang], newHighway[lang]);
      } else if (rec.authority_id === "wb-statewide-unverified") {
        const oldWestBengal = {
          kn: "ಸ್ಥಳವು ಪಿನ್ ಮಾಡಿದ OpenStreetMap ಪಶ್ಚಿಮ ಬಂಗಾಳ ಗಡಿಯೊಳಗೆ ನಕ್ಷೆಗೊಂಡಿದೆ; ಆದರೆ ಜವಾಬ್ದಾರ ಜಿಲ್ಲೆ, ಇಲಾಖೆ ಅಥವಾ ರಸ್ತೆ ಮಾಲೀಕರನ್ನು ಗುರುತಿಸಲಾಗಿಲ್ಲ. ಈ ಸ್ವತಂತ್ರ ಆ್ಯಪ್ ದೂರು ಸಲ್ಲಿಸುವುದಿಲ್ಲ; ಸಾಕ್ಷ್ಯವನ್ನು ಪರಿಶೀಲಿಸಿ West Bengal PGRS ನಲ್ಲಿ ಸರಿಯಾದ ಜಿಲ್ಲೆ ಅಥವಾ ಇಲಾಖೆಯನ್ನು ನೀವೇ ಆಯ್ದು ದೃಢಪಡಿಸಿ.",
          mr: "हे ठिकाण पिन केलेल्या OpenStreetMap पश्चिम बंगाल सीमेत नकाशित आहे; परंतु जबाबदार जिल्हा, विभाग किंवा रस्त्याचा मालक ओळखलेला नाही. हे स्वतंत्र अॅप तक्रार दाखल करत नाही; पुरावा तपासा आणि West Bengal PGRS मध्ये योग्य जिल्हा किंवा विभाग स्वतः निवडून पडताळा.",
          bn: "স্থানটি পিন-করা OpenStreetMap পশ্চিমবঙ্গ সীমানার ভিতরে মানচিত্রভুক্ত, কিন্তু দায়িত্বপ্রাপ্ত জেলা, দপ্তর বা রাস্তার মালিক চিহ্নিত করা হয়নি। এই স্বাধীন অ্যাপটি অভিযোগ জমা দেয় না; প্রমাণ যাচাই করে West Bengal PGRS-এ দায়িত্বপ্রাপ্ত জেলা বা দপ্তর নিজে নির্বাচন ও যাচাই করুন এবং অভিযোগ নম্বরটি সংরক্ষণ করুন।",
          en: "The location is mapped inside the pinned OpenStreetMap West Bengal boundary, but the responsible district, department and road owner have not been identified. This independent app does not submit the grievance; review the evidence, then select and verify the responsible district or department in West Bengal PGRS.",
        };
        const newWestBengal = {
          kn: "ಸೂಚಿಸಿದ ದೂರು ಮಾರ್ಗ: West Bengal PGRS; ಜವಾಬ್ದಾರ ಜಿಲ್ಲೆ ಅಥವಾ ಇಲಾಖೆಯನ್ನು ಗುರುತಿಸಲಾಗಿಲ್ಲ.",
          mr: "सुचवलेला तक्रार मार्ग: West Bengal PGRS; जबाबदार जिल्हा किंवा विभाग ओळखलेला नाही.",
          bn: "প্রস্তাবিত অভিযোগের মাধ্যম: West Bengal PGRS; দায়িত্বপ্রাপ্ত জেলা বা দপ্তর চিহ্নিত করা হয়নি।",
          en: "Suggested complaint channel: West Bengal PGRS; the responsible district or department has not been identified.",
        };
        replaceExact(oldWestBengal[lang], newWestBengal[lang]);
      } else if (rec.ownership_unverified) {
        const rawAuthority = rec.authority_name || ({ kn: "ಅಧಿಕಾರಿಯನ್ನು ಪರಿಶೀಲಿಸಿ", mr: "संस्था पडताळा",
          bn: "কর্তৃপক্ষ যাচাই করুন", en: "verify the authority" })[lang];
        const authority = conciseRouteLabel(rec.authority_name);
        const oldAuthorityName = lang === "bn" && rec.authority_id === "wb-kmc"
          ? "কলকাতা পৌরসংস্থা (KMC)" : rawAuthority;
        const authorityName = lang === "bn" && rec.authority_id === "wb-kmc"
          ? "কলকাতা পৌরসংস্থা (KMC)" : (authority || rawAuthority);
        const official = OFFICIAL_HANDOFF_CHANNELS.has(rec.delivery_channel);
        const oldAuthority = official
          ? {
              kn: `ಸೂಚಿಸಿದ ನಾಗರಿಕ ಸಂಸ್ಥೆ: ${rawAuthority}. ಇದು ರಸ್ತೆ ಮಾಲೀಕತ್ವದ ದೃಢೀಕರಣವಲ್ಲ. ಈ ಸ್ವತಂತ್ರ ಆ್ಯಪ್ ದೂರು ಸಲ್ಲಿಸುವುದಿಲ್ಲ; ಸಾಕ್ಷ್ಯವನ್ನು ಪರಿಶೀಲಿಸಿ ಮತ್ತು ${rec.handoff_name || "ಅಧಿಕೃತ ಸೇವೆ"} ಮೂಲಕ ನೀವೇ ಸಲ್ಲಿಸಿ.`,
              mr: `सुचवलेली नागरी संस्था: ${rawAuthority}. यावरून त्या रस्त्याची मालकी सिद्ध होत नाही. हे स्वतंत्र अॅप तक्रार दाखल करत नाही; पुरावा तपासा आणि ${rec.handoff_name || "अधिकृत सेवेत"} स्वतः नोंदवा.`,
              bn: `অবস্থানের ভিত্তিতে প্রস্তাবিত পৌর কর্তৃপক্ষ: ${oldAuthorityName}। এতে রাস্তার মালিকানা প্রমাণিত হয় না। এই স্বাধীন অ্যাপটি অভিযোগ জমা দেয় না; প্রমাণ যাচাই করে ${rec.handoff_name || "সরকারি পরিষেবা"}-এ নিজে অভিযোগ নথিভুক্ত করুন এবং অভিযোগ নম্বরটি সংরক্ষণ করুন।`,
              en: `Suggested civic authority: ${rawAuthority}. This does not prove who owns this road. This independent app does not submit a grievance; review the evidence and finish it yourself in ${rec.handoff_name || "the official service"}.`,
            }[lang]
          : {
              kn: `ಸ್ಥಳದ ಆಧಾರದ ಮೇಲೆ ಸೂಚಿಸಿದ ನಾಗರಿಕ ಸಂಸ್ಥೆ: ${rawAuthority}. ಇದು ರಸ್ತೆ ಮಾಲೀಕತ್ವದ ದೃಢೀಕರಣವಲ್ಲ; ಬೇರೆ ಸಂಸ್ಥೆ ಜವಾಬ್ದಾರಿಯಾಗಿದ್ದರೆ ದಯವಿಟ್ಟು ಈ ದೂರನ್ನು ಆ ಸಂಸ್ಥೆಗೆ ವರ್ಗಾಯಿಸಿ.`,
              mr: `स्थानावरून सुचवलेली नागरी संस्था: ${rawAuthority}. यावरून रस्त्याची मालकी सिद्ध होत नाही; दुसरी संस्था जबाबदार असल्यास कृपया तक्रार तिच्याकडे पाठवा.`,
              bn: `অবস্থানের ভিত্তিতে প্রস্তাবিত পৌর কর্তৃপক্ষ: ${oldAuthorityName}। এতে রাস্তার মালিকানা প্রমাণিত হয় না; অন্য কোনও সংস্থা দায়িত্বে থাকলে অভিযোগটি তাদের কাছে পাঠিয়ে দেওয়ার অনুরোধ রইল।`,
              en: `Suggested civic authority from the location: ${rawAuthority}. This does not prove road ownership; please forward this complaint if another agency owns the road.`,
            }[lang];
        const addressedAuthority = official
          || normaliseAuthorityValue(rec.officer_name) === normaliseAuthorityValue(authorityName);
        const newAuthority = addressedAuthority ? "" : ({
          kn: `ಸೂಚಿಸಿದ ನಾಗರಿಕ ಸಂಸ್ಥೆ: ${authorityName || "ತಿಳಿದಿಲ್ಲ"}.`,
          mr: `सुचवलेली नागरी संस्था: ${authorityName || "माहित नाही"}.`,
          bn: `প্রস্তাবিত পৌর কর্তৃপক্ষ: ${authorityName || "অজানা"}।`,
          en: `Suggested civic authority: ${authorityName || "unknown"}.`,
        })[lang];
        replaceExact(oldAuthority, newAuthority);
      }

      const oldTenderRequest = {
        kn: "ದೋಷ ಹೊಣೆಗಾರಿಕೆ ಅಥವಾ ನಿರ್ವಹಣಾ ಅವಧಿ ಜಾರಿಯಲ್ಲಿದ್ದರೆ, ಸಂಸ್ಥೆಗೆ ಹೆಚ್ಚುವರಿ ವೆಚ್ಚವಿಲ್ಲದೆ ಗುತ್ತಿಗೆದಾರರಿಂದಲೇ ದುರಸ್ತಿ ಮಾಡಿಸಬೇಕೆಂದು ವಿನಂತಿಸುತ್ತೇನೆ. ಇದು ಸಂಭಾವ್ಯ ದಾಖಲೆ ಹೊಂದಾಣಿಕೆ; ದಯವಿಟ್ಟು ಟೆಂಡರ್ ದಾಖಲೆಗಳೊಂದಿಗೆ ಪರಿಶೀಲಿಸಿ.",
        mr: "दोष दायित्व किंवा देखभाल कालावधी लागू असल्यास महानगरपालिकेला अतिरिक्त खर्च न लावता कंत्राटदाराकडून दुरुस्ती करून घ्यावी. ही संभाव्य नोंद-जुळणी आहे; कृपया मूळ निविदा कागदपत्रांशी पडताळा करा.",
        bn: "ত্রুটি-দায় বা রক্ষণাবেক্ষণের মেয়াদ চালু থাকলে পৌরসংস্থার অতিরিক্ত ব্যয় ছাড়াই ঠিকাদারের মাধ্যমে মেরামত করানোর অনুরোধ করছি। এটি কেবল সম্ভাব্য নথি-মিল; মূল টেন্ডার নথির সঙ্গে যাচাই করুন।",
        en: "If the defect liability or maintenance period is in force, I request that the repair be carried out by the contractor at no additional cost to the corporation. This is a probable record match; kindly verify against the tender documents.",
      };
      replaceExact(oldTenderRequest[lang], "");
    } else {
      const civicRequest = paragraphs.find((paragraph) => ({
        kn: ["ಲಗತ್ತಿಸಿದ ಚಿತ್ರದಲ್ಲಿ ತೆರೆದ ಅಥವಾ ಹಾನಿಗೊಂಡ ಮ್ಯಾನ್‌ಹೋಲ್ ಇದೆ.", "ಲಗತ್ತಿಸಿದ ಚಿತ್ರದಲ್ಲಿ ಈ ಸ್ಥಳದಲ್ಲಿ ಸಂಗ್ರಹವಾದ ಅಥವಾ ತೆರವುಗೊಳಿಸದ ಕಸ ಇದೆ."],
        mr: ["जोडलेल्या फोटोमध्ये उघडे किंवा खराब मॅनहोल दिसत आहे.", "जोडलेल्या फोटोमध्ये या ठिकाणी साचलेला किंवा न उचललेला कचरा दिसत आहे."],
        bn: ["সংযুক্ত ছবিতে একটি খোলা বা ক্ষতিগ্রস্ত ম্যানহোল দেখা যাচ্ছে।", "সংযুক্ত ছবিতে এই স্থানে জমে থাকা বা না-তোলা আবর্জনা দেখা যাচ্ছে।"],
        en: ["The attached photo shows an open or damaged manhole.", "The attached photo shows accumulated or uncollected garbage at this location."],
      })[lang].some((start) => paragraph.startsWith(start)));
      if (civicRequest) recognised = true;

      const importedOld = {
        kn: "ಈ ಚಿತ್ರವನ್ನು ಬಳಕೆದಾರರು ಆಯ್ಕೆಮಾಡಿ/ಆಮದು ಮಾಡಿದ್ದಾರೆ; ಅದು ಯಾವಾಗ ತೆಗೆದದ್ದು ಎಂಬುದನ್ನು ಆ್ಯಪ್ ಪರಿಶೀಲಿಸಿಲ್ಲ.",
        mr: "हे छायाचित्र वापरकर्त्याने निवडले/आयात केले आहे; ते केव्हा घेतले याची अॅपने पडताळणी केलेली नाही.",
        bn: "ছবিটি ব্যবহারকারী বেছে নিয়েছেন/আমদানি করেছেন; এটি কখন তোলা হয়েছিল অ্যাপ তা যাচাই করেনি।",
        en: "This photo was selected/imported by the user; the app has not verified when it was taken.",
      };
      const importedNew = {
        kn: "ಈ ಚಿತ್ರವನ್ನು ಬಳಕೆದಾರರು ಆಯ್ಕೆಮಾಡಿ/ಆಮದು ಮಾಡಿದ್ದಾರೆ; ಮೂಲ ಸೆರೆಹಿಡಿದ ಸಮಯ ತಿಳಿದಿಲ್ಲ.",
        mr: "हे छायाचित्र वापरकर्त्याने निवडले/आयात केले आहे; मूळ छायाचित्रणाची वेळ माहित नाही.",
        bn: "ছবিটি ব্যবহারকারী বেছে নিয়েছেন/আমদানি করেছেন; মূল ছবিটি তোলার সময় অজানা।",
        en: "This photo was selected/imported by the user; its original capture time is unknown.",
      };
      const importedIndex = paragraphs.findIndex((paragraph) => paragraph.startsWith(importedOld[lang]));
      if (importedIndex >= 0) {
        recognised = true;
        paragraphs[importedIndex] = importedNew[lang]
          + paragraphs[importedIndex].slice(importedOld[lang].length);
      }

      const authority = conciseRouteLabel(rec.authority_name);
      if (rec.authority_id === "wb-statewide-unverified") {
        const oldWestBengal = {
          kn: "ಸ್ಥಳವು ಪಶ್ಚಿಮ ಬಂಗಾಳದೊಳಗಿದೆ; ಜವಾಬ್ದಾರ ಸಂಸ್ಥೆಯನ್ನು ಗುರುತಿಸಲಾಗಿಲ್ಲ. West Bengal PGRS ನಲ್ಲಿ ಸರಿಯಾದ ಜಿಲ್ಲೆ ಅಥವಾ ಇಲಾಖೆಯನ್ನು ಆಯ್ದು ದೃಢಪಡಿಸಿ.",
          mr: "ठिकाण पश्चिम बंगालमध्ये आहे; जबाबदार संस्था ओळखलेली नाही. West Bengal PGRS मध्ये योग्य जिल्हा किंवा विभाग निवडून पडताळा.",
          bn: "স্থানটি পিন-করা OpenStreetMap পশ্চিমবঙ্গ সীমানার মধ্যে; দায়িত্বপ্রাপ্ত সংস্থা চিহ্নিত করা হয়নি। West Bengal PGRS-এ দায়িত্বপ্রাপ্ত জেলা বা দপ্তর নির্বাচন ও যাচাই করুন।",
          en: "The location is inside the pinned OpenStreetMap West Bengal boundary; the responsible authority has not been identified. Select and verify the responsible district or department in West Bengal PGRS.",
        };
        const newWestBengal = {
          kn: "ಸೂಚಿಸಿದ ದೂರು ಮಾರ್ಗ: West Bengal PGRS; ಜವಾಬ್ದಾರ ಜಿಲ್ಲೆ ಅಥವಾ ಇಲಾಖೆಯನ್ನು ಗುರುತಿಸಲಾಗಿಲ್ಲ.",
          mr: "सुचवलेला तक्रार मार्ग: West Bengal PGRS; जबाबदार जिल्हा किंवा विभाग ओळखलेला नाही.",
          bn: "প্রস্তাবিত অভিযোগের মাধ্যম: West Bengal PGRS; দায়িত্বপ্রাপ্ত জেলা বা দপ্তর চিহ্নিত করা হয়নি।",
          en: "Suggested complaint channel: West Bengal PGRS; the responsible district or department has not been identified.",
        };
        replaceExact(oldWestBengal[lang], newWestBengal[lang]);
      } else if (authority) {
        const oldAuthority = {
          kn: `ಸ್ಥಳದ ಆಧಾರದ ಮೇಲೆ ಸೂಚಿಸಿದ ನಾಗರಿಕ ಸಂಸ್ಥೆ: ${rec.authority_name}. ಅಧಿಕೃತ ಸೇವೆಯಲ್ಲಿ ಪರಿಶೀಲಿಸಿ.`,
          mr: `ठिकाणावरून सुचवलेली नागरी संस्था: ${rec.authority_name}. अधिकृत सेवेत पडताळा करा.`,
          bn: `অবস্থানের ভিত্তিতে প্রস্তাবিত পৌর কর্তৃপক্ষ: ${rec.authority_name}। সরকারি পরিষেবায় যাচাই করুন।`,
          en: `Suggested civic authority from the location: ${rec.authority_name}. Please verify it in the official service.`,
        }[lang];
        const addressedAuthority = OFFICIAL_HANDOFF_CHANNELS.has(rec.delivery_channel)
          || normaliseAuthorityValue(rec.officer_name) === normaliseAuthorityValue(authority);
        const newAuthority = addressedAuthority ? "" : ({
          kn: `ಸೂಚಿಸಿದ ನಾಗರಿಕ ಸಂಸ್ಥೆ: ${authority}.`, mr: `सुचवलेली नागरी संस्था: ${authority}.`,
          bn: `প্রস্তাবিত পৌর কর্তৃপক্ষ: ${authority}।`, en: `Suggested civic authority: ${authority}.`,
        })[lang];
        replaceExact(oldAuthority, newAuthority);
      }
    }

    const safeWhatsapp = String(rec.whatsapp_text || "").split("\n")
      .filter((line) => !/^Contract:/.test(line));
    if (safeWhatsapp.length !== String(rec.whatsapp_text || "").split("\n").length) {
      const footerIndex = safeWhatsapp.findIndex((line) => /Pothole Reporter/.test(line));
      safeWhatsapp.splice(footerIndex >= 0 ? footerIndex : safeWhatsapp.length, 0,
        `Contract verification: ${NO_VERIFIED_CONTRACT}`);
      recognised = true;
    }
    const safePortalFields = rec.portal_fields && typeof rec.portal_fields === "object"
      ? { ...rec.portal_fields } : null;
    if (safePortalFields) {
      const hadContractIdentity = OUTBOUND_CONTRACT_IDENTITY_FIELDS.some((field) =>
        Object.prototype.hasOwnProperty.call(safePortalFields, field));
      for (const field of OUTBOUND_CONTRACT_IDENTITY_FIELDS) delete safePortalFields[field];
      safePortalFields.contract_verification_status = NO_VERIFIED_CONTRACT;
      if (hadContractIdentity) recognised = true;
    }

    if (!recognised) return rec;

    // Older evidence exports appended a second disclaimer block after the signature.
    // It is generated metadata, not reporter prose; the current evidence exporter adds
    // one compact Evidence line when sharing, so retaining this block would duplicate it.
    const oldEvidenceTruth = [
      "Prepared by an independent app; no official grievance submission is confirmed.",
      "Prepared by an independent app; email delivery is not confirmed.",
      "Locally marked submitted by the user; this app has not independently verified delivery.",
    ];
    const oldEvidenceLine = /^(?:Captured|Report created|Selected photo file date) \(IST\):|^Photo provenance:|^GPS accuracy:|^Suggested civic authority:|^Suggested BMC ward:|^Mapped road reference:|^Local event ID:/;
    paragraphs = paragraphs.map((paragraph) => {
      if (!oldEvidenceTruth.some((line) => paragraph.includes(line))) return paragraph;
      return paragraph.split("\n").filter((line) =>
        !oldEvidenceLine.test(line)
        && !oldEvidenceTruth.some((truth) => line.includes(truth))).join("\n").trim();
    }).filter(Boolean);

    const official = OFFICIAL_HANDOFF_CHANNELS.has(rec.delivery_channel);
    const oldOfficer = rec.officer_name || ({ kn: "ಅಧಿಕಾರಿಗಳೇ", mr: "संबंधित अधिकारी",
      bn: "সংশ্লিষ্ট আধিকারিক", en: "Sir or Madam" })[lang];
    const authority = conciseRouteLabel(rec.authority_name);
    const letterAuthority = lang === "bn" && rec.authority_id === "wb-kmc"
      ? "কলকাতা পৌরসংস্থা (KMC)" : authority;
    const newOfficer = official ? letterAuthority : conciseRouteLabel(rec.officer_name);
    const oldGreeting = ({ kn: `ಮಾನ್ಯ ${oldOfficer} ಅವರಿಗೆ,`, mr: `प्रति ${oldOfficer},`,
      bn: `মাননীয় ${oldOfficer},`, en: `Dear ${oldOfficer},` })[lang];
    const newGreeting = ({ kn: `ಮಾನ್ಯ ${newOfficer || "ಅಧಿಕಾರಿಗಳೇ"} ಅವರಿಗೆ,`,
      mr: `प्रति ${newOfficer || "संबंधित अधिकारी"},`,
      bn: `মাননীয় ${newOfficer || "সংশ্লিষ্ট আধিকারিক"},`,
      en: `Dear ${newOfficer || "Sir or Madam"},` })[lang];
    replaceExact(oldGreeting, newGreeting);

    return {
      ...rec,
      email_body: complaintBodyWithFooter(paragraphs.join("\n\n"), rec.issue_type),
      whatsapp_text: rec.whatsapp_text ? safeWhatsapp.join("\n") : rec.whatsapp_text,
      portal_fields: safePortalFields || rec.portal_fields,
      portal_copy_text: safePortalFields
        ? Object.entries(safePortalFields)
          .map(([key, value]) => `${key.replace(/_/g, " ")}: ${value}`).join("\n")
        : rec.portal_copy_text,
      complaint_template_version: COMPLAINT_TEMPLATE_VERSION,
    };
  }

  function normaliseTenderMatch(tender, route = null) {
    if (!tender || !String(tender.tender_number || "").trim()
        || !String(tender.title || "").trim()) return null;
    const karnataka = tender.tender_pack_id === "in-ka-tenders"
      && tender.tender_pack_state_code === "KA"
      && route && route.routing_pack_state_code === "KA";
    const highway = /^in-nh-contracts-[a-z]{2}$/.test(String(tender.tender_pack_id || ""))
      && route && route.region === "national-highway"
      && tender.tender_pack_state_code === route.contract_state_code;
    const roadNotice = /^in-road-notices-[a-z]{2}$/.test(String(tender.tender_pack_id || ""))
      && route && route.routed === true
      && (!route.issue_type || route.issue_type === "road_damage")
      && tender.tender_pack_state_code === route.contract_state_code;
    const roadAgreement = /^in-road-agreements-[a-z]{2}$/.test(
      String(tender.tender_pack_id || ""))
      && route && route.routed === true
      && (!route.issue_type || route.issue_type === "road_damage")
      && tender.tender_pack_state_code === route.contract_state_code;
    const allowedRoute = (roadNotice || roadAgreement)
      ? canSearchTenderCatalog(route) : route && route.tender_eligible === true;
    const scopeEligible = roadAgreement
      ? tender.scope_verified === true : tenderCoversCarriageway(tender.title, tender.tender_number);
    if (!route || !allowedRoute || (!karnataka && !highway && !roadNotice && !roadAgreement)
        || !Number.isInteger(tender.tender_pack_version)
        || !/^[0-9a-f]{64}$/.test(String(tender.tender_pack_sha256 || ""))
        || !scopeEligible) return null;
    const compact = roadAgreement ? {
      candidate_status: "candidate",
      scope_status: tender.scope_status || "official_road_record",
      scope_verified: true,
      segment_status: tender.segment_status || "unverified_title_match_no_geometry",
      segment_verified: false,
      award_status: "unverified_contractor_assignment",
      award_verified: false,
      dlp_status: "unverified_no_maintenance_dates",
      dlp_verified: false,
    } : contractVerificationFor(tender);
    return {
      ...tender,
      tender_number: String(tender.tender_number).trim(),
      reference_label: tender.reference_label || "Tender number",
      title: String(tender.title).replace(/\s+/g, " ").trim(),
      source_name: tender.source_name || (karnataka
        ? "Karnataka Public Procurement Portal (KPPP) snapshot"
        : roadNotice ? "Official State/UT e-Procurement portal"
          : roadAgreement ? "PMGSY dashboard road tender/agreement details"
            : "Official highway project source"),
      source_url: tender.source_url || (karnataka ? "https://kppp.karnataka.gov.in/" : null),
      ...compact,
      segment_status: tender.segment_verified === true ? "verified" : (tender.segment_status || "unverified"),
      segment_verified: tender.segment_verified === true,
      award_status: tender.award_verified === true && tender.contractor
        ? (tender.award_status || "verified_by_source_record") : "unverified",
      award_verified: tender.award_verified === true && !!tender.contractor,
      dlp_status: tender.dlp_verified === true ? (tender.dlp_status || "verified") : "unverified",
      dlp_verified: tender.dlp_verified === true,
    };
  }

  function officialIndianPublicRecordUrl(value) {
    try {
      const url = new URL(String(value || ""));
      const host = url.hostname.toLowerCase();
      return url.protocol === "https:"
        && (host === "gov.in" || host.endsWith(".gov.in")
          || host === "nic.in" || host.endsWith(".nic.in"));
    } catch (_) { return false; }
  }

  // A title/locality candidate is useful research, but is not an allegation of legal
  // responsibility. Outbound copy may name a contractor only when independent official
  // evidence closes every link from this GPS point to an active obligation.
  function verifiedContractForComplaint(tender, route = null, capturedAt = null) {
    const match = normaliseTenderMatch(tender, route);
    if (!match || match.scope_verified !== true || match.segment_verified !== true
        || match.award_verified !== true || match.dlp_verified !== true
        || match.responsibility_active_verified !== true || match.unambiguous !== true
        || !String(match.contractor || "").trim()
        || !officialIndianPublicRecordUrl(match.source_url)
        || !Number.isInteger(match.tender_pack_version)
        || !/^[0-9a-f]{64}$/.test(String(match.tender_pack_sha256 || ""))) return null;

    const separated = separateRoadResponsibility(route);
    const routeOwnerId = String(separated && separated.road_owner_id || "").trim();
    const contractOwnerId = String(match.road_owner_id || match.responsible_authority_id || "").trim();
    const ownerEvidence = separated && separated.road_owner_evidence;
    if (!routeOwnerId || separated.road_owner_status !== "verified"
        || contractOwnerId !== routeOwnerId || !ownerEvidence
        || !officialIndianPublicRecordUrl(ownerEvidence.source_url)) return null;

    const proof = match.verification_evidence;
    const proofItem = (name) => {
      const item = proof && proof[name];
      return !!(item && String(item.reference || item.document_id || "").trim()
        && officialIndianPublicRecordUrl(item.source_url));
    };
    if (!proofItem("segment") || !proofItem("award") || !proofItem("responsibility")) return null;

    const observed = Number(capturedAt);
    const validFrom = Date.parse(String(match.responsibility_valid_from || ""));
    const validUntil = Date.parse(String(match.responsibility_valid_until || ""));
    if (!Number.isFinite(observed) || !Number.isFinite(validFrom) || !Number.isFinite(validUntil)
        || observed * 1000 < validFrom || observed * 1000 > validUntil) return null;
    return match;
  }

  const SURFACE_LABELS = Object.freeze({
    bituminous_asphalt: "Bituminous / asphalt",
    cement_concrete: "Cement concrete",
    mastic_asphalt: "Mastic asphalt",
    paver_blocks: "Paver blocks",
    temporary_drivable_surface: "Temporary traffic surface",
    unpaved_or_nonroad: "Unpaved / non-road",
    unknown: "Unknown",
  });

  function complaintRoutingBlock(route) {
    const separated = separateRoadResponsibility(route);
    const profile = authorityComplaintProfile(separated);
    const bda = profile.profile_id === "ka-bengaluru-bda";
    const intakeName = bda ? profile.authority_name
      : conciseRouteLabel(separated.intake_authority_name || separated.authority_name);
    const intakeId = bda ? profile.profile_id
      : (separated.intake_authority_id || separated.authority_id || null);
    const geographicName = conciseRouteLabel(separated.geographic_authority_name
      || separated.authority_name) || "Unknown";
    const ownerVerified = separated.road_owner_status === "verified";
    const ownerName = ownerVerified && separated.road_owner_name
      ? separated.road_owner_name : "Unknown — authority to inspect and transfer if required";
    const clue = [separated.routing_source,
      separated.routing_match_field && separated.routing_match_value
        ? `${separated.routing_match_field}=${separated.routing_match_value}` : null]
      .filter(Boolean).join("; ") || "Not recorded";
    return { route: separated, profile, intakeName, intakeId, geographicName,
      ownerVerified, ownerName, clue };
  }

  function assertComplaintInvariants(lat, lng, route) {
    if (!Number.isFinite(lat) || !Number.isFinite(lng)
        || Math.abs(lat) > 90 || Math.abs(lng) > 180) {
      throw new Error("A road complaint requires valid coordinates.");
    }
    if (!route || route.routed !== true || !route.authority_id
        || !route.authority_name || !route.routing_source) {
      throw new Error("A road complaint requires a verified intake route.");
    }
  }

  function buildComplaintOutputs(a, lat, lng, address, officerName, tender, route = null,
                                  evidence = {}) {
    void officerName; // Authority profiles, not honorifics, define the technical output.
    assertComplaintInvariants(lat, lng, route);
    const routing = complaintRoutingBlock(route);
    const tenderMatch = verifiedContractForComplaint(
      tender, routing.route, Number(evidence.captured_at));
    const la = Number(lat).toFixed(6), ln = Number(lng).toFixed(6);
    const coordinates = `${la}, ${ln}`;
    const mapUrl = `https://maps.google.com/?q=${la},${ln}`;
    const location = String(address || "Address unavailable; use coordinates").trim();
    const road = address ? address.split(",")[0].trim() : "the reported location";
    const size = POTHOLE_SIZES.has(a && a.size) ? a.size : "unknown";
    const surface = SURFACE_LABELS[a && a.surface_type] || SURFACE_LABELS.unknown;
    const measurementProvenance = a && a.measurement_provenance === "field_measured"
      ? "Field measured" : "Visual estimate without a scale reference";
    const measurementConfidence = a && a.measurement_confidence === "high" ? "High"
      : a && a.measurement_confidence === "medium" ? "Medium" : "Low";
    const ward = routing.route.ward_code || "Not identified";
    const profileCategory = routing.profile.portal_category || "Road / Pothole";
    const profileChannel = routing.profile.portal_name || routing.route.handoff_name
      || (routing.route.delivery_channel === "email" ? "Email" : "Official grievance service");
    const captured = Number.isFinite(evidence.captured_at)
      ? new Date(evidence.captured_at * 1000).toISOString() : "Not recorded";
    const gpsAccuracy = Number.isFinite(evidence.gps_accuracy)
      ? `±${Math.round(evidence.gps_accuracy)} m` : "Not recorded";
    const photoProvenance = evidence.photo_provenance || "Photo attached from Pothole Reporter";

    const tenderFields = tenderMatch ? {
      status: "Verified exact-road contract and active responsibility",
      reference_label: tenderMatch.reference_label || "Tender number",
      tender_number: tenderMatch.tender_number,
      exact_work_name: tenderMatch.title,
      organisation: tenderMatch.organisation || "Not listed",
      listed_contractor: tenderMatch.contractor || "Not listed",
      publication_date: tenderMatch.published || "Not listed",
      lifecycle_status: tenderMatch.lifecycle_status || "Not listed",
      bid_closing: tenderMatch.bid_closing || "Not listed",
      bid_opening: tenderMatch.bid_opening || "Not listed",
      project_start: tenderMatch.project_start || "Not listed",
      project_completion: tenderMatch.project_completion || "Not listed",
      agreement_number: tenderMatch.agreement_number || "Not listed",
      agreement_date: tenderMatch.agreement_date || "Not listed",
      package_reference: tenderMatch.package_reference || "Not listed",
      highway_reference: tenderMatch.highway_reference || "Not listed",
      published_chainage: tenderMatch.published_chainage || "Not listed",
      road_from: tenderMatch.road_from || "Not listed",
      road_to: tenderMatch.road_to || "Not listed",
      match_basis: tenderMatch.match_basis || "Not listed",
      source_name: tenderMatch.source_name,
      source_url: tenderMatch.source_url,
      detail_url: tenderMatch.detail_url || "Not listed",
      scope: "Verified carriageway work",
      segment_match: "Verified exact GPS segment",
      award_status: "Verified official award/work order",
      dlp_status: "Verified active on capture date",
    } : null;

    const classificationLines = [
      "Defect decision: Pothole — YES",
      `Surface: ${surface}`,
      `App visual size class: ${size}`,
      "Physical dimensions (length / width / depth): Unknown / Unknown / Unknown",
      `Measurement provenance: ${measurementProvenance}`,
      `Measurement confidence: ${measurementConfidence}`,
    ];
    const routingLines = [
      `Geographic corporation/body: ${routing.geographicName}`,
      `Complaint intake authority: ${routing.intakeName}`,
      `Intake profile: ${routing.profile.profile_id}`,
      `Suggested portal category: ${profileCategory}`,
      `Suggested ward: ${ward}`,
      `Road owner/maintainer: ${routing.ownerName}`,
      `Routing basis: ${routing.clue}`,
    ];
    const tenderLines = tenderFields ? [
      `Status: ${tenderFields.status}`,
      `${tenderFields.reference_label}: ${tenderFields.tender_number}`,
      `Exact work name: ${tenderFields.exact_work_name}`,
      `Organisation / department: ${tenderFields.organisation}`,
      `Listed contractor: ${tenderFields.listed_contractor}`,
      `Publication date: ${tenderFields.publication_date}`,
      !["Not listed", "Not applicable"].includes(tenderFields.lifecycle_status)
        ? `Tender / project status: ${tenderFields.lifecycle_status}` : null,
      !["Not listed", "Not applicable"].includes(tenderFields.bid_closing)
        ? `Bid closing: ${tenderFields.bid_closing}` : null,
      !["Not listed", "Not applicable"].includes(tenderFields.bid_opening)
        ? `Bid opening: ${tenderFields.bid_opening}` : null,
      !["Not listed", "Not applicable"].includes(tenderFields.project_start)
        ? `Project start: ${tenderFields.project_start}` : null,
      !["Not listed", "Not applicable"].includes(tenderFields.project_completion)
        ? `Likely completion: ${tenderFields.project_completion}` : null,
      !["Not listed", "Not applicable"].includes(tenderFields.agreement_number)
        ? `Agreement: ${tenderFields.agreement_number}`
          + (!["Not listed", "Not applicable"].includes(tenderFields.agreement_date)
            ? ` dated ${tenderFields.agreement_date}` : "") : null,
      !["Not listed", "Not applicable"].includes(tenderFields.package_reference)
        ? `Package / project reference: ${tenderFields.package_reference}` : null,
      !["Not listed", "Not applicable"].includes(tenderFields.highway_reference)
        ? `Highway reference: ${tenderFields.highway_reference}` : null,
      !["Not listed", "Not applicable"].includes(tenderFields.published_chainage)
        ? `Published package chainage (GPS point not verified): ${tenderFields.published_chainage}` : null,
      !["Not listed", "Not applicable"].includes(tenderFields.road_from)
        ? `Road from: ${tenderFields.road_from}` : null,
      !["Not listed", "Not applicable"].includes(tenderFields.road_to)
        ? `Road to: ${tenderFields.road_to}` : null,
      !["Not listed", "Not applicable"].includes(tenderFields.match_basis)
        ? `Candidate match basis: ${tenderFields.match_basis}` : null,
      `Source: ${tenderFields.source_name}${tenderFields.source_url !== "Not applicable" ? ` — ${tenderFields.source_url}` : ""}`,
      !["Not listed", "Not applicable"].includes(tenderFields.detail_url)
        && tenderFields.detail_url !== tenderFields.source_url
        ? `Official tender detail link (may expire): ${tenderFields.detail_url}` : null,
      `Carriageway scope: ${tenderFields.scope}`,
      `Road-segment match: ${tenderFields.segment_match}`,
      `Award/work-order status: ${tenderFields.award_status}`,
      `DLP status: ${tenderFields.dlp_status}`,
    ].filter(Boolean) : [
      `Status: ${NO_VERIFIED_CONTRACT}`,
    ];
    const request = routing.profile.request
      || "Please register this grievance, inspect and repair the pothole, return the grievance number, and transfer it if another agency maintains the road.";
    const outputLang = LANG();
    const addressedAuthority = outputLang === "bn"
      && routing.route.authority_id === "wb-kmc"
      ? "কলকাতা পৌরসংস্থা (KMC)" : routing.intakeName;
    const greeting = outputLang === "kn" ? `ಮಾನ್ಯ ${addressedAuthority} ಅವರಿಗೆ,`
      : outputLang === "mr" ? `प्रति ${addressedAuthority},`
        : outputLang === "bn" ? `মাননীয় ${addressedAuthority},`
          : `Dear ${routing.intakeName},`;
    const signoff = outputLang === "kn" ? `ವಂದನೆಗಳು,\n${S.name}`
      : outputLang === "mr" ? `आपला/आपली,\n${S.name}`
        : outputLang === "bn" ? `বিনীত,\n${S.name}`
          : `Regards,\n${S.name}`;
    const independentNote = complaintFooter(outputLang);
    const subject = outputLang === "kn" ? `ರಸ್ತೆ ಗುಂಡಿ ದೂರು — ${road}`
      : outputLang === "mr" ? `खड्ड्याची तक्रार — ${road}`
        : outputLang === "bn" ? `রাস্তার গর্তের অভিযোগ — ${road}`
          : `Pothole complaint — ${road}`;
    const emailBody = [
      greeting,
      "Please register the following pothole grievance.",
      `LOCATION\nAddress / landmark: ${location}\nCoordinates: ${coordinates}\nMap: ${mapUrl}\nGPS accuracy: ${gpsAccuracy}\nCaptured: ${captured}\nPhoto: ${photoProvenance}`,
      `CLASSIFICATION\n${classificationLines.join("\n")}`,
      `ROUTING\n${routingLines.join("\n")}`,
      `CONTRACT VERIFICATION\n${tenderLines.join("\n")}`,
      request,
      signoff,
      independentNote,
    ].join("\n\n");
    const whatsappText = [
      `Pothole report: ${location}`,
      `Coordinates: ${coordinates}`,
      `Map: ${mapUrl}`,
      `Classification: Pothole YES; ${surface}; app visual size ${size}; physical measurements unknown (${measurementProvenance.toLowerCase()}, ${measurementConfidence.toLowerCase()} confidence).`,
      `Routing: geographic body ${routing.geographicName}; intake ${routing.intakeName}; road owner ${routing.ownerVerified ? routing.ownerName : "unverified"}; basis ${routing.clue}.`,
      tenderFields
        ? `Contract verification: ${tenderFields.status}; ${tenderFields.reference_label.toLowerCase()} ${tenderFields.tender_number}; work ${tenderFields.exact_work_name}; organisation ${tenderFields.organisation}; contractor ${tenderFields.listed_contractor}; source ${tenderFields.source_name} ${tenderFields.source_url}; DLP ${tenderFields.dlp_status}.`
        : `Contract verification: ${NO_VERIFIED_CONTRACT}`,
      "Please inspect, repair, register the grievance and share its reference number.",
      independentNote,
    ].join("\n");
    const portalFields = {
      title: subject,
      category: profileCategory,
      address_landmark: location,
      coordinates,
      map_url: mapUrl,
      gps_accuracy: gpsAccuracy,
      captured_at: captured,
      photo_provenance: photoProvenance,
      defect_decision: "Pothole — YES",
      surface,
      app_visual_size_class: size,
      physical_dimensions: "Unknown (no reference scale)",
      measurement_provenance: measurementProvenance,
      measurement_confidence: measurementConfidence,
      geographic_body: routing.geographicName,
      intake_authority: routing.intakeName,
      intake_profile: routing.profile.profile_id,
      intake_channel: profileChannel,
      suggested_ward: ward,
      road_owner_maintainer: routing.ownerName,
      routing_basis: routing.clue,
      contract_verification_status: tenderFields ? tenderFields.status : NO_VERIFIED_CONTRACT,
      ...(tenderFields ? {
        tender_number: tenderFields.tender_number,
        exact_work_name: tenderFields.exact_work_name,
        organisation_department: tenderFields.organisation,
        listed_contractor: tenderFields.listed_contractor,
        publication_date: tenderFields.publication_date,
        tender_project_status: tenderFields.lifecycle_status,
        bid_closing: tenderFields.bid_closing,
        bid_opening: tenderFields.bid_opening,
        project_start: tenderFields.project_start,
        likely_completion: tenderFields.project_completion,
        agreement_number: tenderFields.agreement_number,
        agreement_date: tenderFields.agreement_date,
        package_project_reference: tenderFields.package_reference,
        highway_reference: tenderFields.highway_reference,
        published_package_chainage: tenderFields.published_chainage,
        road_from: tenderFields.road_from,
        road_to: tenderFields.road_to,
        contract_match_basis: tenderFields.match_basis,
        contract_source_name: tenderFields.source_name,
        contract_source_url: tenderFields.source_url,
        official_tender_detail_url: tenderFields.detail_url,
        carriageway_scope: tenderFields.scope,
        road_segment_match: tenderFields.segment_match,
        award_work_order_status: tenderFields.award_status,
        dlp_status: tenderFields.dlp_status,
      } : {}),
      request,
      independent_app_note: independentNote,
    };
    const portalCopyText = Object.entries(portalFields)
      .map(([key, value]) => `${key.replace(/_/g, " ")}: ${value}`).join("\n");
    return {
      email_subject: subject, email_body: emailBody,
      whatsapp_text: whatsappText, portal_fields: portalFields,
      portal_copy_text: portalCopyText,
      complaint_profile_id: routing.profile.profile_id,
      intake_authority_id: routing.intakeId,
      intake_authority_name: routing.intakeName,
      geographic_authority_id: routing.route.geographic_authority_id || routing.route.authority_id,
      geographic_authority_name: routing.geographicName,
      road_owner_id: routing.route.road_owner_id || null,
      road_owner_name: routing.route.road_owner_name || null,
      road_owner_status: routing.route.road_owner_status,
      road_owner_evidence: routing.route.road_owner_evidence || null,
    };
  }

  function compatibleDraftRoute(route, officerName) {
    const legacyBmc = !!(route && (route.delivery_channel === "bmc_quickfix"
      || /brihanmumbai municipal corporation|\bbmc\b/i.test(route.authority_name || "")));
    return {
      ...(route || {}),
      routed: true,
      authority_id: route && route.authority_id || (legacyBmc ? "mh-bmc" : "legacy-direct-draft"),
      authority_name: route && route.authority_name || conciseRouteLabel(officerName)
        || "Concerned road authority",
      routing_source: route && route.routing_source || (legacyBmc
        ? "legacy_bmc_record" : "legacy_direct_draft"),
      delivery_channel: route && route.delivery_channel || "email",
      tender_eligible: !!(route && route.tender_eligible),
    };
  }

  function complaintOutputsForRecord(rec) {
    if (!rec || normaliseIssueType(rec.issue_type) !== "road_damage") return null;
    rec = migrateLegacyComplaintRecord(rec);
    if (rec.whatsapp_text && rec.portal_copy_text && rec.portal_fields) {
      return {
        email_subject: rec.email_subject || "",
        email_body: rec.email_body || "",
        whatsapp_text: rec.whatsapp_text,
        portal_fields: rec.portal_fields,
        portal_copy_text: rec.portal_copy_text,
        complaint_profile_id: rec.complaint_profile_id || null,
      };
    }
    const route = compatibleDraftRoute({
      ...rec,
      routed: true,
      tender_eligible: !!rec.tender_number,
    }, rec.officer_name);
    const tender = rec.tender_number ? {
      tender_number: rec.tender_number,
      reference_label: rec.tender_reference_label || "Tender number",
      title: rec.tender_title,
      contractor: rec.contractor,
      published: rec.tender_published,
      organisation: rec.tender_organisation || null,
      detail_url: rec.tender_detail_url || null,
      bid_closing: rec.tender_bid_closing || null,
      bid_opening: rec.tender_bid_opening || null,
      project_start: rec.tender_project_start || null,
      project_completion: rec.tender_project_completion || null,
      agreement_number: rec.tender_agreement_number || null,
      agreement_date: rec.tender_agreement_date || null,
      package_reference: rec.tender_package_reference || null,
      highway_reference: rec.tender_highway_reference || null,
      published_chainage: rec.tender_published_chainage || null,
      road_from: rec.tender_road_from || null,
      road_to: rec.tender_road_to || null,
      source_name: rec.tender_source_name,
      source_url: rec.tender_source_url,
      lifecycle: rec.tender_lifecycle || null,
      lifecycle_status: rec.tender_lifecycle_status || null,
      match_basis: rec.tender_match_basis || null,
      segment_status: rec.tender_segment_status || null,
      segment_verified: rec.tender_segment_verified === true,
      award_status: rec.tender_award_status || null,
      award_verified: rec.tender_award_verified === true,
      dlp_status: rec.tender_dlp_status || null,
      dlp_verified: rec.tender_dlp_verified === true,
      responsibility_active_verified: rec.tender_responsibility_active_verified === true,
      responsibility_valid_from: rec.tender_responsibility_valid_from || null,
      responsibility_valid_until: rec.tender_responsibility_valid_until || null,
      responsible_authority_id: rec.tender_responsible_authority_id || null,
      road_owner_id: rec.tender_road_owner_id || null,
      verification_evidence: rec.tender_verification_evidence || null,
      unambiguous: rec.tender_unambiguous === true,
      tender_pack_id: rec.tender_pack_id,
      tender_pack_version: rec.tender_pack_version,
      tender_pack_sha256: rec.tender_pack_sha256,
      tender_pack_state_code: rec.tender_pack_state_code,
    } : null;
    const assessment = binaryAssessment({
      is_pothole: true,
      looks_like_speed_breaker: false,
      image_quality: "usable",
      surface_type: rec.surface_type || "unknown",
      on_drivable_surface: true,
      has_localized_cavity: true,
      has_broken_edge_or_rim: true,
      has_depth_or_surface_loss: true,
      temporal_consistency: rec.temporal_consistency || "single_view",
      size: POTHOLE_SIZES.has(rec.size) ? rec.size : "medium",
      description: rec.description || "",
    }, false, 1);
    // Legacy v5 rows had no surface field. Preserve their accepted historical status
    // while labelling the surface/measurement as unknown instead of reclassifying them.
    if (!assessment.is_pothole) {
      assessment.is_pothole = true;
      assessment.reportable = true;
      assessment.damage_type = "pothole_cavity";
      assessment.defect_type = "pothole";
      assessment.size = POTHOLE_SIZES.has(rec.size) ? rec.size : "unknown";
      assessment.measurement_provenance = "legacy_unknown";
      assessment.measurement_confidence = "low";
    }
    const output = buildComplaintOutputs(assessment, Number(rec.lat), Number(rec.lng),
      rec.address, rec.officer_name, tender, route, {
        captured_at: rec.captured_at || rec.created_at,
        gps_accuracy: Number(rec.gps_accuracy),
        photo_provenance: rec.capture_source === "manual_import"
          ? "User-selected/imported photo" : "Pothole Reporter camera evidence",
      });
    output.email_subject = rec.email_subject || output.email_subject;
    output.email_body = rec.email_body || output.email_body;
    return output;
  }

  function draftEmail(a, lat, lng, address, officerName, tender, route = null) {
    const output = buildComplaintOutputs(a, lat, lng, address, officerName, tender,
      compatibleDraftRoute(route, officerName));
    return [output.email_subject, output.email_body];
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
    const authority = conciseRouteLabel(route && route.authority_name);
    const officer = conciseRouteLabel(route && OFFICIAL_HANDOFF_CHANNELS.has(route.delivery_channel)
      ? authority : officerName);
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
    const greeting = ({ kn: `ಮಾನ್ಯ ${officer || "ಅಧಿಕಾರಿಗಳೇ"} ಅವರಿಗೆ,`,
      mr: `प्रति ${officer || "संबंधित अधिकारी"},`,
      bn: `মাননীয় ${officer || "সংশ্লিষ্ট আধিকারিক"},` }[lang]
      || `Dear ${officer || "Sir or Madam"},`);
    const close = ({ kn: `ಧನ್ಯವಾದಗಳು.\n\nವಂದನೆಗಳು,\n${sender}`,
      mr: `धन्यवाद.\n\nआपला/आपली,\n${sender}`,
      bn: `ধন্যবাদ।\n\nবিনীত,\n${sender}` }[lang]
      || `Thank you.\n\nRegards,\n${sender}`);
    const statewideWestBengal = route && route.authority_id === "wb-statewide-unverified";
    const authorityNote = route && authority
      ? (statewideWestBengal
        ? (lang === "kn" ? "ಸೂಚಿಸಿದ ದೂರು ಮಾರ್ಗ: West Bengal PGRS; ಜವಾಬ್ದಾರ ಜಿಲ್ಲೆ ಅಥವಾ ಇಲಾಖೆಯನ್ನು ಗುರುತಿಸಲಾಗಿಲ್ಲ."
          : lang === "mr" ? "सुचवलेला तक्रार मार्ग: West Bengal PGRS; जबाबदार जिल्हा किंवा विभाग ओळखलेला नाही."
          : lang === "bn" ? "প্রস্তাবিত অভিযোগের মাধ্যম: West Bengal PGRS; দায়িত্বপ্রাপ্ত জেলা বা দপ্তর চিহ্নিত করা হয়নি।"
          : "Suggested complaint channel: West Bengal PGRS; the responsible district or department has not been identified.")
        : normaliseAuthorityValue(officer) === normaliseAuthorityValue(authority) ? null
        : lang === "kn" ? `ಸೂಚಿಸಿದ ನಾಗರಿಕ ಸಂಸ್ಥೆ: ${authority}.`
        : lang === "mr" ? `सुचवलेली नागरी संस्था: ${authority}.`
        : lang === "bn" ? `প্রস্তাবিত পৌর কর্তৃপক্ষ: ${authority}।`
        : `Suggested civic authority: ${authority}.`)
      : null;
    const imported = captureSource === "manual_import";
    const provenanceNote = imported
      ? (lang === "kn"
          ? `ಈ ಚಿತ್ರವನ್ನು ಬಳಕೆದಾರರು ಆಯ್ಕೆಮಾಡಿ/ಆಮದು ಮಾಡಿದ್ದಾರೆ; ಮೂಲ ಸೆರೆಹಿಡಿದ ಸಮಯ ತಿಳಿದಿಲ್ಲ.${locationSource === "current_confirmed_for_import" ? " ಬಳಕೆದಾರರ ದೃಢೀಕರಣದ ನಂತರ ಪ್ರಸ್ತುತ ಸ್ಥಳವನ್ನು ಚಿತ್ರಕ್ಕೆ ಜೋಡಿಸಲಾಗಿದೆ." : " ಚಿತ್ರಕ್ಕೆ ಪ್ರಸ್ತುತ ಸ್ಥಳವನ್ನು ಜೋಡಿಸಲಾಗಿಲ್ಲ."}`
        : lang === "mr"
          ? `हे छायाचित्र वापरकर्त्याने निवडले/आयात केले आहे; मूळ छायाचित्रणाची वेळ माहित नाही.${locationSource === "current_confirmed_for_import" ? " वापरकर्त्याच्या पुष्टीनंतर सध्याचे स्थान छायाचित्राशी जोडले आहे." : " छायाचित्राशी सध्याचे स्थान जोडलेले नाही."}`
        : lang === "bn"
          ? `ছবিটি ব্যবহারকারী বেছে নিয়েছেন/আমদানি করেছেন; মূল ছবিটি তোলার সময় অজানা।${locationSource === "current_confirmed_for_import" ? " ব্যবহারকারীর নিশ্চিতকরণের পরে বর্তমান অবস্থানটি ছবির সঙ্গে যুক্ত হয়েছে।" : " ছবির সঙ্গে বর্তমান অবস্থান যুক্ত করা হয়নি।"}`
        : `This photo was selected/imported by the user; its original capture time is unknown.${locationSource === "current_confirmed_for_import" ? " The current location was linked only after the user's confirmation." : " No current location was linked to the photo."}`)
      : null;
    return [subject, [greeting, location, request[lang] || request.en, provenanceNote,
      authorityNote, close, complaintFooter(lang, false)]
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

  const STORED_DATA_STORES = ["reports", "drives", "footage", "state_packs"];

  // Delete-all is one IndexedDB transaction. Sequential clears can leave a misleading
  // half-wiped history when a later store aborts (especially under storage pressure).
  function clearAllStoredRecords() {
    return idb().then((d) => new Promise((resolve, reject) => {
      const tx = d.transaction(STORED_DATA_STORES, "readwrite");
      let failure = null;
      for (const name of STORED_DATA_STORES) {
        const req = tx.objectStore(name).clear();
        req.onerror = () => { failure = req.error; };
      }
      tx.oncomplete = () => resolve();
      const died = () => reject(storageError(failure || tx.error));
      tx.onabort = died;
      tx.onerror = () => {};
    }));
  }

  function allStoredRecordsAreEmpty() {
    return idb().then((d) => new Promise((resolve, reject) => {
      const tx = d.transaction(STORED_DATA_STORES, "readonly");
      let remaining = 0, failure = null;
      for (const name of STORED_DATA_STORES) {
        const req = tx.objectStore(name).count();
        req.onsuccess = () => { remaining += Number(req.result) || 0; };
        req.onerror = () => { failure = req.error; };
      }
      tx.oncomplete = () => failure ? reject(storageError(failure)) : resolve(remaining === 0);
      const died = () => reject(storageError(failure || tx.error));
      tx.onabort = died;
      tx.onerror = () => {};
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

  async function migrateLegacyComplaintDrafts(records) {
    const result = [];
    for (const rec of records) {
      const migrated = migrateLegacyComplaintRecord(rec);
      if (migrated !== rec) {
        // Re-read inside the same transaction that writes. A revisit, status change or
        // sighting can land while the list is loading; persisting the stale getAll() row
        // would erase that newer state and its evidence.
        try {
          const current = await mutateReportAtomically(rec.id, () => {});
          result.push(current || migrated);
          continue;
        } catch (_) { /* clean returned copy now; retry persistence on the next read */ }
      }
      result.push(migrated);
    }
    return result;
  }

  // Complaint actions race with Drive Mode: a repair can be committed while a portal
  // refresh, editor, or email composer is awaiting other work. Always re-read and patch
  // the latest row in one transaction so those complaint-only mutations cannot restore
  // a stale physical condition or erase its repair evidence.
  function mutateReportAtomically(id, mutate) {
    const reportId = Number(id);
    if (!Number.isFinite(reportId) || reportId <= 0) {
      return Promise.reject(new Error("Report not found."));
    }
    return idb().then((d) => new Promise((resolve, reject) => {
      const tx = d.transaction("reports", "readwrite");
      const store = tx.objectStore("reports");
      let result = null, failure = null;
      const abortWith = (error) => {
        failure = error instanceof Error ? error : new Error(String(error || "Could not update this report."));
        try { tx.abort(); } catch (_) {}
      };
      const read = store.get(reportId);
      read.onsuccess = () => {
        const current = read.result;
        if (!current) { abortWith(new Error("Report not found.")); return; }
        const migrated = migrateLegacyComplaintRecord(current);
        if (migrated !== current) {
          for (const field of ["email_body", "whatsapp_text", "portal_fields",
            "portal_copy_text", "complaint_template_version"]) {
            current[field] = migrated[field];
          }
        }
        try { mutate(current); } catch (error) { abortWith(error); return; }
        const write = store.put(current);
        write.onsuccess = () => { result = toDict(current); };
        write.onerror = () => { failure = write.error; };
      };
      read.onerror = () => { failure = read.error; };
      tx.oncomplete = () => resolve(result);
      tx.onabort = () => reject(failure || storageError(tx.error));
      tx.onerror = () => {};
    }));
  }

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
              // A fresh accepted detection is direct evidence that the defect remains.
              // It can clear an AI-suggested repair review, but a previously fixed
              // canonical is excluded by acceptedReport and therefore becomes a new
              // recurrence rather than silently rewriting history.
              condition_status: conditionStatus(prior) === "repair_review" ? "open"
                : conditionStatus(prior),
              condition_updated_at: conditionStatus(prior) === "repair_review"
                ? (eventTime(rec) || Date.now() / 1000) : (prior.condition_updated_at || null),
              condition_source: conditionStatus(prior) === "repair_review"
                ? "damage_seen_on_revisit" : (prior.condition_source || null),
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
  const blobToDataUrl = async (v) => {
    if (!v) return null;
    if (typeof v === "string") return v;
    return await new Promise((resolve, reject) => {
      const fr = new FileReader();
      fr.onload = () => resolve(String(fr.result));
      fr.onerror = () => reject(fr.error || new Error("Could not read saved repair evidence."));
      fr.readAsDataURL(v);
    });
  };

  const repairProvenanceIsExact = (observation) => {
    if (!observation || typeof observation !== "object") return false;
    const model = observation.detection_model;
    const detail = observation.image_detail;
    return typeof model === "string" && model.length > 0 && ALLOWED_MODELS.has(model)
      && typeof detail === "string" && detail.length > 0 && ALLOWED_DETAILS.has(detail)
      && normaliseDetail(detail, model) === detail
      && typeof observation.description === "string"
      && observation.description.trim().length > 0
      && observation.prompt_version === REPAIR_PROMPT_VERSION
      && Number.isInteger(observation.schema_version)
      && observation.schema_version === REPAIR_SCHEMA_VERSION;
  };

  // Repair evidence changes a saved physical fact, so it has a stricter contract than
  // legacy report photos: it must be a real, decodable, bounded image Blob. Never retain
  // an arbitrary string merely because fetch() could not decode it.
  async function decodeRepairEvidence(value) {
    let blob = null;
    if (typeof Blob !== "undefined" && value instanceof Blob) {
      blob = value;
    } else if (typeof value === "string"
        && /^data:image\/(?:jpeg|png|webp);base64,/i.test(value)) {
      try {
        const response = await fetch(value);
        if (!response.ok) return null;
        blob = await response.blob();
      } catch (_) { return null; }
    }
    const type = String(blob && blob.type || "").toLowerCase();
    if (!blob || blob.size < REPAIR_EVIDENCE_MIN_BYTES || blob.size > REPAIR_EVIDENCE_MAX_BYTES
        || !REPAIR_EVIDENCE_TYPES.has(type)) return null;
    let header;
    try { header = new Uint8Array(await blob.slice(0, 12).arrayBuffer()); }
    catch (_) { return null; }
    const jpeg = header[0] === 0xff && header[1] === 0xd8 && header[2] === 0xff;
    const png = header[0] === 0x89 && header[1] === 0x50 && header[2] === 0x4e
      && header[3] === 0x47 && header[4] === 0x0d && header[5] === 0x0a
      && header[6] === 0x1a && header[7] === 0x0a;
    const webp = header[0] === 0x52 && header[1] === 0x49 && header[2] === 0x46
      && header[3] === 0x46 && header[8] === 0x57 && header[9] === 0x45
      && header[10] === 0x42 && header[11] === 0x50;
    if ((type === "image/jpeg" && !jpeg) || (type === "image/png" && !png)
        || (type === "image/webp" && !webp)) return null;
    let width = 0, height = 0;
    if (typeof createImageBitmap === "function") {
      let bitmap = null;
      try {
        bitmap = await createImageBitmap(blob);
        width = bitmap.width; height = bitmap.height;
      } catch (_) { return null; }
      finally { if (bitmap && bitmap.close) bitmap.close(); }
    } else {
      const url = URL.createObjectURL(blob);
      try {
        const dimensions = await new Promise((resolve, reject) => {
          const img = new Image();
          img.onload = () => resolve([img.naturalWidth, img.naturalHeight]);
          img.onerror = () => reject(new Error("Repair evidence is not a decodable image."));
          img.src = url;
        });
        width = dimensions[0]; height = dimensions[1];
      } catch (_) { return null; }
      finally { URL.revokeObjectURL(url); }
    }
    if (!Number.isInteger(width) || !Number.isInteger(height)
        || width < REPAIR_EVIDENCE_MIN_DIMENSION || height < REPAIR_EVIDENCE_MIN_DIMENSION
        || width > REPAIR_EVIDENCE_MAX_DIMENSION || height > REPAIR_EVIDENCE_MAX_DIMENSION
        || width * height > REPAIR_EVIDENCE_MAX_PIXELS) return null;
    return blob;
  }
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

  const toDict = (r) => {
    const outward = migrateLegacyComplaintRecord(r);
    return { ...outward, photo_url: outward.photo,
             repair_photo_url: outward.repair_photo || null };
  };
  // The list never renders the evidence copy, so it never receives it.
  const listDict = (r) => { const d = toDict(r); delete d.photo_full; return d; };

  async function findRepairCandidate(observation) {
    if (!observation || !finiteCoord(observation.lat) || !finiteCoord(observation.lng)) return null;
    const latitudeBand = REPAIR_RADIUS_M / 110900;
    const nearby = await op("readonly", (store) => store.index("by_lat").getAll(
      IDBKeyRange.bound(observation.lat - latitudeBand, observation.lat + latitudeBand)));
    return findRepairCandidateFromReports(observation, nearby);
  }

  // Commit the before/after result and its evidence in one transaction. A native retry
  // after the WebView closes is harmless because source_event_key is idempotent.
  async function applyRepairObservation(targetId, observation) {
    const id = Number(targetId);
    const sourceEventKey = String(observation && observation.source_event_key || "").slice(0, 180);
    const nextCondition = repairConditionFor(observation);
    if (!Number.isFinite(id) || id <= 0 || !sourceEventKey || !nextCondition) {
      return { ignored: true, reason: "repair_not_proven" };
    }
    if (!repairProvenanceIsExact(observation)) {
      return { ignored: true, reason: "repair_provenance_invalid" };
    }
    const repairPhoto = await decodeRepairEvidence(observation.current_photo_data_url);
    if (!repairPhoto) return { ignored: true, reason: "repair_evidence_invalid" };
    const candidate = {
      ...observation,
      capture_source: "drive_live",
      debug_capture: false,
      drive_id: observation.drive_id == null ? null : String(observation.drive_id),
    };
    if (!finiteCoord(candidate.lat) || !finiteCoord(candidate.lng)) {
      return { ignored: true, reason: "target_ambiguous_or_mismatched" };
    }
    // The uniqueness scan and update deliberately share one read-write transaction.
    // IndexedDB serialises competing writers on this store, so no nearby report can be
    // inserted or changed between the ambiguity decision and the physical-status write.
    return idb().then((d) => new Promise((resolve, reject) => {
      const tx = d.transaction("reports", "readwrite");
      const store = tx.objectStore("reports");
      let result = null, failure = null;
      const matches = [];
      const replays = [];
      const latitudeBand = REPAIR_RADIUS_M / 110900;
      const scan = store.index("by_lat").openCursor(IDBKeyRange.bound(
        candidate.lat - latitudeBand, candidate.lat + latitudeBand));
      scan.onsuccess = () => {
        const cursor = scan.result;
        if (cursor) {
          const priorKeys = Array.isArray(cursor.value.repair_source_event_keys)
            ? cursor.value.repair_source_event_keys : [];
          if (priorKeys.includes(sourceEventKey)) replays.push(cursor.value);
          if (repairTargetMatch(candidate, cursor.value)) matches.push(cursor.value);
          cursor.continue();
          return;
        }
        if (replays.length) {
          if (replays.length === 1 && Number(replays[0].id) === id) {
            result = { id, duplicate: true, condition_status: conditionStatus(replays[0]) };
          } else {
            result = { ignored: true, reason: "target_ambiguous_or_mismatched" };
          }
          return;
        }
        if (matches.length !== 1 || Number(matches[0].id) !== id) {
          result = { ignored: true, reason: "target_ambiguous_or_mismatched" };
          return;
        }
        const prior = matches[0];
        const keys = Array.isArray(prior.repair_source_event_keys)
          ? prior.repair_source_event_keys.slice() : [];
        if (keys.includes(sourceEventKey)) {
          result = { id, duplicate: true, condition_status: conditionStatus(prior) };
          return;
        }
        keys.push(sourceEventKey);
        const observedAt = observation.observed_at;
        const updated = {
          ...prior,
          condition_status: nextCondition,
          condition_updated_at: observedAt,
          condition_source: "ai_revisit_comparison",
          repair_observed_at: observedAt,
          repair_drive_id: candidate.drive_id,
          repair_source_event_keys: keys.slice(-64),
          repair_photo: repairPhoto,
          repair_lat: observation.lat,
          repair_lng: observation.lng,
          repair_gps_accuracy: observation.gps_accuracy,
          repair_speed_mps: Number.isFinite(observation.speed_mps) ? observation.speed_mps : null,
          repair_heading: Number.isFinite(observation.heading) ? observation.heading : null,
          repair_current_condition: observation.current_condition,
          repair_assessment: observation.assessment,
          repair_image_quality: observation.image_quality,
          repair_same_location_visible: observation.same_location_visible,
          repair_completed_visible: observation.completed_repair_visible,
          repair_description: String(observation.description || "").trim().slice(0, 1000),
          repair_detection_model: observation.detection_model,
          repair_image_detail: observation.image_detail,
          repair_prompt_version: observation.prompt_version,
          repair_schema_version: observation.schema_version,
        };
        const write = store.put(updated);
        write.onsuccess = () => {
          result = { id, duplicate: false, condition_status: nextCondition, report: toDict(updated) };
        };
        write.onerror = () => { failure = write.error; };
      };
      scan.onerror = () => { failure = scan.error; };
      tx.oncomplete = () => resolve(result || { ignored: true, reason: "target_ambiguous_or_mismatched" });
      const died = () => reject(storageError(failure || tx.error));
      tx.onabort = died;
      tx.onerror = () => {};
    }));
  }

  const MAX_REPAIR_TARGETS = 2000;
  const MAX_REPAIR_TARGET_BATCH_SIZE = 2;
  const MAX_REPAIR_TARGET_IMAGE_BYTES = 4 * 1024 * 1024;
  const MAX_REPAIR_TARGET_TOTAL_BYTES = 512 * 1024 * 1024;

  function eligibleRepairTarget(report) {
    if (!acceptedReport(report) || conditionStatus(report) === "fixed"
        || report.debug_capture || report.dedupe_eligible === false || !report.photo
        || report.capture_source === "manual_import"
        || !finiteCoord(report.lat) || !finiteCoord(report.lng)
        || !Number.isFinite(eventTime(report))
        || !Number.isFinite(report.gps_accuracy) || report.gps_accuracy < 0
        || report.gps_accuracy > REPAIR_MAX_ACCURACY_M) return false;
    return normaliseIssueType(report.issue_type) === "road_damage";
  }

  function repairTargetPhotoBytes(photo) {
    if (typeof photo === "string") {
      const match = photo.match(/^data:image\/(?:jpeg|jpg|png|webp|gif);base64,([A-Za-z0-9+/]*={0,2})$/i);
      if (!match) return NaN;
      const payload = match[1];
      const padding = payload.endsWith("==") ? 2 : payload.endsWith("=") ? 1 : 0;
      return Math.floor(payload.length * 3 / 4) - padding;
    }
    return photo && Number.isFinite(photo.size) ? Number(photo.size) : NaN;
  }

  // The manifest cursor keeps only numeric ids and stops after the newest 2,000. Calling
  // getAll() here cloned every historical photo into one JS heap before the native bridge
  // had a chance to apply its cap.
  function getRepairTargetIds() {
    return idb().then((d) => new Promise((resolve, reject) => {
      const tx = d.transaction("reports", "readonly");
      const ids = [];
      let selectedBytes = 0;
      let failure = null;
      const scan = tx.objectStore("reports").openCursor(null, "prev");
      scan.onsuccess = () => {
        const cursor = scan.result;
        if (!cursor || ids.length >= MAX_REPAIR_TARGETS) return;
        const report = cursor.value;
        const id = Number(report && report.id);
        const photoBytes = repairTargetPhotoBytes(report && report.photo);
        if (Number.isSafeInteger(id) && id > 0 && eligibleRepairTarget(report)
            && Number.isFinite(photoBytes) && photoBytes > 0
            && photoBytes <= MAX_REPAIR_TARGET_IMAGE_BYTES
            && selectedBytes <= MAX_REPAIR_TARGET_TOTAL_BYTES - photoBytes) {
          ids.push(id);
          selectedBytes += photoBytes;
        }
        cursor.continue();
      };
      scan.onerror = () => { failure = scan.error; };
      tx.oncomplete = () => failure ? reject(storageError(failure)) : resolve(ids);
      const died = () => reject(storageError(failure || tx.error));
      tx.onabort = died;
      tx.onerror = () => {};
    }));
  }

  async function getRepairTargetBatch(ids) {
    if (!Array.isArray(ids) || !ids.length || ids.length > MAX_REPAIR_TARGET_BATCH_SIZE
        || ids.some((id) => !Number.isSafeInteger(id) || id <= 0)
        || new Set(ids).size !== ids.length) {
      throw new Error("Repair target batch must contain one or two unique report ids.");
    }
    // At most two records and two photos exist in this call at any time.
    const reports = await Promise.all(ids.map((id) => getReport(id)));
    const targets = [];
    for (let index = 0; index < ids.length; index++) {
      const report = reports[index];
      if (!report || Number(report.id) !== ids[index] || !eligibleRepairTarget(report)) {
        throw new Error("Repair history changed while its native cache was being refreshed.");
      }
      const photoBytes = repairTargetPhotoBytes(report.photo);
      if (!Number.isFinite(photoBytes) || photoBytes <= 0
          || photoBytes > MAX_REPAIR_TARGET_IMAGE_BYTES) {
        throw new Error("A repair target photo exceeds the 4 MB native cache limit.");
      }
      targets.push({
        id: report.id,
        lat: report.lat,
        lng: report.lng,
        gps_accuracy: report.gps_accuracy,
        heading: Number.isFinite(report.heading) ? report.heading : null,
        capture_source: report.capture_source || null,
        photo_data_url: await blobToDataUrl(report.photo),
        last_damage_observed_at: eventTime(report),
        damage_type: storedDamageType(report),
        condition_status: conditionStatus(report),
      });
    }
    return targets;
  }

  async function verifyRepairCandidate(prior, contextDataUrl, roadViews, primaryIndex,
                                       model, detail) {
    const oldEvidence = await blobToDataUrl(prior && prior.photo);
    if (!oldEvidence || !contextDataUrl || !Array.isArray(roadViews) || !roadViews.length) {
      return null;
    }
    const current = [roadViews[primaryIndex]];
    for (let i = 0; i < roadViews.length && current.length < 2; i++) {
      if (i !== primaryIndex) current.push(roadViews[i]);
    }
    const images = [{ url: oldEvidence }, { url: contextDataUrl },
      ...current.filter(Boolean).map((url) => ({ url }))];
    const language = LANG() === "kn"
      ? "\n- Write description in formal Kannada."
      : LANG() === "mr" ? "\n- Write description in formal Marathi."
        : LANG() === "bn" ? "\n- Write description in formal Bengali." : "";
    return analyzeImage(images, REPAIR_PROMPT + language, "road_repair_verification",
      REPAIR_SCHEMA, model, null, false, detail);
  }

  // ---------- image ----------

  // Drive frames use the same orientation-aware road region as native Android. Both
  // quality enhancement and inference therefore inspect near/mid road while excluding
  // sky and the dashboard/bonnet. Manual Photo remains full-frame because the person
  // has already aimed the camera at the defect.
  const ROAD_REGION_RATIOS = Object.freeze({
    portrait: Object.freeze({ top: 0.40, bottom: 0.66 }),
    landscape: Object.freeze({ top: 0.48, bottom: 0.78 }),
    square: Object.freeze({ top: 0.40, bottom: 0.70 }),
  });
  const ROAD_ORIENTATION_EPSILON = 0.10;
  const ROAD_CROP_MAX_UPSCALE = 4.0;

  function selectRoadRegion(frameWidth, frameHeight) {
    if (!Number.isFinite(frameWidth) || !Number.isFinite(frameHeight)
        || frameWidth <= 0 || frameHeight <= 0) {
      throw new Error("Frame dimensions must be positive");
    }
    const width = Math.round(frameWidth), height = Math.round(frameHeight);
    if (width <= 0 || height <= 0) throw new Error("Frame dimensions must be positive");
    const aspectRatio = width / height;
    const orientation = aspectRatio < 1 - ROAD_ORIENTATION_EPSILON ? "portrait"
      : aspectRatio > 1 + ROAD_ORIENTATION_EPSILON ? "landscape" : "square";
    const ratios = ROAD_REGION_RATIOS[orientation];
    const top = Math.max(0, Math.min(height - 1, Math.round(height * ratios.top)));
    const bottom = Math.max(top + 1, Math.min(height, Math.round(height * ratios.bottom)));
    return { x: 0, y: top, width, height: bottom - top };
  }

  function detectionEnhancementPlan(data, width, height) {
    if (!Number.isInteger(width) || !Number.isInteger(height) || width <= 0 || height <= 0
        || !data || data.length !== width * height * 4) {
      throw new Error("Enhancement pixels must match positive dimensions");
    }
    const step = Math.max(1, Math.floor(Math.sqrt((width * height) / 12000)));
    let luminanceSum = 0, sampleCount = 0, darkCount = 0, brightCount = 0;
    for (let y = 0; y < height; y += step) {
      for (let x = 0; x < width; x += step) {
        const i = (y * width + x) * 4;
        const luminance = 2126 * data[i] + 7152 * data[i + 1] + 722 * data[i + 2];
        luminanceSum += luminance; sampleCount++;
        if (luminance < 120000) darkCount++;
        if (luminance > 2450000) brightCount++;
      }
    }
    const enhanced = luminanceSum < 720000 * sampleCount
      && brightCount * 100 < 8 * sampleCount;
    let gainNumerator = 1, gainDenominator = 1;
    if (enhanced) {
      gainNumerator = 935000 * sampleCount;
      gainDenominator = Math.max(luminanceSum, 350000 * sampleCount);
      if (gainNumerator * 1000 < 1265 * gainDenominator) {
        gainNumerator = 1265; gainDenominator = 1000;
      } else if (gainNumerator * 1000 > 1815 * gainDenominator) {
        gainNumerator = 1815; gainDenominator = 1000;
      }
    }
    return {
      enhanced, sampleCount, luminanceSum, darkCount, brightCount,
      gainNumerator, gainDenominator,
      mean: sampleCount ? luminanceSum / (10000 * sampleCount) : 0,
      dark: sampleCount ? darkCount / sampleCount : 1,
      bright: sampleCount ? brightCount / sampleCount : 0,
    };
  }

  function applyDetectionEnhancement(data, plan) {
    if (!plan || !plan.enhanced) return data;
    const denominator = 2 * plan.gainDenominator;
    const lookup = new Uint8Array(256);
    for (let channel = 0; channel < lookup.length; channel++) {
      const numerator = 2 * channel * plan.gainNumerator + plan.gainDenominator;
      lookup[channel] = Math.max(0, Math.min(255, Math.floor(numerator / denominator)));
    }
    for (let i = 0; i < data.length; i += 4) {
      data[i] = lookup[data[i]];
      data[i + 1] = lookup[data[i + 1]];
      data[i + 2] = lookup[data[i + 2]];
    }
    return data;
  }

  function averageLuminance(ctx, width, height) {
    return detectionEnhancementPlan(ctx.getImageData(0, 0, width, height).data, width, height);
  }

  async function toDataUrl(blob, maxDim, quality = 0.85, boost = false, cropRoad = false) {
    const bmp = await createImageBitmap(blob, { imageOrientation: "from-image" });
    let c = null;
    try {
      const region = cropRoad ? selectRoadRegion(bmp.width, bmp.height)
        : { x: 0, y: 0, width: bmp.width, height: bmp.height };
      const sx = region.x, sy = region.y, sw = region.width, sh = region.height;
      // Small road defects were disappearing into a sub-512px crop before vision tiling.
      // Preserve full-frame semantics, but enlarge a Drive road crop up to the requested
      // inspection width. The cap avoids pathological expansion of corrupt/tiny inputs.
      const scale = cropRoad
        ? Math.min(ROAD_CROP_MAX_UPSCALE, maxDim / Math.max(sw, sh))
        : Math.min(1, maxDim / Math.max(sw, sh));
      c = document.createElement("canvas");
      c.width = Math.round(sw * scale);
      c.height = Math.round(sh * scale);
      const ctx = c.getContext("2d");
      ctx.drawImage(bmp, sx, sy, sw, sh, 0, 0, c.width, c.height);
      // Enhancement follows the pixels, not the wall clock. Fixed evening hours boosted
      // bright street-lit frames and amplified noise. Preserve the original evidence copy;
      // this is only the small image used for detection.
      const imageData = boost ? ctx.getImageData(0, 0, c.width, c.height) : null;
      const light = imageData
        ? detectionEnhancementPlan(imageData.data, c.width, c.height) : null;
      if (light && light.enhanced) {
        applyDetectionEnhancement(imageData.data, light);
        ctx.putImageData(imageData, 0, 0);
      }
      return c.toDataURL("image/jpeg", quality);
    } finally {
      try {
        if (bmp.close) bmp.close();
      } finally {
        // Resetting the bitmap dimensions asks WebView to free its graphics backing
        // store immediately instead of retaining it until the canvas is collected.
        if (c) { c.width = 0; c.height = 0; }
      }
    }
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

  // VOD replay can keep several detector requests in flight. A 1920px canvas plus its
  // ImageData is intentionally short-lived, but preparing one burst per request at the
  // same time can still exhaust a WebView. Serialize only pixel preparation; the lock is
  // released before network inference so model calls retain their existing concurrency.
  let driveImagePreparationTail = Promise.resolve();
  async function withDriveImagePreparation(work) {
    const wait = driveImagePreparationTail;
    let release;
    driveImagePreparationTail = new Promise((resolve) => { release = resolve; });
    await wait;
    try {
      return await work();
    } finally {
      release();
    }
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
    const normalizedHeading = Number.isFinite(headingRaw)
      ? ((headingRaw % 360) + 360) % 360 : null;
    const repairObservationBase = {
      capture_source: captureSource,
      debug_capture: !dedupe,
      drive_id: driveId,
      lat, lng,
      gps_accuracy: Number.isFinite(gpsAccuracyRaw) ? gpsAccuracyRaw : null,
      speed_mps: Number.isFinite(speedRaw) ? speedRaw : null,
      heading: normalizedHeading,
      source_event_key: sourceEventKey,
      observed_at: Number.isFinite(capturedAtRaw) ? capturedAtRaw / 1000 : Date.now() / 1000,
    };
    // Start the local lookup while pixels are resized. Only a precise, later live drive
    // can be a repair revisit; footage replay and Debug must never change real status.
    const repairCandidateP = driveMode && captureSource === "drive_live" && dedupe
      ? findRepairCandidate(repairObservationBase).catch(() => null) : null;

    progress(driveMode ? pmsg("capture") : pmsg("compress"));
    // Measured on a real device: a 2000px frame is ~1.1 MB of base64 and every request
    // is marshalled across the JS-to-native bridge, which made a live detection call
    // take 13.5s median and stuttered the preview. The live pass therefore runs on a
    // smaller frame. Native Drive retains sparse 720p evidence frames beside low-resolution
    // video, so interrupted saved-frame checks can be retried later with nearby temporal
    // context. Single shots stay at full size:
    // one photo, someone waiting, and no footage behind it.
    // Drive Mode supplies one short burst. The model sees a full context view of the
    // sharpest frame plus three orientation-aware road-region crops in chronological order. Context keeps
    // lane/edge geometry; the crops give a distant defect enough pixels to judge. A
    // manual photo remains one full-resolution view because the user already aimed it.
    let imageInputs, dataUrl, roadViews = null, contextDataUrl = null;
    if (driveMode) {
      const prepared = await withDriveImagePreparation(async () => {
        // Build the three road crops one at a time, preserving chronological order.
        const orderedRoadViews = [];
        for (const p of photos) {
          orderedRoadViews.push(await toDataUrl(p, 1920, 0.85, true, true));
        }
        return {
          roadViews: orderedRoadViews,
          contextDataUrl: await toDataUrl(photo, 768, 0.82, false, false),
        };
      });
      roadViews = prepared.roadViews;
      contextDataUrl = prepared.contextDataUrl;
      imageInputs = [{ url: contextDataUrl }, ...roadViews.map((url) => ({ url }))];
      dataUrl = roadViews[primaryIndex];
    } else {
      dataUrl = await toDataUrl(photo, 2000, 0.85, true, false);
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
    const tenderP = (driveMode || coordCoverage === false || !geoP) ? null
      : geoP.then((g) => stateCodeForGeocode(g) === "KA"
          ? jurisdictionOf(lat, lng).then((w) =>
            (w && w.kind === "town" && w.lgd ? matchTender(shortOf(g), w.lgd) : null))
          : null)
          .catch(() => null);
    const sequenceNote = driveMode
      ? `\n- Capture layout: image 1 is full-frame context from the sharpest burst frame. Images 2-${imageInputs.length} are orientation-aware road-region crops in chronological order; the sharpest crop is chronological frame ${primaryIndex + 1}.`
      : "\n- Capture layout: one user-framed full image.";
    const promptVersion = driveMode ? PROMPT_VERSION : PHOTO_PROMPT_VERSION;
    const detectPrompt = DETECT_PROMPT + (driveMode ? "" : PHOTO_ONLY_PROMPT_SUFFIX)
      + sequenceNote + (LANG() === "kn"
      ? "\n- Write the description field in formal Kannada (ಕನ್ನಡ ಭಾಷೆಯಲ್ಲಿ ಬರೆಯಿರಿ)."
      : LANG() === "mr"
        ? "\n- Write the description field in clear formal Marathi (मराठी भाषेत लिहा)."
        : LANG() === "bn"
          ? "\n- Write the description field in clear formal Bengali (পরিষ্কার, প্রমিত বাংলায় লিখুন)."
        : "");
    const detectionModel = driveMode ? DRIVE_DETECTION_MODEL : S.model;
    const detectionDetail = driveMode ? DRIVE_DETECTION_DETAIL : S.detail;
    const repairCandidate = repairCandidateP ? await repairCandidateP : null;
    // Single shot has one verdict on screen, so show it the moment it streams in.
    // Drive Mode analyses run concurrently and report through the HUD instead.
    // Drive Mode has no verdict on screen to update, so it passed no callback and took
    // the unstreamed path, waiting for a description it discards on every rejected frame.
    // It streams now purely to stop as soon as the frame is known to be rejected.
    const modelAssessment = await analyzeImage(imageInputs, detectPrompt, "pothole_binary_assessment",
      ASSESS_SCHEMA, detectionModel, driveMode ? null : emitVerdict,
      driveMode && !S.debug && !repairCandidate, detectionDetail,
      driveMode ? "low" : null);
    const a = binaryAssessment(modelAssessment, driveMode, photos.length);
    const decision = decisionFor(a, driveMode, photos.length);
    const accepted = decision === "accept";
    const detector = { model: detectionModel, detail: detectionDetail, prompt_version: promptVersion,
                       schema_version: SCHEMA_VERSION, evidence_count: imageInputs.length };
    if (driveMode && !accepted) {
      // Ordinary non-detection is only the gate. Fixed requires a separate model call
      // that sees the saved before photo and the current usable revisit together.
      if (repairCandidate && clearAbsenceForRepair(a)) {
        progress(pmsg("repair"));
        const comparison = await verifyRepairCandidate(repairCandidate, contextDataUrl,
          roadViews, primaryIndex, detectionModel, detectionDetail).catch(() => null);
        const provenCondition = repairConditionFor(comparison);
        if (provenCondition) {
          if (commitTurn) await commitTurn.wait;
          const repairResult = await applyRepairObservation(repairCandidate.id, {
            ...repairObservationBase,
            ...comparison,
            // Keep the full scene, not only the orientation-aware road working crop, so a later
            // reviewer can audit that the before/after frames show the same footprint.
            current_photo_data_url: contextDataUrl,
            detection_model: detectionModel,
            image_detail: detectionDetail,
            prompt_version: REPAIR_PROMPT_VERSION,
            schema_version: REPAIR_SCHEMA_VERSION,
          });
          const applied = !repairResult.ignored ? repairResult.condition_status : null;
          return { analyzed: true, accepted: false, stored: false, found: false,
                   duplicate: false, duplicate_of: null, decision, review: false,
                   repaired: applied === "fixed", repair_review: applied === "repair_review",
                   repair_target_id: repairCandidate.id, repair_result: repairResult,
                   ...a, observation: { ...a }, repair_observation: comparison, detector };
        }
      }
      return { analyzed: true, accepted: false, stored: false, found: false,
               duplicate: false, duplicate_of: null, decision, review: false,
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
    const tenderCandidate = accepted && covered && canSearchTenderCatalog(route)
      ? await matchTenderAt(address, route, lat, lng, tenderP).catch(() => null)
      : null;
    const tender = normaliseTenderMatch(tenderCandidate, covered ? route : null);
    if (accepted) progress(pmsg("write"));
    // No authority means no complaint. The photo, verdict and location are still kept,
    // so nothing is lost if coverage later extends to this place.
    const complaint = accepted && covered
      ? buildComplaintOutputs(a, lat, lng, address, route.officer_name, tender, route, {
          captured_at: Number.isFinite(capturedAtRaw) ? capturedAtRaw / 1000 : null,
          gps_accuracy: Number.isFinite(gpsAccuracyRaw) ? gpsAccuracyRaw : null,
          photo_provenance: driveMode ? "Drive Mode camera frame" : "App camera photo",
        }) : null;
    const subject = complaint ? complaint.email_subject : null;
    const body = complaint ? complaint.email_body : null;
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
      looks_like_speed_breaker: a.looks_like_speed_breaker === true,
      damage_type: a.damage_type, assessment: a.assessment, image_quality: a.image_quality,
      defect_type: a.defect_type,
      surface_type: a.surface_type,
      measurement_provenance: a.measurement_provenance,
      measurement_confidence: a.measurement_confidence,
      measurement_length_cm: a.measurement_length_cm,
      measurement_width_cm: a.measurement_width_cm,
      measurement_depth_cm: a.measurement_depth_cm,
      on_drivable_surface: !!a.on_drivable_surface,
      has_localized_cavity: !!a.has_localized_cavity,
      has_broken_edge_or_rim: !!a.has_broken_edge_or_rim,
      has_depth_or_surface_loss: !!a.has_depth_or_surface_loss,
      temporal_consistency: a.temporal_consistency,
      size: a.size,
      decision,
      description: a.description, email_subject: subject, email_body: body,
      whatsapp_text: complaint ? complaint.whatsapp_text : null,
      portal_fields: complaint ? complaint.portal_fields : null,
      portal_copy_text: complaint ? complaint.portal_copy_text : null,
      complaint_profile_id: complaint ? complaint.complaint_profile_id : null,
      complaint_template_version: body ? COMPLAINT_TEMPLATE_VERSION : null,
      status: accepted ? (covered ? "draft" : "unrouted") : "rejected",
      condition_status: "open", condition_updated_at: null, condition_source: null,
      detection_model: detectionModel, image_detail: detectionDetail, prompt_version: promptVersion,
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
      contract_state_code: covered ? (route.contract_state_code || null) : null,
      routing_pack_id: covered ? (route.routing_pack_id || null) : null,
      routing_pack_version: covered ? (route.routing_pack_version || null) : null,
      routing_pack_sha256: covered ? (route.routing_pack_sha256 || null) : null,
      routing_pack_state_code: covered ? (route.routing_pack_state_code || null) : null,
      region: covered ? (route.region || null) : null,
      ownership_unverified: covered ? !!route.ownership_unverified : null,
      geographic_authority_id: complaint ? complaint.geographic_authority_id : null,
      geographic_authority_name: complaint ? complaint.geographic_authority_name : null,
      intake_authority_id: complaint ? complaint.intake_authority_id : null,
      intake_authority_name: complaint ? complaint.intake_authority_name : null,
      road_owner_id: complaint ? complaint.road_owner_id : null,
      road_owner_name: complaint ? complaint.road_owner_name : null,
      road_owner_status: complaint ? complaint.road_owner_status : null,
      road_owner_evidence: complaint ? complaint.road_owner_evidence : null,
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
      tender_reference_label: tender ? (tender.reference_label || "Tender number") : null,
      tender_title: tender ? tender.title : null,
      contractor: tender ? tender.contractor : null,
      tender_note: tender ? tender.note : null,
      tender_published: tender ? tender.published : null,
      tender_organisation: tender ? (tender.organisation || null) : null,
      tender_detail_url: tender ? (tender.detail_url || null) : null,
      tender_bid_closing: tender ? (tender.bid_closing || null) : null,
      tender_bid_opening: tender ? (tender.bid_opening || null) : null,
      tender_project_start: tender ? (tender.project_start || null) : null,
      tender_project_completion: tender ? (tender.project_completion || null) : null,
      tender_agreement_number: tender ? (tender.agreement_number || null) : null,
      tender_agreement_date: tender ? (tender.agreement_date || null) : null,
      tender_package_reference: tender ? (tender.package_reference || null) : null,
      tender_highway_reference: tender ? (tender.highway_reference || null) : null,
      tender_published_chainage: tender ? (tender.published_chainage || null) : null,
      tender_road_from: tender ? (tender.road_from || null) : null,
      tender_road_to: tender ? (tender.road_to || null) : null,
      tender_source_name: tender ? tender.source_name : null,
      tender_source_url: tender ? tender.source_url : null,
      tender_lifecycle: tender ? (tender.lifecycle || null) : null,
      tender_lifecycle_status: tender ? (tender.lifecycle_status || null) : null,
      tender_match_basis: tender ? (tender.match_basis || null) : null,
      tender_candidate_status: tender ? tender.candidate_status : null,
      tender_scope_status: tender ? tender.scope_status : null,
      tender_scope_verified: tender ? !!tender.scope_verified : false,
      tender_segment_status: tender ? tender.segment_status : null,
      tender_segment_verified: tender ? !!tender.segment_verified : false,
      tender_award_status: tender ? tender.award_status : null,
      tender_award_verified: tender ? !!tender.award_verified : false,
      tender_dlp_status: tender ? tender.dlp_status : null,
      tender_dlp_verified: tender ? !!tender.dlp_verified : false,
      tender_responsibility_active_verified: tender
        ? tender.responsibility_active_verified === true : false,
      tender_responsibility_valid_from: tender ? (tender.responsibility_valid_from || null) : null,
      tender_responsibility_valid_until: tender ? (tender.responsibility_valid_until || null) : null,
      tender_responsible_authority_id: tender ? (tender.responsible_authority_id || null) : null,
      tender_road_owner_id: tender ? (tender.road_owner_id || null) : null,
      tender_verification_evidence: tender ? (tender.verification_evidence || null) : null,
      tender_unambiguous: tender ? tender.unambiguous === true : false,
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
      heading: normalizedHeading,
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
        heading: normalizedHeading,
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
    const dataUrl = await toDataUrl(photo, 2000, 0.85, true, false);
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
      complaint_template_version: body ? COMPLAINT_TEMPLATE_VERSION : null,
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
      tender_title: null,
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
    rec.complaint_template_version = COMPLAINT_TEMPLATE_VERSION;
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
    const nativeIsPothole = native.is_pothole === true || Number(native.is_pothole) === 1;
    const nativeIsReportable = native.is_reportable === true || Number(native.is_reportable) === 1;
    const nativeContract = nativeDetectorContract(native);
    const nativeSize = POTHOLE_SIZES.has(native.size) ? native.size : null;
    const nativeSurface = nativeContract && nativeContract.surfaceTypes.has(native.surface_type)
      ? native.surface_type : "unknown";
    const nativeTemporal = native.temporal_consistency;
    const nativePassedBinaryGate = !!nativeContract && native.decision === "accept"
      && nativeIsPothole && nativeIsReportable && native.damage_type === "pothole_cavity"
      && native.looks_like_speed_breaker === false
      && native.image_quality === "usable" && nativeContract.surfaceTypes.has(nativeSurface)
      && native.on_drivable_surface === true
      && native.has_localized_cavity === true
      && native.has_broken_edge_or_rim === true && native.has_depth_or_surface_loss === true
      && nativeTemporal === "consistent" && Number(native.evidence_count) >= 3 && !!nativeSize;
    // Contracts older than v6, malformed current rows, and v6 rows claiming a v7+-only
    // surface are acknowledged and discarded instead of looping forever or becoming a
    // complaint. Already-synced WebView reports are never reclassified here.
    if (!nativePassedBinaryGate) {
      return { native_id: nativeId, ignored: true, reason: "obsolete_or_invalid_detector_contract" };
    }
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
    const tenderCandidate = covered && canSearchTenderCatalog(route)
      ? await matchTenderAt(address, route, lat, lng).catch(() => null)
      : null;
    const tender = normaliseTenderMatch(tenderCandidate, covered ? route : null);
    const assessment = binaryAssessment({
      // Native v6/v7/v8 saved this row only after the same binary physical gate accepted it.
      is_pothole: true,
      looks_like_speed_breaker: false,
      image_quality: native.image_quality,
      surface_type: nativeSurface,
      on_drivable_surface: true,
      has_localized_cavity: true,
      has_broken_edge_or_rim: true,
      has_depth_or_surface_loss: true,
      temporal_consistency: nativeTemporal,
      size: nativeSize,
      description: native.description || "Pothole detected during Drive Mode.",
    }, true, Math.max(2, Number(native.evidence_count) - 1));
    const complaint = covered
      ? buildComplaintOutputs(assessment, lat, lng, address, route.officer_name, tender, route, {
          captured_at: Number.isFinite(Number(native.captured_at)) ? Number(native.captured_at) : null,
          gps_accuracy: Number.isFinite(gpsAccuracy) ? gpsAccuracy : null,
          photo_provenance: "Android Drive Mode camera frame",
        }) : null;
    const subject = complaint ? complaint.email_subject : null;
    const body = complaint ? complaint.email_body : null;
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
      looks_like_speed_breaker: false,
      damage_type: assessment.damage_type, assessment: assessment.assessment,
      image_quality: assessment.image_quality,
      defect_type: assessment.defect_type,
      surface_type: assessment.surface_type,
      measurement_provenance: assessment.measurement_provenance,
      measurement_confidence: assessment.measurement_confidence,
      measurement_length_cm: assessment.measurement_length_cm,
      measurement_width_cm: assessment.measurement_width_cm,
      measurement_depth_cm: assessment.measurement_depth_cm,
      on_drivable_surface: assessment.on_drivable_surface,
      has_localized_cavity: assessment.has_localized_cavity,
      has_broken_edge_or_rim: assessment.has_broken_edge_or_rim,
      has_depth_or_surface_loss: assessment.has_depth_or_surface_loss,
      temporal_consistency: assessment.temporal_consistency,
      size: assessment.size, decision: native.decision || "accept",
      description: assessment.description, email_subject: subject, email_body: body,
      whatsapp_text: complaint ? complaint.whatsapp_text : null,
      portal_fields: complaint ? complaint.portal_fields : null,
      portal_copy_text: complaint ? complaint.portal_copy_text : null,
      complaint_profile_id: complaint ? complaint.complaint_profile_id : null,
      complaint_template_version: body ? COMPLAINT_TEMPLATE_VERSION : null,
      status: covered ? "draft" : "unrouted",
      condition_status: "open", condition_updated_at: null, condition_source: null,
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
      contract_state_code: covered ? (route.contract_state_code || null) : null,
      routing_pack_id: covered ? (route.routing_pack_id || null) : null,
      routing_pack_version: covered ? (route.routing_pack_version || null) : null,
      routing_pack_sha256: covered ? (route.routing_pack_sha256 || null) : null,
      routing_pack_state_code: covered ? (route.routing_pack_state_code || null) : null,
      region: covered ? (route.region || null) : null,
      ownership_unverified: covered ? !!route.ownership_unverified : null,
      geographic_authority_id: complaint ? complaint.geographic_authority_id : null,
      geographic_authority_name: complaint ? complaint.geographic_authority_name : null,
      intake_authority_id: complaint ? complaint.intake_authority_id : null,
      intake_authority_name: complaint ? complaint.intake_authority_name : null,
      road_owner_id: complaint ? complaint.road_owner_id : null,
      road_owner_name: complaint ? complaint.road_owner_name : null,
      road_owner_status: complaint ? complaint.road_owner_status : null,
      road_owner_evidence: complaint ? complaint.road_owner_evidence : null,
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
      tender_reference_label: tender ? (tender.reference_label || "Tender number") : null,
      tender_title: tender ? tender.title : null,
      contractor: tender ? tender.contractor : null,
      tender_note: tender ? tender.note : null,
      tender_published: tender ? tender.published : null,
      tender_organisation: tender ? (tender.organisation || null) : null,
      tender_detail_url: tender ? (tender.detail_url || null) : null,
      tender_bid_closing: tender ? (tender.bid_closing || null) : null,
      tender_bid_opening: tender ? (tender.bid_opening || null) : null,
      tender_project_start: tender ? (tender.project_start || null) : null,
      tender_project_completion: tender ? (tender.project_completion || null) : null,
      tender_agreement_number: tender ? (tender.agreement_number || null) : null,
      tender_agreement_date: tender ? (tender.agreement_date || null) : null,
      tender_package_reference: tender ? (tender.package_reference || null) : null,
      tender_highway_reference: tender ? (tender.highway_reference || null) : null,
      tender_published_chainage: tender ? (tender.published_chainage || null) : null,
      tender_road_from: tender ? (tender.road_from || null) : null,
      tender_road_to: tender ? (tender.road_to || null) : null,
      tender_source_name: tender ? tender.source_name : null,
      tender_source_url: tender ? tender.source_url : null,
      tender_lifecycle: tender ? (tender.lifecycle || null) : null,
      tender_lifecycle_status: tender ? (tender.lifecycle_status || null) : null,
      tender_match_basis: tender ? (tender.match_basis || null) : null,
      tender_candidate_status: tender ? tender.candidate_status : null,
      tender_scope_status: tender ? tender.scope_status : null,
      tender_scope_verified: tender ? !!tender.scope_verified : false,
      tender_segment_status: tender ? tender.segment_status : null,
      tender_segment_verified: tender ? !!tender.segment_verified : false,
      tender_award_status: tender ? tender.award_status : null,
      tender_award_verified: tender ? !!tender.award_verified : false,
      tender_dlp_status: tender ? tender.dlp_status : null,
      tender_dlp_verified: tender ? !!tender.dlp_verified : false,
      tender_responsibility_active_verified: tender
        ? tender.responsibility_active_verified === true : false,
      tender_responsibility_valid_from: tender ? (tender.responsibility_valid_from || null) : null,
      tender_responsibility_valid_until: tender ? (tender.responsibility_valid_until || null) : null,
      tender_responsible_authority_id: tender ? (tender.responsible_authority_id || null) : null,
      tender_road_owner_id: tender ? (tender.road_owner_id || null) : null,
      tender_verification_evidence: tender ? (tender.verification_evidence || null) : null,
      tender_unambiguous: tender ? tender.unambiguous === true : false,
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


  async function verifiedDirectEmailRoute(rec) {
    if (!rec || normaliseIssueType(rec.issue_type) !== "road_damage"
        || rec.delivery_channel !== "email" || !finiteCoord(rec.lat) || !finiteCoord(rec.lng)
        || !Number.isFinite(rec.gps_accuracy) || rec.gps_accuracy < 0
        || rec.gps_accuracy > 30) {
      throw new Error("This saved email route cannot be verified from precise road coordinates.");
    }
    const current = await routeOfficer(
      rec.address || null, rec.lat, rec.lng, rec.gps_accuracy, rec.heading, rec.speed_mps,
      rec.issue_type);
    if (!current || current.routed !== true || current.delivery_channel !== "email"
        || current.authority_id !== rec.authority_id || !current.officer_email
        || !/^ka-lgd-[0-9]+$/.test(String(current.authority_id || ""))
        || current.routing_pack_id !== "in-ka-routing"
        || current.routing_pack_state_code !== "KA"
        || current.routing_source !== "kgis"
        || current.routing_match_field !== "lgd") {
      throw new Error(
        "The current coordinate-bound authority does not match this saved email recipient. Review or recapture the report; the app will not address a guess."
      );
    }
    return current;
  }


  async function openInGmail(rec) {
    // Always the routed officer. The app never sends; the user does, in their email app.
    // No fallback recipient: an unrouted report must not borrow Bengaluru's address.
    // Preparing a native attachment is asynchronous. Re-read only after that work, then
    // launch without another await so a Drive repair gets the last possible veto.
    const attachment = NATIVE ? await photoToBase64(rec.photo_full || rec.photo) : null;
    const stored = await getReport(rec.id);
    if (!stored) throw new Error("Report not found.");
    const migrated = migrateLegacyComplaintRecord(stored);
    if (conditionStatus(migrated) === "fixed") {
      throw new Error("This pothole was verified fixed on a later drive, so its old complaint cannot be sent.");
    }
    if (migrated.status !== "draft" && migrated.status !== "queued") {
      throw new Error("This report is not a sendable draft.");
    }
    if (!migrated.officer_email) {
      throw new Error("No responsible authority is known for this location, so there is nobody to address.");
    }
    const verifiedRoute = await verifiedDirectEmailRoute(migrated);
    // Commit the migration and today's coordinate-bound recipient before launching. The
    // atomic callback rechecks repair/status after the network work above, so a concurrent
    // revisit cannot reopen a stale complaint.
    const current = await mutateReportAtomically(migrated.id, (latest) => {
      if (conditionStatus(latest) === "fixed") {
        throw new Error("This pothole was verified fixed on a later drive, so its old complaint cannot be sent.");
      }
      if (latest.status !== "draft" && latest.status !== "queued") {
        throw new Error("This report is not a sendable draft.");
      }
      for (const field of ["officer_name", "officer_email", "authority_id", "authority_name",
        "authority_registry_version", "routing_source", "routing_match_field",
        "routing_match_value", "routing_pack_id", "routing_pack_version",
        "routing_pack_sha256", "routing_pack_state_code", "region"]) {
        latest[field] = verifiedRoute[field] === undefined ? null : verifiedRoute[field];
      }
      latest.direct_email_verified_at = Date.now() / 1000;
    });
    const to = current.officer_email;
    progress(pmsg("email"));
    if (NATIVE) {
      // Vanilla-JS WebView: the injected runtime exposes plugins via Capacitor.Plugins
      // and has no registerPlugin. Support both for bundler compatibility.
      const EmailComposer = Capacitor.registerPlugin
        ? Capacitor.registerPlugin("EmailComposer")
        : Capacitor.Plugins.EmailComposer;
      await EmailComposer.open({
        to: [to],
        subject: current.email_subject || "",
        body: current.email_body || "",
        // Full capture where we kept one; the working copy is only a fallback.
        attachments: [{ type: "base64", name: `${issueFileStem(current.issue_type)}.jpg`,
                        path: attachment }],
      });
    } else {
      // A browser cannot attach the saved photo to a draft, but it can still open a
      // real addressed composer. Keep this a deliberate external handoff: the user
      // reviews and presses Send, and can add the photo or use Share evidence.
      const query = new URLSearchParams({
        subject: current.email_subject || "",
        body: current.email_body || "",
      });
      const link = document.createElement("a");
      link.href = `mailto:${encodeURIComponent(to)}?${query.toString()}`;
      link.rel = "noopener noreferrer";
      link.style.display = "none";
      document.body.appendChild(link);
      link.click();
      link.remove();
    }
    return mutateReportAtomically(current.id, (latest) => {
      if (conditionStatus(latest) === "fixed") {
        throw new Error("This pothole was verified fixed on a later drive, so its old complaint cannot be queued.");
      }
      if (latest.status !== "draft" && latest.status !== "queued") {
        throw new Error("This report is not a sendable draft.");
      }
      latest.status = "queued";
      latest.handoff_opened_at = Date.now() / 1000;
    });
  }

  const isOfficialHandoff = (rec) => !!rec && OFFICIAL_HANDOFF_CHANNELS.has(rec.delivery_channel);

  const LEGACY_TAMIL_NADU_TOP50_SHA256 =
    "0250e95980b7c801986a2bf025c82e4b8eb2745fe36dad09fc6dfb2a5a4f8bf5";
  const LEGACY_TAMIL_NADU_TOP50_REGIONS = Object.freeze({
    coimbatore: Object.freeze({
      aliases: Object.freeze(["Coimbatore", "Kovai", "கோயம்புத்தூர்"]),
      envelope: Object.freeze({
        min_lng: 76.8028425, min_lat: 10.8418115,
        max_lng: 77.1228425, max_lat: 11.1618115,
      }),
    }),
    madurai: Object.freeze({
      aliases: Object.freeze(["Madurai", "மதுரை"]),
      envelope: Object.freeze({
        min_lng: 78.0155927, min_lat: 9.8245041,
        max_lng: 78.2030091, max_lat: 9.9933722,
      }),
    }),
  });

  // v1.25 routed these two cities through the old national top-50 pack. Re-resolve that
  // exact, checksum-pinned legacy shape against the current state polygon and return only
  // freshly loaded Tamil Nadu contact data. No URL or authority label saved in IndexedDB
  // participates in the migration.
  async function migrateLegacyTamilNaduHandoff(rec) {
    const region = rec && LEGACY_TAMIL_NADU_TOP50_REGIONS[rec.region];
    const match = String(rec && rec.routing_match_value || "")
      .match(/^(city|municipality): (.+)$/);
    const aliases = region && new Set(region.aliases.map(normaliseAuthorityValue));
    if (!region || !match || !aliases.has(normaliseAuthorityValue(match[2]))
        || rec.authority_id !== "in-tn-cm-helpline"
        || rec.routing_pack_id !== "in-top50-routing"
        || rec.routing_pack_version !== 1
        || rec.routing_pack_sha256 !== LEGACY_TAMIL_NADU_TOP50_SHA256
        || rec.routing_pack_state_code !== "IN"
        || rec.routing_source !== "nominatim_structured_city"
        || rec.routing_match_field !== "structured_place"
        || !Number.isFinite(rec.lat) || !Number.isFinite(rec.lng)
        || !Number.isFinite(rec.gps_accuracy) || rec.gps_accuracy < 0
        || rec.gps_accuracy > 30
        || !accuracyCircleWithinEnvelope(
          rec.lat, rec.lng, rec.gps_accuracy, region.envelope)) {
      return null;
    }
    const current = await tamilNaduRouteFromGeocode(
      null, rec.lat, rec.lng, rec.gps_accuracy);
    if (!current || !current.routed
        || current.authority_id !== "tn-statewide-unverified"
        || current.routing_pack_id !== "in-tn-state-routing") return null;
    return routeForIssue({ ...toDict(rec), ...current }, rec.issue_type);
  }

  const LEGACY_ANDHRA_PRADESH_TOP50_SHA256 = LEGACY_TAMIL_NADU_TOP50_SHA256;
  const LEGACY_ANDHRA_PRADESH_TOP50_REGIONS = Object.freeze({
    visakhapatnam: Object.freeze({
      aliases: Object.freeze(["Visakhapatnam", "Vizag", "Waltair", "విశాఖపట్నం"]),
      envelope: Object.freeze({
        min_lng: 83.1321297, min_lat: 17.5335526,
        max_lng: 83.4521297, max_lat: 17.8535526,
      }),
    }),
    vijayawada: Object.freeze({
      aliases: Object.freeze(["Vijayawada", "Bezawada", "విజయవాడ"]),
      envelope: Object.freeze({
        min_lng: 80.4560469, min_lat: 16.3515306,
        max_lng: 80.7760469, max_lat: 16.6715306,
      }),
    }),
  });

  // Older releases routed only these two Andhra Pradesh cities through the top-50
  // pack. Re-resolve the exact legacy record against the current state polygon and
  // install fresh PGRS contact data before opening any external service.
  async function migrateLegacyAndhraPradeshHandoff(rec) {
    const region = rec && LEGACY_ANDHRA_PRADESH_TOP50_REGIONS[rec.region];
    const match = String(rec && rec.routing_match_value || "")
      .match(/^(city|municipality): (.+)$/);
    const aliases = region && new Set(region.aliases.map(normaliseAuthorityValue));
    if (!region || !match || !aliases.has(normaliseAuthorityValue(match[2]))
        || rec.authority_id !== "in-ap-puramithra"
        || rec.routing_pack_id !== "in-top50-routing"
        || rec.routing_pack_version !== 1
        || rec.routing_pack_sha256 !== LEGACY_ANDHRA_PRADESH_TOP50_SHA256
        || rec.routing_pack_state_code !== "IN"
        || rec.routing_source !== "nominatim_structured_city"
        || rec.routing_match_field !== "structured_place"
        || !Number.isFinite(rec.lat) || !Number.isFinite(rec.lng)
        || !Number.isFinite(rec.gps_accuracy) || rec.gps_accuracy < 0
        || rec.gps_accuracy > 30
        || !accuracyCircleWithinEnvelope(
          rec.lat, rec.lng, rec.gps_accuracy, region.envelope)) {
      return null;
    }
    const current = await andhraPradeshRouteFromGeocode(
      null, rec.lat, rec.lng, rec.gps_accuracy);
    if (!current || !current.routed
        || current.authority_id !== "ap-statewide-unverified"
        || current.routing_pack_id !== "in-ap-routing") return null;
    return routeForIssue({ ...toDict(rec), ...current }, rec.issue_type);
  }

  function routingPackForAuthority(authorityId, preferredPackId = null) {
    const id = String(authorityId || "");
    if (PACK_ID_BY_AUTHORITY.has(id)) {
      const installed = PACK_ID_BY_AUTHORITY.get(id);
      if (preferredPackId && installed.has(preferredPackId)) return preferredPackId;
      if (installed.size === 1) return [...installed][0];
      return null;
    }
    const match = id.match(/^([a-z]{2})-/);
    if (!match) return null;
    const stateCode = match[1].toUpperCase();
    const candidates = Object.entries(SUPPORTED_STATE_PACKS)
      .filter(([, spec]) => spec.kind === "routing" && spec.state_code === stateCode)
      .map(([packId]) => packId);
    if (preferredPackId && candidates.includes(preferredPackId)) return preferredPackId;
    return candidates.length === 1 ? candidates[0] : null;
  }

  function currentOfficialRouteBinding(packId, authorityId, pack, regionId = null) {
    const remaining = REMAINING_STATE_ROUTE_CONFIGS[packId];
    if (remaining) {
      const region = pack && pack.payload && pack.payload.region;
      if (!region || authorityId !== remaining.authority_id
          || region.id !== remaining.region_id
          || region.authority_id !== authorityId) return null;
      return { region: remaining.region_id, routing_source: remaining.routing_source };
    }
    const municipal = MUNICIPAL_CITY_CONFIGS[packId];
    if (municipal) {
      const region = pack && pack.payload && Array.isArray(pack.payload.regions)
        ? pack.payload.regions.find((item) => item && item.id === municipal.region_id) : null;
      if (!region || authorityId !== municipal.authority_id
          || region.authority_id !== authorityId) return null;
      return { region: municipal.region_id, routing_source: municipal.routing_source };
    }
    if (packId === "in-top50-routing") {
      const region = pack && pack.payload && Array.isArray(pack.payload.regions)
        ? pack.payload.regions.find((item) => item && item.id === regionId) : null;
      if (!region || region.authority_id !== authorityId
          || region.routing_source !== "nominatim_structured_city") return null;
      return { region: region.id, routing_source: region.routing_source };
    }
    if (packId === "in-pb-routing" && authorityId === "pb-statewide-unverified") {
      const region = pack && pack.payload && pack.payload.region;
      if (!region || region.id !== "punjab-state" || region.authority_id !== authorityId) return null;
      return { region: region.id, routing_source: "osm_punjab_state_boundary" };
    }
    if (packId === "in-tn-state-routing" && authorityId === "tn-statewide-unverified") {
      const region = pack && pack.payload && pack.payload.region;
      if (!region || region.id !== "tamil-nadu-state"
          || region.authority_id !== authorityId) return null;
      return { region: region.id, routing_source: "osm_tamil_nadu_state_boundary" };
    }
    if (packId === "in-ap-routing" && authorityId === "ap-statewide-unverified") {
      const region = pack && pack.payload && pack.payload.region;
      if (!region || region.id !== "andhra-pradesh-state"
          || region.authority_id !== authorityId) return null;
      return { region: region.id, routing_source: "osm_andhra_pradesh_state_boundary" };
    }
    if (packId === "in-tg-state-routing" && authorityId === "tg-statewide-unverified") {
      const region = pack && pack.payload && pack.payload.region;
      if (!region || region.id !== "telangana-state"
          || region.authority_id !== authorityId) return null;
      return { region: region.id, routing_source: "osm_telangana_state_boundary" };
    }
    if (packId === "in-ka-state-routing" && authorityId === "ka-statewide-unverified") {
      const region = pack && pack.payload && pack.payload.region;
      if (!region || region.id !== "karnataka-state"
          || region.authority_id !== authorityId) return null;
      return { region: region.id, routing_source: "osm_karnataka_state_boundary" };
    }
    if (packId === "in-kl-routing" && authorityId === "kl-statewide-unverified") {
      const region = pack && pack.payload && pack.payload.region;
      if (!region || region.id !== "kerala-state"
          || region.authority_id !== authorityId) return null;
      return { region: region.id, routing_source: "osm_kerala_state_boundary" };
    }
    if (packId === "in-up-routing" && authorityId === "up-statewide-unverified") {
      const region = pack && pack.payload && pack.payload.region;
      if (!region || region.id !== "uttar-pradesh-state"
          || region.authority_id !== authorityId) return null;
      return { region: region.id, routing_source: "osm_uttar_pradesh_state_boundary" };
    }
    if (packId === "in-cg-routing" && authorityId === "cg-statewide-unverified") {
      const region = pack && pack.payload && pack.payload.region;
      if (!region || region.id !== "chhattisgarh-state"
          || region.authority_id !== authorityId) return null;
      return { region: region.id, routing_source: "osm_chhattisgarh_state_boundary" };
    }
    if (packId === "in-rj-routing" && authorityId === "rj-statewide-unverified") {
      const region = pack && pack.payload && pack.payload.region;
      if (!region || region.id !== "rajasthan-state"
          || region.authority_id !== authorityId) return null;
      return { region: region.id, routing_source: "osm_rajasthan_state_boundary" };
    }
    if (packId === "in-ga-routing" && authorityId === "ga-statewide-unverified") {
      const region = pack && pack.payload && pack.payload.region;
      if (!region || region.id !== "goa-state"
          || region.authority_id !== authorityId) return null;
      return { region: region.id, routing_source: "osm_goa_state_boundary" };
    }
    if (packId === "in-mp-routing" && authorityId === "mp-statewide-unverified") {
      const region = pack && pack.payload && pack.payload.region;
      if (!region || region.id !== "madhya-pradesh-state"
          || region.authority_id !== authorityId) return null;
      return { region: region.id, routing_source: "osm_madhya_pradesh_state_boundary" };
    }
    if (packId === "in-br-routing" && authorityId === "br-statewide-unverified") {
      const region = pack && pack.payload && pack.payload.region;
      if (!region || region.id !== "bihar-state"
          || region.authority_id !== authorityId) return null;
      return { region: region.id, routing_source: "osm_bihar_state_boundary" };
    }
    if (packId === "in-od-routing" && authorityId === "od-statewide-unverified") {
      const region = pack && pack.payload && pack.payload.region;
      if (!region || region.id !== "odisha-state"
          || region.authority_id !== authorityId) return null;
      return { region: region.id, routing_source: "osm_odisha_state_boundary" };
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

  function savedMajorCityLocationMatches(rec, pack) {
    const region = pack && pack.payload && Array.isArray(pack.payload.regions)
      ? pack.payload.regions.find((item) => item && item.id === rec.region) : null;
    if (!region || region.authority_id !== rec.authority_id
        || rec.routing_source !== region.routing_source
        || rec.routing_match_field !== "structured_place"
        || !Number.isFinite(rec.lat) || !Number.isFinite(rec.lng)
        || !Number.isFinite(rec.gps_accuracy) || rec.gps_accuracy < 0
        || rec.gps_accuracy > 30
        || !accuracyCircleWithinEnvelope(
          rec.lat, rec.lng, rec.gps_accuracy, region.envelope)) {
      return false;
    }
    const match = String(rec.routing_match_value || "").match(/^(city|municipality): (.+)$/);
    const aliases = new Set(region.place_aliases.map(normaliseAuthorityValue));
    return !!match && aliases.has(normaliseAuthorityValue(match[2]));
  }

  function savedNonMunicipalLocationMatches(rec, packId, authorityId, pack) {
    const payload = pack && pack.payload;
    const remaining = REMAINING_STATE_ROUTE_CONFIGS[packId];
    if (remaining) {
      return authorityId === remaining.authority_id
        && savedBoundaryLocationMatches(rec,
          payload && payload.region && payload.region.geometry);
    }
    if (packId === "in-pb-routing") {
      return authorityId === "pb-statewide-unverified"
        && savedBoundaryLocationMatches(rec, payload && payload.region && payload.region.geometry);
    }
    if (packId === "in-tn-state-routing") {
      return authorityId === "tn-statewide-unverified"
        && savedBoundaryLocationMatches(rec, payload && payload.region && payload.region.geometry);
    }
    if (packId === "in-ap-routing") {
      return authorityId === "ap-statewide-unverified"
        && savedBoundaryLocationMatches(rec, payload && payload.region && payload.region.geometry);
    }
    if (packId === "in-tg-state-routing") {
      return authorityId === "tg-statewide-unverified"
        && savedBoundaryLocationMatches(rec, payload && payload.region && payload.region.geometry);
    }
    if (packId === "in-ka-state-routing") {
      return authorityId === "ka-statewide-unverified"
        && savedBoundaryLocationMatches(rec, payload && payload.region && payload.region.geometry);
    }
    if (packId === "in-kl-routing") {
      return authorityId === "kl-statewide-unverified"
        && savedBoundaryLocationMatches(rec, payload && payload.region && payload.region.geometry);
    }
    if (packId === "in-up-routing") {
      return authorityId === "up-statewide-unverified"
        && savedBoundaryLocationMatches(rec, payload && payload.region && payload.region.geometry);
    }
    if (packId === "in-cg-routing") {
      return authorityId === "cg-statewide-unverified"
        && savedBoundaryLocationMatches(rec, payload && payload.region && payload.region.geometry);
    }
    if (packId === "in-rj-routing") {
      return authorityId === "rj-statewide-unverified"
        && savedBoundaryLocationMatches(rec, payload && payload.region && payload.region.geometry);
    }
    if (packId === "in-ga-routing") {
      return authorityId === "ga-statewide-unverified"
        && savedBoundaryLocationMatches(rec, payload && payload.region && payload.region.geometry);
    }
    if (packId === "in-mp-routing") {
      return authorityId === "mp-statewide-unverified"
        && savedBoundaryLocationMatches(rec, payload && payload.region && payload.region.geometry);
    }
    if (packId === "in-br-routing") {
      return authorityId === "br-statewide-unverified"
        && savedBoundaryLocationMatches(rec, payload && payload.region && payload.region.geometry);
    }
    if (packId === "in-od-routing") {
      return authorityId === "od-statewide-unverified"
        && savedBoundaryLocationMatches(rec, payload && payload.region && payload.region.geometry);
    }
    if (packId === "in-top50-routing") {
      return savedMajorCityLocationMatches(rec, pack);
    }
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
    const binding = currentOfficialRouteBinding(packId, authorityId, pack, rec.region);
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
    const newNeutralRoute = packId === "in-pb-routing"
      || packId === "in-tn-state-routing" || packId === "in-ap-routing"
      || packId === "in-tg-state-routing" || packId === "in-ka-state-routing"
      || packId === "in-kl-routing" || packId === "in-up-routing"
      || packId === "in-cg-routing" || packId === "in-rj-routing"
      || packId === "in-ga-routing" || packId === "in-mp-routing"
      || packId === "in-br-routing" || packId === "in-od-routing"
      || packId === "in-top50-routing"
      || !!REMAINING_STATE_ROUTE_CONFIGS[packId];
    if (newNeutralRoute && present.length !== provenanceFields.length) return null;
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
      if (newNeutralRoute && (rec.routing_pack_version !== resource.pack_version
          || rec.routing_pack_sha256 !== resource.sha256)) return null;
      const digestOwner = Object.values(_statePackManifest.resources)
        .find((item) => item.sha256 === rec.routing_pack_sha256);
      if (digestOwner && digestOwner.pack_id !== packId) return null;
    }
    if (rec.region && rec.region !== binding.region) return null;
    if (newNeutralRoute && rec.region !== binding.region) return null;
    const remaining = REMAINING_STATE_ROUTE_CONFIGS[packId];
    if (remaining
        && (rec.routing_source !== remaining.routing_source
          || rec.routing_match_field !== "boundary"
          || rec.routing_match_value !== `${remaining.name} (OpenStreetMap relation ${remaining.relation_id})`)) {
      return null;
    }
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
    if (packId === "in-pb-routing"
        && (rec.routing_source !== binding.routing_source
          || rec.routing_match_field !== "boundary"
          || rec.routing_match_value !== "Punjab (OpenStreetMap relation 1942686)")) {
      return null;
    }
    if (packId === "in-tn-state-routing"
        && (rec.routing_source !== binding.routing_source
          || rec.routing_match_field !== "boundary"
          || rec.routing_match_value !== "Tamil Nadu (OpenStreetMap relation 96905)")) {
      return null;
    }
    if (packId === "in-ap-routing"
        && (rec.routing_source !== binding.routing_source
          || rec.routing_match_field !== "boundary"
          || rec.routing_match_value !== "Andhra Pradesh (OpenStreetMap relation 2022095)")) {
      return null;
    }
    if (packId === "in-tg-state-routing"
        && (rec.routing_source !== binding.routing_source
          || rec.routing_match_field !== "boundary"
          || rec.routing_match_value !== "Telangana (OpenStreetMap relation 3250963)")) {
      return null;
    }
    if (packId === "in-ka-state-routing"
        && (rec.routing_source !== binding.routing_source
          || rec.routing_match_field !== "boundary"
          || rec.routing_match_value !== "Karnataka (OpenStreetMap relation 2019939)")) {
      return null;
    }
    if (packId === "in-kl-routing"
        && (rec.routing_source !== binding.routing_source
          || rec.routing_match_field !== "boundary"
          || rec.routing_match_value !== "Kerala (OpenStreetMap relation 2018151)")) {
      return null;
    }
    if (packId === "in-up-routing"
        && (rec.routing_source !== binding.routing_source
          || rec.routing_match_field !== "boundary"
          || rec.routing_match_value !== "Uttar Pradesh (OpenStreetMap relation 1942587)")) {
      return null;
    }
    if (packId === "in-cg-routing"
        && (rec.routing_source !== binding.routing_source
          || rec.routing_match_field !== "boundary"
          || rec.routing_match_value !== "Chhattisgarh (OpenStreetMap relation 1972004)")) {
      return null;
    }
    if (packId === "in-rj-routing"
        && (rec.routing_source !== binding.routing_source
          || rec.routing_match_field !== "boundary"
          || rec.routing_match_value !== "Rajasthan (OpenStreetMap relation 1942920)")) {
      return null;
    }
    if (packId === "in-ga-routing"
        && (rec.routing_source !== binding.routing_source
          || rec.routing_match_field !== "boundary"
          || rec.routing_match_value !== "Goa (OpenStreetMap relation 11251493)")) {
      return null;
    }
    if (packId === "in-mp-routing"
        && (rec.routing_source !== binding.routing_source
          || rec.routing_match_field !== "boundary"
          || rec.routing_match_value !== "Madhya Pradesh (OpenStreetMap relation 1950071)")) {
      return null;
    }
    if (packId === "in-br-routing"
        && (rec.routing_source !== binding.routing_source
          || rec.routing_match_field !== "boundary"
          || rec.routing_match_value !== "Bihar (OpenStreetMap relation 1958982)")) {
      return null;
    }
    if (packId === "in-od-routing"
        && (rec.routing_source !== binding.routing_source
          || rec.routing_match_field !== "boundary"
          || rec.routing_match_value !== "Odisha (OpenStreetMap relation 1984022)")) {
      return null;
    }
    if (packId === "in-top50-routing"
        && (rec.routing_source !== binding.routing_source
          || rec.routing_match_field !== "structured_place")) return null;
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
    "routing_match_value", "highway_ref", "contract_state_code",
    "ownership_unverified", "tender_eligible",
    "geographic_authority_id", "geographic_authority_name",
    "intake_authority_id", "intake_authority_name",
    "road_owner_id", "road_owner_name", "road_owner_status", "road_owner_evidence",
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
    return {
      ...toDict(rec), ...current,
      contract_state_code: rec.contract_state_code || null,
      tender_eligible: !!rec.contract_state_code,
    };
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
      ownership_unverified: true,
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
    if (conditionStatus(rec) === "fixed") {
      throw new Error("This pothole was verified fixed on a later drive, so its old handoff cannot be opened.");
    }
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
    if (authorityId === "in-tn-cm-helpline") {
      const migrated = await migrateLegacyTamilNaduHandoff(rec);
      if (!migrated) {
        throw new Error("This saved Tamil Nadu report could not be safely upgraded to the current state route.");
      }
      return migrated;
    }
    if (authorityId === "in-ap-puramithra") {
      const migrated = await migrateLegacyAndhraPradeshHandoff(rec);
      if (!migrated) {
        throw new Error("This saved Andhra Pradesh report could not be safely upgraded to the current state route.");
      }
      return migrated;
    }
    const packId = routingPackForAuthority(authorityId, rec.routing_pack_id || null);
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
      ownership_unverified: true,
      tender_eligible: false,
      ...statePackProvenance(packId),
    }, rec.issue_type);
    if (!verified.handoff_url || !String(verified.handoff_url).startsWith("https://")) {
      throw new Error("The verified official handoff for this saved report is unavailable.");
    }
    return verified;
  }

  async function refreshAndPersistOfficialHandoff(rec) {
    if (conditionStatus(rec) === "fixed") {
      throw new Error("This pothole was verified fixed on a later drive, so its old handoff cannot be refreshed.");
    }
    const verified = await openOfficialHandoff(rec);
    return mutateReportAtomically(rec.id, (current) => {
      if (conditionStatus(current) === "fixed") {
        throw new Error("This pothole was verified fixed on a later drive, so its old handoff cannot be refreshed.");
      }
      applyVerifiedHandoff(current, verified);
    });
  }

  async function evidenceForReport(rec) {
    if (conditionStatus(rec) === "fixed") {
      throw new Error("This pothole was verified fixed on a later drive, so its old complaint evidence is archival only.");
    }
    if (!rec || !ACCEPTED_REPORT_STATUSES.has(rec.status)) {
      throw new Error("Only an accepted report has shareable evidence.");
    }
    rec = migrateLegacyComplaintRecord(rec);
    const source = await dataUrlToBlob(rec.photo_full || rec.photo);
    const wideUrl = await toDataUrl(source, 1280, 0.86, false, false);
    const cropUrl = rec.capture_source && !isManualCaptureSource(rec.capture_source)
      ? await toDataUrl(source, 1280, 0.86, false, true) : null;
    const base64 = wideUrl && wideUrl.split(",")[1];
    const cropBase64 = cropUrl && cropUrl.split(",")[1];
    if (!base64) throw new Error("The report photo could not be read.");
    const safeId = String(rec.id || "report").replace(/[^a-zA-Z0-9_-]/g, "");
    const recordedAt = Number.isFinite(rec.captured_at) ? rec.captured_at : rec.created_at;
    const captured = new Date(recordedAt * 1000);
    const when = Number.isNaN(captured.getTime()) ? "" : captured.toLocaleString("en-IN", {
      timeZone: "Asia/Kolkata", dateStyle: "medium", timeStyle: "medium",
    });
    const issueStem = issueFileStem(rec.issue_type);
    const evidenceBits = [
      when ? `${rec.capture_source === "manual_import" ? "Selected photo file date"
        : Number.isFinite(rec.captured_at) ? "Captured" : "Report created"} (IST): ${when}` : "",
      rec.capture_source === "manual_import"
        ? "Photo provenance: selected/imported by the user; original capture time unknown"
        : rec.capture_source === "manual_camera"
          ? "Photo provenance: app camera" : "",
      Number.isFinite(rec.gps_accuracy) ? `GPS accuracy: ±${Math.round(rec.gps_accuracy)} m` : "",
      rec.official_grievance_id
        ? `User-entered grievance/reference ID: ${rec.official_grievance_id}` : "",
    ].filter(Boolean);
    const evidenceLine = evidenceBits.length ? `Evidence: ${evidenceBits.join("; ")}.` : "";
    const bodyBlocks = String(rec.email_body || "").split(/\n{2,}/);
    const hasFooter = bodyBlocks.length > 1
      && /Pothole Reporter/.test(bodyBlocks[bodyBlocks.length - 1]);
    const finalParagraph = hasFooter ? bodyBlocks.pop() : null;
    const evidenceIndex = Math.max(0, bodyBlocks.length - 2);
    if (evidenceLine) bodyBlocks.splice(evidenceIndex, 0, evidenceLine);
    if (finalParagraph) bodyBlocks.push(finalParagraph);
    const bodyWithEvidence = bodyBlocks.filter(Boolean).join("\n\n");
    const meta = [
      rec.email_subject || `${civicIssueName(rec.issue_type)} report`,
      bodyWithEvidence,
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
          is_pothole: !!r.is_pothole,
          is_reportable: r.is_reportable == null ? !!r.is_pothole : !!r.is_reportable,
          damage_type: damageTypeOf(r), assessment: assessmentOf(r),
          image_quality: r.image_quality || null,
          defect_type: r.defect_type || (r.is_pothole ? "pothole" : "not_pothole"),
          surface_type: r.surface_type || "unknown",
          measurement_provenance: r.measurement_provenance || null,
          measurement_confidence: r.measurement_confidence || null,
          measurement_length_cm: r.measurement_length_cm == null ? null : r.measurement_length_cm,
          measurement_width_cm: r.measurement_width_cm == null ? null : r.measurement_width_cm,
          measurement_depth_cm: r.measurement_depth_cm == null ? null : r.measurement_depth_cm,
          on_drivable_surface: r.on_drivable_surface == null ? null : !!r.on_drivable_surface,
          has_localized_cavity: r.has_localized_cavity == null ? null : !!r.has_localized_cavity,
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
      const reports = (await allReports()).sort((a, b) => b.id - a.id);
      return (await migrateLegacyComplaintDrafts(reports)).map(listDict);
    }
    if (path === "/api/repair-targets" && method === "GET") {
      return { target_ids: await getRepairTargetIds() };
    }
    if (path === "/api/repair-targets" && method === "POST") {
      const body = JSON.parse((opts && opts.body) || "{}");
      return { targets: await getRepairTargetBatch(body.ids) };
    }
    if (path === "/api/native-repair" && method === "POST") {
      const raw = JSON.parse(opts.body || "{}");
      const finiteNumber = (value) => value !== null && value !== "" && Number.isFinite(Number(value))
        ? Number(value) : null;
      const observation = {
        source_event_key: raw.source_event_key,
        observed_at: finiteNumber(raw.observed_at),
        drive_id: raw.drive_id == null ? null : String(raw.drive_id),
        capture_source: "drive_live",
        debug_capture: false,
        lat: finiteNumber(raw.lat),
        lng: finiteNumber(raw.lng),
        gps_accuracy: finiteNumber(raw.gps_accuracy),
        speed_mps: finiteNumber(raw.speed_mps),
        heading: finiteNumber(raw.heading),
        current_photo_data_url: raw.current_photo_data_url,
        current_condition: raw.current_condition,
        assessment: raw.assessment,
        image_quality: raw.image_quality,
        same_location_visible: raw.same_location_visible === true,
        completed_repair_visible: raw.completed_repair_visible === true,
        description: raw.description,
        detection_model: raw.detection_model,
        image_detail: raw.image_detail,
        prompt_version: raw.prompt_version,
        // Provenance is an internal native result contract, not user input to coerce.
        // applyRepairObservation validates these exact values and never fills them in.
        schema_version: raw.schema_version,
      };
      return applyRepairObservation(raw.target_report_id, observation);
    }
    if (path === "/api/reports" && method === "DELETE") {
      await clearAllStoredRecords();
      if (!(await allStoredRecordsAreEmpty())) {
        throw new Error("Some saved reports, media, drives, or routing packs remain on this device.");
      }
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
      const want = JSON.parse(opts.body).label;
      if (!["pothole_cavity", "failed_patch", "surface_breakup", "rut_or_depression",
            "other_road_damage", "not_reportable", "pothole", "not_pothole", null].includes(want)) {
        throw new Error("Bad label.");
      }
      return mutateReportAtomically(m[1], (rec) => { rec.human_label = want; });
    }
    if ((m = path.match(/^\/api\/reports\/(\d+)\/condition$/)) && method === "POST") {
      const requested = String(JSON.parse(opts.body || "{}").condition_status || "");
      return mutateReportAtomically(m[1], (rec) => {
        if (requested === "fixed") {
          if (conditionStatus(rec) !== "repair_review"
              || rec.repair_current_condition !== "repaired"
              || rec.repair_same_location_visible !== true
              || rec.repair_completed_visible !== true
              || rec.repair_image_quality !== "usable" || !rec.repair_photo) {
            throw new Error("This revisit does not contain enough before-and-after evidence to mark the pothole fixed.");
          }
          rec.condition_status = "fixed";
          rec.condition_updated_at = Date.now() / 1000;
          rec.condition_source = "user_confirmed_revisit";
        } else if (requested === "open") {
          if (conditionStatus(rec) === "open") return;
          rec.condition_status = "open";
          rec.condition_updated_at = Date.now() / 1000;
          rec.condition_source = "user_reopened";
        } else {
          throw new Error("Condition must be fixed or open.");
        }
      });
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
      if (conditionStatus(rec) === "fixed") {
        throw new Error("This pothole was verified fixed on a later drive, so its old complaint cannot be sent.");
      }
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
          outside_area: "This civic issue is outside India's verified State/UT boundaries, or exact routing data is unavailable, so the app has no verified authority to open.",
        };
        const roadErrors = {
          no_location: "This report has no location, so there is no way to tell which office is responsible. Retake it with location switched on.",
          location_uncertain: "The GPS fix is too imprecise to choose an authority safely. Retake it with a fresh, more accurate location.",
          road_class_unknown: "The app could not check whether this road is a national highway, and it will not name a city officer for a road that may not be theirs. Try again when you have a signal.",
          national_highway: "This stretch is a national highway. It is maintained by NHAI or the state PWD National Highways division, not by the city or town body, so there is no municipal officer to address.",
          rural_road: "This road is outside every town boundary, so it belongs to the state PWD or a panchayat rather than a city body. The app will not guess an office.",
          no_address_for_body: "This town's body is known, but no official email address for it has been published, so there is no verified recipient to address.",
          jurisdiction_unavailable: "The required verified routing data could not be downloaded or read. Check the connection and try again; the app will not guess an authority.",
          outside_area: "This road damage is outside India's verified State/UT boundaries and mapped National Highways, or exact routing data is unavailable, so there is no verified authority to address.",
        };
        throw new Error((civic ? civicErrors : roadErrors)[rec.unrouted_reason]
          || "This report could not be routed to a responsible office, so there is nothing to send.");
      }
      // "queued" stays reopenable: canceling the external composer/app must not strand
      // the report or falsely mark it submitted.
      if (rec.status !== "draft" && rec.status !== "queued") throw new Error("This report is not a sendable draft.");
      if (!isOfficialHandoff(rec)) return openInGmail(rec);
      const verified = await openOfficialHandoff(rec);
      // Route verification can fetch packs and boundaries. Re-read after it, immediately
      // before the UI receives a URL it can launch.
      const current = await getReport(rec.id);
      if (!current || conditionStatus(current) === "fixed") {
        throw new Error("This pothole was verified fixed on a later drive, so its old complaint cannot be sent.");
      }
      if (current.status !== "draft" && current.status !== "queued") {
        throw new Error("This report is not a sendable draft.");
      }
      return verified;
    }
    if ((m = path.match(/^\/api\/reports\/(\d+)\/submitted$/)) && method === "POST") {
      const body = JSON.parse(opts.body || "{}");
      const reference = String(body.official_grievance_id || "").trim().slice(0, 100);
      return mutateReportAtomically(m[1], (rec) => {
        if (conditionStatus(rec) === "fixed") {
          throw new Error("This pothole was verified fixed on a later drive, so it cannot be marked as a new submission.");
        }
        if (rec.status !== "draft" && rec.status !== "queued" && rec.status !== "sent") {
          throw new Error("This report cannot be marked submitted.");
        }
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
      });
    }
    if ((m = path.match(/^\/api\/reports\/(\d+)\/handoff-opened$/)) && method === "POST") {
      const rec = await getReport(m[1]);
      if (!rec) throw new Error("Report not found.");
      if (conditionStatus(rec) === "fixed") {
        throw new Error("This pothole was verified fixed on a later drive, so its old handoff cannot be opened.");
      }
      if (!isOfficialHandoff(rec)) throw new Error("This report has no official app or portal handoff.");
      if (rec.status !== "draft" && rec.status !== "queued") {
        throw new Error("This report cannot record an official handoff.");
      }
      const verified = await openOfficialHandoff(rec);
      return mutateReportAtomically(rec.id, (current) => {
        if (conditionStatus(current) === "fixed") {
          throw new Error("This pothole was verified fixed on a later drive, so its old handoff cannot be opened.");
        }
        if (!isOfficialHandoff(current)) {
          throw new Error("This report has no official app or portal handoff.");
        }
        if (current.status !== "draft" && current.status !== "queued") {
          throw new Error("This report cannot record an official handoff.");
        }
        applyVerifiedHandoff(current, verified);
        current.status = "queued";
        current.handoff_opened_at = Date.now() / 1000;
      });
    }
    if ((m = path.match(/^\/api\/reports\/(\d+)$/))) {
      const rec = await getReport(m[1]);
      if (!rec) throw new Error("Report not found.");
      if (method === "PATCH") {
        const upd = JSON.parse(opts.body);
        return mutateReportAtomically(rec.id, (current) => {
          if (conditionStatus(current) === "fixed") throw new Error("Fixed reports cannot be edited for sending.");
          if (current.status !== "draft" && current.status !== "queued") throw new Error("Only drafts can be edited.");
          current.email_subject = upd.email_subject;
          current.email_body = complaintBodyWithFooter(upd.email_body, current.issue_type);
          current.complaint_template_version = COMPLAINT_TEMPLATE_VERSION;
        });
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
      let App = Capacitor.registerPlugin ? Capacitor.registerPlugin("App") : null;
      if (!App || !App.addListener) App = Capacitor.Plugins && Capacitor.Plugins.App;
      if (App && App.addListener) {
        App.addListener("backButton", () => {
          if (!(window.handleAppBack && window.handleAppBack())) App.exitApp();
        });
        App.addListener("appStateChange", (state) => {
          if (window.handleNativeAppStateChange) {
            window.handleNativeAppStateChange(!!(state && state.isActive));
          }
        });
      }
    } catch (e) {}
  }

  // Pure helpers, exposed for tests. These are references, not copies: a test exercises
  // exactly the code that runs in production. Nothing here holds state or a secret.
  const __pure = { inCoverage, peekVerdict, peekReject, rejectedVerdict, decisionFor,
                   binaryAssessment,
                   nativeDetectorContract,
                   damageTypeOf, assessmentOf, normaliseModel, normaliseDetail,
                   DRIVE_DETECTION_MODEL, DRIVE_DETECTION_DETAIL,
                   normaliseIssueType, civicIssueName, issueFileStem,
                   buildDetectionRequest, ASSESS_SCHEMA, DETECT_PROMPT, PROMPT_VERSION,
                   PHOTO_ONLY_PROMPT_SUFFIX, PHOTO_PROMPT_VERSION,
                   REPAIR_SCHEMA, REPAIR_PROMPT, REPAIR_PROMPT_VERSION,
                   REPAIR_SCHEMA_VERSION, clearAbsenceForRepair, repairConditionFor,
                   SCHEMA_VERSION, MAX_DETECTION_IMAGES, ROAD_REGION_RATIOS,
                   selectRoadRegion, averageLuminance,
                   detectionEnhancementPlan, applyDetectionEnhancement,
                   distMeters, roadEventMatch, sameRoadEvent, repairTargetMatch,
                   findRepairCandidateFromReports, findDuplicateReport,
                   draftEmail, buildComplaintOutputs, complaintOutputsForRecord,
                   authorityComplaintProfile, separateRoadResponsibility,
                   verifiedBdaResponsibility, normaliseTenderMatch, verifiedContractForComplaint,
                   officialIndianPublicRecordUrl, migrateLegacyComplaintRecord,
                   complaintBodyWithFooter,
                   COMPLAINT_TEMPLATE_VERSION, dataUrlToBlob, blobToDataUrl,
                   photoToBase64, toDict, listDict,
                   contractVerificationFor, tenderCoversCarriageway, shortlistFor, matchTenderFor: matchTender,
                   highwayContractCandidates, candidateLeadIsUnambiguous,
                   matchHighwayContract, matchTenderForRoute,
                   roadNoticeCandidates, matchRoadNotice, canSearchTenderCatalog,
                   roadAgreementCandidates, matchRoadAgreement,
                   matchTenderAt, stateCodeForGeocode, exactPinnedContractStateCode,
                   outerStateBoundaryGeometry, trustedContractStateCode,
                   optionalCatalogResult, startLowerCatalogMatches,
                   preferredLowerCatalogMatch, OPTIONAL_CATALOG_TIMEOUT_MS,
                   mumbaiWardFromName, mumbaiFromGeocode, evidenceForReport,
                   normaliseAuthorityValue, validateAuthorityRegistry,
                   validateOfficialHandoffRegistry,
                   matchedMmrAuthorities, containingMmrAuthorities, bmcWardFromBoundary,
                   pointInGeometry, geometryBoundaryDistanceMeters,
                   validMmrAuthorityBoundaries,
                   validateStatePackManifest, getStatePackManifest, resolvePackUrl,
                   loadStatePack, pruneStatePacks, resetStatePackMemory,
                   sha256Bytes, statePackProvenance,
                   validateContractPackManifest, getContractPackManifest,
                   validateHighwayContractPack, loadHighwayContractPack,
                   contractPackProvenance, resetContractPackMemory,
                   validateRoadNoticeManifest, getRoadNoticeManifest,
                   validateRoadNoticePack, loadRoadNoticePack,
                   roadNoticePackProvenance, resetRoadNoticePackMemory,
                   validateRoadAgreementManifest, getRoadAgreementManifest,
                   validateRoadAgreementPack, loadRoadAgreementPack,
                   roadAgreementPackProvenance, resetRoadAgreementPackMemory,
                   catalogResourceWithinReview,
                   validateHighwayManifest, getHighwayPackManifest, loadHighwayTile,
                   validateHighwayTile, highwayTileIdFor, matchHighwayTile,
                   nationalHighwayRoute, highwayPackProvenance, openNationalHighwayHandoff,
                   maharashtraCoverage,
                   delhiCoverage, delhiRouteFromGeocode, inDelhiEnvelope,
                   westBengalCoverage, kolkataCoverage, kolkataRouteFromGeocode,
                   isWestBengalGeocode, inWestBengalRoutingEnvelope,
                   punjabCoverage, punjabRouteFromGeocode,
                   tamilNaduCoverage, tamilNaduRouteFromGeocode,
                   andhraPradeshCoverage, andhraPradeshRouteFromGeocode,
                   telanganaCoverage, telanganaRouteFromGeocode,
                   karnatakaStateCoverage, karnatakaStateRouteFromGeocode,
                   keralaCoverage, keralaRouteFromGeocode,
                   uttarPradeshCoverage, uttarPradeshRouteFromGeocode,
                   chhattisgarhCoverage, chhattisgarhRouteFromGeocode,
                   rajasthanCoverage, rajasthanRouteFromGeocode,
                   goaCoverage, goaRouteFromGeocode,
                   madhyaPradeshCoverage, madhyaPradeshRouteFromGeocode,
                   biharCoverage, biharRouteFromGeocode,
                   odishaCoverage, odishaRouteFromGeocode,
                   remainingStateCoverage, remainingStateRouteFromGeocode,
                   majorCityCoverage, majorCityRouteFromGeocode,
                   inMajorCityCandidateEnvelope,
                   municipalCityCoverage, municipalCityRouteFromGeocode,
                   gpsAccuracyEnvelope, accuracyCircleWithinEnvelope,
                   officialArcGisCount, officialPointRegionMatch,
                   savedMunicipalLocationMatches, savedMajorCityLocationMatches,
                   savedOfficialRouteBinding, currentOfficialRouteBinding,
                   validatePunjabPayload, validateTamilNaduPayload,
                   validateAndhraPradeshPayload, validateTelanganaPayload,
                   validateKarnatakaStatePayload, validateKeralaPayload,
                   validateUttarPradeshPayload, validateChhattisgarhPayload,
                   validateRajasthanPayload,
                   validateGoaPayload, validateMadhyaPradeshPayload,
                   validateBiharPayload, validateOdishaPayload,
                   validatePinnedStatewidePayload,
                   validateMajorCityPayload,
                   validateMunicipalCityPayload, MUNICIPAL_CITY_CONFIGS,
                   maharashtraRouteFromGeocode, inMaharashtraRoutingEnvelope,
                   isKarnatakaGeocode, inKarnatakaRoutingEnvelope, routeOfficer, routeForIssue,
                   MMR_AUTHORITIES, PMC_AUTHORITY, MMR_FALLBACK_AUTHORITY,
                   MAHARASHTRA_STATE_AUTHORITY, KMC_AUTHORITY,
                   WEST_BENGAL_STATE_AUTHORITY,
                   PUNJAB_STATE_AUTHORITY,
                   TAMIL_NADU_STATE_AUTHORITY,
                   ANDHRA_PRADESH_STATE_AUTHORITY,
                   TELANGANA_STATE_AUTHORITY,
                   KARNATAKA_STATE_AUTHORITY,
                   KERALA_STATE_AUTHORITY,
                   UTTAR_PRADESH_STATE_AUTHORITY,
                   CHHATTISGARH_STATE_AUTHORITY,
                   RAJASTHAN_STATE_AUTHORITY,
                   GOA_STATE_AUTHORITY,
                   MADHYA_PRADESH_STATE_AUTHORITY,
                   BIHAR_STATE_AUTHORITY,
                   ODISHA_STATE_AUTHORITY,
                   REMAINING_STATE_AUTHORITIES, REMAINING_STATE_ROUTE_CONFIGS,
                   DELHI_PWD_AUTHORITY, OFFICIAL_AUTHORITIES,
                   NATIONAL_HIGHWAY_AUTHORITY,
                   DELHI_GEOMETRY_SHA256, KMC_GEOMETRY_SHA256,
                   WEST_BENGAL_STATE_GEOMETRY_SHA256,
                   PUNJAB_STATE_GEOMETRY_SHA256,
                   TAMIL_NADU_STATE_GEOMETRY_SHA256,
                   ANDHRA_PRADESH_STATE_GEOMETRY_SHA256,
                   TELANGANA_STATE_GEOMETRY_SHA256,
                   KARNATAKA_STATE_GEOMETRY_SHA256,
                   KERALA_STATE_GEOMETRY_SHA256,
                   UTTAR_PRADESH_STATE_GEOMETRY_SHA256,
                   CHHATTISGARH_STATE_GEOMETRY_SHA256,
                   RAJASTHAN_STATE_GEOMETRY_SHA256,
                   GOA_STATE_GEOMETRY_SHA256,
                   MADHYA_PRADESH_STATE_GEOMETRY_SHA256,
                   BIHAR_STATE_GEOMETRY_SHA256,
                   ODISHA_STATE_GEOMETRY_SHA256,
                   MAHARASHTRA_STATE_GEOMETRY_SHA256,
                   AUTHORITY_REGISTRY_VERSION, ISSUE_TYPES, CIVIC_HANDOFF_OVERRIDES,
                   BENGALURU_HANDOFF, BENGALURU_AUTHORITY_NAMES,
                   GENERAL_CIVIC_AUTHORITY_IDS,
                   migrateLegacyTamilNaduHandoff, LEGACY_TAMIL_NADU_TOP50_SHA256,
                   migrateLegacyAndhraPradeshHandoff,
                   LEGACY_ANDHRA_PRADESH_TOP50_SHA256 };

  window.StandaloneAPI = { __pure, handle, prewarm };

  // The home screen remains usable without an AI key because Garbage and Manhole are
  // explicit user reports. Pothole and Drive open Settings when their key is missing.
})();
