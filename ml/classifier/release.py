#!/usr/bin/env python3
"""Turn one trained model into the served artifact and its honest scorecard.

    python release.py --encoder efficientnet_b0 --size 448 --head hidden256 \
        --version road-screen-v1 --target-recall 0.99

1. Exports the ONNX (export_onnx.py proves it equals torch on the test split).
2. Scores validation, test and the owner-labelled images through the SERVING path
   (lambda/scorer.mjs: sharp + ONNX Runtime), not through PIL.
3. Sets the threshold on validation only, at the target recall of teacher-damaged frames.
4. Reports test metrics at that threshold, the 99/98/95 curve, human-labelled checks and
   how often the teacher agrees with itself, then writes lambda/model/model.json.
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from common import FRAMES, HERE, ROOT, TEACHER, WORK, is_damaged, prepare_drive_jpeg, read_json, sha256_hex, write_json
from metrics import at_threshold, report, threshold_for_recall
from teacher_label import CONTRACT_KEY

OWNER_IMAGE_ROOTS = (ROOT / "eval" / "images",
                     Path.home() / "Downloads" / "pothole-reporter" / "eval" / "images",
                     Path.home() / "Downloads" / "pothole-full-audit-2026-09-25" / "recovered-images")
POSITIVE_LABELS = {"pothole", "pothole_cavity", "failed_patch", "surface_breakup",
                   "rut_or_depression", "other_road_damage", "damaged"}
NEGATIVE_LABELS = {"not_pothole", "undamaged"}


def node_scores(paths, root):
    done = subprocess.run(["node", str(HERE / "lambda" / "score-frames.mjs"),
                           str(HERE / "lambda" / "model"), str(root)],
                          input="\n".join(paths) + "\n", capture_output=True, text=True)
    if done.returncode:
        sys.exit(done.stderr[-2000:])
    rows = [json.loads(line) for line in done.stdout.splitlines()]
    assert [row["path"] for row in rows] == list(paths)
    return np.array([row["score"] for row in rows]), rows


def owner_images():
    """The labelled eval images, prepared as drive frames. Only labelled_by == owner is
    ground truth; the assistant-labelled rest is reported apart."""
    target = WORK / "owner"
    target.mkdir(exist_ok=True)
    items = []
    for entry in read_json(ROOT / "eval" / "labels.json")["images"]:
        relative = (entry.get("frames") or [entry["path"]])[int(entry.get("primary_index", 0))]
        source = next((root / relative for root in OWNER_IMAGE_ROOTS if (root / relative).exists()), None)
        if source is None:
            continue
        name = relative.replace("/", "__")
        (target / name).write_bytes(prepare_drive_jpeg(Image.open(source)))
        items.append({"path": name, "label": entry["label"],
                      "owner_labelled": entry.get("labelled_by") == "owner"})
    return items, target


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--encoder", required=True)
    parser.add_argument("--size", type=int, required=True)
    parser.add_argument("--head", default="hidden256")
    parser.add_argument("--weights")
    parser.add_argument("--version", required=True)
    parser.add_argument("--target-recall", type=float, default=0.99,
                        help="validation recall the threshold is set for")
    args = parser.parse_args()

    name = f"{args.encoder}_{args.size}_{args.head}"
    command = [sys.executable, str(HERE / "export_onnx.py"), "--encoder", args.encoder,
               "--size", str(args.size), "--head", args.head]
    if args.weights:
        command += ["--weights", args.weights]
    subprocess.run(command, check=True)
    parity = read_json(WORK / "onnx" / f"{name}.parity.json")
    model_dir = HERE / "lambda" / "model"
    model_dir.mkdir(exist_ok=True)
    shutil.copyfile(WORK / "onnx" / f"{name}.onnx", model_dir / "model.onnx")
    meta = {"model_version": args.version, "sha256": parity["onnx_sha256"],
            "input_size": args.size, "threshold": 0.5}
    write_json(model_dir / "model.json", meta)

    rows = [json.loads(line) for line in (WORK / "manifest.jsonl").read_text().splitlines()]
    rows = [row for row in rows if row["split"] in ("validation", "test")]
    scores, timing = node_scores([row["path"] for row in rows], FRAMES)
    split = np.array([row["split"] for row in rows])
    domain = np.array([row["domain"] for row in rows])
    labels = np.array([row["damaged"] for row in rows])
    validation, test = split == "validation", split == "test"

    threshold = threshold_for_recall(scores[validation], labels[validation], args.target_recall)
    out = {
        "model_version": args.version, "encoder": args.encoder, "input_size": args.size,
        "head": args.head, "fine_tuned": bool(args.weights), "onnx": parity,
        "threshold": threshold, "threshold_rule":
            f"highest score that still flags {args.target_recall:.0%} of teacher-damaged "
            "validation frames, scored through the serving path",
        "scoring_path": "lambda/scorer.mjs (sharp letterbox, ONNX Runtime)",
        "local_scoring_ms": {
            "decode_p50": float(np.median([row["decode_ms"] for row in timing])),
            "infer_p50": float(np.median([row["infer_ms"] for row in timing]))},
        "at_threshold": {}, "curve": {},
    }
    for label, chosen in (("all", np.ones(len(rows), bool)), ("drive_video", domain == "drive_video"),
                          ("rdd2022_india", domain == "rdd2022_india")):
        out["at_threshold"][label] = {
            "validation": at_threshold(scores[validation & chosen], labels[validation & chosen], threshold),
            "test": at_threshold(scores[test & chosen], labels[test & chosen], threshold)}
        out["curve"][label] = report((scores[validation & chosen], labels[validation & chosen]),
                                     (scores[test & chosen], labels[test & chosen]))

    # Serving path against the training path (PIL letterbox, torch) on the same frames.
    saved = WORK / "heads" / f"{name}_scores.npy"
    if saved.exists() and not args.weights:
        every = [json.loads(line)["path"] for line in (WORK / "manifest.jsonl").read_text().splitlines()]
        position = {path: index for index, path in enumerate(every)}
        trained = np.load(saved)[[position[row["path"]] for row in rows]]
        out["serving_vs_training_path"] = {
            "frames": len(rows),
            "max_abs_score_difference": float(np.abs(trained - scores).max()),
            "mean_abs_score_difference": float(np.abs(trained - scores).mean()),
            "decisions_that_differ_at_threshold": int(((trained >= threshold) != (scores >= threshold)).sum())}

    # Human labels. Owner-confirmed pothole events in the two Downloads clips.
    events = {}
    for row, score in zip(rows, scores):
        if row.get("owner_event_pothole"):
            events.setdefault(row["source"], []).append(float(score))
    out["owner_video_events"] = {
        source: {"frames": len(values), "frames_flagged": int(sum(v >= threshold for v in values)),
                 "event_flagged": any(v >= threshold for v in values),
                 "scores": [round(v, 4) for v in values]}
        for source, values in events.items()}
    items, owner_root = owner_images()
    owner_scores, _ = node_scores([item["path"] for item in items], owner_root)
    for item, score in zip(items, owner_scores):
        item["score"] = round(float(score), 4)
        item["flagged"] = bool(score >= threshold)
    verified = [item for item in items if item["owner_labelled"]]
    out["owner_images"] = {
        "potholes": sum(item["label"] in POSITIVE_LABELS for item in verified),
        "potholes_flagged": sum(item["flagged"] for item in verified if item["label"] in POSITIVE_LABELS),
        "not_potholes": sum(item["label"] in NEGATIVE_LABELS for item in verified),
        "not_potholes_cleared": sum(not item["flagged"] for item in verified if item["label"] in NEGATIVE_LABELS),
        "items": items}
    # RDD2022's human boxes on the held-out blocks: D40 is a pothole.
    rdd = [(row, score) for row, score in zip(rows, scores)
           if row["split"] == "test" and row["domain"] == "rdd2022_india"]
    with_pothole = [score for row, score in rdd if "D40" in row["rdd_classes"]]
    unannotated = [score for row, score in rdd if not row["rdd_classes"]]
    out["rdd_human_boxes_test"] = {
        "images_with_a_D40_pothole_box": len(with_pothole),
        "flagged": int(sum(score >= threshold for score in with_pothole)),
        "images_with_no_damage_box": len(unannotated),
        "cleared": int(sum(score < threshold for score in unannotated))}
    # How often the teacher repeats its own verdict on the test frames (trial 1 vs 0).
    again = both = first = 0
    for row in rows:
        repeat = TEACHER / CONTRACT_KEY / f"{row['sha256']}.t1.json"
        if row["split"] != "test" or not repeat.exists():
            continue
        again += 1
        if row["damaged"]:
            first += 1
            both += is_damaged(json.loads(repeat.read_text())["verdict"])
    out["teacher_repeat_on_test"] = {
        "frames_labelled_twice": again, "damaged_first_time": first,
        "damaged_both_times": both, "teacher_self_recall": both / first if first else None}

    meta.update({"threshold": threshold, "encoder": args.encoder, "head": args.head,
                 "target_validation_recall": args.target_recall})
    write_json(model_dir / "model.json", meta)
    write_json(WORK / "release-report.json", out)
    for label in ("all", "drive_video", "rdd2022_india"):
        point = out["at_threshold"][label]["test"]
        print(f"{label:14s} test: recall {point['recall']:.4f} ({point['caught']}/{point['damaged']}), "
              f"cleared {point['cleared_share']:.4f} ({point['cleared']}/{point['undamaged']})")
    print(json.dumps({k: out[k] for k in ("threshold", "owner_video_events", "rdd_human_boxes_test",
                                          "teacher_repeat_on_test")}, indent=1))
    print("owner images:", {k: v for k, v in out["owner_images"].items() if k != "items"})


if __name__ == "__main__":
    main()
