# Road screen v1 (`road-screen-v1-mobilenetv3l-448`)

An encoder-only image classifier that scores one Drive Mode frame for road damage in
about 75 ms on Lambda. It exists to answer the 77% of drive frames gpt-5-mini calls
undamaged without a 2.1 s gpt-5-mini call. It is deployed for SHADOW use only.

**It does not meet the bar it was built for.** The bar was: flag at least 98% of the
frames gpt-5-mini judges damaged on held-out sources. At the released threshold it flags
94.0% (532 of 566) and clears 56.5% of undamaged frames (1,070 of 1,893). On held-out
drive video it flags 82.5% (113 of 137). Do not switch `yolo_then_openai` on with this
model on the strength of this card. Shadow mode will say what it does on live frames.

| | |
|---|---|
| Model | MobileNetV3-Large (timm `mobilenetv3_large_100.ra_in1k`, frozen) + one hidden layer of 256 |
| Input | the whole frame, letterboxed to 448 x 448 (long edge scaled to 448, rest padded) |
| Output | `score` in 0..1; damaged when `score >= 0.0470` |
| ONNX | 13,864,690 bytes, sha256 `75bb630aed191893cc4add1f6aa395aad694c6fafaf5b439f5e33186815c1cc4` |
| Published | `s3://pothole-reporter-central-695656921622-ap-south-1/models/road-screen-v1-mobilenetv3l-448.onnx` (and `.json`) |
| Served by | `arn:aws:lambda:ap-south-1:695656921622:function:pothole-reporter-central-screen` |
| Trained | 7 Oct 2026 |

## Data

| Source | Frames used | Licence | Notes |
|---|---|---|---|
| Owner's Bengaluru drive videos, `~/Desktop/pothole video segments/` (30 clips, 1,658 s) | 3,316 | private, owner's own footage | 480 x 720, sampled at 2 frames a second |
| Owner's two construction-drive clips, `~/Downloads/segment_0001.mp4`, `segment_0002.mp4` (109 s) | 218 | private, owner's own footage | hold the two owner-confirmed pothole events; test only |
| RDD2022 India, annotated images | 7,706 | CC BY 4.0 on the figshare record; the Sekimoto Lab README says CC BY-SA 4.0 for the images | 720 x 720, car-mounted phone |

No frame, label file or weight is committed. Working data lives in `ml/classifier/work/`
(gitignored).

RDD2022: Arya, Maeda, Ghosh, Toshniwal, Sekimoto, "RDD2022: A multi-national image
dataset for automatic road damage detection", Geoscience Data Journal 11(4), 2024.
Fetched on 7 Oct 2026 from the figshare record
<https://doi.org/10.6084/m9.figshare.21431547.v1> (one zip of 13,264,172,619 bytes, md5
`b62bd51d2ffcfaa76c60f234f0cc2bb3` as reported by figshare). Only the member
`RDD2022/India.zip` was downloaded, by HTTP range: 526,709,145 bytes, sha256
`28eab0aa85638855e1907e63b175be2305a0accd018c54603037d3fb8cab963a`, CRC checked against
the outer zip. The per-country link on the Sekimoto Lab README
(`bigdatacup.s3.ap-northeast-1.amazonaws.com/.../RDD2022_India.zip`, 502.3 MB) answered
403 that day. The 1,959 unannotated India test images were not used.

The two licence statements differ. Both allow this use with attribution. If the
ShareAlike reading applies, it binds anyone redistributing the images or adaptations of
them; whether trained weights count is not settled. That is the owner's call before the
weights leave this account.

Pretrained weights (all Apache-2.0 on their Hugging Face model cards, checked 7 Oct
2026): `timm/mobilenetv3_large_100.ra_in1k` (the released model),
`timm/efficientnet_b0.ra_in1k`, `timm/vit_small_patch14_dinov2.lvd142m`. The first two
were trained on ImageNet-1k.

## Labels

