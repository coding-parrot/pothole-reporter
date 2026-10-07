#!/usr/bin/env python3
"""Choose images from the public archives and turn them into prepared drive frames.

    python datasets.py list                 # what each archive offers and what is chosen
    python datasets.py prepare              # every dataset
    python datasets.py prepare --only irdd  # one
    python datasets.py paths                # which frames the teacher sees once, and twice

Archives are in work/raw/<name>/ (cloud/fetch_raw.sh). Each adapter below reads one
archive's own annotations and yields candidates; a candidate is a whole image plus what
its annotators marked. The annotations are used for two things only: to choose what the
teacher is asked about (every image with a pothole mark, and likely negatives and hard
cases beside them), and for the separate "annotated pothole" recall check. The training
label is always the teacher's verdict.

Every chosen image gets the app's Drive Mode preparation through ingest.prepare():
the whole frame, downscaled only, never cropped (AGENTS.md).

Splits are by source and fixed here, before any label exists:
  train       RDD2022 Japan, Norway, China motorbike, China drone; RAD training videos;
              BharatPotHole; the dash-camera pothole set; Brazil; Attain
  validation  RDD2022 Czech (whole country); the Rome set (whole dataset); RAD validation videos
  test        IRDD (whole dataset, phone-on-dashboard, never trained on); RAD test videos
"""
import argparse
import json
import random
import re
import sys
import zipfile
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from common import FRAMES, ROOT, WORK, read_json, sha256_hex, write_json

RAW = WORK / "raw"
SEED = 20261007
_archives = {}


def read_ref(ref):
    """A candidate's bytes: a member of a zip (kept open per process) or a plain file."""
    if "file" in ref:
        return Path(ref["file"]).read_bytes()
    if ref["zip"] not in _archives:
        _archives[ref["zip"]] = zipfile.ZipFile(ref["zip"])
    return _archives[ref["zip"]].read(ref["member"])


def take(candidates, plan, rng):
    """plan: tier -> how many (None for all). Candidates of a tier are taken at random."""
    by_tier = {}
    for candidate in sorted(candidates, key=lambda item: item["name"]):
        by_tier.setdefault(candidate["tier"], []).append(candidate)
    chosen = []
    for tier, limit in plan.items():
        pool = by_tier.get(tier, [])
        rng.shuffle(pool)
        chosen += pool if limit is None else pool[:limit]
    return chosen, {tier: len(pool) for tier, pool in sorted(by_tier.items())}


# --- RDD2022 -----------------------------------------------------------------------
# D00 longitudinal crack, D10 transverse crack, D20 alligator crack, D40 pothole; the
# other codes are local to one country. India was prepared whole for v1 and stays as it
# is. The United States images are Google Street View captures: not a vehicle camera and
# not the dataset authors' to license, so they are left out.
RDD_COUNTRIES = {
    # country: (split, {tier: limit})
    "Japan": ("train", {"pothole": None, "alligator": 2000, "cracks": 800, "none": 800}),
    "Norway": ("train", {"pothole": None, "alligator": None, "cracks": 300, "none": 300}),
    "China_MotorBike": ("train", {"pothole": None, "alligator": None, "cracks": 300, "none": None}),
    "China_Drone": ("train", {"pothole": None, "alligator": 150, "cracks": 100, "none": 50}),
    "Czech": ("validation", {"pothole": None, "alligator": None, "cracks": 300, "none": 300}),
}


def rdd_tier(classes):
    kinds = set(classes)
    if "D40" in kinds:
        return "pothole"
    if "D20" in kinds:
        return "alligator"
    return "cracks" if kinds & {"D00", "D10"} else "none"


def rdd2022():
    outer_path = RAW / "rdd2022" / "RDD2022.zip"
    inner_dir = RAW / "rdd2022" / "countries"
    inner_dir.mkdir(exist_ok=True)
    with zipfile.ZipFile(outer_path) as outer:
        for country in RDD_COUNTRIES:
            if not (inner_dir / f"{country}.zip").exists():
                (inner_dir / f"{country}.zip").write_bytes(outer.read(f"RDD2022/{country}.zip"))
    for country, (split, plan) in RDD_COUNTRIES.items():
        slug = country.lower().replace("_", "-")
        path = str(inner_dir / f"{country}.zip")
        archive = zipfile.ZipFile(path)
        images = {info.filename.rsplit("/", 1)[1][:-4]: info.filename for info in archive.infolist()
                  if "/train/images/" in info.filename and info.filename.lower().endswith(".jpg")}
        candidates = []
        for info in archive.infolist():
            if not info.filename.endswith(".xml"):
                continue
            stem = info.filename.rsplit("/", 1)[1][:-4]
            if stem not in images:
                continue
            classes = sorted(set(re.findall(r"<name>([^<]+)</name>", archive.read(info).decode("utf-8", "replace"))))
            candidates.append({
                "name": stem + ".jpg", "ref": {"zip": path, "member": images[stem]},
                "dataset": "rdd2022", "domain": f"rdd2022_{slug.replace('-', '_')}",
                "source": f"rdd2022-{slug}-train", "split_hint": split, "tier": rdd_tier(classes),
                "annotated_pothole": "D40" in classes,
                "extra": {"rdd_index": int(stem.split("_")[-1]), "rdd_classes": classes}})
        yield f"rdd2022_{slug.replace('-', '_')}", candidates, plan


