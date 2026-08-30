#!/usr/bin/env python3
"""Offline guard that Web, native Drive and evaluator share one binary v13 contract."""
import importlib.util, json, pathlib, re, sys, tempfile, textwrap
from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("road_eval", ROOT / "eval" / "run_eval.py")
road_eval = importlib.util.module_from_spec(spec)
spec.loader.exec_module(road_eval)
client = (ROOT / "static" / "standalone.js").read_text()
evaluator_source = (ROOT / "eval" / "run_eval.py").read_text()
native_dir = (ROOT / "android-app" / "android" / "app" / "src" / "main" / "java" /
              "dev" / "aiengg" / "potholereporter" / "drive")
native_engine = (native_dir / "NativeInferenceEngine.kt").read_text()
native_contract = (native_dir / "NativeDetectionContract.kt").read_text()
native_request = (native_dir / "NativeInferenceRequest.kt").read_text()
native_verdict = (native_dir / "NativeDetectionVerdict.kt").read_text()
native = "\n".join((native_engine, native_contract, native_request, native_verdict))
entities = (ROOT / "android-app" / "android" / "app" / "src" / "main" / "java" /
            "dev" / "aiengg" / "potholereporter" / "db" / "Entities.kt").read_text()
database = (ROOT / "android-app" / "android" / "app" / "src" / "main" / "java" /
            "dev" / "aiengg" / "potholereporter" / "db" / "PotholeDatabase.kt").read_text()
plugin = (ROOT / "android-app" / "android" / "app" / "src" / "main" / "java" /
          "dev" / "aiengg" / "potholereporter" / "plugin" / "DriveModePlugin.kt").read_text()
labels = json.loads((ROOT / "eval" / "labels.json").read_text())["images"]
fails = []


drive_preprocessing = re.search(
    r"let imageInputs, dataUrl, fullViews = null, contextDataUrl = null;"
    r"\s*if \(driveMode\) \{(?P<body>.*?)\n\s*\} else \{",
    client,
    re.DOTALL,
)
drive_preparation_gate_start = client.find("let driveImagePreparationTail = Promise.resolve();")
drive_preparation_gate_end = client.find(
    "\n\n  async function createReport", drive_preparation_gate_start)
drive_preparation_gate = (client[drive_preparation_gate_start:drive_preparation_gate_end]
                          if drive_preparation_gate_start >= 0
                          and drive_preparation_gate_end > drive_preparation_gate_start else "")
to_data_url_start = client.find("async function toDataUrl(")
to_data_url_end = client.find("\n\n  // ---------- pipeline ----------", to_data_url_start)
to_data_url_source = (client[to_data_url_start:to_data_url_end]
                      if to_data_url_start >= 0 and to_data_url_end > to_data_url_start else "")


def drive_preprocessing_is_sequential():
    if not drive_preprocessing:
        return False
    body = drive_preprocessing.group("body")
    return ("for (const p of photos)" in body
            and "MAX_PREPARED_FRAME_DIMENSION" in body
            and "1920" not in body
            and "Promise.all(photos.map" not in body)


def drive_preparation_gate_is_safe():
    if not drive_preprocessing or not drive_preparation_gate:
        return False
    body = drive_preprocessing.group("body")
    return ("const wait = driveImagePreparationTail;" in drive_preparation_gate
            and "driveImagePreparationTail = new Promise" in drive_preparation_gate
            and "await wait;" in drive_preparation_gate
            and re.search(r"finally\s*\{\s*release\(\);\s*\}", drive_preparation_gate)
            and "withDriveImagePreparation(async () =>" in body
            and "analyzeImage(" not in body)


def image_preprocessing_always_releases_resources():
    return ("let c = null;" in to_data_url_source
            and "return c.toDataURL(\"image/jpeg\", quality);" in to_data_url_source
            and re.search(
                r"finally\s*\{\s*try\s*\{\s*if \(bmp\.close\) bmp\.close\(\);\s*\}"
                r"\s*finally\s*\{.*?c\.width = 0; c\.height = 0;.*?\}",
                to_data_url_source,
                re.DOTALL,
            ))


