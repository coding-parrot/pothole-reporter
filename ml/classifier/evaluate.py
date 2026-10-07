#!/usr/bin/env python3
"""Judge every trained variant on every held-out slice, against v1 on the same frames.

    python evaluate.py --baseline mobilenetv3_l_448_v1 --variants <stem> <stem> ... --out work/report

Each variant is a work/scores/<stem>.npz (frame paths and scores from the training path).
Thresholds are set on the validation split only, at 99%, 98% and 95% recall of the
frames the teacher called damaged, and judged on test. Nothing here picks a threshold on
test; the "best possible" column is there to show how far the validation one is from it.

Recall is reported three ways:
  1. against every frame the teacher called damaged (its first answer, as in production);
  2. against the stable ones, where its second answer agreed;
  3. against frames a dataset's own annotation marks as a pothole.

The rule for "v2 beats v1", fixed before any v2 test number was read: both models get
their threshold from the same validation split at 98% recall. On a slice v2 wins when
its recall is not lower AND its cleared share is not lower (one of them higher). Where
the two disagree in direction the slice is "mixed", and the report adds the matched
comparison (v1's threshold moved until it has v2's recall on that slice) for the reader;
a mixed slice does not count as a win.
"""
import argparse
import json
from pathlib import Path

import numpy as np

from common import FRAMES, WORK, write_json
from metrics import at_threshold, auc, threshold_for_recall

TARGETS = (0.99, 0.98, 0.95)
# v1 as deployed: 0.0470 on the temperature-softened score (T = 2.462), which is this
# raw probability.
V1_DEPLOYED_RAW = float(1 / (1 + np.exp(-np.log(0.04699918670128432 / (1 - 0.04699918670128432)) * 2.462)))
POSITIVE_OWNER_LABELS = {"pothole", "pothole_cavity", "failed_patch", "surface_breakup",
                         "rut_or_depression", "other_road_damage", "damaged"}
NEGATIVE_OWNER_LABELS = {"not_pothole", "undamaged"}


def load_scores(stem, paths):
    saved = np.load(WORK / "scores" / f"{stem}.npz", allow_pickle=False)
    position = {path: index for index, path in enumerate(saved["paths"])}
    missing = [path for path in paths if path not in position]
    if missing:
        raise SystemExit(f"{stem}: {len(missing)} frames have no score")
    return saved["scores"][[position[path] for path in paths]].astype(np.float64)


def slices_of(rows):
    """Named boolean masks over the manifest rows. Every one is test-split only."""
    test = np.array([row["split"] == "test" for row in rows])
    domain = np.array([row["domain"] for row in rows])
    old = np.isin(domain, ("drive_video", "rdd2022_india"))
    out = {
        "old_test_all": test & old,
        "old_test_drive": test & (domain == "drive_video"),
        "old_test_rdd_india": test & (domain == "rdd2022_india"),
    }
    for name in sorted(set(domain[test & ~old])):
        out[f"new_test_{name}"] = test & (domain == name)
    out["all_test"] = test
    return out


def point(scores, labels, mask, threshold):
    result = at_threshold(scores[mask], labels[mask], threshold)
    own = threshold_for_recall(scores[mask], labels[mask], 0.98)
    result["auc"] = auc(scores[mask], labels[mask])
    result["cleared_share_at_own_98_recall"] = at_threshold(scores[mask], labels[mask], own)["cleared_share"]
    return result


