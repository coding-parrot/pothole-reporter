# Road screen v2 (`road-screen-v2-mobilenetv3l-448`)

MobileNetV3-Large, the whole network fine-tuned at 448 on whole frames with augmentation,
on eleven public sources plus the owner's drive video. Trained on AWS on 7 Oct 2026
(`CLOUD_PLAN.md`), deployed to `pothole-reporter-central-screen` the same day for SHADOW
use only. It replaces v1 (described below, unchanged) in that function.

**It is better than v1 and it still does not meet the bar on every held-out slice.** The
bar: flag at least 98% of teacher-damaged frames from unseen sources and clear at least
30% of the undamaged ones. At the deployed threshold it does that on the owner's held-out
drive video (98.5%, 90.9% cleared), on the held-out RAD videos (98.3%, 38.1%) and on all
test frames together (99.4%, 49.2%). It misses on the held-out RDD2022 India blocks
(97.2% recall) and on IRDD, the dataset held out whole (99.9% recall but 7.0% cleared).

**The automatic gate read "mixed" and nothing would have been deployed by it.** The gate
was: v2 beats v1 on every held-out slice at the validation-98% threshold, both models
calibrated on the same validation split. At that threshold v2 traded recall for clearing
on the old slices (92.0% recall and 84.2% cleared against v1's 99.1% and 42.3%), so no
old slice was a clean win. The coordinator then ruled to put v2 in shadow at the stricter
threshold below, because at equal recall v2 clears more on every slice (AUC 0.932 against
0.801 on all test frames) and shadow mode decides nothing for a user. That was a
decision, not a passed gate.

| | |
|---|---|
| Model | MobileNetV3-Large (timm `mobilenetv3_large_100.ra_in1k`), all layers fine-tuned, mean and max pooling, one hidden layer of 256; seed 2, best epoch 5 of 14 |
| Input | the whole frame, letterboxed to 448 x 448, as v1 |
| Output | `score` in 0..1 (logit divided by a temperature of 1.149); damaged when `score >= 0.0549` |
| Threshold rule | the highest score that still flags 98% of teacher-damaged frames in EVERY validation source with at least 50 of them (RAD, Czech, India, Rome); serving-path scores; test never used |
| ONNX | 13,864,689 bytes, sha256 `53f99bcb0da7e909aff2f2a85fd4389f69d1ffa511204cfff03a5b09d8150e41`; against torch on all 6,583 test frames the largest score difference is 0.000003 |
| Published | `s3://pothole-reporter-ml-695656921622-ap-south-1/models/road-screen-v2-mobilenetv3l-448/` (`model.onnx`, `model.deployed.json`, `release.json`, `parity.json`) |
| Scorecard | `reports/screen-v2-20261007-serving-path.md`, `reports/screen-v2-20261007-scorecard.json`; full files under `runs/screen-v2-20261007/report/` in that bucket |

## v2 data

Only sets with a stated open licence that download over HTTPS with no account, token or
form. Annotations chose which images the teacher saw (every image with a pothole mark,
then likely negatives and hard cases) and feed the "annotated pothole" check. The label
is always the teacher's verdict. Frames below are the ones in the manifest.

| Source | Licence | View, country | Their labels | Frames | Teacher-damaged | Split |
|---|---|---|---|---|---|---|
| Owner drive video (v1) | private | phone on dashboard, Bengaluru | none | 3,534 | 145 | train 1,776, validation 763, test 995 |
| RDD2022 India (v1), figshare 10.6084/m9.figshare.21431547 | CC BY 4.0 on figshare; the authors' README says CC BY-SA 4.0 | phone on dashboard | boxes D00 D10 D20 D40 | 7,656 | 2,320 | by blocks: train 4,652, validation 1,540, test 1,464 |
| RDD2022 Japan | same | phone on dashboard | same | 4,987 | 3,182 | train |
| RDD2022 Norway | same | survey vehicle, wide | same | 1,121 | 449 | train |
| RDD2022 China motorbike | same | phone on motorbike | same | 937 | 834 | train |
| RDD2022 China drone | same | drone, top-down | same | 364 | 233 | train |
| RDD2022 Czech | same | phone on dashboard | same | 853 | 242 | validation (whole country) |
| RAD, Kaggle rohitsuresh15/radroad-anomaly-detection v3 | MIT | dashcam, Bengaluru, 1920 x 1080 | boxes, broad "RoadDamages" class | 7,184 | 2,600 | by source video (the repo's split): train 5,656, validation 899, test 629 |
| Bučko et al., figshare 10.6084/m9.figshare.21214400 | CC BY 4.0 | dash camera, day, sunset, evening, night, rain; country not stated | pothole boxes | 2,094 | 1,413 | train |
| Cracks and Potholes in Road Images, Mendeley 10.17632/t576ydh9v8.4 | CC BY 4.0 | survey vehicle, Brazil | road, crack, pothole masks | 1,259 | 1,197 | train |
| Attain (windshield subsets), Mendeley 10.17632/nykrzdm74f.1 | CC BY 4.0 | phone on windshield; Iran by the institution | distress type and severity | 1,637 | 1,206 | train |
| BharatPotHole, Kaggle surbhisaswatimohanty/bharatpothole | CC BY-SA 4.0 on Kaggle; CC BY 4.0 in the archive | dashcam, India; publisher stretched frames to 640 x 640 | pothole boxes | 2,836 | 770 | train |
| Road Damage, Kaggle alvarobasily/road-damage | CC0 1.0, uploader's own photos | phone, moving vehicle; country not stated | boxes, 4 classes | 2,129 | 1,885 | train |
| Rome road damage, Zenodo 10.5281/zenodo.18528034 | CC BY 4.0 | GoPro in a car, Italy, 640 x 360 | pothole, crack, manhole boxes | 1,897 | 1,218 | validation (whole dataset) |
| IRDD, Zenodo 10.5281/zenodo.21167531 | CC BY 4.0 | phone on dashboard, portrait and landscape, Iraq | oriented boxes D00 D10 D20 D40 | 3,495 | 2,464 | test (whole dataset, never trained on) |

Totals: 41,983 frames. Train 29,448 (14,683 damaged), validation 5,952 (2,267), test
6,583 (3,208). 115 frames were dropped as near-copies (difference hash within 2 bits) of
a frame held out in another dataset. The 27 owner-labelled images are scored only.

The two Kaggle sets download without a key, which is why they are in. The ShareAlike
reading of RDD2022 and BharatPotHole is the owner's call before weights leave the account,
as in v1.

Refused or skipped:

- RDD2022 United States (4,805 images): Google Street View captures, not a vehicle camera
  and not the dataset authors' to license.
- Nienaber / Stellenbosch dashcam potholes (13,482 images, South Africa): no licence, only
  "please cite"; the original host is gone and the Kaggle mirrors say "unknown".
- Pothole Mix (CC BY-NC 3.0) and inside it EdmCrack600 (non-commercial), GAPs384
  (academic only), Pothole-600 and Crack500 (no licence). Its Brazil part was taken from
  its own CC BY 4.0 record instead.
- RDD2020 on Mendeley (CC BY-NC 3.0; the same images as RDD2022), CQU-BPDD and CMIRD
  (non-commercial), GAPs (signed agreement), IDD, the Mexico set and PothRGBD (account or
  subscription), every Roboflow Universe set (API key), SVRDD (Baidu street view terms).
- Kaggle pothole sets scraped from the web under CC0 or ODbL tags (the 665-image and
  681-image families and their re-uploads), MIIA and RTK (no licence).
- Not used though usable: IRD-Dataset Baghdad, PathCare, the Bangladesh and Kent sets
  (small or view unverified), drone and top-down sets.

## v2 labels

Same teacher, same script, same production prompt and contract key (`3d3dad4bd03a8a0a`).
30,858 new frames were labelled once; the 7,794 new validation and test frames a second
time. 0 failed calls. Total teacher spend USD 33.66 of the USD 40 cap (USD 22.15 new).

The teacher against itself on test (its second answer used as if it were the screen):

| Slice | Damaged, first answer | Damaged both times | Recall of its own verdicts | Undamaged repeated |
|---|---|---|---|---|
| Old test split | 566 | 518 | 91.5% | 97.1% |
| IRDD | 2,464 | 2,371 | 96.2% | 89.3% |
| RAD test videos | 178 | 161 | 90.4% | 94.9% |
| All test | 3,208 | 3,050 | 95.1% | 94.5% |

So 98% against every teacher-damaged frame is more than the teacher gives itself. A
screen can still reach it, by flagging more: v2 does at 49.2% cleared on all test.

## v2 results

All through the serving path (sharp and ONNX Runtime), thresholds from validation only.
Each cell: recall of teacher-damaged frames (caught / damaged), share of undamaged cleared.

At the deployed threshold (98% recall on every validation source):

| Test slice | v2 | v2, teacher-stable frames only | v1 as it was deployed (0.0470) |
|---|---|---|---|
| Old test split, all | 97.5% (552/566), 74.7% | 97.7% (506/518), 76.5% | 94.0%, 56.5% |
| Owner drive video (137 damaged frames) | 98.5% (135/137), 90.9% | 98.4% (122/124), 92.1% | 82.5%, 93.1% |
| RDD2022 India held-out blocks | 97.2% (417/429), 61.4% | 97.5% (384/394), 63.3% | 97.7%, 26.2% |
| IRDD (held out whole) | 99.9% (2461/2464), 7.0% | 99.9% (2368/2371), 7.7% | 92.9%, 24.5% |
| RAD test videos | 98.3% (175/178), 38.1% | 98.1% (158/161), 40.0% | 68.5%, 63.9% |
| All test | 99.4% (3188/3208), 49.2% | 99.4% (3032/3050), 51.7% | 91.7%, 47.7% |

Against v1 as deployed, v2 here loses 2 frames of recall on the India blocks (417
against 419 of 429), clears less on drive video (90.9% against 93.1%), IRDD and RAD
because it flags far more of their damage, flags 248 of the 266 India frames with a
pothole box against 253, and clears neither of the two owner not_pothole images (v1
cleared both).

At the validation-98% threshold, pooled (the gate), v1 calibrated the same way:

| Test slice | v1 | v2 (released, seed 2) | v1 cleared at v2's recall |
|---|---|---|---|
| Old test split, all | 99.1% (561/566), 42.3% | 92.0% (521/566), 84.2% | 60.5% |
| Owner drive video | 98.5% (135/137), 84.8% | 93.4% (128/137), 93.7% | 90.6% |
| RDD2022 India held-out blocks | 99.3% (426/429), 7.0% | 91.6% (393/429), 76.3% | 56.2% |
| IRDD | 99.0% (2440/2464), 7.9% | 99.1% (2441/2464), 20.9% | 7.7% |
| RAD test videos | 94.4% (168/178), 25.3% | 96.1% (171/178), 58.3% | 13.7% |
| All test | 98.8% (3169/3208), 29.5% | 97.7% (3133/3208), 61.4% | 35.5% |

Every variant at its own validation-98% threshold (training path, PIL and torch):

| Variant | Old test, all | Owner drive | India blocks | IRDD | RAD test | All test | All-test AUC |
|---|---|---|---|---|---|---|---|
| v1 | 99.1%, 42.3% | 98.5%, 85.2% | 99.3%, 6.7% | 98.9%, 8.0% | 93.8%, 25.5% | 98.7%, 29.5% | 0.801 |
| v1 recipe on new data, MobileNetV3-L | 95.9%, 65.2% | 97.8%, 90.8% | 95.3%, 44.0% | 99.6%, 5.5% | 99.4%, 20.8% | 99.0%, 41.0% | 0.877 |
| the same with 4 augmented views | 95.6%, 63.3% | 94.9%, 90.4% | 95.8%, 40.8% | 99.8%, 5.7% | 96.1%, 29.3% | 98.9%, 41.2% | 0.868 |
| v1 recipe on new data, EfficientNet-B0 | 95.9%, 57.3% | 96.4%, 83.0% | 95.8%, 36.0% | 100.0%, 3.4% | 99.4%, 8.6% | 99.2%, 34.3% | 0.884 |
| the same with 4 augmented views | 95.4%, 63.0% | 97.1%, 90.4% | 94.9%, 40.2% | 99.8%, 6.6% | 97.8%, 17.7% | 98.9%, 39.7% | 0.886 |
| full fine-tune, MobileNetV3-L, seed 1 | 94.5%, 82.4% | 97.8%, 91.8% | 93.5%, 74.6% | 99.2%, 16.1% | 96.6%, 55.2% | 98.2%, 58.5% | 0.934 |
| full fine-tune, MobileNetV3-L, seed 2 (released) | 92.0%, 84.3% | 93.4%, 93.7% | 91.6%, 76.5% | 99.1%, 20.5% | 96.1%, 56.8% | 97.7%, 61.1% | 0.932 |
| full fine-tune, EfficientNet-B0, seed 1 | 91.5%, 85.3% | 98.5%, 92.1% | 89.3%, 79.6% | 98.8%, 30.0% | 96.6%, 61.4% | 97.4%, 65.2% | 0.942 |

The released seed was picked on validation alone (61.6% cleared at 98% validation recall
against 59.9% for seed 1). EfficientNet-B0 ranks a little better but measured 162 ms on
the Lambda in v1, over the 150 ms bar; it was not exported.

Annotated potholes and owner labels, at the deployed threshold: 248 of 266 India frames
with a pothole box flagged (93.2%), 1,494 of 1,495 IRDD frames (99.9%); 9 of 9 owner
potholes flagged; 0 of 2 owner not_pothole images cleared; both owner-confirmed video
events flagged (7 of 7 frames).

Reading it:

- More and wider data moved the ranking a lot (AUC 0.80 to 0.93). Augmented views on a
  frozen encoder did not; fine-tuning the whole network on images did.
- A pooled validation threshold still does not carry to the old test split: Rome is 1,218
  of the 2,267 damaged validation frames and is easy, so the pooled 98% leaves India at
  96.1% on validation and 91.6% on test. The per-source rule fixes most of that.
- The clearing numbers of the public test sets understate a real drive. Their frames were
  chosen for damage: 70% of the IRDD frames are teacher-damaged and most of the rest show
  cracks. The owner's drive video (858 undamaged test frames, 90.9% cleared) is the only
  slice with the mix a phone sees, and it is small: 137 damaged frames from two clips,
  whose 98.5% has a 95% lower bound of about 95%.
- Training still has 7 damaged frames from the owner's own camera.

## v2 Lambda

Same function, package and settings as v1 (31 MB zip, 2048 MB, arm64). Measured by Lambda
on 7 Oct 2026 after the deploy, 20 warm calls per image:

| | Cold start | Warm p50 | Warm p90 | Warm max | Model p50 | Decode p50 | Memory |
|---|---|---|---|---|---|---|---|
| `docs/example-pothole.jpg`, 619 x 1100 (score 0.897, damaged) | 652 ms init + 118 ms | 77 ms | 82 ms | 105 ms | 47 ms | 12 ms | 263 MB |
| `seed/t013s.jpg` at 720 x 1280 (score 0.665, damaged) | | 77 ms | 92 ms | 104 ms | 48 ms | 13 ms | 263 MB |

The 28 Lambda tests pass with the v2 model packaged. The function's log shows
`screen_model_loaded` with `road-screen-v2-mobilenetv3l-448` and threshold 0.0549.
`seed/t013s.jpg` is the frame only the assistant labelled clean; v1 flagged it too.

The flip criteria in "Shadow mode and the flip" below are unchanged and apply to v2 as
written: the threshold that matters is the one read from live scores.

Back to v1: copy `runs/screen-v2-20261007/state/release/road-screen-v1-mobilenetv3l-448/model.onnx`
from the ML bucket to `lambda/model/model.onnx`, restore `lambda/model/model.json` from
commit 2b89f6d, run `AWS_PROFILE=pothole lambda/deploy.sh`.

## v2 cost and what is left on AWS

| | |
|---|---|
| Instance | one `g4dn.2xlarge` on demand, 05:59:49 to 10:30:50 UTC, 4.52 hours at USD 0.828: USD 3.74, plus about USD 0.11 for its 200 GB volume. It powered itself off when the last stage finished. |
| Teacher | 38,652 answers used in v2 (30,858 first, 7,794 second): USD 22.15. Total with v1: USD 33.66. |
| S3 | 34.2 GB: public archives 26.4 GB (expire after 30 days), frames 3.8 GB, v1 work 1.8 GB, run state and reports 2.0 GB, labels 0.1 GB. About USD 0.85 a month now, about USD 0.20 once the archives expire. |
| Left | bucket `pothole-reporter-ml-695656921622-ap-south-1`, role and instance profile `pothole-reporter-ml-trainer`, security group `pothole-reporter-ml-trainer`. No instance, no volume. |

Reproduce: `CLOUD_PLAN.md`. The deployed threshold is
`python3 set_operating_point.py --report report.json --released model.json --rule val98_every_source --out lambda/model/model.json`
on the serving-path `report.json` and the released `model.json`.

---

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