def parse_js_object_constant(source, name):
    """Parse the JSON-compatible object literal assigned to a JS const."""
    match = re.search(rf"\bconst\s+{re.escape(name)}\s*=\s*", source)
    if not match:
        raise ValueError(f"JavaScript constant {name} was not found")
    start = source.find("{", match.end())
    if start < 0:
        raise ValueError(f"JavaScript constant {name} is not an object")

    depth = 0
    quote = None
    escaped = False
    line_comment = False
    block_comment = False
    end = None
    index = start
    while index < len(source):
        char = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""
        if line_comment:
            if char in "\r\n":
                line_comment = False
            index += 1
            continue
        if block_comment:
            if char == "*" and following == "/":
                block_comment = False
                index += 2
            else:
                index += 1
            continue
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char == "/" and following == "/":
            line_comment = True
            index += 2
            continue
        if char == "/" and following == "*":
            block_comment = True
            index += 2
            continue
        if char in ('"', "'", "`"):
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = index + 1
                break
        index += 1
    if end is None:
        raise ValueError(f"JavaScript constant {name} has an unterminated object")

    literal = source[start:end]
    literal = re.sub(r'([,{]\s*)([A-Za-z_$][A-Za-z0-9_$]*)\s*:',
                     r'\1"\2":', literal)
    literal = re.sub(r",\s*([}\]])", r"\1", literal)
    return json.loads(literal)


web_prompt = road_eval.prompts()["baseline"]
try:
    web_schema = parse_js_object_constant(client, "ASSESS_SCHEMA")
except (ValueError, json.JSONDecodeError):
    web_schema = None
native_prompt_match = re.search(
    r'val DETECT_PROMPT =\s*"""(.*?)"""\.trimIndent\(\)', native_contract, re.S
)
native_prompt = (textwrap.dedent(native_prompt_match.group(1)).strip("\n")
                 if native_prompt_match else None)
native_schema_match = re.search(
    r'val SCHEMA_JSON =\s*"""(.*?)"""\.trimIndent\(\)', native_contract, re.S
)
try:
    native_schema = json.loads(textwrap.dedent(native_schema_match.group(1)).strip()) \
        if native_schema_match else None
except json.JSONDecodeError:
    native_schema = None


def check(name, condition):
    print(f"  {'ok  ' if condition else 'FAIL'} {name}")
    if not condition:
        fails.append(name)


check("schema version", 'const SCHEMA_VERSION = 7;' in client and road_eval.SCHEMA_VERSION == 7)
check("prompt version", f'const PROMPT_VERSION = "{road_eval.PROMPT_VERSION}";' in client)
check("native prompt version", f'PROMPT_VERSION = "{road_eval.PROMPT_VERSION}"' in native)
check("Web Drive preprocesses complete burst frames sequentially to bound peak memory",
      drive_preprocessing_is_sequential())
check("Web and evaluator use the native 1280px downscale-only full-frame transform",
      "const MAX_PREPARED_FRAME_DIMENSION = 1280;" in client
      and "const scale = Math.min(1, maxDim / Math.max(sw, sh));" in to_data_url_source
      and road_eval.MAX_PREPARED_FRAME_DIMENSION == 1280)
check("Web Drive serializes preparation globally and releases the gate on errors",
      drive_preparation_gate_is_safe())
check("Web image preprocessing always releases bitmap and canvas backing storage",
      image_preprocessing_always_releases_resources())
check("native and Web use the exact same base detection prompt",
      native_prompt is not None and native_prompt == web_prompt)
check("Web assessment schema is parsed from the shipped JavaScript",
      web_schema is not None)
check("native, Web and evaluator use the exact same assessment schema",
      web_schema is not None and native_schema == web_schema == road_eval.SCHEMA)
layout_contract_fragments = (
    "complete camera frames",
    "chronological frame",
    "No image is cropped, tiled, masked, or limited to a region of interest.",
)
check("native, Web and evaluator describe the Drive image layout identically",
      all(all(fragment in source for fragment in layout_contract_fragments)
          for source in (client, native, evaluator_source))
      and "road-region" not in native.lower())
