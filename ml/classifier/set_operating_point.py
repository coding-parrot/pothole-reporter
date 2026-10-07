#!/usr/bin/env python3
"""Point a released model at another validation-derived threshold from its scorecard.

    python3 set_operating_point.py --report report.json --released model.json \
        --rule val98_every_source --out lambda/model/model.json

release.py sets the threshold for 99% pooled validation recall. evaluate.py also works
out others on the same serving-path scores (val98, val95, val98_every_source), all on
validation only. This writes lambda/model/model.json for one of them and records, in the
file the Lambda ships with, the rule and what it measured on validation and on every
test slice. It needs only the two small JSON files (standard library, any Python 3).

The scorecard's thresholds are on the raw model score. The Lambda compares the
temperature-softened score, so the threshold is softened the same way (order is kept).
It is then lowered by one part in a billion so the validation frame that defines it is
still flagged whatever the last bit of the Lambda's arithmetic says.
"""
import argparse
import json
import math

RULES = {
    "val99": "highest score that still flags 99% of teacher-damaged validation frames (all sources pooled)",
    "val98": "highest score that still flags 98% of teacher-damaged validation frames (all sources pooled)",
    "val95": "highest score that still flags 95% of teacher-damaged validation frames (all sources pooled)",
    "val98_every_source": "highest score that still flags 98% of teacher-damaged frames in EVERY "
                          "validation source with at least 50 of them",
}


def brief(point):
    return {key: point[key] for key in ("recall", "caught", "damaged", "cleared_share", "cleared", "undamaged")}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True, help="the serving-path report.json")
    parser.add_argument("--released", required=True, help="the model.json release.py wrote")
    parser.add_argument("--rule", required=True, choices=sorted(RULES))
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    report = json.load(open(args.report))
    meta = json.load(open(args.released))
    entry = report["variants"][f"served_{meta['model_version']}"][args.rule]
    raw = entry["threshold"]
    softened = 1 / (1 + math.exp(-math.log(raw / (1 - raw)) / meta["temperature"]))
    by_source = {name: brief(point) for name, point in entry["validation_by_domain"].items()}
    pooled = {key: sum(point[key] for point in by_source.values())
              for key in ("caught", "damaged", "cleared", "undamaged")}
    pooled["recall"] = pooled["caught"] / pooled["damaged"]
    pooled["cleared_share"] = pooled["cleared"] / pooled["undamaged"]
    meta.update({
        "threshold": softened * (1 - 1e-9),
        "threshold_raw_score": raw,
        "threshold_rule": RULES[args.rule] + "; scored through the serving path; test never used",
        "scorecard_run": report["run_id"],
        "at_threshold": {
            "validation": pooled, "validation_by_source": by_source,
            "test": {name: brief(point) for name, point in entry["slices"].items()},
            "test_teacher_stable_only": {name: brief(point) for name, point in entry["stable_slices"].items()},
            "annotated_pothole_frames_flagged": {
                name: [point["flagged"], point["frames"]] for name, point in entry["annotated_pothole"].items()},
            "owner_images": {key: entry["owner_images"][key] for key in
                             ("potholes", "potholes_flagged", "not_potholes", "not_potholes_cleared")},
            "owner_video_events": entry["owner_video_events"],
        },
    })
    meta.pop("target_validation_recall", None)
    with open(args.out, "w") as output:
        output.write(json.dumps(meta, indent=1, sort_keys=True) + "\n")
    print(f"{meta['model_version']}: threshold {meta['threshold']:.6f} ({args.rule}); validation "
          f"{pooled['recall']:.4f} recall, {pooled['cleared_share']:.4f} cleared; all test "
          f"{entry['slices']['all_test']['recall']:.4f} recall, {entry['slices']['all_test']['cleared_share']:.4f} cleared")


if __name__ == "__main__":
    main()
