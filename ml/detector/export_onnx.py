#!/usr/bin/env python3
"""Write a trained detector as ONNX and check the file against the PyTorch model.

    python export_onnx.py pothole_tiny /opt/ml/det/export/pothole_tiny.onnx

YOLOX's own exporter calls torch.onnx._export, which PyTorch 2.7 no longer has. The
output is decoded in the graph: one row per candidate box, (cx, cy, w, h, objectness,
pothole score) in input pixels, for a 1 x 3 x 640 x 640 input in BGR, 0 to 255, letterboxed
with grey 114 exactly as yolox.data.ValTransform prepares it. The caller multiplies the
last two and runs non-maximum suppression.
"""
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import torch
from torch import nn

HERE = Path(__file__).resolve().parent
RUNS = Path(os.environ.get("DET_RUNS", "/opt/ml/det/runs"))


def main():
    name, target = sys.argv[1], sys.argv[2]
    sys.path.insert(0, str(HERE / "exps"))
    from yolox.exp import get_exp
    from yolox.models.network_blocks import SiLU
    from yolox.utils import replace_module

    exp = get_exp(str(HERE / "exps" / f"{name}.py"), None)
    model = exp.get_model()
    checkpoint = torch.load(RUNS / name / "best_ckpt.pth", map_location="cpu", weights_only=False)
    model.load_state_dict(checkpoint["model"])
    model.eval()
    model = replace_module(model, nn.SiLU, SiLU)
    model.head.decode_in_inference = True
    frame = torch.rand(1, 3, *exp.test_size) * 255
    with torch.no_grad():
        expected = model(frame).numpy()
    torch.onnx.export(model, frame, target, input_names=["images"], output_names=["output"],
                      opset_version=13, dynamo=False)

    import onnxruntime as ort

    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    session = ort.InferenceSession(target, options, providers=["CPUExecutionProvider"])
    got = session.run(None, {"images": frame.numpy()})[0]
    difference = float(np.abs(got - expected).max())
    times = []
    for _ in range(12):
        started = time.perf_counter()
        session.run(None, {"images": frame.numpy()})
        times.append((time.perf_counter() - started) * 1000)
    receipt = {"model": name, "onnx_bytes": os.path.getsize(target), "output_shape": list(got.shape),
               "largest_difference_from_torch": round(difference, 5),
               "onnxruntime_ms_two_threads_640": round(sorted(times[2:])[5], 1),
               "parameters": sum(p.numel() for p in model.parameters())}
    json.dump(receipt, open(target[:-5] + ".json", "w"), indent=1)
    print(json.dumps(receipt))
    if difference > 0.05:
        sys.exit(f"the ONNX file differs from the PyTorch model by {difference}")


if __name__ == "__main__":
    main()