check("spatial crop machinery is absent from every current detection implementation",
      all(term not in client for term in (
          "selectRoadRegion", "ROAD_REGION_RATIOS", "cropRoad",
          "MAX_PREPARED_ROAD_DIMENSION"))
      and all(term not in evaluator_source for term in (
          "select_road_region", "ROAD_REGION_RATIOS", "road_crop",
          "MAX_PREPARED_ROAD_DIMENSION"))
      and all(term not in native for term in (
          "RoadRegionSelector", "prepareRoadBandDataUrl",
          "MAX_PREPARED_ROAD_DIMENSION")))
check("schema has no model-confidence gate", "confidence" not in road_eval.SCHEMA["properties"])
check("schema has one binary pothole verdict",
      road_eval.SCHEMA["properties"].get("is_pothole") == {"type": "boolean"})
for removed in ("reportable", "assessment", "damage_type"):
    check(f"model no longer predicts {removed}", removed not in road_eval.SCHEMA["properties"])
check("speed-breaker veto is required",
      "looks_like_speed_breaker" in road_eval.SCHEMA["required"])
check("speed-breaker veto is boolean",
      road_eval.SCHEMA["properties"].get("looks_like_speed_breaker") == {"type": "boolean"})
check("surface classification is required", "surface_type" in road_eval.SCHEMA["required"])
check("temporary traffic surface is an explicit schema value",
      "temporary_drivable_surface" in road_eval.SCHEMA["properties"]["surface_type"]["enum"]
      and '"temporary_drivable_surface"' in client
      and '"temporary_drivable_surface"' in native)
check("prompt explicitly distinguishes speed breakers",
      "speed breaker" in road_eval.prompts()["baseline"].lower())
check("prompt makes ambiguity a negative",
      "ambiguous geometry is no" in road_eval.prompts()["baseline"].lower())
check("Drive motion proves use of an unsealed traffic path",
      "coherent forward motion along a continuous wheel path" in web_prompt.lower()
      and "proves this use even when no second vehicle is visible" in web_prompt.lower())
check("one-open-side cavity remains eligible without admitting generic roughness",
      all(term in web_prompt.lower() for term in (
          "it does not mean a closed circular rim",
          "one boundary blending into surrounding failed material",
          "road-wide grading, corrugation, broad roughness",
          "do not turn it into general roughness",
      )))
check("partial temporal visibility is not treated as contradiction",
      "at least two show the same footprint" in web_prompt.lower()
      and "leaving the final full frame is not disagreement" in web_prompt.lower())
check("drivable lane-edge cavities remain eligible without admitting roadside damage",
      all(term in web_prompt.lower() for term in (
          "opening removes part of the flat traffic surface",
          "creates a wheel-reachable drop",
          "an intact kerb or gutter separates the entire opening from traffic",
      ))
      and ", road edge," not in web_prompt.lower())
check("prompt defines every fallback size band",
      all(term in road_eval.prompts()["baseline"].lower()
          for term in ("below 30 cm", "30 to 60 cm", "above 60 cm")))
check("native prompt defines the same fallback size bands",
      all(term in native.lower() for term in ("below 30 cm", "30 to 60 cm", "above 60 cm")))
check("native schema exposes the binary verdict",
      '"is_pothole": { "type": "boolean" }' in native and
      '"has_localized_cavity": { "type": "boolean" }' in native)
check("native Drive rejects single-view output despite the shared schema",
      'if (temporalConsistency != "consistent")' in native_verdict)
check("native persists and syncs every binary physical gate",
      all(term in entities for term in ("looksLikeSpeedBreaker", "hasLocalizedCavity"))
      and all(f'put("{term}"' in plugin for term in
              ("looks_like_speed_breaker", "has_localized_cavity"))
      and all(term in entities for term in
              ("surfaceType", "defectType", "measurementProvenance", "measurementConfidence"))
      and all(f'put("{term}"' in plugin for term in
              ("surface_type", "defect_type", "measurement_provenance", "measurement_confidence"))
      and "MIGRATION_5_6" in database and "version = 6" in database)
for field in road_eval.SCHEMA["required"]:
    check(f"required field {field}", f'"{field}"' in client)

