#!/usr/bin/env python3
"""Fine-tune a small CNN encoder on whole frames with real augmentation.

    python finetune.py --encoder mobilenetv3_l --size 448 --name ft448

v1 could only train the last blocks on cached feature maps, with no augmentation, and
it memorised. This trains on the images: every step sees freshly augmented whole frames
(augment.py: colour, blur, JPEG, resolution, flip, shrink, perspective; never a crop).

What makes it fit an 8 GB shared Mac (measured on its M2 GPU, MobileNetV3-L at 448):
  batch 16, everything trainable        55 s a step (the machine swaps)
  batch 8, everything trainable         0.71 s a step, 2.8 GB
  batch 8, stem and first 2 stages frozen   0.40 s a step, 2.6 GB
The stem and the first --frozen-blocks stages (edges and textures, most of the memory)
stay as pretrained; the rest and the head train. Batch-norm layers keep their running
statistics: a batch of 8 is too small to estimate them.

Sampling is class-balanced per epoch (half the frames damaged), targets are soft where
the teacher answered twice, the loss is binary cross-entropy or focal. After every epoch
the held-out-source validation split is scored on clean frames; the epoch kept is the
one that clears most undamaged validation frames at 98% recall, and training stops when
that has not improved for --patience epochs. Every epoch is checkpointed so an
interrupted run resumes.
"""
import argparse
import json
import time

import numpy as np
import torch
from PIL import Image

from augment import augment
from common import FRAMES, WORK, soft_target, write_json
from metrics import at_threshold, auc, threshold_for_recall
from models import Encoder, Head

SEED = 20261007


class Frames(torch.utils.data.Dataset):
    """rows[i] -> (uint8 canvas, target). With augmentation every draw is different."""

    def __init__(self, paths, targets, size, augmented, seed=0):
        self.paths, self.targets, self.size = paths, targets, size
        self.augmented, self.seed = augmented, seed

    def __len__(self):
        return len(self.paths)

    def __getitem__(self, item):
        index, draw = item if isinstance(item, tuple) else (item, 0)
        rng = np.random.default_rng((self.seed, draw, index)) if self.augmented else None
        pixels = augment(Image.open(FRAMES / self.paths[index]), self.size, rng)
        return torch.from_numpy(np.ascontiguousarray(pixels)), self.targets[index]