The teacher is the production detector, called exactly as
`infra/aws-central/service/detectors.mjs` calls it for a drive frame: gpt-5-mini, image
detail `high`, reasoning effort `minimal`, prompt `road-damage-v5` with the drive capture
layout, schema 4, strict structured output, `store: false`. A frame is damaged when the
teacher answers `image_quality: acceptable` and `assessment: damaged`. Every frame first
gets the app's Drive Mode preparation (whole frame, downscale only to 1280 px, adaptive
brightness, JPEG 0.85) through the evaluator's own code.

Every frame was labelled twice. The first answer is the label everywhere in this card;
the second measures the teacher and softens the training target (0, 0.5 or 1).

**The teacher does not agree with itself often enough for a 98% bar.** Of the 566 test
frames it called damaged, it called 518 damaged again: 91.5%. On RDD train it repeated
1,291 of 1,411 (91.5%), on drive test 124 of 137 (90.5%). Against RDD2022's human boxes
it called 78.7% of the 1,530 images with a pothole box (D40) damaged and 14.1% of the
3,921 images with no box at all.

Teacher spend: USD 11.51 at list price from the token usage OpenAI reported (22,480
calls, 32.1 M input tokens, 1.87 M output tokens), against a limit of USD 25. The invoice
is the authority.

## Split

By source, fixed before any label was read (`build_manifest.py`).

- Desktop videos are three continuous recordings cut into one-minute clips. The last
  clips of each recording are held out whole: 0014 to 0016, 0026, 0027, 0036, 0037 test;
  0011 to 0013, 0024, 0025, 0034, 0035 validation; the other 16 train.
- The two Downloads clips are test only.
- RDD2022 India has no sequence ids. Blocks of 500 consecutive file numbers are kept
  whole: 12 blocks train, 4 validation, 4 test.

| | Train | Validation | Test |
|---|---|---|---|
| Drive video, frames | 1,776 | 763 | 995 |
| Drive video, damaged | 7 (0.4%) | 1 (0.1%) | 137 (13.8%) |
| RDD2022 India, frames | 4,693 | 1,549 | 1,464 |
| RDD2022 India, damaged | 1,411 (30.1%) | 486 (31.4%) | 429 (29.3%) |

Damaged frames by teacher subtype, all splits: surface_breakup 2,160, pothole_cavity
234, rut_or_depression 46, other_road_damage 17, failed_patch 14.

**The drive videos contain almost no damage.** 8 of 2,539 train and validation drive
frames are teacher-damaged, and the teacher repeated only 1 of those 8. Nearly every
damaged drive frame (135 of 137 in test) comes from the two construction-drive clips. So
the model learned damage from RDD2022 and learned drive footage only as undamaged.

## Models compared

Frozen encoder, features pooled over the whole frame (mean and max; for the ViT also the
class token), then a linear probe or one hidden layer of 256. Thresholds for 99 / 98 / 95%
recall are set on validation and judged on test (all sources together, as one deployed
threshold would be). Lambda time is the function's own `Duration` for a 720 x 1280 frame,
warm, 2048 MB arm64, median of 12 calls with an untrained head (the released model was
measured again, below).