request = road_eval.build_request(["one", "two", "three", "four", "five"],
                                  "PROMPT", "gpt-5.6", "original")
content = request["input"][0]["content"]
images = [item for item in content if item["type"] == "input_image"]
check("request image cap", len(images) == 4)
check("Drive evaluator uses the native output-token ceiling",
      request.get("max_output_tokens") == 1536
      and "MAX_OUTPUT_TOKENS = 1_536" in native_contract
      and "maxOutputTokens = NativeDetectionContract.MAX_OUTPUT_TOKENS" in native_request
      and 'put("max_output_tokens", maxOutputTokens)' in native_request)
check("manual evaluator does not inherit the native Drive token ceiling",
      "max_output_tokens" not in road_eval.build_request(
          ["one"], "P", "gpt-5.6", "high", mode="manual"))
check("native Drive fails closed outside the bounded production burst",
      "burstFrames.size !in NativeDriveCameraManager.MIN_DETECTION_SOURCE_FRAMES.." in native
      and "NativeRollingBurstWindow.OUTPUT_COUNT" in native)
check("prompt once and last", len([x for x in content if x["type"] == "input_text"]) == 1
      and content[-1]["type"] == "input_text")
check("detail belongs to every image", all(x.get("detail") == "original" for x in images))
check("evaluator disables response storage like both shipped runtimes",
      request.get("store") is False and 'put("store", false)' in native_request
      and "store: false" in client)
check("mini rejects unsupported original detail",
      road_eval.build_request(["one"], "P", "gpt-5-mini", "original")
      ["input"][0]["content"][0]["detail"] == "high")
evaluator_source = pathlib.Path(road_eval.__file__).read_text()
check("evaluator defaults mirror the two production paths",
      road_eval.DRIVE_DEFAULT_MODEL == "gpt-5.6"
      and road_eval.MANUAL_DEFAULT_MODEL == "gpt-5-mini")
check("explicit unsupported evaluator models fail instead of changing silently",
      "unsupported model(s)" in evaluator_source
      and "unknown_models" in evaluator_source)
photo_suffix = road_eval.client_template_constant("PHOTO_ONLY_PROMPT_SUFFIX")
check("manual evaluator attaches the exact shipped Photo-only scope",
      road_eval.effective_prompt("BASE", "manual", "NOTE")
      == "BASE" + photo_suffix + "NOTE")
check("Drive evaluator never attaches the Photo-only suffix",
      road_eval.effective_prompt("BASE", "drive", "NOTE") == "BASENOTE")
check("evaluator records the shipped mode-specific prompt version",
      road_eval.effective_prompt_version("drive") == road_eval.PROMPT_VERSION
      and road_eval.effective_prompt_version("manual")
      == road_eval.client_string_constant("PHOTO_PROMPT_VERSION")
      == "pothole-photo-only-v4")

good = {"is_pothole": True, "looks_like_speed_breaker": False,
        "image_quality": "usable", "surface_type": "bituminous_asphalt",
        "on_drivable_surface": True,
        "has_localized_cavity": True, "has_broken_edge_or_rim": True,
        "has_depth_or_surface_loss": True, "temporal_consistency": "consistent",
        "size": "medium"}
check("complete physical pothole is accepted", road_eval.decision(good) == "accept")
check("manual Photo permits one defensible view",
      road_eval.decision({**good, "temporal_consistency": "single_view"},
                         "manual", 1) == "accept")
check("Drive requires temporal agreement",
      road_eval.decision({**good, "temporal_consistency": "single_view"},
                         "drive", 3) == "reject")
check("Drive cannot turn one source frame into a consistent burst",
      road_eval.decision(good, "drive", 1) == "reject")
check("speed breaker hard-vetoes contradictory damage",
      road_eval.decision({**good, "looks_like_speed_breaker": True}) == "reject")
check("missing speed-breaker verdict fails closed",
      road_eval.decision({key: value for key, value in good.items()
                          if key != "looks_like_speed_breaker"}) == "reject")
check("mistyped speed-breaker verdict fails closed",
      road_eval.decision({**good, "looks_like_speed_breaker": "false"}) == "reject")