def judge(scores, rows, owner_rows, owner_scores, thresholds):
    labels = np.array([row["damaged"] for row in rows])
    twice = np.array(["damaged_again" in row for row in rows])
    stable = twice & np.array([row.get("damaged_again") == row["damaged"] for row in rows])
    pothole = np.array([bool(row.get("annotated_pothole")) for row in rows])
    domain = np.array([row["domain"] for row in rows])
    validation = np.array([row["split"] == "validation" for row in rows])
    masks = slices_of(rows)
    out = {}
    for label, threshold in thresholds.items():
        entry = {"threshold": threshold, "slices": {}, "stable_slices": {}, "validation_by_domain": {},
                 "annotated_pothole": {}, "unstable_damaged": {}}
        for name, mask in masks.items():
            entry["slices"][name] = point(scores, labels, mask, threshold)
            entry["stable_slices"][name] = point(scores, labels, mask & stable, threshold)
            # Frames the teacher called damaged once and undamaged on the repeat.
            coin = mask & twice & ~stable & labels
            entry["unstable_damaged"][name] = {
                "frames": int(coin.sum()), "flagged": int((scores[coin] >= threshold).sum())}
            marked = mask & pothole
            if marked.any():
                flagged = scores[marked] >= threshold
                both = marked & labels
                entry["annotated_pothole"][name] = {
                    "frames": int(marked.sum()), "flagged": int(flagged.sum()),
                    "recall": float(flagged.mean()),
                    "also_teacher_damaged": int(both.sum()),
                    "flagged_of_those": int((scores[both] >= threshold).sum())}
        for name in sorted(set(domain[validation])):
            chosen = validation & (domain == name)
            entry["validation_by_domain"][name] = at_threshold(scores[chosen], labels[chosen], threshold)
        # Owner labels: the final check, never trained on.
        events = {}
        for row, value in zip(rows, scores):
            if row.get("owner_event_pothole"):
                events.setdefault(row["source"], []).append(float(value))
        entry["owner_video_events"] = {
            "events": len(events),
            "events_flagged": sum(any(v >= threshold for v in values) for values in events.values()),
            "frames": sum(len(values) for values in events.values()),
            "frames_flagged": sum(sum(v >= threshold for v in values) for values in events.values())}
        verified = [(row, value) for row, value in zip(owner_rows, owner_scores) if row["owner_labelled"]]
        entry["owner_images"] = {
            "potholes": sum(row["owner_label"] in POSITIVE_OWNER_LABELS for row, _ in verified),
            "potholes_flagged": sum(value >= threshold for row, value in verified
                                    if row["owner_label"] in POSITIVE_OWNER_LABELS),
            "not_potholes": sum(row["owner_label"] in NEGATIVE_OWNER_LABELS for row, _ in verified),
            "not_potholes_cleared": sum(value < threshold for row, value in verified
                                        if row["owner_label"] in NEGATIVE_OWNER_LABELS),
            "scores": {row["path"]: round(float(value), 5) for row, value in verified}}
        out[label] = entry
    return out


def teacher_ceiling(rows):
    """How often the teacher repeats itself, per slice. A screen that WAS the teacher
    (its second answer used as the screen's verdict) would reach exactly this recall."""
    labels = np.array([row["damaged"] for row in rows])
    twice = np.array(["damaged_again" in row for row in rows])
    again = np.array([bool(row.get("damaged_again")) for row in rows])
    out = {}
    for name, mask in slices_of(rows).items():
        chosen = mask & twice
        first = chosen & labels
        clean = chosen & ~labels
        out[name] = {
            "frames_labelled_twice": int(chosen.sum()),
            "damaged_first_answer": int(first.sum()),
            "damaged_both_answers": int((first & again).sum()),
            "teacher_as_screen_recall": float((first & again).sum() / first.sum()) if first.any() else None,
            "undamaged_first_answer": int(clean.sum()),
            "undamaged_both_answers": int((clean & ~again).sum()),
            "teacher_as_screen_cleared_share": float((clean & ~again).sum() / clean.sum()) if clean.any() else None}
    return out


