#!/usr/bin/env python3
"""Build the pothole detection set from the public archives' own human-drawn boxes.

    python prepare.py                 # everything
    python prepare.py --only bucko    # one dataset (a trial)
    python prepare.py --limit 200     # at most this many images per dataset and split

The first screen learned gpt-5-mini's verdict of "any road damage". This set answers the
narrower question the owner asked for, "is there a pothole, and where", from what the
datasets' annotators drew. A box is four numbers in the annotation file; nothing is ever
drawn on a picture. One class: pothole. Cracks, patches and clean road are negatives.

Whole frames only (AGENTS.md): an image is downscaled to at most 1280 px on its long
side and never cropped, tiled or masked. Boxes are scaled with it.

Splits are by source, fixed here:
  train  RDD2022 Japan, Norway, China (motorbike, drone), India blocks; the dash-camera
         pothole set (Bucko); BharatPotHole; Brazil; Attain; Road Damage (alvarobasily);
         RAD training videos and the owner's training drive frames as negatives only
  val    RDD2022 Czech (whole country); the Rome set (whole); India blocks; RAD
         validation videos and the owner's validation drive frames as negatives
  test   IRDD (whole dataset, never trained on); India blocks; RAD test videos; the
         owner's held-out drive frames and labelled images

RAD's "RoadDamages" box is a broad anomaly mark, not a pothole label (eval/rad_dataset.py),
so a RAD frame with one is left out of training and only counted at test.

Output under $DET_DATA: train2017/ val2017/ test2017/ (images), annotations/train.json,
val.json, test.json (COCO), index.jsonl (one line per image with where it came from),
stats.json, and debug/ (a few pictures per dataset WITH their boxes drawn, to check the
parsing by eye; debug pictures are never read by training).
"""
import argparse
import io
import json
import os
import random
import re
import shutil
import sys
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

Image.MAX_IMAGE_PIXELS = None
RAW = Path(os.environ.get("DET_RAW", "/opt/ml/raw"))
OUT = Path(os.environ.get("DET_DATA", "/opt/ml/det/data"))
OWNER = Path(os.environ.get("DET_OWNER", "/opt/ml/owner"))
REPO = Path(__file__).resolve().parents[2]
MAX_SIDE = 1280
SEED = 20261009
_archives = {}


def read_ref(ref):
    if "file" in ref:
        return Path(ref["file"]).read_bytes()
    if ref["zip"] not in _archives:
        _archives[ref["zip"]] = zipfile.ZipFile(ref["zip"])
    return _archives[ref["zip"]].read(ref["member"])


def safe(name):
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name)


def negatives(pool, positives, rng, floor=300, ratio=1.5):
    """All the images with a pothole, and beside them at most `ratio` times as many
    without (never fewer than `floor` when the source has them)."""
    pool = sorted(pool, key=lambda item: item["name"])
    rng.shuffle(pool)
    return pool[:max(floor, int(ratio * positives))]


# --- RDD2022 ---------------------------------------------------------------------------
# D40 is the pothole class. India is split by blocks of 100 consecutive frames, because
# neighbouring frames show the same stretch of road. The United States images are Google
# Street View captures and are left out, as in the screen's data.
RDD = {"Japan": "train", "Norway": "train", "China_MotorBike": "train", "China_Drone": "train",
       "India": "blocks", "Czech": "val"}


