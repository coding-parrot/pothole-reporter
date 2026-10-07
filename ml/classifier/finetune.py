#!/usr/bin/env python3
"""Fine-tune the last blocks of a CNN encoder, starting from its trained probe head.

    python finetune.py --encoder mobilenetv3_l --size 448 --from-block 5

Used only because the frozen probes are short of the bar. Training whole images on this
shared 8 GB Mac took 5 s a step, so the frozen part of the network (the stem and the
blocks before --from-block) is run once and its feature maps are cached; only the last
blocks and the head are then trained, on those maps. That is the same optimisation as
fine-tuning the last blocks with the earlier ones frozen, without image augmentation.

Targets are soft where the teacher answered twice. The epoch kept is the one that
clears most undamaged validation frames at 98% recall of first-answer damaged frames.
Outputs: work/finetuned/<encoder>_<size>.pt (the full encoder body) and
work/heads/<encoder>_<size>_ft.pt, which export_onnx.py and release.py take.
"""
import argparse
import json
import time

import numpy as np
import torch
from PIL import Image

from common import FRAMES, WORK, soft_target, write_json
from metrics import at_threshold, report, threshold_for_recall
from models import Encoder, Head, letterbox

SEED = 20261007


class Frames(torch.utils.data.Dataset):
    def __init__(self, paths, size):
        self.paths, self.size = paths, size

    def __len__(self):
        return len(self.paths)

    def __getitem__(self, index):
        return torch.from_numpy(letterbox(Image.open(FRAMES / self.paths[index]), self.size))


def cache_trunk(encoder, rows, size, from_block, device, target):
    """Feature maps entering the first trainable block, float16, one row per frame."""
    body = encoder.body
    paths = [row["path"] for row in rows]
    listing = target.with_suffix(".paths.json")
    if target.exists() and listing.exists() and json.loads(listing.read_text()) == paths:
        return np.load(target, mmap_mode="r")
    loader = torch.utils.data.DataLoader(Frames(paths, size), batch_size=32, num_workers=3)
    maps = None
    done = 0
    started = time.monotonic()
    with torch.inference_mode():
        for frames in loader:
            x = (frames.to(device).permute(0, 3, 1, 2).float() - encoder.mean) / encoder.std
            x = body.bn1(body.conv_stem(x))
            for block in body.blocks[:from_block]:
                x = block(x)
            x = x.float().cpu().numpy().astype(np.float16)
            if maps is None:
                maps = np.lib.format.open_memmap(target, mode="w+", dtype=np.float16,
                                                 shape=(len(paths), *x.shape[1:]))
            maps[done:done + len(x)] = x
            done += len(x)
            if done % 1600 == 0:
                print(f"trunk {done}/{len(paths)} {time.monotonic() - started:.0f}s", flush=True)
    maps.flush()
    listing.write_text(json.dumps(paths))
    return np.load(target, mmap_mode="r")


def tail_features(body, from_block, maps):
    x = maps
    for block in body.blocks[from_block:]:
        x = block(x)
    if hasattr(body, "bn2"):  # EfficientNet ends its feature map with conv_head and bn2
        x = body.bn2(body.conv_head(x))
    return torch.cat([x.mean((2, 3)), x.amax((2, 3))], dim=1)


def tail_modules(body, from_block):
    return list(body.blocks[from_block:]) + ([body.conv_head, body.bn2] if hasattr(body, "bn2") else [])


