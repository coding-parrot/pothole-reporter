#!/usr/bin/env python3
"""Run a frozen encoder over every usable frame and cache the pooled features.

    python embed.py --encoder dinov2_s14 --size 448

Writes work/embeddings/<encoder>_<size>.npz (float16 features and their frame paths).
"""
import argparse
import json
import time

import numpy as np
import torch
from PIL import Image

from common import EMBEDDINGS, FRAMES
from models import ENCODERS, Encoder, letterbox


class Frames(torch.utils.data.Dataset):
    def __init__(self, paths, size):
        self.paths, self.size = paths, size

    def __len__(self):
        return len(self.paths)

    def __getitem__(self, index):
        return torch.from_numpy(letterbox(Image.open(FRAMES / self.paths[index]), self.size))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--encoder", choices=sorted(ENCODERS), required=True)
    parser.add_argument("--size", type=int, required=True)
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--device", default="mps" if torch.backends.mps.is_available() else "cpu")
    args = parser.parse_args()
    # Every frame that can get a label: the videos and the annotated RDD images. This
    # does not wait for the teacher, so it can run while labelling does.
    paths = [row["path"] for row in map(json.loads, (FRAMES / "index.jsonl").read_text().splitlines())
             if row["source"] != "rdd2022-india-test"]
    target = EMBEDDINGS / f"{args.encoder}_{args.size}.npz"
    if target.exists() and list(np.load(target, allow_pickle=False)["paths"]) == paths:
        print("up to date", target)
        return
    EMBEDDINGS.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(3)
    encoder = Encoder(args.encoder, args.size).eval().to(args.device)
    loader = torch.utils.data.DataLoader(Frames(paths, args.size), batch_size=args.batch,
                                         num_workers=3)
    chunks = []
    started = time.monotonic()
    with torch.inference_mode():
        for count, frames in enumerate(loader, 1):
            chunks.append(encoder(frames.to(args.device)).float().cpu().numpy().astype(np.float16))
            if count % 100 == 0:
                print(f"{count * args.batch}/{len(paths)} "
                      f"{time.monotonic() - started:.0f}s", flush=True)
    features = np.concatenate(chunks)
    np.savez(target, features=features, paths=np.array(paths))
    print(f"{target.name}: {features.shape} in {time.monotonic() - started:.0f}s on {args.device}")


if __name__ == "__main__":
    main()
