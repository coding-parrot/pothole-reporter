#!/usr/bin/env python3
"""Score the trained pothole detectors on pictures they never trained on.

    python evaluate.py --models pothole_tiny pothole_s

The question a drive frame asks is "is there a pothole in this picture", so the headline
numbers are per picture: a picture is called "pothole" when the detector's best box
scores at or over a cut-off. Two cut-offs are read from VALIDATION only and then judged
on test:
  sure     the lowest score at which at least 95% of the pictures called "pothole" in
           validation really have a human pothole box (the answer-alone bar)
  catch    the highest score that still calls 95% of validation's pothole pictures
Test slices: IRDD (Iraq, a dataset never trained on), RDD2022 India held-out blocks, the
owner's held-out drive frames (gpt-5-mini's verdict, no human boxes), RAD test videos
(Bengaluru; clean frames, and frames with RAD's broad anomaly mark shown apart). Box
accuracy (AP at IoU 0.5) is reported for the two slices with human boxes.

Writes report.json and report.md under $DET_REPORT and scores-<model>.jsonl (every
picture's best score) beside them.
"""
import argparse
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np
import torch

DATA = Path(os.environ.get("DET_DATA", "/opt/ml/det/data"))
RUNS = Path(os.environ.get("DET_RUNS", "/opt/ml/det/runs"))
REPORT = Path(os.environ.get("DET_REPORT", "/opt/ml/det/report"))
HERE = Path(__file__).resolve().parent


def load(name):
    sys.path.insert(0, str(HERE / "exps"))
    from yolox.exp import get_exp

    exp = get_exp(str(HERE / "exps" / f"{name}.py"), None)
    model = exp.get_model()
    checkpoint = torch.load(RUNS / name / "best_ckpt.pth", map_location="cpu", weights_only=False)
    model.load_state_dict(checkpoint["model"])
    parameters = sum(p.numel() for p in model.parameters())
    return exp, model.cuda().eval(), parameters, checkpoint.get("start_epoch"), checkpoint.get("best_ap")


def score_split(exp, model, split, batch=32):
    """Best box score and the boxes over 0.05 for every picture of a split."""
    from yolox.data import ValTransform
    from yolox.utils import postprocess

    images = json.load(open(DATA / "annotations" / f"{split}.json"))["images"]
    transform, size = ValTransform(legacy=False), exp.test_size
    results = {}
    for start in range(0, len(images), batch):
        chunk = images[start:start + batch]
        tensors, ratios = [], []
        for image in chunk:
            pixels = cv2.imread(str(DATA / f"{split}2017" / image["file_name"]))
            ratios.append(min(size[0] / pixels.shape[0], size[1] / pixels.shape[1]))
            tensors.append(torch.from_numpy(transform(pixels, None, size)[0]))
        with torch.no_grad(), torch.autocast("cuda", dtype=torch.float16):
            outputs = model(torch.stack(tensors).cuda().float())
        outputs = postprocess(outputs.float(), exp.num_classes, 0.05, exp.nmsthre)
        for image, ratio, output in zip(chunk, ratios, outputs):
            boxes = []
            if output is not None:
                output = output.cpu().numpy()
                for x1, y1, x2, y2, objectness, confidence, _ in output:
                    boxes.append([float(x1 / ratio), float(y1 / ratio), float(x2 / ratio), float(y2 / ratio),
                                  float(objectness * confidence)])
            results[image["file_name"]] = {"score": max((box[4] for box in boxes), default=0.0), "boxes": boxes}
    return results


def picture_table(split):
    data = json.load(open(DATA / "annotations" / f"{split}.json"))
    boxed = defaultdict(list)
    for item in data["annotations"]:
        boxed[item["image_id"]].append(item["bbox"])
    return [{"name": image["file_name"], "dataset": image["dataset"], "meta": image.get("meta") or {},
             "truth": boxed.get(image["id"], [])} for image in data["images"]]


def labelled(rows):
    """Pictures whose truth is known from human boxes: every dataset with box labels.
    RAD and the owner's frames have no pothole boxes drawn and are scored apart."""
    return [row for row in rows if not row["dataset"].startswith(("rad", "owner"))]


def counts(rows, scores, cut):
    called = [row for row in rows if scores[row["name"]]["score"] >= cut]
    real = [row for row in rows if row["truth"]]
    hit = [row for row in called if row["truth"]]
    return {"pictures": len(rows), "with_pothole": len(real), "called": len(called), "right": len(hit),
            "caught": round(len(hit) / len(real), 4) if real else None,
            "called_right": round(len(hit) / len(called), 4) if called else None}


