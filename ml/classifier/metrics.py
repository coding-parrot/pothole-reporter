"""Screening metrics: how many undamaged frames are cleared at a required recall."""
import math

import numpy as np

TARGET_RECALLS = (0.99, 0.98, 0.95)


def threshold_for_recall(scores, labels, recall):
    """The highest threshold that still flags at least `recall` of the damaged frames.
    A frame is flagged when score >= threshold."""
    positives = np.sort(scores[labels])
    if not len(positives):
        return 0.0
    allowed_misses = math.floor((1 - recall) * len(positives) + 1e-9)
    return float(positives[allowed_misses])


def at_threshold(scores, labels, threshold):
    flagged = scores >= threshold
    damaged = int(labels.sum())
    undamaged = int((~labels).sum())
    caught = int((flagged & labels).sum())
    cleared = int((~flagged & ~labels).sum())
    return {
        "threshold": float(threshold),
        "damaged": damaged, "caught": caught, "missed": damaged - caught,
        "recall": caught / damaged if damaged else None,
        "recall_wilson_low": wilson_low(caught, damaged),
        "undamaged": undamaged, "cleared": cleared,
        "cleared_share": cleared / undamaged if undamaged else None,
        # Of all frames, the share that would skip the gpt-5-mini call.
        "frames_answered_by_screen": float((~flagged).mean()) if len(flagged) else None,
    }


def wilson_low(successes, total, z=1.96):
    if not total:
        return None
    p = successes / total
    centre = p + z * z / (2 * total)
    spread = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
    return (centre - spread) / (1 + z * z / total)


def auc(scores, labels):
    positives, negatives = scores[labels], scores[~labels]
    if not len(positives) or not len(negatives):
        return None
    order = np.argsort(np.concatenate([positives, negatives]), kind="mergesort")
    ranks = np.empty(len(order))
    ranks[order] = np.arange(1, len(order) + 1)
    # Average the ranks of ties.
    combined = np.concatenate([positives, negatives])
    for value in np.unique(combined[np.isin(combined, combined[np.where(np.diff(np.sort(combined)) == 0)])]):
        tied = combined == value
        ranks[tied] = ranks[tied].mean()
    total = ranks[:len(positives)].sum() - len(positives) * (len(positives) + 1) / 2
    return float(total / (len(positives) * len(negatives)))


def report(validation, test):
    """validation and test are (scores, labels). Thresholds come from validation and
    are judged on test; `test_own_threshold` is the curve read off test itself."""
    out = {"auc": auc(*test), "validation_auc": auc(*validation), "operating_points": {}}
    for recall in TARGET_RECALLS:
        chosen = threshold_for_recall(*validation, recall)
        out["operating_points"][f"{recall:.2f}"] = {
            "validation": at_threshold(*validation, chosen),
            "test": at_threshold(*test, chosen),
            "test_own_threshold": at_threshold(*test, threshold_for_recall(*test, recall)),
        }
    return out