check("off-road rejected", road_eval.decision({**good, "on_drivable_surface": False}) == "reject")
check("unknown surface rejected", road_eval.decision({**good, "surface_type": "unknown"}) == "reject")
check("unpaved/non-road surface rejected",
      road_eval.decision({**good, "surface_type": "unpaved_or_nonroad"}) == "reject")
temporary = {**good, "surface_type": "temporary_drivable_surface"}
check("temporary active traffic surface accepts a complete Drive cavity",
      road_eval.decision(temporary, "drive", 3) == "accept")
check("temporary surface fails closed for single Photo",
      road_eval.decision({**temporary, "temporal_consistency": "single_view"},
                         "manual", 1) == "reject")
check("temporary roughness without a localized cavity is NO",
      road_eval.decision({**temporary, "has_localized_cavity": False},
                         "drive", 3) == "reject")
check("surface damage without a localized cavity is NO",
      road_eval.decision({**good, "has_localized_cavity": False}) == "reject")
check("missing broken rim is NO",
      road_eval.decision({**good, "has_broken_edge_or_rim": False}) == "reject")
check("missing visible depth or material loss is NO",
      road_eval.decision({**good, "has_depth_or_surface_loss": False}) == "reject")
check("inconsistent chronological views are NO",
      road_eval.decision({**good, "temporal_consistency": "inconsistent"}) == "reject")
check("positive without size is NO", road_eval.decision({**good, "size": None}) == "reject")
check("all fallback sizes are valid",
      all(road_eval.decision({**good, "size": size}) == "accept"
          for size in ("small", "medium", "large")))
check("decision surface is strictly binary",
      {road_eval.decision(good), road_eval.decision({**good, "image_quality": "unusable"})}
      == {"accept", "reject"})
check("legacy positive label", road_eval.binary_label("pothole") is True)
check("non-cavity surface breakup label is negative",
      road_eval.binary_label("surface_breakup") is False)
check("legacy failed patch awaits explicit binary relabelling",
      road_eval.binary_label("failed_patch") is None)
check("unverified category excluded", road_eval.binary_label("disputed") is None)
selected = road_eval.select_events([
    {"event_id": "one", "path": "one.jpg"},
    {"event_id": "two", "path": "two.jpg"},
    {"event_id": "three", "path": "three.jpg"},
], "three,one")
check("event selector preserves label order and filters exactly",
      [entry["event_id"] for entry in selected] == ["one", "three"])
try:
    road_eval.select_events([{"event_id": "one"}], "missing")
    missing_event_fails = False
except ValueError:
    missing_event_fails = True
check("event selector fails on unknown IDs", missing_event_fails)
speed_breaker_events = [entry for entry in labels
                        if entry.get("event_id") == "tester-second-speed-breaker-2026-08-25"]
check("tester speed breaker is retained as owner-labelled semantic ground truth",
      len(speed_breaker_events) == 1
      and speed_breaker_events[0].get("label") == "not_pothole"
      and speed_breaker_events[0].get("labelled_by") == "owner"
      and speed_breaker_events[0].get("accuracy_eligible") is False
      and len(speed_breaker_events[0].get("frames", [])) == 3)
native_cadence_breaker = speed_breaker_events[0]
native_cadence_timestamps = native_cadence_breaker.get("source_timestamps_seconds", [])
native_cadence_spacing = [
    round((right - left) * 1000)
    for left, right in zip(native_cadence_timestamps, native_cadence_timestamps[1:])
]
check("tester speed breaker preserves the sampled external-recording fixture",
      native_cadence_breaker.get("path")
      == "tester-speed-breaker-native-cadence/later/f1.jpg"
      and native_cadence_breaker.get("frames") == [
          "tester-speed-breaker-native-cadence/later/f0.jpg",
          "tester-speed-breaker-native-cadence/later/f1.jpg",
          "tester-speed-breaker-native-cadence/later/f2.jpg",
      ]
      and native_cadence_breaker.get("fixture_sha256") == [
          "dd59f703b2ba228e6e3a88082c1a46b6c7add0df8b40c26396bde9b0f38b5a83",
          "de6f0e9e37f20cabdba7e7287de2c4aad1556694dc607eae9941ac4d83d6a32f",
          "90428d428e900d448cb145020848b5b4b1f5b5c5954520ad0428e97805efa1ba",
      ]
      and native_cadence_breaker.get("capture_provenance")
          == "external_recording_of_test_device")
