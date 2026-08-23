# -*- coding: utf-8 -*-
"""State packs are pinned, downloaded once, verified, cached, and evicted safely."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import pathlib
import sys

from playwright.sync_api import sync_playwright

TOOLS_ROOT = pathlib.Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS_ROOT))

from state_pack_tools import (  # noqa: E402
    PackError,
    SPECS,
    _validate_municipal_city_payload,
)
from state_pack_utils import (
    MANIFEST_PATH,
    ROOT,
    load_manifest,
    pack_path,
    read_pack,
    resource_relative_path,
    route_pattern,
)


APP = "http://localhost:8765/"
PRODUCTION_SITE_ROOT = "https://coding-parrot.github.io/pothole-reporter/"
EXPECTED_RESOURCES = {
    "in-dl-routing": ("DL", "routing", "pothole-routing-pack", "delhi-nct-v1"),
    "in-gj-routing": ("GJ", "routing", "pothole-routing-pack", "municipal-city-v1"),
    "in-ka-routing": ("KA", "routing", "pothole-routing-pack", "karnataka-kgis-v1"),
    "in-ka-tenders": (
        "KA", "tenders", "pothole-tender-pack", "karnataka-locally-indexed-v1"
    ),
    "in-mh-routing": (
        "MH", "routing", "pothole-routing-pack", "maharashtra-statewide-v1"
    ),
    "in-tg-routing": ("TG", "routing", "pothole-routing-pack", "municipal-city-v1"),
    "in-tn-routing": ("TN", "routing", "pothole-routing-pack", "municipal-city-v1"),
    "in-wb-routing": (
        "WB", "routing", "pothole-routing-pack", "west-bengal-statewide-v1"
    ),
}
MUNICIPAL_CITY_RESOURCES = {
    "in-tn-routing": {
        "region_id": "chennai-gcc",
        "authority_id": "tn-gcc",
        "routing_mode": "boundary",
        "routing_source": "osm_gcc_boundary",
        "source_object_id": "osm:relation:1766358",
    },
    "in-tg-routing": {
        "region_id": "hyderabad-cure-2053",
        "authority_id": "tg-cure-shared",
        "routing_mode": "official_point_query",
        "routing_source": "tgrac_cure_2053_point_query",
        "source_object_id": (
            "tgrac:TCUR_Folder:TCUR_Telangana_Core_Urban_Region_V2:MapServer:22"
        ),
    },
    "in-gj-routing": {
        "region_id": "ahmedabad-amc",
        "authority_id": "gj-amc",
        "routing_mode": "boundary",
        "routing_source": "opencity_amc_wards_union",
        "source_object_id": "opencity:wards-ahmedabad:2026-05-26",
    },
}
LEGACY_BUNDLED_FILES = {
    "delhi-coverage.json",
    "kolkata-coverage.json",
    "maharashtra-coverage.json",
    "karnataka-bodies.json",
    "tenders.json",
}

HOOKS_READY = """
() => {
  const P = window.StandaloneAPI && StandaloneAPI.__pure;
  return P && ["loadStatePack", "pruneStatePacks", "getStatePackManifest",
    "resolvePackUrl", "resetStatePackMemory"].every((name) => typeof P[name] === "function");
}
"""

IDB_HELPERS = r"""
const openPackDb = () => new Promise((resolve, reject) => {
  const request = indexedDB.open("potholes", 6);
  request.onerror = () => reject(request.error);
  request.onsuccess = () => resolve(request.result);
});
const allPackRecords = async () => {
  const db = await openPackDb();
  try {
    return await new Promise((resolve, reject) => {
      const tx = db.transaction("state_packs", "readonly");
      const request = tx.objectStore("state_packs").getAll();
      request.onerror = () => reject(request.error);
      request.onsuccess = () => resolve(request.result);
    });
  } finally { db.close(); }
};
const mutatePackRecords = async (mutator) => {
  const db = await openPackDb();
  try {
    await new Promise((resolve, reject) => {
      const tx = db.transaction("state_packs", "readwrite");
      const store = tx.objectStore("state_packs");
      const request = store.getAll();
      request.onerror = () => reject(request.error);
      request.onsuccess = () => {
        try { mutator(store, request.result); }
        catch (error) { tx.abort(); reject(error); }
      };
      tx.oncomplete = resolve;
      tx.onerror = () => {};
      tx.onabort = () => reject(tx.error || new Error("state-pack transaction aborted"));
    });
  } finally { db.close(); }
};
"""


def check_municipal_city_pack(pack_id: str, envelope: dict, failures: list[str]) -> None:
    expected = MUNICIPAL_CITY_RESOURCES[pack_id]
    payload = envelope.get("payload")
    try:
        _validate_municipal_city_payload(
            SPECS[pack_id],
            payload,
            generated_at=envelope.get("generated_at"),
            authorities=envelope.get("authorities"),
        )
    except PackError as error:
        failures.append(f"{pack_id} fails the release municipal schema: {error}")
    regions = payload.get("regions") if isinstance(payload, dict) else None
    if not isinstance(payload, dict) or payload.get("version") != 1:
        failures.append(f"{pack_id} municipal payload schema/version is invalid")
        return
    if payload.get("retrieved_at") != envelope.get("generated_at"):
        failures.append(f"{pack_id} municipal payload date differs from its pack date")
    if not isinstance(regions, list) or len(regions) != 1 or not isinstance(regions[0], dict):
        failures.append(f"{pack_id} must contain exactly one municipal region")
        return

    region = regions[0]
    required = {
        "id", "authority_id", "name", "scope", "routing_mode", "routing_source",
        "match_value", "state_aliases", "place_aliases", "envelope", "source_name",
        "source_home_url", "source_url", "source_license", "attribution",
        "official_scope_reference", "routing_note", "limitations", "exclusions", "source_object_id",
    }
    missing = sorted(required - set(region))
    if missing:
        failures.append(f"{pack_id} municipal region is missing fields: {missing}")
    for field in (
        "region_id", "authority_id", "routing_mode", "routing_source", "source_object_id"
    ):
        actual_field = "id" if field == "region_id" else field
        if region.get(actual_field) != expected[field]:
            failures.append(
                f"{pack_id} {actual_field} is {region.get(actual_field)!r}, "
                f"want {expected[field]!r}"
            )

    authorities = envelope.get("authorities")
    authority_ids = {
        item.get("id") for item in authorities or []
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    if authority_ids != {expected["authority_id"]}:
        failures.append(f"{pack_id} municipal authority inventory is {sorted(authority_ids)!r}")
    for field in ("source_home_url", "source_url", "official_scope_reference"):
        if not isinstance(region.get(field), str) or not region[field].startswith("https://"):
            failures.append(f"{pack_id} {field} is not an HTTPS source")
    if pack_id != "in-tg-routing" and "ODbL" not in str(region.get("source_license")):
        failures.append(f"{pack_id} does not identify its ODbL source licence")
    if pack_id == "in-tg-routing" and "no boundary geometry" not in str(
        region.get("source_license")
    ):
        failures.append("in-tg-routing does not state its no-redistribution policy")
    expected_attribution = {
        "in-gj-routing": "OpenCity",
        "in-tg-routing": "TGRAC",
    }.get(pack_id, "OpenStreetMap")
    if expected_attribution not in str(region.get("attribution")):
        failures.append(f"{pack_id} does not preserve {expected_attribution} attribution")
    if not all(
        isinstance(region.get(field), list) and region[field]
        for field in ("state_aliases", "place_aliases", "limitations")
    ):
        failures.append(f"{pack_id} aliases or limitations are missing")
    exclusions = region.get("exclusions")
    if not isinstance(exclusions, list):
        failures.append(f"{pack_id} has no exclusion inventory")
    elif pack_id == "in-tg-routing":
        if (
            len(exclusions) != 1
            or exclusions[0].get("id") != "secunderabad-cantonment"
            or exclusions[0].get("mode") != "official_point_query"
            or exclusions[0].get("query_spatial_rel") != "esriSpatialRelIntersects"
        ):
            failures.append("in-tg-routing does not pin the exact Cantonment query")
    elif exclusions:
        failures.append(f"{pack_id} has an unexpected exclusion")

    relevance = region.get("envelope")
    envelope_values = [
        relevance.get(field) if isinstance(relevance, dict) else None
        for field in ("min_lng", "min_lat", "max_lng", "max_lat")
    ]
    if not all(isinstance(value, (int, float)) and math.isfinite(value)
               for value in envelope_values) or not (
        envelope_values[0] < envelope_values[2]
        and envelope_values[1] < envelope_values[3]
    ):
        failures.append(f"{pack_id} has an invalid municipal relevance envelope")

    geometry_fields = {"coordinate_precision", "area_km2", "bbox", "geometry_sha256", "geometry"}
    point_query_fields = {
        "query_url", "query_where", "query_geometry_type", "query_in_sr",
        "query_spatial_rel", "official_area_km2", "legal_references",
    }
    if expected["routing_mode"] == "boundary":
        if not geometry_fields.issubset(region):
            failures.append(f"{pack_id} boundary region has incomplete geometry metadata")
            return
        geometry = region.get("geometry")
        if not isinstance(geometry, dict) or geometry.get("type") not in {"Polygon", "MultiPolygon"}:
            failures.append(f"{pack_id} boundary geometry is not a polygon")
            return
        geometry_bytes = json.dumps(
            geometry, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        calculated = hashlib.sha256(geometry_bytes).hexdigest()
        if region.get("geometry_sha256") != calculated:
            failures.append(
                f"{pack_id} geometry digest is {region.get('geometry_sha256')!r}, "
                f"want {calculated!r}"
            )
        if region.get("coordinate_precision") != 7:
            failures.append(f"{pack_id} geometry coordinate precision is not pinned to 7")
        if not isinstance(region.get("area_km2"), (int, float)) or region["area_km2"] <= 0:
            failures.append(f"{pack_id} geometry has no positive reviewed area")
    elif expected["routing_mode"] == "official_point_query":
        if not point_query_fields.issubset(region):
            failures.append(f"{pack_id} official point route has incomplete query metadata")
        if region.get("query_spatial_rel") != "esriSpatialRelWithin":
            failures.append(f"{pack_id} CURE query does not require full envelope containment")
        if region.get("query_in_sr") != 4326:
            failures.append(f"{pack_id} CURE query is not pinned to EPSG:4326")
        references = region.get("legal_references")
        titles = [item.get("title", "") for item in references or [] if isinstance(item, dict)]
        if not any("G.O.Ms.No.292" in title for title in titles):
            failures.append(f"{pack_id} does not pin G.O.Ms.No.292")
        if not any("G.O.Ms.No.55" in title for title in titles):
            failures.append(f"{pack_id} does not pin G.O.Ms.No.55")
        if geometry_fields.intersection(region):
            failures.append(f"{pack_id} redistributes geometry for a live official query")
    elif geometry_fields.intersection(region) or point_query_fields.intersection(region):
        failures.append(f"{pack_id} structured route implies unsupported spatial data")


def check_catalog(failures: list[str]) -> dict:
    static_manifest = ROOT / "static" / "pack-manifest.json"
    pages_manifest = ROOT / "docs" / "pack-manifest.json"
    if not MANIFEST_PATH.is_file() or not static_manifest.is_file() or not pages_manifest.is_file():
        failures.append("pack-manifest.json is missing from static, Android, or GitHub Pages assets")
        return {}
    if MANIFEST_PATH.read_bytes() != static_manifest.read_bytes():
        failures.append("static and Android pack manifests differ")
    if pages_manifest.read_bytes() != static_manifest.read_bytes():
        failures.append("static and GitHub Pages pack manifests differ")

    try:
        manifest = load_manifest()
    except (OSError, ValueError) as error:
        failures.append(f"pack manifest is not readable JSON: {error}")
        return {}

    if manifest.get("format") != "pothole-pack-manifest":
        failures.append(f"bad pack manifest format: {manifest.get('format')!r}")
    if manifest.get("schema_version") != 1 or manifest.get("catalog_version") != 1:
        failures.append("pack manifest schema/catalog version is not 1")
    cache = manifest.get("cache")
    if not isinstance(cache, dict):
        failures.append("pack manifest cache policy is missing")
    else:
        if not isinstance(cache.get("max_bytes"), int) or not 1_048_576 <= cache["max_bytes"] <= 67_108_864:
            failures.append(f"pack cache size is unsafe: {cache.get('max_bytes')!r}")
        routing_days = cache.get("routing_max_unused_days")
        tender_days = cache.get("tender_max_unused_days")
        if not isinstance(routing_days, int) or not 1 <= routing_days <= 90:
            failures.append(f"routing-pack unused-age policy is unsafe: {routing_days!r}")
        if not isinstance(tender_days, int) or not 1 <= tender_days <= 90:
            failures.append(f"tender-pack unused-age policy is unsafe: {tender_days!r}")
        if isinstance(routing_days, int) and isinstance(tender_days, int) and tender_days > routing_days:
            failures.append("large tender packs are retained longer than routing packs")

    resources = manifest.get("resources")
    if not isinstance(resources, dict):
        failures.append("pack manifest resources is not an object")
        return manifest
    if set(resources) != set(EXPECTED_RESOURCES):
        failures.append(
            f"pack catalog IDs differ: got {sorted(resources)}, want {sorted(EXPECTED_RESOURCES)}"
        )

    urls: set[str] = set()
    for pack_id, (state_code, kind, pack_format, adapter) in EXPECTED_RESOURCES.items():
        resource = resources.get(pack_id)
        if not isinstance(resource, dict):
            continue
        if resource.get("pack_id") != pack_id:
            failures.append(f"{pack_id} resource does not repeat its stable pack_id")
        if resource.get("state_code") != state_code or resource.get("kind") != kind:
            failures.append(f"{pack_id} state/kind metadata does not match its ID")
        if resource.get("pack_version") != 1:
            failures.append(f"{pack_id} pack_version is not 1")
        if resource.get("schema_version") != 1:
            failures.append(f"{pack_id} schema_version is not 1")
        if resource.get("adapter") != adapter:
            failures.append(
                f"{pack_id} adapter is {resource.get('adapter')!r}, want {adapter!r}"
            )
        if pack_id == "in-wb-routing":
            if resource.get("statewide") is not True:
                failures.append("in-wb-routing is not declared statewide")
            if not str(resource.get("coverage_scope") or "").startswith(
                "Full State of West Bengal"
            ):
                failures.append("in-wb-routing does not declare full West Bengal coverage")
        sha = resource.get("sha256")
        if not isinstance(sha, str) or len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
            failures.append(f"{pack_id} has an invalid SHA-256 pin")
            continue
        try:
            relative = resource_relative_path(pack_id)
            hosted_path = pack_path(pack_id)
            envelope, _ = read_pack(pack_id)
        except (AssertionError, OSError, ValueError) as error:
            failures.append(str(error))
            continue
        expected_name = f"{kind}-{sha}.json"
        if pathlib.PurePosixPath(relative).name != expected_name:
            failures.append(f"{pack_id} path is not content-addressed by its full digest")
        if relative != f"states/{state_code.lower()}/{expected_name}":
            failures.append(f"{pack_id} is outside its state/kind pack directory: {relative}")
        expected_catalog_path = f"packs/v1/{relative}"
        if resource.get("path") != expected_catalog_path:
            failures.append(f"{pack_id} catalog path is not canonical: {resource.get('path')!r}")
        expected_url = PRODUCTION_SITE_ROOT + expected_catalog_path
        if resource.get("url") != expected_url:
            failures.append(f"{pack_id} production URL is not the pinned GitHub Pages URL")
        if expected_url in urls:
            failures.append(f"duplicate production pack URL: {expected_url}")
        urls.add(expected_url)
        if not hosted_path.is_file():
            failures.append(f"hosted pack is absent: {hosted_path}")

        identity = {
            "format": pack_format,
            "schema_version": 1,
            "pack_id": pack_id,
            "pack_version": 1,
            "state_code": state_code,
        }
        for field, want in identity.items():
            if envelope.get(field) != want:
                failures.append(f"{pack_id} envelope {field} is {envelope.get(field)!r}, want {want!r}")
        if envelope.get("adapter") != resource.get("adapter"):
            failures.append(f"{pack_id} envelope adapter differs from the catalog")
        if not isinstance(envelope.get("generated_at"), str) or not envelope["generated_at"]:
            failures.append(f"{pack_id} has no generation timestamp")
        if kind == "routing":
            if not isinstance(envelope.get("adapter"), str) or not envelope["adapter"]:
                failures.append(f"{pack_id} has no routing adapter")
            authorities = envelope.get("authorities")
            if not isinstance(authorities, list):
                failures.append(f"{pack_id} has no authority inventory")
            elif state_code != "KA" and not authorities:
                failures.append(f"{pack_id} has an empty authority inventory")
            if not isinstance(envelope.get("payload"), dict):
                failures.append(f"{pack_id} has no routing payload object")
            if pack_id in MUNICIPAL_CITY_RESOURCES:
                check_municipal_city_pack(pack_id, envelope, failures)
            if pack_id == "in-wb-routing":
                wb_authority_ids = {
                    item.get("id") for item in authorities or [] if isinstance(item, dict)
                }
                if wb_authority_ids != {"wb-kmc", "wb-statewide-unverified"}:
                    failures.append(
                        "in-wb-routing authority inventory is "
                        f"{sorted(wb_authority_ids)!r}"
                    )
                payload = envelope.get("payload")
                regions = payload.get("regions") if isinstance(payload, dict) else None
                if not isinstance(payload, dict) or payload.get("version") != 2:
                    failures.append("in-wb-routing payload is not statewide schema version 2")
                elif not isinstance(regions, dict) or set(regions) != {"kmc", "west_bengal"}:
                    failures.append("in-wb-routing does not contain exactly KMC and West Bengal")
                else:
                    if regions["kmc"].get("authority_id") != "wb-kmc":
                        failures.append("in-wb-routing KMC authority pin changed")
                    state = regions["west_bengal"]
                    if (
                        state.get("authority_id") != "wb-statewide-unverified"
                        or state.get("source_relation_id") != 1_960_177
                    ):
                        failures.append("in-wb-routing statewide authority or relation pin changed")
        else:
            tenders = envelope.get("tenders")
            if not isinstance(tenders, list) or not tenders:
                failures.append(f"{pack_id} has no tender records")
            expected_records = resource.get("records")
            if expected_records is not None and len(tenders or []) != expected_records:
                failures.append(
                    f"{pack_id} record count is {len(tenders or [])}, want {expected_records}"
                )

    for asset_root in (ROOT / "static", ROOT / "android-app" / "www"):
        present = sorted(name for name in LEGACY_BUNDLED_FILES if (asset_root / name).exists())
        if present:
            failures.append(f"legacy state data remains bundled in {asset_root}: {present}")
        if (asset_root / "packs").exists():
            failures.append(f"downloadable state packs were copied into {asset_root}")
    return manifest


def open_page(context, *, wait_for_hooks: bool = True):
    page = context.new_page()
    page.goto(APP)
    page.wait_for_load_state("networkidle")
    if wait_for_hooks:
        page.wait_for_function(HOOKS_READY, timeout=30000)
    return page


def records(page) -> list[dict]:
    return page.evaluate(f"""async () => {{
      {IDB_HELPERS}
      const rows = await allPackRecords();
      return rows.map((row) => ({{
        cache_key: row.cache_key, pack_id: row.pack_id, pack_version: row.pack_version,
        state_code: row.state_code, kind: row.kind, sha256: row.sha256,
        bytes: row.bytes, installed_at: row.installed_at, last_used_at: row.last_used_at,
        blob_size: row.blob instanceof Blob ? row.blob.size : null,
      }}));
    }}""")


def check_download_cache_offline(browser, manifest: dict, failures: list[str]) -> None:
    pack_id = "in-dl-routing"
    resource = manifest["resources"][pack_id]
    pattern = route_pattern(pack_id)
    downloads = 0
    download_headers: list[dict[str, str]] = []
    download_urls: list[str] = []

    context = browser.new_context(viewport={"width": 390, "height": 844})
    context.add_cookies([{"name": "pack_test_secret", "value": "must-not-leak", "url": APP}])

    def count_download(route):
        nonlocal downloads
        downloads += 1
        download_headers.append(route.request.headers)
        download_urls.append(route.request.url)
        route.continue_()

    context.route("**/packs/v1/**", count_download)
    page = open_page(context)
    downloads_before_load = downloads
    result = page.evaluate(f"""async () => {{
      {IDB_HELPERS}
      const P = StandaloneAPI.__pure;
      await P.resetStatePackMemory();
      const loaded = await Promise.all([
        P.loadStatePack({json.dumps(pack_id)}),
        P.loadStatePack({json.dumps(pack_id)}),
        P.loadStatePack({json.dumps(pack_id)}),
      ]);
      const catalog = await P.getStatePackManifest();
      const resolved = new URL(P.resolvePackUrl(catalog.resources[{json.dumps(pack_id)}]), location.href);
      const rows = await allPackRecords();
      const row = rows.find((item) => item.pack_id === {json.dumps(pack_id)});
      let blobSha = null;
      if (row && row.blob instanceof Blob) {{
        const digest = await crypto.subtle.digest("SHA-256", await row.blob.arrayBuffer());
        blobSha = [...new Uint8Array(digest)].map((x) => x.toString(16).padStart(2, "0")).join("");
      }}
      return {{
        loaded: loaded.every(Boolean), format: catalog && catalog.format,
        resolved_origin: resolved.origin, resolved_path: resolved.pathname,
        rows: rows.length, row: row && {{
          cache_key: row.cache_key, pack_version: row.pack_version,
          state_code: row.state_code, kind: row.kind, sha256: row.sha256,
          bytes: row.bytes, installed_at: row.installed_at, last_used_at: row.last_used_at,
          blob_size: row.blob instanceof Blob ? row.blob.size : null,
        }}, blobSha,
      }};
    }}""")
    if not result["loaded"]:
        failures.append("concurrent state-pack callers did not all receive the verified pack")
    if downloads_before_load:
        failures.append(f"idle app startup downloaded {downloads_before_load} state pack(s)")
    if downloads != 1:
        failures.append(f"three concurrent pack loads made {downloads} HTTP requests, want 1")
    if download_urls and not download_urls[0].endswith("/" + resource["path"]):
        failures.append(f"runtime downloaded the wrong state pack: {download_urls[0]}")
    if download_headers and ("cookie" in download_headers[0] or "referer" in download_headers[0]):
        failures.append("state-pack download leaked app cookies or a referrer")
    if result["format"] != "pothole-pack-manifest":
        failures.append("runtime did not return the validated bundled manifest")
    if result["resolved_origin"] != "http://localhost:8765":
        failures.append(f"localhost pack URL did not stay local: {result['resolved_origin']}")
    expected_path = "/" + resource["path"]
    if result["resolved_path"] != expected_path:
        failures.append(
            f"localhost pack URL path is {result['resolved_path']!r}, want {expected_path!r}"
        )
    row = result.get("row") or {}
    expected_row = {
        "pack_version": resource["pack_version"],
        "state_code": resource["state_code"],
        "kind": resource["kind"],
        "sha256": resource["sha256"],
        "bytes": resource["bytes"],
        "blob_size": resource["bytes"],
    }
    for key, want in expected_row.items():
        if row.get(key) != want:
            failures.append(f"cached {pack_id} {key} is {row.get(key)!r}, want {want!r}")
    if not isinstance(row.get("cache_key"), str) or pack_id not in row["cache_key"]:
        failures.append(f"cached {pack_id} has no stable cache key")
    if not row.get("installed_at") or not row.get("last_used_at"):
        failures.append(f"cached {pack_id} has no install/use timestamps")
    if result.get("blobSha") != resource["sha256"]:
        failures.append("cached pack blob does not hash to the catalog pin")

    # A fresh document has no in-memory cache. With the pack transport blocked it must
    # still use the verified IndexedDB copy and must not make a speculative request.
    page.close()
    context.unroute("**/packs/v1/**", count_download)
    offline_attempts = 0
    page = context.new_page()

    def block_pack(route):
        nonlocal offline_attempts
        offline_attempts += 1
        route.abort()

    page.route(pattern, block_pack)
    page.goto(APP)
    page.wait_for_load_state("networkidle")
    page.wait_for_function(HOOKS_READY, timeout=30000)
    offline = page.evaluate(
        f"async () => !!(await StandaloneAPI.__pure.loadStatePack({json.dumps(pack_id)}))"
    )
    if not offline:
        failures.append("verified state pack was unavailable offline after a document reload")
    if offline_attempts:
        failures.append("offline cached pack still attempted a network download")

    # Bytes in IndexedDB are untrusted. A changed blob must be deleted and must never be
    # returned while the network is unavailable.
    page.evaluate(f"""async () => {{
      {IDB_HELPERS}
      await mutatePackRecords((store, rows) => {{
        const row = rows.find((item) => item.pack_id === {json.dumps(pack_id)});
        row.blob = new Blob(["tampered-state-pack"], {{type: "application/json"}});
        store.put(row);
      }});
    }}""")
    page.close()
    tamper_attempts = 0
    page = context.new_page()

    def block_tampered_pack(route):
        nonlocal tamper_attempts
        tamper_attempts += 1
        route.abort()

    page.route(pattern, block_tampered_pack)
    page.goto(APP)
    page.wait_for_load_state("networkidle")
    page.wait_for_function(HOOKS_READY, timeout=30000)
    tampered = page.evaluate(
        f"async () => !!(await StandaloneAPI.__pure.loadStatePack({json.dumps(pack_id)}))"
    )
    remaining = records(page)
    if tampered:
        failures.append("tampered cached pack was accepted")
    if tamper_attempts < 1:
        failures.append("tampered cached pack did not attempt a verified replacement download")
    if any(row["pack_id"] == pack_id for row in remaining):
        failures.append("tampered cached pack was not removed")
    context.close()


def check_bad_network_pack(browser, failures: list[str]) -> None:
    pack_id = "in-dl-routing"
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    page.route(
        route_pattern(pack_id),
        lambda route: route.fulfill(status=200, content_type="application/json", body="{}"),
    )
    page.goto(APP)
    page.wait_for_load_state("networkidle")
    page.wait_for_function(HOOKS_READY, timeout=30000)
    accepted = page.evaluate(
        f"async () => !!(await StandaloneAPI.__pure.loadStatePack({json.dumps(pack_id)}))"
    )
    if accepted:
        failures.append("network response with the wrong digest was accepted")
    if any(row["pack_id"] == pack_id for row in records(page)):
        failures.append("network response with the wrong digest was cached")
    context.close()


def check_bad_manifest(browser, failures: list[str]) -> None:
    def manifest_response(status: int, body: str):
        def handler(route):
            route.fulfill(status=status, content_type="application/json", body=body)

        return handler

    for label, status, body in [
        ("missing", 404, "missing"),
        ("malformed", 200, '{"format":"pothole-pack-manifest","schema_version":1}'),
    ]:
        context = browser.new_context(viewport={"width": 390, "height": 844})
        pack_requests = 0

        def count_pack(route):
            nonlocal pack_requests
            pack_requests += 1
            route.abort()

        context.route(
            "**/pack-manifest.json",
            manifest_response(status, body),
        )
        context.route("**/packs/v1/**", count_pack)
        page = open_page(context)
        result = page.evaluate("""async () => {
          try {
            return {loaded: !!(await StandaloneAPI.__pure.loadStatePack("in-dl-routing")), error: null};
          } catch (error) {
            return {loaded: false, error: String(error && error.message || error)};
          }
        }""")
        if result["loaded"]:
            failures.append(f"{label} bundled pack manifest did not fail closed")
        if result["error"]:
            failures.append(f"{label} bundled pack manifest escaped as an exception: {result['error']}")
        if pack_requests:
            failures.append(f"{label} manifest still triggered {pack_requests} pack request(s)")
        context.close()


def check_age_eviction_and_wipe(browser, failures: list[str]) -> None:
    active_id = "in-dl-routing"
    old_id = "in-wb-routing"
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = open_page(context)
    result = page.evaluate(f"""async () => {{
      {IDB_HELPERS}
      const P = StandaloneAPI.__pure;
      const loaded = await Promise.all([
        P.loadStatePack({json.dumps(active_id)}), P.loadStatePack({json.dumps(old_id)}),
      ]);
      await mutatePackRecords((store, rows) => {{
        for (const row of rows) {{
          const now = Number(row.last_used_at) > 1e11 ? Date.now() : Date.now() / 1000;
          const day = Number(row.last_used_at) > 1e11 ? 86400000 : 86400;
          row.last_used_at = now - 120 * day;
          store.put(row);
        }}
        const source = rows.find((row) => row.pack_id === {json.dumps(active_id)});
        store.put({{
          ...source, cache_key: "unlisted-pack@1", pack_id: "unlisted-pack",
          state_code: "xx", last_used_at: 1, installed_at: 1,
        }});
      }});
      P.resetStatePackMemory();
      await P.pruneStatePacks({json.dumps(active_id)});
      const afterPrune = (await allPackRecords()).map((row) => row.pack_id).sort();
      await StandaloneAPI.handle("/api/reports", {{method: "DELETE"}});
      const afterWipe = (await allPackRecords()).map((row) => row.pack_id).sort();
      return {{loaded: loaded.every(Boolean), afterPrune, afterWipe}};
    }}""")
    if not result["loaded"]:
        failures.append("could not seed routing packs for eviction test")
    if result["afterPrune"] != [active_id]:
        failures.append(
            f"unused/unlisted eviction left {result['afterPrune']!r}, want only active {active_id}"
        )
    if result["afterWipe"]:
        failures.append(f"Delete all app data left state packs behind: {result['afterWipe']!r}")
    context.close()


def check_lru_limit(browser, manifest: dict, failures: list[str]) -> None:
    tender_id = "in-ka-tenders"
    active_id = "in-dl-routing"
    custom = copy.deepcopy(manifest)
    tender_bytes = custom["resources"][tender_id]["bytes"]
    active_bytes = custom["resources"][active_id]["bytes"]
    # The tender pack fits by itself, but the next small routing pack crosses the cap.
    custom["cache"]["max_bytes"] = tender_bytes + max(1, active_bytes // 2)

    context = browser.new_context(viewport={"width": 390, "height": 844})
    context.route(
        "**/pack-manifest.json",
        lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps(custom, separators=(",", ":")),
        ),
    )
    page = open_page(context)
    result = page.evaluate(f"""async () => {{
      {IDB_HELPERS}
      const P = StandaloneAPI.__pure;
      const first = await P.loadStatePack({json.dumps(tender_id)});
      const second = await P.loadStatePack({json.dumps(active_id)});
      P.resetStatePackMemory();
      await P.pruneStatePacks({json.dumps(active_id)});
      const rows = await allPackRecords();
      return {{loaded: !!first && !!second,
        ids: rows.map((row) => row.pack_id).sort(),
        bytes: rows.reduce((sum, row) => sum + Number(row.bytes || 0), 0)}};
    }}""")
    if not result["loaded"]:
        failures.append("could not load packs for LRU size-limit test")
    if result["ids"] != [active_id]:
        failures.append(f"LRU size limit left {result['ids']!r}, want only {active_id}")
    if result["bytes"] > custom["cache"]["max_bytes"]:
        failures.append("LRU pruning left the state-pack cache over its byte limit")
    context.close()


def main() -> None:
    failures: list[str] = []
    manifest = check_catalog(failures)
    if not manifest or not isinstance(manifest.get("resources"), dict):
        print("FAIL")
        for failure in failures:
            print("  -", failure)
        sys.exit(1)

    with sync_playwright() as playwright:
        # Exercise normal browser security; development pack URLs resolve same-origin.
        browser = playwright.chromium.launch()
        check_download_cache_offline(browser, manifest, failures)
        check_bad_network_pack(browser, failures)
        check_bad_manifest(browser, failures)
        check_age_eviction_and_wipe(browser, failures)
        check_lru_limit(browser, manifest, failures)
        browser.close()

    if failures:
        print("FAIL")
        for failure in failures:
            print("  -", failure)
        sys.exit(1)
    print("STATE PACK TEST PASS (catalog, integrity, offline, fail-closed, eviction, concurrency)")


if __name__ == "__main__":
    main()
