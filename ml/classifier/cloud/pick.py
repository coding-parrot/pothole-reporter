#!/usr/bin/env python3
"""Print the MobileNetV3-L head name with the best validation number (the share of
undamaged validation frames cleared at 98% validation recall). Test plays no part."""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import WORK  # noqa: E402
from metrics import at_threshold, threshold_for_recall  # noqa: E402

rows = [row for row in map(json.loads, (WORK / "manifest.jsonl").read_text().splitlines())
        if row["split"] == "validation"]
labels = np.array([row["damaged"] for row in rows])
best = (-1.0, None)
for path in sorted((WORK / "scores").glob("mobilenetv3_l_448_*.npz")):
    name = path.stem.removeprefix("mobilenetv3_l_448_")
    if name == "v1":
        continue
    saved = np.load(path, allow_pickle=False)
    position = {p: i for i, p in enumerate(saved["paths"])}
    scores = saved["scores"][[position[row["path"]] for row in rows]]
    cleared = at_threshold(scores, labels, threshold_for_recall(scores, labels, 0.98))["cleared_share"]
    print(f"{name}: validation cleared at 98% recall {cleared:.4f}", file=sys.stderr)
    best = max(best, (cleared, name))
print(best[1])
