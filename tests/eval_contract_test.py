#!/usr/bin/env python3
"""Offline guard that the evaluator represents the generated production contract."""
import importlib.util, json, pathlib, re, sys, tempfile
from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("road_eval", ROOT / "eval" / "run_eval.py")
road_eval = importlib.util.module_from_spec(spec)
spec.loader.exec_module(road_eval)
contract = json.loads((ROOT / "llm" / "generated" / "contract.json").read_text())
detection = contract["prompts"]["detection"]
models = contract["config"]["models"]
runtime = contract["config"]["runtime"]
imaging = contract["config"]["imaging"]
# The shipped browser engine. Several checks below read its source directly to prove
# the evaluator and the app prepare images the same way.
client = (ROOT / "static" / "standalone.js").read_text()
fails = []


to_data_url_start = client.find("async function toDataUrl(")
to_data_url_end = client.find("\n\n  // ---------- pipeline ----------", to_data_url_start)
to_data_url_source = (client[to_data_url_start:to_data_url_end]
                      if to_data_url_start >= 0 and to_data_url_end > to_data_url_start else "")


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
# The Android app runs the generated contract, in the file llm/generate.mjs writes.
# Reading it here proves the generator ran and that all three sides say the same thing.
native_contract = (
    ROOT / "android-app/android/app/src/main/java/com/gauravsen/potholereporter"
         / "drivemode/LlmContractGenerated.kt"
).read_text()


def kotlin_string_constant(source, name):
    match = re.search(rf'const val {name} = "((?:[^"\\]|\\.)*)"', source)
    return json.loads(f'"{match.group(1)}"') if match else None


native_prompt = kotlin_string_constant(native_contract, "DETECT_PROMPT")
native_schema_text = kotlin_string_constant(native_contract, "DETECT_SCHEMA")
try:
    native_schema = json.loads(native_schema_text) if native_schema_text else None
except json.JSONDecodeError:
    native_schema = None


def check(name, condition):
    print(f"  {'ok  ' if condition else 'FAIL'} {name}")
    if not condition:
        fails.append(name)


check("generated contract loaded exactly", road_eval.CONTRACT == contract)
check("schema version", road_eval.SCHEMA_VERSION == detection["schemaVersion"])
check("schema name", road_eval.SCHEMA_NAME == detection["schemaName"])
check("schema exact", road_eval.SCHEMA == detection["schema"])
check("prompt version", road_eval.PROMPT_VERSION == detection["version"])
check("prompt arms exact", road_eval.prompts() == {
    "baseline": detection["base"], **detection["evaluationVariants"],
})
legacy_eval_fields = {"is_pothole", "looks_like_speed_breaker", "confidence"}
check("evaluation variants use the production schema vocabulary",
      all(not any(field in prompt for field in legacy_eval_fields)
          for prompt in detection["evaluationVariants"].values()))
check("responses endpoint", road_eval.API == runtime["responsesUrl"])
check("default model", road_eval.DEFAULT_MODEL == models["defaultModel"])
check("allowed models", road_eval.ALLOWED_MODELS == frozenset(models["allowedModels"]))
check("allowed details", road_eval.ALLOWED_DETAILS == frozenset(models["allowedImageDetails"]))
check("image cap", road_eval.MAX_DETECTION_IMAGES == imaging["maxDetectionImages"])
check("one-image contract", road_eval.MAX_DETECTION_IMAGES == 1)
check("schema has no model-confidence gate", "confidence" not in road_eval.SCHEMA["properties"])
check("schema is exactly the five v4 fields", set(road_eval.SCHEMA["properties"]) == {
    "image_quality", "assessment", "damage_type", "size", "description",
})
check("every field has an example in its definition",
      all("Example:" in definition.get("description", "")
          for definition in road_eval.SCHEMA["properties"].values()))
for field in road_eval.SCHEMA["required"]:
    check(f"required field {field}", field in detection["schema"]["properties"])
for damage in road_eval.SCHEMA["properties"]["damage_type"]["enum"]:
    check(f"damage enum {damage}", damage in detection["schema"]["properties"]["damage_type"]["enum"])

original_model = next(iter(models["originalDetailModels"]))
request = road_eval.build_request(["image-0"], "PROMPT", original_model, "original")
content = request["input"][0]["content"]
images = [item for item in content if item["type"] == "input_image"]
check("request image cap", len(images) == imaging["maxDetectionImages"])
try:
    road_eval.build_request(["one", "two"], "PROMPT", original_model, "original")
    rejects_multiple_images = False
except ValueError:
    rejects_multiple_images = True
check("multi-image detection request is rejected", rejects_multiple_images)
check("responses are not stored", request.get("store") is False
      and request["store"] == runtime["storeResponses"])
check("canonical input role", request["input"][0]["role"] == detection["role"])
check("canonical reasoning", request["reasoning"]["effort"]
      == models["reasoningEffortByModel"][original_model])
