# Pothole detector

Owner's order, 9 Oct 2026: "I just need a damage detector, for potholes", trained on AWS,
two sizes. The screen in `ml/classifier/` learned gpt-5-mini's verdict of "any road
damage", which is why it is only sure of about a quarter of potholes. This one learns the
datasets' own human-drawn pothole boxes and answers "is there a pothole, and where".

| | |
|---|---|
| Models | YOLOX-Tiny (about 5 million parameters) and YOLOX-S (about 9 million), one class, 640 px |
| Code | YOLOX, Apache-2.0 (this repo is MIT; Ultralytics YOLO is AGPL and is not used) |
| Data | `prepare.py`: the public archives already mirrored in the ML bucket, their pothole boxes only; cracks, patches and clean road are negatives |
| Whole frames | downscaled to 1280 px at most, never cropped; mosaic and mixup are off because they cut pictures up (`AGENTS.md`) |
| Drawn-on pictures | `scan_overlays.py` drops any training picture that looks to have a box rendered into its pixels |
| Held out | IRDD (a whole dataset), RDD2022 India blocks, the owner's drive frames, RAD test videos |
| Scorecard | `evaluate.py`: per picture, cut-offs from validation only |

## Bar

A model is worth putting in front of gpt-5-mini only if, on IRDD and on the India blocks,
it catches at least 95% of the pictures with a pothole while at least 95% of its calls
are right, and it answers in under 500 ms on the Lambda. Otherwise the report says so
and nothing ships.

## Result of run pothole-det-20261009 (9 Oct 2026)

Neither model meets the bar, so neither answers alone. Pictures with a pothole caught, and
calls that were right, at a cut-off of 0.3:

| Model | Parameters | India held-out | IRDD | Clean owner frames called | Forward pass, 2 threads |
|---|---|---|---|---|---|
| `pothole_tiny` | 5,032,866 | 59% caught, 80% right | 29%, 58% | 1 of 858 | 127 ms |
| `pothole_s` | 8,937,682 | 64% caught, 79% right | 32%, 58% | 2 of 858 | 194 ms |

Both overfit early (best epoch 12 and 15 of 45). Nearly doubling the model moved nothing
that matters, so the limit is the labelled pictures, not the size. `pothole_tiny` is the
one in production, only to outline a pothole the judging model has already called
(`lambda/`); `pothole_s` was not deployed. Full scorecard: `runs/pothole-det-20261009/report/report.md`.

## Run it

```bash
ml/detector/cloud/supervise.sh      # from the Mac: launches, watches, relaunches after a spot reclaim
aws s3 cp s3://pothole-reporter-ml-695656921622-ap-south-1/runs/pothole-det-20261009/progress.md -
ml/classifier/cloud/teardown.sh     # always, pass or fail
```

Everything is computed on the instance and kept in the bucket: `runs/pothole-det-20261009/`
(`progress.md`, `logs/`, `report/`, `debug/`, `state/`) and `models/pothole-det-20261009/`.
