#!/usr/bin/env python3
"""Download and index the image-only RAD (Road Anomaly Detection) dataset.

RAD's ``RoadDamages`` box is deliberately *not* a pothole label.  This adapter
preserves that source class as ``unreviewed_road_anomaly`` so an independently audited
pothole ground truth can be layered on later without laundering a broad road-
damage label into a detector accuracy claim.

Only complete source images are indexed.  YOLO boxes are label metadata; this
module never crops, masks, tiles, resizes, re-encodes, or otherwise transforms
an image.
"""

from __future__ import annotations

import argparse
import ast
import base64
import hashlib
import json
import math
import os
import posixpath
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from PIL import Image, UnidentifiedImageError


DATASET_REF = "rohitsuresh15/radroad-anomaly-detection"
DATASET_VERSION = 3
DATASET_LICENSE = "MIT"
DATASET_NAME = "RAD—Road Anomaly Detection"
DATASET_URL = "https://www.kaggle.com/datasets/rohitsuresh15/radroad-anomaly-detection"
DATASET_METADATA_URL = (
    "https://www.kaggle.com/api/v1/datasets/metadata/"
    "rohitsuresh15/radroad-anomaly-detection"
)
PAPER_DOI = "10.1007/978-981-97-2004-0_34"
KAGGLE_REST_API = "https://www.kaggle.com/api/v1/datasets"
LIST_METHOD = f"{KAGGLE_REST_API}/list"
DOWNLOAD_METHOD = f"{KAGGLE_REST_API}/download"
INVENTORY_FILE = ".rad-kaggle-images-manifest.json"
INDEX_SCHEMA_VERSION = "rad-dataset-index-v2"
INVENTORY_SCHEMA_VERSION = "rad-kaggle-images-v1"

EXPECTED_CLASSES = (
    "HMV",
    "LMV",
    "Pedestrian",
    "RoadDamages",
    "SpeedBump",
    "UnsurfacedRoad",
)
CLASS_SEMANTICS = {
    "HMV": "non_target_context",
    "LMV": "non_target_context",
    "Pedestrian": "non_target_context",
    "RoadDamages": "unreviewed_road_anomaly",
    "SpeedBump": "speed_breaker",
    "UnsurfacedRoad": "non_target_context",
}
TARGET_SEMANTICS = frozenset(("unreviewed_road_anomaly", "speed_breaker"))
ROAD_DAMAGE_LABEL_LIMITATION = (
    "RoadDamages combines road anomalies and is not pothole ground truth; "
    "it remains unreviewed_road_anomaly until human relabelling."
)
EXPECTED_V3_COUNTS = {
    "images": 8394,
    "labels": 8394,
    "boxes": 29941,
    "class_boxes": {
        "HMV": 4431,
        "LMV": 14392,
        "Pedestrian": 3563,
        "RoadDamages": 6463,
        "SpeedBump": 499,
        "UnsurfacedRoad": 593,
    },
    "road_damage_frames": 3184,
    "speed_breaker_frames": 439,
    "pure_speed_breaker_frames": 341,
    "road_damage_and_speed_breaker_frames": 98,
    "empty_labels": 91,
}
EXPECTED_V3_INDEX_COUNTS = {
    "source_images": 8394,
    "unique_source_frames": 7185,
    "source_videos": 167,
    "events": 5079,
    "skipped_repeated_pixel_windows": 0,
}
# Content seal of the deterministic, complete v3 index produced from the source
# recorded in rad_v3_source_receipt.json. Release gates pin this exact seal; small
# development fixtures can still be built and loaded outside a release gate.
EXPECTED_V3_INDEX_SHA256 = (
    "5fb978ed8c281d85d126ad212d2158c62d121ccd9f5f5f8fd92faec09d78bdd7"
)
SPLIT_THRESHOLDS = (8000, 9000, 10000)
SPLIT_SALT = "rad-v3-source-video-v1"

# The raw RAD archive has two different MP4s with each of these basenames.
# Extracted frame names discarded the source folder, so chronology cannot be
# reconstructed for these IDs.  They remain indexed as stills, in one group,
# but are never assembled into Drive events.
KNOWN_AMBIGUOUS_SOURCE_VIDEOS = frozenset(
    f"{number:02d}_13-06-2023_mp4" for number in range(1, 30)
)

IMAGE_NAME_RE = re.compile(
    r"^(?P<source>.+)_mp4-(?P<frame>[0-9]+)_jpg\.rf\."
    r"(?P<roboflow>[A-Za-z0-9]+)\.(?P<extension>jpg|jpeg)$",
    re.IGNORECASE,
)
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


