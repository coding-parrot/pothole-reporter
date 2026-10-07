#!/usr/bin/env python3
"""Export encoder + head as one ONNX graph and prove it equals the torch model.

    python export_onnx.py --encoder efficientnet_b0 --size 448 --head hidden256

Input "frames": uint8 [1, size, size, 3], the letterboxed whole frame. Output "score":
float32 [1], the probability of road damage. The check runs torch and ONNX Runtime on
every test-split frame and fails the export if any score differs by more than 1e-4.
"""
import argparse
import json

import numpy as np
import torch
from PIL import Image

from common import FRAMES, WORK, sha256_hex, write_json
from models import Encoder, Head, Screen, letterbox


def build(encoder_name, size, head_kind, weights=None):
    saved = torch.load(WORK / "heads" / f"{encoder_name}_{size}_{head_kind}.pt")
    head = Head(saved["width"], hidden=saved["hidden"])
    head.load_state_dict(saved["state"])
    encoder = Encoder(encoder_name, size)
    if weights:  # a fine-tuned encoder body
        encoder.body.load_state_dict(torch.load(weights))
    return Screen(encoder, head).eval()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--encoder", required=True)
    parser.add_argument("--size", type=int, required=True)
    parser.add_argument("--head", default="hidden256")
    parser.add_argument("--weights", help="fine-tuned encoder body state dict")
    parser.add_argument("--limit", type=int, default=0, help="check only this many frames")
    args = parser.parse_args()
    import onnxruntime

    model = build(args.encoder, args.size, args.head, args.weights)
    name = f"{args.encoder}_{args.size}_{args.head}"
    target = WORK / "onnx" / f"{name}.onnx"
    target.parent.mkdir(exist_ok=True)
    example = torch.zeros(1, args.size, args.size, 3, dtype=torch.uint8)
    torch.onnx.export(model, (example,), str(target), input_names=["frames"],
                      output_names=["score"], opset_version=17, dynamo=False,
                      do_constant_folding=True)
    session = onnxruntime.InferenceSession(str(target), providers=["CPUExecutionProvider"])

    rows = [json.loads(line) for line in (WORK / "manifest.jsonl").read_text().splitlines()]
    rows = [row for row in rows if row["split"] == "test"]
    if args.limit:
        rows = rows[:: max(1, len(rows) // args.limit)]
    # ONNX Runtime runs on the CPU, as it does in Lambda. torch runs on the CPU too on
    # the training instance (8 fast cores); on the Mac its CPU needs 0.4 s a frame for a
    # depthwise CNN, so there it runs on the Apple GPU.
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model.to(device)
    torch_scores, onnx_scores = [], []
    with torch.inference_mode():
        for start in range(0, len(rows), 32):
            frames = np.stack([letterbox(Image.open(FRAMES / row["path"]), args.size)
                               for row in rows[start:start + 32]])
            torch_scores += model(torch.from_numpy(frames).to(device)).float().cpu().tolist()
            onnx_scores += [float(session.run(["score"], {"frames": frame[None]})[0][0])
                            for frame in frames]
    worst = float(np.abs(np.array(torch_scores) - np.array(onnx_scores)).max())
    receipt = {
        "model": name, "onnx_bytes": target.stat().st_size,
        "onnx_sha256": sha256_hex(target.read_bytes()),
        "frames_compared": len(rows), "torch_device": device,
        "max_abs_score_difference": worst,
        "mean_abs_score_difference": float(np.mean(np.abs(np.array(torch_scores) - np.array(onnx_scores)))),
    }
    write_json(WORK / "onnx" / f"{name}.parity.json", receipt)
    print(json.dumps(receipt, indent=1))
    if worst > 1e-4:
        raise SystemExit("ONNX and torch disagree by more than 1e-4")


if __name__ == "__main__":
    main()
