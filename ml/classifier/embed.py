#!/usr/bin/env python3
"""Run a frozen encoder over every usable frame and cache the pooled features.

    python embed.py --encoder mobilenetv3_l --size 448             # clean frames
    python embed.py --encoder mobilenetv3_l --size 448 --views 3   # plus augmented views

Clean features go to work/embeddings/<encoder>_<size>.npz (float16 features and their
frame paths). The cache is incremental: only frames it does not hold are computed, so
adding a dataset costs that dataset. With --views N, N augmented views (augment.py) of
every TRAINING frame in the manifest go to <encoder>_<size>_aug<view>.npz; validation
and test frames are never augmented.
"""
import argparse
import json
import time
import zlib

import numpy as np
import torch
from PIL import Image

from augment import augment
from common import EMBEDDINGS, FRAMES, WORK
from models import ENCODERS, Encoder


class Frames(torch.utils.data.Dataset):
    def __init__(self, paths, size, view=None):
        self.paths, self.size, self.view = paths, size, view

    def __len__(self):
        return len(self.paths)

    def __getitem__(self, index):
        path = self.paths[index]
        # The view's randomness depends on the frame, not on its position in the list,
        # so a rerun over a longer list reproduces the views it already made.
        rng = (None if self.view is None
               else np.random.default_rng((20261007, self.view, zlib.crc32(path.encode()))))
        return torch.from_numpy(np.ascontiguousarray(
            augment(Image.open(FRAMES / path), self.size, rng)))


def embed(encoder, paths, size, device, batch, view=None):
    loader = torch.utils.data.DataLoader(Frames(paths, size, view), batch_size=batch, num_workers=3)
    chunks = []
    started = time.monotonic()
    with torch.inference_mode():
        for count, frames in enumerate(loader, 1):
            chunks.append(encoder(frames.to(device)).float().cpu().numpy().astype(np.float16))
            if count % 100 == 0:
                print(f"  {count * batch}/{len(paths)} {time.monotonic() - started:.0f}s", flush=True)
    return np.concatenate(chunks)


def update(target, wanted, compute):
    """Bring the cache at `target` to exactly `wanted`, computing only what is missing."""
    have = {}
    if target.exists():
        cached = np.load(target, allow_pickle=False)
        have = {path: row for path, row in zip(cached["paths"], cached["features"])}
    missing = [path for path in wanted if path not in have]
    if missing:
        for path, row in zip(missing, compute(missing)):
            have[path] = row
        # Written whole to a temporary name: an interrupted run leaves the old cache.
        temporary = target.with_suffix(".tmp.npz")
        np.savez(temporary, features=np.stack([have[path] for path in wanted]),
                 paths=np.array(wanted))
        temporary.replace(target)
    print(f"{target.name}: {len(wanted)} frames, {len(missing)} computed now", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--encoder", choices=sorted(ENCODERS), required=True)
    parser.add_argument("--size", type=int, required=True)
    parser.add_argument("--views", type=int, default=0, help="augmented views of training frames")
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--device", default="mps" if torch.backends.mps.is_available() else "cpu")
    args = parser.parse_args()
    EMBEDDINGS.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(3)
    encoder = Encoder(args.encoder, args.size).eval().to(args.device)
    # Every frame that has or can get a label. The unannotated RDD2022 India test images
    # were never labelled.
    paths = [row["path"] for row in map(json.loads, (FRAMES / "index.jsonl").read_text().splitlines())
             if row["source"] != "rdd2022-india-test"]
    update(EMBEDDINGS / f"{args.encoder}_{args.size}.npz", paths,
           lambda missing: embed(encoder, missing, args.size, args.device, args.batch))
    if args.views:
        train = [row["path"] for row in map(json.loads, (WORK / "manifest.jsonl").read_text().splitlines())
                 if row["split"] == "train"]
        for view in range(args.views):
            update(EMBEDDINGS / f"{args.encoder}_{args.size}_aug{view}.npz", train,
                   lambda missing, view=view: embed(encoder, missing, args.size, args.device,
                                                    args.batch, view))


if __name__ == "__main__":
    main()