def compare(candidate, baseline, scores, baseline_scores, rows, baseline_threshold):
    """Slice by slice at each model's validation-98% threshold."""
    labels = np.array([row["damaged"] for row in rows])
    verdicts = {}

    def verdict(recall_new, recall_old, cleared_new, cleared_old):
        pairs = [(recall_new, recall_old)] + ([(cleared_new, cleared_old)] if cleared_new is not None else [])
        if all(new >= old for new, old in pairs) and any(new > old for new, old in pairs):
            return "v2 wins"
        if all(new == old for new, old in pairs):
            return "tie"
        if all(new <= old for new, old in pairs):
            return "v1 wins"
        return "mixed"

    for group in ("slices", "stable_slices"):
        for name, new in candidate[group].items():
            old = baseline[group][name]
            if not new["damaged"]:
                continue
            entry = {"v2_recall": new["recall"], "v1_recall": old["recall"],
                     "v2_cleared": new["cleared_share"], "v1_cleared": old["cleared_share"],
                     "verdict": verdict(new["recall"], old["recall"],
                                        new["cleared_share"], old["cleared_share"])}
            if group == "slices":
                # v1 moved to the threshold that gives it v2's recall on this slice.
                mask = slices_of(rows)[name]
                matched = threshold_for_recall(baseline_scores[mask], labels[mask], new["recall"])
                entry["v1_cleared_at_v2_recall"] = at_threshold(
                    baseline_scores[mask], labels[mask], matched)["cleared_share"]
            verdicts[f"{group}:{name}"] = entry
    for name, new in candidate["annotated_pothole"].items():
        old = baseline["annotated_pothole"][name]
        verdicts[f"annotated_pothole:{name}"] = {
            "v2_recall": new["recall"], "v1_recall": old["recall"],
            "verdict": verdict(new["recall"], old["recall"], None, None)}
    new, old = candidate["owner_images"], baseline["owner_images"]
    verdicts["owner_images"] = {
        "v2": [new["potholes_flagged"], new["not_potholes_cleared"]],
        "v1": [old["potholes_flagged"], old["not_potholes_cleared"]],
        "verdict": verdict(new["potholes_flagged"], old["potholes_flagged"],
                           new["not_potholes_cleared"], old["not_potholes_cleared"])}
    new, old = candidate["owner_video_events"], baseline["owner_video_events"]
    verdicts["owner_video_events"] = {
        "v2": new["events_flagged"], "v1": old["events_flagged"],
        "verdict": verdict(new["events_flagged"], old["events_flagged"], None, None)}
    wins = all(entry["verdict"] in ("v2 wins", "tie") for entry in verdicts.values()) and any(
        entry["verdict"] == "v2 wins" for entry in verdicts.values())
    return {"by_slice": verdicts, "v2_beats_v1_on_every_slice": wins}


def percent(value):
    return "n/a" if value is None else f"{100 * value:.1f}%"


