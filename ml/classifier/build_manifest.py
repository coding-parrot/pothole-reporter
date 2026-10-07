#!/usr/bin/env python3
"""Join frames, teacher labels and human labels into work/manifest.jsonl with a split.

The split is by SOURCE and was fixed before any label was read:

- Desktop drive videos are three continuous recordings cut into one-minute segments
  (0002 to 0016, 0020 to 0027, 0031 to 0037). The last minutes of each recording are
  held out whole: test and validation never share a minute with training.
- The two Downloads clips carry the owner-confirmed pothole events, so they are test
  only and no frame of them is trained on.
- RDD2022 India has no sequence ids; its file numbers follow capture order. Blocks of
  500 consecutive numbers are kept whole: blocks 3, 8, 13, 18 validate and blocks
  4, 9, 14, 19 test.
"""
import json
import re
from collections import Counter

from common import FRAMES, ROOT, TEACHER, WORK, is_damaged, read_json
from teacher_label import CONTRACT_KEY

VIDEO_TEST = {"0014", "0015", "0016", "0026", "0027", "0036", "0037"}
VIDEO_VALIDATION = {"0011", "0012", "0013", "0024", "0025", "0034", "0035"}
RDD_BLOCK = 500
# Owner-confirmed pothole events (eval/exhaustive_video_visual_labels.json).
OWNER_EVENTS = {
    event["source_id"]: tuple(event["source_interval_seconds"])
    for event in read_json(ROOT / "eval" / "exhaustive_video_visual_labels.json")["events"]
    if event["label"] == "pothole" and event["label_provenance"] == "owner_confirmed"
}


def split_of(row):
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


def rdd_classes(row):
    xml = (WORK / "rdd2022_india" / "train" / "annotations" / "xmls"
           / f"India_{row['rdd_index']:06d}.xml")
    return sorted(set(re.findall(r"<name>([^<]+)</name>", xml.read_text())))


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
            missing[row["source"]] += 1
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
            row["rdd_classes"] = rdd_classes(row)
        interval = OWNER_EVENTS.get(row["source"])
        if interval:
            row["owner_event_pothole"] = interval[0] <= row["seconds"] <= interval[1]
        rows.append(row)
    with open(WORK / "manifest.jsonl", "w") as output:
        for row in rows:
            output.write(json.dumps(row, sort_keys=True) + "\n")

    print(f"{len(rows)} labelled frames; unlabelled and skipped: {dict(missing) or 'none'}")
    for domain in ("drive_video", "rdd2022_india"):
        print(f"\n{domain}")
        for split in ("train", "validation", "test"):
            chosen = [row for row in rows if row["domain"] == domain and row["split"] == split]
            damaged = sum(row["damaged"] for row in chosen)
            rejected = sum(row["teacher"]["image_quality"] == "rejected" for row in chosen)
            types = Counter(row["teacher"]["damage_type"] for row in chosen if row["damaged"])
            sources = len({row["source"] if domain == "drive_video"
                           else row["rdd_index"] // RDD_BLOCK for row in chosen})
            twice = [row for row in chosen if "damaged_again" in row]
            first = [row for row in twice if row["damaged"]]
            repeatable = (f"; teacher repeats {sum(row['damaged_again'] for row in first)}/{len(first)} "
                          f"of its damaged verdicts" if first else "")
            print(f"  {split:10s} {len(chosen):5d} frames from {sources:2d} sources: "
                  f"{damaged:4d} damaged ({100 * damaged / max(1, len(chosen)):.1f}%), "
                  f"{len(chosen) - damaged:5d} undamaged, of which {rejected} rejected for "
                  f"quality; types {dict(types.most_common())}{repeatable}")


if __name__ == "__main__":
    main()
