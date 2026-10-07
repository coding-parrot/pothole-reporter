"""Shared paths, the production contract and the production frame preparation.

Everything a drive frame goes through on the phone before it reaches the detector is
reproduced here by calling the evaluator's own implementation (eval/run_eval.py), so
the classifier is trained on the bytes the teacher judged and the app sends.
"""
import hashlib
import io
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
WORK = HERE / "work"
FRAMES = WORK / "frames"
TEACHER = WORK / "teacher"
EMBEDDINGS = WORK / "embeddings"

sys.path.insert(0, str(ROOT / "eval"))
import run_eval  # noqa: E402 - the path above has to exist first

CONTRACT = run_eval.CONTRACT
DETECTION = run_eval.DETECTION
MODEL_CONFIG = run_eval.MODEL_CONFIG
RUNTIME_CONFIG = run_eval.RUNTIME_CONFIG
DRIVE_IMAGING = run_eval.IMAGING_CONFIG["drive"]
DAMAGE_TYPES = ("pothole_cavity", "surface_breakup", "rut_or_depression",
                "failed_patch", "other_road_damage")


def sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def prepare_drive_jpeg(image):
    """The app's Drive Mode preparation: whole frame, downscale only to 1280 px on the
    long edge, the adaptive brightness lift, JPEG quality 0.85."""
    from PIL import Image

    image = image.convert("RGB")
    limit = DRIVE_IMAGING["maxDimension"]
    scale = min(1.0, limit / max(image.size))
    if scale != 1:
        image = image.resize((run_eval.positive_half_up(image.width * scale),
                              run_eval.positive_half_up(image.height * scale)),
                             Image.Resampling.LANCZOS)
    if DRIVE_IMAGING["adaptiveBrightness"]:
        image, _ = run_eval.adaptive_lift(image)
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=round(DRIVE_IMAGING["jpegQuality"] * 100))
    return buffer.getvalue()


def is_damaged(verdict):
    """The app's rule for a damaged frame, the same one detectors.mjs applies."""
    return (verdict.get("image_quality") == "acceptable"
            and verdict.get("assessment") == "damaged")


def read_json(path):
    return json.loads(Path(path).read_text())


def write_json(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, indent=1, sort_keys=True) + "\n")