class BalancedEpoch(torch.utils.data.Sampler):
    """`length` frames an epoch, half of them damaged, without replacement while a class
    lasts. Yields (index, draw) so the augmentation differs every time a frame returns."""

    def __init__(self, targets, length, seed):
        self.positive = np.flatnonzero(targets >= 0.5)
        self.negative = np.flatnonzero(targets < 0.5)
        self.length, self.seed, self.epoch = length, seed, 0

    def __len__(self):
        return self.length

    def take(self, pool, count, rng):
        rounds = [rng.permutation(pool) for _ in range(-(-count // len(pool)))]
        return np.concatenate(rounds)[:count]

    def __iter__(self):
        rng = np.random.default_rng((self.seed, self.epoch))
        half = self.length // 2
        order = np.concatenate([self.take(self.positive, half, rng),
                                self.take(self.negative, self.length - half, rng)])
        rng.shuffle(order)
        draw = self.epoch * 1000
        return iter([(int(index), draw + position // max(1, len(self.positive)))
                     for position, index in enumerate(order)])


def forward(encoder, head, frames, frozen_blocks):
    body = encoder.body
    x = (frames.permute(0, 3, 1, 2).float() - encoder.mean) / encoder.std
    with torch.no_grad():
        x = body.bn1(body.conv_stem(x))
        for block in body.blocks[:frozen_blocks]:
            x = block(x)
    for block in body.blocks[frozen_blocks:]:
        x = block(x)
    if hasattr(body, "bn2"):  # EfficientNet ends its feature map with conv_head and bn2
        x = body.bn2(body.conv_head(x))
    return head(torch.cat([x.mean((2, 3)), x.amax((2, 3))], dim=1))


def trained_modules(body, frozen_blocks):
    # MobileNetV3 also has a conv_head, but it sits after the pooling and is not used.
    extra = [body.conv_head, body.bn2] if hasattr(body, "bn2") else []
    return list(body.blocks[frozen_blocks:]) + extra


def set_mode(encoder, head, frozen_blocks, training):
    encoder.eval()
    head.train(training)
    if training:
        for module in trained_modules(encoder.body, frozen_blocks):
            module.train()
            for layer in module.modules():
                if isinstance(layer, torch.nn.modules.batchnorm._BatchNorm):
                    layer.eval()


def score(encoder, head, paths, size, device, batch=32, workers=3):
    """Clean (unaugmented, letterboxed) scores, the way the model is served."""
    set_mode(encoder, head, 0, training=False)
    data = Frames(paths, np.zeros(len(paths), dtype=np.float32), size, augmented=False)
    loader = torch.utils.data.DataLoader(data, batch_size=batch, num_workers=workers)
    out = []
    with torch.inference_mode():
        for frames, _ in loader:
            out.append(torch.sigmoid(head(encoder(frames.to(device)))).float().cpu().numpy())
    return np.concatenate(out)


def focal_loss(logits, targets, gamma):
    """Binary focal loss that accepts soft targets (gamma 0 is cross-entropy)."""
    loss = torch.nn.functional.binary_cross_entropy_with_logits(logits, targets, reduction="none")
    if gamma:
        probability = torch.sigmoid(logits)
        wrongness = (targets * (1 - probability) + (1 - targets) * probability)
        loss = loss * wrongness.pow(gamma)
    return loss.mean()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--encoder", required=True, choices=["mobilenetv3_l", "efficientnet_b0"])
    parser.add_argument("--size", type=int, default=448)
    parser.add_argument("--name", required=True, help="output name under work/finetuned/")
    parser.add_argument("--head-from", help="a probe head (work/heads/*.pt) to start from")
    parser.add_argument("--frozen-blocks", type=int, default=2)
    parser.add_argument("--epochs", type=int, default=12)
    parser.add_argument("--epoch-frames", type=int, default=12000)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--lr", type=float, default=1e-4)
    parser.add_argument("--head-lr", type=float, default=5e-4)
    parser.add_argument("--weight-decay", type=float, default=0.05)
    parser.add_argument("--focal-gamma", type=float, default=0.0)
    parser.add_argument("--ema", type=float, default=0.999)
    parser.add_argument("--patience", type=int, default=3)
    parser.add_argument("--manifest", default=str(WORK / "manifest.jsonl"))
    parser.add_argument("--device", default="mps" if torch.backends.mps.is_available() else "cpu")
    args = parser.parse_args()
    torch.manual_seed(SEED)
    torch.set_num_threads(3)

    rows = [json.loads(line) for line in open(args.manifest)]
    train = [row for row in rows if row["split"] == "train"]
    validation = [row for row in rows if row["split"] == "validation"]
    targets = np.array([soft_target(row) for row in train], dtype=np.float32)
    truth = np.array([row["damaged"] for row in validation])
    out = WORK / "finetuned"
    out.mkdir(exist_ok=True)
    checkpoint_path = out / f"{args.name}.checkpoint.pt"

    encoder = Encoder(args.encoder, args.size).to(args.device)
    width = 2 * encoder.body.num_features
    if args.head_from:
        saved = torch.load(args.head_from)
        head = Head(saved["width"], hidden=saved["hidden"], dropout=0.3 if saved["hidden"] else 0.0)
        head.load_state_dict(saved["state"])
    else:
        head = Head(width, hidden=256, dropout=0.3)
    head.to(args.device)
    for parameter in encoder.parameters():
        parameter.requires_grad_(False)
    body_parameters = [p for module in trained_modules(encoder.body, args.frozen_blocks)
                       for p in module.parameters()]
    for parameter in body_parameters:
        parameter.requires_grad_(True)
    optimiser = torch.optim.AdamW([
        {"params": body_parameters, "lr": args.lr},
        {"params": head.layers.parameters(), "lr": args.head_lr},
    ], weight_decay=args.weight_decay)
    steps_per_epoch = args.epoch_frames // args.batch
    schedule = torch.optim.lr_scheduler.OneCycleLR(
        optimiser, max_lr=[args.lr, args.head_lr], total_steps=args.epochs * steps_per_epoch,
        pct_start=0.1)
    # The model that is scored and kept is the running average of the trained weights:
    # with batches of 8 the raw weights jitter from step to step.
    averaged = {"encoder": {k: v.detach().clone() for k, v in encoder.state_dict().items()},
                "head": {k: v.detach().clone() for k, v in head.state_dict().items()}}

    def update_average():
        with torch.no_grad():
            for name, module in (("encoder", encoder), ("head", head)):
                for key, value in module.state_dict().items():
                    if value.dtype.is_floating_point:
                        averaged[name][key].lerp_(value, 1 - args.ema)
                    else:
                        averaged[name][key].copy_(value)

    def validate():
        live = (encoder.state_dict(), head.state_dict())
        live = ({k: v.clone() for k, v in live[0].items()}, {k: v.clone() for k, v in live[1].items()})
        encoder.load_state_dict(averaged["encoder"])
        head.load_state_dict(averaged["head"])
        scores = score(encoder, head, [row["path"] for row in validation], args.size, args.device)
        encoder.load_state_dict(live[0])
        head.load_state_dict(live[1])
        point = at_threshold(scores, truth, threshold_for_recall(scores, truth, 0.98))
        return {"validation_cleared_at_98": point["cleared_share"],
                "validation_auc": auc(scores, truth)}

    history, best, start_epoch = [], {"value": -1.0, "epoch": None}, 0
    if checkpoint_path.exists():
        saved = torch.load(checkpoint_path, map_location=args.device)
        encoder.load_state_dict(saved["encoder"])
        head.load_state_dict(saved["head"])
        optimiser.load_state_dict(saved["optimiser"])
        schedule.load_state_dict(saved["schedule"])
        averaged = saved["averaged"]
        history, best, start_epoch = saved["history"], saved["best"], saved["epoch"] + 1
        print(f"resuming after epoch {saved['epoch']}", flush=True)
    else:
        first = validate()
        history.append({"epoch": -1, **first})
        print(f"start: {first}", flush=True)

    data = Frames([row["path"] for row in train], targets, args.size, augmented=True, seed=SEED)
    sampler = BalancedEpoch(targets, steps_per_epoch * args.batch, SEED)
    loader = torch.utils.data.DataLoader(data, batch_size=args.batch, sampler=sampler,
                                         num_workers=3, persistent_workers=True, drop_last=True)
    for epoch in range(start_epoch, args.epochs):
        sampler.epoch = epoch
        set_mode(encoder, head, args.frozen_blocks, training=True)
        running, started = 0.0, time.monotonic()
        for step, (frames, target) in enumerate(loader, 1):
            optimiser.zero_grad()
            logits = forward(encoder, head, frames.to(args.device), args.frozen_blocks)
            loss = focal_loss(logits, target.to(args.device), args.focal_gamma)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(body_parameters, 5.0)
            optimiser.step()
            schedule.step()
            update_average()
            running += loss.item()
            if step % 250 == 0:
                print(f"  epoch {epoch} step {step}/{steps_per_epoch} loss {running / step:.4f} "
                      f"{time.monotonic() - started:.0f}s", flush=True)
        entry = {"epoch": epoch, "train_loss": running / steps_per_epoch,
                 "seconds": round(time.monotonic() - started), **validate()}
        history.append(entry)
        print(json.dumps(entry), flush=True)
        if entry["validation_cleared_at_98"] > best["value"]:
            best = {"value": entry["validation_cleared_at_98"], "epoch": epoch}
            torch.save(averaged["encoder"], out / f"{args.name}.encoder.pt")
            torch.save({"state": averaged["head"], "hidden": 256 if len(head.layers) > 1 else 0,
                        "width": width}, WORK / "heads" / f"{args.encoder}_{args.size}_{args.name}.pt")
        torch.save({"encoder": encoder.state_dict(), "head": head.state_dict(),
                    "optimiser": optimiser.state_dict(), "schedule": schedule.state_dict(),
                    "averaged": averaged, "history": history, "best": best, "epoch": epoch},
                   checkpoint_path)
        write_json(WORK / "results" / f"finetune_{args.name}.json",
                   {"args": vars(args), "history": history, "best": best,
                    "train_frames": len(train), "validation_frames": len(validation)})
        if epoch - (best["epoch"] if best["epoch"] is not None else -1) >= args.patience:
            print(f"no improvement for {args.patience} epochs; stopping", flush=True)
            break
    print(f"best epoch {best['epoch']}: validation cleared at 98% recall {best['value']:.4f}")


if __name__ == "__main__":
    main()
