// The app engine: the entire pipeline on-device, no server anywhere.
// The page's api() delegates every call here.
(() => {
  const NATIVE = !!(window.Capacitor && Capacitor.isNativePlatform && Capacitor.isNativePlatform());

  // Test harness (browser, non-native): ?key=sk-... seeds the key. Never runs in the APK.
  if (!NATIVE) {
    const k = new URLSearchParams(location.search).get("key");
    if (k) localStorage.setItem("openai_key", k);
  }

  const S = {
    get key() { return (localStorage.getItem("openai_key") || "").trim(); },
    get name() { return (localStorage.getItem("sender_name") || "").trim() || "A concerned citizen"; },
    get debug() { return localStorage.getItem("debug_mode") === "1"; },
  };

  const LANG = () => (localStorage.getItem("app_lang") === "kn" ? "kn" : "en");
  const PROGRESS = {
    en: { compress: "Compressing photo...", capture: "Capturing frame...",
          detect: "AI checking for potholes...", finalize: "Finalizing address and contract...",
          write: "Writing the complaint...", email: "Opening your email app..." },
    kn: { compress: "ಫೋಟೋ ಸಂಕುಚಿಸಲಾಗುತ್ತಿದೆ...", capture: "ಫ್ರೇಮ್ ಸೆರೆಹಿಡಿಯಲಾಗುತ್ತಿದೆ...",
          detect: "AI ಗುಂಡಿ ಪರಿಶೀಲಿಸುತ್ತಿದೆ...", finalize: "ವಿಳಾಸ ಮತ್ತು ಗುತ್ತಿಗೆ ಖಚಿತಪಡಿಸಲಾಗುತ್ತಿದೆ...",
          write: "ದೂರು ಬರೆಯಲಾಗುತ್ತಿದೆ...", email: "ನಿಮ್ಮ ಇಮೇಲ್ ಆ್ಯಪ್ ತೆರೆಯಲಾಗುತ್ತಿದೆ..." },
  };
  const pmsg = (k) => (PROGRESS[LANG()] && PROGRESS[LANG()][k]) || PROGRESS.en[k];

  const MODEL = "gpt-5-mini";
  const MIN_CONFIDENCE = 0.5;
  const DEDUPE_RADIUS_M = 15;
  const DEDUPE_WINDOW_S = 30 * 60;

  const OFFICERS = {
    "bengaluru central city corporation": ["Commissioner, Bengaluru Central City Corporation (BCCC)", "commissionerbccc@gmail.com"],
    "bengaluru east city corporation": ["Commissioner, Bengaluru East City Corporation (BECC)", "commissioner.becc@gmail.com"],
    "bengaluru north city corporation": ["Commissioner, Bengaluru North City Corporation (BNCC)", "bengalurunorthcitycorporation@gmail.com"],
    "bengaluru south city corporation": ["Commissioner, Bengaluru South City Corporation (BSCC)", "comm.south.gba@gmail.com"],
    "bengaluru west city corporation": ["Commissioner, Bengaluru West City Corporation (BWCC)", "commissioner.bwcc@gmail.com"],
  };
  const HQ = ["Commissioner, Greater Bengaluru Authority (HQ)", "comm@bbmp.gov.in"];

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
  async function bodies() {
    if (_bodies) return _bodies;
    try {
      const res = await fetchWithTimeout("karnataka-bodies.json", {}, 8000);
      _bodies = (await res.json()).bodies || {};
    } catch (e) { _bodies = {}; }
    return _bodies;
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
  const hasLocation = (lat, lng, address) =>
    (lat != null && lng != null && !Number.isNaN(lat) && !Number.isNaN(lng)) || !!address;

  const DETECT_PROMPT = `You are inspecting a road photo taken in Bengaluru for a civic complaint app.

Decide whether the photo clearly shows a pothole on a road surface.
- Classify size like pizzas: small (below 30 cm wide), medium (30 to 60 cm), large (above 60 cm or a cluster).
- Beware of speed breakers: from a distance they can look like potholes. Set looks_like_speed_breaker accordingly, and if it is actually a speed breaker, is_pothole must be false.
- Shadows, manhole covers, wet patches, and road repair scars are NOT potholes.
- confidence is your 0 to 1 confidence in the is_pothole verdict. Be conservative: this triggers a government complaint.
- description: one or two factual sentences usable in a complaint (surface condition, position on the road, hazard posed).
- Some images are dashcam frames from a moving vehicle: moderate motion blur, low light, or a boosted-brightness look are normal; judge the road surface itself.`;

  // Key order is the streaming order: the verdict fields arrive before the
  // description, so the UI can show a result while the sentence is still being written.
  const ASSESS_SCHEMA = {
    type: "object", additionalProperties: false,
    required: ["is_pothole", "size", "confidence", "looks_like_speed_breaker", "description"],
    properties: {
      is_pothole: { type: "boolean" },
      size: { type: ["string", "null"], enum: ["small", "medium", "large", null] },
      confidence: { type: "number" },
      looks_like_speed_breaker: { type: "boolean" },
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

  // ---------- detection service ----------
  // The point of the hosted service: a citizen reports a pothole without owning an
  // OpenAI account. Their own key still works and takes priority, because someone who
  // has one should not be spending the operator's budget or be bound by its quota.
  const SERVICE_URL = (localStorage.getItem("service_url") || "https://pothole-detect.gauravsen.workers.dev").replace(/\/$/, "");
  const usingService = () => !S.key;

  // An install signs every request with a key it generated itself and never sends.
  // This does not prove the caller is honest, and is not meant to: it is what makes a
  // per-device quota and a revocation handle possible at all.
  const KEYPAIR_STORE = "device_keypair";
  let deviceKeys = null;

  async function deviceIdentity() {
    if (deviceKeys) return deviceKeys;
    const cached = await op("readonly", (st) => st.get(KEYPAIR_STORE), "identity").catch(() => null);
    if (cached && cached.privateKey) {
      deviceKeys = cached;
      return deviceKeys;
    }
    const pair = await crypto.subtle.generateKey(
      { name: "ECDSA", namedCurve: "P-256" }, false, ["sign", "verify"]);
    const raw = await crypto.subtle.exportKey("raw", pair.publicKey);
    const publicKey = btoa(String.fromCharCode(...new Uint8Array(raw)));
    const res = await fetchWithTimeout(`${SERVICE_URL}/v1/register`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ publicKey }),
    }, 15000);
    if (!res.ok) throw new Error("Could not set this device up with the detection service.");
    const { device_id } = await res.json();
    // The private key is stored non-extractable: it cannot be read back out, only used.
    deviceKeys = { key: KEYPAIR_STORE, privateKey: pair.privateKey, publicKey, deviceId: device_id };
    await op("readwrite", (st) => st.put(deviceKeys), "identity");
    return deviceKeys;
  }

  const OAI_URL = "https://api.openai.com/v1/responses";
  const authHeaders = () => ({ "Content-Type": "application/json", "Authorization": `Bearer ${S.key}` });

  // Detection is a classification job, not an essay: left at its default the model
  // spends 200+ hidden reasoning tokens per photo before answering, which measured
  // as roughly 3.5 of the 6.5 seconds a verdict used to take.
  const withSpeedDefaults = (body) => ({ reasoning: { effort: "minimal" }, ...body });

  // Fatal means "fails the same way without streaming", so retrying plain is pointless.
  const fatal = (e) => { e.fatal = true; return e; };
  // A stalled request is worse than a failed one: without this a lost connection
  // leaves the UI on a spinner with no end, and a drive quietly stops forever.
  const REQUEST_TIMEOUT_MS = 30000;

  async function fetchWithTimeout(url, init, ms = REQUEST_TIMEOUT_MS) {
    const ctl = typeof AbortController !== "undefined" ? new AbortController() : null;
    const timer = setTimeout(() => ctl && ctl.abort(), ms);
    try {
      return await fetch(url, ctl ? { ...init, signal: ctl.signal } : init);
    } catch (e) {
      if (e && (e.name === "AbortError" || /abort/i.test(e.message || ""))) {
        throw new Error("The network did not respond. Check the connection and try again.");
      }
      // Platform network errors read like `Unable to resolve host "api.openai.com"`.
      // Nobody watching a demo should be shown that.
      throw new Error("Could not reach OpenAI. Check the connection and try again.");
    } finally { clearTimeout(timer); }
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
    const data = await res.json();
    const msg = (data.output || []).find((o) => o.type === "message");
    const text = msg && msg.content && msg.content.find((c) => c.type === "output_text");
    if (!text || !text.text) throw new Error("Empty model response.");
    return JSON.parse(text.text);
  }

  // Structured outputs stream their keys in schema order, so is_pothole and size land
  // well before the description does. onEarly fires on that partial verdict.
  // The early verdict must apply the same rule as the final one, or a low-confidence
  // true would announce a pothole the pipeline then rejects. A false needs no
  // confidence to be final, so the early "no" stays as fast as the flag itself.
  // The delimiter is not optional. A number arrives in pieces, so "0.8" can be read
  // mid-stream as "0." and parse to 0, which would announce "no pothole" for a real one
  // and reject a frame the model accepted. Requiring the comma or brace that closes the
  // value means we only ever read a finished number.
  const CONF_RE = /"confidence"\s*:\s*([0-9]*\.?[0-9]+)\s*[,}]/;
  const POTHOLE_RE = /"is_pothole"\s*:\s*(true|false)/;

  const peekVerdict = (partial) => {
    const m = POTHOLE_RE.exec(partial);
    if (!m) return null;
    if (m[1] === "false") return { is_pothole: false, size: null };
    const c = CONF_RE.exec(partial);
    if (!c) return null;
    const s = /"size"\s*:\s*(?:"(small|medium|large)"|null)/.exec(partial);
    return { is_pothole: parseFloat(c[1]) >= MIN_CONFIDENCE, size: s ? s[1] || null : null };
  };

  // True once the response has already proved this frame will be rejected. Drive Mode
  // reads nothing but is_pothole and confidence for a rejected frame, so everything the
  // model writes after this point (size, the speed-breaker flag, a sentence of
  // description) is generated and then thrown away. Stopping here changes no verdict:
  // the bytes that decide it have already arrived and are identical either way.
  const peekReject = (partial) => {
    const m = POTHOLE_RE.exec(partial);
    if (!m) return false;
    if (m[1] === "false") return true;
    const c = CONF_RE.exec(partial);
    return !!c && parseFloat(c[1]) < MIN_CONFIDENCE;
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
        drainSSE(dec.decode(value, { stream: true }), state, onEarly, stopWhenRejected);
        if (state.stop) { try { await reader.cancel(); } catch (e) {} break; }
      }
    } else {
      // Buffered transports (the native HTTP bridge) hand back the whole SSE body at once,
      // so there is nothing left to stop early: the tokens were already generated.
      drainSSE(await res.text(), state, onEarly, stopWhenRejected);
    }
    if (state.stop) return rejectedVerdict(state.text);
    drainSSE("\n", state, onEarly, stopWhenRejected);
    if (state.stop) return rejectedVerdict(state.text);
    if (!state.text) throw new Error("Empty model response.");
    return JSON.parse(state.text);
  }

  // Reconstructed from the part of the response that had already arrived. The JSON is
  // deliberately incomplete, so it is built by hand rather than parsed. Only is_pothole
  // and confidence are read on the path that receives this, and both are present by
  // definition: nothing sets state.stop until they are.
  function rejectedVerdict(text) {
    const m = POTHOLE_RE.exec(text);
    const c = CONF_RE.exec(text);
    return {
      is_pothole: !!m && m[1] === "true",
      confidence: c ? parseFloat(c[1]) : 0,
      size: null,
      looks_like_speed_breaker: false,
      description: "",
    };
  }

  const fmt = (name, schema) => ({
    format: { type: "json_schema", name, schema, strict: true },
    verbosity: "low",
  });
  const progress = (m) => { try { window.dispatchEvent(new CustomEvent("pipeline-progress", { detail: m })); } catch (e) {} };
  const emitVerdict = (v) => { try { window.dispatchEvent(new CustomEvent("pipeline-verdict", { detail: v })); } catch (e) {} };

  let streamBroken = false;
  // Sends the image to the hosted service instead of OpenAI, signed so the service can
  // hold this install to a quota. Errors come back already phrased for a human, so they
  // are surfaced as-is rather than reworded here.
  async function analyzeViaService(dataUrl) {
    const id = await deviceIdentity();
    const b64 = dataUrl.slice(dataUrl.indexOf(",") + 1);
    const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
    const digest = await crypto.subtle.digest("SHA-256", bytes);
    const imageHash = [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
    const timestamp = String(Date.now());
    const sig = await crypto.subtle.sign(
      { name: "ECDSA", hash: "SHA-256" }, id.privateKey,
      new TextEncoder().encode(`${id.deviceId}.${timestamp}.${imageHash}`));
    const headers = {
      "content-type": "application/json",
      "x-device-id": id.deviceId,
      "x-timestamp": timestamp,
      "x-signature": btoa(String.fromCharCode(...new Uint8Array(sig))),
    };
    const attestation = await playIntegrityToken(`${id.deviceId}.${timestamp}`);
    if (attestation) headers["x-integrity-token"] = attestation;

    const res = await fetchWithTimeout(`${SERVICE_URL}/v1/detect`, {
      method: "POST", headers, body: JSON.stringify({ image: dataUrl, lang: LANG() }),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      const e = new Error(body.message || "The detection service could not check that image.");
      // A spent quota or a rejected build will not fix itself on the next frame.
      if (["daily_limit_reached", "service_budget_reached", "failed_integrity"].includes(body.error)) e.fatal = true;
      throw e;
    }
    return body;
  }

  // Present only once the app ships through Play; until then the service applies the
  // stricter unattested quota rather than refusing outright.
  async function playIntegrityToken(nonce) {
    try {
      const P = window.Capacitor && Capacitor.Plugins;
      if (!P || !P.PlayIntegrity) return null;
      const r = await P.PlayIntegrity.requestIntegrityToken({ nonce });
      return r && r.token ? r.token : null;
    } catch (e) { return null; }
  }

  async function analyzeImage(dataUrl, prompt, name, schema, model, onEarly, stopWhenRejected) {
    // A user with no key of their own goes through the hosted service, which
    // returns a finished verdict rather than a stream, so the early-abort path
    // below does not apply to them.
    if (usingService() && name === "assessment") return analyzeViaService(dataUrl);
    const body = {
      model,
      input: [{ role: "user", content: [
        { type: "input_image", image_url: dataUrl },
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
      const d = await res.json();
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
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        const r = await fetchWithTimeout(kgisPoint(url, lat, lng, fields), {}, 8000);
        if (r.ok) return r;
      } catch (e) { /* fall through to the retry */ }
    }
    return null;
  }

  async function kgisJurisdiction(lat, lng) {
    // Which polygon contains this point answers WHERE the pothole is, not WHO owns the
    // road. A national highway is a line that crosses town boundaries, so containment
    // alone addressed a Commissioner for NHAI's carriageway: measured, 5 of 12 verified
    // NH points in Karnataka routed to a municipal officer. The state's own basemap has
    // the highway network, on the host this already calls, so the two questions are asked
    // together and the answer costs no extra wait.
    const [town, nh] = await Promise.all([
      fetchWithTimeout(kgisPoint(KGIS_TOWN_URL, lat, lng,
        "KGISTownName,Town_Type,KGISTownCode,LGD_TownCode"), {}, 10000),
      // Exact containment only. A buffer picks up OBJECTID 3059, Bengaluru's MG Road,
      // which this land-cover layer misclassifies as National Highway, and that would
      // start refusing genuine city reports in the densest coverage area.
      //
      // Retried once, because this check fails closed: a momentary blip on the state's
      // server refuses a report the app could have routed, and there are now two calls
      // per report where there used to be one.
      retryQuery(KGIS_NH_URL, lat, lng, "Name"),
    ]);
    if (!town.ok) throw new Error("jurisdiction lookup unavailable");
    // Fail closed, but not into offline(): that fallback only knows Bengaluru, so a
    // failed highway check there refused every report in the rest of the state and
    // called it "outside Karnataka". An unanswered road-class check is its own outcome.
    if (!nh || !nh.ok) return { kind: "road_class_unknown" };
    const h = (await nh.json()).features || [];
    if (h.length) {
      const road = ((h[0].attributes || {}).Name || "").trim();
      return { kind: "national_highway", name: road || null };
    }
    const t = (await town.json()).features || [];
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
    const g = (await gp.json()).features || [];
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

    // Bengaluru without the network: the common case must not depend on the state GIS.
    const offline = () => {
      if (!inCoverage(lat, lng, address)) return [null, null, "outside_area"];
      if (address) {
        const low = address.toLowerCase();
        // Whole words only. Plain substring matching resolved a Bengaluru address to
        // "Chief Officer, Alur", because "alur" is inside "bengaluru".
        const named = (name) => {
          const n = name.toLowerCase().replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
          return new RegExp(`(^|[^a-z])${n}([^a-z]|$)`).test(low);
        };
        for (const [code, b] of Object.entries(registry)) {
          if (b.email && named(b.name)) {
            return [`${b.officer}, ${b.name}${b.short ? ` (${b.short})` : ""}`, b.email, null];
          }
        }
      }
      return [HQ[0], HQ[1], null];
    };

    if (lat == null || lng == null || Number.isNaN(lat) || Number.isNaN(lng)) {
      return hasLocation(lat, lng, address) ? offline() : [null, null, "no_location"];
    }

    let where;
    try { where = await jurisdictionOf(lat, lng); }
    catch (e) { return offline(); }               // GIS down: fall back, do not fail

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
  async function tenders() {
    if (_tenders) return _tenders;
    try {
      const res = await fetchWithTimeout("tenders.json", {}, 20000);
      _tenders = await res.json();
    } catch (e) { _tenders = []; }
    return _tenders;
  }

  // Contracts grouped by the body that awarded them, built once. Each municipal row
  // carries the LGD code of its body (stamped by tools/index-tenders.py), and the state
  // GIS gives the same code for the pothole, so picking the right shortlist is a lookup
  // rather than a scan of every municipal contract in Karnataka.
  let _byBody = null;
  async function tendersFor(lgd) {
    if (!_byBody) {
      _byBody = new Map();
      for (const t of await tenders()) {
        if (!t.b) continue;
        let list = _byBody.get(t.b);
        if (!list) { list = []; _byBody.set(t.b, list); }
        list.push(t);
      }
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

// ---------- server-side contracts, dedup and the city view ----------
  // A service user gets the contract match from the service, so the 9.5 MB contract file
  // does not need to be on the phone at all and can be refreshed without an app release.
  // A user on their own key keeps the on-device path, because they have no service.

  async function signedHeaders(extraHash) {
    const id = await deviceIdentity();
    const timestamp = String(Date.now());
    const payload = `${id.deviceId}.${timestamp}.${extraHash || ""}`;
    const sig = await crypto.subtle.sign(
      { name: "ECDSA", hash: "SHA-256" }, id.privateKey, new TextEncoder().encode(payload));
    return {
      "content-type": "application/json",
      "x-device-id": id.deviceId,
      "x-timestamp": timestamp,
      "x-signature": btoa(String.fromCharCode(...new Uint8Array(sig))),
    };
  }

  async function tenderFromService(address, lgd) {
    if (!address || !lgd) return null;
    try {
      const q = `address=${encodeURIComponent(address)}&lgd=${encodeURIComponent(lgd)}`;
      const res = await fetchWithTimeout(`${SERVICE_URL}/v1/tender?${q}`, {}, 30000);
      if (!res.ok) return null;
      return (await res.json()).tender || null;
    } catch (e) { return null; }
  }

  // Tells the service a pothole was confirmed here, and asks whether somebody already
  // reported it. A duplicate is not a failure: it is the answer, and the app says so
  // rather than adding a second complaint about one hole.
  async function registerReport(rec, imageHash) {
    if (!usingService()) return null;
    try {
      const headers = await signedHeaders(imageHash);
      const res = await fetchWithTimeout(`${SERVICE_URL}/v1/report`, {
        method: "POST", headers,
        body: JSON.stringify({
          lat: rec.lat, lng: rec.lng, size: rec.size, confidence: rec.confidence,
          image_hash: imageHash, lgd: rec.body_lgd || null, town: rec.body_name || null,
        }),
      }, 15000);
      if (!res.ok) return null;
      return await res.json();
    } catch (e) { return null; }
  }

  // Everything reported in the body the citizen is standing in. Returns null rather than
  // throwing: a dashboard that cannot reach the service should say so, not break.
  async function cityPotholes(lgd, lat, lng) {
    try {
      const q = lgd ? `lgd=${encodeURIComponent(lgd)}`
                    : `lat=${encodeURIComponent(lat)}&lng=${encodeURIComponent(lng)}&radius=5000`;
      const res = await fetchWithTimeout(`${SERVICE_URL}/v1/city?${q}`, {}, 20000);
      if (!res.ok) return null;
      return await res.json();
    } catch (e) { return null; }
  }

  const sha256Hex = async (dataUrl) => {
    const b64 = dataUrl.slice(dataUrl.indexOf(",") + 1);
    const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
    const d = await crypto.subtle.digest("SHA-256", bytes);
    return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, "0")).join("");
  };

  async function matchTender(address, lgd) {
    if (!address || !lgd) return null;
    // No key of their own means no OpenAI account to spend, so the match happens on the
    // service, which also holds the contract table.
    if (usingService()) return tenderFromService(address, lgd);
    if (!S.key) return null;
    const tokens = new Set();
    for (const part of address.split(",").slice(0, 4)) {
      for (const w of part.trim().toLowerCase().replace(/[()]/g, " ").split(/\s+/)) {
        if (w.length > 2 && !TENDER_STOP.has(w)) tokens.add(w);
      }
    }
    if (!tokens.size) return null;
    // Only this body's own contracts are candidates. That is what makes naming one safe:
    // the officer receiving the letter awarded the work. It also rules out the failure
    // this matcher used to be vulnerable to, a contract from a different town whose road
    // name happened to match, and it rules out state PWD, panchayat and irrigation
    // contracts, which a municipal officer has no standing over.
    const pool = await tendersFor(lgd);
    if (!pool.length) return null;
    // Scored on the work description alone. The location field is the body's own name,
    // identical in every one of its rows, so it cannot tell one of its roads from
    // another: it only ever added the town's name to every candidate equally.
    const hays = pool.map((t) => (t.t || "").toLowerCase());

    // The body's own name is not evidence about which of its roads this is, and it turns
    // up in some work titles as well as in every location, so counting alone will not
    // remove it. "Krishnamurtipuram, Mysuru" matched a Mysuru water-supply contract on
    // the strength of the word Mysuru. Drop the body's own words from the query.
    const bodyWords = new Set();
    for (const w of (pool[0].loc || "").toLowerCase().split(/[^a-z]+/)) {
      if (w.length > 2) bodyWords.add(w);
    }
    for (const w of bodyWords) tokens.delete(w);
    if (!tokens.size) return null;

    // Weight each word by how rare it is inside this body's own contracts. Counting
    // matched words equally does not work here: the town's name appears in most of its
    // own work titles, so "Vidyanagar, Hubli" scored a ward-48 Vidyanagar contract the
    // same as eighty unrelated ones that merely said Hubli, and the right one was cut by
    // the shortlist. The locality is the word that identifies a road; the town name is
    // noise once the pool is already that town.
    const idf = new Map();
    for (const tok of tokens) {
      let df = 0;
      for (const hay of hays) if (hay.includes(tok)) df++;
      // A word in none of this body's contracts is no evidence, and a word in every one
      // of them (the town's own name) does not distinguish one road from another.
      //
      // The "more than half" cut only means something once there are enough contracts to
      // count. A town with three of them had every matching word appear in more than
      // half, so every word was dropped and the town could never match anything.
      if (df === 0) continue;
      if (pool.length > 1 && df === pool.length) continue;
      if (pool.length >= 8 && df > pool.length * 0.5) continue;
      // Smoothed, so a word present in a one-contract town still scores above zero.
      idf.set(tok, Math.log((pool.length + 1) / (df + 0.5)));
    }
    // Nothing in the address narrows the pool below "somewhere in this town". Naming a
    // contract on that basis is a guess, and this letter goes to a public official with
    // a private company named in it, so it names nothing instead.
    if (!idf.size) return null;
    const scored = [];
    for (let i = 0; i < pool.length; i++) {
      let score = 0;
      for (const [tok, w] of idf) if (hays[i].includes(tok)) score += w;
      if (score > 0) scored.push([score, pool[i]]);
    }
    scored.sort((a, b) => b[0] - a[0]);
    const candidates = scored.slice(0, 25).map((x) => x[1]);
    if (!candidates.length) return null;
    const listing = candidates.map((t, i) =>
      `${i}: ${t.t.slice(0, 150)} | ${t.loc} | contractor: ${t.c || "not named"} | published: ${t.d}`).join("\n");
    const prompt = `You match a pothole's location to road-work contracts awarded by the
local body that owns this road. Every candidate below was awarded by that same body, so
the town is already correct and your only job is whether the work covers this stretch.
The pothole's reverse-geocoded address is:
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
        model: MODEL, input: prompt,
        reasoning: { effort: "medium" },
        text: fmt("tender_match", TENDER_SCHEMA),
      });
    } catch (e) { return null; }
    if (!m || m.match_index === null || m.match_index < 0 || m.match_index >= candidates.length || m.confidence < 0.6) return null;
    const t = candidates[m.match_index];
    let warranty = "recorded for this stretch";
    let warranty_code = "record";
    const dm = /^(\d{2})-(\d{2})-(\d{4})/.exec(t.d);
    if (dm) {
      const ageYears = (Date.now() - new Date(`${dm[3]}-${dm[2]}-${dm[1]}`).getTime()) / (365.25 * 24 * 3600 * 1000);
      if (ageYears <= 1) { warranty = "within the defect liability period"; warranty_code = "dlp"; }
      else if (ageYears <= 3) { warranty = "within the maintenance period"; warranty_code = "maint"; }
    }
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
  function draftEmail(a, lat, lng, address, officerName, tender) {
    const kn = LANG() === "kn";
    const sizeName = (s) => (kn ? ({ small: "ಸಣ್ಣ", medium: "ಮಧ್ಯಮ", large: "ದೊಡ್ಡ" })[s] || s : s);
    const size = a.size ? sizeName(a.size) : (kn ? "ಗಾತ್ರ ನಿರ್ಧರಿಸದ" : "unclassified");
    const road = address ? address.split(",")[0].trim() : null;

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
      ? `ರಸ್ತೆ ಗುಂಡಿ ದೂರು: ${size} ಗುಂಡಿ` + (road ? ` (${road})` : "")
      : `Pothole complaint: ${size} pothole` + (road ? ` near ${road}` : "");

    // The AI's own description of the photo used to be pasted in as a "Details:" line.
    // The photo is attached and the officer can see it, so the sentence added length
    // without adding information. Same reasoning for dropping the mention of filing on
    // Sahaaya: an officer reading this does not need to be told about a parallel filing.
    const paras = kn
      ? [
          `ಮಾನ್ಯ ${officerName || "ಅಧಿಕಾರಿಗಳೇ"} ಅವರಿಗೆ,`,
          "ದುರಸ್ತಿ ಅಗತ್ಯವಿರುವ ರಸ್ತೆ ಗುಂಡಿಯ ಬಗ್ಗೆ ದೂರು ಸಲ್ಲಿಸುತ್ತಿದ್ದೇನೆ.",
          `${locLines}\nಅಂದಾಜು ಗಾತ್ರ: ${size}`,
          "ಫೋಟೋ ಲಗತ್ತಿಸಲಾಗಿದೆ. ಈ ಗುಂಡಿ ದ್ವಿಚಕ್ರ ವಾಹನ ಸವಾರರಿಗೆ ಮತ್ತು ಇತರ ರಸ್ತೆ ಬಳಕೆದಾರರಿಗೆ ಅಪಾಯಕಾರಿ. ಇದನ್ನು ಶೀಘ್ರ ಪರಿಶೀಲಿಸಿ ದುರಸ್ತಿ ಮಾಡಬೇಕೆಂದು, ಮತ್ತು ಈ ರಸ್ತೆ ಭಾಗ ನಿರ್ವಹಣಾ ವಾರಂಟಿ ಅಡಿಯಲ್ಲಿದ್ದರೆ ಜವಾಬ್ದಾರ ಗುತ್ತಿಗೆದಾರರಿಗೆ ವರ್ಗಾಯಿಸಬೇಕೆಂದು ವಿನಂತಿಸುತ್ತೇನೆ.",
        ]
      : [
          `Dear ${officerName || "Sir or Madam"},`,
          "I would like to report a pothole that needs repair.",
          `${locLines}\nApproximate size: ${size}`,
          "PFA image. This pothole poses a danger to two wheeler riders and other road users. I request the city corporation to inspect and repair it at the earliest, and to route it to the contractor responsible if this road section is still under a maintenance warranty.",
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
        paras.push("ದೋಷ ಹೊಣೆಗಾರಿಕೆ ಅಥವಾ ನಿರ್ವಹಣಾ ಅವಧಿ ಜಾರಿಯಲ್ಲಿದ್ದರೆ, ಪಾಲಿಕೆಗೆ ಹೆಚ್ಚುವರಿ ವೆಚ್ಚವಿಲ್ಲದೆ ಗುತ್ತಿಗೆದಾರರಿಂದಲೇ ದುರಸ್ತಿ ಮಾಡಿಸಬೇಕೆಂದು ವಿನಂತಿಸುತ್ತೇನೆ. ಇದು ಸಂಭಾವ್ಯ ದಾಖಲೆ ಹೊಂದಾಣಿಕೆ; ದಯವಿಟ್ಟು ಟೆಂಡರ್ ದಾಖಲೆಗಳೊಂದಿಗೆ ಪರಿಶೀಲಿಸಿ.");
      } else {
        paras.push(`Public procurement records indicate this road stretch probably falls under tender ${tender.tender_number} ("${title}"), published on ${tender.published}${tender.contractor ? `, with ${tender.contractor} recorded as the winning bidder` : ", with no winning bidder recorded"}, and it may still be ${tender.warranty}.`);
        paras.push("If the defect liability or maintenance period is in force, I request that the repair be carried out by the contractor at no additional cost to the corporation. This is a probable record match; kindly verify against the tender documents.");
      }
    }

    paras.push(kn ? "ನಗರ ಸೇವೆಗೆ ಧನ್ಯವಾದಗಳು." : "Thank you for your service to the city.");
    paras.push(kn ? `ವಂದನೆಗಳು,\n${S.name}` : `Regards,\n${S.name}`);
    return [subject, paras.join("\n\n")];
  }

  // ---------- storage (IndexedDB) ----------
  let _db = null;
  function idb() {
    return new Promise((resolve, reject) => {
      if (_db) return resolve(_db);
      const req = indexedDB.open("potholes", 4);
      req.onupgradeneeded = () => {
        const d = req.result;
        if (!d.objectStoreNames.contains("reports")) d.createObjectStore("reports", { keyPath: "id", autoIncrement: true });
        // How many frames a drive actually checked is only known while it runs:
        // rejected frames are not kept unless debug mode is on, so the count has to
        // be recorded at the end or it is lost.
        if (!d.objectStoreNames.contains("drives")) d.createObjectStore("drives", { keyPath: "id" });
        // Continuous footage: capture stops guessing an interval, and a drive can be
        // re-analysed later, more densely or by a better model. Discarded frames are gone.
        if (!d.objectStoreNames.contains("identity")) d.createObjectStore("identity", { keyPath: "key" });
        if (!d.objectStoreNames.contains("footage")) {
          const f = d.createObjectStore("footage", { keyPath: "key" });
          f.createIndex("by_drive", "drive_id");
        }
      };
      req.onsuccess = () => { _db = req.result; resolve(_db); };
      req.onerror = () => reject(req.error);
    });
  }
  function op(mode, fn, storeName = "reports") {
    return idb().then((d) => new Promise((resolve, reject) => {
      const store = d.transaction(storeName, mode).objectStore(storeName);
      const req = fn(store);
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    }));
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

  const toDict = (r) => ({ ...r, photo_url: r.photo });

  // ---------- image ----------
  // Between 7 PM and 5 AM a dark frame is lifted so the model can read the road.
  // This is a detection aid only: it never touches the copy attached to a complaint,
  // which must be what the camera saw and not something visibly processed.
  const isNight = () => { const h = new Date().getHours(); return h >= 19 || h < 5; };

  async function toDataUrl(blob, maxDim, quality = 0.85, boost = false) {
    const bmp = await createImageBitmap(blob, { imageOrientation: "from-image" });
    const scale = Math.min(1, maxDim / Math.max(bmp.width, bmp.height));
    const c = document.createElement("canvas");
    c.width = Math.round(bmp.width * scale);
    c.height = Math.round(bmp.height * scale);
    const ctx = c.getContext("2d");
    ctx.drawImage(bmp, 0, 0, c.width, c.height);
    if (boost && isNight()) {
      ctx.filter = "brightness(1.6) contrast(1.15)";
      ctx.drawImage(bmp, 0, 0, c.width, c.height);
      ctx.filter = "none";
    }
    return c.toDataURL("image/jpeg", quality);
  }

  // ---------- pipeline ----------
  async function createReport(fd, driveMode) {
    const photo = fd.get("photo");
    if (!photo || !photo.size) throw new Error("Empty photo.");
    const latRaw = fd.get("lat"), lngRaw = fd.get("lng");
    const lat = latRaw != null && latRaw !== "" ? parseFloat(latRaw) : null;
    const lng = lngRaw != null && lngRaw !== "" ? parseFloat(lngRaw) : null;
    const driveId = driveMode ? (fd.get("drive_id") || null) : null;

    if (driveMode && lat != null) {
      // Footage analysed hours later must still dedupe against what the live pass
      // already found on that drive, so the caller can drop the time window.
      const cutoff = fd.get("dedupe_all") ? 0 : Date.now() / 1000 - DEDUPE_WINDOW_S;
      for (const r of await allReports()) {
        if ((r.status === "draft" || r.status === "queued" || r.status === "sent" || r.status === "unrouted") && r.lat != null &&
            r.created_at > cutoff && distMeters(lat, lng, r.lat, r.lng) < DEDUPE_RADIUS_M) {
          return { found: false, skipped: "already reported nearby" };
        }
      }
    }

    progress(driveMode ? pmsg("capture") : pmsg("compress"));
    // Measured on a real device: a 2000px frame is ~1.1 MB of base64 and every request
    // is marshalled across the JS-to-native bridge, which made a live detection call
    // take 13.5s median and stuttered the preview. The live pass therefore runs on a
    // smaller frame; the recorded footage keeps full quality, so a pothole missed live
    // is still recoverable by re-analysing the video. Single shots stay at full size:
    // one photo, someone waiting, and no footage behind it.
    const dataUrl = await toDataUrl(photo, driveMode ? 1024 : 2000, 0.85, true);
    // Geocoding runs in parallel with the AI calls; it never gates detection.
    const geoP = lat != null ? reverseGeocode(lat, lng).catch(() => null) : Promise.resolve(null);
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
    const detectPrompt = DETECT_PROMPT + (LANG() === "kn"
      ? "\n- Write the description field in formal Kannada (ಕನ್ನಡ ಭಾಷೆಯಲ್ಲಿ ಬರೆಯಿರಿ)."
      : "");
    // Single shot has one verdict on screen, so show it the moment it streams in.
    // Drive Mode analyses run concurrently and report through the HUD instead.
    // Drive Mode has no verdict on screen to update, so it passed no callback and took
    // the unstreamed path, waiting for a description it discards on every rejected frame.
    // It streams now purely to stop as soon as the frame is known to be rejected.
    const a = await analyzeImage(dataUrl, detectPrompt, "assessment", ASSESS_SCHEMA, MODEL,
      driveMode ? null : emitVerdict, driveMode);
    const accepted = a.is_pothole && a.confidence >= MIN_CONFIDENCE;
    if (driveMode && !accepted) return { found: false };

    if (accepted) progress(pmsg("finalize"));
    const geo = accepted ? await geoP : null;
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
      created_at: Date.now() / 1000, lat, lng, address, photo: dataUrl, photo_full: photoFull,
      is_pothole: a.is_pothole ? 1 : 0, size: a.size, confidence: a.confidence,
      description: a.description, email_subject: subject, email_body: body,
      status: accepted ? (covered ? "draft" : "unrouted") : "rejected",
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
    };

    // Ask the service whether this pothole is already reported, before it becomes a
    // complaint. Two people driving the same road should not send two letters about one
    // hole: that is how a genuine report starts looking like spam to the office
    // receiving it. A duplicate is still kept and still shown, with the count of how
    // many people have now seen it, which is worth more than a second letter.
    if (accepted && covered) {
      const dedupe = await registerReport(rec, await sha256Hex(dataUrl).catch(() => ""));
      if (dedupe && dedupe.report) {
        rec.server_id = dedupe.report.id || null;
        rec.seen_count = dedupe.report.seen_count || 1;
        if (dedupe.duplicate) {
          // Already reported. Keep the photo and the location, drop the draft: there is
          // nothing to send that has not been sent.
          rec.status = "duplicate";
          rec.duplicate_of = dedupe.report.id || null;
          rec.first_reported = dedupe.report.first_reported || null;
          rec.distance_m = dedupe.report.distance_m || 0;
          rec.email_subject = null;
          rec.email_body = null;
          rec.photo_full = null;
        }
      }
    }

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
        attachments: [{ type: "base64", name: "pothole.jpg",
                        path: (rec.photo_full || rec.photo).split(",")[1] }],
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
      files.push({ name, data: b64ToBytes(r.photo.split(",")[1]) });
      index.push({
        path: name,
        label: r.human_label,
        labelled_by: "owner",
        model_said: { is_pothole: !!r.is_pothole, size: r.size, confidence: r.confidence,
                      description: r.description },
        lat: r.lat, lng: r.lng, address: r.address,
        drive_id: r.drive_id, captured_at: new Date(r.created_at * 1000).toISOString(),
      });
    }
    files.push({ name: "labels.json", data: new TextEncoder().encode(
      JSON.stringify({ exported_at: new Date().toISOString(), count: index.length, images: index }, null, 1)) });
    const bytes = zip(files);
    return { name: `pothole-dataset-${Date.now()}.zip`, base64: bytesToB64(bytes),
             count: index.length, bytes: bytes.length };
  }

  // ---------- API dispatch ----------
  async function handle(path, opts) {
    const method = ((opts && opts.method) || "GET").toUpperCase();
    let m;
    if (path === "/api/health") {
      // Detection is available either way now: with a personal key it goes straight to
      // OpenAI, without one it goes through the hosted service. The old flag meant
      // "has a key", which would now wrongly tell a public user they are not set up.
      return { ai_configured: true, using_service: usingService(), service_url: SERVICE_URL,
               provider: usingService() ? "service" : "openai",
               delivery: "gmail_compose", email_configured: true };
    }
    if (path === "/api/reports" && method === "GET") {
      return (await allReports()).sort((a, b) => b.id - a.id).map(toDict);
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
    // Everything reported around the citizen, by anyone. Only available on the hosted
    // service, because there is nowhere else for other people's reports to live.
    if (path === "/api/city" && method === "GET") {
      if (!usingService()) return { available: false, reason: "own_key" };
      const p2 = new URLSearchParams(opts.query || "");
      const lat = parseFloat(p2.get("lat")), lng = parseFloat(p2.get("lng"));
      let lgd = null;
      try {
        const w = await jurisdictionOf(lat, lng);
        if (w && w.kind === "town") { lgd = w.lgd; }
        var town = w && w.name;
      } catch (e) { /* fall back to a radius */ }
      const data = await cityPotholes(lgd, lat, lng);
      if (!data) return { available: false, reason: "unreachable" };
      return { available: true, town: town || null, ...data };
    }
    if ((m = path.match(/^\/api\/reports\/(\d+)\/label$/)) && method === "POST") {
      const rec = await getReport(m[1]);
      if (!rec) throw new Error("Report not found.");
      const want = JSON.parse(opts.body).label;
      if (!["pothole", "not_pothole", null].includes(want)) throw new Error("Bad label.");
      rec.human_label = want;
      await putReport(rec);
      return toDict(rec);
    }
    if (path === "/api/report" && method === "POST") return createReport(opts.body, false);
    if (path === "/api/frame" && method === "POST") return createReport(opts.body, true);
    if ((m = path.match(/^\/api\/reports\/(\d+)\/send$/)) && method === "POST") {
      const rec = await getReport(m[1]);
      if (!rec) throw new Error("Report not found.");
      if (rec.status === "duplicate") {
        throw new Error("Somebody has already reported this pothole. Your sighting has been added to it, which counts for more than a second complaint about the same hole.");
      }
      if (rec.status === "unrouted") {
        // Say which of the four reasons it was. "Outside the area" is wrong and
        // confusing when the real problem is that the phone never got a GPS fix.
        throw new Error({
          no_location: "This report has no location, so there is no way to tell which office is responsible. Retake it with location switched on.",
          road_class_unknown: "The app could not check whether this road is a national highway, and it will not name a city officer for a road that may not be theirs. Try again when you have a signal.",
          national_highway: "This stretch is a national highway. It is maintained by NHAI or the state PWD National Highways division, not by the city or town body, so there is no municipal officer to address.",
          rural_road: "This road is outside every town boundary, so it belongs to the state PWD or a panchayat rather than a city body. The app will not guess an office.",
          no_address: "This town's body is known, but no official email address for it has been published, so there is no verified recipient to address.",
          outside_area: "This pothole is outside Karnataka, which is the area this app covers, so there is no authority to address.",
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

  window.StandaloneAPI = { handle, prewarm };

  // First run: open settings if no key yet (after the main script wires the UI).
  window.addEventListener("load", () => {
    if (!S.key && typeof window.openSettings === "function") window.openSettings(true);
  });
})();
