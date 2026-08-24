# -*- coding: utf-8 -*-
"""Opening a handoff must never be presented or counted as a submission."""
import sys

from playwright.sync_api import sync_playwright


APP = "http://localhost:8765/"
PIXEL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)

SCENARIO = r"""
async ({pixel}) => {
  const checks = [];
  const eq = (name, got, want) => checks.push([
    name, JSON.stringify(got) === JSON.stringify(want), got, want,
  ]);
  const ok = (name, condition, detail) => checks.push([
    name, !!condition, detail === undefined ? condition : detail, true,
  ]);
  const errorFrom = async (promise) => {
    try { await promise; return null; }
    catch (error) { return String(error && error.message || error); }
  };
  const byId = async (id) => (await StandaloneAPI.handle("/api/reports"))
    .find((report) => report.id === id);

  await StandaloneAPI.handle("/api/reports", {method: "DELETE"});
  const packManifest = await StandaloneAPI.__pure.getStatePackManifest();
  const tnResource = packManifest.resources["in-tn-routing"];
  const tnStateResource = packManifest.resources["in-tn-state-routing"];
  const gjResource = packManifest.resources["in-gj-routing"];
  const dlResource = packManifest.resources["in-dl-routing"];
  const wbResource = packManifest.resources["in-wb-routing"];
  const tnProvenance = {
    routing_pack_id: "in-tn-routing",
    routing_pack_version: tnResource.pack_version,
    routing_pack_sha256: tnResource.sha256,
    routing_pack_state_code: "TN",
  };
  const tnStateProvenance = {
    routing_pack_id: "in-tn-state-routing",
    routing_pack_version: tnStateResource.pack_version,
    routing_pack_sha256: tnStateResource.sha256,
    routing_pack_state_code: "TN",
  };
  const gjProvenance = {
    routing_pack_id: "in-gj-routing",
    routing_pack_version: gjResource.pack_version,
    routing_pack_sha256: gjResource.sha256,
    routing_pack_state_code: "GJ",
  };
  const dlProvenance = {
    routing_pack_id: "in-dl-routing",
    routing_pack_version: dlResource.pack_version,
    routing_pack_sha256: dlResource.sha256,
    routing_pack_state_code: "DL",
  };
  const wbProvenance = {
    routing_pack_id: "in-wb-routing",
    routing_pack_version: wbResource.pack_version,
    routing_pack_sha256: wbResource.sha256,
    routing_pack_state_code: "WB",
  };
  const base = {
    created_at: 1787260200,
    captured_at: 1787260200,
    decision: "accept",
    damage_type: "pothole_cavity",
    assessment: "clear",
    image_quality: "usable",
    size: "medium",
    description: "Road cavity",
    address: "Juhu Lane, Mumbai",
    email_subject: "Pothole complaint",
    email_body: "Please inspect and repair this pothole.",
    lat: 19.1197,
    lng: 72.8468,
    gps_accuracy: 12,
    photo: pixel,
    photo_full: pixel,
    official_grievance_id: null,
    submitted_at: null,
    sent_at: null,
  };
  const records = [
    {
      ...base, id: 71001, status: "draft",
      // v1.14 records predate stored GPS-accuracy provenance.
      gps_accuracy: null,
      delivery_channel: "bmc_quickfix", ward_code: "K/W",
      officer_name: "BMC Pothole QuickFix (K/W Ward suggested)",
      authority_name: "Brihanmumbai Municipal Corporation", officer_email: null,
    },
    {
      ...base, id: 71002, created_at: base.created_at + 1, status: "queued",
      handoff_opened_at: base.created_at + 10,
      delivery_channel: "bmc_quickfix", ward_code: "K/W",
      officer_name: "BMC Pothole QuickFix (K/W Ward suggested)",
      authority_name: "Brihanmumbai Municipal Corporation", officer_email: null,
    },
    {
      ...base, id: 71003, created_at: base.created_at + 2, status: "queued",
      handoff_opened_at: base.created_at + 11,
      delivery_channel: "email", ward_code: null,
      officer_name: "Municipal Commissioner", authority_name: "Test Corporation",
      officer_email: "commissioner@example.invalid",
    },
    {
      ...base, id: 71004, created_at: base.created_at + 3, status: "draft",
      delivery_channel: "bmc_quickfix", ward_code: "K/W",
      officer_name: "BMC Pothole QuickFix (K/W Ward suggested)",
      authority_name: "Brihanmumbai Municipal Corporation", officer_email: null,
    },
    {
      ...base, id: 71005, created_at: base.created_at + 4, status: "draft",
      address: "Shivajinagar, Pune", lat: 18.5308, lng: 73.8475,
      delivery_channel: "official_handoff", ward_code: null,
      officer_name: "PMC Road Mitra, Pune Municipal Corporation",
      authority_id: "mh-pmc", authority_name: "Pune Municipal Corporation",
      authority_registry_version: 1, region: "pune",
      routing_source: "pmc_official_gis", routing_match_field: "boundary",
      routing_match_value: "PMC_Boundary", ownership_unverified: true,
      officer_email: null, handoff_name: "PMC Road Mitra",
      // Simulate an older saved URL: /send must refresh it from today's registry.
      handoff_url: "https://example.invalid/stale-road-mitra",
      handoff_package: "com.nyatitechnologies.pmcroadmitra",
      alternate_handoff_name: "PMC CARE", alternate_handoff_url: "https://pmccare.in/",
      helpline: "1800-103-0222", requires_official_reference: true,
    },
    {
      ...base, id: 71006, created_at: base.created_at + 5, status: "draft",
      address: "Ambarnath, Thane", lat: 19.1860, lng: 73.1910,
      delivery_channel: "email", ward_code: null,
      officer_name: "Civic complaint desk, Ambarnath Municipal Council",
      officer_email: "coud.ambernath@maharashtra.gov.in",
      authority_id: "mh-ambarnath", authority_name: "Ambarnath Municipal Council",
      authority_registry_version: 1, region: "mmr",
      routing_source: "openstreetmap_structured", routing_match_field: "town",
      routing_match_value: "Ambarnath", ownership_unverified: true,
      requires_official_reference: false,
    },
    {
      ...base, id: 71007, created_at: base.created_at + 6, status: "draft",
      delivery_channel: "official_handoff", ward_code: null, officer_email: null,
      officer_name: "Retired handoff", authority_id: "mh-retired-test-body",
      authority_name: "Retired Test Body", authority_registry_version: 0,
      handoff_name: "Retired Portal", handoff_url: "https://example.invalid/retired",
      ownership_unverified: true, requires_official_reference: true,
    },
    {
      ...base, id: 71008, created_at: base.created_at + 7, status: "draft",
      address: "Esplanade, Kolkata", lat: 22.5726, lng: 88.3639,
      delivery_channel: "official_handoff", ward_code: null,
      officer_name: "KMC Grievance 2.0, Kolkata Municipal Corporation",
      authority_id: "wb-kmc", authority_name: "Kolkata Municipal Corporation",
      authority_registry_version: 1, region: "kolkata",
      routing_source: "wb_udma_official_gis", routing_match_field: "boundary",
      routing_match_value: "wb_municipal_boundary:250299_0000001",
      ownership_unverified: true, officer_email: null,
      handoff_name: "Retired KMC URL",
      handoff_url: "https://example.invalid/stale-kmc",
      alternate_handoff_name: "Old KMC app",
      alternate_handoff_url: "https://example.invalid/stale-kmc-app",
      whatsapp_url: "https://example.invalid/stale-whatsapp",
      helpline: "0000", requires_official_reference: true,
    },
    {
      ...base, id: 71009, created_at: base.created_at + 8, status: "draft",
      address: "India Gate, New Delhi", lat: 28.6129, lng: 77.2295,
      delivery_channel: "official_handoff", ward_code: null,
      officer_name: "Old Delhi service", authority_id: "dl-pwd-sewa",
      authority_name: "Delhi road grievance coordination",
      authority_registry_version: 0, region: "delhi",
      routing_source: "osm_delhi_nct_boundary", routing_match_field: "boundary",
      routing_match_value: "OpenStreetMap relation 1942586",
      ownership_unverified: true, officer_email: null,
      handoff_name: "Malicious saved Delhi service",
      handoff_url: "https://example.invalid/stale-delhi",
      handoff_package: "invalid.saved.package",
      alternate_handoff_name: "Malicious alternate",
      alternate_handoff_url: "https://example.invalid/stale-delhi-alternate",
      whatsapp_url: "https://example.invalid/stale-delhi-whatsapp",
      helpline: "0000", requires_official_reference: true,
    },
    {
      ...base, ...tnProvenance, id: 71010, created_at: base.created_at + 9, status: "draft",
      address: "Anna Salai, Chennai", lat: 13.0604, lng: 80.2496,
      delivery_channel: "official_handoff", ward_code: null, officer_email: null,
      officer_name: "Old Chennai service", authority_id: "tn-gcc",
      authority_name: "Old Chennai authority", authority_registry_version: 0,
      region: "chennai-gcc", routing_source: "osm_gcc_boundary",
      routing_match_field: "boundary", routing_match_value: "OpenStreetMap relation 1766358",
      ownership_unverified: true, handoff_name: "Old GCC service",
      handoff_url: "https://example.invalid/stale-gcc",
      requires_official_reference: true,
    },
    {
      ...base, ...tnProvenance, id: 71011, created_at: base.created_at + 10, status: "draft",
      address: "Anna Salai, Chennai", lat: 13.0604, lng: 80.2496,
      delivery_channel: "official_handoff", ward_code: null, officer_email: null,
      officer_name: "Rebound AMC route", authority_id: "gj-amc",
      authority_name: "Amdavad Municipal Corporation complaint intake",
      authority_registry_version: 0, region: "chennai-gcc",
      routing_source: "osm_gcc_boundary", ownership_unverified: true,
      handoff_name: "AMC CCRS", handoff_url: "https://example.invalid/rebound",
      requires_official_reference: true,
    },
    {
      ...base, ...tnProvenance, id: 71012, created_at: base.created_at + 11, status: "draft",
      routing_pack_state_code: "GJ",
      address: "Anna Salai, Chennai", lat: 13.0604, lng: 80.2496,
      delivery_channel: "official_handoff", ward_code: null, officer_email: null,
      officer_name: "Wrong-state GCC route", authority_id: "tn-gcc",
      authority_name: "Greater Chennai Corporation", authority_registry_version: 0,
      region: "chennai-gcc", routing_source: "osm_gcc_boundary",
      ownership_unverified: true, handoff_name: "GCC Public Grievance",
      handoff_url: "https://example.invalid/wrong-state", requires_official_reference: true,
    },
    {
      ...base, ...tnProvenance, id: 71013, created_at: base.created_at + 12, status: "draft",
      address: "Anna Salai, Chennai", lat: 13.0604, lng: 80.2496,
      delivery_channel: "official_handoff", ward_code: null, officer_email: null,
      officer_name: "Wrong-region GCC route", authority_id: "tn-gcc",
      authority_name: "Greater Chennai Corporation", authority_registry_version: 0,
      region: "ahmedabad-amc", routing_source: "osm_gcc_boundary",
      ownership_unverified: true, handoff_name: "GCC Public Grievance",
      handoff_url: "https://example.invalid/wrong-region", requires_official_reference: true,
    },
    {
      ...base, ...gjProvenance, id: 71014, created_at: base.created_at + 13, status: "draft",
      // Every routing string was coherently rebound to Ahmedabad, but the evidence
      // coordinates still locate this report in Chennai.
      address: "Anna Salai, Chennai", lat: 13.0827, lng: 80.2707,
      delivery_channel: "official_handoff", ward_code: null, officer_email: null,
      officer_name: "AMC CCRS, Amdavad Municipal Corporation", authority_id: "gj-amc",
      authority_name: "Amdavad Municipal Corporation complaint intake",
      authority_registry_version: 0, region: "ahmedabad-amc",
      routing_source: "opencity_amc_wards_union", routing_match_field: "boundary",
      routing_match_value: "OpenCity AMC 48-ward union, snapshot 2026-05-26",
      ownership_unverified: true,
      handoff_name: "AMC CCRS", handoff_url: "https://example.invalid/coherent-rebind",
      requires_official_reference: true,
    },
    {
      ...base, ...tnProvenance, id: 71015, created_at: base.created_at + 14, status: "draft",
      // A previously pinned pack digest remains attributable to the same state/pack;
      // current boundary containment lets it be safely upgraded to today's digest.
      routing_pack_sha256: "a".repeat(64),
      address: "Anna Salai, Chennai", lat: 13.0827, lng: 80.2707,
      delivery_channel: "official_handoff", ward_code: null, officer_email: null,
      officer_name: "GCC Public Grievance", authority_id: "tn-gcc",
      authority_name: "Greater Chennai Corporation complaint intake",
      authority_registry_version: 0, region: "chennai-gcc",
      routing_source: "osm_gcc_boundary", routing_match_field: "boundary",
      routing_match_value: "OpenStreetMap relation 1766358", ownership_unverified: true,
      handoff_name: "GCC Public Grievance", handoff_url: "https://example.invalid/prior-digest",
      requires_official_reference: true,
    },
    {
      ...base, ...dlProvenance, id: 71016, created_at: base.created_at + 15, status: "draft",
      // The recipient and every saved provenance string agree with Delhi; only the
      // immutable evidence coordinates expose the coherent rebinding.
      address: "Anna Salai, Chennai", lat: 13.0827, lng: 80.2707,
      delivery_channel: "official_handoff", ward_code: null, officer_email: null,
      officer_name: "PWD Sewa, Delhi road grievance coordination",
      authority_id: "dl-pwd-sewa", authority_name: "Delhi road grievance coordination",
      authority_registry_version: 0, region: "delhi",
      routing_source: "osm_delhi_nct_boundary", routing_match_field: "boundary",
      routing_match_value: "OpenStreetMap relation 1942586", ownership_unverified: true,
      handoff_name: "PWD Sewa", handoff_url: "https://example.invalid/coherent-delhi-rebind",
      requires_official_reference: true,
    },
    {
      ...base, ...wbProvenance, id: 71017, created_at: base.created_at + 16, status: "draft",
      address: "Darjeeling, West Bengal", lat: 27.0410, lng: 88.2663,
      delivery_channel: "official_handoff", ward_code: null, officer_email: null,
      officer_name: "Old West Bengal service",
      authority_id: "wb-statewide-unverified",
      authority_name: "West Bengal authority (select in PGRS)",
      authority_registry_version: 0, region: "west-bengal",
      routing_source: "osm_west_bengal_state_boundary",
      routing_match_field: "boundary",
      routing_match_value: "West Bengal (OpenStreetMap relation 1960177)",
      ownership_unverified: true, handoff_name: "Old state grievance service",
      handoff_url: "https://example.invalid/stale-west-bengal",
      alternate_handoff_name: "Old alternate",
      alternate_handoff_url: "https://example.invalid/stale-west-bengal-alternate",
      requires_official_reference: true,
    },
    {
      ...base, ...tnStateProvenance, id: 71018, created_at: base.created_at + 17,
      status: "draft", issue_type: "garbage",
      address: "Erode, Tamil Nadu", lat: 11.3410, lng: 77.7172,
      delivery_channel: "official_handoff", ward_code: null, officer_email: null,
      officer_name: "Old Tamil Nadu service",
      authority_id: "tn-statewide-unverified",
      authority_name: "Old Tamil Nadu authority", authority_registry_version: 0,
      region: "tamil-nadu-state", routing_source: "osm_tamil_nadu_state_boundary",
      routing_match_field: "boundary",
      routing_match_value: "Tamil Nadu (OpenStreetMap relation 96905)",
      ownership_unverified: true, handoff_name: "Old state grievance service",
      handoff_url: "https://example.invalid/stale-tamil-nadu",
      handoff_package: "example.invalid.stale",
      alternate_handoff_name: "Old alternate",
      alternate_handoff_url: "https://example.invalid/stale-tamil-nadu-alternate",
      helpline: "0000", requires_official_reference: true,
    },
    {
      ...base, id: 71019, created_at: base.created_at + 18, status: "draft",
      issue_type: "open_manhole",
      address: "Coimbatore, Tamil Nadu", lat: 11.0018115, lng: 76.9628425,
      delivery_channel: "official_handoff", ward_code: null, officer_email: null,
      officer_name: "Legacy Tamil Nadu service", authority_id: "in-tn-cm-helpline",
      authority_name: "Untrusted legacy authority", authority_registry_version: 10,
      region: "coimbatore", routing_source: "nominatim_structured_city",
      routing_match_field: "structured_place", routing_match_value: "city: Coimbatore",
      routing_pack_id: "in-top50-routing", routing_pack_version: 1,
      routing_pack_sha256: "0250e95980b7c801986a2bf025c82e4b8eb2745fe36dad09fc6dfb2a5a4f8bf5",
      routing_pack_state_code: "IN", ownership_unverified: true,
      handoff_name: "Untrusted legacy service",
      handoff_url: "https://example.invalid/stale-top50-tamil-nadu",
      handoff_package: "example.invalid.stale",
      requires_official_reference: true,
    },
  ];

  const db = await new Promise((resolve, reject) => {
    const request = indexedDB.open("potholes");
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  await new Promise((resolve, reject) => {
    const tx = db.transaction("reports", "readwrite");
    for (const record of records) tx.objectStore("reports").put(record);
    tx.oncomplete = resolve;
    tx.onabort = () => reject(tx.error || new Error("seed transaction aborted"));
    tx.onerror = () => {};
  });
  db.close();

  const prepared = await StandaloneAPI.handle("/api/reports/71001/send", {method: "POST"});
  eq("handoff: preparing the official link stays draft", prepared.status, "draft");
  eq("handoff: preparation does not set a handoff timestamp",
     prepared.handoff_opened_at, undefined);
  eq("handoff: preparation does not invent a grievance ID", prepared.official_grievance_id, null);
  eq("handoff: preparation does not set submitted_at", prepared.submitted_at, null);
  eq("handoff: preparation does not set sent_at", prepared.sent_at, null);
  ok("handoff: preparation points to BMC's official QuickFix listing",
     String(prepared.handoff_url || "").includes("com.bmc.potholequickfix"),
     prepared.handoff_url);
  eq("legacy BMC: v1.14 records recover the QuickFix package",
     prepared.handoff_package, "com.bmc.potholequickfix");
  eq("legacy BMC: v1.14 records recover the verified WhatsApp route",
     prepared.whatsapp_url, "https://wa.me/918999228999");
  eq("legacy BMC: v1.14 records recover the helpline", prepared.helpline, "1916");
  eq("legacy BMC: official reference remains required",
     prepared.requires_official_reference, true);

  const preparedStored = await byId(71001);
  eq("handoff: preparing a link does not mutate persisted status", preparedStored.status, "draft");
  eq("handoff: preparing a link stores no handoff timestamp",
     preparedStored.handoff_opened_at, undefined);

  // The UI records this endpoint only after AppLauncher/window.open reports success.
  const handedOff = await StandaloneAPI.handle(
    "/api/reports/71001/handoff-opened", {method: "POST"});
  eq("handoff: a confirmed launcher open becomes queued", handedOff.status, "queued");
  ok("handoff: records a handoff timestamp",
     Number.isFinite(handedOff.handoff_opened_at), handedOff.handoff_opened_at);
  eq("handoff: opening does not invent a grievance ID", handedOff.official_grievance_id, null);
  eq("handoff: does not set submitted_at", handedOff.submitted_at, null);
  eq("handoff: does not set sent_at", handedOff.sent_at, null);

  const queuedStored = await byId(71001);
  eq("handoff: queued status is persisted", queuedStored.status, "queued");
  eq("handoff: persisted record remains unsubmitted", queuedStored.submitted_at, null);

  // Opening a saved detail refreshes current verified channels before rendering and
  // persists only authority metadata, never a handoff/submission claim.
  const staleChennai = await byId(71010);
  eq("saved municipal handoff: stale record has no WhatsApp route",
     staleChennai.whatsapp_url, undefined);
  const chennaiList = [staleChennai];
  await openReportDetail(staleChennai, chennaiList);
  const refreshedChennai = await byId(71010);
  eq("saved municipal handoff: refresh preserves draft status",
     refreshedChennai.status, "draft");
  eq("saved municipal handoff: refresh records no handoff time",
     refreshedChennai.handoff_opened_at, undefined);
  eq("saved municipal handoff: current authority name is persisted",
     refreshedChennai.authority_name, "Greater Chennai Corporation complaint intake");
  eq("saved municipal handoff: current primary service is persisted",
     refreshedChennai.handoff_name, "GCC Public Grievance");
  eq("saved municipal handoff: current Android package is persisted",
     refreshedChennai.handoff_package, "com.ceedeev.grivenancev2");
  eq("saved municipal handoff: newly added WhatsApp route is persisted",
     refreshedChennai.whatsapp_url, "https://wa.me/919445061913");
  eq("saved municipal handoff: newly added helpline is persisted",
     refreshedChennai.helpline, "1913");
  eq("saved municipal handoff: current alternate portal is persisted",
     refreshedChennai.alternate_handoff_url,
     "https://erp.chennaicorporation.gov.in/pgr/");
  eq("saved municipal handoff: current pack provenance is retained",
     [refreshedChennai.routing_pack_id, refreshedChennai.routing_pack_state_code,
      refreshedChennai.routing_pack_sha256],
     ["in-tn-routing", "TN", tnResource.sha256]);
  const chennaiUi = {
    hasWhatsapp: !!document.getElementById("officialWhatsAppBtn"),
    hasCall: !!document.getElementById("officialCallBtn"),
    hasAlternate: !!document.getElementById("alternateHandoffBtn"),
    text: document.getElementById("detail").textContent,
  };
  ok("saved municipal handoff: refreshed channels render before use",
     chennaiUi.hasWhatsapp && chennaiUi.hasCall && chennaiUi.hasAlternate
       && /GCC Public Grievance/.test(chennaiUi.text) && /1913/.test(chennaiUi.text),
     chennaiUi);
  const chennaiOpened = await StandaloneAPI.handle(
    "/api/reports/71010/handoff-opened", {method: "POST"});
  eq("saved municipal handoff: opening remains only queued", chennaiOpened.status, "queued");
  eq("saved municipal handoff: handoff-opened returns refreshed WhatsApp",
     chennaiOpened.whatsapp_url, "https://wa.me/919445061913");
  const chennaiOpenedStored = await byId(71010);
  eq("saved municipal handoff: refreshed metadata survives handoff-opened storage",
     [chennaiOpenedStored.handoff_name, chennaiOpenedStored.helpline,
      chennaiOpenedStored.alternate_handoff_url],
     ["GCC Public Grievance", "1913", "https://erp.chennaicorporation.gov.in/pgr/"]);

  const priorPackChennai = await StandaloneAPI.handle("/api/reports/71015/handoff");
  eq("saved municipal handoff: prior same-pack digest refresh succeeds",
     priorPackChennai.handoff_name, "GCC Public Grievance");
  eq("saved municipal handoff: prior digest upgrades to the current manifest digest",
     priorPackChennai.routing_pack_sha256, tnResource.sha256);
  eq("saved municipal handoff: prior digest refresh makes no handoff claim",
     priorPackChennai.status, "draft");
  const priorPackChennaiStored = await byId(71015);
  eq("saved municipal handoff: upgraded current provenance is persisted",
     [priorPackChennaiStored.routing_pack_id, priorPackChennaiStored.routing_pack_version,
      priorPackChennaiStored.routing_pack_sha256, priorPackChennaiStored.routing_pack_state_code],
     ["in-tn-routing", tnResource.pack_version, tnResource.sha256, "TN"]);

  for (const [id, label] of [
    [71011, "cross-pack authority"],
    [71012, "cross-state provenance"],
    [71013, "cross-region provenance"],
    [71014, "coordinated Ahmedabad strings with Chennai coordinates"],
    [71016, "coordinated Delhi strings with Chennai coordinates"],
  ]) {
    const error = await errorFrom(StandaloneAPI.handle(
      `/api/reports/${id}/send`, {method: "POST"}));
    ok(`saved municipal handoff: ${label} rebinding is blocked`,
       /does not match its verified routing provenance/i.test(error || ""), error);
    const stored = await byId(id);
    eq(`saved municipal handoff: blocked ${label} remains draft`, stored.status, "draft");
    eq(`saved municipal handoff: blocked ${label} records no handoff time`,
       stored.handoff_opened_at, undefined);
  }

  // The queued detail must explain the truth and offer the explicit confirmation step.
  openDetail(queuedStored, [queuedStored]);
  const queuedUi = {
    verdict: document.querySelector("#detail .verdict").textContent.trim(),
    text: document.getElementById("detail").textContent,
    hasReferenceInput: !!document.getElementById("grievanceId"),
    hasMarkButton: !!document.getElementById("markSubmittedBtn"),
  };
  ok("UI: queued BMC says handoff, not submitted",
     /handoff/i.test(queuedUi.verdict) && !/submitted/i.test(queuedUi.verdict), queuedUi.verdict);
  ok("UI: queued legacy BMC explicitly says this app only prepares evidence",
     /independent app only prepares evidence/i.test(queuedUi.text), queuedUi.text);
  ok("UI: queued legacy BMC keeps its official service and helpline",
     /BMC Pothole QuickFix/i.test(queuedUi.text) && /1916/.test(queuedUi.text), queuedUi.text);
  ok("UI: queued BMC asks for the official reference",
     queuedUi.hasReferenceInput && queuedUi.hasMarkButton, queuedUi);

  const priorWhatsappConfirm = window.confirm, priorWhatsappOpen = window.open;
  const whatsappConfirms = [];
  let whatsappOpenCalls = 0;
  window.confirm = (message) => { whatsappConfirms.push(String(message)); return false; };
  window.open = () => { whatsappOpenCalls++; return {opener: null}; };
  await openOfficialWhatsApp(queuedStored);
  window.confirm = priorWhatsappConfirm;
  window.open = priorWhatsappOpen;
  ok("WhatsApp: confirmation discloses report text, exact location and send boundary",
     whatsappConfirms.some((message) => /Brihanmumbai Municipal Corporation/.test(message)
       && /report's text and exact location/i.test(message)
       && /Nothing is sent until you press Send in WhatsApp/i.test(message)),
     whatsappConfirms);
  eq("WhatsApp: cancelling confirmation performs no external launch", whatsappOpenCalls, 0);
  const afterWhatsappCancel = await byId(71001);
  eq("WhatsApp: cancelling does not change the queued status",
     afterWhatsappCancel.status, "queued");
  eq("WhatsApp: cancelling does not replace the recorded primary handoff time",
     afterWhatsappCancel.handoff_opened_at, queuedStored.handoff_opened_at);

  // If Android/the browser refuses to launch QuickFix, sendReport must leave the
  // record as a draft and tell the user; merely preparing the URL is not a handoff.
  const blockedReport = await byId(71004);
  const priorConfirm = window.confirm, priorAlert = window.alert, priorOpen = window.open;
  const blockedAlerts = [];
  window.confirm = () => true;
  window.alert = (message) => blockedAlerts.push(String(message));
  window.open = () => null;
  openDetail(blockedReport, [blockedReport]);
  await sendReport(blockedReport);
  window.confirm = priorConfirm;
  window.alert = priorAlert;
  window.open = priorOpen;
  const afterBlockedLaunch = await byId(71004);
  eq("UI handoff: blocked launcher leaves report draft", afterBlockedLaunch.status, "draft");
  eq("UI handoff: blocked launcher stores no handoff time",
     afterBlockedLaunch.handoff_opened_at, undefined);
  ok("UI handoff: blocked launcher produces an actionable error",
     blockedAlerts.some((message) => /could not open (?:BMC )?Pothole QuickFix/i.test(message)),
     blockedAlerts);

  // Current generic handoffs persist their verified primary and alternate channels.
  // Preparing either channel is still not a submission.
  const pmcPrepared = await StandaloneAPI.handle(
    "/api/reports/71005/send", {method: "POST"});
  eq("generic handoff: preparing Road Mitra stays draft", pmcPrepared.status, "draft");
  eq("generic handoff: returns the persisted primary service name",
     pmcPrepared.handoff_name, "PMC Road Mitra");
  eq("generic handoff: returns the persisted Android package",
     pmcPrepared.handoff_package, "com.nyatitechnologies.pmcroadmitra");
  ok("generic handoff: refreshes a stale saved URL from the current verified registry",
     String(pmcPrepared.handoff_url || "").includes("com.nyatitechnologies.pmcroadmitra")
       && !String(pmcPrepared.handoff_url || "").includes("example.invalid"),
     pmcPrepared.handoff_url);
  eq("generic handoff: returns the persisted alternate service name",
     pmcPrepared.alternate_handoff_name, "PMC CARE");
  eq("generic handoff: returns the persisted alternate URL",
     pmcPrepared.alternate_handoff_url, "https://pmccare.in/");
  eq("generic handoff: preparation does not set a handoff timestamp",
     pmcPrepared.handoff_opened_at, undefined);
  const pmcPreparedStored = await byId(71005);
  eq("generic handoff: alternate service name survives IndexedDB storage",
     pmcPreparedStored.alternate_handoff_name, "PMC CARE");
  eq("generic handoff: alternate URL survives IndexedDB storage",
     pmcPreparedStored.alternate_handoff_url, "https://pmccare.in/");

  const retiredRouteError = await errorFrom(StandaloneAPI.handle(
    "/api/reports/71007/send", {method: "POST"}));
  ok("generic handoff: an authority absent from the current registry is blocked",
     /verified official handoff is unavailable/i.test(retiredRouteError || ""), retiredRouteError);
  const retiredStored = await byId(71007);
  eq("generic handoff: blocked retired route remains a draft", retiredStored.status, "draft");
  eq("generic handoff: blocked retired route records no handoff time",
     retiredStored.handoff_opened_at, undefined);

  const kmcPrepared = await StandaloneAPI.handle(
    "/api/reports/71008/send", {method: "POST"});
  eq("KMC handoff: preparing the portal stays draft", kmcPrepared.status, "draft");
  eq("KMC handoff: current registry restores the primary service name",
     kmcPrepared.handoff_name, "KMC Grievance 2.0");
  eq("KMC handoff: current registry restores the official portal",
     kmcPrepared.handoff_url, "https://kmc.wb.gov.in/citizen/language-selection");
  eq("KMC handoff: installed official KMC app is launchable",
     kmcPrepared.handoff_package, "com.kmc.app");
  eq("KMC handoff: stale stored channels cannot survive revalidation",
     kmcPrepared.whatsapp_url, "https://wa.me/918335988888");
  ok("KMC handoff: official KMC app is offered as the alternate",
     /com\.kmc\.app/.test(kmcPrepared.alternate_handoff_url || ""),
     kmcPrepared.alternate_handoff_url);
  eq("KMC handoff: opening has not been claimed yet",
     kmcPrepared.handoff_opened_at, undefined);

  const westBengalPrepared = await StandaloneAPI.handle(
    "/api/reports/71017/send", {method: "POST"});
  eq("West Bengal handoff: preparing PGRS stays draft",
     westBengalPrepared.status, "draft");
  eq("West Bengal handoff: current registry restores PGRS",
     westBengalPrepared.handoff_name, "West Bengal PGRS");
  eq("West Bengal handoff: stale primary URL cannot survive revalidation",
     westBengalPrepared.handoff_url,
     "https://finance.wb.gov.in/pgrs/page/PGMS_Lodge_Greivance.aspx");
  eq("West Bengal handoff: current CMO alternate is restored",
     westBengalPrepared.alternate_handoff_url,
     "https://cmo.wb.gov.in/landing/raise-grievance");
  eq("West Bengal handoff: current pack provenance is retained",
     [westBengalPrepared.routing_pack_id, westBengalPrepared.routing_pack_state_code,
      westBengalPrepared.routing_pack_sha256],
     ["in-wb-routing", "WB", wbResource.sha256]);
  eq("West Bengal handoff: preparing PGRS records no handoff time",
     westBengalPrepared.handoff_opened_at, undefined);

  const tamilNaduPrepared = await StandaloneAPI.handle(
    "/api/reports/71018/send", {method: "POST"});
  eq("Tamil Nadu handoff: preparing Mudhalvarin Mugavari stays draft",
     tamilNaduPrepared.status, "draft");
  eq("Tamil Nadu handoff: current primary service is restored",
     tamilNaduPrepared.handoff_name, "Mudhalvarin Mugavari");
  eq("Tamil Nadu handoff: stale primary URL cannot survive revalidation",
     tamilNaduPrepared.handoff_url, "https://cmhelpline.tnega.org/portal/en/home");
  eq("Tamil Nadu handoff: current official Android package is restored",
     tamilNaduPrepared.handoff_package, "org.tnega.cmhelpline.citizen");
  eq("Tamil Nadu handoff: stale municipal alternate is removed",
     tamilNaduPrepared.alternate_handoff_url, null);
  eq("Tamil Nadu handoff: current helpline is restored",
     tamilNaduPrepared.helpline, "1100");
  eq("Tamil Nadu handoff: current pack provenance is retained",
     [tamilNaduPrepared.routing_pack_id, tamilNaduPrepared.routing_pack_state_code,
      tamilNaduPrepared.routing_pack_sha256],
     ["in-tn-state-routing", "TN", tnStateResource.sha256]);
  eq("Tamil Nadu handoff: preparation records no handoff time",
     tamilNaduPrepared.handoff_opened_at, undefined);

  const legacyTamilNadu = await StandaloneAPI.handle("/api/reports/71019/handoff");
  eq("Tamil Nadu migration: old top-50 report becomes a statewide report",
     [legacyTamilNadu.authority_id, legacyTamilNadu.region,
      legacyTamilNadu.routing_pack_id, legacyTamilNadu.routing_pack_state_code],
     ["tn-statewide-unverified", "tamil-nadu-state", "in-tn-state-routing", "TN"]);
  eq("Tamil Nadu migration: stale legacy URL is never trusted",
     legacyTamilNadu.handoff_url, "https://cmhelpline.tnega.org/portal/en/home");
  eq("Tamil Nadu migration: stale legacy package is never trusted",
     legacyTamilNadu.handoff_package, "org.tnega.cmhelpline.citizen");
  eq("Tamil Nadu migration: current digest replaces the retired top-50 digest",
     legacyTamilNadu.routing_pack_sha256, tnStateResource.sha256);
  eq("Tamil Nadu migration: report remains a draft without a handoff claim",
     [legacyTamilNadu.status, legacyTamilNadu.handoff_opened_at], ["draft", undefined]);
  const legacyTamilNaduStored = await byId(71019);
  eq("Tamil Nadu migration: fresh authority binding is persisted",
     [legacyTamilNaduStored.authority_id, legacyTamilNaduStored.routing_pack_id,
      legacyTamilNaduStored.handoff_url],
     ["tn-statewide-unverified", "in-tn-state-routing",
      "https://cmhelpline.tnega.org/portal/en/home"]);

  const delhiPrepared = await StandaloneAPI.handle(
    "/api/reports/71009/send", {method: "POST"});
  eq("Delhi handoff: preparing PWD Sewa stays draft", delhiPrepared.status, "draft");
  eq("Delhi handoff: current registry restores PWD Sewa",
     delhiPrepared.handoff_name, "PWD Sewa");
  eq("Delhi handoff: current registry restores the complaint portal",
     delhiPrepared.handoff_url, "https://www.pwddelhi.gov.in/sewa/complaint");
  eq("Delhi handoff: current registry restores the official Android package",
     delhiPrepared.handoff_package, "com.sis.pwdsewaapp");
  eq("Delhi handoff: stale stored alternate cannot survive revalidation",
     delhiPrepared.alternate_handoff_url, "https://pgms.delhi.gov.in/");
  eq("Delhi handoff: stale stored WhatsApp cannot survive revalidation",
     delhiPrepared.whatsapp_url, "https://wa.me/918130188222");
  eq("Delhi handoff: current helpline is restored", delhiPrepared.helpline, "1908");
  eq("Delhi handoff: preparation sets no handoff time",
     delhiPrepared.handoff_opened_at, undefined);
  const delhiPreparedStored = await byId(71009);
  eq("Delhi handoff: preparing a URL does not mutate persisted status",
     delhiPreparedStored.status, "draft");

  openDetail(pmcPreparedStored, [pmcPreparedStored]);
  const pmcUi = {
    verdict: document.querySelector("#detail .verdict").textContent.trim(),
    text: document.getElementById("detail").textContent,
    labels: [...document.querySelectorAll("#detail label")].map((x) => x.textContent.trim()),
    hasReferenceInput: !!document.getElementById("grievanceId"),
    hasAlternate: !!document.getElementById("alternateHandoffBtn"),
    alternateText: document.getElementById("alternateHandoffBtn")?.textContent.trim(),
  };
  ok("generic UI: identifies the suggested authority and ownership uncertainty",
     /Pune Municipal Corporation/.test(pmcUi.text) && /does not prove who owns this road/i.test(pmcUi.text),
     pmcUi.text);
  ok("generic UI: primary and alternate official services are visible",
     /PMC Road Mitra/.test(pmcUi.text) && pmcUi.hasAlternate && /PMC CARE/.test(pmcUi.alternateText || ""),
     pmcUi);
  ok("generic UI: asks for a generic official reference, not a BMC-only reference",
     pmcUi.hasReferenceInput
       && pmcUi.labels.includes("Official grievance/reference ID")
       && !pmcUi.labels.includes("Official BMC grievance ID"), pmcUi.labels);

  // A blocked primary-app launch must remain a draft for generic handoffs too.
  const priorPmcConfirm = window.confirm, priorPmcAlert = window.alert;
  const pmcBlockedAlerts = [];
  window.confirm = () => true;
  window.alert = (message) => pmcBlockedAlerts.push(String(message));
  window.open = () => null;
  openDetail(pmcPreparedStored, [pmcPreparedStored]);
  await sendReport(pmcPreparedStored);
  window.confirm = priorPmcConfirm;
  window.alert = priorPmcAlert;
  window.open = priorOpen;
  const pmcAfterBlocked = await byId(71005);
  eq("generic UI handoff: blocked launcher leaves report draft",
     pmcAfterBlocked.status, "draft");
  eq("generic UI handoff: blocked launcher stores no handoff time",
     pmcAfterBlocked.handoff_opened_at, undefined);
  ok("generic UI handoff: blocked launcher names the failed official service",
     pmcBlockedAlerts.some((message) => /could not open PMC Road Mitra/i.test(message)),
     pmcBlockedAlerts);

  const pmcDraftEvidence = await StandaloneAPI.handle("/api/reports/71005/evidence");
  ok("generic evidence: names the suggested Pune authority",
     /Pune Municipal Corporation/.test(pmcDraftEvidence.text), pmcDraftEvidence.text);
  ok("generic evidence: never falsely describes a PMC report as a BMC report",
     !/\bBMC\b/.test(pmcDraftEvidence.text), pmcDraftEvidence.text);

  // Successfully opening the verified alternate portal records only a handoff.
  const priorAlternateOpen = window.open;
  window.open = () => ({opener: null});
  await openAlternateHandoff(pmcAfterBlocked);
  window.open = priorAlternateOpen;
  const pmcQueued = await byId(71005);
  eq("generic alternate: successful portal open becomes queued", pmcQueued.status, "queued");
  ok("generic alternate: successful portal open records a handoff timestamp",
     Number.isFinite(pmcQueued.handoff_opened_at), pmcQueued.handoff_opened_at);
  eq("generic alternate: opening does not invent a reference",
     pmcQueued.official_grievance_id, null);
  eq("generic alternate: opening does not mark submitted", pmcQueued.submitted_at, null);

  const pmcBlankError = await errorFrom(StandaloneAPI.handle(
    "/api/reports/71005/submitted",
    {method: "POST", body: JSON.stringify({official_grievance_id: ""})},
  ));
  ok("generic confirmation: blank reference is rejected with the authority named",
     /official grievance\/reference ID from Pune Municipal Corporation/i.test(pmcBlankError || ""),
     pmcBlankError);
  const pmcShortError = await errorFrom(StandaloneAPI.handle(
    "/api/reports/71005/submitted",
    {method: "POST", body: JSON.stringify({official_grievance_id: "123"})},
  ));
  ok("generic confirmation: implausibly short reference is rejected",
     /official grievance\/reference ID from Pune Municipal Corporation/i.test(pmcShortError || ""),
     pmcShortError);
  const pmcAfterRejectedConfirmation = await byId(71005);
  eq("generic confirmation: rejected attempt stays queued",
     pmcAfterRejectedConfirmation.status, "queued");
  eq("generic confirmation: rejected attempt stores no submission time",
     pmcAfterRejectedConfirmation.submitted_at, null);

  const blankError = await errorFrom(StandaloneAPI.handle(
    "/api/reports/71001/submitted",
    {method: "POST", body: JSON.stringify({official_grievance_id: ""})},
  ));
  ok("BMC confirmation: blank grievance ID is rejected",
     /official BMC grievance ID/i.test(blankError || ""), blankError);
  const shortError = await errorFrom(StandaloneAPI.handle(
    "/api/reports/71001/submitted",
    {method: "POST", body: JSON.stringify({official_grievance_id: "123"})},
  ));
  ok("BMC confirmation: implausibly short grievance ID is rejected",
     /official BMC grievance ID/i.test(shortError || ""), shortError);
  const afterRejectedConfirmation = await byId(71001);
  eq("BMC confirmation: rejected attempt stays queued",
     afterRejectedConfirmation.status, "queued");
  eq("BMC confirmation: rejected attempt stores no submission time",
     afterRejectedConfirmation.submitted_at, null);

  const submitted = await StandaloneAPI.handle("/api/reports/71001/submitted", {
    method: "POST",
    body: JSON.stringify({official_grievance_id: "  BMC-2026-000123  "}),
  });
  eq("BMC confirmation: valid reference marks sent", submitted.status, "sent");
  eq("BMC confirmation: reference is trimmed",
     submitted.official_grievance_id, "BMC-2026-000123");
  ok("BMC confirmation: submitted_at is recorded",
     Number.isFinite(submitted.submitted_at), submitted.submitted_at);
  eq("BMC confirmation: legacy sent_at matches submitted_at",
     submitted.sent_at, submitted.submitted_at);

  const submittedStored = await byId(71001);
  eq("BMC confirmation: reference is persisted",
     submittedStored.official_grievance_id, "BMC-2026-000123");
  openDetail(submittedStored, [submittedStored]);
  const sentUi = {
    verdict: document.querySelector("#detail .verdict").textContent.trim(),
    text: document.getElementById("detail").textContent,
    hasSend: !!document.getElementById("sendBtn"),
    hasMark: !!document.getElementById("markSubmittedBtn"),
  };
  ok("UI: confirmed BMC is visibly marked submitted",
     /marked submitted/i.test(sentUi.verdict), sentUi.verdict);
  ok("UI: confirmed BMC shows the official reference",
     sentUi.text.includes("BMC-2026-000123"), sentUi.text);
  ok("UI: confirmed BMC has no repeat send/mark controls",
     !sentUi.hasSend && !sentUi.hasMark, sentUi);

  // One confirmed report plus two merely queued handoffs: the dashboard must say 1.
  await openDash();
  const stats = [...document.querySelectorAll("#dashStats .card")].map((card) => ({
    value: card.querySelector(".verdict").textContent.trim(),
    label: card.querySelector(".meta").textContent.trim(),
  }));
  const submissionTile = stats.find((tile) => /confirmed submissions/i.test(tile.label));
  ok("dashboard: labels the metric as confirmed submissions", !!submissionTile, stats);
  eq("dashboard: queued handoffs are not counted as submissions",
     submissionTile && submissionTile.value, "1");

  const pmcSubmitted = await StandaloneAPI.handle("/api/reports/71005/submitted", {
    method: "POST",
    body: JSON.stringify({official_grievance_id: "  PMC-2026-009876  "}),
  });
  eq("generic confirmation: valid reference marks sent", pmcSubmitted.status, "sent");
  eq("generic confirmation: reference is trimmed",
     pmcSubmitted.official_grievance_id, "PMC-2026-009876");
  ok("generic confirmation: submitted_at is recorded",
     Number.isFinite(pmcSubmitted.submitted_at), pmcSubmitted.submitted_at);

  const pmcSubmittedStored = await byId(71005);
  eq("generic confirmation: official reference is persisted",
     pmcSubmittedStored.official_grievance_id, "PMC-2026-009876");
  openDetail(pmcSubmittedStored, [pmcSubmittedStored]);
  const pmcSentUi = {
    verdict: document.querySelector("#detail .verdict").textContent.trim(),
    text: document.getElementById("detail").textContent,
    hasSend: !!document.getElementById("sendBtn"),
    hasMark: !!document.getElementById("markSubmittedBtn"),
  };
  ok("generic UI: confirmed PMC report is visibly marked submitted",
     /marked submitted/i.test(pmcSentUi.verdict), pmcSentUi.verdict);
  ok("generic UI: confirmed PMC report shows its official reference",
     pmcSentUi.text.includes("PMC-2026-009876"), pmcSentUi.text);
  ok("generic UI: confirmed PMC report has no repeat send/mark controls",
     !pmcSentUi.hasSend && !pmcSentUi.hasMark, pmcSentUi);

  const pmcSentEvidence = await StandaloneAPI.handle("/api/reports/71005/evidence");
  ok("generic evidence: submitted text includes the official reference",
     /PMC-2026-009876/.test(pmcSentEvidence.text), pmcSentEvidence.text);
  ok("generic evidence: submitted PMC text never calls it a BMC reference",
     !/\bBMC\b/.test(pmcSentEvidence.text), pmcSentEvidence.text);

  await openDash();
  const postPmcStats = [...document.querySelectorAll("#dashStats .card")].map((card) => ({
    value: card.querySelector(".verdict").textContent.trim(),
    label: card.querySelector(".meta").textContent.trim(),
  }));
  const postPmcTile = postPmcStats.find((tile) => /confirmed submissions/i.test(tile.label));
  eq("dashboard: a second reference-confirmed handoff raises the total",
     postPmcTile && postPmcTile.value, "2");

  const kmcOpened = await StandaloneAPI.handle(
    "/api/reports/71008/handoff-opened", {method: "POST"});
  eq("KMC handoff: a successful launcher open is only queued", kmcOpened.status, "queued");
  eq("KMC handoff: portal open invents no grievance number",
     kmcOpened.official_grievance_id, null);
  eq("KMC handoff: portal open does not mark submitted", kmcOpened.submitted_at, null);
  const kmcBlankError = await errorFrom(StandaloneAPI.handle(
    "/api/reports/71008/submitted",
    {method: "POST", body: JSON.stringify({official_grievance_id: ""})},
  ));
  ok("KMC confirmation: official reference is required and names KMC",
     /official grievance\/reference ID from Kolkata Municipal Corporation/i.test(kmcBlankError || ""),
     kmcBlankError);
  const kmcStillQueued = await byId(71008);
  eq("KMC confirmation: rejected confirmation stays queued", kmcStillQueued.status, "queued");
  const kmcSubmitted = await StandaloneAPI.handle("/api/reports/71008/submitted", {
    method: "POST",
    body: JSON.stringify({official_grievance_id: "  KMC-2026-001234  "}),
  });
  eq("KMC confirmation: reference-confirmed report becomes sent", kmcSubmitted.status, "sent");
  eq("KMC confirmation: official reference is trimmed and retained",
     kmcSubmitted.official_grievance_id, "KMC-2026-001234");

  const delhiOpened = await StandaloneAPI.handle(
    "/api/reports/71009/handoff-opened", {method: "POST"});
  eq("Delhi handoff: a confirmed launcher open is only queued", delhiOpened.status, "queued");
  ok("Delhi handoff: confirmed open records a handoff timestamp",
     Number.isFinite(delhiOpened.handoff_opened_at), delhiOpened.handoff_opened_at);
  eq("Delhi handoff: opening invents no official reference",
     delhiOpened.official_grievance_id, null);
  eq("Delhi handoff: opening does not mark submitted", delhiOpened.submitted_at, null);
  for (const [label, reference] of [["blank", ""], ["short", "123"]]) {
    const error = await errorFrom(StandaloneAPI.handle(
      "/api/reports/71009/submitted",
      {method: "POST", body: JSON.stringify({official_grievance_id: reference})},
    ));
    ok(`Delhi confirmation: ${label} official reference is rejected`,
       /official grievance\/reference ID from Delhi road grievance coordination/i.test(error || ""),
       error);
  }
  const delhiStillQueued = await byId(71009);
  eq("Delhi confirmation: rejected confirmation stays queued",
     delhiStillQueued.status, "queued");
  eq("Delhi confirmation: rejected confirmation stores no submission time",
     delhiStillQueued.submitted_at, null);
  const delhiSubmitted = await StandaloneAPI.handle("/api/reports/71009/submitted", {
    method: "POST",
    body: JSON.stringify({official_grievance_id: "  DL-PWD-2026-001234  "}),
  });
  eq("Delhi confirmation: reference-confirmed report becomes sent",
     delhiSubmitted.status, "sent");
  eq("Delhi confirmation: official reference is trimmed and retained",
     delhiSubmitted.official_grievance_id, "DL-PWD-2026-001234");
  ok("Delhi confirmation: submitted_at is recorded",
     Number.isFinite(delhiSubmitted.submitted_at), delhiSubmitted.submitted_at);

  // A municipality email inferred from the point is still only a civic-authority
  // suggestion. The warning and the final confirmation must say ownership is unknown.
  const councilDraft = await byId(71006);
  openDetail(councilDraft, [councilDraft]);
  const councilUiText = document.getElementById("detail").textContent;
  ok("suggested email route: UI names the authority and warns ownership is unverified",
     /Ambarnath Municipal Council/.test(councilUiText)
       && /road ownership is not verified/i.test(councilUiText), councilUiText);
  const priorCouncilConfirm = window.confirm;
  const councilConfirms = [];
  window.confirm = (message) => { councilConfirms.push(String(message)); return false; };
  await sendReport(councilDraft);
  window.confirm = priorCouncilConfirm;
  ok("suggested email route: confirmation repeats the ownership warning",
     councilConfirms.some((message) => /Ambarnath Municipal Council/.test(message)
       && /does not prove road ownership/i.test(message)
       && /coud\.ambernath@maharashtra\.gov\.in/.test(message)), councilConfirms);
  const councilAfterCancel = await byId(71006);
  eq("suggested email route: cancelling confirmation leaves the report draft",
     councilAfterCancel.status, "draft");

  // The browser/PWA path must open a real mailto composer instead of merely logging
  // and claiming that an email handoff happened.
  const originalAnchorClick = HTMLAnchorElement.prototype.click;
  const mailtoLaunches = [];
  HTMLAnchorElement.prototype.click = function () { mailtoLaunches.push(this.href); };
  const councilPrepared = await StandaloneAPI.handle(
    "/api/reports/71006/send", {method: "POST"});
  HTMLAnchorElement.prototype.click = originalAnchorClick;
  ok("suggested email route: browser opens an addressed mailto composer",
     mailtoLaunches.length === 1
       && mailtoLaunches[0].startsWith("mailto:coud.ambernath%40maharashtra.gov.in?")
       && mailtoLaunches[0].includes("subject=Pothole+complaint")
       && mailtoLaunches[0].includes("body=Please+inspect+and+repair+this+pothole."),
     mailtoLaunches);
  eq("suggested email route: composer open is queued, not submitted",
     councilPrepared.status, "queued");
  eq("suggested email route: composer open stores no submission time",
     councilPrepared.submitted_at, null);

  // Email also requires an explicit citizen confirmation, but unlike BMC it has no
  // official grievance ID to record.
  const emailSubmitted = await StandaloneAPI.handle("/api/reports/71003/submitted", {
    method: "POST", body: "{}",
  });
  eq("email confirmation: explicit confirmation marks sent", emailSubmitted.status, "sent");
  eq("email confirmation: no grievance ID is invented",
     emailSubmitted.official_grievance_id, null);

  return checks;
}
"""


def main():
    failures = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=["--disable-web-security"])
        context = browser.new_context(viewport={"width": 390, "height": 844})
        context.add_init_script(
            "localStorage.setItem('openai_key', 'test-key-never-sent');"
        )
        page = context.new_page()
        page.goto(APP)
        page.wait_for_load_state("networkidle")
        page.wait_for_function(
            "typeof StandaloneAPI !== 'undefined' && typeof openDetail === 'function' "
            "&& typeof openReportDetail === 'function' && typeof openDash === 'function'",
            timeout=30000,
        )
        results = page.evaluate(SCENARIO, {"pixel": PIXEL})
        context.close()
        browser.close()

    for name, passed, got, want in results:
        if not passed:
            failures.append(name)
            print(f"  FAIL {name}\n         got  {got}\n         want {want}")
    if failures:
        print(f"{len(failures)} of {len(results)} failed")
        sys.exit(1)
    print(f"SUBMISSION TRUTH TEST PASS ({len(results)} checks)")


main()
