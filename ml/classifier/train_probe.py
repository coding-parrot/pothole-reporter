#!/usr/bin/env python3
"""Train a small head on cached frozen-encoder features (the v1 recipe).

    python train_probe.py --encoder mobilenetv3_l --size 448 --name v2probe
    python train_probe.py --encoder mobilenetv3_l --size 448 --name v2probe_aug --views 4
    python train_probe.py --encoder mobilenetv3_l --size 448 --name v1 --score-head hidden256

The recipe is v1's: frozen encoder, features pooled over the whole frame, one hidden
layer of 256, class-balanced loss, soft targets where the teacher answered twice, the
weight decay and the epoch picked on validation by the share of undamaged frames cleared
at 98% recall. --views adds augmented views of every training frame (embed.py --views).
--score-head only scores an existing head (v1's released one) on today's frames.

Writes work/heads/<encoder>_<size>_<name>.pt and work/scores/<encoder>_<size>_<name>.npz
(frame paths and scores for every manifest frame and the owner-labelled images).
"""
import argparse
import json

import numpy as np
import torch

from common import EMBEDDINGS, FRAMES, WORK, soft_target, write_json
from metrics import at_threshold, auc, threshold_for_recall
from models import Head

SEED = 20261007


def features_for(encoder, size, paths, suffix=""):
    cached = np.load(EMBEDDINGS / f"{encoder}_{size}{suffix}.npz", allow_pickle=False)
    position = {path: index for index, path in enumerate(cached["paths"])}
    missing = [path for path in paths if path not in position]
    if missing:
        raise SystemExit(f"{len(missing)} frames have no {suffix or 'clean'} embedding; rerun embed.py")
    return cached["features"][[position[path] for path in paths]].astype(np.float32)


def owner_paths():
    return [row["path"] for row in map(json.loads, (FRAMES / "index.jsonl").read_text().splitlines())
            if row.get("split_hint") == "owner"]


def fit_head(x, y, validation, hidden, weight_decay, epochs):
    """Minibatch AdamW with class-balanced loss; keeps the best validation epoch."""
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
    parser.add_argument("--name", required=True)
    parser.add_argument("--views", type=int, default=0)
    parser.add_argument("--hidden", type=int, default=256)
    parser.add_argument("--score-head", help="score this existing head instead of training")
    args = parser.parse_args()
    rows = [json.loads(line) for line in (WORK / "manifest.jsonl").read_text().splitlines()]
    paths = [row["path"] for row in rows]
    split = np.array([row["split"] for row in rows])
    labels = np.array([row["damaged"] for row in rows])
    train, validation = split == "train", split == "validation"
    x = torch.from_numpy(features_for(args.encoder, args.size, paths))
    stem = f"{args.encoder}_{args.size}_{args.name}"
    (WORK / "heads").mkdir(exist_ok=True)
    (WORK / "scores").mkdir(exist_ok=True)

    if args.score_head:
        saved = torch.load(WORK / "heads" / f"{args.encoder}_{args.size}_{args.score_head}.pt")
        head = Head(saved["width"], hidden=saved["hidden"])
        head.load_state_dict(saved["state"])
        head.eval()
        result = {"scored_head": args.score_head}
    else:
        # Training targets are soft where the teacher answered twice; every metric is
        # against the first answer alone, which is what production would have returned.
        soft = np.array([soft_target(row) for row in rows], dtype=np.float32)
        train_paths = [path for path, chosen in zip(paths, train) if chosen]
        x_train, y_train = [x[train]], [torch.from_numpy(soft[train])]
        for view in range(args.views):
            x_train.append(torch.from_numpy(
                features_for(args.encoder, args.size, train_paths, f"_aug{view}")))
            y_train.append(y_train[0])
        x_train, y_train = torch.cat(x_train), torch.cat(y_train)
        # The same number of passes over real frames as v1 made, whatever the view count.
        epochs = max(20, 80 // (args.views + 1))
        candidates = []
        for weight_decay in (1e-4, 1e-2, 1e-1):
            head, cleared = fit_head(x_train, y_train, (x[validation], labels[validation]),
                                     args.hidden, weight_decay, epochs)
            candidates.append((cleared, weight_decay, head))
            print(f"  weight decay {weight_decay:g}: validation cleared at 98% recall {cleared:.4f}",
                  flush=True)
        cleared, weight_decay, head = max(candidates, key=lambda item: item[0])
        torch.save({"state": head.state_dict(), "hidden": args.hidden, "width": x.shape[1]},
                   WORK / "heads" / f"{stem}.pt")
        result = {"weight_decay": weight_decay, "epochs": epochs, "views": args.views,
                  "train_rows": len(x_train), "validation_cleared_at_98": cleared}

    extra = owner_paths()
    with torch.inference_mode():
        scores = torch.sigmoid(head(x)).numpy()
        owner = (torch.sigmoid(head(torch.from_numpy(
            features_for(args.encoder, args.size, extra)))).numpy() if extra else np.zeros(0))
    np.savez(WORK / "scores" / f"{stem}.npz", paths=np.array(paths + extra),
             scores=np.concatenate([scores, owner]).astype(np.float32))
    test = split == "test"
    result.update({"encoder": args.encoder, "size": args.size, "name": args.name,
                   "validation_auc": auc(scores[validation], labels[validation]),
                   "test_auc": auc(scores[test], labels[test])})
    write_json(WORK / "results" / f"probe_{stem}.json", result)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