class RadDatasetError(ValueError):
    """Raised when RAD input is incomplete, malformed, or semantically unsafe."""


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            + "\n").encode("utf-8")


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sealed(value: Mapping[str, Any], field: str) -> Dict[str, Any]:
    result = dict(value)
    result.pop(field, None)
    result[field] = sha256_bytes(canonical_json_bytes(result))
    return result


def _verify_seal(value: Mapping[str, Any], field: str) -> None:
    expected = value.get(field)
    if not isinstance(expected, str) or not HEX64_RE.fullmatch(expected):
        raise RadDatasetError(f"missing or malformed {field}")
    payload = dict(value)
    payload.pop(field)
    if sha256_bytes(canonical_json_bytes(payload)) != expected:
        raise RadDatasetError(f"{field} does not match index content")


def _basic_auth_header() -> Optional[str]:
    token = os.environ.get("KAGGLE_API_TOKEN")
    if token:
        return f"Bearer {token}"
    credentials_path = Path.home() / ".kaggle" / "kaggle.json"
    try:
        credentials = json.loads(credentials_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    username = credentials.get("username")
    key = credentials.get("key")
    if not isinstance(username, str) or not isinstance(key, str):
        return None
    encoded = base64.b64encode(f"{username}:{key}".encode("utf-8")).decode("ascii")
    return f"Basic {encoded}"


def _request_headers() -> Dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "pothole-reporter-rad-adapter/1",
    }
    authorization = _basic_auth_header()
    if authorization:
        headers["Authorization"] = authorization
    return headers


def _get_json(url: str, timeout: int = 60) -> Dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers=_request_headers(),
        method="GET",
    )
    raw = None
    for attempt in range(6):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
            break
        except urllib.error.HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 5:
                raise RadDatasetError(f"Kaggle API returned HTTP {error.code}") from error
            retry_after = error.headers.get("Retry-After")
            delay = int(retry_after) if retry_after and retry_after.isdigit() else 2 ** attempt
        except urllib.error.URLError as error:
            if attempt == 5:
                raise RadDatasetError(f"Kaggle API request failed: {error.reason}") from error
            delay = 2 ** attempt
        time.sleep(min(30, delay))
    if raw is None:
        raise RadDatasetError("Kaggle API retry loop ended without a response")
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RadDatasetError("Kaggle API returned malformed JSON") from error
    if not isinstance(result, dict):
        raise RadDatasetError("Kaggle API response root is not an object")
    return result


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def _signed_file_url(remote_name: str, timeout: int = 60) -> str:
    validate_remote_image_name(remote_name)
    owner, slug = DATASET_REF.split("/", 1)
    encoded_name = urllib.parse.quote(remote_name, safe="")
    url = (
        f"{DOWNLOAD_METHOD}/{owner}/{slug}/{encoded_name}?"
        + urllib.parse.urlencode({"datasetVersionNumber": DATASET_VERSION})
    )
    request = urllib.request.Request(
        url,
        headers=_request_headers(),
        method="GET",
    )
    opener = urllib.request.build_opener(_NoRedirect)
    location = None
    for attempt in range(6):
        try:
            opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            if error.code in (301, 302, 303, 307, 308):
                location = error.headers.get("Location")
                break
            if error.code not in (429, 500, 502, 503, 504) or attempt == 5:
                raise RadDatasetError(
                    f"Kaggle download API returned HTTP {error.code} for {remote_name}"
                ) from error
            retry_after = error.headers.get("Retry-After")
            delay = int(retry_after) if retry_after and retry_after.isdigit() else 2 ** attempt
        except urllib.error.URLError as error:
            if attempt == 5:
                raise RadDatasetError(
                    f"Kaggle download request failed for {remote_name}: {error.reason}"
                ) from error
            delay = 2 ** attempt
        else:
            raise RadDatasetError("Kaggle download API did not return a signed redirect")
        time.sleep(min(30, delay))
    if not isinstance(location, str) or not location.startswith("https://"):
        raise RadDatasetError("Kaggle download API returned an unsafe redirect")
    return location


