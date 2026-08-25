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

`OPENAI_API_KEY` comes from the environment or the repo-root `.env`. Use
`--dry-run` to validate transforms and request configuration without making calls.

Drive replay uses the shipped lower-60%/1024 px road view plus a 768 px full-context
view. An entry can provide a three-frame event with `frames` and `primary_index`;
legacy entries with one `path` still work. Manual replay uses the shipped full-frame
2000 px path. Pixel-triggered low-light enhancement, model, image detail, prompt,
schema and decision policy are recorded in each run.

The replay follows the production operation order—crop, resize, sample luminance,
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

The current set is not a release gate: it has six owner-verified positive photos and one
owner-verified speed-breaker burst, while the other negative labels are unverified and
historical result files predate this contract. Do not choose a model or claim accuracy
from it. First collect fully audited drives with diverse positive and negative events,
keep adjacent frames in one event/split, and lock a held-out test set.

To reproduce the set, drop the owner's photos and drive frames into the paths under
`eval/images/` named in `labels.json`. The private tester frames must not be distributed.
When adding third-party imagery, record its licence and attribution in `labels.json`.
Openly licensed street-level imagery is suitable for evaluation; it is **not** suitable
for filing complaints, because a complaint asserts a current condition on a road you
observed.

## Arms

`baseline` is read live from `DETECT_PROMPT` in `static/standalone.js`, so the
control cannot silently drift from what ships. Every `.txt` file in
`eval/prompts/` becomes an additional arm named after the file.

Everything below is a historical log for retired detector contracts. It explains past
choices but is not directly comparable with the binary v5 result.

## Results log

Kept so nobody re-runs a dead end.

| Change | Verdict | Evidence |
|---|---|---|
| Remove the `gpt-5-nano` pre-screen | **Shipped** | nano missed 8 of 9 potholes the main model caught, including a confirmed one at 0.28 confidence |
| Drive Mode at full resolution instead of 1280px | **Shipped** | 1280px dropped a real pothole to 0.46, under the gate, that holds at 0.60 full size |
| `reasoning: minimal` on detection | **Shipped** | 6/6 on confirmed potholes at identical median confidence, roughly half the latency |
| JPEG q95 instead of q85 | Rejected | apparent gain sat inside the noise floor; 1.75x the bytes for nothing |
| Crop to the road band | Rejected | destroys true positives (1/10): a narrow band excludes mid-lane damage |
| Crop keeping horizon and hood as anchors | Rejected | anchors restore true positives but false positives rise with road resolution: 50% → 75% → 85% as the road band goes 1.04x → 1.42x → 1.90x |
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

The set is too small to settle this: six owner-confirmed potholes, one owner-confirmed
speed-breaker burst, several assistant-labelled negatives, and five frames nobody has
reviewed. Growing it with retained raw Drive bursts and openly licensed Indian
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

  Cropping to the lower 55% of the frame, on the theory that a portrait mount wastes half
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

## 20 Aug 2026: crop drive frames to the road band, ACCEPTED

A phone mounted in a car points at the horizon, so the top of every dashcam frame is sky,
trees and parked cars, and the road worth inspecting is underneath. Frames from a real
drive, three runs each, at the 1024px the drive path already uses:

                          real drive      dashcam potholes    labelled intact, rejected
  full frame (shipped)    6/33  (18%)     6/6                 4/6
  lower 60%               9/33  (27%)     6/6                 3/6
  lower 45%               3/33  ( 9%)     0/6                 6/6
  1600px, full frame      7/33  (21%)     6/6                 5/6
  1600px, lower 60%       9/33  (27%)     3/6                 4/6

Keeping the lower 60% at 1024px is the only variant that gains on the real road without
losing a known pothole. More pixels did not help: 1600px full frame is inside the noise of
1024px, which says the limit is what is in the frame rather than how finely it is sampled.
Cropping harder removes the damage itself, and cropping at 1600px loses half the dashcam
positives, so both are out.

This applies to Drive Mode only. A single shot is not cropped, because the person holding
the phone has already aimed at the defect, and the full-resolution copy attached to the
complaint is never cropped, because that is what the officer looks at.

Confirmed on a device through the real drive path: 9 of 33, and the three frames that pass
are the ones where the broken surface is near the camera. Every frame where the road
recedes to the horizon still fails, which is the honest limit of this approach.

A caveat that belongs with these numbers: they were measured on frames recovered from a
screen recording, at 720x1584, not on what the camera actually captured. Real captures are
sharper and larger, so the absolute rates are probably pessimistic. The comparison between
variants is what should be trusted here, not the level.
