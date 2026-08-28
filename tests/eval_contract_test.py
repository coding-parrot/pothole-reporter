#!/usr/bin/env python3
"""Offline guard that the evaluator mirrors the pure client's binary v8 contract."""
import importlib.util, json, pathlib, sys, tempfile
from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("road_eval", ROOT / "eval" / "run_eval.py")
road_eval = importlib.util.module_from_spec(spec)
spec.loader.exec_module(road_eval)
client = (ROOT / "static" / "standalone.js").read_text()
native = (ROOT / "android-app" / "android" / "app" / "src" / "main" / "java" /
          "dev" / "aiengg" / "potholereporter" / "drive" / "NativeInferenceEngine.kt").read_text()
entities = (ROOT / "android-app" / "android" / "app" / "src" / "main" / "java" /
            "dev" / "aiengg" / "potholereporter" / "db" / "Entities.kt").read_text()
database = (ROOT / "android-app" / "android" / "app" / "src" / "main" / "java" /
            "dev" / "aiengg" / "potholereporter" / "db" / "PotholeDatabase.kt").read_text()
plugin = (ROOT / "android-app" / "android" / "app" / "src" / "main" / "java" /
          "dev" / "aiengg" / "potholereporter" / "plugin" / "DriveModePlugin.kt").read_text()
labels = json.loads((ROOT / "eval" / "labels.json").read_text())["images"]
fails = []


def check(name, condition):
    print(f"  {'ok  ' if condition else 'FAIL'} {name}")
    if not condition:
        fails.append(name)


check("schema version", 'const SCHEMA_VERSION = 7;' in client and road_eval.SCHEMA_VERSION == 7)
check("prompt version", f'const PROMPT_VERSION = "{road_eval.PROMPT_VERSION}";' in client)
check("native prompt version", f'PROMPT_VERSION = "{road_eval.PROMPT_VERSION}"' in native)
expected_road_regions = {
    "portrait": {"top": .40, "bottom": .66},
    "landscape": {"top": .48, "bottom": .78},
    "square": {"top": .40, "bottom": .70},
}
check("orientation-aware road-region ratios",
      road_eval.ROAD_REGION_RATIOS == expected_road_regions
      and 'portrait: Object.freeze({ top: 0.40, bottom: 0.66 })' in client
      and 'landscape: Object.freeze({ top: 0.48, bottom: 0.78 })' in client
      and 'square: Object.freeze({ top: 0.40, bottom: 0.70 })' in client)
check("portrait road-region geometry",
      road_eval.select_road_region(480, 720) == {
          "x": 0, "y": 288, "width": 480, "height": 187,
          "orientation": "portrait", "top_ratio": .40, "bottom_ratio": .66})
check("landscape road-region geometry",
      road_eval.select_road_region(1280, 720) == {
          "x": 0, "y": 346, "width": 1280, "height": 216,
          "orientation": "landscape", "top_ratio": .48, "bottom_ratio": .78})
check("square road-region geometry",
      road_eval.select_road_region(1000, 1000) == {
          "x": 0, "y": 400, "width": 1000, "height": 300,
          "orientation": "square", "top_ratio": .40, "bottom_ratio": .70})
retired_fixed_band_name = "ROAD_" + "BAND"
check("retired fixed bottom-band transform is absent",
      retired_fixed_band_name not in client and not hasattr(road_eval, retired_fixed_band_name))
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
      "any ambiguity must be no" in road_eval.prompts()["baseline"].lower())
check("prompt defines every fallback size band",
      all(term in road_eval.prompts()["baseline"].lower()
          for term in ("below 30 cm", "30 to 60 cm", "above 60 cm")))
check("native prompt defines the same fallback size bands",
      all(term in native.lower() for term in ("below 30 cm", "30 to 60 cm", "above 60 cm")))
check("native schema exposes the binary verdict",
      '"is_pothole": { "type": "boolean" }' in native and
      '"has_localized_cavity": { "type": "boolean" }' in native)
check("native Drive cannot emit a single-view positive",
      '"consistent", "single_view"' not in native)
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
check("prompt once and last", len([x for x in content if x["type"] == "input_text"]) == 1
      and content[-1]["type"] == "input_text")
check("detail belongs to every image", all(x.get("detail") == "original" for x in images))
check("mini rejects unsupported original detail",
      road_eval.build_request(["one"], "P", "gpt-5-mini", "original")
      ["input"][0]["content"][0]["detail"] == "high")

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
check("tester speed breaker is retained as a verified negative event",
      len(speed_breaker_events) == 1
      and speed_breaker_events[0].get("label") == "not_pothole"
      and speed_breaker_events[0].get("labelled_by") == "owner"
      and len(speed_breaker_events[0].get("frames", [])) == 3)
check("manual and drive sets stay separate",
      road_eval.entry_mode({"source": "project owner, dashcam frame"}) == "drive"
      and road_eval.entry_mode({"source": "project owner, own camera"}) == "manual")

# Low-light decisions must be taken from the cropped/resized production view. This
# caught the evaluator enhancing the original first and measuring it with a different
# grayscale formula.
observed = {}
real_lift = road_eval.adaptive_lift
def observe_lift(image):
    observed["size"] = image.size
    return real_lift(image)
road_eval.adaptive_lift = observe_lift
with tempfile.TemporaryDirectory() as tmp:
    path = pathlib.Path(tmp) / "dark.jpg"
    Image.new("RGB", (2000, 1000), (30, 30, 30)).save(path, quality=100)
    _, transform = road_eval.encode_view(path, 1000, 85, True, True)
road_eval.adaptive_lift = real_lift
check("evaluator crops and resizes before luminance", observed.get("size") == (1000, 150))
check("evaluator records landscape road-region transform",
      transform["road_region"] == {
          "x": 0, "y": 480, "width": 2000, "height": 300,
          "orientation": "landscape", "top_ratio": .48, "bottom_ratio": .78})
check("dark resized view is enhanced", transform["enhanced"] is True)
with tempfile.TemporaryDirectory() as tmp:
    path = pathlib.Path(tmp) / "small-drive.jpg"
    Image.new("RGB", (480, 720), (90, 90, 90)).save(path, quality=100)
    _, drive_transform = road_eval.encode_view(path, 1024, 85, True, False)
check("small Drive road crop is enlarged for model inspection",
      drive_transform["output"] == {"width": 1024, "height": 399})
with tempfile.TemporaryDirectory() as tmp:
    path = pathlib.Path(tmp) / "manual.jpg"
    Image.new("RGB", (480, 720), (80, 80, 80)).save(path, quality=100)
    _, manual_transform = road_eval.encode_view(path, 2000, 85, False, False)
check("manual Photo remains full-frame",
      manual_transform["road_region"] is None
      and manual_transform["output"] == {"width": 480, "height": 720})
_, green = real_lift(Image.new("RGB", (32, 32), (0, 101, 0)))
check("evaluator uses client RGB luma weights", green["enhanced"] is False)

if fails:
    print(f"\n{len(fails)} check(s) failed")
    sys.exit(1)
print("\nEVAL CONTRACT TEST PASS")
