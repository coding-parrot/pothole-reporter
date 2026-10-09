#!/usr/bin/env python3
"""Look at every prepared picture for a box drawn into its pixels, and drop those from
training and validation.

    python scan_overlays.py

The owner's worry (9 Oct 2026): a model shown pictures with a red rectangle on them
learns rectangles, not potholes. The annotations here are numbers in files, but an
archive can still ship pictures somebody rendered boxes onto. A drawn box is four thin,
straight, axis-aligned strokes of one vivid colour, which a road scene almost never has:
this finds pictures with at least two thin horizontal and two thin vertical vivid runs.
It errs towards dropping (a billboard with a bright frame goes too).

Writes scan.json (counts by dataset and the flagged names), copies up to 60 flagged
pictures to debug/flagged/ to be looked at, and rewrites annotations/train.json and
val.json without them. Test pictures are reported, never removed.
"""
import json
import os
import shutil
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

OUT = Path(os.environ.get("DET_DATA", "/opt/ml/det/data"))
SIDE = 640


def longest_runs(mask):
    """The longest unbroken run of True in each row."""
    ones = mask.astype(np.int32)
    total = np.cumsum(ones, axis=1)
    reset = np.maximum.accumulate(np.where(ones == 0, total, 0), axis=1)
    return (total - reset).max(axis=1)


def thin_bands(lines):
    """Groups of neighbouring rows that hold a long run, at most 5 rows thick."""
    bands, start, previous = 0, None, None
    for index in np.flatnonzero(lines):
        if start is None:
            start = previous = index
        elif index == previous + 1:
            previous = index
        else:
            bands += (previous - start) < 5
            start = previous = index
    if start is not None:
        bands += (previous - start) < 5
    return bands


def drawn_box(path):
    image = Image.open(path).convert("RGB")
    image.thumbnail((SIDE, SIDE))
    pixels = np.asarray(image).astype(np.int16)
    vivid = (pixels.max(axis=2) >= 200) & (pixels.max(axis=2) - pixels.min(axis=2) >= 150)
    if vivid.sum() < 80:
        return False
    need = 24
    return bool(thin_bands(longest_runs(vivid) >= need) >= 2 and thin_bands(longest_runs(vivid.T) >= need) >= 2)


def check(entry):
    split, name = entry
    try:
        return split, name, drawn_box(OUT / f"{split}2017" / name)
    except Exception:
        return split, name, False


def main():
    index = [json.loads(line) for line in open(OUT / "index.jsonl")]
    dataset_of = {(row["split"], row["name"]): row["dataset"] for row in index}
    with ProcessPoolExecutor(max(2, (os.cpu_count() or 4) - 1)) as pool:
        results = list(pool.map(check, [(row["split"], row["name"]) for row in index], chunksize=64))
    flagged = [(split, name) for split, name, hit in results if hit]
    by_dataset = Counter(f"{dataset_of[key]}/{key[0]}" for key in flagged)
    (OUT / "debug" / "flagged").mkdir(parents=True, exist_ok=True)
    for split, name in flagged[:60]:
        shutil.copy(OUT / f"{split}2017" / name, OUT / "debug" / "flagged" / f"{split}__{name}")
    removed = Counter()
    for split in ("train", "val"):
        path = OUT / "annotations" / f"{split}.json"
        data = json.load(open(path))
        bad = {name for part, name in flagged if part == split}
        gone = {image["id"] for image in data["images"] if image["file_name"] in bad}
        data["images"] = [image for image in data["images"] if image["id"] not in gone]
        data["annotations"] = [item for item in data["annotations"] if item["image_id"] not in gone]
        json.dump(data, open(path, "w"))
        removed[split] = len(gone)
    summary = {"scanned": len(results), "flagged": len(flagged), "by_dataset_and_split": dict(by_dataset),
               "removed_from": dict(removed), "flagged_names": [f"{split}/{name}" for split, name in flagged]}
    json.dump(summary, open(OUT / "scan.json", "w"), indent=1)
    print(f"scanned {len(results)} pictures, {len(flagged)} look drawn on; removed {dict(removed)}; {dict(by_dataset)}")


if __name__ == "__main__":
    main()
