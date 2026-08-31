#!/usr/bin/env python3
"""Focused regression guard: every detector view preserves the complete camera frame."""

import base64
import importlib.util
import io
import pathlib
import tempfile

from PIL import Image, ImageDraw


ROOT = pathlib.Path(__file__).resolve().parent.parent
NATIVE = ROOT / "android-app" / "android" / "app" / "src" / "main" / "java" / \
    "dev" / "aiengg" / "potholereporter" / "drive"


def read(path):
    return (ROOT / path).read_text()


def require(label, condition):
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        raise AssertionError(label)


rules = read("AGENTS.md")
client = read("static/standalone.js")
web = read("static/index.html")
quality = (NATIVE / "FrameQualityEvaluator.kt").read_text()
engine = (NATIVE / "NativeInferenceEngine.kt").read_text()
camera = (NATIVE / "NativeDriveCameraManager.kt").read_text()
rtsp = (NATIVE / "NativeRtspFrameSource.kt").read_text()
activity = read("android-app/android/app/src/main/java/dev/aiengg/potholereporter/MainActivity.java")
detect_contract = (NATIVE / "NativeDetectionContract.kt").read_text()
repair_contract = (NATIVE / "NativeRepairContract.kt").read_text()
evaluator_source = read("eval/run_eval.py")

require("repository instructions require complete edge-to-edge frames",
        "complete camera frame from edge to edge" in rules)
require("repository instructions forbid every spatial-subset mechanism",
        all(term in rules for term in (
            "Never spatially crop, tile, mask, extract a road band",
            "region of interest",
            "live detection, saved-frame replay, evaluation, training-data preparation",
        )))
require("repository instructions allow only whole-frame transforms",
        "Whole-frame orientation correction, downscaling, compression" in rules)

forbidden_identifiers = (
    "RoadRegionSelector",
    "selectRoadRegion",
    "select_road_region",
    "ROAD_REGION_RATIOS",
    "prepareRoadBandDataUrl",
    "MAX_PREPARED_ROAD_DIMENSION",
    "cropRoad",
    "road_crop",
)
current_implementations = "\n".join((client, web, quality, engine, camera, rtsp, evaluator_source))
require("current implementations contain no crop or road-band machinery",
        all(term not in current_implementations for term in forbidden_identifiers))
require("native CameraX acquisition does not configure a crop or viewport",
        all(term not in camera for term in (
            "UseCaseGroup", "ViewPort", "setCropAspectRatio", "cropRect",
        ))
        and "Bitmap.createBitmap(raw, 0, 0, raw.width, raw.height, matrix, true)" in camera)
require("native RTSP acquisition rejects partial decoder frames and preserves the full view",
        "image.cropRect.left != 0" in rtsp
        and "image.cropRect.top != 0" in rtsp
        and "image.cropRect.right != image.width" in rtsp
        and "image.cropRect.bottom != image.height" in rtsp
        and "setVideoScalingMode(C.VIDEO_SCALING_MODE_SCALE_TO_FIT)" in rtsp
        and "VIDEO_SCALING_MODE_SCALE_TO_FIT_WITH_CROPPING" not in rtsp
        and "for (row in 0 until outputHeight)" in rtsp
        and "for (column in 0 until outputWidth)" in rtsp
        and "sourceRow = row * (height - 1) / (outputHeight - 1)" in rtsp
        and "sourceColumn = column * (width - 1) / (outputWidth - 1)" in rtsp
        and "PreviewView.ScaleType.FIT_CENTER" in activity
        and "ImageView.ScaleType.FIT_CENTER" in activity)
require("manual camera disables interactive cropping",
        'const shot = await camera.getPhoto({' in web and "allowEditing: false" in web)
require("Web preprocessing draws the entire source into an aspect-preserving target",
        "const scale = Math.min(1, maxDim / Math.max(sw, sh));" in client
        and "ctx.drawImage(bmp, 0, 0, sw, sh, 0, 0, c.width, c.height);" in client)
require("preview quality selection also scores the entire camera frame",
        "ctx.drawImage(video, 0, 0, video.videoWidth, video.videoHeight, 0, 0, width, height);"
        in web)
require("saved-video replay extracts complete frames",
        'canvas.getContext("2d").drawImage(v, 0, 0, canvas.width, canvas.height);' in web)
require("native preparation starts from full bitmap dimensions",
        "val sw = bitmap.width" in quality and "val sh = bitmap.height" in quality
        and "NativePreparedImageScale.downscaleOnly(\n            sw,\n            sh," in quality)
require("native inference sends a complete view for every chronological frame",
        "burstFrames.forEach { frame ->" in engine
        and "prepareDetectionFrameDataUrl(" in engine)

layout_rule = "No image is cropped, tiled, masked, or limited to a region of interest."
require("detection prompts disclose and enforce the complete-frame layout",
        layout_rule in client and layout_rule in detect_contract and layout_rule in evaluator_source)
require("repair prompts enforce complete current frames",
        "No current image is cropped, tiled, masked, or limited" in client
        and "No current image is cropped, tiled, masked, or limited" in repair_contract)
require("Web repair preserves every current frame in camera-time order",
        "const MAX_REPAIR_IMAGES = 5;" in client
        and "const current = fullViews.filter(Boolean);" in client
        and "...current.map((url) => ({ url }))" in client)

spec = importlib.util.spec_from_file_location("full_frame_eval", ROOT / "eval" / "run_eval.py")
road_eval = importlib.util.module_from_spec(spec)
spec.loader.exec_module(road_eval)
with tempfile.TemporaryDirectory() as tmp:
    source_path = pathlib.Path(tmp) / "four-corners.png"
    source = Image.new("RGB", (2000, 1000))
    draw = ImageDraw.Draw(source)
    draw.rectangle((0, 0, 999, 499), fill=(255, 0, 0))
    draw.rectangle((1000, 0, 1999, 499), fill=(0, 255, 0))
    draw.rectangle((0, 500, 999, 999), fill=(0, 0, 255))
    draw.rectangle((1000, 500, 1999, 999), fill=(255, 255, 0))
    source.save(source_path)
    encoded, metadata = road_eval.encode_view(source_path, 1000, 95, False)
    decoded = Image.open(io.BytesIO(base64.b64decode(encoded.split(",", 1)[1]))).convert("RGB")

    require("evaluator preserves the full 2:1 field of view when downscaling",
            decoded.size == (1000, 500)
            and metadata["source"] == {"width": 2000, "height": 1000}
            and metadata["output"] == {"width": 1000, "height": 500}
            and metadata["full_frame"] is True)
    samples = [decoded.getpixel(point) for point in ((20, 20), (980, 20), (20, 480), (980, 480))]
    require("evaluator retains all four source corners",
            samples[0][0] > 220 and samples[0][1] < 30
            and samples[1][1] > 220 and samples[1][0] < 30
            and samples[2][2] > 220 and samples[2][0] < 30
            and samples[3][0] > 220 and samples[3][1] > 220)

require("Android and documentation Web bundles exactly mirror the production sources",
        read("android-app/www/standalone.js") == client
        and read("docs/standalone.js") == client
        and read("android-app/www/index.html") == web
        and read("docs/index.html") == web)

print("full-frame invariant checks passed")