def validate_remote_image_name(name: str) -> PurePosixPath:
    if not isinstance(name, str):
        raise RadDatasetError("dataset file name is not a string")
    path = PurePosixPath(name)
    if (path.is_absolute() or not path.parts or path.parts[0] != "images"
            or any(part in ("", ".", "..") for part in path.parts)):
        raise RadDatasetError(f"unsafe or out-of-scope dataset path: {name!r}")
    if name == "images/data.yaml":
        return path
    if len(path.parts) != 4:
        raise RadDatasetError(f"unexpected file under images/**: {name}")
    _, split, kind, filename = path.parts
    if split not in ("train", "valid", "test"):
        raise RadDatasetError(f"unknown RAD source split in {name}")
    expected_suffix = ".txt" if kind == "labels" else None
    if kind == "images":
        if Path(filename).suffix.lower() not in (".jpg", ".jpeg"):
            raise RadDatasetError(f"non-image payload under RAD images directory: {name}")
    elif kind == "labels":
        if not filename.endswith(expected_suffix):
            raise RadDatasetError(f"non-label payload under RAD labels directory: {name}")
    else:
        raise RadDatasetError(f"unknown RAD directory under images/**: {name}")
    return path


def list_remote_inventory(get_json=_get_json, require_complete: bool = True) -> Dict[str, Any]:
    """List Kaggle metadata and return a sealed image-only download inventory.

    Metadata for the raw videos is inspected only to detect basename collisions.
    Those video files are never included in the returned download list.
    """
    owner, slug = DATASET_REF.split("/", 1)
    token: Optional[str] = None
    all_files: List[Dict[str, Any]] = []
    seen_tokens = set()
    while True:
        query: Dict[str, Any] = {
            "datasetVersionNumber": DATASET_VERSION,
            "pageSize": 200,
        }
        if token:
            query["pageToken"] = token
        url = f"{LIST_METHOD}/{owner}/{slug}?{urllib.parse.urlencode(query)}"
        response = get_json(url)
        files = response.get("datasetFiles")
        if not isinstance(files, list):
            raise RadDatasetError("Kaggle file listing omitted datasetFiles")
        for item in files:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                raise RadDatasetError("Kaggle returned a malformed file entry")
            all_files.append(item)
        next_token = response.get("nextPageToken")
        if next_token in (None, ""):
            break
        if not isinstance(next_token, str) or next_token in seen_tokens:
            raise RadDatasetError("Kaggle returned a malformed pagination token")
        seen_tokens.add(next_token)
        token = next_token

    image_files: List[Dict[str, Any]] = []
    raw_video_paths: Dict[str, List[str]] = defaultdict(list)
    seen_names = set()
    for item in all_files:
        name = item["name"]
        if name in seen_names:
            raise RadDatasetError(f"Kaggle listed the same path twice: {name}")
        seen_names.add(name)
        if name.startswith("images/"):
            validate_remote_image_name(name)
            size = item.get("totalBytes", 0)
            # Kaggle omits totalBytes for zero-byte YOLO label files.
            if type(size) is not int or size < 0:
                raise RadDatasetError(f"Kaggle returned an invalid size for {name}")
            image_files.append({"name": name, "bytes": size})
        elif name.lower().endswith(".mp4"):
            raw_video_paths[posixpath.basename(name)].append(name)

    image_files.sort(key=lambda item: item["name"])
    if not any(item["name"] == "images/data.yaml" for item in image_files):
        raise RadDatasetError("RAD images/data.yaml is missing from Kaggle")
    inventory_counts = {
        "images": sum(
            item["name"].startswith((
                "images/train/images/", "images/valid/images/", "images/test/images/"
            )) for item in image_files
        ),
        "labels": sum(
            item["name"].startswith((
                "images/train/labels/", "images/valid/labels/", "images/test/labels/"
            )) for item in image_files
        ),
        "zero_byte_labels": sum(
            item["name"].endswith(".txt") and item["bytes"] == 0 for item in image_files
        ),
        "yaml_files": sum(item["name"] == "images/data.yaml" for item in image_files),
    }
    expected_inventory_counts = {
        "images": EXPECTED_V3_COUNTS["images"],
        "labels": EXPECTED_V3_COUNTS["labels"],
        "zero_byte_labels": EXPECTED_V3_COUNTS["empty_labels"],
        "yaml_files": 1,
    }
    if require_complete and inventory_counts != expected_inventory_counts:
        raise RadDatasetError(
            "RAD v3 Kaggle image inventory changed or is incomplete: "
            f"expected {expected_inventory_counts}, got {inventory_counts}"
        )
    ambiguous = sorted(
        Path(name).stem.replace(".mp4", "").replace("_mp4", "") + "_mp4"
        for name, paths in raw_video_paths.items() if len(paths) > 1
    )
    if set(ambiguous) != set(KNOWN_AMBIGUOUS_SOURCE_VIDEOS):
        raise RadDatasetError(
            "RAD raw-video basename collisions changed; review provenance before indexing"
        )
    inventory = {
        "schema_version": INVENTORY_SCHEMA_VERSION,
        "dataset_ref": DATASET_REF,
        "dataset_version": DATASET_VERSION,
        "license": DATASET_LICENSE,
        "scope": "images/** only; raw videos are metadata-inspected but never downloaded",
        "files": image_files,
        "file_count": len(image_files),
        "total_bytes": sum(item["bytes"] for item in image_files),
        "content_counts": inventory_counts,
        "ambiguous_source_videos": ambiguous,
    }
    return _sealed(inventory, "inventory_sha256")