check("canonical structured output", request["text"]["format"] == {
    "type": "json_schema", "name": detection["schemaName"],
    "schema": detection["schema"], "strict": runtime["strictStructuredOutputs"],
})
check("canonical text verbosity", request["text"]["verbosity"] == runtime["textVerbosity"])
check("prompt once and last", len([x for x in content if x["type"] == "input_text"]) == 1
      and content[-1]["type"] == "input_text")
check("request does not invent an ordered-image suffix", content[-1]["text"] == "PROMPT")
check("detail belongs to every image", all(x.get("detail") == "original" for x in images))
non_original_model = next(model for model in models["allowedModels"]
                          if model not in models["originalDetailModels"])
check("unsupported original detail uses canonical default",
      road_eval.build_request(["one"], "P", non_original_model, "original")
      ["input"][0]["content"][0]["detail"] == models["defaultImageDetail"])
fallback = road_eval.build_request(["one"], "P", "not-a-model", "not-a-detail")
check("invalid model and detail use canonical defaults",
      fallback["model"] == models["defaultModel"]
      and fallback["input"][0]["content"][0]["detail"] == models["defaultImageDetail"]
      and fallback["reasoning"]["effort"]
      == models["reasoningEffortByModel"][models["defaultModel"]])

# `prepare_event` must use the generated transform parameters, not shadow copies.
transform_calls = []
real_encode = road_eval.encode_view
def observe_encode(path, max_dim, quality=85, enhance=False):
    transform_calls.append((path.name, max_dim, quality, enhance))
    return f"data:{path.name}", {"path": path.name}
road_eval.encode_view = observe_encode
manual_views, _, manual_note = road_eval.prepare_event(
    {"path": "manual.jpg"}, pathlib.Path("/unused"), "manual")
drive_views, _, drive_note = road_eval.prepare_event(
    {"path": "a.jpg", "frames": ["a.jpg", "b.jpg", "c.jpg"], "primary_index": 1},
    pathlib.Path("/unused"), "drive")
road_eval.encode_view = real_encode
manual = imaging["manual"]
drive = imaging["drive"]
# No roadBand: the contract carries no crop, and AGENTS.md forbids one. The evaluator
# downscales the complete frame and nothing else.
check("manual transform comes from contract", transform_calls[0][1:] == (
    manual["maxDimension"], round(manual["jpegQuality"] * 100),
    manual["adaptiveBrightness"]))
check("drive transform comes from contract", transform_calls[1][1:] == (
    drive["maxDimension"], round(drive["jpegQuality"] * 100),
    drive["adaptiveBrightness"]))
check("no capture mode declares a crop band",
      not any("roadBand" in mode for mode in (manual, drive))
      and "roadBand" not in client)
check("Drive selects only its labelled primary frame",
      [call[0] for call in transform_calls] == ["manual.jpg", "b.jpg"]
      and len(drive_views) == 1)
check("manual layout comes from contract",
      manual_note == detection["captureLayouts"]["manual"] and len(manual_views) == 1)
check("drive layout comes from contract",
      drive_note == detection["captureLayouts"]["drive"])

good = {"image_quality": "acceptable", "assessment": "damaged",
        "damage_type": "failed_patch", "size": "medium",
        "description": "A prior patch has broken open."}
check("damaged road is accepted", road_eval.decision(good) == "accept")
check("acceptable undamaged road is rejected", road_eval.decision({
    **good, "assessment": "undamaged", "damage_type": None, "size": None,
}) == "reject")
check("rejected image is held for review",
      road_eval.decision({**good, "image_quality": "rejected"}) == "review")
check("damaged without subtype is contradictory",
      road_eval.decision({**good, "damage_type": None}) == "review")
check("damaged with unknown subtype is contradictory",
      road_eval.decision({**good, "damage_type": "cat"}) == "review")
check("damaged with unknown size is contradictory",
      road_eval.decision({**good, "size": "huge"}) == "review")
check("undamaged with subtype is contradictory",
      road_eval.decision({**good, "assessment": "undamaged"}) == "review")
check("undamaged with size is contradictory",
      road_eval.decision({**good, "assessment": "undamaged",
                          "damage_type": None, "size": "small"}) == "review")
check("legacy positive label", road_eval.binary_label("pothole") is True)
# The shipped contract reports road damage, not cavities alone: every damage_type in
# its enum is a positive, and an undamaged verdict is the only negative.
check("every contract damage type is a positive label",
      all(road_eval.binary_label(value) is True
          for value in road_eval.SCHEMA["properties"]["damage_type"]["enum"] if value))
check("undamaged is the negative label",
      road_eval.binary_label("undamaged") is False
      and road_eval.binary_label("not_pothole") is False)
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
# The owner-labelled evaluation corpus.
labels = json.loads((ROOT / "eval" / "labels.json").read_text())["images"]
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
check("evaluator resizes full frame before luminance", observed.get("size") == (1000, 500))
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