| Encoder, input, head | Test AUC all / RDD / drive | Val 99%: test recall, cleared | Val 98%: test recall, cleared | Val 95%: test recall, cleared | Best possible cleared at test recall 99 / 98 / 95 | Lambda p50 |
|---|---|---|---|---|---|---|
| DINOv2 ViT-S/14, 224, linear | 0.825 / 0.884 / 0.760 | 80.2%, 62.8% | 74.6%, 71.0% | 71.7%, 76.8% | 17.1% / 19.0% / 30.2% | not deployed |
| DINOv2 ViT-S/14, 224, hidden | 0.880 / 0.907 / 0.951 | 88.7%, 63.2% | 80.9%, 72.0% | 74.4%, 79.0% | 47.6% / 50.1% / 55.6% | not deployed |
| DINOv2 ViT-S/14, 448, linear | 0.815 / 0.899 / 0.601 | 80.4%, 63.4% | 75.8%, 73.5% | 70.3%, 82.1% | 0.7% / 2.9% / 14.3% | not deployed |
| DINOv2 ViT-S/14, 448, hidden | 0.879 / 0.904 / 0.953 | 82.3%, 72.3% | 78.6%, 75.8% | 73.1%, 81.3% | 44.7% / 45.5% / 51.1% | not deployed |
| EfficientNet-B0, 224, linear | 0.799 / 0.809 / 0.810 | 94.9%, 31.6% | 88.9%, 48.4% | 77.6%, 64.0% | 15.1% / 23.3% / 30.1% | 53 ms |
| EfficientNet-B0, 224, hidden | 0.859 / 0.850 / 0.911 | 96.3%, 42.2% | 91.5%, 57.3% | 86.0%, 65.7% | 29.1% / 34.1% / 48.2% | 53 ms |
| EfficientNet-B0, 448, linear | 0.796 / 0.842 / 0.684 | 89.8%, 41.5% | 79.9%, 55.9% | 71.9%, 70.4% | 2.4% / 4.1% / 23.8% | 162 ms |
| EfficientNet-B0, 448, hidden | 0.861 / 0.868 / 0.913 | 97.7%, 42.2% | 88.0%, 61.9% | 82.7%, 68.9% | 29.5% / 42.0% / 51.0% | 162 ms |
| MobileNetV3-L, 224, linear | 0.826 / 0.813 / 0.891 | 93.8%, 45.9% | 88.7%, 57.3% | 83.4%, 63.7% | 18.5% / 25.6% / 41.8% | 29 ms |
| MobileNetV3-L, 224, hidden | 0.858 / 0.850 / 0.952 | 94.3%, 54.9% | 82.5%, 66.8% | 80.0%, 70.0% | 40.1% / 45.1% / 53.9% | 29 ms |
| MobileNetV3-L, 448, linear | 0.863 / 0.849 / 0.913 | 97.7%, 39.8% | 93.1%, 54.3% | 86.9%, 66.2% | 25.1% / 34.8% / 47.8% | 80 ms |
| **MobileNetV3-L, 448, hidden** | **0.882 / 0.865 / 0.957** | 93.6%, 57.0% | 89.0%, 62.9% | 85.3%, 70.9% | 42.5% / 48.9% / 55.8% | 80 ms |

(This table scores through the training path, PIL. The released model's numbers below
come from the serving path and differ in the last digit.)

Reading it:

- No row reaches 98% test recall at any validation threshold. The thresholds do not
  carry from validation to test, because validation has one damaged drive frame.
- 448 against 224 helps MobileNetV3 a little and does not help the ViT.
- The ViT is not a Lambda CPU model at 448: 0.9 to 1.4 s per frame in ONNX Runtime on
  this Mac, against 0.14 to 0.22 s at 224. It was not deployed. Its drive recall at the
  common threshold is also the worst (38 to 59%).
- A 2 x 2 tile grid was not tried: `AGENTS.md` forbids tiling or cropping a detection
  input, in training data too.

**Fine-tuning did not help.** The last two stages of MobileNetV3-L (blocks 5 and 6, 2.2 M
parameters) were trained on cached trunk features, with no image augmentation, because
whole-image training ran at 5 s a step on this Mac. Measured by undamaged validation
frames cleared at 98% recall: the probe starts at 62.5%; six epochs at 1e-4 gave 54.5,
53.4, 50.8, 57.6, 59.2, 58.9%. A second run at 3e-5 with feature dropout started at 63.2%
and gave 60.9, 59.8, 58.9% before it was stopped (the Mac was on battery). The training
loss fell to 0.03, so it memorised. Proper fine-tuning needs augmentation and a GPU, and
more than anything damaged drive frames.

## Released model: metrics

Scored through the serving path (`lambda/scorer.mjs`: sharp + ONNX Runtime). The
threshold is the highest score that still flags 99% of teacher-damaged VALIDATION frames;
test was not used to set it. The score is the model's probability with its logit divided
by a temperature of 2.462 (fitted on validation), which spreads the scores out and does
not change their order.

At the released threshold, 0.0470:

