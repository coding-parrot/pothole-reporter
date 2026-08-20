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

  const LANG = () => (localStorage.getItem("app_lang") === "kn" ? "kn" : "en");
  const PROGRESS = {
    en: { compress: "Preparing photo...", capture: "Preparing road views...",
          detect: "AI checking for reportable road damage...", finalize: "Finalizing address and contract...",
          write: "Writing the complaint...", email: "Opening your email app..." },
    kn: { compress: "ಫೋಟೋ ಸಂಕುಚಿಸಲಾಗುತ್ತಿದೆ...", capture: "ಫ್ರೇಮ್ ಸೆರೆಹಿಡಿಯಲಾಗುತ್ತಿದೆ...",
          detect: "AI ವರದಿ ಮಾಡಬಹುದಾದ ರಸ್ತೆ ಹಾನಿ ಪರಿಶೀಲಿಸುತ್ತಿದೆ...", finalize: "ವಿಳಾಸ ಮತ್ತು ಗುತ್ತಿಗೆ ಖಚಿತಪಡಿಸಲಾಗುತ್ತಿದೆ...",
          write: "ದೂರು ಬರೆಯಲಾಗುತ್ತಿದೆ...", email: "ನಿಮ್ಮ ಇಮೇಲ್ ಆ್ಯಪ್ ತೆರೆಯಲಾಗುತ್ತಿದೆ..." },
  };
  const pmsg = (k) => (PROGRESS[LANG()] && PROGRESS[LANG()][k]) || PROGRESS.en[k];

  const DEFAULT_MODEL = "gpt-5-mini";
  const ALLOWED_MODELS = new Set([DEFAULT_MODEL, "gpt-5.6"]);
  const ALLOWED_DETAILS = new Set(["high", "original"]);
  const PROMPT_VERSION = "road-damage-v3";
  const SCHEMA_VERSION = 3;
  const MAX_DETECTION_IMAGES = 4;

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
    reasoning: { effort: body && body.model === "gpt-5.6" ? "none" : "minimal" },
    ...body,
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
  async function reverseGeocode(lat, lng) {
    try {
      const res = await fetchWithTimeout(
        `https://nominatim.openstreetmap.org/reverse?lat=${lat}&lon=${lng}&format=jsonv2&zoom=17&addressdetails=1`,
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
      return { short: parts.join(", ") || d.display_name || null, full: d.display_name || null };
    } catch (e) { return null; }
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

  // Resolves the officer to address, or [null, null] with a reason. Every path that
  // cannot name a real body returns nothing rather than a plausible-looking guess.
  // One GIS answer per location, shared by contract lookup and officer routing. Both
  // need it, and asking twice would double the latency of the one network call on the
  // critical path.
  let _jurKey = null, _jurP = null;
  function jurisdictionOf(lat, lng) {
    const key = `${lat},${lng}`;
    if (_jurKey !== key) { _jurKey = key; _jurP = kgisJurisdiction(lat, lng); }
    return _jurP;
  }

  async function routeOfficer(address, lat, lng) {
    const registry = await bodies();


    if (lat == null || lng == null || Number.isNaN(lat) || Number.isNaN(lng)) {
      return [null, null, "no_location"];
    }

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
    catch (e) { return [null, null, "road_class_unknown"]; }

    if (where.kind === "outside_state") return [null, null, "outside_area"];
    if (where.kind === "national_highway") return [null, null, "national_highway", where.name];
    if (where.kind === "road_class_unknown") return [null, null, "road_class_unknown"];
    if (where.kind === "rural") return [null, null, "rural_road", where.name];

    const entry = where.lgd && registry[where.lgd];
    if (!entry || !entry.email) return [null, null, "no_address_for_body", where.name];
    const title = entry.officer || OFFICER_TITLES[entry.type || where.type] || "Chief Officer";
    return [`${title}, ${entry.name}${entry.short ? ` (${entry.short})` : ""}`, entry.email, null];
  }

  function distMeters(lat1, lng1, lat2, lng2) {
    const rad = Math.PI / 180;
    const dLat = (lat2 - lat1) * rad, dLng = (lng2 - lng1) * rad;
    const a = Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(dLng / 2) ** 2;
    return 2 * 6371000 * Math.asin(Math.sqrt(a));
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

  // ---------- drafting (English / Kannada) ----------
  function damageTypeOf(value) {
    if (value && value.damage_type) return value.damage_type;
    return value && value.is_pothole ? "pothole_cavity" : "none";
  }

  function assessmentOf(value) {
    if (value && value.assessment) return value.assessment;
    if (value && value.is_pothole) return "clear";
    return "absent";
  }

  function draftEmail(a, lat, lng, address, officerName, tender) {
    const kn = LANG() === "kn";
    const sizeName = (s) => (kn ? ({ small: "ಸಣ್ಣ", medium: "ಮಧ್ಯಮ", large: "ದೊಡ್ಡ" })[s] || s : s);
    const size = a.size ? sizeName(a.size) : (kn ? "ಗಾತ್ರ ನಿರ್ಧರಿಸದ" : "unclassified");
    const road = address ? address.split(",")[0].trim() : null;
    const type = damageTypeOf(a);
    const typeNames = kn ? {
      pothole_cavity: "ರಸ್ತೆ ಗುಂಡಿ", failed_patch: "ವಿಫಲವಾದ ರಸ್ತೆ ದುರಸ್ತಿ",
      surface_breakup: "ಹಾಳಾದ ರಸ್ತೆ ಮೇಲ್ಮೈ", rut_or_depression: "ರಸ್ತೆ ಕುಸಿತ",
      other_road_damage: "ರಸ್ತೆ ಹಾನಿ", none: "ರಸ್ತೆ ಹಾನಿ",
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
        : `Location: ${address || "see coordinates below"}\nCoordinates: ${la}, ${ln}\nMap link: https://maps.google.com/?q=${la},${ln}`;
    } else {
      locLines = kn
        ? "ಸ್ಥಳ: ಸ್ವಯಂಚಾಲಿತವಾಗಿ ನಿರ್ಧರಿಸಲಾಗಲಿಲ್ಲ. ದಯವಿಟ್ಟು ಲಗತ್ತಿಸಿದ ಫೋಟೋ ನೋಡಿ."
        : "Location: could not be determined automatically. Please see the attached photo for landmarks.";
    }

    const subject = kn
      ? `${typeName} ದೂರು` + (type === "pothole_cavity" ? `: ${size}` : "") + (road ? ` (${road})` : "")
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
      : [
          `Dear ${officerName || "Sir or Madam"},`,
          `I would like to report a ${typeName} that needs repair.`,
          `${locLines}\nDamage type: ${typeName}${a.size ? `\nApproximate size: ${size}` : ""}`,
          "PFA image. This road damage poses a danger to two wheeler riders and other road users. I request your office to inspect and repair it at the earliest, and to route it to the contractor responsible if this road section is still under a maintenance warranty.",
        ];

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
      } else {
        paras.push(`Public procurement records indicate this road stretch probably falls under tender ${tender.tender_number} ("${title}"), published on ${tender.published}${tender.contractor ? `, with ${tender.contractor} recorded as the winning bidder` : ", with no winning bidder recorded"}, and it may still be ${tender.warranty}.`);
        paras.push("If the defect liability or maintenance period is in force, I request that the repair be carried out by the contractor at no additional cost to the corporation. This is a probable record match; kindly verify against the tender documents.");
      }
    }

    paras.push(kn ? "ನಿಮ್ಮ ಸೇವೆಗೆ ಧನ್ಯವಾದಗಳು." : "Thank you for your service.");
    paras.push(kn ? `ವಂದನೆಗಳು,\n${S.name}` : `Regards,\n${S.name}`);
    return [subject, paras.join("\n\n")];
  }

  // ---------- storage (IndexedDB) ----------
  let _db = null;
  function idb() {
    return new Promise((resolve, reject) => {
      if (_db) return resolve(_db);
      const req = indexedDB.open("potholes", 3);
      req.onupgradeneeded = () => {
        const d = req.result;
        if (!d.objectStoreNames.contains("reports")) d.createObjectStore("reports", { keyPath: "id", autoIncrement: true });
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
  const putFootage = (seg) => op("readwrite", (s) => s.put(seg), "footage");
  const footageFor = (driveId) => op("readonly", (s) => s.index("by_drive").getAll(String(driveId)), "footage");
  const allFootage = () => op("readonly", (s) => s.getAll(), "footage");
  const putDrive = (d) => op("readwrite", (s) => s.put(d), "drives");

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
    const capturedAtRaw = parseInt(fd.get("captured_at_ms"), 10);
    const gpsAccuracyRaw = parseFloat(fd.get("gps_accuracy"));
    const speedRaw = parseFloat(fd.get("speed"));
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
    if (driveMode && !accepted) {
      return { found: false, decision, review: decision === "review", ...a,
               detector: { model: detectionModel, detail: detectionDetail, prompt_version: PROMPT_VERSION,
                           schema_version: SCHEMA_VERSION, evidence_count: imageInputs.length } };
    }

    if (accepted) progress(pmsg("finalize"));
    const geo = accepted
      ? await (geoP || (lat != null ? reverseGeocode(lat, lng).catch(() => null) : Promise.resolve(null)))
      : null;
    const address = shortOf(geo);
    const [officerName, officerEmail, unroutedReason, bodyName] = accepted
      ? await routeOfficer((geo && geo.full) || address, lat, lng) : [null, null, null, null];
    const covered = accepted && !!officerEmail;
    // Drive Mode does not speculate (it would bill a text call for every frame, and most
    // frames are rejected), so an accepted drive pothole matches its contract here, once
    // it is known to be worth a complaint. The GIS answer is already memoised, so this
    // costs no extra network call.
    const tender = accepted && covered
      ? await (tenderP || jurisdictionOf(lat, lng)
          .then((w) => (w && w.kind === "town" && w.lgd ? matchTender(address, w.lgd) : null))
          .catch(() => null))
      : null;
    if (accepted) progress(pmsg("write"));
    // No authority means no complaint. The photo, verdict and location are still kept,
    // so nothing is lost if coverage later extends to this place.
    const [subject, body] = accepted && covered
      ? draftEmail(a, lat, lng, address, officerName, tender)
      : [null, null];
    // The evidence copy: what the officer receives. Detection works on a small
    // frame for speed and token cost, but the complaint deserves the full capture,
    // unmodified. Only kept for reports that can actually be emailed.
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
      unrouted_reason: accepted && !covered ? (unroutedReason || "outside_area") : null,
      unrouted_body: accepted && !covered ? (bodyName || null) : null,
      officer_name: officerName, officer_email: officerEmail,
      tender_number: tender ? tender.tender_number : null,
      contractor: tender ? tender.contractor : null,
      tender_note: tender ? tender.note : null,
      sent_at: null,
      drive_id: driveId,
      captured_at: Number.isFinite(capturedAtRaw) ? capturedAtRaw / 1000 : null,
      gps_accuracy: Number.isFinite(gpsAccuracyRaw) ? gpsAccuracyRaw : null,
      speed_mps: Number.isFinite(speedRaw) ? speedRaw : null,
      frame_quality: Array.isArray(frameQuality) ? frameQuality : null,
      primary_frame_index: primaryIndex,
    };
    rec.id = await addReport(rec);
    return driveMode ? { found: true, report: toDict(rec) } : toDict(rec);
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
    rec.sent_at = Date.now() / 1000;
    await putReport(rec);
    return toDict(rec);
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
      return { ai_configured: !!S.key, provider: "openai", delivery: "gmail_compose", email_configured: true,
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
      if (!blob || !blob.size) throw new Error("Empty footage segment.");
      await putFootage({ key: `${driveId}#${String(seq).padStart(5, "0")}`, drive_id: driveId,
                         seq, blob, mime: blob.type || "video/mp4", bytes: blob.size,
                         at: Date.now() / 1000 });
      return { ok: true, bytes: blob.size };
    }
    // Summaries only: the caller asks for the blobs separately, because a drive's
    // footage is hundreds of megabytes and must never be materialised by accident.
    if (path === "/api/footage" && method === "GET") {
      const byDrive = {};
      for (const f of await allFootage()) {
        const d = byDrive[f.drive_id] || (byDrive[f.drive_id] = { drive_id: f.drive_id, segments: 0, bytes: 0, mime: f.mime });
        d.segments++; d.bytes += f.bytes;
      }
      return Object.values(byDrive);
    }
    if ((m = path.match(/^\/api\/footage\/([^/]+)\/blobs$/)) && method === "GET") {
      const segs = (await footageFor(decodeURIComponent(m[1]))).sort((a, b) => a.seq - b.seq);
      if (!segs.length) throw new Error("No footage stored for that drive.");
      return { mime: segs[0].mime, blobs: segs.map((x) => x.blob) };
    }
    if ((m = path.match(/^\/api\/footage\/([^/]+)$/)) && method === "DELETE") {
      const id = decodeURIComponent(m[1]);
      for (const f of await footageFor(id)) await op("readwrite", (s) => s.delete(f.key), "footage");
      return { ok: true };
    }
    if (path === "/api/drives" && method === "POST") {
      const d = JSON.parse(opts.body);
      if (!d || !d.id) throw new Error("Drive id missing.");
      await putDrive({ id: String(d.id), started_at: d.started_at || null,
                       ended_at: Date.now() / 1000, checked: d.checked | 0, found: d.found | 0,
                       gps_track: Array.isArray(d.gps_track) ? d.gps_track : [] });
      return { ok: true };
    }
    if (path === "/api/export" && method === "POST") return exportDataset();
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
          road_class_unknown: "The app could not check whether this road is a national highway, and it will not name a city officer for a road that may not be theirs. Try again when you have a signal.",
          national_highway: "This stretch is a national highway. It is maintained by NHAI or the state PWD National Highways division, not by the city or town body, so there is no municipal officer to address.",
          rural_road: "This road is outside every town boundary, so it belongs to the state PWD or a panchayat rather than a city body. The app will not guess an office.",
          no_address: "This town's body is known, but no official email address for it has been published, so there is no verified recipient to address.",
          outside_area: "This road damage is outside Karnataka, which is the area this app covers, so there is no authority to address.",
        }[rec.unrouted_reason] || "This report could not be routed to a responsible office, so there is nothing to send.");
      }
      // "queued" stays reopenable: canceling the Gmail composer must not strand the report.
      if (rec.status !== "draft" && rec.status !== "queued") throw new Error("This report is not a sendable draft.");
      return openInGmail(rec);
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
                   distMeters, draftEmail, dataUrlToBlob, photoToBase64, toDict, listDict,
                   warrantyFor, shortlistFor, matchTenderFor: matchTender };

  window.StandaloneAPI = { __pure, handle, prewarm };

  // First run: open settings if no key yet (after the main script wires the UI).
  window.addEventListener("load", () => {
    if (!S.key && typeof window.openSettings === "function") window.openSettings(true);
  });
})();