def india_split(index):
    block = (index // 100) % 10
    return "test" if block in (8, 9) else "val" if block == 7 else "train"


def rdd2022(rng):
    outer_path = RAW / "rdd2022" / "RDD2022.zip"
    inner_dir = RAW / "rdd2022" / "countries"
    inner_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(outer_path) as outer:
        members = {Path(name).name: name for name in outer.namelist() if name.endswith(".zip")}
        for country in RDD:
            target = inner_dir / f"{country}.zip"
            if not target.exists():
                partial = target.with_suffix(".part")
                with outer.open(members[f"{country}.zip"]) as source, open(partial, "wb") as output:
                    shutil.copyfileobj(source, output, 16 * 1024 * 1024)
                partial.rename(target)
    for country, split in RDD.items():
        path = str(inner_dir / f"{country}.zip")
        archive = zipfile.ZipFile(path)
        images = {info.filename.rsplit("/", 1)[1][:-4]: info.filename for info in archive.infolist()
                  if "/train/images/" in info.filename and info.filename.lower().endswith(".jpg")}
        with_pothole, without = [], []
        for info in archive.infolist():
            if not info.filename.endswith(".xml"):
                continue
            stem = info.filename.rsplit("/", 1)[1][:-4]
            if stem not in images:
                continue
            root = ET.fromstring(archive.read(info))
            boxes, classes = [], set()
            for found in root.iter("object"):
                name = (found.findtext("name") or "").strip()
                classes.add(name)
                box = found.find("bndbox")
                if name == "D40" and box is not None:
                    boxes.append({"xyxy": [float(box.findtext(key)) for key in ("xmin", "ymin", "xmax", "ymax")]})
            index = int(stem.split("_")[-1])
            item = {"name": f"rdd-{safe(stem)}.jpg", "ref": {"zip": path, "member": images[stem]},
                    "dataset": f"rdd2022_{country.lower()}", "source": f"rdd2022-{country.lower()}",
                    "split": india_split(index) if split == "blocks" else split, "boxes": boxes,
                    "meta": {"classes": sorted(classes)}}
            (with_pothole if boxes else without).append(item)
        if split == "blocks":  # the ratio is kept inside each split
            for part in ("train", "val", "test"):
                yes = [item for item in with_pothole if item["split"] == part]
                no = [item for item in without if item["split"] == part]
                # Test keeps every frame: a clean test set must not be thinned by rule.
                yield from yes + (no if part == "test" else negatives(no, len(yes), rng))
        else:
            yield from with_pothole + negatives(without, len(with_pothole), rng)


# --- IRDD ------------------------------------------------------------------------------
def irdd(rng):
    """Phone-on-dashboard frames from Iraq with oriented boxes in the RDD classes
    (class 3 is D40). Held out whole: test only, every frame."""
    path = str(RAW / "irdd-iraq" / "IRDD_v1.0_final.zip")
    archive = zipfile.ZipFile(path)
    labels = {info.filename[:-4].replace("/labels/", "/images/"): info.filename
              for info in archive.infolist() if info.filename.endswith(".txt")}
    for info in archive.infolist():
        if not info.filename.lower().endswith(".jpg") or info.filename[:-4] not in labels:
            continue
        boxes, kinds = [], set()
        for line in archive.read(labels[info.filename[:-4]]).decode().splitlines():
            parts = line.split()
            if not parts:
                continue
            kinds.add(parts[0])
            if parts[0] == "3":
                values = [float(value) for value in parts[1:]]
                boxes.append({"poly": values} if len(values) >= 8 else {"yolo": values[:4]})
        yield {"name": f"irdd-{safe(info.filename.rsplit('/', 1)[1])}", "ref": {"zip": path, "member": info.filename},
               "dataset": "irdd", "source": "irdd-iraq", "split": "test", "boxes": boxes,
               "meta": {"classes": sorted(kinds)}}


# --- Bucko et al. ----------------------------------------------------------------------
def bucko(rng):
    """Dash-camera frames by day, at sunset, in the evening, at night and in rain, with
    pothole boxes in YOLO text files beside the pictures."""
    path = str(RAW / "bucko-dashcam" / "Potholes_dataset.zip")
    archive = zipfile.ZipFile(path)
    names = set(archive.namelist())
    for name in sorted(names):
        if not name.lower().endswith(".jpg"):
            continue
        label = name[:-4] + ".txt"
        boxes, kinds = [], Counter()
        if label in names:
            for line in archive.read(label).decode().splitlines():
                parts = line.split()
                if len(parts) >= 5:
                    kinds[parts[0]] += 1
                    boxes.append({"yolo": [float(value) for value in parts[1:5]]})
        condition, stem = name.split("/")[-2:]
        yield {"name": f"bucko-{safe(condition)}-{safe(stem)}", "ref": {"zip": path, "member": name},
               "dataset": "bucko", "source": f"bucko-{condition.lower()}", "split": "train", "boxes": boxes,
               "meta": {"label_classes": dict(kinds)}}


# --- Cracks and Potholes in Road Images (Brazil) ---------------------------------------
def brazil(rng):
    """Survey-vehicle frames with a pothole mask each. A box is the bounding rectangle of
    each connected patch of the mask (worked out when the frame is written)."""
    path = str(RAW / "cracks-potholes-brazil" / "cracks-and-potholes-in-road-images.zip")
    archive = zipfile.ZipFile(path)
    names = set(archive.namelist())
    for name in sorted(names):
        if not name.endswith("_RAW.jpg"):
            continue
        mask = name.replace("_RAW.jpg", "_POTHOLE.png")
        if mask not in names:
            continue
        stem = name.rsplit("/", 1)[1][:-len("_RAW.jpg")]
        yield {"name": f"brazil-{safe(stem)}.jpg", "ref": {"zip": path, "member": name},
               "dataset": "brazil", "source": "brazil", "split": "train", "boxes": [],
               "mask": {"zip": path, "member": mask}, "meta": {}}


# --- Attain (windshield subsets) -------------------------------------------------------
ATTAIN_WS1 = ["Alligator crack", "Alligator crack", "Alligator crack", "Block crack", "Faded marking",
              "Faded marking", "Linear crack", "Linear crack", "Manhole", "Manhole", "Patch", "Pothole",
              "Pothole", "Raveling", "Weathering", "Weathering"]


def attain(rng):
    path = str(RAW / "attain-iran" / "attain-nykrzdm74f-v1.zip")
    archive = zipfile.ZipFile(path)
    names = set(archive.namelist())
    for name in sorted(names):
        if not name.lower().endswith(".jpg") or "_WS_" not in name:
            continue
        text_label = name.replace("/Images/", "/Labels/")[:-4] + ".txt"
        xml_label = name.replace("/Images/", "/Labels/")[:-4] + ".xml"
        boxes, kinds = [], set()
        if text_label in names:
            for line in archive.read(text_label).decode().splitlines():
                parts = line.split()
                if len(parts) < 5:
                    continue
                kind = ATTAIN_WS1[int(parts[0])]
                kinds.add(kind)
                if kind == "Pothole":
                    boxes.append({"yolo": [float(value) for value in parts[1:5]]})
        elif xml_label in names:
            root = ET.fromstring(archive.read(xml_label))
            for found in root.iter("object"):
                kind = (found.findtext("name") or "").split(" - ")[0].split("- ")[0].strip()
                kinds.add(kind)
                box = found.find("bndbox")
                if kind == "Pothole" and box is not None:
                    boxes.append({"xyxy": [float(box.findtext(key)) for key in ("xmin", "ymin", "xmax", "ymax")]})
        else:
            continue
        yield {"name": f"attain-{safe(name.rsplit('/', 1)[1])}", "ref": {"zip": path, "member": name},
               "dataset": "attain", "source": "attain", "split": "train", "boxes": boxes,
               "meta": {"classes": sorted(kinds)}}


# --- Rome ------------------------------------------------------------------------------
def rome(rng):
    """GoPro frames around Rome; class 0 is pothole. Held out whole for validation."""
    path = str(RAW / "rome-road-damage" / "data.zip")
    archive = zipfile.ZipFile(path)
    names = set(archive.namelist())
    for name in sorted(names):
        if "/images/" not in name or not name.lower().endswith(".jpg"):
            continue
        label = name.replace("/images/", "/labels-YOLO/")[:-4] + ".txt"
        boxes, kinds = [], set()
        if label in names:
            for line in archive.read(label).decode().splitlines():
                parts = line.split()
                if len(parts) >= 5:
                    kinds.add(parts[0])
                    if parts[0] == "0":
                        boxes.append({"yolo": [float(value) for value in parts[1:5]]})
        yield {"name": f"rome-{safe(name.rsplit('/', 1)[1])}", "ref": {"zip": path, "member": name},
               "dataset": "rome", "source": "rome", "split": "val", "boxes": boxes,
               "meta": {"label_classes": sorted(kinds)}}


# --- BharatPotHole ---------------------------------------------------------------------
def bharat(rng):
    """Indian dashcam frames with pothole boxes. The export holds some frames more than
    once; one copy is kept (the first by name) with that copy's own boxes."""
    path = str(RAW / "bharatpothole" / "bharatpothole.zip")
    archive = zipfile.ZipFile(path)
    names = set(archive.namelist())
    seen = set()
    for name in sorted(names):
        if "/images/" not in name or not name.lower().endswith(".jpg"):
            continue
        stem = name.rsplit("/", 1)[1].split("_jpg.rf.")[0]
        if stem in seen:
            continue
        seen.add(stem)
        label = name.replace("/images/", "/labels/")[:-4] + ".txt"
        boxes, kinds = [], Counter()
        if label in names:
            for line in archive.read(label).decode().splitlines():
                parts = line.split()
                if len(parts) >= 5:
                    kinds[parts[0]] += 1
                    values = [float(value) for value in parts[1:]]
                    boxes.append({"yolo": values[:4]} if len(values) == 4 else {"poly": values})
        yield {"name": f"bharat-{safe(stem)}.jpg", "ref": {"zip": path, "member": name},
               "dataset": "bharat", "source": "bharat-" + stem.split("_frame_")[0].lower(), "split": "train",
               "boxes": boxes, "meta": {"label_classes": dict(kinds)}}


# --- Road Damage (alvarobasily) --------------------------------------------------------
def alvaro(rng):
    """Phone frames from a moving vehicle; class 0 is D40 (pothole)."""
    path = str(RAW / "road-damage-alvarobasily" / "road-damage.zip")
    archive = zipfile.ZipFile(path)
    names = set(archive.namelist())
    with_pothole, without = [], []
    for name in sorted(names):
        if not name.lower().endswith((".jpeg", ".jpg")):
            continue
        label = name.rsplit(".", 1)[0] + ".txt"
        boxes, kinds = [], set()
        if label in names:
            for line in archive.read(label).decode().splitlines():
                parts = line.split()
                if len(parts) >= 5:
                    kinds.add(parts[0])
                    if parts[0] == "0":
                        boxes.append({"yolo": [float(value) for value in parts[1:5]]})
        stem = name.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        item = {"name": f"alvaro-{safe(stem)}.jpg", "ref": {"zip": path, "member": name},
                "dataset": "alvaro", "source": "alvaro-" + stem.split("_")[0].lower(), "split": "train",
                "boxes": boxes, "meta": {"label_classes": sorted(kinds)}}
        (with_pothole if boxes else without).append(item)
    yield from with_pothole + negatives(without, len(with_pothole), rng)


# --- RAD (Bengaluru) -------------------------------------------------------------------
def rad(rng):
    sys.path.insert(0, str(REPO / "eval"))
    import rad_dataset

    root = RAW / "rad-bengaluru" / "files"
    index = rad_dataset.build_index(root, require_complete=True)
    by_split = defaultdict(list)
    for frame in index["frames"]:
        video = frame["source_video"].lower().replace("_mp4", "").replace("_", "-")
        anomaly = "unreviewed_road_anomaly" in frame["semantic_labels"]
        split = {"train": "train", "validation": "val", "test": "test"}[frame["evaluation_split"]]
        if anomaly and split != "test":
            continue  # not a pothole label and not a clean negative either
        by_split[split].append({
            "name": f"rad-{safe(video)}-f{frame['frame_number']:05d}.jpg",
            "ref": {"file": str(root / frame["image_path"])},
            "dataset": "rad", "source": f"rad-{video}", "split": split, "boxes": [],
            "meta": {"rad_anomaly": anomaly, "speed_breaker": "speed_breaker" in frame["semantic_labels"]}})
    yield from negatives(by_split["train"], 0, rng, floor=2500)
    yield from negatives(by_split["val"], 0, rng, floor=600)
    yield from by_split["test"]


# --- The owner's drive video -----------------------------------------------------------
def owner(rng):
    """His Bengaluru drive frames, with gpt-5-mini's verdict (no human boxes). Frames the
    teacher called undamaged are negatives in the split the screen's manifest gave them;
    the held-out test frames are all kept, with the verdict, for the scorecard. Private:
    read from the private bucket's copy and never written anywhere else."""
    manifest = OWNER / "manifest.jsonl"
    if not manifest.exists():
        return
    for line in manifest.open():
        row = json.loads(line)
        if not str(row.get("dataset", "")).startswith("owner"):
            continue
        teacher = row.get("teacher") or {}
        split = {"train": "train", "validation": "val", "test": "test"}.get(row.get("split"), "test")
        drive = row["dataset"] == "owner_drive"
        if not drive:
            split = "test"
        elif split != "test" and teacher.get("assessment") != "undamaged":
            continue
        source = OWNER / "frames" / row["path"]
        if not source.exists():
            source = OWNER / "owner" / Path(row["path"]).name
        if not source.exists():
            continue
        yield {"name": f"owner-{safe(row['path'])}", "ref": {"file": str(source)},
               "dataset": row["dataset"], "source": row.get("source", "owner"), "split": split, "boxes": [],
               "meta": {"teacher_assessment": teacher.get("assessment"),
                        "teacher_damage_type": teacher.get("damage_type"),
                        "owner_label": row.get("owner_label") or row.get("human_label") or row.get("label"),
                        "tier": row.get("tier")}}


ADAPTERS = {"rdd2022": rdd2022, "irdd": irdd, "bucko": bucko, "brazil": brazil, "attain": attain,
            "rome": rome, "bharat": bharat, "alvaro": alvaro, "rad": rad, "owner": owner}


def to_pixels(spec, width, height):
    if "xyxy" in spec:
        return spec["xyxy"]
    if "yolo" in spec:
        cx, cy, w, h = spec["yolo"]
        unit = max(cx, cy, w, h) <= 1.5
        sx, sy = (width, height) if unit else (1, 1)
        return [(cx - w / 2) * sx, (cy - h / 2) * sy, (cx + w / 2) * sx, (cy + h / 2) * sy]
    values = spec["poly"]
    xs, ys = values[0::2], values[1::2]
    unit = max(values) <= 1.5
    sx, sy = (width, height) if unit else (1, 1)
    return [min(xs) * sx, min(ys) * sy, max(xs) * sx, max(ys) * sy]


def mask_boxes(mask_ref, width, height):
    from scipy import ndimage

    mask = np.asarray(Image.open(io.BytesIO(read_ref(mask_ref))).convert("L")) > 127
    labelled, count = ndimage.label(mask)
    sx, sy = width / mask.shape[1], height / mask.shape[0]
    found = []
    for piece in ndimage.find_objects(labelled):
        ys, xs = piece
        if (ys.stop - ys.start) * (xs.stop - xs.start) < 64:
            continue  # specks of the mask, not a pothole
        found.append([xs.start * sx, ys.start * sy, xs.stop * sx, ys.stop * sy])
    return found


def write(item):
    try:
        image = Image.open(io.BytesIO(read_ref(item["ref"])))
        rotation = image.getexif().get(274, 1)
        image = image.convert("RGB")
    except Exception as error:  # a broken member is counted, not fatal
        return {"name": item["name"], "error": str(error)[:120], "dataset": item["dataset"]}
    width, height = image.size
    boxes = [to_pixels(spec, width, height) for spec in item["boxes"]]
    if item.get("mask"):
        boxes += mask_boxes(item["mask"], width, height)
    scale = min(1.0, MAX_SIDE / max(width, height))
    if scale < 1.0:
        image = image.resize((round(width * scale), round(height * scale)), Image.LANCZOS)
    new_width, new_height = image.size
    kept = []
    for x1, y1, x2, y2 in boxes:
        x1, x2 = sorted((min(max(x1 * scale, 0), new_width), min(max(x2 * scale, 0), new_width)))
        y1, y2 = sorted((min(max(y1 * scale, 0), new_height), min(max(y2 * scale, 0), new_height)))
        if x2 - x1 >= 3 and y2 - y1 >= 3:
            kept.append([round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1)])
    folder = OUT / f"{item['split']}2017"
    image.save(folder / item["name"], "JPEG", quality=90)
    if item.get("debug"):
        draw = ImageDraw.Draw(image)
        for box in kept:
            draw.rectangle(box, outline=(255, 0, 255), width=3)
        image.save(OUT / "debug" / f"{item['dataset']}__{item['name']}", "JPEG", quality=80)
    return {"name": item["name"], "dataset": item["dataset"], "source": item["source"], "split": item["split"],
            "width": new_width, "height": new_height, "boxes": kept, "dropped_boxes": len(boxes) - len(kept),
            "exif_rotated": rotation not in (1, None), "meta": item["meta"]}