check("tester speed breaker records its 267 ms source-video spacing",
      native_cadence_breaker.get("capture_cadence_ms") == 250
      and native_cadence_breaker.get("selected_source_indices") == [0, 1, 2]
      and native_cadence_breaker.get("observed_source_spacing_ms") == [267, 267]
      and native_cadence_breaker.get("observed_frame_spacing_ms") == [267, 267]
      and native_cadence_spacing == [267, 267]
      and all(spacing >= native_cadence_breaker["capture_cadence_ms"]
              for spacing in native_cadence_spacing))
traffic_calming_ids = {
    "tester-opening-grid-calming-marking-2026-08-25",
    "tester-zebra-raised-speed-breaker-2026-08-25",
    "tester-second-speed-breaker-2026-08-25",
}
traffic_calming_events = [entry for entry in labels
                          if entry.get("event_id") in traffic_calming_ids]
check("all three supplied traffic-calming intervals are retained as negative bursts",
      {entry.get("event_id") for entry in traffic_calming_events} == traffic_calming_ids
      and all(entry.get("label") == "not_pothole"
              and len(entry.get("frames", [])) == 3
              for entry in traffic_calming_events)
      and next(entry for entry in traffic_calming_events
               if entry.get("event_id") == "tester-opening-grid-calming-marking-2026-08-25")
          .get("labelled_by") == "independent assistant frame review"
      and all(entry.get("labelled_by") == "owner"
              for entry in traffic_calming_events
              if entry.get("event_id") != "tester-opening-grid-calming-marking-2026-08-25"))
check("external tester recordings cannot inflate production accuracy",
      all(entry.get("capture_provenance") == "external_recording_of_test_device"
              and entry.get("accuracy_eligible") is False
              for entry in traffic_calming_events)
      and 'row.get("accuracy_eligible") is True'
          in pathlib.Path(road_eval.__file__).read_text())
production_bursts = [entry for entry in labels
                     if entry.get("mode") == "drive" and len(entry.get("frames", [])) == 3]
check("every labelled Drive burst records the configured 250 ms sample spacing",
      bool(production_bursts)
      and all(
          entry.get("capture_cadence_ms") == 250
          and entry.get("selected_source_indices") == [0, 1, 2]
          and entry.get("source_sample_timestamps_seconds")
              == entry.get("source_timestamps_seconds")
          and entry.get("observed_source_spacing_ms")
              == entry.get("observed_frame_spacing_ms")
          and len(entry.get("observed_source_spacing_ms", [])) == 2
          and all(spacing >= entry["capture_cadence_ms"]
                  for spacing in entry["observed_source_spacing_ms"])
          and [round((right - left) * 1000)
               for left, right in zip(entry["source_timestamps_seconds"],
                                      entry["source_timestamps_seconds"][1:])]
              == entry["observed_source_spacing_ms"]
          for entry in production_bursts))
corrected_b_events = [entry for entry in labels
                      if entry.get("event_id") == "owner-construction-drive-2026-08-28-b"]
check("unresolved segment_0001 event B stays outside accuracy rates",
      len(corrected_b_events) == 1
      and corrected_b_events[0].get("label") == "disputed"
      and "broad disturbed patch" in corrected_b_events[0].get("notes", "").lower()
      and "owner label" in corrected_b_events[0].get("notes", "").lower())
mid_events = [entry for entry in labels
              if entry.get("event_id") == "owner-construction-drive-2026-08-28-mid"]
check("conflicting assistant reviews keep segment_0001 event M outside accuracy rates",
      len(mid_events) == 1
      and mid_events[0].get("label") == "disputed"
      and "no owner label" in mid_events[0].get("notes", "").lower())
owner_clip_positives = [entry for entry in labels
                        if entry.get("event_id") in {
                            "owner-construction-drive-2026-08-28-a",
                            "owner-construction-drive-2026-08-28-segment-2-second-4",
                        }]