def _local_path(root: Path, remote_name: str) -> Path:
    relative = validate_remote_image_name(remote_name)
    root_resolved = root.resolve()
    target = root.joinpath(*relative.parts)
    try:
        target.resolve().relative_to(root_resolved)
    except ValueError as error:
        raise RadDatasetError(f"dataset path escapes download root: {remote_name}") from error
    return target


def _download_one(root: Path, item: Mapping[str, Any], timeout: int = 120) -> str:
    name = item.get("name")
    expected = item.get("bytes")
    if not isinstance(name, str) or type(expected) is not int or expected < 0:
        raise RadDatasetError("malformed download inventory entry")
    target = _local_path(root, name)
    part = target.with_name(target.name + ".part")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        actual = target.stat().st_size
        if actual == expected:
            return "verified"
        if actual > expected:
            raise RadDatasetError(f"local file is larger than inventory: {name}")
        if part.exists():
            raise RadDatasetError(f"both partial and undersized final files exist: {name}")
        target.replace(part)
    if part.exists() and part.stat().st_size > expected:
        raise RadDatasetError(f"partial file is larger than inventory: {name}")
    offset = part.stat().st_size if part.exists() else 0
    if offset == expected:
        if part.exists():
            part.replace(target)
            return "resumed"
        # Kaggle omits totalBytes for its 91 valid, empty YOLO label files.
        # Materialise them locally without spending a download request.
        target.touch(exist_ok=False)
        return "created_empty"

    signed_url = _signed_file_url(name, timeout=timeout)
    headers = {"User-Agent": "pothole-reporter-rad-adapter/1"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    request = urllib.request.Request(signed_url, headers=headers, method="GET")
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        raise RadDatasetError(f"download returned HTTP {error.code} for {name}") from error
    except urllib.error.URLError as error:
        raise RadDatasetError(f"download failed for {name}: {error.reason}") from error
    with response:
        status = getattr(response, "status", response.getcode())
        if offset and status == 206:
            content_range = response.headers.get("Content-Range", "")
            if not content_range.startswith(f"bytes {offset}-"):
                raise RadDatasetError(f"invalid resume range for {name}")
            mode = "ab"
        elif status == 200:
            mode = "wb"
            offset = 0
        else:
            raise RadDatasetError(f"unexpected HTTP {status} for {name}")
        with part.open(mode) as output:
            shutil.copyfileobj(response, output, length=1024 * 1024)
    actual = part.stat().st_size
    if actual != expected:
        raise RadDatasetError(
            f"download size mismatch for {name}: expected {expected}, got {actual}"
        )
    part.replace(target)
    return "resumed" if offset else "downloaded"


def write_inventory(root: Path, inventory: Mapping[str, Any]) -> Path:
    _verify_seal(inventory, "inventory_sha256")
    path = root / INVENTORY_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(inventory))
    return path


def load_inventory(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as error:
        raise RadDatasetError(f"cannot read inventory: {path}") from error
    if not isinstance(value, dict) or value.get("schema_version") != INVENTORY_SCHEMA_VERSION:
        raise RadDatasetError("unsupported RAD inventory schema")
    _verify_seal(value, "inventory_sha256")
    return value


def verify_download(root: Path, inventory: Mapping[str, Any]) -> Dict[str, int]:
    _verify_seal(inventory, "inventory_sha256")
    missing = []
    wrong_size = []
    total = 0
    for item in inventory.get("files", []):
        name, expected = item.get("name"), item.get("bytes")
        if not isinstance(name, str) or type(expected) is not int or expected < 0:
            raise RadDatasetError("malformed inventory file entry")
        target = _local_path(root, name)
        if not target.is_file():
            missing.append(name)
            continue
        actual = target.stat().st_size
        if actual != expected:
            wrong_size.append(name)
        total += actual
    if missing or wrong_size:
        detail = []
        if missing:
            detail.append(f"{len(missing)} missing")
        if wrong_size:
            detail.append(f"{len(wrong_size)} wrong-size")
        raise RadDatasetError("RAD download verification failed: " + ", ".join(detail))
    return {"file_count": len(inventory["files"]), "total_bytes": total}


def download_images(root: Path, workers: int = 6) -> Dict[str, int]:
    if workers < 1 or workers > 16:
        raise RadDatasetError("workers must be between 1 and 16")
    inventory = list_remote_inventory()
    write_inventory(root, inventory)
    counts = defaultdict(int)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_download_one, root, item): item["name"]
            for item in inventory["files"]
        }
        try:
            for future in as_completed(futures):
                counts[future.result()] += 1
        except Exception:
            for future in futures:
                future.cancel()
            raise
    verified = verify_download(root, inventory)
    return {**verified, **dict(sorted(counts.items()))}