def scores_for(body, head, from_block, maps, index, device):
    for module in tail_modules(body, from_block):
        module.eval()
    head.eval()
    out = []
    with torch.inference_mode():
        for chunk in np.array_split(index, max(1, len(index) // 128)):
            batch = torch.from_numpy(np.asarray(maps[np.sort(chunk)], dtype=np.float32)).to(device)
            out.append(torch.sigmoid(head(tail_features(body, from_block, batch))).cpu().numpy())
    return np.concatenate(out)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--encoder", required=True, choices=["mobilenetv3_l", "efficientnet_b0"])
    parser.add_argument("--size", type=int, required=True)
    parser.add_argument("--head", default="hidden256")
    parser.add_argument("--from-block", type=int, default=5, help="first block that is trained")
    parser.add_argument("--epochs", type=int, default=12)
    parser.add_argument("--batch", type=int, default=64)
    parser.add_argument("--lr", type=float, default=1e-4)
    parser.add_argument("--device", default="mps" if torch.backends.mps.is_available() else "cpu")
    args = parser.parse_args()
    torch.manual_seed(SEED)
    torch.set_num_threads(3)
    generator = np.random.default_rng(SEED)

    rows = [json.loads(line) for line in (WORK / "manifest.jsonl").read_text().splitlines()]
    split = np.array([row["split"] for row in rows])
    domain = np.array([row["domain"] for row in rows])
    labels = np.array([row["damaged"] for row in rows])
    soft = np.array([soft_target(row) for row in rows], dtype=np.float32)
    index = {name: np.flatnonzero(split == name) for name in ("train", "validation", "test")}

    encoder = Encoder(args.encoder, args.size).eval().to(args.device)
    for parameter in encoder.parameters():
        parameter.requires_grad_(False)
    (WORK / "trunk").mkdir(exist_ok=True)
    maps = cache_trunk(encoder, rows, args.size, args.from_block, args.device,
                       WORK / "trunk" / f"{args.encoder}_{args.size}_b{args.from_block}.npy")
    body = encoder.body
    saved = torch.load(WORK / "heads" / f"{args.encoder}_{args.size}_{args.head}.pt")
    head = Head(saved["width"], hidden=saved["hidden"], dropout=0.5 if saved["hidden"] else 0.0)
    head.load_state_dict(saved["state"])
    head.to(args.device)
    trained = tail_modules(body, args.from_block)
    parameters = [p for module in trained for p in module.parameters()]
    for parameter in parameters:
        parameter.requires_grad_(True)
    optimiser = torch.optim.AdamW([
        {"params": parameters, "lr": args.lr},
        {"params": head.layers.parameters(), "lr": args.lr},
    ], weight_decay=0.05)
    steps_per_epoch = len(index["train"]) // args.batch
    schedule = torch.optim.lr_scheduler.OneCycleLR(
        optimiser, max_lr=args.lr, total_steps=args.epochs * steps_per_epoch, pct_start=0.15)
    train_soft = soft[index["train"]]
    positive_weight = torch.tensor((1 - train_soft).sum() / train_soft.sum(), device=args.device)
    loss_function = torch.nn.BCEWithLogitsLoss(pos_weight=positive_weight)

    def validation_point():
        scores = scores_for(body, head, args.from_block, maps, index["validation"], args.device)
        truth = labels[np.sort(index["validation"])]
        return at_threshold(scores, truth, threshold_for_recall(scores, truth, 0.98))["cleared_share"]

    history = [{"epoch": -1, "validation_cleared_at_98": validation_point()}]
    print(f"probe start: validation cleared at 98% recall {history[0]['validation_cleared_at_98']:.4f}",
          flush=True)
    best = (history[0]["validation_cleared_at_98"],
            {k: v.detach().cpu().clone() for k, v in body.state_dict().items()},
            {k: v.detach().cpu().clone() for k, v in head.state_dict().items()}, -1)
    started = time.monotonic()
    for epoch in range(args.epochs):
        for module in trained:
            module.train()
        head.train()
        order = generator.permutation(index["train"])
        running = 0.0
        for step in range(steps_per_epoch):
            chosen = np.sort(order[step * args.batch:(step + 1) * args.batch])
            batch = torch.from_numpy(np.asarray(maps[chosen], dtype=np.float32)).to(args.device)
            target = torch.from_numpy(soft[chosen]).to(args.device)
            optimiser.zero_grad()
            loss = loss_function(head(tail_features(body, args.from_block, batch)), target)
            loss.backward()
            optimiser.step()
            schedule.step()
            running += loss.item()
        cleared = validation_point()
        history.append({"epoch": epoch, "train_loss": running / steps_per_epoch,
                        "validation_cleared_at_98": cleared})
        print(f"epoch {epoch}: loss {running / steps_per_epoch:.4f}, validation cleared at 98% "
              f"recall {cleared:.4f}, {time.monotonic() - started:.0f}s", flush=True)
        if cleared > best[0]:
            best = (cleared, {k: v.detach().cpu().clone() for k, v in body.state_dict().items()},
                    {k: v.detach().cpu().clone() for k, v in head.state_dict().items()}, epoch)
    body.load_state_dict(best[1])
    head.load_state_dict(best[2])
    (WORK / "finetuned").mkdir(exist_ok=True)
    torch.save(best[1], WORK / "finetuned" / f"{args.encoder}_{args.size}.pt")
    torch.save({"state": best[2], "hidden": saved["hidden"], "width": saved["width"]},
               WORK / "heads" / f"{args.encoder}_{args.size}_ft.pt")

    every = np.arange(len(rows))
    scores = scores_for(body, head, args.from_block, maps, every, args.device)
    np.save(WORK / "heads" / f"{args.encoder}_{args.size}_ft_scores.npy", scores)
    validation, test = split == "validation", split == "test"
    result = {"encoder": args.encoder, "size": args.size, "from_block": args.from_block,
              "epochs": args.epochs, "best_epoch": best[3], "lr": args.lr, "history": history,
              "all": report((scores[validation], labels[validation]), (scores[test], labels[test]))}
    for name in ("drive_video", "rdd2022_india"):
        chosen = domain == name
        result[name] = report((scores[validation & chosen], labels[validation & chosen]),
                              (scores[test & chosen], labels[test & chosen]))
    write_json(WORK / "results" / f"{args.encoder}_{args.size}_ft.json", result)
    point = result["all"]["operating_points"]["0.98"]["test"]
    print(f"fine-tuned {args.encoder} {args.size} from block {args.from_block}: test AUC "
          f"{result['all']['auc']:.4f}; at the validation 98% threshold: test recall "
          f"{point['recall']:.3f}, cleared {point['cleared_share']:.3f}; best epoch {best[3]}")


if __name__ == "__main__":
    main()