| Test split | Recall of teacher-damaged | Undamaged cleared |
|---|---|---|
| All | 94.0% (532 / 566), 95% lower bound 91.7% | 56.5% (1,070 / 1,893) |
| Drive video | 82.5% (113 / 137) | 93.1% (799 / 858) |
| RDD2022 India | 97.7% (419 / 429) | 26.2% (271 / 1,035) |
| All, frames the teacher judged the same way twice | 94.0% (487 / 518) | 57.9% |
| Drive video, same | 83.1% (103 / 124) | 94.2% |
| RDD2022 India, same | 97.5% (384 / 394) | 27.1% |

At this threshold the screen alone would answer 44.9% of all test frames.

Trade-off curve (threshold from validation, judged on test; "best possible" sets the
threshold on test itself):

| Validation recall target | Test recall | Test cleared | Best possible cleared at that test recall |
|---|---|---|---|
| 99% | 94.0% | 56.5% | 42.7% |
| 98% | 89.4% | 62.5% | 49.0% |
| 95% | 85.7% | 70.4% | 55.6% |

Human labels:

- Owner-confirmed pothole events in video: 2 of 2 flagged (7 of 7 frames; scores 0.78,
  0.77, 0.46, 0.12 and 0.71, 0.08, 0.28).
- The 11 owner-labelled images: 8 of 9 potholes flagged, 2 of 2 not_pothole cleared. The
  missed pothole is `seed/IMG20260720144450.jpg` (score 0.004), a close-range photo. One
  cleared speed breaker scored 0.0468, under the threshold by 0.0002.
- RDD2022 human boxes, held-out blocks: 253 of 266 images with a pothole box (D40)
  flagged (95.1%); 198 of 766 images with no box cleared (25.8%).

Export and serving checks:

- ONNX against torch on all 2,459 test frames: largest score difference 0.00001.
- Serving path (sharp) against training path (PIL) on 4,771 validation and test frames:
  mean score difference 0.0035, largest 0.047, 16 decisions differ at the threshold. The
  threshold was set on serving-path scores.

## Lambda

`pothole-reporter-central-screen`, Node.js 22, arm64, 2048 MB, 15 s timeout, ZIP of 31 MB
(59 MB unzipped), no Docker. Measured by Lambda itself on 7 Oct 2026, 30 warm calls per
image with 720 x 1280 JPEGs sent in the central service's request shape:

| | Cold start | Warm p50 | Warm p90 | Warm max | Model p50 | Decode and resize p50 | Memory used |
|---|---|---|---|---|---|---|---|
| Owner pothole photo (score 0.986, damaged) | 650 ms init + 98 ms | 76 ms | 90 ms | 98 ms | 46 ms | 13 ms | 270 MB |
| Dashcam frame, assistant-labelled clean (score 0.220, damaged) | | 71 ms | 81 ms | 94 ms | 46 ms | 9 ms | 271 MB |

The "clean" frame is flagged. It is `seed/t013s.jpg`, which only the assistant labelled.
A flag costs nothing in `yolo_then_openai` (the frame goes to gpt-5-mini as it does
today), but it is one of the 43.5% of undamaged test frames this model does not clear.

## Known failure modes

1. **Drive footage.** Recall on held-out drive video is 82.5%, and all of that evidence
   is two clips of one construction site. Training had no repeatable damaged drive
   frame. Expect live recall well under the offline 94%.
2. **Calibration moves with the camera.** A threshold that keeps 98% of RDD damage clears
   a quarter of RDD frames; the same threshold clears 93% of the Bengaluru clips. The
   right threshold for live frames can only be read from live scores.
3. **Close-range and manual-style photos.** One of six owner close-ups was missed. The
   screen is for drive frames only; the central service never sends it a manual photo.
4. **Speed breakers, rumble strips, debris.** Cleared in the two owner-labelled cases,
   one of them by 0.0002.
5. **The frame is letterboxed to 448.** A 720 x 1280 frame becomes 252 x 448, so a far
   pothole is a few pixels.
