#!/usr/bin/env python3
"""Offline guard that the evaluator still represents the pure client's v3 contract."""
import importlib.util, pathlib, sys, tempfile
from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("road_eval", ROOT / "eval" / "run_eval.py")
road_eval = importlib.util.module_from_spec(spec)
spec.loader.exec_module(road_eval)
client = (ROOT / "static" / "standalone.js").read_text()
fails = []


def check(name, condition):
    print(f"  {'ok  ' if condition else 'FAIL'} {name}")
    if not condition:
        fails.append(name)


check("schema version", 'const SCHEMA_VERSION = 3;' in client and road_eval.SCHEMA_VERSION == 3)
check("prompt version", f'const PROMPT_VERSION = "{road_eval.PROMPT_VERSION}";' in client)
check("road-band transform", 'const ROAD_BAND = 0.6;' in client and road_eval.ROAD_BAND == .60)
check("schema has no model-confidence gate", "confidence" not in road_eval.SCHEMA["properties"])
for field in road_eval.SCHEMA["required"]:
    check(f"required field {field}", f'"{field}"' in client)
for damage in road_eval.SCHEMA["properties"]["damage_type"]["enum"]:
    check(f"damage enum {damage}", damage in client)

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

good = {"reportable": True, "assessment": "clear", "image_quality": "usable",
        "damage_type": "failed_patch", "on_drivable_surface": True,
        "has_broken_edge_or_rim": False, "has_depth_or_surface_loss": True,
        "temporal_consistency": "consistent"}
check("failed repair accepted without cavity rim", road_eval.decision(good) == "accept")
check("uncertain held for review", road_eval.decision({**good, "assessment": "uncertain"}) == "review")
check("off-road rejected", road_eval.decision({**good, "on_drivable_surface": False}) == "reject")
check("legacy positive label", road_eval.binary_label("pothole") is True)
check("new failed-surface label", road_eval.binary_label("surface_breakup") is True)
check("unverified category excluded", road_eval.binary_label("disputed") is None)
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
    _, transform = road_eval.encode_view(path, 1000, 85, .60, True)
road_eval.adaptive_lift = real_lift
check("evaluator resizes before luminance", observed.get("size") == (1000, 300))
check("dark resized view is enhanced", transform["enhanced"] is True)
_, green = real_lift(Image.new("RGB", (32, 32), (0, 101, 0)))
check("evaluator uses client RGB luma weights", green["enhanced"] is False)

if fails:
    print(f"\n{len(fails)} check(s) failed")
    sys.exit(1)
print("\nEVAL CONTRACT TEST PASS")
