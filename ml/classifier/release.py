#!/usr/bin/env python3
"""Turn one trained model into the served artifact and score it through the serving path.

    python release.py --encoder mobilenetv3_l --size 448 --head ft_s1 \
        --weights work/finetuned/ft_s1.body.pt --version road-screen-v2-mobilenetv3l-448

1. Exports the ONNX (export_onnx.py proves it equals torch on the whole test split).
2. Scores validation, test and the owner-labelled images through the SERVING path
   (lambda/scorer.mjs: sharp + ONNX Runtime), not through PIL, with no temperature.
   The scores go to work/scores/<name>.npz so evaluate.py judges exactly what ships.
3. Fits the temperature on validation and sets the threshold there, at --target-recall
   of teacher-damaged frames. Test is never used for either.
4. Writes work/release/<version>/{model.onnx, model.json, parity.json}.

--existing scores an ONNX that already exists (v1's) the same way, so the two models
are compared on scores from one path.
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

from common import FRAMES, HERE, WORK, read_json, sha256_hex, write_json
from metrics import at_threshold, threshold_for_recall

LOGIT_LIMIT = 30  # the same clamp as lambda/scorer.mjs soften()


def node_scores(model_dir, paths):
    done = subprocess.run(["node", str(HERE / "lambda" / "score-frames.mjs"), str(model_dir), str(FRAMES)],
                          input="\n".join(paths) + "\n", capture_output=True, text=True)
    if done.returncode:
        sys.exit(done.stderr[-2000:])
    rows = [json.loads(line) for line in done.stdout.splitlines()]
    assert [row["path"] for row in rows] == list(paths)
    return np.array([row["score"] for row in rows]), rows


def soften(scores, temperature):
    logits = np.clip(np.log(scores / (1 - scores)), -LOGIT_LIMIT, LOGIT_LIMIT)
    return 1 / (1 + np.exp(-logits / temperature))


def fit_temperature(scores, labels):
    """The temperature that minimises validation log loss. Order is unchanged."""
    best = (np.inf, 1.0)
    for temperature in np.geomspace(1, 64, 61):
        p = np.clip(soften(scores, temperature), 1e-7, 1 - 1e-7)
        loss = -np.mean(np.where(labels, np.log(p), np.log(1 - p)))
        best = min(best, (loss, float(temperature)))
    return round(best[1], 3)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--encoder")
    parser.add_argument("--size", type=int, default=448)
    parser.add_argument("--head")
    parser.add_argument("--weights")
    parser.add_argument("--version", required=True)
    parser.add_argument("--existing", help="an ONNX file to score as it is (v1's)")
    parser.add_argument("--target-recall", type=float, default=0.99,
                        help="validation recall the released threshold is set for")
    args = parser.parse_args()

    out = WORK / "release" / args.version
    out.mkdir(parents=True, exist_ok=True)
    if args.existing:
        shutil.copyfile(args.existing, out / "model.onnx")
        parity = {"onnx_sha256": sha256_hex((out / "model.onnx").read_bytes())}
    else:
        name = f"{args.encoder}_{args.size}_{args.head}"
        command = [sys.executable, str(HERE / "export_onnx.py"), "--encoder", args.encoder,
                   "--size", str(args.size), "--head", args.head]
        if args.weights:
            command += ["--weights", args.weights]
        subprocess.run(command, check=True)
        parity = read_json(WORK / "onnx" / f"{name}.parity.json")
        shutil.copyfile(WORK / "onnx" / f"{name}.onnx", out / "model.onnx")
        write_json(out / "parity.json", parity)
    # Temperature 1 first: raw scores, the same scale for every model compared.
    meta = {"model_version": args.version, "sha256": parity["onnx_sha256"],
            "input_size": args.size, "threshold": 0.5}
    write_json(out / "model.json", meta)

    rows = [json.loads(line) for line in (WORK / "manifest.jsonl").read_text().splitlines()]
    rows = [row for row in rows if row["split"] in ("validation", "test")]
    owner = [row["path"] for row in map(json.loads, (FRAMES / "index.jsonl").read_text().splitlines())
             if row.get("split_hint") == "owner"]
    paths = [row["path"] for row in rows] + owner
    raw, timing = node_scores(out, paths)
    (WORK / "scores").mkdir(exist_ok=True)
    np.savez(WORK / "scores" / f"served_{args.version}.npz", paths=np.array(paths),
             scores=raw.astype(np.float64))

    labels = np.array([row["damaged"] for row in rows])
    validation = np.array([row["split"] == "validation" for row in rows])
    scores = raw[:len(rows)]
    report = {"model_version": args.version, "onnx": parity,
              "frames_scored": len(paths), "scoring_path": "lambda/scorer.mjs (sharp letterbox, ONNX Runtime)",
              "local_scoring_ms": {
                  "decode_p50": float(np.median([row["decode_ms"] for row in timing])),
                  "infer_p50": float(np.median([row["infer_ms"] for row in timing]))}}
    if not args.existing:
        temperature = fit_temperature(scores[validation], labels[validation])
        softened = soften(scores, temperature)
        threshold = threshold_for_recall(softened[validation], labels[validation], args.target_recall)
        meta.update({"temperature": temperature, "threshold": threshold, "encoder": args.encoder,
                     "head": args.head, "target_validation_recall": args.target_recall})
        write_json(out / "model.json", meta)
        # The served path now applies the temperature itself. Prove it gives these numbers.
        step = max(1, len(rows) // 60)
        served, _ = node_scores(out, [row["path"] for row in rows[::step]])
        drift = float(np.abs(served - softened[::step]).max())
        if drift > 1e-6:
            sys.exit(f"the served temperature differs from release.py's by {drift}")
        report.update({
            "temperature": temperature, "threshold": threshold,
            "threshold_rule": f"highest score that still flags {args.target_recall:.0%} of "
                              "teacher-damaged validation frames, scored through the serving path",
            "thresholds_softened": {
                f"val{round(100 * t)}": threshold_for_recall(softened[validation], labels[validation], t)
                for t in (0.99, 0.98, 0.95)},
            "at_released_threshold": {
                "validation": at_threshold(softened[validation], labels[validation], threshold),
                "test": at_threshold(softened[~validation], labels[~validation], threshold)}})
    write_json(out / "release.json", report)
    print(json.dumps({k: v for k, v in report.items() if k != "onnx"}, indent=1))


if __name__ == "__main__":
    main()
