# Detection benchmark

The detector now returns one binary result: `is_pothole: true/false`. A positive must
pass every physical evidence gate and include an approximate `small` / `medium` /
`large` size; ambiguous inputs are negative. It does not ask a language model for a
confidence score or accept generic road damage. This directory holds labels, prompt
arms and the production-semantic replay harness.

```bash
python3 eval/run_eval.py --mode drive --trials 5
python3 eval/run_eval.py --mode manual --trials 5
python3 eval/run_eval.py --mode drive --models gpt-5-mini,gpt-5.6 --details high,original
```

Use `--events id-one,id-two` to replay only exact labelled event IDs; unknown IDs fail
closed instead of silently running a different subset.

`OPENAI_API_KEY` comes from the environment or the repo-root `.env`. Use
`--dry-run` to validate transforms and request configuration without making calls.

## RAD v3 full-frame pipeline

The source is the official [RAD—Road Anomaly Detection Kaggle dataset](https://www.kaggle.com/datasets/rohitsuresh15/radroad-anomaly-detection), version 3. Its [Kaggle metadata](https://www.kaggle.com/api/v1/datasets/metadata/rohitsuresh15/radroad-anomaly-detection) reports an MIT licence; the related dataset paper is available at [DOI 10.1007/978-981-97-2004-0_34](https://doi.org/10.1007/978-981-97-2004-0_34). The exact source and index audit is recorded in `rad_v3_source_receipt.json`.

RAD's `RoadDamages` class combines multiple anomaly types. It is a review queue, not pothole ground truth, and never enters a pothole accuracy result without a named audit overlay. The published train/valid/test folders also leak source videos across splits, so the adapter ignores those folders for evaluation splitting and groups complete frames by source video. Ambiguous duplicate video basenames are excluded from chronological events.

```bash
# Download only images/**, verify every listed path and byte count, then build an index.
python3 eval/rad_dataset.py download --root eval/.rad-data
python3 eval/rad_dataset.py verify --root eval/.rad-data
python3 eval/rad_dataset.py index --root eval/.rad-data --out eval/.rad-data/index.json

# Validate source bytes and the exact production Drive contract without an API call.
python3 eval/run_rad_eval.py --manifest eval/.rad-data/index.json \
  --dataset-root eval/.rad-data --validate-only

# An audit overlay must be pinned to the index hash before pothole metrics are valid.
python3 eval/run_rad_eval.py --manifest eval/.rad-data/index.json \
  --audit-manifest <audit.json> --dataset-root eval/.rad-data --dry-run
python3 eval/run_rad_eval.py --manifest eval/.rad-data/index.json \
  --audit-manifest <audit.json> --dataset-root eval/.rad-data \
  --split validation --label pothole,not_pothole,speed_breaker \
  --paid-run --max-calls <exact-selected-event-count> --gate
```

Release runs reject standalone fixtures and require the exact official, complete v3 index
identity, source counts, derived index counts and audited content seal. `--allow-incomplete`
is for adapter test fixtures only and can never enter `--gate`. Kaggle's file inventory
supplies byte lengths, not per-file content hashes: download verification therefore checks
paths and byte counts. The deterministic index separately hashes every selected full-frame
model input and seals the derived annotations, chronology and source-video split; duplicate
Roboflow variants that are not model inputs contribute fail-closed target semantics but are
not described as individually byte-sealed. The full-archive hash in
`rad_v3_source_receipt.json` is an audit receipt, not a hash supplied by the downloader.

The default gate requires 100% recall on the selected locked potholes, zero accepted
audited non-potholes, zero accepted speed breakers and zero errors. Cached responses are
accepted only after the exact production request is rebuilt and its hash matches; resumed
rows are rebound to their immutable event, label, audit and full-frame transform data.
That is a reproducible gate on the audited subset, not a claim of universal or 100%
real-world pothole accuracy.
The shipped v15 Drive contract uses `gpt-5.6` with `original` image detail; `rad_v3_release_receipt.json` records the exact validation and sealed-test result hashes.

Drive replay uses a 768 px full-frame context plus as many as three chronological,
downscale-only 1280 px full frames. No live, replay, evaluation, training-data or
evidence path may crop, tile, mask or extract a road region. An entry can provide a
three-frame event with `frames` and `primary_index`; legacy entries with one `path`
still work. Manual replay uses the shipped full-frame 2000 px path. Pixel-triggered
low-light enhancement, model, image detail, prompt, schema and decision policy are
recorded in each run.

The replay follows the production operation order—whole-frame resize, sample luminance,
then conditionally enhance. Pillow and an Android WebView do not use byte-identical
resamplers/JPEG codecs, so hashes are reproducibility identifiers for evaluator runs,
not a claim that Python emits the exact browser JPEG bytes.

Give new entries an explicit `mode` of `manual` or `drive`. For the legacy seed,
the harness infers Drive Mode only when `source` contains `dashcam`; the two product
paths are never mixed into one accuracy number.

## The one rule: measure the noise floor first

This detector is **stochastic**. Byte-identical input, same prompt, same
parameters, re-run: the true-positive rate has swung 30 points and a single frame
has swung 80 points (accepted 4 of 5 in one run, 0 of 5 in the next). That is not
transport error. Every call returned valid structured output.

`run_eval.py` always runs the control configuration twice on identical bytes.
Each repetition has a separate cache slot. Intervals resample source events, not
individual repeated calls, so five calls to one frame never masquerade as five roads.

The noise is asymmetric and that matters. Losses on confirmed potholes have shown
almost no drift (56 consecutive true-positive calls with zero variation in one
batch), while the false-positive rate wobbles by 5 to 15 points on its own. So a
change that costs real potholes is believable at low trial counts, and a change
that appears to reduce false alarms usually is not.

## Images and labels

Images are **not committed**: they are large, and third-party sources carry their
own licence and attribution terms. `eval/images/` is gitignored. `labels.json`
records the path, the label, why it is labelled that way, the source and the
licence, so an image set can be rebuilt and its provenance stays auditable.

New labels are `pothole` or `not_pothole`. Legacy `pothole_cavity` is positive and
clearly non-cavity surface classes are negative. Legacy `failed_patch` is excluded until
a human records whether it contains a distinct cavity. Unlabelled and disputed images
also run but are excluded from binary rates.

For an unfinished lane that is visibly carrying traffic, only a discrete localized cavity
with a strong rim and stable geometry across a chronological burst can be positive.
Ordinary gravel texture, corrugation, ruts, broad breakup, puddle ambiguity, shoulders,
and construction beds remain negative.

The general labels set is not a broad accuracy release gate; the narrow exact-media
regression gate is documented below. The general set has seven owner-verified positive photos, two
owner-confirmed Drive positives at the exact reported moments, other independently
reviewed Drive events, one unresolved ambiguous patch and one local negative burst near
second 44 of the second clip. The two Drive positives are reconstructed
from the app's native MediaRecorder segments; they prove saved-video coverage but are not
pixel-identical to the separate CameraX ImageAnalysis frames used live. The tester's traffic-calming footage is
an external recording of the test phone, not raw CameraX evidence; it remains semantic
ground truth but is excluded from production-accuracy rates until unobstructed app frames
are available.
Other labels remain unverified, and historical result files
predate this contract. Do not choose a model or claim accuracy from this set alone. First
collect fully audited drives with diverse positive and negative events, keep adjacent
frames in one event/split, and lock a held-out test set.

To reproduce the set, drop the owner's photos and drive frames into the paths under
`eval/images/` named in `labels.json`. The private tester frames must not be distributed.
When adding third-party imagery, record its licence and attribution in `labels.json`.
Openly licensed street-level imagery is suitable for evaluation; it is **not** suitable
for filing complaints, because a complaint asserts a current condition on a road you
observed.

Private events retain a neutral source identifier, the reviewed source interval and exact
frame timestamps in `labels.json`; every fixture also records a SHA-256 so a local private
copy can be verified without publishing its filename or pixels. Exact original filenames
and capture times stay outside the repository. `tests/media_regression_manifest_test.py`
guards this coverage without requiring the private image files to be committed or present
in CI.

## Exact private-media release gate

`private_release_gate.json` locks the exact fingerprints and stream metadata of the two
owner Drive segments and the tester traffic-calming clip. It also locks five source-frame
phases for both confirmed potholes and all three negative breaker intervals. The private
videos and generated JPEGs remain uncommitted. The positives are MediaRecorder
reconstructions and the negatives are external recordings of the test device, so the
manifest explicitly excludes every event from raw CameraX accuracy claims.

```bash
# Verify all source bytes, video metadata, timestamps and 75 regenerated JPEGs; no API call.
python3 eval/private_release_gate.py --source-dir <private-video-directory> --validate-only

# Run one fresh production-model decision for every event phase (25 paid calls).
python3 eval/private_release_gate.py --source-dir <private-video-directory>

# Intentionally repeat each exact phase when measuring stochastic stability.
python3 eval/private_release_gate.py --source-dir <private-video-directory> --trials 3
```

The gate reads the current native Drive prompt, strict schema and model and blocks if the
evaluator mirror has drifted. It exits non-zero on a missing or altered source, extraction
or schema error, any missed positive phase, or any accepted negative phase. Use
`--source SOURCE_ID=/path/to/video` instead of `--source-dir` when explicit mappings are
more convenient. `--check-manifest` is the media-free, API-free CI contract check.

## Private Desktop drive corpus

`private_drive_corpus.json` turns all 30 supplied Desktop segments into a permanent,
metadata-only corpus: source hashes, stream metadata, the exact 3,314-window VOD cadence,
and 50 reviewed hard-negative or abstention phases. Videos and generated frames stay local.
The exhaustive model output is recorded only as an audit receipt; it is not a label.
These MP4 reconstructions test saved-video behavior and do not claim raw CameraX accuracy.

```bash
# Normal source-free check (also runs in tests/run-all.sh).
python3 eval/private_drive_corpus.py --check-manifest

# Verify all exact source bytes, decode every video, and regenerate all 150 full frames.
python3 eval/private_drive_corpus.py --source-dir "/path/to/pothole video segments" --validate-only

# Optional, explicit, bounded and resumable production-model gate (50 phases today).
python3 eval/private_drive_corpus.py --source-dir "/path/to/pothole video segments" \
  --paid-run --max-calls 50
```

The Desktop cases test false-positive rejection and abstention. Recall remains gated by
the separate owner-confirmed positives in `private_release_gate.json` and the RAD suite.

### Materialized private eval

The committed manifests can be converted into a real, persistent local image eval. The
export contains 67 owner-ground-truth images across 27 cases and 11 physical events
(two video potholes, two confirmed speed breakers and seven manual pothole photos), plus
150 separately marked diagnostic expected-reject frames. It excludes the assistant-labelled
opening-grid event. `desktop_pool.jsonl` also retains all 3,314 Desktop windows: 50 point
to diagnostic cases and the other 3,264 remain explicitly unlabelled. Model results never
become labels.

```bash
python3 eval/materialize_private_eval.py \
  --desktop-source-dir "/path/to/pothole video segments" \
  --release-source-dir "/path/containing/private/release/videos" \
  --manual-source-dir eval/images \
  --export-dataset

# Later integrity check; source videos and API access are not needed.
python3 eval/materialize_private_eval.py --check-dataset
```

The ignored `eval/.private-drive-corpus/dataset/` directory contains content-addressed,
uncropped JPEGs, `labels.jsonl`, `bursts.jsonl`, `desktop_pool.jsonl` and a sealed
`dataset.json`. Production-accuracy consumers must filter to both
`eval_tier=owner_ground_truth` and `accuracy_metric_eligible=true`, then group metrics by
`physical_event_group_id`; external phone recordings remain semantic regressions only.
Desktop diagnostics have `label=null` and only an
`expected_decision=reject`; they are not ground-truth negatives. All records preserve the
capture provenance and explicitly avoid raw CameraX accuracy claims.

## Arms

`baseline` is read live from `DETECT_PROMPT` in `static/standalone.js`, so the
control cannot silently drift from what ships. Every `.txt` file in
`eval/prompts/` becomes an additional arm named after the file.

Everything below is a historical log for retired detector contracts. It explains past
choices but is not directly comparable with the binary v13 result.

## Results log

Kept so nobody re-runs a dead end.

| Change | Verdict | Evidence |
|---|---|---|
| Remove the `gpt-5-nano` pre-screen | **Shipped** | nano missed 8 of 9 potholes the main model caught, including a confirmed one at 0.28 confidence |
| Drive Mode at full resolution instead of 1280px | **Shipped** | 1280px dropped a real pothole to 0.46, under the gate, that holds at 0.60 full size |
| `reasoning: low` on detection | **Shipped** | 6/6 on confirmed potholes at identical median confidence, roughly half the latency |
| JPEG q95 instead of q85 | Rejected | apparent gain sat inside the noise floor; 1.75x the bytes for nothing |
| Fixed narrow crop | Rejected | destroys true positives (1/10): a narrow crop excludes mid-lane damage |
| Expanded crop with horizon and hood anchors | Rejected | anchors restore true positives but false positives rise with road resolution: 50% → 75% → 85% as the retained road area grows 1.04x → 1.42x → 1.90x |
| Unsharp mask | Rejected | only variant with a negative confidence delta; lost the hard case outright |
| Carriageway / kerb-exclusion prompt (`prompts/carriageway.txt`) | Rejected | cost real potholes (18/18 → 15/18 photos, 10/10 → 8/10 dashcam) with no false-positive gain, and *raised* false-positive confidence by handing the model the word "carriageway" to assert |
| Raise the gate to 0.60 or 0.65 | Rejected | 0.65 cuts false alarms 48% → 8% but dashcam true positives collapse to 9/20, and Drive Mode is exactly where distant real potholes live |

Two corrections worth remembering, both cases of a confident story that the data
did not support:

- An earlier run found 22 of 51 accepted negatives describing a kerb, drain or
  shoulder, which looked like a clear failure mode. It **did not reproduce**: a
  byte-identical control arm hit zero edge-worded false positives with no prompt
  change at all.
- A claimed 1536-patch cap on the vision endpoint does not exist. Calibrated
  across 8 probe sizes: the long side is clamped to 2048, patches are
  `ceil(w/32) * ceil(h/32)`, tokens are `285 + 1.2 * patches`, no cap.

## What the data says to try next

The surviving false positives are not at the road edge. They concentrate on
mid-lane texture: an erosion and silt strip, and an intact but dusty road. The
hypothesis the evidence supports is a clause requiring a **visible depth cue or a
defined rim**, rather than anything about where on the road the defect sits.

The set is too small to settle this: seven owner-confirmed photo potholes, two
owner-confirmed traffic-calming intervals, one independently reviewed traffic-calming
interval, a few independently reviewed Drive events,
several assistant-labelled negatives, and five frames nobody has reviewed. Growing it
with retained raw Drive bursts and openly licensed Indian
street-level imagery is the highest-value work available here.

## 20 Aug 2026: the repair-scar clause, ACCEPTED

The first change to this prompt that measurement supported rather than killed.

A real drive down M V Jayaram Road produced one detection on a street with at least five
defects. Running the shipped prompt over ten frames from that drive explained why, and the
reasons were unanimous: "no clearly defined pothole cavity visible", "worn ruts and uneven
patched surface", "rough patches and worn pavement but no clear distinct pothole". That
street has not got five holes in an intact road, it has lost its surface. The prompt said
"road repair scars are NOT potholes", so the model was following instructions.

Two candidates were measured and rejected before this one.

  Widening the definition to any failed carriageway: real drive 2/11 to 5/11, but it
  accepted all four images labelled intact, including two that are plainly clean asphalt.

  A fixed bottom-55% crop, on the theory that a portrait mount wastes half
  the image on sky: eval positives fell from 8/8 to 4/8, because the owner-verified
  positives are close-range shots where the damage sits higher in the frame.

What shipped changes one clause: a level, intact patch is still not a pothole, but a patch
that has itself broken up is reportable damage. Three runs per image:

                            real drive     known potholes    labelled intact, rejected
  shipped                   5/33  (15%)    24/24 (100%)      5/12 (42%)
  repair clause softened    9/33  (27%)    24/24 (100%)      4/12 (33%)

The apparent cost on the last column is not real. Per image, t049s and t061s are accepted
4 of 4 by the OLD prompt as well, with specific descriptions, so they are not a regression
from this change. Both were labelled not_pothole by the assistant and never reviewed, and
on inspection the detector looks right, so they are now marked disputed and excluded rather
than counted against it. The only movement attributable to this change is t013s going 1/4
to 2/4, which is inside the noise of four runs.

What this does not fix: the detector still describes this damage as a pothole, and a street
that has lost its surface is really a different complaint. The letter wording has not been
changed to match, and should be, once someone decides what that complaint says.

## 28 Aug 2026: orientation-aware Drive road region, RETIRED

This was a short-lived experiment, not the current contract. It replaced a fixed bottom
crop with one bounded region selected by source-image orientation:

| Orientation | Retained vertical interval | Purpose |
|---|---:|---|
| Portrait | 40%–66% | retains the near cavities in the audited 480×720 drive while stopping at the dashboard boundary |
| Landscape | 48%–78% | keeps near/mid carriageway without the usual bonnet-dominated bottom strip |
| Near-square (aspect ratio 0.9–1.1) | 40%–70% | gives ambiguous orientations an explicit, deterministic fallback |

This was retired because it could discard a pothole outside the chosen band and made the
detector depend on mount orientation. Binary v13 always supplies complete edge-to-edge
frames. The old interval table remains only to explain historical result files; none of
these regions may be used by current live, replay, evaluation, evidence or dataset paths.