check("both owner-confirmed clip moments are positive Drive bursts",
      len(owner_clip_positives) == 2
      and all(entry.get("mode") == "drive"
              and entry.get("label") == "pothole"
              and entry.get("labelled_by") == "owner"
              and entry.get("capture_provenance") == "native_mediarecorder_reconstruction"
              for entry in owner_clip_positives))
segment_two_second_four = next(
    (entry for entry in owner_clip_positives
     if entry.get("event_id") == "owner-construction-drive-2026-08-28-segment-2-second-4"),
    {})
check("segment_0002 second 4 retains the reconstructed native-video burst",
      segment_two_second_four.get("source_interval_seconds") == [3.8, 5.4]
      and segment_two_second_four.get("source_timestamps_seconds")
          == [4.533333, 4.8, 5.066667]
      and segment_two_second_four.get("fixture_sha256") == [
          "5b212ccd4a7de01a998873735faef6effafcf190749190a62fe19d46ccb893b5",
          "7d79d4c5d7bfdce996ae14eacd99129eb95adeebb200a26cbd68acf05ad04991",
          "307755fd21a78a777364215b1fd2e926e32259b90b4aee8ef2b1a207d70a5695",
      ])
kanjur_events = [entry for entry in labels
                 if entry.get("event_id") == "owner-kanjur-drivable-edge-pothole-2026-08-25"]
check("owner-confirmed Kanjur drivable-edge cavity is a manual positive",
      len(kanjur_events) == 1
      and kanjur_events[0].get("mode") == "manual"
      and kanjur_events[0].get("label") == "pothole"
      and kanjur_events[0].get("labelled_by") == "owner"
      and "drivable surface" in kanjur_events[0].get("notes", "").lower())
check("manual and drive sets stay separate",
      road_eval.entry_mode({"source": "project owner, dashcam frame"}) == "drive"
      and road_eval.entry_mode({"source": "project owner, own camera"}) == "manual")

# Low-light decisions must be taken from the full-frame resized production view.
observed = {}
real_lift = road_eval.adaptive_lift
def observe_lift(image):
    observed["size"] = image.size
    return real_lift(image)
road_eval.adaptive_lift = observe_lift
with tempfile.TemporaryDirectory() as tmp:
    path = pathlib.Path(tmp) / "dark.jpg"
    Image.new("RGB", (2000, 1000), (30, 30, 30)).save(path, quality=100)
    _, transform = road_eval.encode_view(path, 1000, 85, True)
road_eval.adaptive_lift = real_lift
check("evaluator preserves the full field of view before luminance",
      observed.get("size") == (1000, 500)
      and transform["full_frame"] is True
      and transform["source"] == {"width": 2000, "height": 1000})
check("dark resized view is enhanced", transform["enhanced"] is True)
with tempfile.TemporaryDirectory() as tmp:
    path = pathlib.Path(tmp) / "small-drive.jpg"
    Image.new("RGB", (480, 720), (90, 90, 90)).save(path, quality=100)
    _, drive_transform = road_eval.encode_view(
        path, road_eval.MAX_PREPARED_FRAME_DIMENSION, 85, False
    )
check("small Drive frame remains complete and is not upscaled",
      drive_transform["full_frame"] is True
      and drive_transform["output"] == {"width": 480, "height": 720})
with tempfile.TemporaryDirectory() as tmp:
    path = pathlib.Path(tmp) / "manual.jpg"
    Image.new("RGB", (480, 720), (80, 80, 80)).save(path, quality=100)
    _, manual_transform = road_eval.encode_view(path, 2000, 85, False)
check("manual Photo remains full-frame",
      manual_transform["full_frame"] is True
      and manual_transform["output"] == {"width": 480, "height": 720})
_, green = real_lift(Image.new("RGB", (32, 32), (0, 101, 0)))
check("evaluator uses client RGB luma weights", green["enhanced"] is False)

if fails:
    print(f"\n{len(fails)} check(s) failed")
    sys.exit(1)
print("\nEVAL CONTRACT TEST PASS")
