#!/usr/bin/env python3
"""Fine-tune the last blocks of an encoder, starting from its trained probe head.

    python finetune.py --encoder efficientnet_b0 --size 448 --head hidden256

Used only when the frozen probe is short of the bar. Augmentation keeps the whole frame
(AGENTS.md): a horizontal flip and brightness, contrast and colour gains, never a crop.
The best validation epoch (most undamaged frames cleared at 98% recall) is kept and
written as work/heads/<encoder>_<size>_ft.pt and work/finetuned/<encoder>_<size>.pt.
"""
import argparse
import json
import random
import time

import numpy as np
import torch
from PIL import Image, ImageEnhance

from common import FRAMES, WORK, write_json
from metrics import at_threshold, report, threshold_for_recall
from models import Encoder, Head, letterbox

SEED = 20261007


class Frames(torch.utils.data.Dataset):
    def __init__(self, rows, size, augment):
        self.rows, self.size, self.augment = rows, size, augment

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, index):
        row = self.rows[index]
        image = Image.open(FRAMES / row["path"]).convert("RGB")
        if self.augment:
            if random.random() < 0.5:
                image = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
            for enhancer in (ImageEnhance.Brightness, ImageEnhance.Contrast, ImageEnhance.Color):
                image = enhancer(image).enhance(random.uniform(0.75, 1.25))
        return torch.from_numpy(letterbox(image, self.size).copy()), float(row["damaged"])


def trainable_parts(encoder, blocks):
    body = encoder.body
    if encoder.kind == "tokens":
        return list(body.blocks[-blocks:]) + [body.norm]
    return list(body.blocks[-blocks:]) + [body.conv_head, body.bn2]


def scores_for(encoder, head, rows, size, device, batch):
    loader = torch.utils.data.DataLoader(Frames(rows, size, False), batch_size=batch, num_workers=3)
    out = []
    encoder.eval()
    head.eval()
    with torch.inference_mode():
        for frames, _ in loader:
            out.append(torch.sigmoid(head(encoder(frames.to(device)))).float().cpu().numpy())
    return np.concatenate(out)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--encoder", required=True)
    parser.add_argument("--size", type=int, required=True)
    parser.add_argument("--head", default="hidden256")
    parser.add_argument("--blocks", type=int, default=2, help="how many final blocks to train")
    parser.add_argument("--epochs", type=int, default=4)
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--lr", type=float, default=2e-5)
    parser.add_argument("--device", default="mps" if torch.backends.mps.is_available() else "cpu")
    args = parser.parse_args()
    random.seed(SEED)
    torch.manual_seed(SEED)
    torch.set_num_threads(3)

    rows = [json.loads(line) for line in (WORK / "manifest.jsonl").read_text().splitlines()]
    by_split = {name: [row for row in rows if row["split"] == name]
                for name in ("train", "validation", "test")}
    saved = torch.load(WORK / "heads" / f"{args.encoder}_{args.size}_{args.head}.pt")
    head = Head(saved["width"], hidden=saved["hidden"], dropout=0.5 if saved["hidden"] else 0.0)
    head.load_state_dict(saved["state"])
    encoder = Encoder(args.encoder, args.size)
    for parameter in encoder.parameters():
        parameter.requires_grad_(False)
    parts = trainable_parts(encoder, args.blocks)
    for part in parts:
        for parameter in part.parameters():
            parameter.requires_grad_(True)
    encoder.to(args.device)
    head.to(args.device)
    optimiser = torch.optim.AdamW([
        {"params": [p for part in parts for p in part.parameters()], "lr": args.lr},
        {"params": head.layers.parameters(), "lr": args.lr * 10},
    ], weight_decay=0.05)
    labels = {name: np.array([row["damaged"] for row in chosen]) for name, chosen in by_split.items()}
    positive_weight = torch.tensor((~labels["train"]).sum() / labels["train"].sum(), device=args.device)
    loss_function = torch.nn.BCEWithLogitsLoss(pos_weight=positive_weight)
    loader = torch.utils.data.DataLoader(Frames(by_split["train"], args.size, True),
                                         batch_size=args.batch, shuffle=True, num_workers=3,
                                         drop_last=True)
    steps = args.epochs * len(loader)
    schedule = torch.optim.lr_scheduler.OneCycleLR(
        optimiser, max_lr=[args.lr, args.lr * 10], total_steps=steps, pct_start=0.1)
    best = (-1.0, None, None, -1)
    started = time.monotonic()
    for epoch in range(args.epochs):
        # Frozen layers stay in eval mode so their batch-norm statistics do not drift.
        encoder.eval()
        for part in parts:
            part.train()
        head.train()
        for step, (frames, target) in enumerate(loader, 1):
            optimiser.zero_grad()
            loss = loss_function(head(encoder(frames.to(args.device))), target.float().to(args.device))
            loss.backward()
            optimiser.step()
            schedule.step()
            if step % 50 == 0:
                print(f"epoch {epoch} step {step}/{len(loader)} loss {loss.item():.4f} "
                      f"{time.monotonic() - started:.0f}s", flush=True)
        scores = scores_for(encoder, head, by_split["validation"], args.size, args.device, args.batch)
        point = at_threshold(scores, labels["validation"],
                             threshold_for_recall(scores, labels["validation"], 0.98))
        print(f"epoch {epoch}: validation cleared at 98% recall {point['cleared_share']:.4f}", flush=True)
        if point["cleared_share"] > best[0]:
            best = (point["cleared_share"],
                    {k: v.detach().cpu().clone() for k, v in encoder.body.state_dict().items()},
                    {k: v.detach().cpu().clone() for k, v in head.state_dict().items()}, epoch)
    encoder.body.load_state_dict(best[1])
    head.load_state_dict(best[2])
    (WORK / "finetuned").mkdir(exist_ok=True)
    torch.save(best[1], WORK / "finetuned" / f"{args.encoder}_{args.size}.pt")
    torch.save({"state": best[2], "hidden": saved["hidden"], "width": saved["width"]},
               WORK / "heads" / f"{args.encoder}_{args.size}_ft.pt")

    domain = {name: np.array([row["domain"] for row in chosen]) for name, chosen in by_split.items()}
    scores = {name: scores_for(encoder, head, by_split[name], args.size, args.device, args.batch)
              for name in ("validation", "test")}
    result = {"encoder": args.encoder, "size": args.size, "blocks": args.blocks,
              "epochs": args.epochs, "best_epoch": best[3], "lr": args.lr,
              "all": report((scores["validation"], labels["validation"]),
                            (scores["test"], labels["test"]))}
    for name in ("drive_video", "rdd2022_india"):
        v = domain["validation"] == name
        t = domain["test"] == name
        result[name] = report((scores["validation"][v], labels["validation"][v]),
                              (scores["test"][t], labels["test"][t]))
    write_json(WORK / "results" / f"{args.encoder}_{args.size}_ft.json", result)
    point = result["all"]["operating_points"]["0.98"]["test"]
    print(f"fine-tuned {args.encoder} {args.size}: test AUC {result['all']['auc']:.4f}; at the "
          f"validation 98% threshold: test recall {point['recall']:.3f}, cleared "
          f"{point['cleared_share']:.3f}; best epoch {best[3]}")


if __name__ == "__main__":
    main()
