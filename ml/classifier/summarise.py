#!/usr/bin/env python3
"""One table for every trained head: the trade-off curve on the held-out test split.

For each model the threshold for 99 / 98 / 95% recall is set on validation (all
sources together, as one deployed threshold would be) and judged on test, overall and
per source. "own" columns read the curve off the test split itself: the share of
undamaged frames cleared at exactly that test recall, the best any threshold could do.
"""
import json

import numpy as np

from common import WORK, write_json
from metrics import TARGET_RECALLS, at_threshold, auc, threshold_for_recall


def main():
    rows = [json.loads(line) for line in (WORK / "manifest.jsonl").read_text().splitlines()]
    split = np.array([row["split"] for row in rows])
    domain = np.array([row["domain"] for row in rows])
    labels = np.array([row["damaged"] for row in rows])
    validation, test = split == "validation", split == "test"
    table = []
    for path in sorted((WORK / "heads").glob("*_scores.npy")):
        scores = np.load(path)
        entry = {"model": path.name.removesuffix("_scores.npy"), "test_auc": {}, "points": {}, "own": {}}
        for name, chosen in (("all", np.ones(len(rows), bool)), ("drive_video", domain == "drive_video"),
                             ("rdd2022_india", domain == "rdd2022_india")):
            entry["test_auc"][name] = auc(scores[test & chosen], labels[test & chosen])
            entry["own"][name] = {
                f"{recall:.2f}": at_threshold(scores[test & chosen], labels[test & chosen], threshold_for_recall(
                    scores[test & chosen], labels[test & chosen], recall))["cleared_share"]
                for recall in TARGET_RECALLS}
        for recall in TARGET_RECALLS:
            threshold = threshold_for_recall(scores[validation], labels[validation], recall)
            entry["points"][f"{recall:.2f}"] = {
                name: at_threshold(scores[test & chosen], labels[test & chosen], threshold)
                for name, chosen in (("all", np.ones(len(rows), bool)), ("drive_video", domain == "drive_video"),
                                     ("rdd2022_india", domain == "rdd2022_india"))}
        table.append(entry)
    write_json(WORK / "results" / "summary.json", table)
    print("| model | test AUC all / RDD / drive | val 99%: recall, cleared | val 98%: recall, cleared "
          "| val 95%: recall, cleared | best possible cleared at test recall 99 / 98 / 95 |")
    print("|---|---|---|---|---|---|")
    for entry in table:
        cells = [entry["model"], " / ".join(f"{entry['test_auc'][k]:.3f}" for k in ("all", "rdd2022_india", "drive_video"))]
        for recall in TARGET_RECALLS:
            point = entry["points"][f"{recall:.2f}"]["all"]
            cells.append(f"{100 * point['recall']:.1f}%, {100 * point['cleared_share']:.1f}%")
        cells.append(" / ".join(f"{100 * entry['own']['all'][f'{r:.2f}']:.1f}%" for r in TARGET_RECALLS))
        print("| " + " | ".join(cells) + " |")
    print("\nPer source at the validation 99% threshold (recall, cleared):")
    for entry in table:
        point = entry["points"]["0.99"]
        print(f"  {entry['model']:34s} RDD {100 * point['rdd2022_india']['recall']:.1f}%, "
              f"{100 * point['rdd2022_india']['cleared_share']:.1f}%   drive "
              f"{100 * point['drive_video']['recall']:.1f}%, {100 * point['drive_video']['cleared_share']:.1f}%")


if __name__ == "__main__":
    main()
