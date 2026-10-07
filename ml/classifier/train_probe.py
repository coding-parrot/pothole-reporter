#!/usr/bin/env python3
"""Train a small head on cached frozen-encoder features and score it by source split.

    python train_probe.py --encoder dinov2_s14 --size 448

Two heads are fitted: a linear probe and one hidden layer. Hyperparameters are picked
on validation by the share of undamaged frames cleared at 98% recall. Results go to
work/results/<encoder>_<size>.json and the heads to work/heads/.
"""
import argparse
import json

import numpy as np
import torch

from common import EMBEDDINGS, WORK, soft_target, write_json
from metrics import at_threshold, report, threshold_for_recall
from models import Head

SEED = 20261007


def load(encoder, size):
    rows = [json.loads(line) for line in (WORK / "manifest.jsonl").read_text().splitlines()]
    cached = np.load(EMBEDDINGS / f"{encoder}_{size}.npz", allow_pickle=False)
    position = {path: index for index, path in enumerate(cached["paths"])}
    missing = [row["path"] for row in rows if row["path"] not in position]
    if missing:
        raise SystemExit(f"{len(missing)} frames have no embedding; rerun embed.py")
    order = [position[row["path"]] for row in rows]
    return rows, cached["features"][order].astype(np.float32)


def fit_head(x, y, validation, hidden, weight_decay, epochs=80):
    """Full-batch AdamW with class-balanced loss; keeps the best validation epoch."""
    torch.manual_seed(SEED)
    head = Head(x.shape[1], hidden=hidden, dropout=0.5 if hidden else 0.0)
    head.centre.copy_(x.mean(0))
    head.scale.copy_(x.std(0).clamp_min(1e-6))
    optimiser = torch.optim.AdamW(head.layers.parameters(), lr=1e-3, weight_decay=weight_decay)
    positive_weight = (len(y) - y.sum()) / y.sum()
    loss_function = torch.nn.BCEWithLogitsLoss(pos_weight=positive_weight)
    best = (-1.0, None)
    generator = torch.Generator().manual_seed(SEED)
    for epoch in range(epochs):
        head.train()
        for batch in torch.randperm(len(x), generator=generator).split(256):
            optimiser.zero_grad()
            loss_function(head(x[batch]), y[batch]).backward()
            optimiser.step()
        head.eval()
        with torch.inference_mode():
            scores = torch.sigmoid(head(validation[0])).numpy()
        labels = validation[1]
        cleared = at_threshold(scores, labels, threshold_for_recall(scores, labels, 0.98))
        if cleared["cleared_share"] > best[0]:
            best = (cleared["cleared_share"], {k: v.clone() for k, v in head.state_dict().items()})
    head.load_state_dict(best[1])
    return head.eval(), best[0]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--encoder", required=True)
    parser.add_argument("--size", type=int, required=True)
    args = parser.parse_args()
    rows, features = load(args.encoder, args.size)
    split = np.array([row["split"] for row in rows])
    domain = np.array([row["domain"] for row in rows])
    labels = np.array([row["damaged"] for row in rows])
    x = torch.from_numpy(features)
    train = split == "train"
    validation = split == "validation"
    test = split == "test"
    # Training targets are soft where the teacher answered twice; every metric below is
    # against the first answer alone, which is what production would have returned.
    soft = np.array([soft_target(row) for row in rows], dtype=np.float32)
    y_train = torch.from_numpy(soft[train])

    results = {"encoder": args.encoder, "size": args.size, "feature_width": features.shape[1],
               "heads": {}}
    for kind, hidden in (("linear", 0), ("hidden256", 256)):
        candidates = []
        for weight_decay in (1e-4, 1e-2, 1e-1):
            head, cleared = fit_head(x[train], y_train, (x[validation], labels[validation]),
                                     hidden, weight_decay)
            candidates.append((cleared, weight_decay, head))
        cleared, weight_decay, head = max(candidates, key=lambda item: item[0])
        with torch.inference_mode():
            scores = torch.sigmoid(head(x)).numpy()
        entry = {"weight_decay": weight_decay,
                 "all": report((scores[validation], labels[validation]),
                               (scores[test], labels[test]))}
        for name in ("drive_video", "rdd2022_india"):
            chosen = domain == name
            entry[name] = report((scores[validation & chosen], labels[validation & chosen]),
                                 (scores[test & chosen], labels[test & chosen]))
        results["heads"][kind] = entry
        (WORK / "heads").mkdir(exist_ok=True)
        torch.save({"state": head.state_dict(), "hidden": hidden, "width": features.shape[1]},
                   WORK / "heads" / f"{args.encoder}_{args.size}_{kind}.pt")
        np.save(WORK / "heads" / f"{args.encoder}_{args.size}_{kind}_scores.npy", scores)
        point = entry["all"]["operating_points"]["0.98"]["test"]
        print(f"{args.encoder} {args.size} {kind:9s} wd {weight_decay:g}: test AUC "
              f"{entry['all']['auc']:.4f}; at the validation 98% threshold: test recall "
              f"{point['recall']:.3f} ({point['caught']}/{point['damaged']}), cleared "
              f"{point['cleared_share']:.3f} of undamaged", flush=True)
    write_json(WORK / "results" / f"{args.encoder}_{args.size}.json", results)


if __name__ == "__main__":
    main()