6. **Image quality.** The screen always answers `image_quality: acceptable`. gpt-5-mini
   rejected 7 of 11,240 frames for quality; in `yolo_then_openai` such a frame would be
   answered undamaged by the screen instead of rejected.
7. **Description language.** The screen's description is one fixed English sentence, also
   when the request asks for Kannada.
8. **The teacher.** The screen copies gpt-5-mini, including the 21.3% of human-boxed
   potholes it does not call damaged.

## Shadow mode and the flip

Deploy shadow mode (the owner does this):

```bash
EXTRA_PARAMETER_OVERRIDES="SharedDetectorProvider=openai_with_shadow_screen YoloFunctionName=pothole-reporter-central-screen" infra/aws-central/deploy.sh
```

The central service refuses to call a screen without a key. Add one field to the detector
secret, beside `openai_api_key`:

```json
{"openai_api_key": "...", "yolo_api_key": "<contents of ml/classifier/work/screen-api-key>"}
```

The function holds only the SHA-256 of that key. `GET /v1/health` then answers
`shared_vision_shadow_screen_configured: true`. Every drive detection logs `screen_score`,
`screen_assessment`, `screen_ms`, `screen_agrees`, `screen_error` and `screen_model`, and
`node infra/aws-central/tools/production-health.mjs --window 7d` reports (never fails on)
the live recall, the cleared share, and the threshold that would have kept 98% recall.

Recommended conditions for `yolo_then_openai`, all of them:

1. At least 300 screened frames that gpt-5-mini judged damaged, over at least 7 days and
   more than one phone.
2. A threshold read from one week of logged scores keeps at least 98% of the NEXT week's
   damaged frames. One week alone proves nothing: the threshold was fitted to it.
3. At that threshold the screen clears at least 30% of the frames gpt-5-mini judged
   undamaged. Below that the saving is not worth a new way to miss a pothole.
4. `screen_error` on under 1% of drive frames and `screen_ms` p90 under 300 ms.

If the 98% in condition 2 cannot be met at a useful cleared share, that is the expected
result of the teacher's own 91.5% repeatability, not a reason to lower the bar quietly.
The honest options then are to judge recall per pothole instead of per frame (a pothole
is seen in several frames), or to retrain on live drive frames. Retraining needs the
frames: today the service keeps none. Keeping a sample of drive frames with their
gpt-5-mini verdicts is a privacy decision for the owner.

Set the threshold without a new model: redeploy the function with
`SCREEN_THRESHOLD=<value> ml/classifier/lambda/deploy.sh`.

## Reproduce

```bash
cd ml/classifier
python3.12 -m venv work/venv && work/venv/bin/pip install torch torchvision timm onnx onnxruntime pillow numpy scikit-learn
(cd lambda && npm install)
work/venv/bin/python fetch_rdd2022_india.py          # 527 MB
work/venv/bin/python extract_frames.py               # videos at 2 fps, RDD images
work/venv/bin/python teacher_label.py --source-prefix desktop- --source-prefix downloads- --source-prefix rdd2022-india-train
work/venv/bin/python build_manifest.py               # split and counts
work/venv/bin/python teacher_label.py --paths-file work/trainval-paths.txt --trial 1   # second answer
for e in dinov2_s14 efficientnet_b0 mobilenetv3_l; do for s in 224 448; do
  work/venv/bin/python embed.py --encoder $e --size $s
  work/venv/bin/python train_probe.py --encoder $e --size $s; done; done
work/venv/bin/python summarise.py
work/venv/bin/python release.py --encoder mobilenetv3_l --size 448 --head hidden256 \
  --version road-screen-v1-mobilenetv3l-448 --target-recall 0.99
AWS_PROFILE=pothole PUBLISH_MODEL=1 lambda/deploy.sh
AWS_PROFILE=pothole node lambda/measure.mjs <pothole.jpg> <clean.jpg> 30
```

To deploy without retraining, fetch the published ONNX into `lambda/model/model.onnx`;
`lambda/model/model.json` (committed) holds its sha256, temperature and threshold.