# --- RAD -----------------------------------------------------------------------------
def rad():
    """Bengaluru dashcam frames. The repo's adapter (eval/rad_dataset.py) checks the
    published counts, keeps one untouched frame per source frame and splits by source
    video. "RoadDamages" is a broad anomaly class, so it never counts as a pothole mark."""
    sys.path.insert(0, str(ROOT / "eval"))
    import rad_dataset

    root = RAW / "rad-bengaluru" / "files"
    index = rad_dataset.build_index(root, require_complete=True)
    candidates = []
    for frame in index["frames"]:
        video = frame["source_video"].lower().replace("_mp4", "").replace("_", "-")
        semantics = frame["semantic_labels"]
        candidates.append({
            "name": f"rad-{video}__f{frame['frame_number']:05d}.jpg",
            "ref": {"file": str(root / frame["image_path"])},
            "dataset": "rad", "domain": "rad_bengaluru", "source": f"rad-{video}",
            "split_hint": frame["evaluation_split"],
            "tier": ("road_damage" if "unreviewed_road_anomaly" in semantics
                     else "speed_breaker" if "speed_breaker" in semantics else "none"),
            "annotated_pothole": None,
            "extra": {"rad_semantics": semantics,
                      "rad_classes": sorted({box["class_name"] for box in frame["boxes"]})}})
    yield "rad_bengaluru", candidates, {"road_damage": None, "speed_breaker": None, "none": None}


# --- the owner's labelled images: the final check, never training data -------------------
def owner_rows():
    """work/owner holds the owner-labelled eval images as v1 prepared them. They are
    copied as they are (no second JPEG generation) and indexed with split 'owner'."""
    target = FRAMES / "owner-labels"
    target.mkdir(parents=True, exist_ok=True)
    rows = []
    for entry in read_json(ROOT / "eval" / "labels.json")["images"]:
        relative = (entry.get("frames") or [entry["path"]])[int(entry.get("primary_index", 0))]
        name = relative.replace("/", "__")
        source = WORK / "owner" / name
        if not source.exists():
            continue
        data = source.read_bytes()
        (target / name).write_bytes(data)
        rows.append({"path": f"owner-labels/{name}", "sha256": sha256_hex(data),
                     "domain": "owner_labels", "dataset": "owner_labels", "source": "owner-labels",
                     "split_hint": "owner", "owner_label": entry["label"],
                     "owner_labelled": entry.get("labelled_by") == "owner"})
    return rows


ADAPTERS = {"rdd2022": rdd2022, "rad": rad}


def prepare_one(candidate):
    from ingest import prepare

    return prepare(read_ref(candidate["ref"]), candidate["source"], candidate["name"],
                   candidate["domain"], dataset=candidate["dataset"],
                   split_hint=candidate["split_hint"], tier=candidate["tier"],
                   annotated_pothole=candidate["annotated_pothole"], **candidate["extra"])


def label_paths(rows):
    """Every v2 frame is labelled once; validation and test frames a second time, so the
    teacher's own repeatability can be measured where the model is judged."""
    new = [row for row in rows.values() if row.get("split_hint") in ("train", "validation", "test")]
    (WORK / "label-once.txt").write_text("".join(row["path"] + "\n" for row in new))
    twice = [row for row in new if row["split_hint"] != "train"]
    (WORK / "label-twice.txt").write_text("".join(row["path"] + "\n" for row in twice))
    print(f"label once: {len(new)} frames; label a second time: {len(twice)} of them")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["list", "prepare", "paths"])
    parser.add_argument("--only", action="append", default=[])
    args = parser.parse_args()
    from ingest import read_index, write_index

    rows = read_index()
    if args.command == "paths":
        label_paths(rows)
        return
    selection = read_json(WORK / "selection.json") if (WORK / "selection.json").exists() else {}
    for name, adapter in ADAPTERS.items():
        if args.only and name not in args.only:
            continue
        for domain, candidates, plan in adapter():
            chosen, offered = take(candidates, plan, random.Random(SEED))
            selection[domain] = {
                "offered": offered, "chosen": dict(Counter(c["tier"] for c in chosen)),
                "frames": len(chosen), "split": dict(Counter(c["split_hint"] for c in chosen)),
                "sources": len({c["source"] for c in chosen}),
                "annotated_pothole": sum(bool(c["annotated_pothole"]) for c in chosen)}
            print(domain, json.dumps(selection[domain]), flush=True)
            if args.command == "list":
                continue
            todo = [c for c in chosen
                    if not (FRAMES / c["source"] / (c["name"].rsplit(".", 1)[0].replace("/", "__").replace(" ", "_") + ".jpg")).exists()
                    or f"{c['source']}/{c['name'].rsplit('.', 1)[0].replace('/', '__').replace(' ', '_')}.jpg" not in rows]
            with ProcessPoolExecutor() as pool:
                for row in pool.map(prepare_one, todo, chunksize=16):
                    rows[row["path"]] = row
            write_index(rows)
            print(f"  prepared {len(todo)} now", flush=True)
    if args.command == "prepare" and not args.only:
        for row in owner_rows():
            rows[row["path"]] = row
        write_index(rows)
    write_json(WORK / "selection.json", selection)


if __name__ == "__main__":
    main()
