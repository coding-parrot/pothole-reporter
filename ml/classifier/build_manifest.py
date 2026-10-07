#!/usr/bin/env python3
"""Join frames, teacher labels and dataset annotations into work/manifest.jsonl.

Splits are by SOURCE and were fixed before any v2 label was read. Validation and test
never share a source (a video, a country, a dataset) with training.

v1 sources, unchanged so v1 and v2 are judged on the same test frames:
- Desktop drive videos are three continuous recordings cut into one-minute segments. The
  last minutes of each recording are held out whole (segment numbers below).
- The two Downloads clips carry the owner-confirmed pothole events: test only.
- RDD2022 India has no sequence ids; its file numbers follow capture order. Blocks of
  500 consecutive numbers are kept whole: blocks 3, 8, 13, 18 validate, 4, 9, 14, 19 test.

v2 sources carry their split from datasets.py (SPLITS there):
- train: RDD2022 Japan, Norway, China (motorbike and drone), RAD training videos, and
  the other public sets.
- validation: RDD2022 Czech (a whole country), one whole public dataset, RAD validation
  videos.
- test: one whole public dataset held out entirely, RAD test videos.

The same picture can sit in two public datasets. A training frame whose 64-bit
difference hash is within 2 bits of a held-out frame from another dataset is dropped.
"""
import json
import re
from collections import Counter
from concurrent.futures import ProcessPoolExecutor

import numpy as np

from common import FRAMES, ROOT, TEACHER, WORK, is_damaged, read_json, write_json
from teacher_label import CONTRACT_KEY

VIDEO_TEST = {"0014", "0015", "0016", "0026", "0027", "0036", "0037"}
VIDEO_VALIDATION = {"0011", "0012", "0013", "0024", "0025", "0034", "0035"}
RDD_BLOCK = 500
DUPLICATE_BITS = 2
# Owner-confirmed pothole events (eval/exhaustive_video_visual_labels.json).
OWNER_EVENTS = {
    event["source_id"]: tuple(event["source_interval_seconds"])
    for event in read_json(ROOT / "eval" / "exhaustive_video_visual_labels.json")["events"]
    if event["label"] == "pothole" and event["label_provenance"] == "owner_confirmed"
}