def markdown(report):
    lines = [f"# Screen v2 scorecard ({report['run_id']})", "",
             "Thresholds come from the validation split only. Recall is of frames the teacher "
             "called damaged; cleared is the share of undamaged frames the screen would answer itself.", ""]
    names = list(report["variants"])
    slices = list(report["variants"][names[0]]["val98"]["slices"])
    for label, title in (("val98", "At the validation-98% threshold"), ("val99", "At the validation-99% threshold")):
        lines += [f"## {title}", "", "| Variant | " + " | ".join(slices) + " |",
                  "|---|" + "---|" * len(slices)]
        for name in names:
            cells = []
            for item in slices:
                p = report["variants"][name][label]["slices"][item]
                cells.append(f"{percent(p['recall'])} ({p['caught']}/{p['damaged']}), {percent(p['cleared_share'])}")
            lines.append(f"| {name} | " + " | ".join(cells) + " |")
        lines += ["", "Stable frames only (the teacher gave the same answer twice):", "",
                  "| Variant | " + " | ".join(slices) + " |", "|---|" + "---|" * len(slices)]
        for name in names:
            cells = []
            for item in slices:
                p = report["variants"][name][label]["stable_slices"][item]
                cells.append(f"{percent(p['recall'])} ({p['caught']}/{p['damaged']}), {percent(p['cleared_share'])}")
            lines.append(f"| {name} | " + " | ".join(cells) + " |")
        lines.append("")
    lines += ["## Annotated potholes, owner labels (validation-98% threshold)", "",
              "| Variant | Annotated-pothole frames flagged, per slice | Owner potholes flagged | Owner not_pothole cleared | Owner video events flagged |",
              "|---|---|---|---|---|"]
    for name in names:
        entry = report["variants"][name]["val98"]
        marked = "; ".join(f"{k}: {v['flagged']}/{v['frames']}" for k, v in entry["annotated_pothole"].items())
        own, events = entry["owner_images"], entry["owner_video_events"]
        lines.append(f"| {name} | {marked} | {own['potholes_flagged']}/{own['potholes']} | "
                     f"{own['not_potholes_cleared']}/{own['not_potholes']} | "
                     f"{events['events_flagged']}/{events['events']} |")
    lines += ["", "## The teacher against itself", "",
              "| Slice | Damaged, first answer | Damaged both times | Teacher-as-screen recall | Undamaged both times |",
              "|---|---|---|---|---|"]
    for name, entry in report["teacher_ceiling"].items():
        lines.append(f"| {name} | {entry['damaged_first_answer']} | {entry['damaged_both_answers']} | "
                     f"{percent(entry['teacher_as_screen_recall'])} | {percent(entry['teacher_as_screen_cleared_share'])} |")
    lines += ["", "## AUC and the best possible clearing at 98% slice recall", "",
              "| Variant | " + " | ".join(slices) + " |", "|---|" + "---|" * len(slices)]
    for name in names:
        cells = []
        for item in slices:
            p = report["variants"][name]["val98"]["slices"][item]
            cells.append("n/a" if p["auc"] is None else
                         f"{p['auc']:.3f}, {percent(p['cleared_share_at_own_98_recall'])}")
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    lines += ["", "## v2 against v1", ""]
    for name, verdict in report["comparisons"].items():
        lines.append(f"- {name}: beats v1 on every slice: **{verdict['v2_beats_v1_on_every_slice']}**. "
                     + "; ".join(f"{k} {v['verdict']}" for k, v in verdict["by_slice"].items()))
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", required=True, help="score file stem of v1 on today's frames")
    parser.add_argument("--variants", nargs="+", required=True)
    parser.add_argument("--run-id", default="local")
    parser.add_argument("--out", default=str(WORK / "report"))
    args = parser.parse_args()
    rows = [row for row in map(json.loads, (WORK / "manifest.jsonl").read_text().splitlines())
            if row["split"] in ("validation", "test")]
    owner_rows = [row for row in map(json.loads, (FRAMES / "index.jsonl").read_text().splitlines())
                  if row.get("split_hint") == "owner"]
    paths = [row["path"] for row in rows]
    owner_paths = [row["path"] for row in owner_rows]
    labels = np.array([row["damaged"] for row in rows])
    validation = np.array([row["split"] == "validation" for row in rows])

    report = {"run_id": args.run_id, "frames": len(rows), "variants": {}, "comparisons": {},
              "teacher_ceiling": teacher_ceiling(rows), "baseline": args.baseline}
    kept = {}
    for stem in [args.baseline] + [v for v in args.variants if v != args.baseline]:
        scores = load_scores(stem, paths)
        owner_scores = load_scores(stem, owner_paths) if owner_paths else np.zeros(0)
        thresholds = {f"val{round(100 * t)}": threshold_for_recall(scores[validation], labels[validation], t)
                      for t in TARGETS}
        if stem == args.baseline:
            thresholds["v1_as_deployed"] = V1_DEPLOYED_RAW
        report["variants"][stem] = judge(scores, rows, owner_rows, owner_scores, thresholds)
        report["variants"][stem]["validation_auc"] = auc(scores[validation], labels[validation])
        kept[stem] = scores
    for stem in args.variants:
        if stem == args.baseline:
            continue
        report["comparisons"][stem] = compare(
            report["variants"][stem]["val98"], report["variants"][args.baseline]["val98"],
            kept[stem], kept[args.baseline], rows, report["variants"][args.baseline]["val98"]["threshold"])
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "report.json", report)
    (out / "report.md").write_text(markdown(report))
    print(markdown(report))


if __name__ == "__main__":
    main()