def cut_offs(rows, scores):
    values = sorted({round(scores[row["name"]]["score"], 4) for row in rows})
    sure = catch = None
    for cut in values:  # lowest cut whose calls are at least 95% right
        result = counts(rows, scores, cut)
        if result["called"] >= 20 and result["called_right"] is not None and result["called_right"] >= 0.95:
            sure = cut
            break
    for cut in reversed(values):  # highest cut that still catches 95%
        result = counts(rows, scores, cut)
        if result["caught"] is not None and result["caught"] >= 0.95:
            catch = cut
            break
    return sure, catch


def average_precision(rows, scores):
    """Per picture: area under precision against recall as the cut-off falls."""
    ranked = sorted(rows, key=lambda row: -scores[row["name"]]["score"])
    total = sum(1 for row in rows if row["truth"])
    if not total:
        return None
    hits, area, previous = 0, 0.0, 0.0
    for position, row in enumerate(ranked, 1):
        if row["truth"]:
            hits += 1
            recall = hits / total
            area += (recall - previous) * hits / position
            previous = recall
    return round(area, 4)


def iou(a, b):
    x1, y1, x2, y2 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def box_ap50(rows, scores):
    """AP at IoU 0.5 over every predicted box against the human boxes."""
    truths = {row["name"]: [[x, y, x + w, y + h] for x, y, w, h in row["truth"]] for row in rows}
    total = sum(len(boxes) for boxes in truths.values())
    if not total:
        return None
    predictions = sorted(((box[4], row["name"], box[:4]) for row in rows for box in scores[row["name"]]["boxes"]),
                         key=lambda item: -item[0])
    used = {name: [False] * len(boxes) for name, boxes in truths.items()}
    hits, area, previous = 0, 0.0, 0.0
    for position, (_, name, box) in enumerate(predictions, 1):
        best, best_index = 0.0, -1
        for index, truth in enumerate(truths[name]):
            overlap = iou(box, truth)
            if overlap > best and not used[name][index]:
                best, best_index = overlap, index
        if best >= 0.5:
            used[name][best_index] = True
            hits += 1
            recall = hits / total
            area += (recall - previous) * hits / position
            previous = recall
    return round(area, 4)


def rate(rows, scores, cut):
    called = sum(1 for row in rows if scores[row["name"]]["score"] >= cut)
    return {"pictures": len(rows), "called": called, "share": round(called / len(rows), 4) if rows else None}