def coco(rows):
    images, annotations = [], []
    for image_id, row in enumerate(rows, 1):
        images.append({"id": image_id, "file_name": row["name"], "width": row["width"], "height": row["height"],
                       "dataset": row["dataset"], "source": row["source"], "meta": row["meta"]})
        for x1, y1, x2, y2 in row["boxes"]:
            annotations.append({"id": len(annotations) + 1, "image_id": image_id, "category_id": 1,
                                "bbox": [x1, y1, round(x2 - x1, 1), round(y2 - y1, 1)],
                                "area": round((x2 - x1) * (y2 - y1), 1), "iscrowd": 0})
    return {"images": images, "annotations": annotations, "categories": [{"id": 1, "name": "pothole"}]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", nargs="*")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int, default=max(2, (os.cpu_count() or 4) - 1))
    options = parser.parse_args()
    for folder in ("train2017", "val2017", "test2017", "annotations", "debug"):
        (OUT / folder).mkdir(parents=True, exist_ok=True)
    rng = random.Random(SEED)
    items = []
    for name, adapter in ADAPTERS.items():
        if options.only and name not in options.only:
            continue
        found = list(adapter(rng))
        if options.limit:
            limited, taken = [], Counter()
            for item in sorted(found, key=lambda entry: (not entry["boxes"] and not entry.get("mask"), entry["name"])):
                if taken[item["split"]] < options.limit:
                    taken[item["split"]] += 1
                    limited.append(item)
            found = limited
        shown = Counter()
        for item in found:  # the first six with a mark, per dataset, are drawn for the eye
            if (item["boxes"] or item.get("mask")) and shown[item["dataset"]] < 6:
                shown[item["dataset"]] += 1
                item["debug"] = True
        print(f"{name}: {len(found)} images chosen", flush=True)
        items += found
    names = Counter(item["name"] for item in items)
    clash = [name for name, count in names.items() if count > 1]
    if clash:
        sys.exit(f"{len(clash)} output names are not unique, for example {clash[:3]}")
    with ProcessPoolExecutor(options.workers) as pool:
        rows = list(pool.map(write, items, chunksize=16))
    errors = [row for row in rows if "error" in row]
    rows = [row for row in rows if "error" not in row]
    stats = defaultdict(lambda: Counter())
    for row in rows:
        key = f"{row['dataset']}/{row['split']}"
        stats[key]["images"] += 1
        stats[key]["with_pothole"] += bool(row["boxes"])
        stats[key]["boxes"] += len(row["boxes"])
        stats[key]["dropped_boxes"] += row["dropped_boxes"]
        stats[key]["exif_rotated"] += row["exif_rotated"]
    for split, file_name in (("train", "train.json"), ("val", "val.json"), ("test", "test.json")):
        part = sorted((row for row in rows if row["split"] == split), key=lambda row: row["name"])
        json.dump(coco(part), open(OUT / "annotations" / file_name, "w"))
    with open(OUT / "index.jsonl", "w") as output:
        for row in rows:
            output.write(json.dumps(row) + "\n")
    summary = {"by_dataset_and_split": {key: dict(value) for key, value in sorted(stats.items())},
               "unreadable": len(errors), "unreadable_examples": errors[:5],
               "totals": {split: {"images": sum(1 for row in rows if row["split"] == split),
                                  "with_pothole": sum(1 for row in rows if row["split"] == split and row["boxes"]),
                                  "boxes": sum(len(row["boxes"]) for row in rows if row["split"] == split)}
                          for split in ("train", "val", "test")}}
    json.dump(summary, open(OUT / "stats.json", "w"), indent=1)
    print(json.dumps(summary["totals"]))
    for key, value in sorted(stats.items()):
        print(key, dict(value))
    print(f"prepared {len(rows)} images, {len(errors)} unreadable")


if __name__ == "__main__":
    main()