def split_of(row):
    if "split_hint" in row:  # a v2 public source; datasets.py fixed its split
        return row["split_hint"]
    if row["domain"] == "drive_video":
        if row["source"].startswith("downloads-"):
            return "test"
        number = row["source"].rsplit("-", 1)[1]
        return ("test" if number in VIDEO_TEST
                else "validation" if number in VIDEO_VALIDATION else "train")
    if row["source"] != "rdd2022-india-train":
        return None  # the unannotated RDD test images are not used
    block = (row["rdd_index"] // RDD_BLOCK) % 5
    return "validation" if block == 3 else "test" if block == 4 else "train"


def india_classes(row):
    xml = (WORK / "rdd2022_india" / "train" / "annotations" / "xmls"
           / f"India_{row['rdd_index']:06d}.xml")
    return sorted(set(re.findall(r"<name>([^<]+)</name>", xml.read_text())))


def frame_dhash(path):
    from PIL import Image

    from ingest import dhash
    return dhash(Image.open(FRAMES / path))


def with_hashes(rows):
    """v1 frames were indexed without a difference hash; compute and remember them."""
    cache_path = WORK / "dhash-v1-frames.json"
    cache = read_json(cache_path) if cache_path.exists() else {}
    missing = [row["path"] for row in rows if "dhash" not in row and row["path"] not in cache]
    if missing:
        with ProcessPoolExecutor() as pool:
            for path, value in zip(missing, pool.map(frame_dhash, missing, chunksize=256)):
                cache[path] = value
        write_json(cache_path, cache)
    for row in rows:
        row.setdefault("dhash", cache.get(row["path"]))


def drop_twins(rows):
    """Remove training (then validation) frames that are near-copies of a frame held out
    in another dataset. Returns the kept rows and what was dropped."""
    family = np.array([row.get("dataset", row["domain"]) for row in rows])
    split = np.array([row["split"] for row in rows])
    hashes = np.array([int(row["dhash"], 16) for row in rows], dtype=np.uint64)
    drop = np.zeros(len(rows), dtype=bool)
    dropped = Counter()
    for held, lower in (("test", ("train", "validation")), ("validation", ("train",))):
        held_index = np.flatnonzero(split == held)
        for name in lower:
            candidates = np.flatnonzero((split == name) & ~drop)
            for start in range(0, len(candidates), 2048):
                chunk = candidates[start:start + 2048]
                distance = np.bitwise_count(hashes[chunk, None] ^ hashes[None, held_index])
                other = family[chunk, None] != family[None, held_index]
                twin = ((distance <= DUPLICATE_BITS) & other).any(1)
                for index in chunk[twin]:
                    drop[index] = True
                    dropped[f"{family[index]} {name} (twin in {held})"] += 1
    return [row for row, gone in zip(rows, drop) if not gone], dict(dropped)


def main():
    rows = []
    missing = Counter()
    for line in (FRAMES / "index.jsonl").read_text().splitlines():
        row = json.loads(line)
        row["split"] = split_of(row)
        if row["split"] is None:
            continue
        cached = TEACHER / CONTRACT_KEY / f"{row['sha256']}.json"
        if not cached.exists():
            missing[row["source"] if "dataset" not in row else row["dataset"]] += 1
            continue
        verdict = json.loads(cached.read_text())["verdict"]
        row["teacher"] = {key: verdict[key] for key in
                          ("image_quality", "assessment", "damage_type", "size")}
        row["damaged"] = is_damaged(verdict)
        # A second, independent teacher answer where one was bought (--trial 1). The
        # label stays the first answer, as in production; the second one measures how
        # repeatable the teacher is and softens the training target.
        repeat = TEACHER / CONTRACT_KEY / f"{row['sha256']}.t1.json"
        if repeat.exists():
            row["damaged_again"] = is_damaged(json.loads(repeat.read_text())["verdict"])
        if row["domain"] == "rdd2022_india":
            row["dataset"] = "rdd2022"
            row["rdd_classes"] = india_classes(row)
            row["annotated_pothole"] = "D40" in row["rdd_classes"]
        elif row["domain"] == "drive_video":
            row["dataset"] = "owner_drive"
        interval = OWNER_EVENTS.get(row["source"])
        if interval:
            row["owner_event_pothole"] = interval[0] <= row["seconds"] <= interval[1]
        rows.append(row)
    with_hashes(rows)
    rows, dropped = drop_twins(rows)
    with open(WORK / "manifest.jsonl", "w") as output:
        for row in rows:
            output.write(json.dumps(row, sort_keys=True) + "\n")

    summary = {"frames": len(rows), "unlabelled_and_skipped": dict(missing),
               "dropped_as_twins_of_held_out_frames": dropped, "by_domain": {}}
    print(f"{len(rows)} labelled frames; unlabelled and skipped: {dict(missing) or 'none'}; "
          f"dropped as twins of held-out frames: {dropped or 'none'}")
    for domain in sorted({row["domain"] for row in rows}):
        print(f"\n{domain}")
        summary["by_domain"][domain] = {}
        for split in ("train", "validation", "test"):
            chosen = [row for row in rows if row["domain"] == domain and row["split"] == split]
            if not chosen:
                continue
            damaged = sum(row["damaged"] for row in chosen)
            rejected = sum(row["teacher"]["image_quality"] == "rejected" for row in chosen)
            types = Counter(row["teacher"]["damage_type"] for row in chosen if row["damaged"])
            twice = [row for row in chosen if "damaged_again" in row]
            first = [row for row in twice if row["damaged"]]
            again = sum(row["damaged_again"] for row in first)
            potholes = [row for row in chosen if row.get("annotated_pothole")]
            summary["by_domain"][domain][split] = {
                "frames": len(chosen), "sources": len({row["source"] for row in chosen}),
                "damaged": damaged, "rejected_for_quality": rejected,
                "damage_types": dict(types.most_common()),
                "labelled_twice": len(twice), "damaged_first_time_of_those": len(first),
                "damaged_both_times": again,
                "annotated_pothole": len(potholes),
                "annotated_pothole_teacher_damaged": sum(row["damaged"] for row in potholes)}
            print(f"  {split:10s} {len(chosen):5d} frames, {damaged:4d} damaged "
                  f"({100 * damaged / len(chosen):.1f}%), {rejected} rejected for quality; "
                  f"annotated pothole {len(potholes)}, of which teacher-damaged "
                  f"{sum(row['damaged'] for row in potholes)}; teacher repeats {again}/{len(first)}")
    write_json(WORK / "manifest-summary.json", summary)


if __name__ == "__main__":
    main()