def cpu_milliseconds(exp, model):
    """Median forward time at 640 px on this machine's processor, 2 threads. A guide
    only: the Lambda is a different processor and also decodes and resizes the frame."""
    cpu = model.cpu().float()
    torch.set_num_threads(2)
    frame = torch.zeros(1, 3, *exp.test_size)
    times = []
    with torch.no_grad():
        for _ in range(12):
            started = time.perf_counter()
            cpu(frame)
            times.append((time.perf_counter() - started) * 1000)
    model.cuda()
    return round(sorted(times[2:])[len(times[2:]) // 2], 1)


def evaluate(name):
    exp, model, parameters, epoch, best_ap = load(name)
    scores = {split: score_split(exp, model, split) for split in ("val", "test")}
    with open(REPORT / f"scores-{name}.jsonl", "w") as output:
        for split, table in scores.items():
            for file_name, value in table.items():
                output.write(json.dumps({"split": split, "name": file_name, "score": round(value["score"], 5),
                                         "boxes": [[round(v, 1) for v in box[:4]] + [round(box[4], 4)]
                                                   for box in value["boxes"][:20]]}) + "\n")
    val_rows, test_rows = picture_table("val"), picture_table("test")
    sure, catch = cut_offs(labelled(val_rows) + [row for row in val_rows if row["dataset"].startswith(("rad", "owner"))],
                           scores["val"])
    report = {"model": name, "parameters": parameters, "best_epoch": epoch, "validation_box_ap_50_95": best_ap,
              "cut_offs_from_validation": {"sure": sure, "catch": catch},
              "cpu_ms_640_two_threads": cpu_milliseconds(exp, model), "slices": {}}
    slices = {"validation, all": (val_rows, "val"),
              "IRDD (Iraq, never trained on)": ([row for row in test_rows if row["dataset"] == "irdd"], "test"),
              "RDD2022 India, held-out blocks": ([row for row in test_rows if row["dataset"] == "rdd2022_india"], "test")}
    for title, (rows, split) in slices.items():
        entry = {"picture_average_precision": average_precision(rows, scores[split]),
                 "box_ap50": box_ap50(rows, scores[split])}
        for label, cut in (("sure", sure), ("catch", catch), ("at_0.5", 0.5), ("at_0.3", 0.3)):
            if cut is not None:
                entry[label] = counts(rows, scores[split], cut)
        report["slices"][title] = entry
    extras = {}
    owner_drive = [row for row in test_rows if row["dataset"] == "owner_drive"]
    groups = {"owner drive, teacher said undamaged": [r for r in owner_drive if r["meta"].get("teacher_assessment") == "undamaged"],
              "owner drive, teacher said pothole cavity": [r for r in owner_drive if r["meta"].get("teacher_damage_type") == "pothole_cavity"],
              "owner drive, teacher said other damage": [r for r in owner_drive if r["meta"].get("teacher_assessment") == "damaged"
                                                         and r["meta"].get("teacher_damage_type") != "pothole_cavity"],
              "owner labelled images": [r for r in test_rows if r["dataset"].startswith("owner") and r["dataset"] != "owner_drive"],
              "RAD test, clean frames": [r for r in test_rows if r["dataset"] == "rad" and not r["meta"].get("rad_anomaly")],
              "RAD test, speed breakers": [r for r in test_rows if r["dataset"] == "rad" and r["meta"].get("speed_breaker")
                                           and not r["meta"].get("rad_anomaly")],
              "RAD test, frames with the broad anomaly mark": [r for r in test_rows if r["dataset"] == "rad" and r["meta"].get("rad_anomaly")]}
    for title, rows in groups.items():
        extras[title] = {label: rate(rows, scores["test"], cut)
                         for label, cut in (("sure", sure), ("catch", catch), ("at_0.5", 0.5), ("at_0.3", 0.3)) if cut is not None}
    extras["owner labelled images, each"] = [
        {"name": row["name"], "label": row["meta"].get("owner_label") or row["meta"].get("tier"),
         "score": round(scores["test"][row["name"]]["score"], 3)} for row in groups["owner labelled images"]]
    report["no_human_boxes"] = extras
    return report


def markdown(reports):
    lines = ["# Pothole detectors: scorecard", "",
             "Per picture. `caught` is the share of pictures with a human pothole box that the detector called;",
             "`called_right` is the share of its calls that have one. Cut-offs come from validation only.", ""]
    for report in reports:
        lines += [f"## {report['model']}", "",
                  f"{report['parameters']:,} parameters. Best epoch {report['best_epoch']}. "
                  f"Cut-offs: sure {report['cut_offs_from_validation']['sure']}, catch {report['cut_offs_from_validation']['catch']}. "
                  f"Forward pass on this instance's processor, 2 threads, 640 px: {report['cpu_ms_640_two_threads']} ms.", "",
                  "| Slice | Pictures | With pothole | Cut-off | Caught | Called right | Box AP50 |", "|---|---|---|---|---|---|---|"]
        for title, entry in report["slices"].items():
            for label in ("sure", "catch", "at_0.5", "at_0.3"):
                if label in entry:
                    row = entry[label]
                    lines.append(f"| {title} | {row['pictures']} | {row['with_pothole']} | {label} | "
                                 f"{row['caught']} ({row['right']}/{row['with_pothole']}) | "
                                 f"{row['called_right']} ({row['right']}/{row['called']}) | {entry['box_ap50']} |")
        lines += ["", "| No human boxes | Cut-off | Called pothole |", "|---|---|---|"]
        for title, entry in report["no_human_boxes"].items():
            if isinstance(entry, dict):
                for label, row in entry.items():
                    lines.append(f"| {title} | {label} | {row['called']} of {row['pictures']} ({row['share']}) |")
        lines += ["", "Owner-labelled images: " + json.dumps(report["no_human_boxes"]["owner labelled images, each"]), ""]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs="+", default=["pothole_tiny", "pothole_s"])
    options = parser.parse_args()
    REPORT.mkdir(parents=True, exist_ok=True)
    reports = [evaluate(name) for name in options.models if (RUNS / name / "best_ckpt.pth").exists()]
    json.dump(reports, open(REPORT / "report.json", "w"), indent=1)
    open(REPORT / "report.md", "w").write(markdown(reports))
    for report in reports:
        india = report["slices"]["RDD2022 India, held-out blocks"]
        print(report["model"], report["parameters"], "cut-offs", report["cut_offs_from_validation"],
              "India sure", india.get("sure"), "India catch", india.get("catch"))


if __name__ == "__main__":
    main()
