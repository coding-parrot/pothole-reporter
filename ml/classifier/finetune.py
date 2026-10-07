#!/usr/bin/env python3
"""Fine-tune a small CNN encoder on whole frames with real augmentation.

    python finetune.py --encoder mobilenetv3_l --size 448 --name ft_mnv3_s1 --seed 1

v1 could only train the last blocks on cached feature maps, with no augmentation, and
it memorised. This trains on the images: every step sees freshly augmented whole frames
(augment.py: colour, blur, JPEG, resolution, flip, shrink, perspective; never a crop).

On a GPU (the T4 this was run on) the whole network trains, batch 32, mixed precision,
batch-norm statistics included. On the 8 GB Mac the same script runs with
--frozen-blocks 2 --batch 8 --bn-frozen (0.40 s a step), which is why training moved.

Sampling is class-balanced per epoch (half the frames damaged), targets are soft where
the teacher answered twice, the loss is binary cross-entropy or focal. After every epoch
the held-out-source validation split is scored on clean frames with the running average
of the weights; the epoch kept is the one that clears most undamaged validation frames
at 98% recall, and training stops when that has not improved for --patience epochs.
Every epoch is checkpointed, so an interrupted run resumes.

Writes work/finetuned/<encoder>_<size>_<name>.body.pt, work/heads/<encoder>_<size>_<name>.pt and
work/scores/<encoder>_<size>_<name>.npz (every manifest frame and the owner images).
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
from models import Encoder, Head, default_device


class Frames(torch.utils.data.Dataset):
    """item -> (uint8 canvas, target). With augmentation every draw is different."""

    def __init__(self, paths, targets, size, augmented, seed=0):
        self.paths, self.targets, self.size = paths, targets, size
        self.augmented, self.seed = augmented, seed

    def __len__(self):
        return len(self.paths)

    def __getitem__(self, item):
        index, draw = item if isinstance(item, tuple) else (item, 0)
        rng = np.random.default_rng((self.seed, draw, index)) if self.augmented else None
        pixels = augment(Image.open(FRAMES / self.paths[index]), self.size, rng)
        return torch.from_numpy(np.array(pixels)), self.targets[index]


class BalancedEpoch(torch.utils.data.Sampler):
    """`length` frames an epoch, half of them damaged. Yields (index, draw) so the
    augmentation differs every time a frame comes round again."""

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
        return iter([(int(index), self.epoch * 100000 + position)
                     for position, index in enumerate(order)])


def trained_modules(body, frozen_blocks):
    """frozen_blocks < 0 trains everything; otherwise the stem and that many stages stay
    as pretrained. MobileNetV3's conv_head sits after the pooling and is not used."""
    tail = [body.conv_head, body.bn2] if hasattr(body, "bn2") else []
    if frozen_blocks < 0:
        return [body.conv_stem, body.bn1, *body.blocks, *tail]
    return [*body.blocks[frozen_blocks:], *tail]


def forward(encoder, head, frames, frozen_blocks):
    body = encoder.body
    x = (frames.permute(0, 3, 1, 2).float() - encoder.mean) / encoder.std
    x = x.contiguous(memory_format=torch.channels_last)
    with torch.set_grad_enabled(frozen_blocks < 0):
        x = body.bn1(body.conv_stem(x))
        for block in body.blocks[:max(frozen_blocks, 0)]:
            x = block(x)
    for block in body.blocks[max(frozen_blocks, 0):]:
        x = block(x)
    if hasattr(body, "bn2"):  # EfficientNet ends its feature map with conv_head and bn2
        x = body.bn2(body.conv_head(x))
    x = x.float()
    return head(torch.cat([x.mean((2, 3)), x.amax((2, 3))], dim=1))


def set_mode(encoder, head, frozen_blocks, training, bn_frozen):
    encoder.eval()
    head.train(training)
    if training:
        for module in trained_modules(encoder.body, frozen_blocks):
            module.train()
            if bn_frozen:
                for layer in module.modules():
                    if isinstance(layer, torch.nn.modules.batchnorm._BatchNorm):
                        layer.eval()