def parse_data_yaml(path: Path) -> Tuple[str, ...]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise RadDatasetError(f"missing or unreadable RAD data.yaml: {path}") from error
    values: Dict[str, str] = {}
    for line_number, raw_line in enumerate(lines, 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            raise RadDatasetError(f"data.yaml:{line_number}: malformed line")
        key, value = (piece.strip() for piece in line.split(":", 1))
        if key not in {"train", "val", "test", "nc", "names"} or key in values:
            raise RadDatasetError(f"data.yaml:{line_number}: unknown or duplicate key {key!r}")
        values[key] = value
    if set(values) != {"train", "val", "test", "nc", "names"}:
        raise RadDatasetError("data.yaml does not contain the exact RAD schema")
    try:
        class_count = int(values["nc"])
        class_names = ast.literal_eval(values["names"])
    except (ValueError, SyntaxError) as error:
        raise RadDatasetError("data.yaml has malformed nc or names") from error
    if class_count != len(EXPECTED_CLASSES) or tuple(class_names) != EXPECTED_CLASSES:
        raise RadDatasetError("RAD class schema changed; semantic review is required")
    expected_paths = {"train": "train/images", "val": "valid/images", "test": "test/images"}
    for key, suffix in expected_paths.items():
        value = values[key].replace("\\", "/").rstrip("/")
        if value.startswith("/") or not value.endswith(suffix):
            raise RadDatasetError(f"data.yaml has an unsafe or unexpected {key} path")
    return tuple(class_names)


def parse_yolo_labels(path: Path, class_names: Sequence[str]) -> List[Dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise RadDatasetError(f"missing or unreadable YOLO label: {path}") from error
    boxes = []
    for line_number, raw_line in enumerate(lines, 1):
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 5:
            raise RadDatasetError(f"{path.name}:{line_number}: expected 5 YOLO fields")
        if not re.fullmatch(r"[0-9]+", parts[0]):
            raise RadDatasetError(f"{path.name}:{line_number}: malformed class id")
        class_id = int(parts[0])
        if not 0 <= class_id < len(class_names):
            raise RadDatasetError(f"{path.name}:{line_number}: unknown class id {class_id}")
        try:
            coordinates = tuple(float(value) for value in parts[1:])
        except ValueError as error:
            raise RadDatasetError(f"{path.name}:{line_number}: malformed coordinate") from error
        if (not all(math.isfinite(value) for value in coordinates)
                or not all(0.0 <= value <= 1.0 for value in coordinates)):
            raise RadDatasetError(f"{path.name}:{line_number}: coordinate outside [0,1]")
        x_center, y_center, width, height = coordinates
        if width <= 0.0 or height <= 0.0:
            raise RadDatasetError(f"{path.name}:{line_number}: empty YOLO box")
        tolerance = 1e-5
        if (x_center - width / 2 < -tolerance or x_center + width / 2 > 1 + tolerance
                or y_center - height / 2 < -tolerance
                or y_center + height / 2 > 1 + tolerance):
            raise RadDatasetError(f"{path.name}:{line_number}: YOLO box leaves the full frame")
        class_name = class_names[class_id]
        boxes.append({
            "class_id": class_id,
            "class_name": class_name,
            "semantic": CLASS_SEMANTICS[class_name],
            "x_center": x_center,
            "y_center": y_center,
            "width": width,
            "height": height,
        })
    return boxes


def parse_image_identity(filename: str) -> Tuple[str, int]:
    match = IMAGE_NAME_RE.fullmatch(filename)
    if not match:
        raise RadDatasetError(f"unrecognised RAD image filename: {filename}")
    return match.group("source") + "_mp4", int(match.group("frame"))


def evaluation_split(source_video: str) -> str:
    digest = hashlib.sha256(f"{SPLIT_SALT}\0{source_video}".encode("utf-8")).digest()
    bucket = int.from_bytes(digest[:8], "big") % SPLIT_THRESHOLDS[-1]
    if bucket < SPLIT_THRESHOLDS[0]:
        return "train"
    if bucket < SPLIT_THRESHOLDS[1]:
        return "validation"
    return "test"


def _dataset_dir(root: Path) -> Path:
    if (root / "data.yaml").is_file():
        return root
    if (root / "images" / "data.yaml").is_file():
        return root / "images"
    raise RadDatasetError("RAD root must contain data.yaml or images/data.yaml")


def _image_metadata(path: Path) -> Tuple[int, int, str]:
    try:
        with Image.open(path) as image:
            width, height = image.size
            image_format = image.format
            image.verify()
    except (OSError, UnidentifiedImageError) as error:
        raise RadDatasetError(f"invalid image: {path}") from error
    if image_format != "JPEG" or width <= 0 or height <= 0:
        raise RadDatasetError(f"RAD image is not a non-empty JPEG: {path}")
    return width, height, sha256_file(path)


def _path_from_repo_root(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def build_index(root: Path, require_complete: bool = True) -> Dict[str, Any]:
    dataset_dir = _dataset_dir(root)
    class_names = parse_data_yaml(dataset_dir / "data.yaml")
    candidates: Dict[Tuple[str, int], List[Dict[str, Any]]] = defaultdict(list)
    repo_root = root.resolve()
    source_counts: Dict[str, Any] = {
        "images": 0,
        "labels": 0,
        "boxes": 0,
        "class_boxes": {name: 0 for name in class_names},
        "road_damage_frames": 0,
        "speed_breaker_frames": 0,
        "pure_speed_breaker_frames": 0,
        "road_damage_and_speed_breaker_frames": 0,
        "empty_labels": 0,
    }

    for source_split in ("train", "valid", "test"):
        image_dir = dataset_dir / source_split / "images"
        label_dir = dataset_dir / source_split / "labels"
        if not image_dir.is_dir() or not label_dir.is_dir():
            raise RadDatasetError(f"missing RAD {source_split} image or label directory")
        images = sorted(
            path for path in image_dir.iterdir()
            if path.is_file() and path.suffix.lower() in (".jpg", ".jpeg")
        )
        labels = sorted(path for path in label_dir.iterdir() if path.is_file())
        unexpected_labels = [path.name for path in labels if path.suffix != ".txt"]
        if unexpected_labels:
            raise RadDatasetError(f"unexpected file in labels directory: {unexpected_labels[0]}")
        expected_label_names = {path.with_suffix(".txt").name for path in images}
        actual_label_names = {path.name for path in labels}
        missing = expected_label_names - actual_label_names
        orphaned = actual_label_names - expected_label_names
        if missing or orphaned:
            raise RadDatasetError(
                f"{source_split}: image/label pairing failed "
                f"({len(missing)} missing, {len(orphaned)} orphaned)"
            )
        for image_path in images:
            label_path = label_dir / image_path.with_suffix(".txt").name
            source_video, frame_number = parse_image_identity(image_path.name)
            width, height, image_sha = _image_metadata(image_path)
            boxes = parse_yolo_labels(label_path, class_names)
            semantics = sorted({box["semantic"] for box in boxes})
            source_counts["images"] += 1
            source_counts["labels"] += 1
            source_counts["boxes"] += len(boxes)
            present_classes = {box["class_name"] for box in boxes}
            for box in boxes:
                source_counts["class_boxes"][box["class_name"]] += 1
            has_damage = "RoadDamages" in present_classes
            has_breaker = "SpeedBump" in present_classes
            source_counts["road_damage_frames"] += int(has_damage)
            source_counts["speed_breaker_frames"] += int(has_breaker)
            source_counts["pure_speed_breaker_frames"] += int(has_breaker and not has_damage)
            source_counts["road_damage_and_speed_breaker_frames"] += int(
                has_damage and has_breaker
            )
            source_counts["empty_labels"] += int(not boxes)
            variant = {
                "original_split": source_split,
                "image_path": _path_from_repo_root(image_path, repo_root),
                "label_path": _path_from_repo_root(label_path, repo_root),
                "image_sha256": image_sha,
                "width": width,
                "height": height,
                "boxes": boxes,
                "semantic_labels": semantics,
            }
            candidates[(source_video, frame_number)].append(variant)

    if require_complete and source_counts != EXPECTED_V3_COUNTS:
        raise RadDatasetError(
            "RAD v3 is incomplete or has changed annotation counts; "
            f"expected {EXPECTED_V3_COUNTS}, got {source_counts}"
        )

    frames = []
    for (source_video, frame_number), variants in sorted(candidates.items()):
        variants.sort(key=lambda item: item["image_path"])
        selected = variants[0]
        # Roboflow placed augmented copies of some source frames in multiple
        # folders, and their target annotations can disagree.  The complete
        # selected image remains the sole model input, but target semantics are
        # unioned across every copy so file ordering can never erase a road
        # anomaly or turn a mixed annotation into a speed-breaker hard negative.
        selected_semantics = set(selected["semantic_labels"])
        variant_target_semantics = {
            semantic
            for item in variants
            for semantic in item["semantic_labels"]
            if semantic in TARGET_SEMANTICS
        }
        evaluation_semantics = sorted(selected_semantics | variant_target_semantics)
        frame_id = f"{source_video}:{frame_number}"
        frame = {
            "id": frame_id,
            "source_video": source_video,
            "frame_number": frame_number,
            "original_split": selected["original_split"],
            "original_splits": sorted({item["original_split"] for item in variants}),
            "evaluation_split": evaluation_split(source_video),
            "image_path": selected["image_path"],
            "label_path": selected["label_path"],
            "image_sha256": selected["image_sha256"],
            "width": selected["width"],
            "height": selected["height"],
            "boxes": selected["boxes"],
            "semantic_labels": evaluation_semantics,
            "selected_variant_semantic_labels": selected["semantic_labels"],
            "target_semantics_are_variant_union": True,
            "source_variant_count": len(variants),
            "provenance_ambiguous": source_video in KNOWN_AMBIGUOUS_SOURCE_VIDEOS,
            "full_frame": True,
        }
        frames.append(frame)

    by_source: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for frame in frames:
        by_source[frame["source_video"]].append(frame)
    events = []
    skipped_repeated_pixels = 0
    for source_video, source_frames in sorted(by_source.items()):
        source_frames.sort(key=lambda item: item["frame_number"])
        if source_video in KNOWN_AMBIGUOUS_SOURCE_VIDEOS:
            continue
        for index in range(len(source_frames) - 2):
            window = source_frames[index:index + 3]
            numbers = [item["frame_number"] for item in window]
            if numbers != list(range(numbers[0], numbers[0] + 3)):
                continue
            hashes = [item["image_sha256"] for item in window]
            if len(set(hashes)) != 3:
                skipped_repeated_pixels += 1
                continue
            event_id = f"{source_video}:{numbers[0]}-{numbers[-1]}"
            events.append({
                "id": event_id,
                "source_video": source_video,
                "evaluation_split": evaluation_split(source_video),
                "frame_numbers": numbers,
                "frame_ids": [item["id"] for item in window],
                "semantic_labels": sorted({
                    semantic for item in window for semantic in item["semantic_labels"]
                }),
                "full_frame": True,
            })

    source_split_map = {
        source_video: evaluation_split(source_video) for source_video in sorted(by_source)
    }
    index = {
        "schema_version": INDEX_SCHEMA_VERSION,
        "dataset": {
            "name": DATASET_NAME,
            "ref": DATASET_REF,
            "version": DATASET_VERSION,
            "license": DATASET_LICENSE,
            "source_url": DATASET_URL,
            "metadata_url": DATASET_METADATA_URL,
            "paper_doi": PAPER_DOI,
            "label_limitation": ROAD_DAMAGE_LABEL_LIMITATION,
        },
        "class_names": list(class_names),
        "class_semantics": dict(CLASS_SEMANTICS),
        "split_policy": {
            "unit": "source_video",
            "salt": SPLIT_SALT,
            "buckets": {"train": 8000, "validation": 1000, "test": 1000},
            "source_splits": source_split_map,
        },
        "provenance": {
            "ambiguous_source_videos": sorted(KNOWN_AMBIGUOUS_SOURCE_VIDEOS),
            "ambiguous_sources_are_excluded_from_events": True,
        },
        "input_policy": {
            "full_frame_only": True,
            "boxes_are_metadata_only": True,
            "spatial_transforms": [],
            "duplicate_target_semantics": "union across all source-frame variants",
            "event_size": 3,
            "chronology": "strict consecutive numeric source-frame IDs",
        },
        "counts": {
            "source_images": sum(len(items) for items in candidates.values()),
            "unique_source_frames": len(frames),
            "source_videos": len(by_source),
            "events": len(events),
            "skipped_repeated_pixel_windows": skipped_repeated_pixels,
        },
        "source_dataset_counts": source_counts,
        "source_dataset_complete": source_counts == EXPECTED_V3_COUNTS,
        "frames": frames,
        "events": events,
    }
    return _sealed(index, "index_sha256")


def write_index(path: Path, index: Mapping[str, Any]) -> None:
    _verify_seal(index, "index_sha256")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(index))


def load_index(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as error:
        raise RadDatasetError(f"cannot read RAD index: {path}") from error
    if not isinstance(value, dict) or value.get("schema_version") != INDEX_SCHEMA_VERSION:
        raise RadDatasetError("unsupported RAD index schema")
    _verify_seal(value, "index_sha256")
    policy = value.get("input_policy")
    if not isinstance(policy, dict) or policy != {
        "full_frame_only": True,
        "boxes_are_metadata_only": True,
        "spatial_transforms": [],
        "duplicate_target_semantics": "union across all source-frame variants",
        "event_size": 3,
        "chronology": "strict consecutive numeric source-frame IDs",
    }:
        raise RadDatasetError("RAD index does not preserve the full-frame invariant")
    if "pothole" in set(value.get("class_semantics", {}).values()):
        raise RadDatasetError("RAD source labels must never be promoted to pothole ground truth")
    return value


def validate_official_release_index(value: Mapping[str, Any]) -> None:
    """Require the exact complete RAD v3 source/index used by the release audit."""
    dataset = value.get("dataset")
    if (
        not isinstance(dataset, dict)
        or dataset.get("ref") != DATASET_REF
        or dataset.get("version") != DATASET_VERSION
        or dataset.get("name") != DATASET_NAME
    ):
        raise RadDatasetError("release gate requires the official RAD v3 dataset identity")
    if value.get("schema_version") != INDEX_SCHEMA_VERSION:
        raise RadDatasetError("release gate requires the current RAD v3 index schema")
    if value.get("source_dataset_complete") is not True:
        raise RadDatasetError("release gate requires the complete RAD v3 source dataset")
    if value.get("source_dataset_counts") != EXPECTED_V3_COUNTS:
        raise RadDatasetError("release gate source counts do not match the audited RAD v3 source")
    if value.get("counts") != EXPECTED_V3_INDEX_COUNTS:
        raise RadDatasetError("release gate index counts do not match the audited RAD v3 index")
    if value.get("class_names") != list(EXPECTED_CLASSES):
        raise RadDatasetError("release gate RAD class order does not match the audited source")
    if value.get("class_semantics") != CLASS_SEMANTICS:
        raise RadDatasetError("release gate RAD class semantics do not match the audited source")
    if value.get("index_sha256") != EXPECTED_V3_INDEX_SHA256:
        raise RadDatasetError("release gate index seal does not match the audited RAD v3 index")


def _command_download(args: argparse.Namespace) -> None:
    result = download_images(Path(args.root), workers=args.workers)
    print(json.dumps(result, sort_keys=True))


def _command_verify(args: argparse.Namespace) -> None:
    root = Path(args.root)
    inventory = load_inventory(root / INVENTORY_FILE)
    print(json.dumps(verify_download(root, inventory), sort_keys=True))


def _command_index(args: argparse.Namespace) -> None:
    index = build_index(Path(args.root), require_complete=not args.allow_incomplete)
    write_index(Path(args.out), index)
    print(json.dumps({
        "index": str(Path(args.out)),
        "index_sha256": index["index_sha256"],
        **index["counts"],
    }, sort_keys=True))


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="Download and index the full-frame RAD image subset"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    download = subparsers.add_parser(
        "download", help="download only public Kaggle images/** files, with resume"
    )
    download.add_argument("--root", required=True)
    download.add_argument("--workers", type=int, default=6)
    download.set_defaults(handler=_command_download)
    verify = subparsers.add_parser("verify", help="verify all downloaded sizes")
    verify.add_argument("--root", required=True)
    verify.set_defaults(handler=_command_verify)
    index = subparsers.add_parser("index", help="validate and write a deterministic index")
    index.add_argument("--root", required=True)
    index.add_argument("--out", required=True)
    index.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="development fixtures only: write an index that is explicitly marked incomplete",
    )
    index.set_defaults(handler=_command_index)
    args = parser.parse_args(argv)
    try:
        args.handler(args)
    except RadDatasetError as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