def score(encoder, head, paths, size, device, batch=64, workers=6):
    """Clean (unaugmented, letterboxed) scores, the way the model is served."""
    encoder.eval()
    head.eval()
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
        wrongness = targets * (1 - probability) + (1 - targets) * probability
        loss = loss * wrongness.pow(gamma)
    return loss.mean()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--encoder", required=True, choices=["mobilenetv3_l", "efficientnet_b0"])
    parser.add_argument("--size", type=int, default=448)
    parser.add_argument("--name", required=True, help="output name")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--head-from", help="a probe head name (work/heads/<encoder>_<size>_<it>.pt)")
    parser.add_argument("--frozen-blocks", type=int, default=-1, help="-1 trains the whole encoder")
    parser.add_argument("--bn-frozen", action="store_true",
                        help="keep batch-norm running statistics (for batches too small to estimate them)")
    parser.add_argument("--epochs", type=int, default=14)
    parser.add_argument("--epoch-frames", type=int, default=24000)
    parser.add_argument("--batch", type=int, default=32)
    parser.add_argument("--lr", type=float, default=2e-4)
    parser.add_argument("--head-lr", type=float, default=1e-3)
    parser.add_argument("--weight-decay", type=float, default=0.05)
    parser.add_argument("--focal-gamma", type=float, default=0.0)
    parser.add_argument("--ema", type=float, default=0.999)
    parser.add_argument("--patience", type=int, default=4)
    parser.add_argument("--workers", type=int, default=7)
    parser.add_argument("--device", default=default_device())
    args = parser.parse_args()
    torch.manual_seed(args.seed)
    torch.backends.cudnn.benchmark = True  # one input size throughout
    amp = args.device == "cuda"

    rows = [json.loads(line) for line in (WORK / "manifest.jsonl").read_text().splitlines()]
    train = [row for row in rows if row["split"] == "train"]
    validation = [row for row in rows if row["split"] == "validation"]
    targets = np.array([soft_target(row) for row in train], dtype=np.float32)
    truth = np.array([row["damaged"] for row in validation])
    validation_domain = np.array([row["domain"] for row in validation])
    out = WORK / "finetuned"
    out.mkdir(exist_ok=True)
    (WORK / "scores").mkdir(exist_ok=True)
    stem = f"{args.encoder}_{args.size}_{args.name}"
    checkpoint_path = out / f"{stem}.checkpoint.pt"

    encoder = Encoder(args.encoder, args.size).to(args.device).to(memory_format=torch.channels_last)
    width = 2 * encoder.body.num_features
    hidden = 256
    head = Head(width, hidden=hidden, dropout=0.3)
    if args.head_from:
        saved = torch.load(WORK / "heads" / f"{args.encoder}_{args.size}_{args.head_from}.pt")
        hidden = saved["hidden"]
        head = Head(saved["width"], hidden=hidden, dropout=0.3 if hidden else 0.0)
        head.load_state_dict(saved["state"])
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
    scaler = torch.amp.GradScaler("cuda", enabled=amp)
    # The model that is scored and kept is the running average of the trained weights.
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

    def with_average(function):
        live = ({k: v.clone() for k, v in encoder.state_dict().items()},
                {k: v.clone() for k, v in head.state_dict().items()})
        encoder.load_state_dict(averaged["encoder"])
        head.load_state_dict(averaged["head"])
        try:
            return function()
        finally:
            encoder.load_state_dict(live[0])
            head.load_state_dict(live[1])

    def validate():
        scores = with_average(lambda: score(encoder, head, [row["path"] for row in validation],
                                            args.size, args.device, workers=args.workers))
        threshold = threshold_for_recall(scores, truth, 0.98)
        point = at_threshold(scores, truth, threshold)
        by_domain = {}
        for domain in sorted(set(validation_domain)):
            chosen = validation_domain == domain
            part = at_threshold(scores[chosen], truth[chosen], threshold)
            by_domain[domain] = {"recall": part["recall"], "cleared_share": part["cleared_share"]}
        return {"validation_cleared_at_98": point["cleared_share"],
                "validation_auc": auc(scores, truth), "validation_by_domain": by_domain}

    history, best, start_epoch = [], {"value": -1.0, "epoch": None}, 0
    if checkpoint_path.exists():
        saved = torch.load(checkpoint_path, map_location=args.device)
        encoder.load_state_dict(saved["encoder"])
        head.load_state_dict(saved["head"])
        optimiser.load_state_dict(saved["optimiser"])
        schedule.load_state_dict(saved["schedule"])
        scaler.load_state_dict(saved["scaler"])
        averaged = saved["averaged"]
        history, best, start_epoch = saved["history"], saved["best"], saved["epoch"] + 1
        print(f"resuming after epoch {saved['epoch']}", flush=True)
    else:
        first = validate()
        history.append({"epoch": -1, **first})
        print(f"start: {json.dumps(first)}", flush=True)

    def keep_best():
        body = {key[5:]: value.cpu() for key, value in averaged["encoder"].items()
                if key.startswith("body.")}
        torch.save(body, out / f"{stem}.body.pt")
        torch.save({"state": {k: v.cpu() for k, v in averaged["head"].items()},
                    "hidden": hidden, "width": width}, WORK / "heads" / f"{stem}.pt")

    data = Frames([row["path"] for row in train], targets, args.size, augmented=True, seed=args.seed)
    sampler = BalancedEpoch(targets, steps_per_epoch * args.batch, args.seed)
    loader = torch.utils.data.DataLoader(data, batch_size=args.batch, sampler=sampler,
                                         num_workers=args.workers, persistent_workers=True,
                                         drop_last=True, prefetch_factor=4)
    stopped = any(entry.get("stopped") for entry in history)
    for epoch in range(start_epoch, args.epochs):
        if stopped:
            break
        sampler.epoch = epoch
        set_mode(encoder, head, args.frozen_blocks, training=True, bn_frozen=args.bn_frozen)
        running, started = 0.0, time.monotonic()
        for step, (frames, target) in enumerate(loader, 1):
            optimiser.zero_grad(set_to_none=True)
            with torch.autocast("cuda", dtype=torch.float16, enabled=amp):
                logits = forward(encoder, head, frames.to(args.device, non_blocking=True),
                                 args.frozen_blocks)
            loss = focal_loss(logits.float(), target.to(args.device), args.focal_gamma)
            scaler.scale(loss).backward()
            scaler.unscale_(optimiser)
            torch.nn.utils.clip_grad_norm_(body_parameters, 5.0)
            scaler.step(optimiser)
            scaler.update()
            schedule.step()
            update_average()
            running += loss.item()
            if step % 250 == 0:
                print(f"  epoch {epoch} step {step}/{steps_per_epoch} loss {running / step:.4f} "
                      f"{time.monotonic() - started:.0f}s", flush=True)
        entry = {"epoch": epoch, "train_loss": running / steps_per_epoch,
                 "seconds": round(time.monotonic() - started), **validate()}
        if entry["validation_cleared_at_98"] > best["value"]:
            best = {"value": entry["validation_cleared_at_98"], "epoch": epoch}
            keep_best()
        if epoch - (best["epoch"] if best["epoch"] is not None else -1) >= args.patience:
            entry["stopped"] = stopped = True
        history.append(entry)
        print(json.dumps(entry), flush=True)
        torch.save({"encoder": encoder.state_dict(), "head": head.state_dict(),
                    "optimiser": optimiser.state_dict(), "schedule": schedule.state_dict(),
                    "scaler": scaler.state_dict(), "averaged": averaged, "history": history,
                    "best": best, "epoch": epoch}, checkpoint_path)
    if best["epoch"] is None:
        # No epoch beat the starting point: keep the start (the pretrained encoder and head).
        raise SystemExit("fine-tuning never improved on its starting point; nothing kept")

    # Score everything with the kept weights, through the same clean path.
    encoder.body.load_state_dict(torch.load(out / f"{stem}.body.pt"))
    kept = torch.load(WORK / "heads" / f"{stem}.pt")
    head.load_state_dict(kept["state"])
    owner = [row["path"] for row in map(json.loads, (FRAMES / "index.jsonl").read_text().splitlines())
             if row.get("split_hint") == "owner"]
    paths = [row["path"] for row in rows] + owner
    scores = score(encoder, head, paths, args.size, args.device, workers=args.workers)
    np.savez(WORK / "scores" / f"{stem}.npz", paths=np.array(paths), scores=scores.astype(np.float32))
    write_json(WORK / "results" / f"finetune_{stem}.json",
               {"args": vars(args), "history": history, "best": best,
                "train_frames": len(train), "validation_frames": len(validation)})
    checkpoint_path.unlink()  # the kept weights and the history are what matter now
    print(f"best epoch {best['epoch']}: validation cleared at 98% recall {best['value']:.4f}")


if __name__ == "__main__":
    main()
